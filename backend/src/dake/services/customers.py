"""客户发现及资料同步业务入口，供后台任务与后续调度共用，不调用模型。"""

from datetime import datetime, timedelta
import math

from ..domain.models import TZ, Window
from ..config import bound
from ..ingestion.client import ChatRecordsClient, ChatRecordsError
from ..ingestion.directory import DirectoryClient
from ..ingestion.discovery import discover_groups
from ..repositories.customers import CustomerRepository, SYNC_MODE, SYNC_UID
from ..repositories.local_state import private_json
from .queries import duration_label

MAX_SYNC_CUSTOMERS = 500


def prepare_sync(values, interval=timedelta(minutes=15)):
    """客户同步沿用可配置周期，单次至多一天；时间范围不读取演示配置。"""
    start, end = bound(values["start"]), bound(values["end"], end=True)
    Window(start, end)
    if end - start > timedelta(days=1) or end > datetime.now(TZ) + timedelta(minutes=1):
        raise ValueError("客户同步范围不能超过一天或包含未来时间")
    if interval <= timedelta(0) or interval > timedelta(days=1) or math.ceil((end - start) / interval) > 1440:
        raise ValueError("客户同步周期或窗口数量超出上限")
    return {"mode": SYNC_MODE, "uid": SYNC_UID, "start": start.isoformat(), "end": end.isoformat(),
            "interval": duration_label(interval), "offline": False}, math.ceil((end - start) / interval)


class CustomerSyncService:
    """一次任务内资料按客户复用，跨任务刷新，失败不覆盖已保存的有效资料。"""

    def __init__(self, settings, repository=None, chats=None, directory=None):
        self.settings = settings
        self.repository = repository or CustomerRepository(settings)
        self.chats = chats or ChatRecordsClient(timeout=10, retries=1)
        self.directory = directory or DirectoryClient(timeout=10, retries=1)
        self.profiles = {}
        self.task_options = None

    def sync_window(self, window, run_id, runner_id, index, check=lambda: None):
        """查询窗口、补查归属及资料后，原子保存客户和周期结果。"""
        check()
        raw = self.chats.fetch_window(window.start, window.end)
        private_json(self.settings.data_dir / "raw" / "customer-sync" / run_id / f"{index:04d}.json", raw)
        groups, count = discover_groups(raw, window)
        lookup_errors = 0
        for group in groups:
            if group["status"] == "confirmed" or len(group["candidate_uids"]) > 1:
                continue
            keyword = group["group_name"]
            if not keyword or len(keyword) > 128:
                group["reason"] = "群名缺失或过长，无法自动补查客户"
                continue
            check()
            try:
                matches = self.directory.search(keyword)
            except ChatRecordsError:
                group["reason"] = "群搜索失败，保留待确认"
                lookup_errors += 1
                continue
            exact = {item["uid"] for item in matches if item["group_id"] == group["group_id"] and item["uid"]}
            candidates = set(group["candidate_uids"])
            if len(exact) == 1 and (not candidates or candidates == exact):
                group.update(uid=next(iter(exact)), status="confirmed", reason="群搜索返回同一群的明确客户归属", candidate_uids=sorted(exact))
            else:
                candidates.update(item["uid"] for item in matches if item["uid"])
                group.update(candidate_uids=sorted(candidates), reason="搜索未返回可确认的同群归属" if matches else "群搜索无结果")
        pending_profiles, profile_errors = {}, 0
        from ..repositories.customers import all_profiles
        known = all_profiles(self.repository) if self.task_options else []
        previous = {p["uid"]: p for p in known}
        identities = {g["uid"] for g in groups if g["uid"]}
        if self.task_options:
            identities.update(previous)
            identities.update(self.task_options.get("uids", []))
        for uid in sorted(identities):
            if uid in self.profiles:
                continue
            if self.task_options and uid in previous:
                from ..config import duration
                saved = previous[uid]
                if (saved["level_state"] == "fresh" and saved["tam_state"] == "fresh"
                        and saved.get("level_synced_at") and saved.get("tam_synced_at")
                        and all(datetime.now(TZ) - bound(saved[k]) < duration(self.task_options["profile_ttl"])
                                for k in ("level_synced_at", "tam_synced_at"))):
                    self.profiles[uid] = {}
                    continue
            if not self.task_options and len(self.profiles) >= MAX_SYNC_CUSTOMERS:
                raise ValueError("单次同步客户数超过上限，请缩小范围")
            profile = {}
            for name in ("level", "tam"):
                check()
                try:
                    profile[name] = {"data": getattr(self.directory, name)(uid), "error": None}
                except ChatRecordsError:
                    profile[name] = {"data": {}, "error": "等级查询失败" if name == "level" else "负责人查询失败"}
                    profile_errors += 1
            self.profiles[uid] = profile
            pending_profiles[uid] = profile
        check()
        if self.task_options:
            self.repository.ingest_discovered(raw, window, groups)
        check()
        return self.repository.save_window(run_id, runner_id, window, groups, pending_profiles,
            {"messages": count, "groups": len(groups), "customers": len({g["uid"] for g in groups if g["uid"]}),
             "profile_errors": profile_errors, "lookup_errors": lookup_errors})
