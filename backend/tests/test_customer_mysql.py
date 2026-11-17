"""客户同步实库测试仅使用随机合成标识，并只清理本测试创建的记录。"""

from dataclasses import replace
from uuid import uuid4

import pytest

from dake.config import Settings
from dake.domain.models import Window
from dake.repositories.customers import CustomerRepository, SYNC_UID, SYNC_MODE
from dake.repositories.runs import RunRepository, _encoded, _now
from dake.repositories.mysql import StorageError
from test_store import mysql_store
from test_ingestion_windows import START, END


@pytest.mark.mysql
def test_sender_candidates_group_by_stable_identity_and_paginate(mysql_store, tmp_path):
    from dake.repositories.senders import SenderRepository
    from dake.domain.sender_rules import SenderRule
    from test_store import fake_message
    store, uid, other, options = mysql_store
    settings = replace(Settings(), data_dir=tmp_path, mysql_host=options["host"], mysql_port=options["port"],
        mysql_user=options["user"], mysql_password=options["password"], mysql_database=options["database"])
    name = "合成MAAS-" + uid
    for owner, group, number in ((uid, "synthetic-a", 2), (uid, "synthetic-b", 1), (other, "synthetic-a", 1)):
        with store.transaction(owner):
            store.upsert_messages([fake_message(owner, sender_id="synthetic-shared-name", sender_name=name,
                conversation_id=group) for _ in range(number)])
    repo = SenderRepository(settings)
    page = repo.search(name.lower(), 0, 2)
    assert page["total"] == 3 and len(page["items"]) == 2
    remaining = repo.search(name, 2, 2)
    assert len(remaining["items"]) == 1
    all_items = page["items"] + remaining["items"]
    assert sorted(item["message_count"] for item in all_items) == [1, 1, 2]
    assert all("content" not in item for item in all_items)
    rule = SenderRule(uid=uid, source="test_store", sender_id="synthetic-shared-name", basis="合成确认")
    assert repo.observed(rule)
    assert not repo.observed(rule.model_copy(update={"conversation_id": "unobserved-room"}))


@pytest.mark.mysql
def test_sender_search_treats_percent_as_literal(mysql_store, tmp_path):
    from dake.repositories.senders import SenderRepository
    from test_store import fake_message
    store, uid, _, options = mysql_store
    settings = replace(Settings(), data_dir=tmp_path, mysql_host=options["host"], mysql_port=options["port"],
        mysql_user=options["user"], mysql_password=options["password"], mysql_database=options["database"])
    with store.transaction(uid):
        store.upsert_messages([fake_message(uid, sender_id="literal-match", sender_name=uid + "_%合成"),
                               fake_message(uid, sender_id="wildcard-decoy", sender_name=uid + "-合成")])
    result = SenderRepository(settings).search(uid + "_%")
    assert result["total"] == 1 and result["items"][0]["sender_id"] == "literal-match"


@pytest.fixture
def directory_db(mysql_store, tmp_path):
    store, uid, other, options = mysql_store
    settings = replace(Settings(), data_dir=tmp_path, mysql_host=options["host"], mysql_port=options["port"],
        mysql_user=options["user"], mysql_password=options["password"], mysql_database=options["database"])
    customers, runs = CustomerRepository(settings), RunRepository(settings)
    runs.init_schema()
    gid = "synthetic-" + uuid4().hex
    run_ids = []
    def new_run():
        request = {"uid": SYNC_UID, "mode": SYNC_MODE, "start": START.isoformat(), "end": END.isoformat(), "interval": "15m"}
        # 直接插入本测试拥有的运行态，避免真实后台线程领取合成任务。
        rid = str(uuid4())
        run_ids.append(rid)
        store._execute("INSERT INTO processing_runs (run_id,uid,mode,status,request,total_windows,runner_id,created_at,cycles) "
            "VALUES (%s,%s,%s,'running',%s,1,'synthetic-worker',%s,JSON_ARRAY())",
            (rid, uid, SYNC_MODE, _encoded(request), _now()))
        return rid
    try:
        yield customers, runs, store, uid, other, gid, new_run
    finally:
        store._execute("DELETE FROM customer_group_history WHERE group_id=%s", (gid,))
        store._execute("DELETE FROM customer_groups WHERE group_id=%s", (gid,))
        store._execute("DELETE FROM customer_profiles WHERE uid IN (%s,%s)", (uid, other))
        for rid in run_ids:
            store._execute("DELETE FROM processing_runs WHERE run_id=%s", (rid,))


def profile(level="L7", error=None):
    return {"level": {"data": {"level": level, "rank": "synthetic"}, "error": error},
            "tam": {"data": {"tam": "合成负责人", "tam_b": None}, "error": None}}


def group(gid, uid):
    return {"source": "official_chat", "group_id": gid, "group_name": "合成测试群", "uid": uid,
            "status": "confirmed", "reason": "合成明确归属", "candidate_uids": [uid]}


