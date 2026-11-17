"""正式执行记录的虚构跨日和故障回归；不读取真实数据或建立网络连接。"""

from copy import deepcopy
from dataclasses import replace
from contextlib import contextmanager
import json

import httpx
import pytest
from offline_http import offline_only

from dake.repositories.attempts import AttemptError, AttemptPendingVerification, DeliveryError
from dake.repositories.attempts import AttemptRepository
from dake.repositories.mysql import MySQLStore, StorageError, TransactionOutcomeUnknown
from dake.analysis.providers.xhub import XHubConfig, XHubProvider, configured_stream
from dake.config import Settings
from dake.jobs.runner import RunRunner, prepare_request
from dake.services.executions import restore_receipt, verify_attempt
from dake.services.processing import IncrementalService
from dake.services.replay import ReplayService
from execution_fakes import MemoryAttempts, RecordingStore
from test_attribution import UID, build, candidate, message, model_output, window
from test_summary_history import change
from test_runs import MemoryRuns, execute_next
from test_recurrence import boundary


def setup(tmp_path, messages=None, mapping=None):
    first = message("first", 1, "虚构工单上传失败，尚待排查。")
    base, _, provider, source = build(tmp_path, messages or [first], mapping or {first.message_id: candidate(first, changes=[change(first)])})
    attempts = MemoryAttempts()
    store = RecordingStore(attempts)
    service = IncrementalService(store, source, provider, base.settings, attempts=attempts)
    return service, attempts, store, source, provider, first


def last(attempts):
    return list(attempts.items.values())[-1]


def stage(record, name):
    return next(item["payload"] for item in record["stages"] if item["stage"] == name)


def test_cross_day_records_real_candidates_context_suggestions_decisions_and_receipt(tmp_path):
    service, attempts, store, source, provider, first = setup(tmp_path)
    one = service.run_window(UID, window(1), execution=dict(run_id="synthetic-run", window_index=0))
    event_id = one.created[0]
    second = message("second", 10, "虚构工单上次上传问题未好，现已确认参数错误。")
    source.messages.append(second)
    provider.mapping[second.message_id] = candidate(second, "update", event_id, [first], changes=[change(second)])
    ten = service.run_window(UID, window(10), execution=dict(run_id="synthetic-run", window_index=1))
    record = last(attempts)
    assert record["attempt_id"] == ten.attempt_id and record["run_id"] == "synthetic-run"
    assert record["retry_of"] is None and ten.updated == [event_id]
    assert stage(record, "history")["candidates"][0]["event_id"] == event_id
    assert stage(record, "history_context")["result"]["items"]
    assert stage(record, "suggestions")["suggestions"][0]["value"]["decision"] == "update"
    decision = stage(record, "decision")
    assert decision["action"] == "update" and decision["proposal_id"]
    receipt = record["receipt"]
    assert receipt["processed_versions"][0]["digest"] == second.digest
    assert [r["message_id"] for r in receipt["processed_versions"]] == [second.message_id]
    assert receipt["writes"][0]["revision_no"] == 2 and record["outcome"] == "committed"
    revision = store.events[event_id].updated_by[-1]
    assert revision["attempt_id"] == ten.attempt_id and revision["proposal_ids"] == [decision["proposal_id"]]
    assert store.attributions[(second.key, service.stream)]["records"][0]["attempt_id"] == ten.attempt_id
    assert restore_receipt(service, receipt).to_dict() == ten.to_dict()


@pytest.mark.parametrize("failed_stage", ["execution", "ingestion", "input", "history", "history_context", "extraction", "provider_input", "suggestions", "decision", "program_result", "commit"])
def test_critical_record_failure_aborts_batch_and_preserves_earlier_stages(tmp_path, failed_stage):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    attempts.fail_stage = failed_stage
    with pytest.raises(AttemptError):
        service.run_window(UID, window(1))
    assert not store.events and not store.processed and not store.attributions
    assert last(attempts)["receipt"] is None and last(attempts)["outcome"] == "not_committed"
    assert all(s["stage"] != failed_stage for s in last(attempts)["stages"])
    if failed_stage in {"execution", "ingestion", "input", "history", "history_context", "extraction", "provider_input"}:
        assert not provider.calls


def test_receipt_failure_rolls_back_events_attributions_and_processing_flags(tmp_path):
    service, attempts, store, _, _, _ = setup(tmp_path)
    attempts.fail_receipt = True
    with pytest.raises(AttemptError):
        service.run_window(UID, window(1))
    assert not store.events and not store.processed and not store.attributions
    assert stage(last(attempts), "decision")["action"] == "new"
    assert last(attempts)["receipt"] is None


