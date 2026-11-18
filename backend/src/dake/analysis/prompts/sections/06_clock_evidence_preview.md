# 服务时钟证据（clock_evidence）

clock_evidence 只引用本轮实际发生的动作，逐项输出 {kind, owner, next_step, review_at, evidence_ids, quote}。handoff 需要同时逐字引用负责人和下一步；progress 仅限明确诊断结果或已执行的处置；production_fault 表示有生产故障事实。external_wait 需要逐字引用必要的客户或第三方材料请求和明确的下次复查时间，用上海时间 ISO 表示 review_at；resume 表示已恢复推进。无取值填 null，收到、内部转交、催促、自动分配均非接手或进展。
