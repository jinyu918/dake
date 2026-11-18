# 历史候选事件边界（existing_events）

- `existing_events` 是只读历史背景，不是新增消息或预设归属决定；零命中不独自证明应当 `new`，多命中也不代表应当合并。
- `hit_reasons` 只是检索召回线索而非已确认事实；历史候选中的 `evidence_ids` 仅作索引参考，未提供原文的 ID 不得用作本轮证据。
- `history_retrieval.incomplete=true` 表示受预算、版本或快照限制，候选列表可能不完整。
- 较早或已关闭的事件同样可作为背景参考；是否允许更新仍严格遵守归属决策与恢复边界规则。