def test_unknown_commit_without_receipt_blocks_every_automatic_attempt_until_explicit_verification(tmp_path):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    store.commit_fault = "before"
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    assert last(attempts)["outcome"] == "commit_unknown"
    store.commit_fault = None
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(2))
    assert len(attempts.items) == len(provider.calls) == 1
    assert verify_attempt(service, UID, previous)["outcome"] == "not_committed"
    result = service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert result.attempt_id != previous and last(attempts)["retry_of"] == previous
    assert stage(last(attempts), "input")["input_changed"] is False


def test_unknown_ack_with_committed_receipt_only_recovers_display_without_source_or_model(tmp_path, monkeypatch):
    service, attempts, store, source, provider, _ = setup(tmp_path)
    store.commit_fault = "after"
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    saved = deepcopy(store.events)
    assert last(attempts)["outcome"] == "commit_unknown" and last(attempts)["receipt"] is not None
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert len(provider.calls) == len(attempts.items) == 1
    assert verify_attempt(service, UID, previous)["outcome"] == "committed"
    store.commit_fault = None
    monkeypatch.setattr(source, "load_day", lambda *a: pytest.fail("恢复展示不得取数"))
    result = service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert len(provider.calls) == 1 and store.events == saved
    assert result.recovered_from_attempt_id == previous
    assert last(attempts)["receipt"]["kind"] == "delivery_recovery"
    assert last(attempts)["receipt"]["writes"] == last(attempts)["receipt"]["processed_versions"] == []


def test_lost_failure_record_stays_blocked_instead_of_assuming_rollback(tmp_path):
    service, attempts, _, _, provider, _ = setup(tmp_path)
    attempts.fail_stage, attempts.fail_failure = "input", True
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1))
    assert last(attempts)["outcome"] == "running"
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1))
    assert not provider.calls


@pytest.mark.parametrize("destination", ["snapshot", "advance"])
def test_post_commit_file_failure_restores_original_version_not_future_edits(tmp_path, monkeypatch, destination):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    original = getattr(service.state, destination)
    monkeypatch.setattr(service.state, destination, lambda *args: (_ for _ in ()).throw(OSError("虚构失败")))
    with pytest.raises(DeliveryError):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    receipt = deepcopy(last(attempts)["receipt"])
    expected = restore_receipt(service, receipt).events
    event = next(iter(store.events.values()))
    future = deepcopy(event.updated_by[-1])
    future["snapshot"]["summary"] = "虚构后来编辑，不应回填原批次"
    event.updated_by.append(future)
    monkeypatch.setattr(service.state, destination, original)
    recovered = service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert recovered.events == expected and len(provider.calls) == 1
    assert restore_receipt(service, last(attempts)["receipt"]).events == expected


def test_changed_input_after_rolled_back_attempt_is_marked_and_previous_version_retained(tmp_path):
    service, attempts, _, source, provider, first = setup(tmp_path)
    attempts.fail_stage = "suggestions"
    with pytest.raises(AttemptError):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    changed = replace(first, content="虚构同一消息的编辑版本")
    source.messages[:] = [changed]
    provider.mapping[first.message_id] = candidate(changed, changes=[change(changed)])
    attempts.fail_stage = None
    service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert stage(last(attempts), "input")["input_changed"] is True
    assert stage(attempts.items[previous], "provider_input")["new_messages"][0]["content"] == first.content


@pytest.mark.parametrize("mismatch", ["uid", "stream", "window", "policy"])
def test_retry_rejects_foreign_or_incompatible_attempt_before_reading_messages(tmp_path, mismatch):
    service, attempts, _, _, provider, _ = setup(tmp_path)
    service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    record = attempts.items[previous]
    if mismatch == "uid": record["uid"] = "synthetic-other-customer"
    if mismatch == "stream": record["stream"] = "synthetic-other-stream"
    if mismatch == "window": record["window_end"] = window(2).end.isoformat()
    if mismatch == "policy": record["metadata"]["policy_digest"] = "synthetic-other-policy"
    with pytest.raises(AttemptError):
        service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert len(provider.calls) == len(attempts.items) == 1


def test_replay_keeps_run_window_identity_and_same_window_fresh_call_is_not_claimed_as_retry(tmp_path):
    service, attempts, _, _, _, _ = setup(tmp_path)
    results = list(ReplayService(service).run_range(UID, window(1).start, window(2).end,
                   window(1).end - window(1).start, run_id="synthetic-task"))
    assert [r["window_index"] for r in attempts.items.values()] == [0, 1]
    assert all(r["run_id"] == "synthetic-task" for r in attempts.items.values())
    result = service.run_window(UID, window(1))
    assert result.processed == 0 and last(attempts)["retry_of"] is None
    assert len({r.attempt_id for r in [*results, result]}) == 3


