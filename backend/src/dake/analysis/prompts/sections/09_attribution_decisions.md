# 正式增量归属决策（decision 与 basis）

按每个候选给出 `new`、`update`、`duplicate`、`review` 四选一 `decision`，并用一句简短客观事实填写 `decision_reason`（不输出思考过程）：

1. **四类决策定义**
   - **`new`（新建独立事项）**：本轮新增证据明确成立一个独立事项，`referenced_event_id=null`、`possible_event_ids=[]`。候选为空、关键词相同或时间相隔较久，均不能独自证明 `new`。
   - **`update`（更新已有事项）**：证据能认回 `existing_events` 中的同一旧事项（必填 `referenced_event_id`），且本轮新增了反馈、排查、处理、标签、属性、状态或优先级事实：
     - 同一批次指向同一旧事件的进展合并为一个候选；独立事项不得仅按 `topic_key` 合并；类型标签差异不能单独否定事件关联。
     - 当新消息使用口语指代（如“上面那个”“刚才发的”“这几个 request”“查得怎么样了”）时，必须结合 `context_messages` 与 `existing_events` 核对同群近期事项并优先 `update` 原 `event_id`，严禁因指代短句未重复前文技术名词而拆成新事件。
     - “今天仍失败”属于新一次状态确认，应选 `update` 而非 `duplicate`；已解决但尚无客户恢复确认的事项，若后续反馈同一问题仍未解决，使用 `update` 重开为 `跟进中`，不新建。
   - **`duplicate`（重复无新增事实）**：已明确属于某旧事项（必填 `referenced_event_id`），但本轮仅复述、转发或确认收到，没有新增业务事实或状态变化。不关联任何事项的普通寒暄直接忽略（返回 `candidates=[]`，无需建 `duplicate`）。
   - **`review`（待人工确认）**：指代不清、多个候选难以区分、关键依据缺失，或恢复及再次发生的边界不明确；`referenced_event_id=null`，在 `possible_event_ids` 列出输入中有依据的备选事件 ID，不猜测新建。

2. **归属依据（`basis`）与字段证据边界**
   - `basis` 为 `[{message_id, quote}]`，引用支持本次归属决定的连续原文片段，`message_id` 必须带完整来源前缀。
   - 明确决定（`new`/`update`/`duplicate`）至少提供一条 `new_messages` 依据；`update` 与 `duplicate` 还必须提供指向目标旧事件的旧证据（可从 `context_messages` 或 `history_context` 已提供的 `fragments` 中引用，不可引用截断之外或缺失的正文）。
   - 邻域聊天仅作参考，不能只凭同群、时间相近或关键词相同证明同一事件。
   - `assignees`、`typed_attributes`、`status_proposal`、`priority_proposal`、`extensions` 仍须有本轮直接证据支持；无新依据时保持空值，重复消息不得输出新增状态事实。
