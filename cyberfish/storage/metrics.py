from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .models import MetricReport


COUNTER_FIELDS = {
    "transfer_sent": "transfer_sent",
    "transfer_recv": "transfer_received",
    "ack_recv": "transfer_acked",
    "transfer_expired": "transfer_expired",
    "datagrams_recv": "datagrams_received",
    "send_errors": "send_errors",
}


def minute_start(value: datetime) -> datetime:
    return value.replace(second=0, microsecond=0)


@dataclass
class MinuteMetric:
    run_id: str
    node_id: str
    bucket_start: datetime
    sample_count: int = 0
    fish_count_sum: int = 0
    fish_count_min: int = 65535
    fish_count_max: int = 0
    fps_sum: float = 0.0
    fps_min: float = float("inf")
    online_seconds: int = 0
    transfer_sent: int = 0
    transfer_received: int = 0
    transfer_acked: int = 0
    transfer_expired: int = 0
    datagrams_received: int = 0
    send_errors: int = 0

    def add_sample(self, report: MetricReport, deltas: dict[str, int], online_seconds: int) -> None:
        self.sample_count += 1
        self.fish_count_sum += report.fish_count
        self.fish_count_min = min(self.fish_count_min, report.fish_count)
        self.fish_count_max = max(self.fish_count_max, report.fish_count)
        self.fps_sum += report.fps
        self.fps_min = min(self.fps_min, report.fps)
        self.online_seconds = min(60, self.online_seconds + max(0, online_seconds))
        for field in COUNTER_FIELDS.values():
            setattr(self, field, getattr(self, field) + max(0, deltas.get(field, 0)))

    def db_values(self) -> tuple:
        return (
            self.run_id,
            self.node_id,
            self.bucket_start,
            self.sample_count,
            self.fish_count_sum,
            0 if self.fish_count_min == 65535 else self.fish_count_min,
            self.fish_count_max,
            round(self.fps_sum, 3),
            0.0 if self.fps_min == float("inf") else round(self.fps_min, 3),
            self.online_seconds,
            self.transfer_sent,
            self.transfer_received,
            self.transfer_acked,
            self.transfer_expired,
            self.datagrams_received,
            self.send_errors,
        )


@dataclass
class _Baseline:
    boot_id: str
    sequence: int
    observed_at: datetime
    counters: dict[str, int]


class MetricAggregator:
    """Convert 10-second cumulative reports into idempotent minute rows."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._baselines: dict[str, _Baseline] = {}
        self._buckets: dict[tuple[str, datetime], MinuteMetric] = {}

    def add(self, report: MetricReport, observed_at: datetime) -> list[MinuteMetric]:
        if not report.node_id or not report.boot_id:
            return []
        baseline = self._baselines.get(report.node_id)
        if (
            baseline is not None
            and baseline.boot_id == report.boot_id
            and report.sequence <= baseline.sequence
        ):
            return []

        deltas = {field: 0 for field in COUNTER_FIELDS.values()}
        online_seconds = 0
        if baseline is not None and baseline.boot_id == report.boot_id:
            online_seconds = min(10, max(0, int((observed_at - baseline.observed_at).total_seconds())))
            for source, target in COUNTER_FIELDS.items():
                current = report.counters.get(source, 0)
                previous = baseline.counters.get(source, 0)
                deltas[target] = current - previous if current >= previous else 0

        self._baselines[report.node_id] = _Baseline(
            boot_id=report.boot_id,
            sequence=report.sequence,
            observed_at=observed_at,
            counters=dict(report.counters),
        )
        bucket = minute_start(observed_at)
        key = (report.node_id, bucket)
        metric = self._buckets.setdefault(key, MinuteMetric(self.run_id, report.node_id, bucket))
        metric.add_sample(report, deltas, online_seconds)
        return self.flush_before(bucket)

    def flush_before(self, bucket: datetime) -> list[MinuteMetric]:
        keys = [key for key in self._buckets if key[1] < bucket]
        return [self._buckets.pop(key) for key in sorted(keys, key=lambda item: item[1])]

    def flush_stale(self, now: datetime) -> list[MinuteMetric]:
        return self.flush_before(minute_start(now) - timedelta(minutes=1))

    def flush_all(self) -> list[MinuteMetric]:
        metrics = list(self._buckets.values())
        self._buckets.clear()
        return sorted(metrics, key=lambda metric: (metric.bucket_start, metric.node_id))

