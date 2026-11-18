# 受影响客户（affected_customers）

affected_customers 仅评价报告 UID 的本窗可写消息，不能获取其他客户聊天。逐项给出 {customer, uid, status, impact_quote, evidence_ids, quote}。customer 必须逐字出现。confirmed 仅限原文用 UID 标记完整写出另一客户的 UID 且明确描述同一故障已经给它造成的影响，impact_quote 是 quote 内的原文。名称无法核对为 UID、疑似波及、共享服务、未来风险或影响尚未发生时均列 pending。原文没有 UID 时用 null；等待核实不得提档。不要把工单号、项目号或任意数字误作客户 UID。不输出客户等级。