def test_recovery_keeps_later_snapshot_and_watermark(tmp_path):
    service, attempts, _, _, provider, _ = setup(tmp_path)
    first = service.run_window(UID, window(1))
    previous = first.attempt_id
    path = service.state.snapshot_path(UID, service.stream, window(1))
    path.write_text('{"synthetic":"later snapshot"}')
    watermark = dict(next_start=window(3).end.isoformat(), last_message_id="synthetic-later")
    service.state.write(UID, service.stream, watermark)
    service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert json.loads(path.read_text()) == dict(synthetic="later snapshot")
    assert service.state.read(UID, service.stream) == watermark and len(provider.calls) == 1


def test_revision_links_only_proposals_that_wrote_facts(tmp_path):
    service, attempts, store, source, provider, first = setup(tmp_path)
    event_id = service.run_window(UID, window(1)).created[0]
    repeated, progress = message("repeated", 2, "虚构客户仅重复既有反馈"), message("progress", 2, "虚构已定位参数错误")
    source.messages += [repeated, progress]
    provider.mapping.update({repeated.message_id: candidate(repeated, "update", event_id, [first],
        changes=[change(repeated, text=first.content)]), progress.message_id: candidate(progress, "update", event_id, [first],
        changes=[change(progress)])})
    result = service.run_window(UID, window(2))
    records = result.filter_audit
    active = [r["proposal_id"] for r in records if r.get("revision_no") is not None]
    assert len(active) == 1 and store.events[event_id].updated_by[-1]["proposal_ids"] == active


@pytest.mark.parametrize("repair", [False, True])
def test_formal_xhub_records_actual_inputs_and_repair_calls_without_extra_requests(tmp_path, repair):
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    calls = []
    def respond(request):
        calls.append(json.loads(request.content))
        content = 'invalid synthetic json' if repair and len(calls) == 1 else model_output(first)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-key-never-store", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        result = service.run_window(UID, window(1))
        record = last(attempts)
        started = [s["payload"] for s in record["stages"] if s["stage"] == "model_started"]
        assert len(started) == len(calls) == (2 if repair else 1)
        assert started[0]["input"] == json.loads(calls[0]["messages"][1]["content"])
        assert started[0]["message_versions"][0]["digest"] == first.digest
        assert [s["call_no"] for s in started] == list(range(1, len(calls) + 1))
        assert "synthetic-key-never-store" not in json.dumps(record)
        assert result.filter_audit[0]["proposal_id"].startswith(f"{result.attempt_id}:{len(calls)}:")
        assert provider.execution_observer is None
    finally:
        provider.close()


@pytest.mark.parametrize("corrected", [True, False])
def test_wrong_old_basis_quote_uses_existing_bounded_repair_before_commit(tmp_path, corrected):
    """旧引文错配在已有纠正环节处理；仍错就整批回滚，不能绕过原证据规则。"""
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    follow = message("follow", 2, "虚构工单今天再次排查，已定位参数错误。")
    calls = []
    target = None

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            content = model_output(first)
        else:
            # 文字确实存在，但故意把本轮正文配到旧消息编号；不能据此绕过旧证据检查。
            quote = first.content if corrected and len(calls) == 3 else follow.content
            content = model_output(follow, decision="update", referenced_event_id=target,
                basis=[dict(message_id=follow.key, quote=follow.content),
                       dict(message_id=first.key, quote=quote)])
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-only", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        target = service.run_window(UID, window(1)).created[0]
        before = deepcopy(store.events)
        source.messages.append(follow)
        if corrected:
            result = service.run_window(UID, window(2))
            assert result.updated == [target] and not result.created
            assert len(store.events[target].updated_by) == 2
            assert last(attempts)["receipt"]["processed_versions"][0]["digest"] == follow.digest
            assert last(attempts)["outcome"] == "committed"
        else:
            from dake.analysis.providers.xhub import XHubContractError
            with pytest.raises(XHubContractError, match="归属依据的消息或引文无效"):
                service.run_window(UID, window(2))
            assert store.events == before and (follow.key, service.stream) not in store.processed
            assert last(attempts)["outcome"] == "not_committed" and last(attempts)["receipt"] is None
        assert len(calls) == 3  # 初始新建一次；后续一批最多原请求加一次纠正。
        assert "归属依据的消息或引文无效" in calls[-1]["messages"][-1]["content"]
        assert "candidates[0].basis[1]" in calls[-1]["messages"][-1]["content"]
        assert "不能借用其他消息的正文" in calls[-1]["messages"][-1]["content"]
        assert first.content not in calls[-1]["messages"][-1]["content"]
        assert follow.content not in calls[-1]["messages"][-1]["content"]
        assert calls[-1]["messages"][1] == calls[-2]["messages"][1]
        started = [s for s in last(attempts)["stages"] if s["stage"] == "model_started"]
        assert len(started) == 2 and started[-1]["payload"]["repair_attempt"]
        assert service.stream == configured_stream(settings)
    finally:
        provider.close()


