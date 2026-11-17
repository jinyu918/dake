-- 任务配置与每次执行独立保存；不改动现有消息、事件及处理记录。
CREATE TABLE IF NOT EXISTS task_definitions (
    task_id VARCHAR(36) COLLATE utf8mb4_bin PRIMARY KEY,
    config JSON NOT NULL,
    frozen_uids JSON NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    version INT NOT NULL DEFAULT 1,
    cursor_at DATETIME(6) NULL,
    next_run_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
CREATE TABLE IF NOT EXISTS task_executions (
    execution_id VARCHAR(36) COLLATE utf8mb4_bin PRIMARY KEY,
    task_id VARCHAR(36) COLLATE utf8mb4_bin NOT NULL,
    status VARCHAR(24) NOT NULL,
    stage VARCHAR(24) NOT NULL,
    state JSON NOT NULL,
    created_at DATETIME(6) NOT NULL,
    finished_at DATETIME(6) NULL,
    KEY ix_task_executions_task (task_id,created_at),
    KEY ix_task_executions_status (status,created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin;
