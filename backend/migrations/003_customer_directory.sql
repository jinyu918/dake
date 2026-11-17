-- 客户资料及群归属；运行进度复用 processing_runs，不修改已有消息或事件。
CREATE TABLE IF NOT EXISTS customer_profiles (
    uid VARCHAR(128) COLLATE utf8mb4_bin PRIMARY KEY,
    level VARCHAR(8) NULL,
    rank_value VARCHAR(64) NULL,
    tam VARCHAR(256) NULL,
    tam_b VARCHAR(256) NULL,
    level_state VARCHAR(16) NOT NULL,
    tam_state VARCHAR(16) NOT NULL,
    level_error VARCHAR(256) NULL,
    tam_error VARCHAR(256) NULL,
    level_synced_at DATETIME(6) NULL,
    tam_synced_at DATETIME(6) NULL,
    attempted_at DATETIME(6) NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS customer_groups (
    source VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    group_name VARCHAR(512) NOT NULL,
    uid VARCHAR(128) COLLATE utf8mb4_bin NULL,
    status VARCHAR(16) NOT NULL,
    reason VARCHAR(256) NOT NULL,
    candidate_uids JSON NOT NULL,
    first_seen_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    synced_at DATETIME(6) NOT NULL,
    PRIMARY KEY (source, group_id),
    KEY ix_customer_groups_uid (uid),
    KEY ix_customer_groups_status (status, synced_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;

CREATE TABLE IF NOT EXISTS customer_group_history (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    group_id VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    run_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    before_value JSON NULL,
    after_value JSON NOT NULL,
    recorded_at DATETIME(6) NOT NULL,
    KEY ix_customer_group_history (source, group_id, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