@pytest.mark.parametrize("failed_stage,expected_calls", [("model_started", 0), ("model_completed", 1), ("model_failed", 1)])
def test_xhub_record_failure_stops_before_call_or_before_commit_and_disables_repair(tmp_path, failed_stage, expected_calls):
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    calls = []
    def respond(request):
        calls.append(1)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": "invalid synthetic json" if failed_stage == "model_failed" else model_output(first)}}]})
    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-unused", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    attempts.fail_stage = failed_stage
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        with pytest.raises(AttemptError):
            service.run_window(UID, window(1))
        assert len(calls) == expected_calls and not store.events and not store.processed
        assert last(attempts)["receipt"] is None
    finally:
        provider.close()


def test_runner_progress_failure_recovers_exact_attempt_without_reanalyzing(tmp_path):
    service, attempts, _, _, provider, _ = setup(tmp_path)
    settings = replace(service.settings, end=window(1).end)
    service.settings = settings
    runs = MemoryRuns()
    @contextmanager
    def factory(configuration, **kwargs):
        yield service
    runner = RunRunner(settings, runs, service_factory=factory)
    request, count = prepare_request(settings, dict(interval="1d"))
    runs.fail_completion_once = True
    failed = execute_next(runs, runner, request, count)
    original = last(attempts)["attempt_id"]
    assert failed["status"] == "failed" and last(attempts)["receipt"] is not None
    assert last(attempts)["delivery"]["run_progress"]["status"] == "unconfirmed"
    recovered = execute_next(runs, runner, request, count)
    assert recovered["status"] == "succeeded" and len(provider.calls) == 1
    assert last(attempts)["retry_of"] == original
    assert recovered["cycles"][0]["recovered_from_attempt_id"] == original
    assert recovered["cycles"][0]["recovered_from_history"] is True


def test_receipt_uses_business_connection_and_scope_without_independent_write():
    repo = AttemptRepository(Settings())
    calls = []
    class Business:
        def _require_transaction(self, uid):
            assert uid == UID
        def _execute(self, sql, args):
            calls.append((sql, args))
            return [], 1
    repo.connection = lambda: pytest.fail("提交收据不得另开连接")
    repo.write_receipt(Business(), UID, "synthetic-stream", "synthetic-attempt", dict(version=1))
    sql, args = calls[0]
    assert "uid=%s AND stream=%s AND attempt_id=%s" in sql
    assert args[1:] == (UID, "synthetic-stream", "synthetic-attempt")


@pytest.mark.parametrize("failure", [None, "body", "commit", "rollback", "close"])
def test_mysql_transaction_preserves_unknown_commit_and_uses_one_customer_lock(failure):
    store = object.__new__(MySQLStore)
    store.database, store._transaction_uid, store._customer_uid, store._closed = "synthetic_database", None, None, False
    calls = []
    class Connection:
        def begin(self): calls.append("begin")
        def commit(self):
            calls.append("commit")
            if failure in {"commit", "close"}: raise OSError("synthetic lost ack")
        def rollback(self):
            calls.append("rollback")
            if failure == "rollback": raise OSError("synthetic lost rollback")
        def close(self):
            calls.append("close")
            if failure == "close": raise OSError("synthetic close failure")
    store._conn = Connection()
    def execute(sql, args):
        if store._closed: raise StorageError("虚构已关闭")
        calls.append("lock" if "GET_LOCK" in sql else "release")
        return [dict(acquired=1)], 1
    store._execute = execute
    def work():
        with store.customer_lock(UID):
            with store.transaction(UID):
                if failure in {"body", "rollback"}: raise ValueError("synthetic body failure")
    if failure is None:
        work()
    else:
        with pytest.raises(ValueError if failure == "body" else TransactionOutcomeUnknown): work()
    assert calls.count("lock") == 1
    assert ("rollback" in calls) == (failure in {"body", "rollback"})
    assert store._transaction_uid is None and store._customer_uid is None


def test_lost_commit_ack_and_failed_error_record_still_block_with_receipt(tmp_path):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    store.commit_fault, attempts.fail_failure = "after", True
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1))
    assert last(attempts)["outcome"] == "running" and last(attempts)["receipt"]
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(2))
    assert len(provider.calls) == 1


