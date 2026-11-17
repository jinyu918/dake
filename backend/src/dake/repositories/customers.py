"""客户资料、群归属和同步周期的原子保存；不修改消息或分析事件。"""

from .runs import RunRepository, _decode, _encoded, _now
from .mysql import StorageError, _sql_time
from ..domain.models import TZ
from ..domain.customer_policy import (
    SEGMENT_LEVELS,
    SUPER_LARGE_LEVELS,
    CONFIRMED_LEVEL_STATE,
    CustomerFilter,
    is_super_large,
)

SYNC_UID = "customer-discovery"
SYNC_MODE = "customer_sync"


def all_profiles(repository):
    """分页读取完整客户目录，单页大小不再限制自动任务覆盖范围。"""
    items, offset = [], 0
    while True:
        page = repository.list_customers(offset=offset, limit=500)
        items.extend(page["items"])
        offset += len(page["items"])
        if offset >= page["total"]:
            return items
        if not page["items"]:
            raise ValueError("客户目录分页未完成，请重试")


def record(row):
    """转换查询行，不推导未提供的客户名称或等级。"""
    result = dict(row)
    for key, value in result.items():
        if hasattr(value, "isoformat"):
            result[key] = value.replace(tzinfo=TZ).isoformat()
    if "candidate_uids" in result:
        result["candidate_uids"] = _decode(result["candidate_uids"], [])
    if "level" in result or "level_state" in result:
        result["is_super_large"] = is_super_large(result.get("level"), result.get("level_state"))
    return result


