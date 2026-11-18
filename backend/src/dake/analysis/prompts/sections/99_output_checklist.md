# 输出协议与提交前自检

{{schema_version_rule}}输出前逐项自检：
- 每个候选至少包含一条 `new_messages` 直接证据，独立验收目标已拆分，闲聊与孤立机器人告警未误建事项。
- 每个采用的标签在 `type_labels` 中均有独立连续原文 `quote`；`typed_attributes` 的 `value` 逐字出现在对应 `quote` 中，且同一类型下属性 `name` 无重复。
- `status_proposal` 与 `priority_proposal` 满足枚举与互斥约束，所有 `quote` 均为对应消息的连续子串。{{mode_checklist_tail}}