def test_failed_postcommit_confirmation_requires_verification_without_fake_file_failure(tmp_path):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    attempts.fail_confirm = True
    with pytest.raises(DeliveryError, match="提交确认记录未保存"):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    assert store.events and last(attempts)["receipt"] and last(attempts)["delivery"] == {}
    with pytest.raises(AttemptPendingVerification):
        service.run_window(UID, window(1), execution=dict(retry_of=previous))
    attempts.fail_confirm = False
    assert verify_attempt(service, UID, previous)["outcome"] == "committed"
    service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert len(provider.calls) == 1


def test_file_status_record_failure_does_not_claim_files_failed_or_repeat_model(tmp_path):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    attempts.fail_delivery = True
    with pytest.raises(DeliveryError, match="展示状态记录未确认"):
        service.run_window(UID, window(1))
    previous = last(attempts)["attempt_id"]
    assert last(attempts)["outcome"] == "committed" and last(attempts)["delivery"] == {}
    assert service.state.snapshot_path(UID, service.stream, window(1)).exists()
    attempts.fail_delivery = False
    service.run_window(UID, window(1), execution=dict(retry_of=previous))
    assert len(provider.calls) == len(store.events) == 1


@pytest.mark.parametrize("missing", ["snapshot", "revision", "digest"])
def test_recovery_missing_or_edited_version_stops_without_using_current_event(tmp_path, missing):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    result = service.run_window(UID, window(1))
    event = store.events[result.created[0]]
    if missing == "snapshot":
        event.updated_by[0].pop("snapshot")
    elif missing == "revision":
        event.updated_by.clear()
    else:
        event.updated_by[0]["snapshot"]["summary"] = "虚构后来改写的摘要"
    with pytest.raises(DeliveryError):
        service.run_window(UID, window(1), execution=dict(retry_of=result.attempt_id))
    assert len(provider.calls) == 1 and last(attempts)["receipt"] is None


@pytest.mark.parametrize("input_kind", ["empty", "bot_only"])
def test_no_model_batch_still_has_honest_skipped_stages_and_receipt(tmp_path, monkeypatch, input_kind):
    service, attempts, store, source, provider, first = setup(tmp_path)
    if input_kind == "empty":
        source.messages.clear()
    else:
        monkeypatch.setattr("dake.services.processing.load_sender_kinds", lambda _: {
            (UID, first.source, first.sender_id, ""): "bot"})
    result = service.run_window(UID, window(1))
    record = last(attempts)
    assert not provider.calls and not store.events and record["receipt"]["writes"] == []
    assert [s["status"] for s in record["stages"] if s["stage"] in {"history", "history_context", "extraction"}] == ["skipped"] * 3
    assert len(record["receipt"]["processed_versions"]) == result.processed == (input_kind == "bot_only")


def test_recording_keeps_confirmation_late_evidence_and_recurrence_separate(tmp_path, monkeypatch):
    service, attempts, store, source, provider, first = setup(tmp_path)
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: {
        (UID, first.source, first.sender_id): "customer"})
    event_id = service.run_window(UID, window(1)).created[0]
    def follow(msg, day=None, **changes):
        source.messages.append(msg)
        provider.mapping[msg.message_id] = candidate(msg, "update", event_id, [first], changes=[change(msg)], **changes)
        return service.run_window(UID, window(day or msg.sent_at.day))
    follow(message("follow", 2, "虚构工单排查发现报告上传参数错误。"))
    follow(message("reference", 10, "虚构工单上次报告上传问题还没好。"))
    confirmed = message("confirmed", 11, "虚构工单报告上传已由我重新验证，现在确实恢复正常。")
    follow(confirmed, continuity=boundary("confirm_recovery", confirmed))
    confirmation = deepcopy(store.events[event_id])
    late = message("late", 10, "虚构工单补充恢复前报告上传失败的日志。")
    result = follow(late, day=12)
    assert result.updated == [event_id]
    decision = stage(last(attempts), "decision")
    assert decision["before"]["summary"] == decision["after"]["summary"] == confirmation.summary
    assert decision["after"]["recovery"] == confirmation.recovery
    assert decision["evidence_delta"]["added"][0]["message_id"] == late.message_id
    assert last(attempts)["receipt"]["processed_versions"][0]["send_time"] == late.sent_at.isoformat()
    parent = deepcopy(store.events[event_id])
    again = message("again", 13, "虚构工单上次报告上传恢复了，今天同一报告又上传失败。")
    source.messages.append(again)
    provider.mapping[again.message_id] = candidate(again, evidence=[first], changes=[change(again)],
        continuity=boundary("recurrence", again, event_id))
    result = service.run_window(UID, window(13))
    new_id = result.created[0]
    assert new_id != event_id and store.events[event_id] == parent
    assert stage(last(attempts), "decision")["after"]["recurrence_of"]["event_id"] == event_id
    assert [r["event_id"] for r in last(attempts)["receipt"]["writes"]] == [new_id]
    assert len(provider.calls) == len(attempts.items) == 6


