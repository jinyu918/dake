上一次 JSON 未通过程序校验：{{validation_error}}。
请仅修正格式、枚举约束、重复属性名或不合法证据；仍以最初提供的消息和事件为事实依据，保留有效事件，不新增猜测。

修正核对要点：
1. `typed_attributes` 的 `value` 必须逐字来自连续 `quote`，同一类型下属性 `name` 不得重复；
2. `status_proposal`、`priority_proposal`、`type_labels`、`basis` 与 `changes` 的 `quote` 必须是对应消息的连续子串；
3. 逐一处理报错反馈中列出的所有字段位置，返回完整的修正后 JSON 对象。
