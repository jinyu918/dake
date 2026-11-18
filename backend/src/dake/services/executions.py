"""共用入口的执行身份、阶段观察和提交核实；不参与事件判断。"""

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from ..analysis.attribution import attribution_version
from ..domain.models import CycleResult, json_value, parse_time
from ..repositories.attempts import AttemptError, AttemptPendingVerification, DeliveryError, now
from ..repositories.mysql import TransactionOutcomeUnknown


def fingerprint(value):
    """对已冻结的业务输入取指纹，不混入凭据和连接配置。"""
    return hashlib.sha256(json.dumps(json_value(value), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def message_ref(message):
    """元数据引用用于区分消息身份和编辑版本，正文另按实际输入保存。"""
    return dict(source=message.source, message_id=message.message_id, digest=message.digest,
                conversation_id=message.conversation_id, send_time=message.sent_at.isoformat(), key=message.key)


def execution_policy(service):
    """只冻结已有处理参数，不改变策略、分析范围或既有规则。"""
    settings = service.settings
    return dict(provider=service.stream, attribution=attribution_version(),
        start=settings.start.isoformat(), history_retrieval=asdict(settings.history_retrieval),
        history_context=asdict(settings.history_context), context_lookback=str(settings.context_lookback),
        ingest_lookback=str(settings.ingest_lookback), match_window=str(settings.match_window),
        closed_grace=str(settings.closed_grace), closed_statuses=list(settings.closed_statuses),
        default_status=settings.default_status, default_severity=settings.default_severity)


def receipt_for(result, events, completed, *, kind="analysis", parent=None, source_receipt=None):
    """收据保存实际消费版本及事件修订位置，复用原快照，不复制全部历史正文。"""
    cycle = result.to_dict()
    snapshots = cycle.pop("events")
    by_id = {event.event_id: event for event in events}
    refs = []
    for snapshot in ([] if source_receipt is not None else snapshots):
        event = by_id[snapshot["event_id"]]
        indexes = [i for i, rev in enumerate(event.updated_by) if rev.get("source") not in {"manual", "directory", "clock"} and parse_time(rev["window_end"]) <= result.window.end]
        if not indexes:
            raise AttemptError("提交结果缺少可引用的事件版本")
        refs.append(dict(event_id=event.event_id, revision_index=indexes[-1], digest=fingerprint(snapshot)))
    if source_receipt is not None:
        refs = deepcopy(source_receipt["event_versions"])
    writes = [dict(event_id=event.event_id, revision_index=i, revision_no=rev.get("revision_no"), action=rev["action"])
              for event in events for i, rev in enumerate(event.updated_by)
              if kind == "analysis" and rev.get("attempt_id") == result.attempt_id]
    return dict(version=1, kind=kind, parent_attempt_id=parent, receipt_prepared_at=now(),
                processed_versions=[message_ref(m) for m in completed], writes=writes,
                cycle=cycle, event_versions=refs)


def restore_receipt(service, receipt):
    """精确读取原修订；缺失时停止展示恢复，不重算、也不读后来版本补空。"""
    value = deepcopy(receipt["cycle"])
    if value["uid"] != service.settings.uid or value["stream"] != service.stream:
        raise AttemptError("提交收据与当前客户或分析范围不符")
    events = {e.event_id: e for e in service.store.list_events(value["uid"], value["stream"])}
    snapshots = []
    for ref in receipt["event_versions"]:
        event = events.get(ref["event_id"])
        index = ref["revision_index"]
        if event is None or not 0 <= index < len(event.updated_by):
            raise DeliveryError("业务已提交，但原事件版本缺失；不能重新分析补造")
        snapshot = deepcopy(event.updated_by[index].get("snapshot"))
        if not snapshot or (snapshot.get("uid"), snapshot.get("stream"), snapshot.get("event_id")) != (value["uid"], value["stream"], event.event_id):
            raise DeliveryError("业务已提交，但历史快照缺失或范围不符")
        snapshot["updated_by"] = [{k: v for k, v in r.items() if k != "snapshot"}
            for r in event.updated_by[:index + 1] if r.get("source") not in {"manual", "directory", "clock"} and parse_time(r["window_end"]) <= parse_time(value["window"]["end"])]
        if fingerprint(snapshot) != ref["digest"]:
            raise DeliveryError("业务已提交，但原事件版本核验失败；不能用新版本替换")
        snapshots.append(snapshot)
    return CycleResult.from_dict(dict(value, events=snapshots))


class ExecutionRecorder:
    """阶段单独持久化，提交收据最后写入业务事务；观察数据不会回灌判断。"""

    def __init__(self, repository, uid, stream, attempt_id, parent=None):
        self.repository, self.uid, self.stream, self.attempt_id = repository, uid, stream, attempt_id
        self.parent = parent
        self.committed = False
        self.files_started = False
        self.proposals = {}
        self.model_calls = 0
        self.model_disposition = None

    def record(self, stage, status="completed", **payload):
        self.repository.stage(self.uid, self.stream, self.attempt_id, stage, status, deepcopy(payload))

    def input(self, pending, eligible, auxiliary):
        manifest = dict(pending=[message_ref(m) for m in pending], eligible=[m.key for m in eligible],
                        auxiliary=[message_ref(m) for m in auxiliary])
        digest = fingerprint(manifest)
        previous = next((s["payload"].get("input_digest") for s in (self.parent or {}).get("stages", [])
                         if s["stage"] == "input"), None)
        self.record("input", manifest=manifest, input_digest=digest,
                    comparison_scope="pending_and_auxiliary_message_versions",
                    previous_input_digest=previous, input_changed=None if previous is None else previous != digest)

    def model(self, stage, **payload):
        """Provider 只传无鉴权的实际输入与结构化结果；修正调用共用执行 ID。"""
        if stage == "model_started":
            self.model_calls += 1
            previous = next((s["payload"].get("request_digest") for s in (self.parent or {}).get("stages", [])
                if s["stage"] == stage and s["payload"].get("call_no") == self.model_calls), None)
            payload.update(previous_request_digest=previous,
                           request_changed=None if previous is None else previous != payload["request_digest"])
        self.record(stage, status="failed" if stage == "model_failed" else "started" if stage == "model_started" else "completed",
                    call_no=self.model_calls, **payload)
        # 阶段没写上表示请求尚未发出；写上之后才把这次调用视为可能已经送出。
        if stage == "model_started":
            self.model_disposition = "maybe_sent"
        elif stage == "model_completed":
            self.model_disposition = "completed"
        elif stage == "model_failed":
            self.model_disposition = "maybe_sent" if payload.get("error_type") == "transport_error" else "known_failure"

    def extracted(self, candidates):
        self.proposals = {id(candidate): f"{self.attempt_id}:{self.model_calls}:{i}" for i, candidate in enumerate(candidates)}
        self.record("suggestions", suggestions=[dict(proposal_id=self.proposals[id(c)], value=asdict(c)) for c in candidates])

    def decision(self, **payload):
        """仅保存实际程序结果和拟变化，绝不把这个观察点当成提交证明。"""
        before = (payload.get("before") or {}).get("evidence", [])
        after = (payload.get("after") or {}).get("evidence", [])
        key = lambda ref: (ref["source"], ref["message_id"], ref.get("digest"))
        known = {key(ref) for ref in before}
        added = [ref for ref in after if key(ref) not in known]
        ids = {(ref["source"], ref["message_id"]) for ref in added}
        payload["evidence_delta"] = dict(added=added,
            previous_versions=[ref for ref in before if (ref["source"], ref["message_id"]) in ids])
        for key in ("before", "after"):
            if payload.get(key):
                payload[key] = {k: v for k, v in payload[key].items() if k in
                    {"event_id", "summary", "status", "severity", "assignees", "recovery", "recurrence_of"}}
        self.record("decision", **payload)

    def receipt(self, store, result, events, completed, **kwargs):
        self.record("commit", "pending", note="程序已决定，等待事务提交确认")
        self.repository.write_receipt(store, self.uid, self.stream, self.attempt_id,
                                      receipt_for(result, events, completed, **kwargs))


def verify_attempt(service, uid, attempt_id):
    """显式核实入口；持有原客户锁后读取收据，不取数、不调用模型或自动重跑。"""
    if uid != service.settings.uid or service.attempts is None:
        raise AttemptError("执行核实范围无效")
    with service.store.customer_lock(uid):
        record = service.attempts.get(uid, service.stream, attempt_id)
        if record is None:
            raise AttemptError("当前范围内未找到执行记录")
        if record.get("receipt") is not None:
            service.attempts.verified(uid, service.stream, attempt_id, committed=True)
            return dict(attempt_id=attempt_id, outcome="committed")
        if record["outcome"] not in {"running", "commit_unknown", "not_committed"}:
            raise AttemptPendingVerification("执行结果与收据不一致，不能自动核实")
        # 取得同一客户锁证明原执行已退出；新连接读取不到原子收据，才允许明确未提交。
        service.attempts.verified(uid, service.stream, attempt_id, committed=False)
        return dict(attempt_id=attempt_id, outcome="not_committed")


def run_recorded(service, uid, window, *, observer=None, execution=None):
    """正式 CLI 与后台共用此包装；未决执行阻断、明确重试和展示恢复走同一入口。"""
    if uid != service.settings.uid:
        raise AttemptError("执行客户与当前配置不符")
    identity = dict(execution or {})
    if set(identity) - {"run_id", "window_index", "retry_of"}:
        raise AttemptError("执行身份字段无效")
    repo = service.attempts
    with service.store.customer_lock(uid):
        unresolved = repo.unresolved(uid, service.stream)
        if unresolved:
            raise AttemptPendingVerification(f"执行 {unresolved} 尚待核实，禁止自动重跑")
        parent = None
        policy = execution_policy(service)
        if identity.get("retry_of"):
            parent = repo.get(uid, service.stream, identity["retry_of"])
            if (parent is None or parent["window_start"] != window.start.isoformat() or
                    parent["window_end"] != window.end.isoformat() or
                    parent["metadata"].get("policy_digest") != fingerprint(policy)):
                raise AttemptError("前次执行不存在、范围或策略不同，不能关联为重试")
        attempt_id = repo.create(uid, service.stream, window, **identity,
            metadata=dict(version=1, policy=policy, policy_digest=fingerprint(policy)))
        recorder = ExecutionRecorder(repo, uid, service.stream, attempt_id, parent)

        def work():
            return _work_recorded(service, uid, window, observer, repo, recorder, parent)

        if getattr(service, "gate", None) is None:
            return work()
    # 有执行权租约时，模型调用不能占着客户锁；提交时再进短事务核对代次。
    return work()


def _work_recorded(service, uid, window, observer, repo, recorder, parent):
    """执行或按收据恢复。调用方决定模型期间是否持有客户锁。"""
    attempt_id = recorder.attempt_id
    configure = getattr(service.provider, "configure_execution_observer", None)
    files_saved = False
    try:
        recorder.record("execution", "started", retry_of=None if parent is None else parent["attempt_id"])
        if parent and parent.get("receipt"):
            recorder.record("recovery", source_attempt_id=parent["attempt_id"], mode="delivery_only")
            result = restore_receipt(service, parent["receipt"])
            result.attempt_id = attempt_id
            result.recovered_from_attempt_id = parent["attempt_id"]
            with service.store.transaction(uid):
                recorder.receipt(service.store, result, service.store.list_events(uid, service.stream), [],
                                 kind="delivery_recovery", parent=parent["attempt_id"], source_receipt=parent["receipt"])
            recorder.committed = True
            repo.confirm_commit(uid, service.stream, attempt_id)
            recorder.files_started = True
            # 原路径可能已有后续同窗口快照；恢复旧执行不能覆盖后来展示或倒退水位。
            if not service.state.snapshot_path(uid, service.stream, window).exists():
                service.state.snapshot(result)
            watermark = service.state.read(uid, service.stream)
            if not watermark.get("next_start") or parse_time(watermark["next_start"]) < parse_time(result.watermark["next_start"]):
                service.state.advance(uid, service.stream, result.watermark)
        else:
            if configure is not None:
                configure(recorder.model)
            result = service._run_window(uid, window, observer=observer, recorder=recorder)
        files_saved = True
        repo.delivery(uid, service.stream, attempt_id, "files", "restored_or_kept_existing" if parent and parent.get("receipt") else "saved")
        return result
    except BaseException as error:
        if recorder.committed:
            if recorder.files_started and not files_saved:
                try:
                    repo.delivery(uid, service.stream, attempt_id, "files", "failed")
                except Exception:
                    pass
            detail = ("提交确认记录未保存，须先核实" if not recorder.files_started else
                      "展示状态记录未确认" if files_saved else "展示文件保存失败")
            raise DeliveryError(f"执行 {attempt_id} 的业务已提交，{detail}；请恢复展示，不要重新分析") from None
        unknown = isinstance(error, TransactionOutcomeUnknown)
        model_unknown = (not unknown) and recorder.model_disposition == "maybe_sent"
        try:
            repo.failure(uid, service.stream, attempt_id, unknown=unknown, model_unknown=model_unknown,
                         error_type=type(error).__name__)
        except Exception:
            raise AttemptPendingVerification(f"执行 {attempt_id} 的结束记录未保存，须核实后再处理") from None
        gate = getattr(service, "gate", None)
        if gate is not None and (unknown or model_unknown):
            gate.block("commit_unknown" if unknown else "model_unknown",
                       "提交结果未核实，禁止换流重跑" if unknown else "模型请求可能已发出但结果未知，禁止自动重发")
        if unknown:
            raise AttemptPendingVerification(f"执行 {attempt_id} 的提交结果待核实，禁止自动重跑") from None
        if model_unknown:
            raise AttemptPendingVerification(f"执行 {attempt_id} 的模型结果未知，须核实，禁止自动重跑") from None
        raise
    finally:
        if configure is not None:
            configure(None)
