# 类型专属属性（typed_attributes）

`typed_attributes` 每项结构为 `{type_label, name, value, evidence_ids, quote}`，仅在对应 `type_label` 已采用且本轮有直接原文依据时选填；`value` 必须逐字出现在连续原文 `quote` 中，同一类型下同一属性 `name` 不得重复输出：

{{typed_attribute_catalog}}

补充与兼容字段规则：
- 未提及的属性直接省略，绝不编造数字、单位、币种、日期或结论；字典以外的补充结构化信号放入 `extensions`。
- `assignees` 仅在明确认领（如“我来跟进”可用对应 `sender_id`）或明确指派负责人时填写，否则填 `[]`。
- 历史兼容字段 `status`、`severity` 保持 `null`，`attributes` 保持 `[]`。