class CustomerRepository(RunRepository):
    """复用短连接工厂；表由显式迁移创建，查询不隐式建表。"""

    def list_customers(self, offset=0, limit=50, keyword="", segment: CustomerFilter = "all"):
        """先按统一等级口径筛选再分页，统计与列表使用相同条件。"""
        if segment not in ("all", "super_large", "pending", *SEGMENT_LEVELS):
            raise ValueError("客户筛选类别无效")
        clauses, classification_args = [], []
        for name, levels in SEGMENT_LEVELS.items():
            clauses.append("WHEN p.level_state=%s AND p.level IN (" + ",".join(["%s"] * len(levels)) + ") THEN %s")
            classification_args.extend((CONFIRMED_LEVEL_STATE, *levels, name))
        classification = "CASE " + " ".join(clauses) + " ELSE 'pending' END"
        source = " FROM (SELECT p.*, " + classification + " AS segment FROM customer_profiles p) p"
        conditions, args = [], list(classification_args)
        if keyword:
            conditions.append("p.uid LIKE %s")
            args.append("%" + keyword + "%")
        if segment == "super_large":
            conditions.append(
                "p.level_state=%s AND p.level IN (" + ",".join(["%s"] * len(SUPER_LARGE_LEVELS)) + ")"
            )
            args.extend((CONFIRMED_LEVEL_STATE, *SUPER_LARGE_LEVELS))
        elif segment != "all":
            conditions.append("p.segment=%s")
            args.append(segment)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as store:
            counts, _ = store._execute("SELECT COUNT(*) AS total" + source + where, tuple(args))
            rows, _ = store._execute("SELECT p.*, (SELECT COUNT(*) FROM customer_groups g WHERE g.uid=p.uid) AS group_count "
                + source + where + " ORDER BY p.updated_at DESC,p.uid LIMIT %s OFFSET %s", tuple(args) + (limit, offset))
        return {"items": [record(row) for row in rows], "total": counts[0]["total"]}

    def list_groups(self, uid=None, review=False, offset=0, limit=50):
        conditions, args = [], []
        if uid is not None:
            conditions.append("uid=%s")
            args.append(uid)
        if review:
            conditions.append("status='review'")
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self.connection() as store:
            counts, _ = store._execute("SELECT COUNT(*) AS total FROM customer_groups" + where, tuple(args))
            rows, _ = store._execute("SELECT * FROM customer_groups" + where
                + " ORDER BY synced_at DESC,source,group_id LIMIT %s OFFSET %s", tuple(args) + (limit, offset))
        return {"items": [record(row) for row in rows], "total": counts[0]["total"]}

    def ingest_discovered(self, raw, window, groups):
        """逐客户提交已核实消息后再保存同步进度；中断重放依靠消息幂等。"""
        from copy import deepcopy
        from collections import defaultdict
        from ..ingestion.adapter import ChatRecordsAdapter

        owners = {}
        with self.connection() as store:
            for group in groups:
                if group["status"] != "confirmed" or not group["uid"]:
                    continue
                old, _ = store._execute("SELECT uid,status FROM customer_groups WHERE source=%s AND group_id=%s",
                    (group["source"], group["group_id"]))
                if old and (old[0]["status"] != "confirmed" or old[0]["uid"] != group["uid"]):
                    continue
                owners[group["group_id"]] = group["uid"]
        accepted = []
        for group in raw["groups"]:
            uid = owners.get(group["group_number"])
            if not uid:
                continue
            value = deepcopy(group)
            value["uid"] = uid
            for row in value["records"]:
                row["uid"] = uid
            accepted.append(value)
        payload = dict(raw, groups=accepted, total=sum(len(g["records"]) for g in accepted))
        messages = ChatRecordsAdapter().normalize_window(payload, window.start, window.end)
        by_uid = defaultdict(list)
        for message in messages:
            by_uid[message.uid].append(message)
        with self.connection() as store:
            for uid, items in by_uid.items():
                with store.transaction(uid):
                    store.upsert_messages(items)

    def save_window(self, run_id, runner_id, window, groups, profiles, stats):
        """客户及映射与任务进度同事务提交；提交不明时任务失败，可幂等重同步。"""
        now = _now()
        with self.connection() as store, store.transaction(SYNC_UID):
            owned, _ = store._execute("SELECT run_id FROM processing_runs WHERE run_id=%s AND runner_id=%s "
                "AND status='running' FOR UPDATE", (run_id, runner_id))
            if not owned:
                raise StorageError("客户同步执行权已变化")
            stats = dict(stats, new_customers=0, refreshed_customers=0, new_groups=0, review_groups=0)
            for uid, profile in profiles.items():
                old, _ = store._execute("SELECT * FROM customer_profiles WHERE uid=%s FOR UPDATE", (uid,))
                previous = old[0] if old else {}
                result = dict(previous)
                for section, fields in (("level", ("level", "rank")), ("tam", ("tam", "tam_b"))):
                    value = profile[section]
                    success = value["error"] is None
                    for field in fields:
                        column = "rank_value" if field == "rank" else field
                        result[column] = value["data"].get(field) if success else previous.get(column)
                    result[section + "_synced_at"] = now if success else previous.get(section + "_synced_at")
                    result[section + "_error"] = value["error"]
                    result[section + "_state"] = ("fresh" if any(value["data"].get(f) for f in fields if f != "rank") else "unknown") if success else ("stale" if previous.get(section + "_synced_at") else "error")
                columns = ("level", "rank_value", "tam", "tam_b", "level_state", "tam_state", "level_error", "tam_error", "level_synced_at", "tam_synced_at")
                values = tuple(result[c] for c in columns)
                if old:
                    store._execute("UPDATE customer_profiles SET " + ",".join(c + "=%s" for c in columns)
                        + ",attempted_at=%s,updated_at=%s WHERE uid=%s", values + (now, now, uid))
                    stats["refreshed_customers"] += 1
                else:
                    store._execute("INSERT INTO customer_profiles (uid," + ",".join(columns)
                        + ",attempted_at,created_at,updated_at) VALUES (" + ",".join(["%s"] * 14) + ")", (uid,) + values + (now, now, now))
                    stats["new_customers"] += 1
            for group in groups:
                group = dict(group)
                old, _ = store._execute("SELECT * FROM customer_groups WHERE source=%s AND group_id=%s FOR UPDATE",
                                       (group["source"], group["group_id"]))
                previous = record(old[0]) if old else None
                if previous and previous["uid"] and (previous["uid"] != group["uid"] or previous["status"] == "review"):
                    group.update(uid=previous["uid"], status="review", reason="已有归属与本轮结果不一致或尚待复核，保留原归属并暂停自动使用",
                        candidate_uids=sorted(set(group["candidate_uids"] + previous["candidate_uids"] + [previous["uid"]])))
                stats["review_groups"] += group["status"] == "review"
                values = (group["group_name"], group["uid"], group["status"], group["reason"], _encoded(group["candidate_uids"]))
                if old:
                    store._execute("UPDATE customer_groups SET group_name=%s,uid=%s,status=%s,reason=%s,candidate_uids=%s,"
                        "first_seen_at=LEAST(first_seen_at,%s),last_seen_at=GREATEST(last_seen_at,%s),synced_at=%s WHERE source=%s AND group_id=%s",
                        values + (_sql_time(window.start), _sql_time(window.end), now, group["source"], group["group_id"]))
                else:
                    store._execute("INSERT INTO customer_groups (group_name,uid,status,reason,candidate_uids,first_seen_at,last_seen_at,synced_at,source,group_id) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", values + (_sql_time(window.start), _sql_time(window.end), now, group["source"], group["group_id"]))
                    stats["new_groups"] += 1
                keys = ("uid", "status", "reason", "candidate_uids")
                after = {key: group[key] for key in keys}
                before = {key: previous[key] for key in keys} if previous else None
                if before != after:
                    store._execute("INSERT INTO customer_group_history (source,group_id,run_id,before_value,after_value,recorded_at) "
                        "VALUES (%s,%s,%s,%s,%s,%s)", (group["source"], group["group_id"], run_id,
                        _encoded(before) if before else None, _encoded(after), now))
            cycle = {"window": {"start": window.start.isoformat(), "end": window.end.isoformat()}, **stats}
            _, changed = store._execute("UPDATE processing_runs SET cycles=JSON_ARRAY_APPEND(cycles,'$',CAST(%s AS JSON)),"
                "completed_windows=completed_windows+1,current_window=NULL,current_baseline=NULL "
                "WHERE run_id=%s AND runner_id=%s AND status='running'", (_encoded(cycle), run_id, runner_id))
            if not changed:
                raise StorageError("客户同步进度保存失败")
        return cycle
