-- 客户独立推进：来源水位、分析窗口、执行权与隔离队列。
-- 不修改事件业务状态，不删除已有消息、事件或处理记录。
-- 仅供隔离库或经确认后的结构升级；重复执行时已存在的列与索引由应用忽略。
CREATE TABLE IF NOT EXISTS source_state (
    source VARCHAR(64) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    coverage_start DATETIME(6) NULL,
    fetched_until DATETIME(6) NULL,
    owner VARCHAR(64) COLLATE utf8mb4_bin NULL,
    epoch BIGINT NOT NULL DEFAULT 0,
    lease_until DATETIME(6) NULL,
    next_poll_at DATETIME(6) NULL,
    incomplete_reason VARCHAR(255) NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS source_windows (
    fetch_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    source VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    window_start DATETIME(6) NOT NULL,
    window_end DATETIME(6) NOT NULL,
    refresh_generation BIGINT NOT NULL DEFAULT 0,
    status VARCHAR(24) NOT NULL,
    retry_at DATETIME(6) NULL,
    message_count INT NOT NULL DEFAULT 0,
    isolated_count INT NOT NULL DEFAULT 0,
    digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    error_reason VARCHAR(255) NULL,
    created_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    UNIQUE KEY uq_source_window (source, window_start, window_end, refresh_generation),
    KEY ix_source_window_status (source, status, retry_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS uid_pipeline_state (
    uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    active_stream VARCHAR(128) COLLATE utf8mb4_bin NULL,
    origin DATETIME(6) NULL,
    catchup_target DATETIME(6) NULL,
    planned_until DATETIME(6) NULL,
    analyzed_until DATETIME(6) NULL,
    active_window_start DATETIME(6) NULL,
    active_window_end DATETIME(6) NULL,
    owner VARCHAR(64) COLLATE utf8mb4_bin NULL,
    epoch BIGINT NOT NULL DEFAULT 0,
    lease_until DATETIME(6) NULL,
    heartbeat_at DATETIME(6) NULL,
    input_revision BIGINT NOT NULL DEFAULT 0,
    block_reason VARCHAR(255) NULL,
    block_kind VARCHAR(32) NULL,
    last_served_at DATETIME(6) NULL,
    phase VARCHAR(16) NOT NULL DEFAULT 'catchup',
    incomplete TINYINT NOT NULL DEFAULT 0,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS analysis_windows (
    window_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    uid VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    stream VARCHAR(128) COLLATE utf8mb4_bin NOT NULL,
    config_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    window_start DATETIME(6) NOT NULL,
    window_end DATETIME(6) NOT NULL,
    predecessor_end DATETIME(6) NULL,
    status VARCHAR(24) NOT NULL,
    available_at DATETIME(6) NULL,
    attempt_id VARCHAR(36) COLLATE utf8mb4_bin NULL,
    claimed_epoch BIGINT NULL,
    run_id VARCHAR(36) COLLATE utf8mb4_bin NULL,
    stage VARCHAR(32) NULL,
    queued_at DATETIME(6) NULL,
    started_at DATETIME(6) NULL,
    model_started_at DATETIME(6) NULL,
    committed_at DATETIME(6) NULL,
    error_code VARCHAR(64) NULL,
    UNIQUE KEY uq_analysis_window (uid, stream, window_start, window_end),
    KEY ix_analysis_claim (status, available_at, uid),
    KEY ix_analysis_uid_start (uid, status, window_start)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS runtime_limits (
    resource VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
    slot INT NOT NULL,
    owner VARCHAR(64) COLLATE utf8mb4_bin NULL,
    epoch BIGINT NOT NULL DEFAULT 0,
    lease_until DATETIME(6) NULL,
    next_allowed_at DATETIME(6) NULL,
    held_reason VARCHAR(64) NULL,
    PRIMARY KEY (resource, slot)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS isolated_records (
    isolation_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL PRIMARY KEY,
    uid VARCHAR(128) COLLATE utf8mb4_bin NULL,
    source VARCHAR(64) COLLATE utf8mb4_bin NOT NULL,
    message_id VARCHAR(128) COLLATE utf8mb4_bin NULL,
    digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    send_time DATETIME(6) NULL,
    kind VARCHAR(32) NOT NULL,
    reason VARCHAR(255) NOT NULL,
    window_start DATETIME(6) NULL,
    window_end DATETIME(6) NULL,
    payload JSON NULL,
    created_at DATETIME(6) NOT NULL,
    UNIQUE KEY uq_isolated_version (source, message_id, digest, kind),
    KEY ix_isolated_uid (uid, created_at),
    KEY ix_isolated_kind (kind, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
ALTER TABLE processing_attempts
    ADD COLUMN window_id VARCHAR(36) COLLATE utf8mb4_bin NULL,
    ADD COLUMN claim_epoch BIGINT NULL,
    ADD COLUMN model_state VARCHAR(32) NULL,
    ADD COLUMN request_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NULL,
    ADD COLUMN error_code VARCHAR(64) NULL;
ALTER TABLE processing_attempts
    ADD KEY ix_attempt_model_state (uid, model_state),
    ADD KEY ix_attempt_window (window_id);
