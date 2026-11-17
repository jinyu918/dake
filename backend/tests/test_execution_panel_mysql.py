"""正式变化面板的真实 MySQL 查询验收；必须由专用入口显式指定全新测试库。"""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4
import json

import pymysql
import pytest

from dake.api.schemas import EventChangesResponse
from dake.jobs.runner import prepare_request
from dake.repositories.attempts import AttemptError, AttemptPendingVerification, DeliveryError
from dake.repositories.mysql import MySQLStore
from dake.repositories.runs import RunRepository
from dake.services.queries import QueryService
from dake.domain.execution_links import event_link_patterns
from offline_http import TestClient
from panel_preview import create_preview
from test_attribution import candidate, message, window
from test_execution_mysql import case, explicit_isolation
from test_summary_history import change
from identity_mysql_cases import (
    test_identity_real_atomic_http_alias_followup_and_both_receipts,
    test_identity_real_second_save_rollback_and_retry,
    test_identity_real_receipt_waits_for_customer_lock_and_sees_atomic_commit,
)


pytestmark = pytest.mark.isolated_mysql


def test_generated_hints_preserve_every_exact_link_and_reject_false_hits(case):
    """新生成列必须覆盖所有正式路径，提示误命中仍由原条件排除。"""
    target = 'synthetic-target-event'
    repository = case.service.attempts
    expected = set()
    for _, field, value in event_link_patterns(target):
        attempt_id = repository.create(case.uid, case.provider.name, window(1))
        expected.add(attempt_id)
        with repository.connection() as store:
            store._execute('UPDATE processing_attempts SET ' + field + '=CAST(%s AS JSON) WHERE attempt_id=%s',
                           (json.dumps(value), attempt_id))
    for value in (
        [dict(stage='unrelated', payload=dict(candidates=[dict(event_id=target)]))],
        [dict(stage='history', payload=dict(candidates=[dict(event_id=target.upper())]))],
        [dict(stage='program_result', payload=dict(events=[dict(event_id=target)]))],
    ):
        attempt_id = repository.create(case.uid, case.provider.name, window(1))
        with repository.connection() as store:
            store._execute('UPDATE processing_attempts SET stages=CAST(%s AS JSON) WHERE attempt_id=%s',
                           (json.dumps(value), attempt_id))
    page = repository.list_for_event_changes(case.uid, case.provider.name, target, limit=100)
    assert set(page['related_ids']) == expected
    assert {row['attempt_id'] for row in page['items']} == expected


def test_cycle_projection_preserves_scope_audit_and_original_position(case):
    """展开后的周期序号、旧范围判定与精确审计关联保持不变，快照成员不算关联。"""
    target = 'synthetic-target-event'
    repository = RunRepository(case.settings)
    run = repository.create(dict(uid=case.uid, mode='official', analysis_stream=case.provider.name), 5)
    base = dict(uid=case.uid, stream=case.provider.name,
                window=dict(start=window(1).start.isoformat(), end=window(1).end.isoformat()),
                created=[], updated=[], filter_audit=[], events=[dict(event_id=target)])
    cycles = [deepcopy(base) for _ in range(5)]
    cycles[1]['filter_audit'] = [dict(kind='unrelated', event_id=target)]
    cycles[2].update(uid=case.uid + '-foreign', created=[target])
    cycles[3]['filter_audit'] = [dict(kind='event_attribution', possible_event_ids=[target])]
    cycles[4]['updated'] = [target]
    with repository.connection() as store:
        store._execute("UPDATE processing_runs SET cycles=CAST(%s AS JSON),status='succeeded' WHERE run_id=%s",
                       (json.dumps(cycles), run['run_id']))
    page = repository.list_for_event_changes(case.uid, case.provider.name, event_id=target)
    assert page['total'] == 1
    assert [c['_cycle_index'] for c in page['items'][0]['cycles']] == [3, 4]
    assert all('events' not in c for c in page['items'][0]['cycles'])
    with repository.connection() as store:
        store._execute("UPDATE processing_runs SET request=CAST(%s AS JSON) WHERE run_id=%s",
                       (json.dumps(dict(analysis_stream='foreign-stream')), run['run_id']))
    assert repository.list_for_event_changes(case.uid, case.provider.name, event_id=target)['total'] == 0


