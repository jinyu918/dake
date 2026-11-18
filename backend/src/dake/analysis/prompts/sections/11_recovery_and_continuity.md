# 客户确认恢复与再次发生边界（continuity）

每个候选提供 `continuity` 字段，无恢复或复发边界事实时填 `null`；非空结构为 `{kind, previous_event_id, evidence: [{message_id, quote}]}`（这些属于归属依据，不是事件状态枚举）：

- **`confirm_recovery`**（配 `decision="update"`，`previous_event_id=null`）
  - 只有 `confirmed_sender_role="customer"` 且 `sender_kind` 不是 `bot` 的本轮消息中，由客户本人明确确认该问题已经恢复才有效；内部修复、内部复测、转述客户已恢复、自动通知、“收到/谢谢”、预计恢复均不算；身份未确认时保留事实并选 `review`。
  - 恢复证据同步写入本候选 `changes`，时间保存确认消息的 `send_time`，不猜测实际恢复时刻，也不自动关闭工单。
- **`recurrence`**（配 `decision="new"`，`referenced_event_id=null`，`previous_event_id` 指向本次已有候选中的前次事件）
  - 必须同时具备可信的客户恢复确认，以及本轮明确说明同一对象或问题在恢复后再次发生的原文；仅有关键词相同、相隔数天、“又”或“还是”均不够，没有恢复记录也不能默认合并或拆单。
  - 若同一条新消息明确回顾“之前确已恢复，现在再次发生”，必须拆成 `confirm_recovery`（`update` 原事件）和 `recurrence`（`new` 新事件）两个候选，各自引用对应原文；不同故障独立 `new`，不能只因同产品就标成复发。
- **`historical`**（配 `decision="update"`，`previous_event_id=null`）
  - 本轮明确回顾恢复前的情况，只给原事件补充历史证据，不修改当前结论。
- **`retract_recovery`**（配 `decision="update"`，`previous_event_id=null`）
  - 必须由已确认身份的客户明确纠正先前恢复结论（实际一直没好），撤销恢复记录；更正摘要中已有恢复片段时使用 `correction`，未包含该结论时追加更正事实。
- **`uncertain`**（配 `decision="review"`，`previous_event_id=null`）
  - 恢复真伪、指代对象或发生先后无法核对时使用；`changes=[]`，备选事件放入 `possible_event_ids`。

除 `recurrence` 外 `previous_event_id` 必须为 `null`；所有 `continuity.evidence` 只能引用本轮直接证据，且明确动作须同步在 `changes` 引文中体现。
