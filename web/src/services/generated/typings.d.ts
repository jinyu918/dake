declare namespace API {
  type AlertPreview = {
    /** Source */
    source: string;
    /** Alert Id */
    alert_id: string;
    /** Round Id */
    round_id: string;
    /** State */
    state: string;
    /** Priority */
    priority: string;
    /** Queue */
    queue: string;
    /** Pending */
    pending: boolean;
    /** Original Severity */
    original_severity: string | null;
    /** Triggered At */
    triggered_at: string | null;
    /** Recovered At */
    recovered_at: string | null;
    /** Reminders */
    reminders: AlertReminderPreview[];
  };

  type AlertPreviewList = {
    /** Items */
    items: AlertPreview[];
  };

  type AlertReminderPreview = {
    /** Message Id */
    message_id: string;
    /** At */
    at: string;
    /** Priority */
    priority: string;
    /** Route */
    route: string;
    /** Kind */
    kind: string;
  };

  type AnalyticsEventItem = {
    /** Event Id */
    event_id: string;
    /** Uid */
    uid: string;
    /** Customer Level */
    customer_level: string | null;
    /** Summary */
    summary: string;
    /** Primary Type */
    primary_type: string;
    /** Types */
    types: string[];
    /** Priority */
    priority: string;
    /** Lifecycle Status */
    lifecycle_status: string;
    /** Primary Product */
    primary_product: string | null;
    /** Products */
    products: string[];
    /** End Time */
    end_time: string;
    /** Props */
    props: Record<string, any>;
    /** Prop Evidence */
    prop_evidence: Record<string, any>[];
  };

  type AttributeBackfillJob = {
    /** Status */
    status: "idle" | "running" | "completed" | "failed";
    /** Job Id */
    job_id: string;
    result: AttributeBackfillResponse | null;
  };

  type AttributeBackfillRequest = {
    /** Uid */
    uid?: string;
    /** Product Key */
    product_key?: string;
    /** Event Type */
    event_type?: string;
    /** Only Missing */
    only_missing?: boolean;
    /** Limit */
    limit?: number;
    /** Use Llm */
    use_llm?: boolean;
  };

  type AttributeBackfillResponse = {
    /** Stream */
    stream: string;
    /** Scanned */
    scanned: number;
    /** Updated */
    updated: number;
    /** Llm Enriched */
    llm_enriched: number;
    /** Failed */
    failed: number;
    /** Schema Version */
    schema_version: number;
  };

  type AttributeFieldDeleteRequest = {
    /** Expected Version */
    expected_version: number;
    /** Field Id */
    field_id: string;
  };

  type AttributeFieldInput = {
    /** Scope Type */
    scope_type: "product" | "event_type";
    /** Scope Key */
    scope_key: string;
    /** Field Key */
    field_key: string;
    /** Label */
    label: string;
    /** Data Type */
    data_type: "number" | "enum" | "boolean" | "string";
    /** Unit */
    unit?: string;
    /** Unit Family */
    unit_family?:
      | "none"
      | "count_scale"
      | "million_tokens"
      | "bandwidth_mbps"
      | "storage_tb"
      | "currency_cny"
      | "duration_ms";
    /** Enum Options */
    enum_options?: string[];
    /** Agg Ops */
    agg_ops?: ("sum" | "avg" | "max" | "terms")[];
    /** Applicable Types */
    applicable_types?: string[];
    /** Extraction Hint */
    extraction_hint: string;
    /** Sort Order */
    sort_order?: number;
    /** Enabled */
    enabled?: boolean;
  };

  type AttributeFieldSaveRequest = {
    /** Expected Version */
    expected_version: number;
    /** Field Id */
    field_id?: string | null;
    field: AttributeFieldInput;
  };

  type AttributeFieldSchemaItem = {
    /** Field Id */
    field_id: string;
    /** Scope Type */
    scope_type: "product" | "event_type";
    /** Scope Key */
    scope_key: string;
    /** Field Key */
    field_key: string;
    /** Label */
    label: string;
    /** Data Type */
    data_type: "number" | "enum" | "boolean" | "string";
    /** Unit */
    unit: string;
    /** Unit Family */
    unit_family:
      | "none"
      | "count_scale"
      | "million_tokens"
      | "bandwidth_mbps"
      | "storage_tb"
      | "currency_cny"
      | "duration_ms";
    /** Enum Options */
    enum_options: string[];
    /** Agg Ops */
    agg_ops: ("sum" | "avg" | "max" | "terms")[];
    /** Applicable Types */
    applicable_types: string[];
    /** Extraction Hint */
    extraction_hint: string;
    /** Sort Order */
    sort_order: number;
    /** Enabled */
    enabled: boolean;
    /** Builtin */
    builtin: boolean;
  };

  type AttributeSchemaRegistryResponse = {
    /** Version */
    version: number;
    /** Updated At */
    updated_at: string;
    /** Products */
    products: ProductSchemaItem[];
    /** Fields */
    fields: AttributeFieldSchemaItem[];
    /** Unit Families */
    unit_families: UnitFamilyOption[];
  };

  type ChangeEvidence = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Digest */
    digest: string;
    /** Conversation Id */
    conversation_id: string;
    /** Send Time */
    send_time: string;
    /** Quote */
    quote: string;
  };

  type ConfigResponse = {
    /** Uid */
    uid: string;
    /** Start */
    start: string;
    /** End */
    end: string;
    /** Interval */
    interval: string;
    /** Context Lookback */
    context_lookback: string;
    /** Match Window */
    match_window: string;
    /** Ingest Lookback */
    ingest_lookback: string;
    /** Closed Grace */
    closed_grace: string;
    /** Event Types */
    event_types: string[];
    /** Filter Event Types */
    filter_event_types: string[];
    /** Lifecycle Statuses */
    lifecycle_statuses: string[];
    /** Waiting Reasons */
    waiting_reasons: string[];
    /** Resolutions */
    resolutions: string[];
    /** Priorities */
    priorities: string[];
    /** Provider */
    provider: "stub" | "xhub";
    /** Customers */
    customers: CustomerResponse[];
  };

  type ContextMessageResponse = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Send Time */
    send_time: string;
    /** Excerpt */
    excerpt: string;
    /** Content */
    content: string | null;
    /** Digest */
    digest: string | null;
    /** Platform */
    platform: string;
    /** Platform Label */
    platform_label: string;
    /** Conversation Id */
    conversation_id: string;
    /** Conversation Name */
    conversation_name: string;
    /** Sender Name */
    sender_name: string;
    /** Internal Hint */
    internal_hint: number | null;
    /** Sender Role */
    sender_role: "internal" | "support" | "customer" | "external" | "unknown";
    /** Sender Kind */
    sender_kind: "human" | "bot" | "unknown";
    /** Is Evidence */
    is_evidence: boolean;
  };

  type ContinuityEvidence = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Digest */
    digest: string;
    /** Conversation Id */
    conversation_id: string;
    /** Send Time */
    send_time: string;
    /** Quote */
    quote: string;
    /** Sender Id */
    sender_id: string;
  };

  type ConversationRef = {
    /** Id */
    id: string;
    /** Name */
    name: string;
    /** Platform */
    platform: string;
    /** Platform Label */
    platform_label: string;
  };

  type CustomerGroup = {
    /** Source */
    source: string;
    /** Group Id */
    group_id: string;
    /** Group Name */
    group_name: string;
    /** Uid */
    uid: string | null;
    /** Status */
    status: "confirmed" | "review";
    /** Reason */
    reason: string;
    /** Candidate Uids */
    candidate_uids: string[];
    /** First Seen At */
    first_seen_at: string;
    /** Last Seen At */
    last_seen_at: string;
    /** Synced At */
    synced_at: string;
  };

  type CustomerGroupList = {
    /** Items */
    items: CustomerGroup[];
    /** Total */
    total: number;
  };

  type CustomerProfile = {
    /** Segment */
    segment: "large" | "non_large" | "pending";
    /** Is Super Large */
    is_super_large: boolean;
    /** Uid */
    uid: string;
    /** Level */
    level: string | null;
    /** Rank Value */
    rank_value: string | null;
    /** Tam */
    tam: string | null;
    /** Tam B */
    tam_b: string | null;
    /** Level State */
    level_state: string;
    /** Tam State */
    tam_state: string;
    /** Level Error */
    level_error: string | null;
    /** Tam Error */
    tam_error: string | null;
    /** Level Synced At */
    level_synced_at: string | null;
    /** Tam Synced At */
    tam_synced_at: string | null;
    /** Attempted At */
    attempted_at: string;
    /** Created At */
    created_at: string;
    /** Updated At */
    updated_at: string;
    /** Group Count */
    group_count: number;
  };

  type CustomerProfileList = {
    /** Items */
    items: CustomerProfile[];
    /** Total */
    total: number;
  };

  type CustomerRecoveryEvidence = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Digest */
    digest: string;
    /** Conversation Id */
    conversation_id: string;
    /** Send Time */
    send_time: string;
    /** Quote */
    quote: string;
    /** Sender Id */
    sender_id: string;
    /** Sender Role */
    sender_role: string;
  };

  type CustomerResponse = {
    /** Uid */
    uid: string;
    /** Label */
    label: string;
    /** Message Count */
    message_count: number;
    /** Event Count */
    event_count: number;
    /** Archived Event Counts 本客户历史分析版本的已保存事件数 */
    archived_event_counts?: Record<string, any>;
    /** Stream */
    stream: string;
    /** Level */
    level?: string | null;
    /** Level State */
    level_state?: string;
    /** Segment */
    segment?: "large" | "non_large" | "pending";
    /** Is Super Large */
    is_super_large?: boolean;
    /** Tam */
    tam?: string | null;
    /** Tam B */
    tam_b?: string | null;
    /** Group Count */
    group_count?: number;
    /** Group Names */
    group_names?: string[];
    /** Latest Event Time */
    latest_event_time?: string | null;
  };

  type CustomerSyncCycle = {
    window: TimeWindow;
    /** Messages */
    messages: number;
    /** Groups */
    groups: number;
    /** Customers */
    customers: number;
    /** New Customers */
    new_customers: number;
    /** Refreshed Customers */
    refreshed_customers: number;
    /** New Groups */
    new_groups: number;
    /** Review Groups */
    review_groups: number;
    /** Profile Errors */
    profile_errors: number;
    /** Lookup Errors */
    lookup_errors: number;
  };

  type CustomerSyncList = {
    /** Items */
    items: CustomerSyncResponse[];
    /** Total */
    total: number;
  };

  type CustomerSyncRequest = {
    /** Start */
    start: string;
    /** End */
    end: string;
  };

  type CustomerSyncResponse = {
    /** Run Id */
    run_id: string;
    /** Status */
    status: "queued" | "running" | "succeeded" | "failed";
    request: CustomerSyncScope;
    /** Total Windows */
    total_windows: number;
    /** Completed Windows */
    completed_windows: number;
    current_window: TimeWindow | null;
    /** Created At */
    created_at: string;
    /** Started At */
    started_at: string | null;
    /** Finished At */
    finished_at: string | null;
    /** Error */
    error: string | null;
    /** Cycles */
    cycles: CustomerSyncCycle[];
  };

  type CustomerSyncScope = {
    /** Start */
    start: string;
    /** End */
    end: string;
    /** Interval */
    interval: string;
  };

  type CycleResponse = {
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    window: TimeWindow;
    /** Ingested */
    ingested: number;
    /** Processed */
    processed: number;
    /** Created */
    created: string[];
    /** Updated */
    updated: string[];
    /** Review */
    review: ReviewResponse[];
    /** Events */
    events: EventResponse[];
    watermark: WatermarkResponse;
    /** Filter Audit */
    filter_audit: Record<string, any>[];
    /** Recovered From History */
    recovered_from_history: boolean;
    /** Counts Complete */
    counts_complete: boolean;
    /** Skipped Empty */
    skipped_empty: boolean;
  };

  type ErrorResponse = {
    /** Detail */
    detail: string;
  };

  type EventChange = {
    /** Kind */
    kind: "progress" | "confirmation" | "correction";
    /** Effect */
    effect: "progress" | "confirmation" | "correction" | "evidence_only";
    /** Text */
    text: string;
    /** Replaces */
    replaces: string | null;
    /** Evidence */
    evidence: ChangeEvidence[];
  };

  type EventChangeBatch = {
    /** Run Id */
    run_id: string;
    /** Cycle Index */
    cycle_index: number;
    window: TimeWindow;
    /** Processed */
    processed: number | null;
    /** Counts Complete */
    counts_complete: boolean;
    /** Recovered From History */
    recovered_from_history: boolean;
    /** Created */
    created: boolean;
    /** Updated */
    updated: boolean;
    /** Attributions */
    attributions: RecordedAttribution[];
    /** Revision Indexes */
    revision_indexes: number[];
  };

  type EventChangesResponse = {
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Event Id */
    event_id: string;
    /** Details Included */
    details_included: boolean;
    /** Batches */
    batches: EventChangeBatch[];
    /** Tasks */
    tasks: ScopedChangeTask[];
    /** Revisions */
    revisions: EventRevision[];
    /** Total Runs */
    total_runs: number;
    /** Offset */
    offset: number;
    /** Next Offset */
    next_offset: number | null;
    /** Executions */
    executions: EventExecution[];
    /** Execution Records Available */
    execution_records_available: boolean;
    /** Total Executions */
    total_executions: number;
    /** Execution Offset */
    execution_offset: number;
    /** Next Execution Offset */
    next_execution_offset: number | null;
  };

  type EventContextResponse = {
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Event Id */
    event_id: string;
    window: TimeWindow;
    /** Items */
    items: ContextMessageResponse[];
    /** Truncated */
    truncated: boolean;
  };

  type EventExecution = {
    /** Attempt Id */
    attempt_id: string;
    /** Run Id */
    run_id: string | null;
    /** Window Index */
    window_index: number | null;
    window: TimeWindow;
    /** Outcome */
    outcome: string;
    /** Created At */
    created_at: string | null;
    /** Finished At */
    finished_at: string | null;
    /** Retry Of */
    retry_of: string | null;
    /** Retry Link Unavailable */
    retry_link_unavailable: boolean;
    /** Related By */
    related_by: string[];
    /** Error Type */
    error_type: string | null;
    /** Stages */
    stages: ExecutionStage[];
    /** Inputs */
    inputs: ExecutionInput[];
    /** Candidates */
    candidates: ExecutionCandidate[];
    /** Contexts */
    contexts: ExecutionContext[];
    /** Context Reasons */
    context_reasons: string[];
    /** Context Omitted */
    context_omitted: number | null;
    /** Suggestions */
    suggestions: ExecutionSuggestion[];
    /** Decisions */
    decisions: ExecutionDecision[];
    /** Commit Status */
    commit_status: string;
    /** Receipt Kind */
    receipt_kind: string | null;
    /** Receipt Prepared At */
    receipt_prepared_at: string | null;
    /** Processed */
    processed: number | null;
    /** Created */
    created: boolean;
    /** Updated */
    updated: boolean;
    /** Revision Indexes */
    revision_indexes: number[];
    /** Revision Link Unavailable */
    revision_link_unavailable: boolean;
    /** Delivery */
    delivery: ExecutionDelivery[];
  };

  type EventListResponse = {
    /** Items */
    items: EventResponse[];
    /** Total */
    total: number;
  };

  type EventResponse = {
    /** Event Id */
    event_id: string;
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Type */
    type: string;
    /** Types */
    types: string[];
    /** Type Evidence */
    type_evidence: Record<string, any>;
    /** Removed Types */
    removed_types: Record<string, any>;
    /** Classification Evidence Status */
    classification_evidence_status: "已记录" | "未记录";
    /** Classification Status */
    classification_status: "已分类" | "待分类";
    /** Other Reason */
    other_reason: Record<string, any> | null;
    /** Typed Attributes */
    typed_attributes: Record<string, any>;
    /** Primary Product */
    primary_product: string | null;
    /** Products */
    products: string[];
    /** Structured Props */
    structured_props: Record<string, any>;
    /** Structured Evidence */
    structured_evidence: Record<string, any>[];
    /** Topic Key */
    topic_key: string | null;
    /** Start Time */
    start_time: string;
    /** End Time */
    end_time: string;
    /** Status */
    status: string;
    /** Lifecycle Status */
    lifecycle_status: "状态未明" | "跟进中" | "等待中" | "已解决" | "已取消";
    /** Waiting Reason */
    waiting_reason: "等待客户" | "等待内部支撑" | "等待其他方" | null;
    /** Resolution */
    resolution:
      | "客户确认恢复"
      | "支撑声明已修复"
      | "已答复或已交付"
      | "已拒绝"
      | "已撤回"
      | "确认不成立"
      | null;
    /** Status Detail */
    status_detail: Record<string, any> | null;
    /** Severity */
    severity: string;
    /** Priority */
    priority: "P0" | "P1" | "P2" | "P3" | "待评估";
    /** Base Priority */
    base_priority: "P0" | "P1" | "P2" | "P3" | "待评估";
    /** Priority Detail */
    priority_detail: Record<string, any> | null;
    /** Summary */
    summary: string;
    /** Created On */
    created_on: string;
    /** Assignees */
    assignees: string[];
    /** Evidence */
    evidence: EvidenceResponse[];
    /** Attributes */
    attributes: Record<string, any>;
    /** Extensions */
    extensions: Record<string, any>;
    /** Review Flag */
    review_flag: string | null;
    /** Review Items */
    review_items: Record<string, any>[];
    /** Version */
    version: number;
    /** Manual Controls */
    manual_controls: Record<string, any>;
    /** Manual Operations */
    manual_operations: ManualOperationResponse[];
    /** Closed At */
    closed_at: string | null;
    recovery: RecoveryConfirmation | null;
    recurrence_of: RecurrenceLink | null;
    /** Schema Version */
    schema_version: number;
    /** Updated By */
    updated_by: EventRevision[];
    /** Conversations */
    conversations: ConversationRef[];
    /** Platforms */
    platforms: string[];
  };

  type EventRevision = {
    /** Action */
    action: "created" | "updated";
    /** Source */
    source: "automatic" | "manual";
    /** Window Start */
    window_start: string;
    /** Window End */
    window_end: string;
    /** Message Ids */
    message_ids: string[];
    snapshot: EventSnapshot | null;
    /** Revision No */
    revision_no: number | null;
    /** Recorded At */
    recorded_at: string | null;
    /** Change Kind */
    change_kind:
      | "created"
      | "progress"
      | "confirmation"
      | "correction"
      | "evidence_only"
      | "classification"
      | "manual"
      | null;
    /** Change Summary */
    change_summary: string | null;
    /** Summary Changed */
    summary_changed: boolean | null;
    /** Changed Fields */
    changed_fields: string[];
    /** Changes */
    changes: EventChange[];
    manual_operation: ManualOperationResponse | null;
    /** Policy Version */
    policy_version: string | null;
  };

  type EventSnapshot = {
    /** Event Id */
    event_id: string;
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Type */
    type: string;
    /** Types */
    types: string[];
    /** Type Evidence */
    type_evidence: Record<string, any>;
    /** Removed Types */
    removed_types: Record<string, any>;
    /** Classification Evidence Status */
    classification_evidence_status: "已记录" | "未记录";
    /** Classification Status */
    classification_status: "已分类" | "待分类";
    /** Other Reason */
    other_reason: Record<string, any> | null;
    /** Typed Attributes */
    typed_attributes: Record<string, any>;
    /** Primary Product */
    primary_product: string | null;
    /** Products */
    products: string[];
    /** Structured Props */
    structured_props: Record<string, any>;
    /** Structured Evidence */
    structured_evidence: Record<string, any>[];
    /** Topic Key */
    topic_key: string | null;
    /** Start Time */
    start_time: string;
    /** End Time */
    end_time: string;
    /** Status */
    status: string;
    /** Lifecycle Status */
    lifecycle_status: "状态未明" | "跟进中" | "等待中" | "已解决" | "已取消";
    /** Waiting Reason */
    waiting_reason: "等待客户" | "等待内部支撑" | "等待其他方" | null;
    /** Resolution */
    resolution:
      | "客户确认恢复"
      | "支撑声明已修复"
      | "已答复或已交付"
      | "已拒绝"
      | "已撤回"
      | "确认不成立"
      | null;
    /** Status Detail */
    status_detail: Record<string, any> | null;
    /** Severity */
    severity: string;
    /** Priority */
    priority: "P0" | "P1" | "P2" | "P3" | "待评估";
    /** Base Priority */
    base_priority: "P0" | "P1" | "P2" | "P3" | "待评估";
    /** Priority Detail */
    priority_detail: Record<string, any> | null;
    /** Summary */
    summary: string;
    /** Created On */
    created_on: string;
    /** Assignees */
    assignees: string[];
    /** Evidence */
    evidence: EvidenceResponse[];
    /** Attributes */
    attributes: Record<string, any>;
    /** Extensions */
    extensions: Record<string, any>;
    /** Review Flag */
    review_flag: string | null;
    /** Review Items */
    review_items: Record<string, any>[];
    /** Version */
    version: number;
    /** Manual Controls */
    manual_controls: Record<string, any>;
    /** Manual Operations */
    manual_operations: ManualOperationResponse[];
    /** Closed At */
    closed_at: string | null;
    recovery: RecoveryConfirmation | null;
    recurrence_of: RecurrenceLink | null;
    /** Schema Version */
    schema_version: number;
  };

  type EvidenceResponse = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Send Time */
    send_time: string;
    /** Excerpt */
    excerpt: string;
    /** Content */
    content: string | null;
    /** Digest */
    digest: string | null;
    /** Platform */
    platform: string;
    /** Platform Label */
    platform_label: string;
    /** Conversation Id */
    conversation_id: string;
    /** Conversation Name */
    conversation_name: string;
    /** Sender Name */
    sender_name: string;
    /** Internal Hint */
    internal_hint: number | null;
    /** Sender Role */
    sender_role: "internal" | "support" | "customer" | "external" | "unknown";
    /** Sender Kind */
    sender_kind: "human" | "bot" | "unknown";
  };

  type ExecutionCandidate = {
    /** Event Id */
    event_id: string;
    /** Summary */
    summary: string | null;
    /** Status */
    status: string | null;
    /** Hit Reasons */
    hit_reasons: string[];
    /** Selected For Request */
    selected_for_request: boolean | null;
  };

  type ExecutionContext = {
    /** Source */
    source: string | null;
    /** Message Id */
    message_id: string | null;
    /** Digest */
    digest: string | null;
    /** Conversation Id */
    conversation_id: string | null;
    /** Send Time */
    send_time: string | null;
    /** Quote */
    quote: string | null;
    /** Role */
    role: string | null;
    /** Availability */
    availability: string | null;
    /** Truncated */
    truncated: boolean | null;
    /** Fragments */
    fragments: ExecutionFragment[];
    /** Links */
    links: ExecutionContextLink[];
    /** Selected For Request */
    selected_for_request: boolean | null;
  };

  type ExecutionContextLink = {
    /** Event Id */
    event_id: string;
    /** Kind */
    kind: string;
  };

  type ExecutionDecision = {
    /** Proposal Id */
    proposal_id: string | null;
    /** Action */
    action: string | null;
    /** Event Id */
    event_id: string | null;
    /** Possible Event Ids */
    possible_event_ids: string[];
    /** Reason */
    reason: string | null;
    /** Contributed To Revision */
    contributed_to_revision: boolean | null;
    /** Change Scope */
    change_scope: string | null;
    before: ExecutionState | null;
    after: ExecutionState | null;
    /** Added Evidence */
    added_evidence: RecordedMessageRef[];
  };

  type ExecutionDelivery = {
    /** Destination */
    destination: string;
    /** Status */
    status: string;
    /** Observed At */
    observed_at: string | null;
  };

  type ExecutionFragment = {
    /** Start */
    start: number;
    /** End */
    end: number;
    /** Text */
    text: string;
  };

  type ExecutionInput = {
    /** Origin */
    origin: string;
    /** Call No */
    call_no: number | null;
    /** New Messages */
    new_messages: RecordedMessageRef[];
    /** Auxiliary Messages */
    auxiliary_messages: RecordedMessageRef[];
  };

  type ExecutionStage = {
    /** Stage */
    stage: string;
    /** Status */
    status: string;
    /** Recorded At */
    recorded_at: string | null;
    /** Call No */
    call_no: number | null;
    /** Error Type */
    error_type: string | null;
  };

  type ExecutionState = {
    /** Event Id */
    event_id: string | null;
    /** Summary */
    summary: string | null;
    /** Status */
    status: string | null;
  };

  type ExecutionSuggestion = {
    /** Proposal Id */
    proposal_id: string | null;
    /** Call No */
    call_no: number | null;
    /** Action */
    action: string | null;
    /** Event Id */
    event_id: string | null;
    /** Possible Event Ids */
    possible_event_ids: string[];
    /** Summary */
    summary: string | null;
    /** Status */
    status: string | null;
    /** Reason */
    reason: string | null;
    /** Facts */
    facts: string[];
    /** Basis */
    basis: RecordedMessageRef[];
  };

  type getCustomerSyncParams = {
    run_id: string;
  };

  type getEventChangesParams = {
    event_id: string;
    uid: string;
    stream: string;
    offset?: number;
    execution_offset?: number;
    include_details?: boolean;
  };

  type getEventContextParams = {
    event_id: string;
    uid?: string | null;
    stream?: string | null;
    padding_minutes?: number;
    limit?: number;
  };

  type getEventParams = {
    event_id: string;
    uid?: string | null;
    stream?: string | null;
  };

  type getManualDecisionParams = {
    event_id: string;
    operation_id: string;
    uid?: string | null;
    stream?: string | null;
  };

  type getProductAnalyticsParams = {
    product_key?: string;
    event_type?: string;
    stream?: string | null;
  };

  type getRunParams = {
    run_id: string;
    uid?: string | null;
    stream?: string | null;
  };

  type getTaskExecutionParams = {
    execution_id: string;
  };

  type HealthResponse = {
    /** Status */
    status: string;
  };

  type JsonValue = Record<string, any>;

  type listAlertPreviewsParams = {
    uid?: string | null;
    limit?: number;
  };

  type listCustomerGroupsParams = {
    uid?: string | null;
    review?: boolean;
    offset?: number;
    limit?: number;
  };

  type listCustomerProfilesParams = {
    offset?: number;
    limit?: number;
    keyword?: string;
    segment?: "all" | "super_large" | "large" | "non_large" | "pending";
  };

  type listEventsParams = {
    uid?: string | null;
    stream?: string | null;
    type?: string | null;
    status?: string | null;
    priority?: string | null;
  };

  type listRunsParams = {
    uid?: string | null;
    mode?: string | null;
    stream?: string | null;
  };

  type listTaskExecutionsParams = {
    task_id: string;
  };

  type ManualControlResponse = {
    /** Field */
    field: "types" | "status" | "priority" | "assignees";
    /** Locked */
    locked: boolean;
    /** Operator */
    operator: string | null;
    /** Reason */
    reason: string | null;
    /** Decided At */
    decided_at: string | null;
    adopted_value: JsonValue | null;
    /** Pending Conflict */
    pending_conflict: Record<string, any> | null;
  };

  type ManualDecisionRequest = {
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Expected Version */
    expected_version: number;
    /** Operation Id */
    operation_id: string;
    /** Operator */
    operator: string;
    /** Reason */
    reason: string;
    /** Actions */
    actions: ManualFieldAction[];
  };

  type ManualDecisionResponse = {
    event: EventResponse;
    operation: ManualOperationResponse;
    /** Idempotent Replay */
    idempotent_replay: boolean;
  };

  type ManualFieldAction = {
    /** Field */
    field: "types" | "status" | "priority" | "assignees";
    /** Action */
    action: "set" | "lock" | "unlock" | "adopt_conflict" | "dismiss_conflict";
    /** Types */
    types?: string[] | null;
    /** Status */
    status?: string | null;
    /** Waiting Reason */
    waiting_reason?: string | null;
    /** Resolution */
    resolution?: string | null;
    /** Priority */
    priority?: string | null;
    /** Assignees */
    assignees?: string[] | null;
    /** Locked */
    locked?: boolean | null;
  };

  type ManualOperationResponse = {
    /** Operation Id */
    operation_id: string;
    /** Request Digest */
    request_digest: string;
    /** Operator */
    operator: string;
    /** Reason */
    reason: string;
    /** Decided At */
    decided_at: string;
    /** Version */
    version: number;
    /** Actions */
    actions: Record<string, any>[];
  };

  type NormalizedRunRequest = {
    /** Mode */
    mode: string;
    /** Uid */
    uid: string;
    /** Start */
    start: string;
    /** End */
    end: string;
    /** Interval */
    interval: string;
    /** Offline */
    offline: boolean;
    /** Context Lookback */
    context_lookback: string;
    /** Match Window */
    match_window: string;
    /** Closed Grace */
    closed_grace: string;
    /** Ingest Lookback */
    ingest_lookback: string;
    /** Default Status */
    default_status: string;
    /** Default Severity */
    default_severity: string;
    /** Closed Statuses */
    closed_statuses: string[];
    /** Provider */
    provider: "stub" | "xhub";
    /** Llm Model */
    llm_model: string | null;
    /** Llm Max Tokens */
    llm_max_tokens: number | null;
    /** Llm Timeout Seconds */
    llm_timeout_seconds: number | null;
    /** Analysis Stream */
    analysis_stream: string | null;
  };

  type NumericMetricStat = {
    /** Field Id */
    field_id: string;
    /** Scope Type */
    scope_type: "product" | "event_type";
    /** Scope Key */
    scope_key: string;
    /** Field Key */
    field_key: string;
    /** Label */
    label: string;
    /** Unit */
    unit: string;
    /** Agg Ops */
    agg_ops: ("sum" | "avg" | "max" | "terms")[];
    /** Hit Count */
    hit_count: number;
    /** Sum Value */
    sum_value: number;
    /** Avg Value */
    avg_value: number;
    /** Max Value */
    max_value: number;
  };

  type ProductAnalyticsResponse = {
    /** Total Events */
    total_events: number;
    /** Total Customers */
    total_customers: number;
    /** Selected Product */
    selected_product: string | null;
    /** Selected Event Type */
    selected_event_type: string | null;
    /** Product Summaries */
    product_summaries: ProductOverviewStat[];
    /** Numeric Metrics */
    numeric_metrics: NumericMetricStat[];
    /** Term Distributions */
    term_distributions: TermDistributionStat[];
    /** Events */
    events: AnalyticsEventItem[];
  };

  type ProductOverviewStat = {
    /** Product Key */
    product_key: string;
    /** Name */
    name: string;
    /** Enabled */
    enabled: boolean;
    /** Event Count */
    event_count: number;
    /** Customer Count */
    customer_count: number;
    /** High Priority Count */
    high_priority_count: number;
  };

  type ProductSchemaDeleteRequest = {
    /** Expected Version */
    expected_version: number;
    /** Product Key */
    product_key: string;
  };

  type ProductSchemaInput = {
    /** Product Key */
    product_key: string;
    /** Name */
    name: string;
    /** Description */
    description?: string;
    /** Keywords */
    keywords?: string[];
    /** Sort Order */
    sort_order?: number;
    /** Enabled */
    enabled?: boolean;
  };

  type ProductSchemaItem = {
    /** Product Key */
    product_key: string;
    /** Name */
    name: string;
    /** Description */
    description: string;
    /** Keywords */
    keywords: string[];
    /** Sort Order */
    sort_order: number;
    /** Enabled */
    enabled: boolean;
    /** Builtin */
    builtin: boolean;
  };

  type ProductSchemaSaveRequest = {
    /** Expected Version */
    expected_version: number;
    product: ProductSchemaInput;
  };

  type RecordedAttribution = {
    /** Requested Action */
    requested_action: string | null;
    /** Action */
    action: string | null;
    /** Event Id */
    event_id: string | null;
    /** Possible Event Ids */
    possible_event_ids: string[];
    /** Reason */
    reason: string | null;
    /** Revision No */
    revision_no: number | null;
    /** Summary Changed */
    summary_changed: boolean | null;
    /** Change Kind */
    change_kind: string | null;
    /** Basis */
    basis: RecordedMessageRef[];
    /** New Evidence */
    new_evidence: RecordedMessageRef[];
  };

  type RecordedMessageRef = {
    /** Source */
    source: string | null;
    /** Message Id */
    message_id: string | null;
    /** Digest */
    digest: string | null;
    /** Conversation Id */
    conversation_id: string | null;
    /** Send Time */
    send_time: string | null;
    /** Quote */
    quote: string | null;
    /** Role */
    role: string | null;
  };

  type RecoveryConfirmation = {
    /** Confirmed At */
    confirmed_at: string;
    /** Evidence */
    evidence: CustomerRecoveryEvidence[];
    /** Identity Basis */
    identity_basis: string;
    /** Policy Version */
    policy_version: string;
  };

  type RecurrenceLink = {
    /** Event Id */
    event_id: string;
    /** Reason */
    reason: string;
    recovery: RecoveryConfirmation;
    /** Evidence */
    evidence: ContinuityEvidence[];
    /** Policy Version */
    policy_version: string;
  };

  type ReviewResponse = {
    /** Review Flag */
    review_flag: string;
    /** Type */
    type: string;
    /** Message Ids */
    message_ids: string[];
    /** Possible Event Ids */
    possible_event_ids: string[];
    /** Reason */
    reason: string;
  };

  type RunListResponse = {
    /** Items */
    items: RunResponse[];
    /** Total */
    total: number;
  };

  type RunResponse = {
    /** Run Id */
    run_id: string;
    /** Uid */
    uid: string;
    /** Mode */
    mode: string;
    /** Status */
    status: "queued" | "running" | "succeeded" | "failed";
    request: NormalizedRunRequest;
    /** Total Windows */
    total_windows: number;
    /** Completed Windows */
    completed_windows: number;
    current_window: TimeWindow | null;
    /** Created At */
    created_at: string;
    /** Started At */
    started_at: string | null;
    /** Finished At */
    finished_at: string | null;
    /** Error */
    error: string | null;
    /** Cycles */
    cycles: CycleResponse[];
  };

  type ScopedChangeTask = {
    /** Run Id */
    run_id: string;
    /** Status */
    status: "queued" | "running" | "succeeded" | "failed";
    /** Created At */
    created_at: string | null;
    /** Total Windows */
    total_windows: number;
    /** Completed Windows */
    completed_windows: number;
    current_window: TimeWindow | null;
    /** Current Scope Confirmed */
    current_scope_confirmed: boolean;
    /** Error */
    error: string | null;
    /** Recovered By */
    recovered_by: string | null;
    /** Recovery Link Unavailable */
    recovery_link_unavailable: boolean;
  };

  type searchSenderCandidatesParams = {
    keyword?: string;
    offset?: number;
    limit?: number;
  };

  type SenderCandidate = {
    /** Uid */
    uid: string;
    /** Source */
    source: string;
    /** Sender Id */
    sender_id: string;
    /** Conversation Id */
    conversation_id: string;
    /** Sender Name */
    sender_name: string;
    /** Conversation Name */
    conversation_name: string;
    /** Message Count */
    message_count: number;
    /** Last Seen At */
    last_seen_at: string;
    /** Sender Kind */
    sender_kind: "human" | "bot" | "unknown";
    /** Rule Id */
    rule_id: string;
    /** Configured */
    configured: boolean;
  };

  type SenderCandidateList = {
    /** Items */
    items: SenderCandidate[];
    /** Total */
    total: number;
  };

  type SenderRule = {
    /** Uid */
    uid: string;
    /** Source */
    source: string;
    /** Sender Id */
    sender_id: string;
    /** Conversation Id */
    conversation_id?: string | null;
    /** Kind */
    kind?: "human" | "bot" | "unknown";
    /** Label */
    label?: string;
    /** Basis */
    basis: string;
    /** Enabled */
    enabled?: boolean;
  };

  type SenderRuleList = {
    /** Version */
    version: string;
    /** Items */
    items: SenderRuleResponse[];
  };

  type SenderRuleResponse = {
    /** Uid */
    uid: string;
    /** Source */
    source: string;
    /** Sender Id */
    sender_id: string;
    /** Conversation Id */
    conversation_id: string | null;
    /** Kind */
    kind: "human" | "bot" | "unknown";
    /** Label */
    label: string;
    /** Basis */
    basis: string;
    /** Enabled */
    enabled: boolean;
    /** Rule Id */
    rule_id: string;
  };

  type SenderRuleSave = {
    /** Expected Version */
    expected_version: string;
    /** Rule Id */
    rule_id?: string | null;
    rule: SenderRule;
  };

  type submitManualDecisionParams = {
    event_id: string;
  };

  type TaskAction = {
    /** Action */
    action: "run" | "pause" | "resume";
  };

  type taskActionParams = {
    task_id: string;
  };

  type TaskConfig = {
    /** Name */
    name: string;
    /** Operation */
    operation?: "sync" | "analyze";
    /** Scope */
    scope?: "levels" | "selected";
    /** Levels */
    levels?: string[];
    /** Uids */
    uids?: string[];
    /** Include New */
    include_new?: boolean;
    /** Excluded Uids */
    excluded_uids?: string[];
    /** Excluded Groups */
    excluded_groups?: string[];
    /** Mode */
    mode?: "manual" | "automatic";
    /** Frequency */
    frequency?: "5m" | "15m" | "1h" | "1d";
    /** Interval */
    interval?: "5m" | "15m" | "1h" | "1d";
    /** Start Mode */
    start_mode?: "specified" | "activation";
    /** Start */
    start?: string | null;
    /** End */
    end?: string | null;
    /** Profile Ttl */
    profile_ttl?: "5m" | "15m" | "1h" | "6h" | "1d";
    /** Lookback */
    lookback?: "5m" | "15m" | "1h" | "6h" | "1d" | "2d";
    /** Retries */
    retries?: number;
    /** Retry Delay */
    retry_delay?: "1m" | "5m" | "15m";
  };

  type TaskCustomerProgress = {
    /** Uid */
    uid: string;
    /** Level */
    level: string | null;
    /** Status */
    status: string;
    /** Reason */
    reason: string | null;
    /** Run Id */
    run_id: string | null;
    /** Stream */
    stream: string;
    /** Processed */
    processed: number;
    /** Created */
    created: number;
    /** Updated */
    updated: number;
    /** Completed Windows */
    completed_windows: number;
    /** Total Windows */
    total_windows: number;
    /** Phase */
    phase: string | null;
    /** Incomplete */
    incomplete: boolean;
    /** Withheld */
    withheld: number;
    /** Analyzed Until */
    analyzed_until: string | null;
    /** Duration Seconds */
    duration_seconds: number | null;
  };

  type TaskExecutionHead = {
    /** Execution Id */
    execution_id: string;
    /** Task Id */
    task_id: string;
    /** Status */
    status: "queued" | "running" | "succeeded" | "partial" | "failed";
    /** Stage */
    stage: "discovery" | "analysis" | "completed";
    /** Created At */
    created_at: string;
    /** Finished At */
    finished_at: string | null;
  };

  type TaskExecutionList = {
    /** Items */
    items: TaskExecutionHead[];
    /** Total */
    total: number;
  };

  type TaskExecutionResponse = {
    /** Execution Id */
    execution_id: string;
    /** Task Id */
    task_id: string;
    /** Status */
    status: "queued" | "running" | "succeeded" | "partial" | "failed";
    /** Stage */
    stage: "discovery" | "analysis" | "completed";
    /** Created At */
    created_at: string;
    /** Finished At */
    finished_at: string | null;
    config: TaskConfig;
    /** Start */
    start: string;
    /** End */
    end: string;
    /** Error */
    error: string | null;
    /** Source Messages */
    source_messages: number;
    /** Source Errors */
    source_errors: number;
    /** Processed */
    processed: number;
    /** Created Events */
    created_events: number;
    /** Updated Events */
    updated_events: number;
    /** Sync Completed */
    sync_completed: number;
    /** Sync Total */
    sync_total: number;
    /** Customers */
    customers: TaskCustomerProgress[];
    /** Backlog */
    backlog: number;
    /** Oldest Wait Seconds */
    oldest_wait_seconds: number | null;
    /** Capacity Used */
    capacity_used: number;
    /** Capacity Total */
    capacity_total: number;
    /** Isolated Count */
    isolated_count: number;
    /** Phases */
    phases: Record<string, any>;
    /** Capacity Notice */
    capacity_notice: string;
  };

  type TaskList = {
    /** Items */
    items: TaskResponse[];
    /** Total */
    total: number;
  };

  type TaskPreview = {
    /** Items */
    items: TaskPreviewCustomer[];
    /** Total */
    total: number;
    /** Notice */
    notice: string;
  };

  type TaskPreviewCustomer = {
    /** Uid */
    uid: string;
    /** Level */
    level: string | null;
    /** Tam */
    tam: string | null;
  };

  type TaskResponse = {
    /** Task Id */
    task_id: string;
    config: TaskConfig;
    /** Enabled */
    enabled: boolean;
    /** Version */
    version: number;
    /** Cursor At */
    cursor_at: string | null;
    /** Next Run At */
    next_run_at: string | null;
    /** Created At */
    created_at: string;
    /** Updated At */
    updated_at: string;
    latest: TaskExecutionHead | null;
  };

  type TaskUpdate = {
    config: TaskConfig;
    /** Version */
    version: number;
  };

  type TermBucketStat = {
    /** Value */
    value: string;
    /** Count */
    count: number;
  };

  type TermDistributionStat = {
    /** Field Id */
    field_id: string;
    /** Scope Type */
    scope_type: "product" | "event_type";
    /** Scope Key */
    scope_key: string;
    /** Field Key */
    field_key: string;
    /** Label */
    label: string;
    /** Hit Count */
    hit_count: number;
    /** Buckets */
    buckets: TermBucketStat[];
  };

  type TimeWindow = {
    /** Start */
    start: string;
    /** End */
    end: string;
  };

  type UnitFamilyOption = {
    /** Family */
    family:
      | "none"
      | "count_scale"
      | "million_tokens"
      | "bandwidth_mbps"
      | "storage_tb"
      | "currency_cny"
      | "duration_ms";
    /** Label */
    label: string;
    /** Default Unit */
    default_unit: string;
  };

  type updateTaskParams = {
    task_id: string;
  };

  type WatermarkResponse = {
    /** Next Start */
    next_start: string | null;
    /** Last Send Time */
    last_send_time: string | null;
    /** Last Message Id */
    last_message_id: string | null;
  };
}