@pytest.mark.parametrize("action", ["duplicate", "review"])
def test_nonwriting_decisions_are_recorded_without_creating_a_revision(tmp_path, action):
    service, attempts, store, source, provider, first = setup(tmp_path)
    opened = service.run_window(UID, window(1)).created[0]
    before = deepcopy(store.events)
    ack = message("ack", 2, "虚构工单这个消息已收到。")
    source.messages.append(ack)
    provider.mapping[ack.message_id] = candidate(ack, action, opened if action == "duplicate" else None, [first], changes=[])
    service.run_window(UID, window(2))
    assert stage(last(attempts), "suggestions")["suggestions"][0]["value"]["decision"] == action
    assert stage(last(attempts), "decision")["action"] == action
    assert last(attempts)["receipt"]["writes"] == [] and store.events == before


def test_same_message_different_facts_link_only_the_proposal_that_was_written(tmp_path):
    service, attempts, store, source, provider, first = setup(tmp_path)
    event_id = service.run_window(UID, window(1)).created[0]
    discussion = message("two-facts", 2, "虚构重复此前上传问题，同时补充发现参数错误。")
    source.messages.append(discussion)
    provider.mapping[discussion.message_id] = [
        candidate(discussion, "update", event_id, [first], changes=[change(discussion, text=first.content)]),
        candidate(discussion, "update", event_id, [first], changes=[change(discussion)])]
    service.run_window(UID, window(2))
    decisions = [s["payload"] for s in last(attempts)["stages"] if s["stage"] == "decision"]
    assert [d["contributed_to_revision"] for d in decisions] == [False, True]
    assert {d["change_scope"] for d in decisions} == {"combined_event_group"}
    assert store.events[event_id].updated_by[-1]["proposal_ids"] == [decisions[1]["proposal_id"]]


def test_saved_progress_with_lost_ack_is_unconfirmed_and_does_not_repeat_analysis(tmp_path, monkeypatch):
    service, attempts, store, _, provider, _ = setup(tmp_path)
    settings = replace(service.settings, end=window(1).end)
    service.settings = settings
    runs = MemoryRuns()
    @contextmanager
    def factory(configuration, **kwargs):
        yield service
    runner = RunRunner(settings, runs, service_factory=factory)
    request, count = prepare_request(settings, dict(interval="1d"))
    original = runs.complete_window
    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise StorageError("虚构任务进度已保存，但应答丢失")
    monkeypatch.setattr(runs, "complete_window", lose_ack)
    failed = execute_next(runs, runner, request, count)
    assert failed["status"] == "failed" and failed["completed_windows"] == 1
    assert last(attempts)["delivery"]["run_progress"]["status"] == "unconfirmed"
    monkeypatch.setattr(runs, "complete_window", original)
    next_run = execute_next(runs, runner, request, count)
    assert next_run["status"] == "succeeded" and len(provider.calls) == len(store.events) == 1
    assert last(attempts)["receipt"]["writes"] == []


def test_failed_journal_does_not_claim_task_progress_was_written(tmp_path, monkeypatch):
    service, attempts, _, _, provider, _ = setup(tmp_path)
    settings = replace(service.settings, end=window(1).end)
    service.settings = settings
    runs = MemoryRuns()
    @contextmanager
    def factory(configuration, **kwargs):
        yield service
    runner = RunRunner(settings, runs, service_factory=factory)
    request, count = prepare_request(settings, dict(interval="1d"))
    def fail(*args, **kwargs):
        raise OSError("虚构任务结果文件保存失败")
    monkeypatch.setattr("dake.jobs.runner.private_json", fail)
    failed = execute_next(runs, runner, request, count)
    assert failed["status"] == "failed" and failed["completed_windows"] == 0
    assert last(attempts)["receipt"] and len(provider.calls) == 1
    assert last(attempts)["delivery"]["run_progress"]["status"] == "not_attempted"


