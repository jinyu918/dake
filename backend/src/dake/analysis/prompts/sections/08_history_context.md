# 补充历史聊天背景（history_context）

- `history_context` 是只读历史背景，`items` 中的消息不能因被补取就变成 `evidence_ids` 中的本轮新增证据，也不能仅因补取了背景就要求修改旧事件。
- `links` 中 `event_evidence` 表示原事件已有引用；`neighbor` 仅表示时间邻近的附近聊天，本身不证明事件归属。
- `availability`、`fragments`、`reasons` 明示正文缺失和首尾截断范围，不得补造未提供的正文或结论；`arrival_history_available=false` 不代表完整还原当时已知信息。
- 本轮输出 `evidence_ids` 仍仅允许引用 `new_messages` 和 `context_messages`，绝不引用仅出现在 `history_context` 中的消息。
