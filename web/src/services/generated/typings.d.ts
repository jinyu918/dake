declare namespace API {
  type BusinessRelation = {
    /** Kind */
    kind: "based_on" | "caused_by";
    /** Event Id */
    event_id: string;
    /** Reason */
    reason: string;
    /** Evidence */
    evidence: ChangeEvidence[];
    /** Review Status */
    review_status: "confirmed" | "pending" | "retracted";
    /** Review Reason */
    review_reason: string | null;
    /** Retraction Evidence */
    retraction_evidence: ChangeEvidence[];
    /** Origin Event Id */
    origin_event_id: string | null;
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
    /** Evidence Status */
    evidence_status: string | null;
  };

  type ConfigResponse = {
    /** Field Targets */
    field_targets: FieldTarget[];
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
    /** Provider */
    provider: "stub" | "xhub";
    /** Customers */
    customers: CustomerResponse[];
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
    /** Evidence Status */
    evidence_status: string | null;
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

  type CorrectionNotCommitted = {
    /** Outcome */
    outcome: string;
    /** Operation Id */
    operation_id: string;
  };

  type CorrectionProof = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Digest */
    digest: string;
    /** Send Time */
    send_time: string;
    /** Quote */
    quote: string;
  };

  type CorrectionResolution = {
    target: FieldTarget;
    /** Adopt */
    adopt: FactOrigin[];
    /** Acknowledged Locks */
    acknowledged_locks?: FactOrigin[];
    /** Locked */
    locked: boolean;
    /** Reason */
    reason: string;
  };

  type correctSameRoundParams = {
    event_id: string;
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
    /** Evidence Status */
    evidence_status: string | null;
    /** Sender Id */
    sender_id: string;
    /** Sender Role */
    sender_role: "customer" | "internal";
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
    classification_evidence_status: string;
    /** Classification Status */
    classification_status: string;
    other_reason: JsonValue | null;
    /** Topic Key */
    topic_key: string | null;
    /** Start Time */
    start_time: string;
    /** End Time */
    end_time: string;
    /** Status */
    status: string;
    /** Severity */
    severity: string;
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
    /** Typed Attributes */
    typed_attributes: Record<string, any>;
    /** Version */
    version: number;
    /** Field States */
    field_states: Record<string, any>;
    /** Field Issues */
    field_issues: FieldIssue[];
    /** Manual Operations */
    manual_operations: FieldOperation[];
    /** Corrected Into */
    corrected_into: string | null;
    /** Identity Corrections */
    identity_corrections: IdentityCorrectionOperation[];
    /** Extensions */
    extensions: Record<string, any>;
    /** Review Flag */
    review_flag: string | null;
    /** Closed At */
    closed_at: string | null;
    recovery: RecoveryConfirmation | null;
    recurrence_of: RecurrenceLink | null;
    /** Relations */
    relations: BusinessRelation[];
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
    /** Source */
    source: "automatic" | "manual";
    /** Manual Operation */
    manual_operation: Record<string, any> | null;
    /** Change Summary */
    change_summary: string | null;
    /** Summary Changed */
    summary_changed: boolean | null;
    /** Changed Fields */
    changed_fields: string[];
    /** Changes */
    changes: EventChange[];
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
    classification_evidence_status: string;
    /** Classification Status */
    classification_status: string;
    other_reason: JsonValue | null;
    /** Topic Key */
    topic_key: string | null;
    /** Start Time */
    start_time: string;
    /** End Time */
    end_time: string;
    /** Status */
    status: string;
    /** Severity */
    severity: string;
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
    /** Typed Attributes */
    typed_attributes: Record<string, any>;
    /** Version */
    version: number;
    /** Field States */
    field_states: Record<string, any>;
    /** Field Issues */
    field_issues: FieldIssue[];
    /** Manual Operations */
    manual_operations: FieldOperation[];
    /** Corrected Into */
    corrected_into: string | null;
    /** Identity Corrections */
    identity_corrections: IdentityCorrectionOperation[];
    /** Extensions */
    extensions: Record<string, any>;
    /** Review Flag */
    review_flag: string | null;
    /** Closed At */
    closed_at: string | null;
    recovery: RecoveryConfirmation | null;
    recurrence_of: RecurrenceLink | null;
    /** Relations */
    relations: BusinessRelation[];
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
    /** Origin Event Id */
    origin_event_id: string | null;
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

  type FactOrigin = {
    /** Event Id */
    event_id: string;
    /** Fact Id */
    fact_id: string;
  };

  type FieldAction = {
    target: FieldTarget;
    /** Action */
    action:
      | "confirm"
      | "adopt"
      | "set"
      | "clear"
      | "unknown"
      | "not_applicable"
      | "lock"
      | "unlock"
      | "resolve";
    /** Value */
    value?: string | boolean | string[] | null;
    /** Fact Id */
    fact_id?: string | null;
    /** Stage */
    stage?: string;
    /** Scope */
    scope?: string;
    /** Business Time */
    business_time?: string | null;
    /** Time Kind */
    time_kind?: "unknown" | "planned" | "actual";
    /** Issue Ids */
    issue_ids?: string[];
    /** Archive Dependents */
    archive_dependents?: boolean;
  };

  type FieldDecisionRequest = {
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
    actions: FieldAction[];
  };

  type FieldEvidence = {
    /** Source */
    source: string;
    /** Message Id */
    message_id: string;
    /** Digest */
    digest?: string | null;
    /** Send Time */
    send_time?: string | null;
    /** Conversation Id */
    conversation_id?: string | null;
    /** Quote */
    quote?: string | null;
    /** Evidence Status */
    evidence_status?: string | null;
  };

  type FieldFact = {
    /** Id */
    id: string;
    /** Value */
    value: string | boolean | string[] | null;
    /** Value State */
    value_state: "value" | "cleared" | "unknown" | "not_applicable";
    /** Stage */
    stage: string;
    /** Scope */
    scope: string;
    /** Business Time */
    business_time: string | null;
    /** Time Kind */
    time_kind: "unknown" | "planned" | "actual";
    /** Intent */
    intent: string;
    /** Replaces */
    replaces: string[];
    /** Reason */
    reason: string;
    /** Evidence */
    evidence: FieldEvidence[];
    /** Source */
    source: "automatic" | "manual" | "legacy";
    /** Evidence Status */
    evidence_status: string;
    /** Status */
    status: string;
    /** Input Ref */
    input_ref: string | null;
    /** Operation Id */
    operation_id?: string | null;
    /** Operator */
    operator?: string | null;
    /** Decided At */
    decided_at?: string | null;
    /** Origin Event Id */
    origin_event_id?: string | null;
    /** Origin Fact Id */
    origin_fact_id?: string | null;
  };

  type FieldIssue = {
    /** Id */
    id: string;
    /** Field Key */
    field_key: string;
    target: FieldTarget;
    /** Reason */
    reason: string;
    /** Fact Id */
    fact_id: string | null;
    /** Affected Ids */
    affected_ids: string[];
    /** Status */
    status: "open" | "resolved";
    /** Resolution */
    resolution: Record<string, any> | null;
    /** Origin Event Id */
    origin_event_id?: string | null;
    /** Origin Issue Id */
    origin_issue_id?: string | null;
  };

  type FieldOperation = {
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
    actions: FieldAction[];
  };

  type FieldState = {
    target: FieldTarget;
    /** Facts */
    facts: FieldFact[];
    /** Adopted Ids */
    adopted_ids: string[];
    /** Locked Ids */
    locked_ids: string[];
    /** Locked */
    locked: boolean;
    /** Decision */
    decision: Record<string, any> | null;
    /** Archived */
    archived: boolean;
    /** Inherited Decisions */
    inherited_decisions?: Record<string, any>[];
  };

  type FieldTarget = {
    /** Kind */
    kind: "common" | "label" | "attribute";
    /** Name */
    name: string;
    /** Type Label */
    type_label?: string | null;
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

  type getEventParams = {
    event_id: string;
    uid?: string | null;
    stream?: string | null;
  };

  type getFieldDecisionParams = {
    event_id: string;
    operation_id: string;
    uid: string;
    stream: string;
  };

  type getRunParams = {
    run_id: string;
    uid?: string | null;
    stream?: string | null;
  };

  type getSameRoundCorrectionParams = {
    event_id: string;
    operation_id: string;
    uid: string;
    stream: string;
  };

  type getTaskExecutionParams = {
    execution_id: string;
  };

  type HealthResponse = {
    /** Status */
    status: string;
  };

  type IdentityCorrectionOperation = {
    /** Operation Id */
    operation_id: string;
    /** Request Digest */
    request_digest: string;
    /** Outcome */
    outcome: string;
    /** Duplicate Event Id */
    duplicate_event_id: string;
    /** Retained Event Id */
    retained_event_id: string;
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Before Versions */
    before_versions: Record<string, any>;
    /** After Versions */
    after_versions: Record<string, any>;
    /** Operator */
    operator: string;
    /** Reason */
    reason: string;
    /** Scope */
    scope: string;
    /** Decided At */
    decided_at: string;
    /** Effective After */
    effective_after: string;
    /** Evidence */
    evidence: CorrectionProof[];
    /** Resolutions */
    resolutions: CorrectionResolution[];
  };

  type JsonValue = Record<string, any>;

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
  };

  type listRunsParams = {
    uid?: string | null;
    mode?: string | null;
    stream?: string | null;
  };

  type listTaskExecutionsParams = {
    task_id: string;
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
    identity_basis: "confirmed_account" | "confirmed_support_account";
    /** Policy Version */
    policy_version: string;
    /** Review Status */
    review_status: "confirmed" | "pending";
    /** Supporting Evidence */
    supporting_evidence: CustomerRecoveryEvidence[];
    /** Scope */
    scope: string | null;
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
    /** Review Status */
    review_status: "confirmed" | "pending" | "retracted";
    /** Review Reason */
    review_reason: string | null;
    /** Retraction Evidence */
    retraction_evidence: Record<string, any>[];
    /** Retracted By */
    retracted_by: string | null;
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

  type SameRoundCorrectionRequest = {
    /** Uid */
    uid: string;
    /** Stream */
    stream: string;
    /** Retained Event Id */
    retained_event_id: string;
    /** Expected Version */
    expected_version: number;
    /** Retained Expected Version */
    retained_expected_version: number;
    /** Operation Id */
    operation_id: string;
    /** Operator */
    operator: string;
    /** Reason */
    reason: string;
    /** Same Round */
    same_round: boolean;
    /** Scope */
    scope: string;
    /** Evidence */
    evidence: CorrectionProof[];
    /** Resolutions */
    resolutions?: CorrectionResolution[];
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

  type submitFieldDecisionParams = {
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

  type TimeWindow = {
    /** Start */
    start: string;
    /** End */
    end: string;
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
