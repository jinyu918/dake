"""空窗口优化只适用于已同步来源，保留迟到消息、排除群和未决提交边界。"""
from contextlib import nullcontext
from datetime import timedelta
from types import SimpleNamespace

from dake.config import bound
from dake.domain.models import Message, windows
from dake.ingestion.stored import ScopedStore
from dake.services.empty_windows import empty_prefix
from fakes import MemoryStore
from test_runs import run_fixture, execute_next


def setup(messages=(), unresolved=None):
    start = bound('2026-11-08')
    span = list(windows(start, start + timedelta(hours=1), timedelta(minutes=5)))
    store = MemoryStore()
    with store.transaction("synthetic-run-customer"):
        store.upsert_messages(messages)
    svc = SimpleNamespace(store=ScopedStore(store, ['allowed']), settings=SimpleNamespace(start=start),
        stream='stub-v1', attempts=SimpleNamespace(unresolved=lambda *args: unresolved))
    return svc, span


def message(minute, group='allowed'):
    return Message('synthetic-run-customer', str(minute), bound('2026-11-08') + timedelta(minutes=minute),
        '合成窗口消息', conversation_id=group)


def test_empty_range_is_one_batch_and_boundary_message_is_not_skipped():
    svc, span = setup()
    assert len(empty_prefix(svc, 'synthetic-run-customer', span)) == 12
    svc, span = setup([message(15)])
    result = empty_prefix(svc, 'synthetic-run-customer', span)
    assert len(result) == 3 and all(c['skipped_empty'] for c in result)
    assert not empty_prefix(svc, 'synthetic-run-customer', span[3:])


def test_late_message_and_unresolved_attempt_block_skipping():
    svc, span = setup([message(1)])
    assert not empty_prefix(svc, 'synthetic-run-customer', span[6:])
    svc, span = setup(unresolved='synthetic-attempt')
    assert not empty_prefix(svc, 'synthetic-run-customer', span)


def test_excluded_group_does_not_create_analysis_work():
    svc, span = setup([message(1, 'excluded')])
    assert len(empty_prefix(svc, 'synthetic-run-customer', span)) == 12


def test_stored_runner_batches_all_empty_windows_without_processing(tmp_path):
    settings, repo, runner, store, source, provider, request, total, seen = run_fixture(tmp_path)
    request.update(message_source='stored', allowed_groups=['allowed'])
    store.customer_lock = lambda uid: nullcontext()
    repo.has_interrupted_range = lambda *args: False
    calls = []
    def batch(rid, owner, index, cycles):
        calls.append((index, len(cycles)))
        for cycle in cycles: repo.complete_window(rid, owner, cycle)
    repo.complete_empty_windows = batch
    run = execute_next(repo, runner, request, total)
    assert run['status'] == 'succeeded' and run['completed_windows'] == total
    assert calls == [(0, total)]
    assert all(c['skipped_empty'] and c['processed'] == 0 for c in run['cycles'])
    assert not list((tmp_path / 'snapshots').glob('**/*.json'))


def test_stored_runner_processes_message_between_empty_batches(tmp_path):
    from dake.jobs.runner import prepare_request
    settings, repo, runner, store, source, provider, _, _, seen = run_fixture(tmp_path)
    request, total = prepare_request(settings, {'start': '2026-11-08T00:00:00+08:00',
        'end': '2026-11-08T01:00:00+08:00', 'interval': '5m'})
    request.update(message_source='stored', allowed_groups=['allowed'])
    with store.transaction(settings.uid):
        store.upsert_messages([message(15)])
    store.customer_lock = lambda uid: nullcontext()
    repo.has_interrupted_range = lambda *args: False
    calls = []
    def batch(rid, owner, index, cycles):
        calls.append((index, len(cycles)))
        for cycle in cycles: repo.complete_window(rid, owner, cycle)
    repo.complete_empty_windows = batch
    run = execute_next(repo, runner, request, total)
    assert run['status'] == 'succeeded', run['error']
    assert calls == [(0, 3), (4, 8)]
    assert sum(c['processed'] for c in run['cycles']) == 1
    assert not run['cycles'][3].get('skipped_empty')
    assert len(list((tmp_path / 'snapshots').glob('**/*.json'))) == 1
