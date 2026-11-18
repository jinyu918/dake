ALTER TABLE events
    ADD COLUMN priority_due_at DATETIME(6) NULL,
    ADD INDEX idx_item_priority_due (priority_due_at, event_id);
