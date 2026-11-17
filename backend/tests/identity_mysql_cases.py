from copy import deepcopy
from dataclasses import replace
from threading import Event, Thread
from types import SimpleNamespace
from uuid import uuid4

from dake.api.app_factory import create_app
from dake.repositories.mysql import MySQLStore
from dake.services.executions import receipt_for, restore_receipt
from dake.services.processing import IncrementalService
from dake.services.queries import QueryService
from dake.services.identity_corrections import IdentityCorrectionService
from offline_http import TestClient
import final_replay
import test_classification_final
import test_classification_replay
import test_identity_corrections


def seed(case, tmp_path, monkeypatch):
    for module in (final_replay, test_classification_replay, test_identity_corrections):
        monkeypatch.setattr(module, "UID", case.uid)
        monkeypatch.setattr(module, "STREAM", "stub-v1")
    original = test_classification_replay.msg
    prefix = uuid4().hex
    def message(identity, *args, **kwargs):
        return original(prefix + identity, *args, **kwargs)
    monkeypatch.setattr(test_classification_final, "msg", message)
    monkeypatch.setattr(test_identity_corrections, "msg", message)
    monkeypatch.setattr("dake.services.processing.load_sender_roles", lambda _: {
        (case.uid, "synthetic", "synthetic-human"): "customer"})
    synthetic = test_identity_corrections.build_split(tmp_path,
        again_content="合成背景说明。" * 40 + "A上传再次失败，请继续排查。")
    with case.service.store.transaction(case.uid):
        case.service.store.upsert_messages(synthetic.source.messages)
        for event in synthetic.store.events.values():
            case.service.store.save_event(event)
        case.service.store.mark_processed(synthetic.source.messages, "stub-v1")
    synthetic.service = IncrementalService(case.service.store, synthetic.source, synthetic.provider, case.settings, attempts=case.attempts)
    synthetic.queries = QueryService(case.settings)
    synthetic.client = TestClient(create_app(case.settings, query_service=synthetic.queries, start_worker=False,
        runner=object(), run_repository=SimpleNamespace(init_schema=lambda: None)))
    return synthetic


def test_identity_real_atomic_http_alias_followup_and_both_receipts(case, tmp_path, monkeypatch):
    synthetic = seed(case, tmp_path, monkeypatch)
    assert "A上传" not in synthetic.event(synthetic.duplicate_id).evidence[0]["excerpt"]
    old = deepcopy(synthetic.rounds[-1])
    old_receipt = receipt_for(old, list(synthetic.store.events.values()), [])
    response = test_identity_corrections.submit(synthetic)
    assert response.status_code == 200, response.text
    with synthetic.queries.connection() as store:
        events = store.list_events(case.uid, "stub-v1")
        alias = next(event for event in events if event.event_id == synthetic.duplicate_id)
        assert alias.corrected_into == synthetic.parent_id and alias.recurrence_of["review_status"] == "retracted"
        assert len(events) == 2
    assert synthetic.queries.events(case.uid, "stub-v1")["total"] == 1
    assert synthetic.queries.config()["customers"][0]["event_count"] == 1
    assert restore_receipt(synthetic.service, old_receipt).events == old.events
    follow = test_classification_replay.msg(uuid4().hex, 5, "A上传同一次故障继续排查。")
    synthetic.source.messages.append(follow)
    synthetic.provider.mapping[follow.message_id] = final_replay.record(follow, ["排障"], old=alias, anchor=synthetic.again_message)
    result = synthetic.service.run_window(case.uid, test_classification_replay.day_window(5))
    assert result.updated == [synthetic.parent_id] and not result.created
    assert len(result.events) == 1
    record = case.attempts.get(case.uid, "stub-v1", result.attempt_id)
    assert restore_receipt(synthetic.service, record["receipt"]).events == result.events
    assert restore_receipt(synthetic.service, old_receipt).events == old.events


def test_identity_real_second_save_rollback_and_retry(case, tmp_path, monkeypatch):
    synthetic = seed(case, tmp_path, monkeypatch)
    original = MySQLStore.save_event
    calls = []
    def fail(self, event):
        calls.append(event.event_id)
        if len(calls) == 2:
            raise RuntimeError("合成第二侧失败")
        return original(self, event)
    monkeypatch.setattr(MySQLStore, "save_event", fail)
    assert test_identity_corrections.submit(synthetic).status_code == 503
    with synthetic.queries.connection() as store:
        events = store.list_events(case.uid, "stub-v1")
        assert all(event.corrected_into is None and not event.identity_corrections for event in events)
    monkeypatch.setattr(MySQLStore, "save_event", original)
    body = test_identity_corrections.request_for(synthetic)
    url = f"/api/v1/events/{synthetic.duplicate_id}/same-round-corrections"
    saved = synthetic.client.post(url, json=body)
    assert saved.status_code == 200
    assert synthetic.client.post(url, json=body).json() == saved.json()
    assert synthetic.client.post(url, json=dict(body, reason="不同请求")).status_code == 409


def test_identity_real_receipt_waits_for_customer_lock_and_sees_atomic_commit(case, tmp_path, monkeypatch):
    synthetic = seed(case, tmp_path, monkeypatch)
    reached, release, verified = Event(), Event(), Event()
    original = MySQLStore.save_event
    def pause(self, event):
        original(self, event)
        if event.event_id == synthetic.duplicate_id:
            reached.set()
            assert release.wait(8)
    monkeypatch.setattr(MySQLStore, "save_event", pause)
    results, failures = {}, []
    body = test_identity_corrections.request_for(synthetic)
    service = IdentityCorrectionService(synthetic.queries)
    def submit():
        try:
            results["submitted"] = service.submit(synthetic.duplicate_id, body)
        except BaseException as error:
            failures.append(type(error).__name__)
    def verify():
        try:
            results["receipt"] = service.operation(synthetic.duplicate_id, body["operation_id"], case.uid, "stub-v1")
            verified.set()
        except BaseException as error:
            failures.append(type(error).__name__)
    writer = Thread(target=submit)
    writer.start()
    assert reached.wait(8)
    reader = Thread(target=verify)
    reader.start()
    assert not verified.wait(0.2)
    with synthetic.queries.connection() as store:
        assert all(not event.identity_corrections and event.corrected_into is None for event in store.list_events(case.uid, "stub-v1"))
    release.set()
    writer.join(8)
    reader.join(8)
    assert not failures and not writer.is_alive() and not reader.is_alive()
    assert results["receipt"] == results["submitted"]