@pytest.mark.parametrize("corrected", [True, False])
def test_field_coverage_uses_bounded_repair_and_preserves_old_fields(tmp_path, corrected):
    """字段引用不被本轮事实覆盖时精确纠正，仍不合格必须回滚。"""
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    follow = message("follow", 2, "虚构工单今天再次排查，已定位参数错误。")
    calls = []
    target = None

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            content = model_output(first)
        else:
            payload = json.loads(model_output(follow, decision="update", referenced_event_id=target,
                basis=[dict(message_id=follow.key, quote=follow.content), dict(message_id=first.key, quote=first.content)]))
            item = payload["candidates"][0]
            if not (corrected and len(calls) == 3):
                item["evidence_ids"].append(first.key)
                item["attributes"] = [dict(name="synthetic-field", value=first.content,
                    quote=first.content, evidence_ids=[first.key])]
            content = json.dumps(payload)

        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-only", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        target = service.run_window(UID, window(1)).created[0]
        before = deepcopy(store.events)
        source.messages.append(follow)
        if corrected:
            result = service.run_window(UID, window(2))
            assert result.updated == [target] and not result.created
            assert len(store.events[target].updated_by) == 2
            assert last(attempts)["receipt"]["processed_versions"][0]["digest"] == follow.digest
            assert last(attempts)["outcome"] == "committed"
        else:
            from dake.analysis.providers.xhub import XHubContractError
            with pytest.raises(XHubContractError, match="字段更新必须有本轮直接消息依据"):
                service.run_window(UID, window(2))
            assert store.events == before and (follow.key, service.stream) not in store.processed
            assert last(attempts)["outcome"] == "not_committed" and last(attempts)["receipt"] is None
        assert len(calls) == 3  # 初始新建一次；后续一批最多原请求加一次纠正。
        assert "字段更新必须有本轮直接消息依据" in calls[-1]["messages"][-1]["content"]
        assert "candidates[0]" in calls[-1]["messages"][-1]["content"]
        assert "attributes[0]" in calls[-1]["messages"][-1]["content"]
        assert first.content not in calls[-1]["messages"][-1]["content"]
        assert follow.content not in calls[-1]["messages"][-1]["content"]
        assert calls[-1]["messages"][1] == calls[-2]["messages"][1]
        started = [s for s in last(attempts)["stages"] if s["stage"] == "model_started"]
        assert len(started) == 2 and started[-1]["payload"]["repair_attempt"]
        assert service.stream == configured_stream(settings)
    finally:
        provider.close()



@pytest.mark.parametrize("fault", ["old_evidence", "replacement", "overlap", "missing_basis"])
@pytest.mark.parametrize("corrected", [True, False])
def test_late_validation_enters_single_repair_without_preflight_writes(tmp_path, fault, corrected):
    """完整业务预校验无副作用，旧证据、摘要冲突与缺失背景都只纠正一次。"""
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    follow = message("follow", 2, "虚构工单今天确认参数错误，需要继续处理。")
    calls, target = [], None

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            payload = json.loads(model_output(first))
            payload["candidates"][0]["summary"] = "合成问题尚未解决，正在等待核实。"
        else:
            assert len(store.events[target].updated_by) == 1
            assert (follow.key, provider.name) not in store.processed
            payload = json.loads(model_output(follow, decision="update", referenced_event_id=target,
                basis=[dict(message_id=follow.key, quote=follow.content),
                       dict(message_id=first.key, quote=first.content)]))
            item = payload["candidates"][0]
            if not (corrected and len(calls) == 3):
                if fault == "old_evidence":
                    item["evidence_ids"].append(first.key)
                elif fault == "replacement":
                    item["changes"][0]["replaces"] = "不存在的合成摘要片段"
                elif fault == "overlap":
                    item["changes"][0]["replaces"] = "尚未解决"
                    other = deepcopy(item["changes"][0])
                    other.update(replaces="问题尚未解决", text="合成的另一项替换")
                    item["changes"].append(other)
                else:
                    item["basis"].append(dict(message_id="official_chat:absent", quote="虚构缺失依据"))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(payload)}}]})

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-only", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        target = service.run_window(UID, window(1)).created[0]
        before = deepcopy(store.events)
        source.messages.append(follow)
        if corrected:
            result = service.run_window(UID, window(2))
            assert result.updated == [target] and not result.created
            assert len(store.events[target].updated_by) == 2
            assert last(attempts)["outcome"] == "committed"
        else:
            from dake.analysis.providers.xhub import XHubContractError
            with pytest.raises(XHubContractError):
                service.run_window(UID, window(2))
            assert store.events == before
            assert last(attempts)["receipt"] is None
            assert (follow.key, service.stream) not in store.processed
        assert len(calls) == 3
        assert provider.candidate_validator is None
    finally:
        provider.close()