def view(case, event_id, *, offset=0):
    """使用实际只读查询、SQL 事件筛选和公开响应模型，不用内存关联替身。"""
    query = QueryService(case.settings)
    result = query.event_changes(event_id, case.uid, case.provider.name, RunRepository(case.settings), execution_offset=offset)
    return EventChangesResponse.model_validate(result).model_dump(mode='json')


def remember(request, case, event_id, label):
    """仅汇总此用例真正产生的虚构事件身份，全部通过后才允许写出索引。"""
    request.config._dake_panel_cases.append(dict(label=label, uid=case.uid, stream=case.provider.name, event_id=event_id))


def follow(case, event_id, day, text):
    msg = replace(message(uuid4().hex, day, text), uid=case.uid)
    case.source.messages.append(msg)
    case.provider.mapping[msg.message_id] = candidate(msg, 'update', event_id, [case.first], changes=[change(msg)])
    return msg


def test_real_cross_day_sql_and_public_response_preserve_original_versions(case, request):
    opened = case.service.run_window(case.uid, window(1))
    event_id = opened.created[0]
    follow(case, event_id, 2, '虚构工单 CASE-SYN-42 开始排查上传失败。')
    second = case.fresh().run_window(case.uid, window(2))
    last = follow(case, event_id, 10, '虚构工单 CASE-SYN-42 上次问题未好，本次定位参数错误。')
    tenth = case.fresh().run_window(case.uid, window(10))
    other = replace(message(uuid4().hex, 10, '虚构工单 CASE-SYN-99 是另一独立事项。'), uid=case.uid)
    case.source.messages.append(other)
    case.provider.mapping[other.message_id] = candidate(other, topic_key='虚构独立事项', changes=[change(other)])
    independent = case.fresh().run_window(case.uid, window(10))
    assert independent.created and independent.created != opened.created
    data = view(case, event_id)
    by_id = {r['attempt_id']: r for r in data['executions']}
    assert by_id[opened.attempt_id]['revision_indexes'] == [0]
    assert by_id[second.attempt_id]['revision_indexes'] == [1]
    current = by_id[tenth.attempt_id]
    assert current['revision_indexes'] == [2] and current['updated'] and current['commit_status'] == 'committed'
    assert any(c['event_id'] == event_id for c in current['candidates'])
    assert any(c['message_id'] == case.first.message_id and c['fragments'] for c in current['contexts'])
    assert any(m['digest'] == last.digest and m['quote'] == last.content for group in current['inputs'] for m in group['new_messages'])
    assert current['suggestions'][0]['action'] == 'update' and current['decisions'][0]['action'] == 'update'
    if independent.attempt_id in by_id:
        assert not by_id[independent.attempt_id]['updated']
    other_data = view(case, independent.created[0])
    assert any(r['attempt_id'] == independent.attempt_id and r['created'] for r in other_data['executions'])
    assert opened.attempt_id not in {r['attempt_id'] for r in other_data['executions']}
    before = deepcopy(case.latest()), len(case.provider.calls)
    manifest = dict(cases=[dict(uid=case.uid, stream=case.provider.name, event_id=event_id, label='虚构跨天')])
    app = create_preview(case.settings, manifest, store_factory=MySQLStore)
    with TestClient(app) as client:
        response = client.get(f'/api/v1/events/{event_id}/changes', params=dict(uid=case.uid, stream=case.provider.name))
        assert response.status_code == 200 and response.json() == data
        config = client.get('/api/v1/config').json()
        assert config['customers'] and all(c['uid'] == case.uid and c['label'].startswith('虚构验收客户 · ')
                                           for c in config['customers'])
    assert (case.latest(), len(case.provider.calls)) == before
    remember(request, case, event_id, '首日创建、次日跟进、第十日引用与必要旧聊天')
    remember(request, case, independent.created[0], '同客户另一独立事件：切换后不能串批次')


