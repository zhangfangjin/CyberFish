from __future__ import annotations

from dataclasses import dataclass
import json
import os
import queue
import threading
from typing import Any
import uuid

from .metrics import MetricAggregator, MinuteMetric
from .models import ConfigSnapshot, MetricReport, NodeOverride, NodeRecord, StorageResult, utc_now


TRUE_VALUES = {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MySQLSettings:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 3306
    unix_socket: str | None = None
    database: str = "cyberfish"
    user: str = "cyberfish_app"
    password: str = ""
    connect_timeout: int = 3

    @classmethod
    def from_env(cls) -> "MySQLSettings":
        enabled = os.getenv("CYBERFISH_DB_ENABLED", "").strip().lower() in TRUE_VALUES
        try:
            port = int(os.getenv("CYBERFISH_DB_PORT", "3306"))
        except ValueError:
            port = 0
        try:
            connect_timeout = max(1, int(os.getenv("CYBERFISH_DB_CONNECT_TIMEOUT", "3")))
        except ValueError:
            connect_timeout = 3
        return cls(
            enabled=enabled,
            host=os.getenv("CYBERFISH_DB_HOST", "127.0.0.1"),
            port=port,
            unix_socket=os.getenv("CYBERFISH_DB_UNIX_SOCKET") or None,
            database=os.getenv("CYBERFISH_DB_NAME", "cyberfish"),
            user=os.getenv("CYBERFISH_DB_USER", "cyberfish_app"),
            password=os.getenv("CYBERFISH_DB_PASSWORD", ""),
            connect_timeout=connect_timeout,
        )

    def validate(self) -> None:
        if not self.enabled:
            return
        if (not self.host and not self.unix_socket) or not self.database or not self.user:
            raise ValueError("MySQL is enabled but host, database, or user is empty")
        if not self.unix_socket and not (1 <= self.port <= 65535):
            raise ValueError("MySQL port must be between 1 and 65535")


class ConfigConflictError(RuntimeError):
    pass


class MySQLRepository:
    def __init__(self, settings: MySQLSettings) -> None:
        settings.validate()
        try:
            import mysql.connector  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "mysql-connector-python is required when CYBERFISH_DB_ENABLED=1"
            ) from exc
        self._connector = mysql.connector
        connection_args: dict[str, Any] = {
            "host": settings.host,
            "port": settings.port,
            "database": settings.database,
            "user": settings.user,
            "password": settings.password,
            "connection_timeout": settings.connect_timeout,
            "autocommit": False,
            "charset": "utf8mb4",
            "time_zone": "+00:00",
        }
        if settings.unix_socket:
            connection_args["unix_socket"] = settings.unix_socket
        self._connection = self._connector.connect(
            **connection_args,
        )

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:
            pass

    def ping(self) -> None:
        self._connection.ping(reconnect=False, attempts=1, delay=0)

    def _upsert_node(self, cursor: Any, node: NodeRecord) -> None:
        now = utc_now()
        cursor.execute(
            """
            INSERT INTO cf_nodes (
                node_id, hostname, bootstrap_role, last_ip, udp_port,
                screen_width, screen_height, first_seen_at, last_seen_at,
                last_applied_config_version
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                hostname = VALUES(hostname),
                bootstrap_role = VALUES(bootstrap_role),
                last_ip = VALUES(last_ip),
                udp_port = VALUES(udp_port),
                screen_width = VALUES(screen_width),
                screen_height = VALUES(screen_height),
                last_seen_at = VALUES(last_seen_at),
                last_applied_config_version = GREATEST(
                    COALESCE(last_applied_config_version, 0),
                    VALUES(last_applied_config_version)
                )
            """,
            (
                node.node_id,
                node.hostname,
                node.role,
                node.ip_address,
                node.udp_port,
                max(0, node.screen_size[0]),
                max(0, node.screen_size[1]),
                now,
                now,
                max(0, node.applied_config_version),
            ),
        )

    def upsert_node(self, node: NodeRecord, run_id: str | None = None) -> None:
        cursor = self._connection.cursor()
        try:
            self._upsert_node(cursor, node)
            if run_id and node.boot_id:
                now = utc_now()
                node_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{node.node_id}:{node.boot_id}"))
                cursor.execute(
                    """
                    INSERT INTO cf_node_sessions (
                        node_session_id, run_id, node_id, boot_id,
                        joined_at, last_seen_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE last_seen_at = VALUES(last_seen_at)
                    """,
                    (node_session_id, run_id, node.node_id, node.boot_id, now, now),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def bootstrap(
        self,
        node: NodeRecord,
        cached: ConfigSnapshot,
        run_id: str,
    ) -> ConfigSnapshot:
        cursor = self._connection.cursor(dictionary=True)
        try:
            self._upsert_node(cursor, node)
            cursor.execute(
                "SELECT active_config_version, active_manual_topology_id "
                "FROM cf_system_state WHERE state_id = 1 FOR UPDATE"
            )
            state = cursor.fetchone()
            if state is None:
                version = self._insert_config_revision(cursor, cached, node.node_id)
                self._insert_node_override(cursor, version, node.node_id, cached.node)
                cursor.execute(
                    """
                    INSERT INTO cf_system_state (
                        state_id, active_config_version, active_manual_topology_id
                    ) VALUES (1, %s, NULL)
                    """,
                    (version,),
                )
            else:
                version = int(state["active_config_version"])
            snapshot = self._load_snapshot(cursor, version, node.node_id)
            cursor.execute(
                """
                INSERT INTO cf_run_sessions (
                    run_id, admin_node_id, config_version, status, started_at
                ) VALUES (%s, %s, %s, 'running', %s)
                ON DUPLICATE KEY UPDATE status = 'running', ended_at = NULL, end_reason = NULL
                """,
                (run_id, node.node_id, snapshot.version, utc_now()),
            )
            now = utc_now()
            node_session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:{node.node_id}:{node.boot_id}"))
            cursor.execute(
                """
                INSERT INTO cf_node_sessions (
                    node_session_id, run_id, node_id, boot_id, joined_at, last_seen_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE last_seen_at = VALUES(last_seen_at)
                """,
                (node_session_id, run_id, node.node_id, node.boot_id, now, now),
            )
            self._connection.commit()
            return snapshot
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def _insert_config_revision(
        self,
        cursor: Any,
        snapshot: ConfigSnapshot,
        created_by: str,
        reason: str | None = None,
    ) -> int:
        cursor.execute(
            """
            INSERT INTO cf_config_revisions (
                fish_count, speed_multiplier, sound_enabled, network_enabled,
                auto_topology, created_by, change_reason
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                snapshot.fish_count,
                snapshot.speed_multiplier,
                snapshot.sound_enabled,
                snapshot.network_enabled,
                snapshot.auto_topology,
                created_by,
                reason,
            ),
        )
        return int(cursor.lastrowid)

    def _insert_node_override(
        self,
        cursor: Any,
        version: int,
        node_id: str,
        override: NodeOverride,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO cf_config_node_overrides (
                config_version, node_id, fullscreen, display_index,
                window_width, window_height
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                fullscreen = VALUES(fullscreen),
                display_index = VALUES(display_index),
                window_width = VALUES(window_width),
                window_height = VALUES(window_height)
            """,
            (
                version,
                node_id,
                override.fullscreen,
                override.display_index,
                override.window_width,
                override.window_height,
            ),
        )

    def _load_snapshot(self, cursor: Any, version: int, node_id: str) -> ConfigSnapshot:
        cursor.execute(
            """
            SELECT c.config_version, c.fish_count, c.speed_multiplier,
                   c.sound_enabled, c.network_enabled, c.auto_topology,
                   o.fullscreen, o.display_index, o.window_width, o.window_height,
                   s.active_manual_topology_id
            FROM cf_config_revisions c
            JOIN cf_system_state s ON s.state_id = 1
            LEFT JOIN cf_config_node_overrides o
              ON o.config_version = c.config_version AND o.node_id = %s
            WHERE c.config_version = %s
            """,
            (node_id, version),
        )
        row = cursor.fetchone()
        if row is None:
            raise RuntimeError(f"Active configuration {version} does not exist")
        topology = {}
        topologies: dict[str, dict[str, str]] = {}
        cursor.execute(
            """
            SELECT node_id, fullscreen, display_index, window_width, window_height
            FROM cf_config_node_overrides WHERE config_version = %s
            """,
            (version,),
        )
        node_overrides = {
            item["node_id"]: {
                "fullscreen": item["fullscreen"],
                "display_index": item["display_index"],
                "window_width": item["window_width"],
                "window_height": item["window_height"],
            }
            for item in cursor.fetchall()
        }
        topology_id = row.get("active_manual_topology_id")
        if topology_id:
            cursor.execute(
                """
                SELECT from_node_id, direction, to_node_id FROM cf_topology_edges
                WHERE topology_id = %s
                """,
                (topology_id,),
            )
            for item in cursor.fetchall():
                source = item["from_node_id"]
                topologies.setdefault(source, {})[item["direction"]] = item["to_node_id"]
            topology = topologies.get(node_id, {})
        return ConfigSnapshot.from_dict(
            {
                "config_version": row["config_version"],
                "fish_count": row["fish_count"],
                "speed_multiplier": row["speed_multiplier"],
                "sound_enabled": row["sound_enabled"],
                "network_enabled": row["network_enabled"],
                "auto_topology": row["auto_topology"],
                "node": {
                    "fullscreen": row.get("fullscreen") or False,
                    "display_index": row.get("display_index") or 0,
                    "window_width": row.get("window_width") or 1280,
                    "window_height": row.get("window_height") or 720,
                },
                "manual_topology_id": topology_id,
                "topology": topology,
                "topologies": topologies,
                "node_overrides": node_overrides,
            }
        )

    def ensure_node_override(
        self,
        version: int,
        node_id: str,
        override: NodeOverride,
    ) -> None:
        cursor = self._connection.cursor()
        try:
            self._insert_node_override_ignore(cursor, version, node_id, override)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def _insert_node_override_ignore(
        self,
        cursor: Any,
        version: int,
        node_id: str,
        override: NodeOverride,
    ) -> None:
        cursor.execute(
            """
            INSERT IGNORE INTO cf_config_node_overrides (
                config_version, node_id, fullscreen, display_index,
                window_width, window_height
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                version,
                node_id,
                override.fullscreen,
                override.display_index,
                override.window_width,
                override.window_height,
            ),
        )

    def create_config_revision(
        self,
        snapshot: ConfigSnapshot,
        base_version: int,
        node_id: str,
        reason: str,
    ) -> ConfigSnapshot:
        cursor = self._connection.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT active_config_version FROM cf_system_state "
                "WHERE state_id = 1 FOR UPDATE"
            )
            state = cursor.fetchone()
            if state is None or int(state["active_config_version"]) != int(base_version):
                actual = None if state is None else state["active_config_version"]
                raise ConfigConflictError(
                    f"Configuration changed concurrently: expected {base_version}, active {actual}"
                )
            version = self._insert_config_revision(cursor, snapshot, node_id, reason)
            cursor.execute(
                """
                INSERT INTO cf_config_node_overrides (
                    config_version, node_id, fullscreen, display_index,
                    window_width, window_height
                )
                SELECT %s, node_id, fullscreen, display_index, window_width, window_height
                FROM cf_config_node_overrides WHERE config_version = %s
                """,
                (version, base_version),
            )
            self._insert_node_override(cursor, version, node_id, snapshot.node)
            cursor.execute(
                "UPDATE cf_system_state SET active_config_version = %s WHERE state_id = 1",
                (version,),
            )
            result = self._load_snapshot(cursor, version, node_id)
            self._connection.commit()
            return result
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def save_metric(self, metric: MinuteMetric) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO cf_node_metric_minute (
                    run_id, node_id, bucket_start, sample_count,
                    fish_count_sum, fish_count_min, fish_count_max,
                    fps_sum, fps_min, online_seconds, transfer_sent,
                    transfer_received, transfer_acked, transfer_expired,
                    datagrams_received, send_errors
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    sample_count = VALUES(sample_count),
                    fish_count_sum = VALUES(fish_count_sum),
                    fish_count_min = VALUES(fish_count_min),
                    fish_count_max = VALUES(fish_count_max),
                    fps_sum = VALUES(fps_sum),
                    fps_min = VALUES(fps_min),
                    online_seconds = VALUES(online_seconds),
                    transfer_sent = VALUES(transfer_sent),
                    transfer_received = VALUES(transfer_received),
                    transfer_acked = VALUES(transfer_acked),
                    transfer_expired = VALUES(transfer_expired),
                    datagrams_received = VALUES(datagrams_received),
                    send_errors = VALUES(send_errors)
                """,
                metric.db_values(),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def save_topology(
        self,
        topology_id: str,
        run_id: str,
        mode: str,
        converged: bool,
        created_by: str,
        edges: list[tuple[str, str, str]],
    ) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                INSERT IGNORE INTO cf_topology_versions (
                    topology_id, run_id, topology_mode, converged, created_by, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (topology_id, run_id, mode, converged, created_by, utc_now()),
            )
            cursor.execute("DELETE FROM cf_topology_edges WHERE topology_id = %s", (topology_id,))
            if edges:
                cursor.executemany(
                    """
                    INSERT INTO cf_topology_edges (
                        topology_id, from_node_id, direction, to_node_id
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    [(topology_id, source, direction, target) for source, direction, target in edges],
                )
            if mode == "manual":
                cursor.execute(
                    "UPDATE cf_system_state SET active_manual_topology_id = %s WHERE state_id = 1",
                    (topology_id,),
                )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def save_command(self, payload: dict[str, Any]) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO cf_admin_commands (
                    command_id, run_id, admin_node_id, target_node_id, action,
                    payload, config_version, expected_results, status, requested_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'pending', %s)
                ON DUPLICATE KEY UPDATE command_id = VALUES(command_id)
                """,
                (
                    payload["command_id"],
                    payload["run_id"],
                    payload["admin_node_id"],
                    payload.get("target_node_id"),
                    payload["action"],
                    json.dumps(payload.get("payload", {}), ensure_ascii=False),
                    payload.get("config_version"),
                    max(1, int(payload.get("expected_results", 1))),
                    payload.get("requested_at", utc_now()),
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def save_command_result(self, payload: dict[str, Any]) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO cf_admin_command_results (
                    command_id, node_id, ok, message, acknowledged_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    ok = VALUES(ok), message = VALUES(message),
                    acknowledged_at = VALUES(acknowledged_at)
                """,
                (
                    payload["command_id"],
                    payload["node_id"],
                    payload["ok"],
                    payload.get("message", ""),
                    payload.get("acknowledged_at", utc_now()),
                ),
            )
            cursor.execute(
                """
                UPDATE cf_admin_commands c
                SET c.status = CASE
                        WHEN (SELECT COUNT(*) FROM cf_admin_command_results r
                              WHERE r.command_id = c.command_id) < c.expected_results
                            THEN 'pending'
                        WHEN (SELECT COUNT(*) FROM cf_admin_command_results r
                              WHERE r.command_id = c.command_id AND r.ok = 0) > 0
                            THEN 'partial'
                        ELSE 'completed'
                    END,
                    c.completed_at = CASE
                        WHEN (SELECT COUNT(*) FROM cf_admin_command_results r
                              WHERE r.command_id = c.command_id) >= c.expected_results
                            THEN %s
                        ELSE NULL
                    END
                WHERE c.command_id = %s
                """,
                (payload.get("acknowledged_at", utc_now()), payload["command_id"]),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def finish_run(self, run_id: str, reason: str) -> None:
        cursor = self._connection.cursor()
        try:
            now = utc_now()
            cursor.execute(
                """
                UPDATE cf_run_sessions
                SET status = 'completed', ended_at = %s, end_reason = %s
                WHERE run_id = %s
                """,
                (now, reason, run_id),
            )
            cursor.execute(
                """
                UPDATE cf_node_sessions SET left_at = %s, exit_reason = %s
                WHERE run_id = %s AND left_at IS NULL
                """,
                (now, reason, run_id),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()

    def apply_retention(self) -> None:
        cursor = self._connection.cursor()
        try:
            cursor.execute(
                "DELETE FROM cf_node_metric_minute WHERE bucket_start < UTC_TIMESTAMP() - INTERVAL 30 DAY LIMIT 1000"
            )
            cursor.execute(
                """
                DELETE FROM cf_topology_versions
                WHERE topology_mode = 'auto' AND created_at < UTC_TIMESTAMP() - INTERVAL 90 DAY
                LIMIT 1000
                """
            )
            cursor.execute(
                "DELETE FROM cf_admin_commands WHERE requested_at < UTC_TIMESTAMP() - INTERVAL 180 DAY LIMIT 1000"
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            cursor.close()


@dataclass
class _Task:
    kind: str
    payload: Any = None
    request_id: str | None = None
    attempts: int = 0


class DatabaseService:
    """Admin-only asynchronous MySQL adapter.

    The Pygame thread only enqueues tasks and polls results. No connector call is
    made from the real-time frame loop.
    """

    def __init__(
        self,
        settings: MySQLSettings,
        node: NodeRecord,
        cached_config: ConfigSnapshot,
        *,
        queue_size: int = 10_000,
    ) -> None:
        self.settings = settings
        self.node = node
        self.cached_config = cached_config
        self.run_id = str(uuid.uuid4())
        self._tasks: queue.Queue[_Task] = queue.Queue(maxsize=queue_size)
        self._results: queue.Queue[StorageResult] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._aggregator = MetricAggregator(self.run_id)
        self._healthy = False

    @property
    def enabled(self) -> bool:
        return self.settings.enabled

    @property
    def healthy(self) -> bool:
        return self._healthy

    def start(self) -> None:
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="cyberfish-mysql", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        if self._thread is None:
            return
        self._stop.set()
        self._enqueue(_Task("shutdown"), coalesce=False)
        self._thread.join(timeout=max(0.0, timeout))
        self._thread = None

    def poll_results(self) -> list[StorageResult]:
        results: list[StorageResult] = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                break
            self._healthy = result.ok if result.kind in ("bootstrap", "health") else self._healthy
            results.append(result)
        return results

    def submit_config(self, snapshot: ConfigSnapshot, reason: str) -> str | None:
        if not self.healthy:
            return None
        request_id = uuid.uuid4().hex
        task = _Task(
            "config",
            {"snapshot": snapshot, "base_version": snapshot.version, "reason": reason},
            request_id=request_id,
        )
        return request_id if self._enqueue(task, coalesce=False) else None

    def record_node(self, node: NodeRecord) -> None:
        self._enqueue(_Task("node", node), coalesce=True)

    def record_metric(self, report: MetricReport) -> None:
        self._enqueue(_Task("metric", report), coalesce=True)

    def record_node_override(
        self,
        node_id: str,
        version: int,
        override: NodeOverride,
    ) -> None:
        self._enqueue(
            _Task("node_override", (node_id, version, override)),
            coalesce=True,
        )

    def record_topology(
        self,
        mode: str,
        converged: bool,
        edges: list[tuple[str, str, str]],
        *,
        topology_id: str | None = None,
    ) -> str | None:
        topology_id = topology_id or str(uuid.uuid4())
        accepted = self._enqueue(
            _Task(
                "topology",
                {
                    "topology_id": topology_id,
                    "mode": mode,
                    "converged": converged,
                    "edges": list(edges),
                },
                request_id=topology_id,
            ),
            coalesce=True,
        )
        return topology_id if accepted else None

    def record_command(self, payload: dict[str, Any]) -> None:
        self._enqueue(_Task("command", dict(payload)), coalesce=False)

    def record_command_result(self, payload: dict[str, Any]) -> None:
        self._enqueue(_Task("command_result", dict(payload)), coalesce=False)

    def _enqueue(self, task: _Task, *, coalesce: bool) -> bool:
        try:
            self._tasks.put_nowait(task)
            return True
        except queue.Full:
            # Never evict an older configuration/audit task just to admit telemetry.
            # Metric reports are cumulative, so dropping one intermediate report is safe.
            return False

    def _run(self) -> None:
        repository: MySQLRepository | None = None
        pending: _Task | None = None
        last_health_message = ""
        while not self._stop.is_set():
            if repository is None:
                try:
                    repository = MySQLRepository(self.settings)
                    snapshot = repository.bootstrap(self.node, self.cached_config, self.run_id)
                    repository.apply_retention()
                    self._results.put(StorageResult("bootstrap", True, snapshot=snapshot))
                    last_health_message = ""
                except Exception as exc:
                    message = str(exc)
                    if message != last_health_message:
                        self._results.put(StorageResult("health", False, message=message))
                        last_health_message = message
                    if repository is not None:
                        repository.close()
                        repository = None
                    self._stop.wait(5.0)
                    continue

            try:
                task = pending or self._tasks.get(timeout=1.0)
                pending = None
            except queue.Empty:
                try:
                    for metric in self._aggregator.flush_stale(utc_now()):
                        repository.save_metric(metric)
                except Exception:
                    repository.close()
                    repository = None
                continue

            if task.kind == "shutdown":
                break
            try:
                self._execute(repository, task)
            except ConfigConflictError as exc:
                self._results.put(
                    StorageResult("config", False, task.request_id, message=str(exc))
                )
            except Exception as exc:
                if task.kind == "config":
                    self._results.put(
                        StorageResult("config", False, task.request_id, message=str(exc))
                    )
                elif task.attempts < 3:
                    task.attempts += 1
                    pending = task
                self._results.put(StorageResult("health", False, message=str(exc)))
                repository.close()
                repository = None

        if repository is not None:
            try:
                for metric in self._aggregator.flush_all():
                    repository.save_metric(metric)
                repository.finish_run(self.run_id, "application shutdown")
            except Exception:
                pass
            repository.close()

    def _execute(self, repository: MySQLRepository, task: _Task) -> None:
        if task.kind == "config":
            payload = task.payload
            snapshot = repository.create_config_revision(
                payload["snapshot"],
                payload["base_version"],
                self.node.node_id,
                payload["reason"],
            )
            self._results.put(StorageResult("config", True, task.request_id, snapshot=snapshot))
        elif task.kind == "node":
            repository.upsert_node(task.payload, self.run_id)
        elif task.kind == "metric":
            for metric in self._aggregator.add(task.payload, utc_now()):
                repository.save_metric(metric)
        elif task.kind == "node_override":
            node_id, version, override = task.payload
            repository.ensure_node_override(version, node_id, override)
        elif task.kind == "topology":
            payload = task.payload
            repository.save_topology(
                payload["topology_id"],
                self.run_id,
                payload["mode"],
                payload["converged"],
                self.node.node_id,
                payload["edges"],
            )
            self._results.put(StorageResult("topology", True, request_id=task.request_id))
        elif task.kind == "command":
            payload = {**task.payload, "run_id": self.run_id}
            repository.save_command(payload)
        elif task.kind == "command_result":
            repository.save_command_result(task.payload)
