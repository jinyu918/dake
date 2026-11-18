CREATE TABLE alert_preview_rounds (
    source VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    alert_id VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    round_id VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    payload JSON NOT NULL,
    PRIMARY KEY (source, alert_id, round_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE alert_preview_deliveries (
    source VARCHAR(96) COLLATE utf8mb4_bin NOT NULL,
    message_id VARCHAR(255) COLLATE utf8mb4_bin NOT NULL,
    PRIMARY KEY (source, message_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