def test_candidate_hit_without_suggestion_is_not_committed_event_update(case, request):
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    case.source.messages.append(replace(message(uuid4().hex, 10, '虚构工单 CASE-SYN-42 上次上传失败消息收到。'), uid=case.uid))
    result = case.fresh().run_window(case.uid, window(10))
    current = next(r for r in view(case, event_id)['executions'] if r['attempt_id'] == result.attempt_id)
    assert 'candidate' in current['related_by'] and current['candidates']
    assert current['commit_status'] == 'committed' and not current['updated'] and not current['created']
    assert current['suggestions'] == current['decisions'] == current['revision_indexes'] == []
    remember(request, case, event_id, '候选命中但没有事件写入')


@pytest.mark.parametrize('committed', [False, True])
def test_pending_commit_remains_pending_after_real_query_and_never_retries(case, monkeypatch, request, committed):
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    follow(case, event_id, 10, '虚构工单 CASE-SYN-42 新的排查结论。')
    service = case.fresh()
    original = service.store._conn.commit
    def missing_ack():
        if committed:
            original()
        raise pymysql.OperationalError(2013, 'synthetic panel commit acknowledgement lost')
    monkeypatch.setattr(service.store._conn, 'commit', missing_ack)
    with pytest.raises(AttemptPendingVerification):
        service.run_window(case.uid, window(10))
    prior = case.latest()
    before = deepcopy(prior), len(case.provider.calls)
    current = next(r for r in view(case, event_id)['executions'] if r['attempt_id'] == prior['attempt_id'])
    assert current['outcome'] == 'commit_unknown'
    assert current['commit_status'] == ('committed' if committed else 'unconfirmed')
    assert current['updated'] == committed and bool(current['revision_indexes']) == committed
    assert (case.latest(), len(case.provider.calls)) == before
    with pytest.raises(AttemptPendingVerification):
        case.fresh().run_window(case.uid, window(10), execution=dict(retry_of=prior['attempt_id']))
    assert len(case.provider.calls) == 2
    # 此测试保留未决状态供只读网页检查，不为展示目的核实或改成成功。
    remember(request, case, event_id, '提交应答不明：' + ('收据证明已提交，执行仍待核实' if committed else '无收据，提交结果未知'))


def test_failed_receipt_and_explicit_retry_have_distinct_real_rows(case, monkeypatch, request):
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    follow(case, event_id, 10, '虚构工单 CASE-SYN-42 补充定位参数错误。')
    original = case.attempts.write_receipt
    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise AttemptError('虚构收据已写但提交前停止')
    monkeypatch.setattr(case.attempts, 'write_receipt', fail)
    with pytest.raises(AttemptError):
        case.fresh().run_window(case.uid, window(10))
    prior = case.latest()
    failed = next(r for r in view(case, event_id)['executions'] if r['attempt_id'] == prior['attempt_id'])
    assert failed['commit_status'] == 'not_committed' and failed['decisions'] and not failed['revision_indexes']
    monkeypatch.setattr(case.attempts, 'write_receipt', original)
    retried = case.fresh().run_window(case.uid, window(10), execution=dict(retry_of=prior['attempt_id']))
    current = next(r for r in view(case, event_id)['executions'] if r['attempt_id'] == retried.attempt_id)
    assert current['retry_of'] == prior['attempt_id'] and current['updated'] and current['commit_status'] == 'committed'
    assert len(case.provider.calls) == 3
    remember(request, case, event_id, '提交前失败保留阶段，明确重试后写入')


def test_committed_display_failure_and_recovery_are_separate_from_business(case, monkeypatch, request):
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    follow(case, event_id, 10, '虚构工单 CASE-SYN-42 定位失败参数。')
    failed_service = case.fresh()
    monkeypatch.setattr(failed_service.state, 'snapshot', lambda *args: (_ for _ in ()).throw(OSError('虚构展示失败')))
    with pytest.raises(DeliveryError):
        failed_service.run_window(case.uid, window(10))
    prior = case.latest()
    recovered = case.fresh().run_window(case.uid, window(10), execution=dict(retry_of=prior['attempt_id']))
    items = {r['attempt_id']: r for r in view(case, event_id)['executions']}
    assert items[prior['attempt_id']]['updated'] and items[prior['attempt_id']]['commit_status'] == 'committed'
    assert any(d['destination'] == 'files' and d['status'] == 'failed' for d in items[prior['attempt_id']]['delivery'])
    recovery = items[recovered.attempt_id]
    assert recovery['receipt_kind'] == 'delivery_recovery' and recovery['retry_of'] == prior['attempt_id']
    assert recovery['processed'] == 0 and not recovery['updated'] and recovery['revision_indexes'] == []
    assert len(case.provider.calls) == 2
    remember(request, case, event_id, '业务已提交、展示失败、仅恢复展示不再分析')