def test_verified_current_field_quote_is_saved_without_extra_model_request(tmp_path):
    """独立字段引文进入事实记录，不依赖摘要复述，也不额外请求模型。"""
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    follow = message("follow", 2, "虚构工单今天完成初步排查，仍需验证。")
    detail = message("detail", 2, "虚构测试环境为合成集群甲。")
    calls, target = [], None

    def respond(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            payload = json.loads(model_output(first))
        else:
            payload = json.loads(model_output(follow, decision="update", referenced_event_id=target,
                basis=[dict(message_id=follow.key, quote=follow.content), dict(message_id=first.key, quote=first.content)]))
            item = payload["candidates"][0]
            item["evidence_ids"].append(detail.key)
            item["field_updates"] = [dict(target=dict(kind="attribute", type_label="排障", name="异常对象与环境"),
                ref="environment", value="合成集群甲", stage="本轮排查", scope="虚构工单", business_time=None,
                time_kind="unknown", intent="supplement", replaces=[], reason="合成环境直接依据",
                evidence=[dict(message_id=detail.key, quote=detail.content)])]
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(payload)}}]})

    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-only", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        target = service.run_window(UID, window(1)).created[0]
        source.messages.extend([follow, detail])
        result = service.run_window(UID, window(2))
        assert result.updated == [target] and len(calls) == 2
        event = store.events[target]
        assert event.attributes["异常对象与环境"] == "合成集群甲"
        assert follow.content in event.summary and detail.content not in event.summary
        assert {item["message_id"] for item in event.evidence} >= {follow.message_id, detail.message_id}
        assert len(event.updated_by) == 2
        changes = event.updated_by[-1]["changes"]
        assert changes[-1]["text"] == changes[-1]["evidence"][0]["quote"] == follow.content
        saved = stage(last(attempts), "model_completed")
        assert saved["normalizations"] == []
        assert len(json.loads(provider.traces[-1]["output"])["candidates"][0]["changes"]) == 1
        assert last(attempts)["outcome"] == "committed"
        assert len(last(attempts)["receipt"]["processed_versions"]) == 2
    finally:
        provider.close()


def test_resume_same_failed_task_links_attempt_and_only_restores_committed_result(tmp_path):
    """同任务续跑保持显式重试链，已有收据不再次调用模型。"""
    service, attempts, _, _, provider, _ = setup(tmp_path)
    settings = replace(service.settings, end=window(1).end)
    service.settings = settings
    runs = MemoryRuns()
    @contextmanager
    def factory(configuration, **kwargs):
        yield service
    runner = RunRunner(settings, runs, service_factory=factory)
    request, count = prepare_request(settings, dict(interval="1d"))
    runs.fail_completion_once = True
    failed = execute_next(runs, runner, request, count)
    original = last(attempts)["attempt_id"]
    failed.update(status="running", error=None)
    runner.execute(deepcopy(failed))
    assert failed["status"] == "succeeded" and len(provider.calls) == 1
    assert last(attempts)["retry_of"] == original
    assert failed["cycles"][0]["recovered_from_attempt_id"] == original


@pytest.mark.parametrize("extra_unknown", [False, True])
def test_uncertain_parent_reference_stays_review_without_changing_event(tmp_path, extra_unknown):
    """待确认字段归位后只保存待核对，不变成恢复或事件更新。"""
    service, attempts, store, source, _, first = setup(tmp_path)
    settings = replace(service.settings, provider="xhub")
    follow = message("uncertain-follow", 2, "合成工单是否恢复还需要确认。")
    calls, target = [], None
    def respond(request):
        calls.append(json.loads(request.content))
        content = model_output(first) if len(calls) == 1 else model_output(follow, decision="review",
            possible_event_ids=["synthetic-unprovided"] if extra_unknown else [],
            continuity=dict(kind="uncertain", previous_event_id=target,
                            evidence=[dict(message_id=follow.key, quote=follow.content)]))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
    provider = XHubProvider(XHubConfig(model=settings.llm_model, api_key="synthetic-only", max_tokens=8192),
        settings, transport=httpx.MockTransport(respond))
    try:
        service = IncrementalService(store, source, provider, settings, attempts=attempts)
        target = service.run_window(UID, window(1)).created[0]
        before = deepcopy(store.events)
        source.messages.append(follow)
        result = service.run_window(UID, window(2))
        assert not result.created and not result.updated and store.events == before
        assert result.review[0]["possible_event_ids"] == [target]
        assert last(attempts)["receipt"]["writes"] == [] and last(attempts)["outcome"] == "committed"
        assert stage(last(attempts), "model_completed")["normalizations"][0]["reason"] == "uncertain_reference_to_possible_events"
        assert len(calls) == 2
    finally:
        provider.close()
