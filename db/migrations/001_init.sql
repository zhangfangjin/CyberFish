-- CyberFish MySQL 8.0 initial schema.
-- Run with a migration account; the application account only needs DML rights.

SET NAMES utf8mb4;
SET time_zone = '+00:00';

CREATE TABLE IF NOT EXISTS cf_schema_migrations (
    version VARCHAR(32) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    applied_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_nodes (
    node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL,
    bootstrap_role VARCHAR(20) CHARACTER SET ascii NOT NULL,
    last_ip VARCHAR(45) CHARACTER SET ascii NULL,
    udp_port SMALLINT UNSIGNED NOT NULL,
    screen_width SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    screen_height SMALLINT UNSIGNED NOT NULL DEFAULT 0,
    first_seen_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    last_applied_config_version BIGINT UNSIGNED NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT chk_cf_nodes_role
        CHECK (bootstrap_role IN ('admin', 'display_node')),
    KEY idx_cf_nodes_last_seen (last_seen_at),
    KEY idx_cf_nodes_config_version (last_applied_config_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_config_revisions (
    config_version BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
    fish_count SMALLINT UNSIGNED NOT NULL,
    speed_multiplier DECIMAL(2,1) NOT NULL,
    sound_enabled TINYINT(1) NOT NULL,
    network_enabled TINYINT(1) NOT NULL,
    auto_topology TINYINT(1) NOT NULL,
    created_by VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    change_reason VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT chk_cf_config_fish_count CHECK (fish_count BETWEEN 1 AND 200),
    CONSTRAINT chk_cf_config_speed CHECK (speed_multiplier BETWEEN 0.1 AND 4.0),
    CONSTRAINT fk_cf_config_created_by
        FOREIGN KEY (created_by) REFERENCES cf_nodes(node_id),
    KEY idx_cf_config_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_config_node_overrides (
    config_version BIGINT UNSIGNED NOT NULL,
    node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    fullscreen TINYINT(1) NOT NULL,
    display_index SMALLINT UNSIGNED NOT NULL,
    window_width SMALLINT UNSIGNED NOT NULL,
    window_height SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (config_version, node_id),
    CONSTRAINT chk_cf_override_width CHECK (window_width >= 320),
    CONSTRAINT chk_cf_override_height CHECK (window_height >= 240),
    CONSTRAINT fk_cf_override_config
        FOREIGN KEY (config_version) REFERENCES cf_config_revisions(config_version)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_override_node
        FOREIGN KEY (node_id) REFERENCES cf_nodes(node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_run_sessions (
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    admin_node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    config_version BIGINT UNSIGNED NOT NULL,
    status VARCHAR(20) CHARACTER SET ascii NOT NULL,
    started_at DATETIME(6) NOT NULL,
    ended_at DATETIME(6) NULL,
    end_reason VARCHAR(255) NULL,
    CONSTRAINT chk_cf_run_status
        CHECK (status IN ('running', 'completed', 'aborted', 'recovered')),
    CONSTRAINT fk_cf_run_admin
        FOREIGN KEY (admin_node_id) REFERENCES cf_nodes(node_id),
    CONSTRAINT fk_cf_run_config
        FOREIGN KEY (config_version) REFERENCES cf_config_revisions(config_version),
    KEY idx_cf_run_started_at (started_at),
    KEY idx_cf_run_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_topology_versions (
    topology_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    topology_mode VARCHAR(10) CHARACTER SET ascii NOT NULL,
    converged TINYINT(1) NOT NULL,
    created_by VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    created_at DATETIME(6) NOT NULL,
    CONSTRAINT chk_cf_topology_mode CHECK (topology_mode IN ('manual', 'auto')),
    CONSTRAINT fk_cf_topology_run
        FOREIGN KEY (run_id) REFERENCES cf_run_sessions(run_id)
        ON DELETE SET NULL,
    CONSTRAINT fk_cf_topology_created_by
        FOREIGN KEY (created_by) REFERENCES cf_nodes(node_id),
    KEY idx_cf_topology_created_at (created_at),
    KEY idx_cf_topology_mode_created (topology_mode, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_topology_edges (
    topology_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    from_node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    direction VARCHAR(5) CHARACTER SET ascii NOT NULL,
    to_node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (topology_id, from_node_id, direction),
    UNIQUE KEY uq_cf_topology_peer (topology_id, from_node_id, to_node_id),
    CONSTRAINT chk_cf_topology_direction
        CHECK (direction IN ('left', 'right', 'up', 'down')),
    CONSTRAINT chk_cf_topology_not_self CHECK (from_node_id <> to_node_id),
    CONSTRAINT fk_cf_edge_topology
        FOREIGN KEY (topology_id) REFERENCES cf_topology_versions(topology_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_edge_from_node
        FOREIGN KEY (from_node_id) REFERENCES cf_nodes(node_id),
    CONSTRAINT fk_cf_edge_to_node
        FOREIGN KEY (to_node_id) REFERENCES cf_nodes(node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_system_state (
    state_id TINYINT UNSIGNED NOT NULL PRIMARY KEY,
    active_config_version BIGINT UNSIGNED NOT NULL,
    active_manual_topology_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NULL,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    CONSTRAINT chk_cf_system_singleton CHECK (state_id = 1),
    CONSTRAINT fk_cf_state_config
        FOREIGN KEY (active_config_version) REFERENCES cf_config_revisions(config_version),
    CONSTRAINT fk_cf_state_topology
        FOREIGN KEY (active_manual_topology_id) REFERENCES cf_topology_versions(topology_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_node_sessions (
    node_session_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    boot_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    joined_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    left_at DATETIME(6) NULL,
    exit_reason VARCHAR(255) NULL,
    UNIQUE KEY uq_cf_node_session_boot (run_id, node_id, boot_id),
    CONSTRAINT fk_cf_node_session_run
        FOREIGN KEY (run_id) REFERENCES cf_run_sessions(run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_node_session_node
        FOREIGN KEY (node_id) REFERENCES cf_nodes(node_id),
    KEY idx_cf_node_session_open (run_id, left_at),
    KEY idx_cf_node_session_last_seen (last_seen_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_node_metric_minute (
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    bucket_start DATETIME NOT NULL,
    sample_count SMALLINT UNSIGNED NOT NULL,
    fish_count_sum BIGINT UNSIGNED NOT NULL,
    fish_count_min SMALLINT UNSIGNED NOT NULL,
    fish_count_max SMALLINT UNSIGNED NOT NULL,
    fps_sum DECIMAL(14,3) NOT NULL,
    fps_min DECIMAL(7,3) NOT NULL,
    online_seconds SMALLINT UNSIGNED NOT NULL,
    transfer_sent BIGINT UNSIGNED NOT NULL DEFAULT 0,
    transfer_received BIGINT UNSIGNED NOT NULL DEFAULT 0,
    transfer_acked BIGINT UNSIGNED NOT NULL DEFAULT 0,
    transfer_expired BIGINT UNSIGNED NOT NULL DEFAULT 0,
    datagrams_received BIGINT UNSIGNED NOT NULL DEFAULT 0,
    send_errors BIGINT UNSIGNED NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (run_id, node_id, bucket_start),
    CONSTRAINT chk_cf_metric_online_seconds CHECK (online_seconds <= 60),
    CONSTRAINT fk_cf_metric_run
        FOREIGN KEY (run_id) REFERENCES cf_run_sessions(run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_metric_node
        FOREIGN KEY (node_id) REFERENCES cf_nodes(node_id),
    KEY idx_cf_metric_bucket (bucket_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_admin_commands (
    command_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,
    run_id CHAR(36) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    admin_node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    target_node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    action VARCHAR(48) CHARACTER SET ascii NOT NULL,
    payload JSON NOT NULL,
    config_version BIGINT UNSIGNED NULL,
    expected_results SMALLINT UNSIGNED NOT NULL DEFAULT 1,
    status VARCHAR(20) CHARACTER SET ascii NOT NULL,
    requested_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    CONSTRAINT chk_cf_command_status
        CHECK (status IN ('pending', 'partial', 'completed', 'failed')),
    CONSTRAINT fk_cf_command_run
        FOREIGN KEY (run_id) REFERENCES cf_run_sessions(run_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_command_admin
        FOREIGN KEY (admin_node_id) REFERENCES cf_nodes(node_id),
    CONSTRAINT fk_cf_command_target
        FOREIGN KEY (target_node_id) REFERENCES cf_nodes(node_id),
    CONSTRAINT fk_cf_command_config
        FOREIGN KEY (config_version) REFERENCES cf_config_revisions(config_version),
    KEY idx_cf_command_requested_at (requested_at),
    KEY idx_cf_command_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS cf_admin_command_results (
    command_id VARCHAR(96) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    node_id VARCHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    ok TINYINT(1) NOT NULL,
    message VARCHAR(255) NOT NULL,
    acknowledged_at DATETIME(6) NOT NULL,
    PRIMARY KEY (command_id, node_id),
    CONSTRAINT fk_cf_command_result_command
        FOREIGN KEY (command_id) REFERENCES cf_admin_commands(command_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_cf_command_result_node
        FOREIGN KEY (node_id) REFERENCES cf_nodes(node_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

INSERT INTO cf_schema_migrations(version)
VALUES ('001_init')
ON DUPLICATE KEY UPDATE version = VALUES(version);