def test_mysql_filters_exact_event_and_scope_before_loading_pages(case):
    event_id = case.service.run_window(case.uid, window(1)).created[0]
    original = case.latest()
    associated = {original['attempt_id']}
    for number in range(23):
        # 这里专门构造边界记录以验 JSON 查询，不把它们称为已经调用模型的完整批次。
        rid = case.attempts.create(case.uid, case.provider.name, window(10))
        case.attempts.stage(case.uid, case.provider.name, rid, 'history', 'completed', dict(candidates=[dict(event_id=event_id)]))
        associated.add(rid)
    parent = rid
    child = case.attempts.create(case.uid, case.provider.name, window(10), retry_of=parent)
    associated.add(child)
    for uid, stream in [(case.uid + '-other', case.provider.name), (case.uid, 'synthetic-other-stream')]:
        rid = case.attempts.create(uid, stream, window(10))
        case.attempts.stage(uid, stream, rid, 'history', 'completed', dict(candidates=[dict(event_id=event_id)]))
    unrelated = case.attempts.create(case.uid, case.provider.name, window(10))
    receipt = deepcopy(original['receipt'])
    receipt['writes'] = []
    receipt['cycle'].update(created=[], updated=[], filter_audit=[], attempt_id=unrelated)
    # 负例保留 event_versions 全量快照成员，不包含本事件候选、决定或写入。
    with case.service.store.transaction(case.uid):
        case.attempts.write_receipt(case.service.store, case.uid, case.provider.name, unrelated, receipt)
    first = view(case, event_id)
    assert first['total_executions'] == 25 and len(first['executions']) == 20 and first['next_execution_offset'] == 20
    second = view(case, event_id, offset=20)
    assert len(second['executions']) == 5 and second['next_execution_offset'] is None
    ids = [r['attempt_id'] for page in [first, second] for r in page['executions']]
    assert set(ids) == associated and len(ids) == len(set(ids))
    assert case.attempts.list_for_event_changes(case.uid, case.provider.name, event_id + "'other")["items"] == []
    queries = QueryService(case.settings)
    assert queries.event_changes(event_id, case.uid + '-other', case.provider.name, RunRepository(case.settings)) is None
    with pytest.raises(ValueError):
        queries.event_changes(event_id, case.uid, 'synthetic-other-stream', RunRepository(case.settings))


def test_legacy_scope_filter_and_missing_table_are_real_sql_compatible(case, request):
    service = case.fresh()
    service.attempts = None
    result = service.run_window(case.uid, window(1))
    event_id = result.created[0]
    runs = RunRepository(case.settings)
    payload, count = prepare_request(replace(case.settings, end=window(1).end), dict(interval='1d'))
    run = runs.create(payload, count)
    runner_id = 'synthetic-panel-legacy'
    assert runs.claim_next(runner_id)['run_id'] == run['run_id']
    runs.complete_window(run['run_id'], runner_id, result.to_dict())
    runs.finish(run['run_id'], runner_id)
    normal = view(case, event_id)
    assert normal['execution_records_available'] and not normal['executions'] and normal['batches'][0]['created']
    assert runs.list_for_event_changes(case.uid, case.provider.name, event_id=event_id)['total'] == 1
    assert runs.list_for_event_changes(case.uid, case.provider.name, event_id='synthetic-unrelated')['total'] == 0
    # 只在专用、已核验的测试库短暂移开表；finally 恢复，不删任何记录。
    with case.attempts.connection() as store:
        store._execute('RENAME TABLE processing_attempts TO synthetic_panel_hidden_attempts')
        try:
            missing = view(case, event_id)
            assert not missing['execution_records_available'] and missing['executions'] == []
            assert missing['batches'] == normal['batches'] and missing['revisions'] == normal['revisions']
        finally:
            store._execute('RENAME TABLE synthetic_panel_hidden_attempts TO processing_attempts')
    assert view(case, event_id)['execution_records_available']
    remember(request, case, event_id, '旧批次兼容：过程未记录，已有版本保留')
