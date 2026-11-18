"""稳定的 HTTP 数据合约；为前端生成类型，不在示例中放入真实聊天。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ..domain.customer_policy import CustomerSegment
from ..domain.manual_controls import ControlledField, ManualDecisionRequest, ManualFieldAction
from ..domain.models import EVENT_LABELS, PENDING_TYPE, SenderKind, SenderRole, normalize_event_payload
from ..domain.priority_policy import PRIORITIES, PriorityLevel
from ..domain.status_policy import LIFECYCLE_STATUSES, RESOLUTIONS, WAITING_REASONS, LifecycleStatus, Resolution, WaitingReason


class APIModel(BaseModel):
    """响应总会包含缺省字段；输入仍按校验模式保留可选参数语义。"""

    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=True)


class TimeWindow(APIModel):
    """业务窗口统一使用上海时区的左闭右开时间戳。"""

    start: str
    end: str


class EvidenceResponse(APIModel):
    """可回到本地消息存储核验的证据，接口不提供完整原始响应。"""

    source: str
    message_id: str
    send_time: str
    excerpt: str
    content: str | None = None
    digest: str | None = None
    platform: str = ""
    platform_label: str = ""
    conversation_id: str = ""
    conversation_name: str = ""
    sender_name: str = ""
    internal_hint: int | None = None
    sender_role: SenderRole = "unknown"
    sender_kind: SenderKind = "unknown"


class ContextMessageResponse(EvidenceResponse):
    """事件所在群聊前后邻近的原始消息视图，标明是否属于该事件已采纳证据。"""

    is_evidence: bool = False


class EventContextResponse(APIModel):
    """事件前后群聊上下文只读响应，严格限定在同客户及同群范围内。"""

    uid: str
    stream: str
    event_id: str
    window: TimeWindow
    items: list[ContextMessageResponse] = Field(default_factory=list)
    truncated: bool = False


class ConversationRef(APIModel):
    """事件覆盖到的群聊，供列表和筛选展示，不改变事件身份。"""

    id: str
    name: str
    platform: str
    platform_label: str


class ManualControlResponse(APIModel):
    """单个受控核心字段的人工锁定与待核对冲突视图。"""

    field: ControlledField
    locked: bool = False
    operator: str | None = None
    reason: str | None = None
    decided_at: str | None = None
    adopted_value: JsonValue | None = None
    pending_conflict: dict[str, JsonValue] | None = None


class ManualOperationResponse(APIModel):
    """已提交的人工裁定操作收据。"""

    operation_id: str
    request_digest: str
    operator: str
    reason: str
    decided_at: str
    version: int
    actions: list[dict[str, JsonValue]] = Field(default_factory=list)


class EventSnapshot(APIModel):
    """一个事件版本的完整业务字段，不递归嵌入所有修订历史。"""

    @model_validator(mode="before")
    @classmethod
    def normalize_snapshot(cls, value):
        if isinstance(value, dict):
            return normalize_event_payload(value)
        return value

    event_id: str
    uid: str
    stream: str
    type: str
    types: list[str] = Field(default_factory=list)
    type_evidence: dict[str, list[dict[str, JsonValue]]] = Field(default_factory=dict)
    removed_types: dict[str, list[dict[str, JsonValue]]] = Field(default_factory=dict)
    classification_evidence_status: Literal["已记录", "未记录"] = "未记录"
    classification_status: Literal["已分类", "待分类"] = "已分类"
    other_reason: dict[str, JsonValue] | None = None
    typed_attributes: dict[str, dict[str, dict[str, JsonValue]]] = Field(default_factory=dict)
    topic_key: str | None
    start_time: str
    end_time: str
    status: str
    lifecycle_status: LifecycleStatus = "状态未明"
    waiting_reason: WaitingReason | None = None
    resolution: Resolution | None = None
    status_detail: dict[str, JsonValue] | None = None
    severity: str
    priority: PriorityLevel = "待评估"
    base_priority: PriorityLevel = "待评估"
    priority_detail: dict[str, JsonValue] | None = None
    summary: str
    created_on: str
    assignees: list[str] = Field(default_factory=list)
    evidence: list[EvidenceResponse] = Field(default_factory=list)
    attributes: dict[str, JsonValue] = Field(default_factory=dict)
    extensions: dict[str, JsonValue] = Field(default_factory=dict)
    review_flag: str | None = None
    review_items: list[dict[str, JsonValue]] = Field(default_factory=list)
    version: int = 0
    manual_controls: dict[str, ManualControlResponse] = Field(default_factory=dict)
    manual_operations: list[ManualOperationResponse] = Field(default_factory=list)
    closed_at: str | None = None
    recovery: "RecoveryConfirmation | None" = None
    recurrence_of: "RecurrenceLink | None" = None
    schema_version: int = 1


class ChangeEvidence(APIModel):
    """变化当时保存的证据定位和引文，不拿后来编辑的正文替换。"""

    source: str
    message_id: str
    digest: str
    conversation_id: str
    send_time: str
    quote: str


class ContinuityEvidence(ChangeEvidence):
    """恢复或再次发生的直接依据，保留当时的账号与消息版本。"""

    sender_id: str


class CustomerRecoveryEvidence(ContinuityEvidence):
    """已确认客户账号的明确反馈，不采用展示身份回退。"""

    sender_role: Literal["customer"]


class RecoveryConfirmation(APIModel):
    """确认消息的时间与证据；不推测故障实际恢复的精确时刻。"""

    confirmed_at: str
    evidence: list[CustomerRecoveryEvidence]
    identity_basis: Literal["confirmed_account"]
    policy_version: str


class RecurrenceLink(APIModel):
    """新事件指向前次事件，保留建立关联时的恢复依据。"""

    event_id: str
    reason: str
    recovery: RecoveryConfirmation
    evidence: list[ContinuityEvidence]
    policy_version: str


class EventChange(APIModel):
    """新增事实、摘要局部替换及其实际影响。"""

    kind: Literal["progress", "confirmation", "correction"]
    effect: Literal["progress", "confirmation", "correction", "evidence_only"]
    text: str
    replaces: str | None = None
    evidence: list[ChangeEvidence]


class EventRevision(APIModel):
    """记录哪一次处理、哪些消息或人工裁定创建或更新了事件。"""

    action: Literal["created", "updated"]
    source: Literal["automatic", "manual"] = "automatic"
    window_start: str
    window_end: str
    message_ids: list[str]
    snapshot: EventSnapshot | None = None
    revision_no: int | None = Field(default=None, ge=1)
    recorded_at: str | None = None
    change_kind: Literal["created", "progress", "confirmation", "correction", "evidence_only", "classification", "manual"] | None = None
    change_summary: str | None = None
    summary_changed: bool | None = None
    changed_fields: list[str] = Field(default_factory=list)
    changes: list[EventChange] = Field(default_factory=list)
    manual_operation: ManualOperationResponse | None = None
    policy_version: str | None = None


class AlertReminderPreview(APIModel):
    message_id: str
    at: str
    priority: str
    route: str
    kind: str


class AlertPreview(APIModel):
    source: str
    alert_id: str
    round_id: str
    state: str
    priority: str
    queue: str
    pending: bool
    original_severity: str | None = None
    triggered_at: str | None = None
    recovered_at: str | None = None
    reminders: list[AlertReminderPreview]


class AlertPreviewList(APIModel):
    items: list[AlertPreview]


class EventResponse(EventSnapshot):
    """事件详情及其变更历史；周期结果中的事件保持当时的版本。"""

    updated_by: list[EventRevision] = Field(default_factory=list)
    conversations: list[ConversationRef] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)


class EventListResponse(APIModel):
    """当前客户、当前分析流的事件列表。"""

    items: list[EventResponse]
    total: int


class ManualDecisionResponse(APIModel):
    """人工裁定提交或查询结果，包含更新后的事件与对应收据。"""

    event: EventResponse
    operation: ManualOperationResponse
    idempotent_replay: bool = False


class ReviewResponse(APIModel):
    """指代或身份不确定时保留复核项，不自动拆分或合并事件。"""

    review_flag: str
    type: str
    message_ids: list[str]
    possible_event_ids: list[str]
    reason: str


class RecordedMessageRef(APIModel):
    """当时归属记录的定位与引文，缺失字段保持空值。"""

    source: str | None = None
    message_id: str | None = None
    digest: str | None = None
    conversation_id: str | None = None
    send_time: str | None = None
    quote: str | None = None
    role: str | None = None


class RecordedAttribution(APIModel):
    """模型建议动作与程序最终动作分开；不是模型完整输出或内部思维。"""

    requested_action: str | None = None
    action: str | None = None
    event_id: str | None = None
    possible_event_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    revision_no: int | None = None
    summary_changed: bool | None = None
    change_kind: str | None = None
    basis: list[RecordedMessageRef] = Field(default_factory=list)
    new_evidence: list[RecordedMessageRef] = Field(default_factory=list)


class EventChangeBatch(APIModel):
    """已持久化且有本事件关联依据的一个周期，不代表每个阶段都被记录。"""

    run_id: str
    cycle_index: int
    window: TimeWindow
    processed: int | None = None
    counts_complete: bool
    recovered_from_history: bool
    created: bool
    updated: bool
    attributions: list[RecordedAttribution]
    revision_indexes: list[int]


class ScopedChangeTask(APIModel):
    """范围内任务不等于本事件的处理批次；失败状态不能证明事务回滚。"""

    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    created_at: str | None = None
    total_windows: int
    completed_windows: int
    current_window: TimeWindow | None = None
    current_scope_confirmed: bool
    error: str | None = None
    recovered_by: str | None = None
    recovery_link_unavailable: bool


class ExecutionStage(APIModel):
    """实际保存的阶段状态与观察时间，不是模型思维过程或精确提交时刻。"""

    stage: str
    status: str
    recorded_at: str | None = None
    call_no: int | None = None
    error_type: str | None = None


class ExecutionInput(APIModel):
    """消息清单或实际请求输入；角色与版本明确，未保存的正文为空。"""

    origin: str
    call_no: int | None = None
    new_messages: list[RecordedMessageRef]
    auxiliary_messages: list[RecordedMessageRef]


class ExecutionCandidate(APIModel):
    """实际检索候选；被纳入请求也不表示程序决定更新它。"""

    event_id: str
    summary: str | None = None
    status: str | None = None
    hit_reasons: list[str] = Field(default_factory=list)
    selected_for_request: bool | None = None


class ExecutionFragment(APIModel):
    """保存当时的必要片段及原文偏移，不拼造省略的内容。"""

    start: int
    end: int
    text: str


class ExecutionContextLink(APIModel):
    event_id: str
    kind: str


class ExecutionContext(RecordedMessageRef):
    availability: str | None = None
    truncated: bool | None = None
    fragments: list[ExecutionFragment] = Field(default_factory=list)
    links: list[ExecutionContextLink] = Field(default_factory=list)
    selected_for_request: bool | None = None


class ExecutionSuggestion(APIModel):
    """已保存的结构化模型建议，尚不表示被程序采纳或已提交。"""

    proposal_id: str | None = None
    call_no: int | None = None
    action: str | None = None
    event_id: str | None = None
    possible_event_ids: list[str] = Field(default_factory=list)
    summary: str | None = None
    status: str | None = None
    reason: str | None = None
    facts: list[str] = Field(default_factory=list)
    basis: list[RecordedMessageRef] = Field(default_factory=list)


class ExecutionState(APIModel):
    """程序拟变化的已记录值，不能替代事务收据和持久修订。"""

    event_id: str | None = None
    summary: str | None = None
    status: str | None = None


class ExecutionDecision(APIModel):
    proposal_id: str | None = None
    action: str | None = None
    event_id: str | None = None
    possible_event_ids: list[str] = Field(default_factory=list)
    reason: str | None = None
    contributed_to_revision: bool | None = None
    change_scope: str | None = None
    before: ExecutionState | None = None
    after: ExecutionState | None = None
    added_evidence: list[RecordedMessageRef] = Field(default_factory=list)


class ExecutionDelivery(APIModel):
    destination: str
    status: str
    observed_at: str | None = None


class EventExecution(APIModel):
    """同客户同范围的当前事件关联执行；执行状态、提交收据和展示状态独立。"""

    attempt_id: str
    run_id: str | None = None
    window_index: int | None = None
    window: TimeWindow
    outcome: str
    created_at: str | None = None
    finished_at: str | None = None
    retry_of: str | None = None
    retry_link_unavailable: bool
    related_by: list[str]
    error_type: str | None = None
    stages: list[ExecutionStage]
    inputs: list[ExecutionInput]
    candidates: list[ExecutionCandidate]
    contexts: list[ExecutionContext]
    context_reasons: list[str]
    context_omitted: int | None = None
    suggestions: list[ExecutionSuggestion]
    decisions: list[ExecutionDecision]
    commit_status: str
    receipt_kind: str | None = None
    receipt_prepared_at: str | None = None
    processed: int | None = None
    created: bool
    updated: bool
    revision_indexes: list[int]
    revision_link_unavailable: bool
    delivery: list[ExecutionDelivery]


class EventChangesResponse(APIModel):
    """只读变化面板；任务分页与事件版本分别提供，不伪造执行追踪。"""

    uid: str
    stream: str
    event_id: str
    details_included: bool = True
    batches: list[EventChangeBatch]
    tasks: list[ScopedChangeTask]
    revisions: list[EventRevision]
    total_runs: int
    offset: int
    next_offset: int | None = None
    executions: list[EventExecution] = Field(default_factory=list)
    execution_records_available: bool = False
    total_executions: int = 0
    execution_offset: int = 0
    next_execution_offset: int | None = None


class WatermarkResponse(APIModel):
    """辅助水位提示；已处理与否仍以数据库内的消息摘要为准。"""

    next_start: str | None = None
    last_send_time: str | None = None
    last_message_id: str | None = None


class CycleResponse(APIModel):
    """一个周期的实际变化；恢复中断记录时明确标记来源。"""

    uid: str
    stream: str
    window: TimeWindow
    ingested: int = 0
    processed: int = 0
    created: list[str] = Field(default_factory=list)
    updated: list[str] = Field(default_factory=list)
    review: list[ReviewResponse] = Field(default_factory=list)
    events: list[EventResponse] = Field(default_factory=list)
    watermark: WatermarkResponse = Field(default_factory=WatermarkResponse)
    filter_audit: list[dict[str, JsonValue]] = Field(default_factory=list)
    recovered_from_history: bool = False
    counts_complete: bool = True
    skipped_empty: bool = False


class CustomerResponse(APIModel):
    """官方来源客户的消息、事件统计、等级分层和分析范围。"""

    model_config = ConfigDict(extra="ignore", json_schema_serialization_defaults_required=False)

    uid: str
    label: str
    message_count: int
    event_count: int
    archived_event_counts: dict[str, int] = Field(default_factory=dict, description="本客户历史分析版本的已保存事件数")
    stream: str
    level: str | None = None
    level_state: str = "unknown"
    segment: CustomerSegment = "pending"
    is_super_large: bool = False
    tam: str | None = None
    tam_b: str | None = None
    group_count: int = 0
    group_names: list[str] = Field(default_factory=list)
    latest_event_time: str | None = None


class ConfigResponse(APIModel):
    """界面可见配置；不返回数据库密码、连接串或内部取数响应。"""

    uid: str
    start: str
    end: str
    interval: str
    context_lookback: str
    match_window: str
    ingest_lookback: str
    closed_grace: str
    event_types: list[str]
    filter_event_types: list[str] = Field(default_factory=lambda: [*EVENT_LABELS, PENDING_TYPE])
    lifecycle_statuses: list[str] = Field(default_factory=lambda: list(LIFECYCLE_STATUSES))
    waiting_reasons: list[str] = Field(default_factory=lambda: list(WAITING_REASONS))
    resolutions: list[str] = Field(default_factory=lambda: list(RESOLUTIONS))
    priorities: list[str] = Field(default_factory=lambda: list(PRIORITIES))
    provider: Literal["stub", "xhub"] = "stub"
    customers: list[CustomerResponse]


class NormalizedRunRequest(APIModel):
    """已校验并冻结的请求，范围变化不会受之后修改 .env 的影响。"""

    mode: Literal["official"]
    uid: str
    start: str
    end: str
    interval: str
    offline: bool
    context_lookback: str
    match_window: str
    closed_grace: str
    ingest_lookback: str
    default_status: str
    default_severity: str
    closed_statuses: list[str]
    provider: Literal["stub", "xhub"] = "stub"
    llm_model: str | None = None
    llm_max_tokens: int | None = None
    llm_timeout_seconds: int | None = None
    analysis_stream: str | None = None


class RunResponse(APIModel):
    """持久化任务状态；列表省略 cycles 内容，详情提供完整周期结果。"""

    run_id: str
    uid: str
    mode: Literal["official"]
    status: Literal["queued", "running", "succeeded", "failed"]
    request: NormalizedRunRequest
    total_windows: int
    completed_windows: int
    current_window: TimeWindow | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    cycles: list[CycleResponse] = Field(default_factory=list)


class RunListResponse(APIModel):
    """按创建时间倒序提供任务，刷新页面后仍能看到进度。"""

    items: list[RunResponse]
    total: int


class HealthResponse(APIModel):
    """本地服务健康状态，不携带运行凭据。"""

    status: Literal["ok"] = "ok"


class ErrorResponse(APIModel):
    """统一安全错误格式，避免验证错误回显请求或聊天内容。"""

    detail: str


class CustomerProfile(APIModel):
    """已保存客户资料及统一等级分类，未知与刷新失败分别展示。"""

    segment: CustomerSegment
    is_super_large: bool = False
    uid: str
    level: str | None = None
    rank_value: str | None = None
    tam: str | None = None
    tam_b: str | None = None
    level_state: str
    tam_state: str
    level_error: str | None = None
    tam_error: str | None = None
    level_synced_at: str | None = None
    tam_synced_at: str | None = None
    attempted_at: str
    created_at: str
    updated_at: str
    group_count: int


class CustomerProfileList(APIModel):
    """客户分页列表。"""

    items: list[CustomerProfile]
    total: int


class CustomerGroup(APIModel):
    """群归属与候选依据，不含聊天正文。"""

    source: str
    group_id: str
    group_name: str
    uid: str | None = None
    status: Literal["confirmed", "review"]
    reason: str
    candidate_uids: list[str]
    first_seen_at: str
    last_seen_at: str
    synced_at: str


class CustomerGroupList(APIModel):
    """群映射分页列表。"""

    items: list[CustomerGroup]
    total: int


class CustomerSyncRequest(APIModel):
    """手动同步所选上海时间范围，不触发分析。"""

    model_config = ConfigDict(extra="forbid")
    start: str = Field(min_length=1, max_length=64)
    end: str = Field(min_length=1, max_length=64)


class CustomerSyncScope(APIModel):
    """冻结的同步窗口设置。"""

    start: str
    end: str
    interval: str


class CustomerSyncCycle(APIModel):
    """已实际提交的周期计数，不代表累计客户数。"""

    window: TimeWindow
    messages: int
    groups: int
    customers: int
    new_customers: int
    refreshed_customers: int
    new_groups: int
    review_groups: int
    profile_errors: int
    lookup_errors: int


class CustomerSyncResponse(APIModel):
    """复用持久任务的客户同步进度。"""

    run_id: str
    status: Literal["queued", "running", "succeeded", "failed"]
    request: CustomerSyncScope
    total_windows: int
    completed_windows: int
    current_window: TimeWindow | None = None
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    cycles: list[CustomerSyncCycle] = Field(default_factory=list)


class CustomerSyncList(APIModel):
    """客户同步记录列表。"""

    items: list[CustomerSyncResponse]
    total: int
