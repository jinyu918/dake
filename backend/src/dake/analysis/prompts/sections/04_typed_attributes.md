# 跟进人员与兼容字段纪律

- `assignees` 仅在聊天中明确认领（如“我来跟进”可使用对应 `sender_id`）或明确指派跟进负责人时填写，否则保持空列表 `[]`。
- 结构化产品与业务指标由独立的动态配置模块负责提取；主归属输出中旧版兼容字段 `typed_attributes`、`attributes`、`extensions` 统一保持空列表 `[]`，`status`、`severity` 统一保持 `null`。
