# 事项定级证据（priority_proposal）

本轮有客观影响、客观期限、明显情绪升级或强催办的直接证据时按输出 Schema 填写 `priority_proposal`；更新旧事项且本轮无新线索时填 null（review/duplicate 填 null）。不读取或猜测客户等级：
- impact 取值：critical（已证实大范围关键生产中断且无绕行，或正在发生重大安全、资损事故）、high（核心生产链路受阻但未达严重条件）、medium（局部降级、部分用户受影响或有可用绕行）、low（咨询、一般需求或无当前生产影响的调整）、unknown（证据不足）。报错本身不证明影响范围。
- urgency 取值：immediate（明确需要立即止损）、soon（有明确近时限）、normal（常规可计划）、unknown（时限不明）。客户级别和语气不证明影响或客观时限。
- 用 impact_evidence、urgency_evidence 分别引用支持本轮已知判断的原文，结构为 {evidence_ids, quote}。没有对应依据时该项判断填 unknown，证据填 null。
- escalated_emotion 和 strong_followup 引用证据，缺依据填 null。只引用报告 UID 下已确认客户本人就当前事项的直接发言。前者需要明确的强烈焦虑或愤怒，后者需要重复催办或明确要求升级、负责人介入。单句“尽快”、表情、机器人通知、转述或无关消息均不成立。
- 只有客户响应信号时，客观维度可均为 unknown。根级 quote 仍须是候选消息的连续原文。


明显情绪升级填 `escalated_emotion`，强催办填 `strong_followup`。证据只引用最新一条已确认客户就本事项发出的 `question` 原话，`evidence_ids` 填这一条消息的 ID。仅因催办新增提案时，客观维度缺少新证据填 `unknown`。
明确要求升级或负责人介入可直接成立。以重复催办为依据时，`strong_followup.previous_message_id` 引用同一待回复轮次的更早催办，可以引用已保存 `reply_round` 的原消息，格式 `来源:消息ID`。普通的单句“尽快”、表情、转述或无关消息均不足以提档。