def save(repo, rid, groups, profiles):
    return repo.save_window(rid, "synthetic-worker", Window(START, END), groups, profiles,
        {"messages": 1, "groups": len(groups), "customers": len(profiles), "profile_errors": 0, "lookup_errors": 0})


@pytest.mark.mysql
def test_customer_repeat_refresh_failure_and_mapping_conflict(directory_db):
    repo, runs, store, uid, other, gid, new_run = directory_db
    rid = new_run()
    result = save(repo, rid, [group(gid, uid)], {uid: profile()})
    runs.finish(rid, "synthetic-worker")
    assert result["new_customers"] == result["new_groups"] == 1
    rid = new_run()
    result = save(repo, rid, [group(gid, uid)], {uid: profile(error="合成查询失败")})
    runs.finish(rid, "synthetic-worker")
    assert result["new_customers"] == result["new_groups"] == 0
    item = repo.list_customers(keyword=uid)["items"][0]
    assert item["level"] == "L7" and item["level_state"] == "stale"
    assert item["tam_state"] == "fresh"
    assert repo.list_groups(uid=uid)["total"] == 1
    rid = new_run()
    save(repo, rid, [group(gid, other)], {other: profile()})
    runs.finish(rid, "synthetic-worker")
    mapping = repo.list_groups(uid=uid)["items"][0]
    assert mapping["uid"] == uid and mapping["status"] == "review"
    assert set(mapping["candidate_uids"]) == {uid, other}
    assert runs.get(rid)["completed_windows"] == 1
    history, _ = store._execute("SELECT COUNT(*) AS n FROM customer_group_history WHERE group_id=%s", (gid,))
    assert history[0]["n"] == 2


@pytest.mark.mysql
def test_customer_transaction_rolls_back_with_progress_and_lost_owner(directory_db, monkeypatch):
    repo, runs, store, uid, _, gid, new_run = directory_db
    rid = new_run()
    original = repo.store_factory._execute
    def fail_progress(self, sql, args=()):
        if sql.startswith("UPDATE processing_runs SET cycles="):
            raise StorageError("合成进度失败")
        return original(self, sql, args)
    with monkeypatch.context() as context:
        context.setattr(repo.store_factory, "_execute", fail_progress)
        with pytest.raises(StorageError): save(repo, rid, [group(gid, uid)], {uid: profile()})
    assert repo.list_customers(keyword=uid)["total"] == 0
    assert repo.list_groups(uid=uid)["total"] == 0
    assert runs.get(rid)["completed_windows"] == 0
    runs.finish(rid, "synthetic-worker", error="合成失败")
    with pytest.raises(StorageError): save(repo, rid, [group(gid, uid)], {uid: profile()})


@pytest.mark.mysql
def test_customer_filter_counts_before_pagination_and_matches_shared_policy(directory_db):
    from dake.domain.customer_policy import customer_segment

    repo, _, store, uid, _, _, _ = directory_db
    prefix = uid + '-segment-'
    values = [('L5', 'fresh')] * 51 + [('L6', 'fresh'), ('L10', 'fresh'), ('L9', 'fresh'),
        ('L9', 'stale'), ('L0', 'error'), (None, 'unknown'), ('L11', 'fresh'), ('L6', 'unknown'), ('l6', 'fresh')]
    ids = [prefix + str(index).zfill(3) for index in range(len(values))]
    now = _now()
    try:
        for identity, (level, state) in zip(ids, values):
            store._execute("INSERT INTO customer_profiles (uid,level,level_state,tam_state,attempted_at,created_at,updated_at) "
                "VALUES (%s,%s,%s,'unknown',%s,%s,%s)", (identity, level, state, now, now, now))
        expected = {name: [identity for identity, (level, state) in zip(ids, values) if customer_segment(level, state) == name]
                    for name in ('large', 'non_large', 'pending')}
        for name in expected:
            page = repo.list_customers(keyword=prefix, segment=name, limit=100)
            assert [item['uid'] for item in page['items']] == expected[name]
            assert page['total'] == len(expected[name])
            assert all(item['segment'] == name for item in page['items'])
        super_page = repo.list_customers(keyword=prefix, segment='super_large', limit=100)
        assert [item['uid'] for item in super_page['items']] == [ids[52], ids[53]]
        assert super_page['total'] == 2
        assert all(item['is_super_large'] and item['segment'] == 'large' for item in super_page['items'])
        page = repo.list_customers(keyword=prefix, segment='large', offset=1, limit=1)
        assert page['total'] == 3 and [item['uid'] for item in page['items']] == [ids[52]]
        assert repo.list_customers(keyword=ids[52], segment='large')['total'] == 1
        assert repo.list_customers(keyword=ids[52], segment='non_large')['total'] == 0
        assert repo.list_customers(keyword=prefix, segment='all')['total'] == len(ids)
        assert repo.list_customers(keyword=prefix, segment='large', offset=3)['items'] == []
        with pytest.raises(ValueError): repo.list_customers(segment="invalid")
    finally:
        store._execute("DELETE FROM customer_profiles WHERE uid IN (" + ','.join(['%s'] * len(ids)) + ")", tuple(ids))
