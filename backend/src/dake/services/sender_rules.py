"""配置化账号身份管理，读写与增量处理共用同一份私有规则。"""

from datetime import datetime
import fcntl
import hashlib
import json
import os

from ..domain.models import TZ
from ..domain.sender_rules import SenderRule
from ..repositories.local_state import private_json
from ..repositories.senders import SenderRepository
from .sender_kinds import parse_sender_kinds, read_sender_kind_document, sender_kind


class RuleConflict(ValueError):
    """并发编辑时要求重新读取，避免覆盖其他窗口的新配置。"""


def rule_id(account):
    """按明确身份生成不含原始账号的稳定标识。"""
    parts = [account.get(key) or "" for key in ("uid", "source", "sender_id", "conversation_id")]
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def version(document):
    """配置内容而非文件时间决定版本，防止同秒写入丢失。"""
    return hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class SenderRuleService:
    """只改变后续处理的身份判断，历史消息和事件保持原有处理记录。"""

    def __init__(self, settings, repository=None):
        self.settings = settings
        self.repository = repository or SenderRepository(settings)

    def list(self):
        document = read_sender_kind_document(self.settings.data_dir)
        return self.response(document)

    @staticmethod
    def response(document):
        fields = SenderRule.model_fields
        return {"version": version(document), "items": [dict(
            SenderRule.model_validate({k: v for k, v in item.items() if k in fields}).model_dump(),
            rule_id=rule_id(item)) for item in document["accounts"]]}

    def candidates(self, keyword="", offset=0, limit=25):
        document = read_sender_kind_document(self.settings.data_dir)
        kinds = parse_sender_kinds(document)
        configured = {rule_id(a) for a in document["accounts"]}
        page = self.repository.search(keyword.strip(), offset, limit)
        for item in page["items"]:
            item["conversation_id"] = item.get("conversation_id") or ""
            item["sender_name"] = item.get("sender_name") or item["sender_id"]
            item["conversation_name"] = item.get("conversation_name") or item["conversation_id"]
            item["sender_kind"] = sender_kind(item, kinds)
            item["rule_id"] = rule_id(item)
            item["configured"] = item["rule_id"] in configured
        return page

    def save(self, values, expected_version, previous_id=None):
        rule = SenderRule.model_validate(values)
        folder = self.settings.data_dir / "config"
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(folder / "sender_kinds.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            document = read_sender_kind_document(self.settings.data_dir)
            if version(document) != expected_version:
                raise RuleConflict("配置已被更新，请刷新后再保存")
            previous = next((a for a in document["accounts"] if rule_id(a) == previous_id), None)
            if previous_id and previous is None:
                raise RuleConflict("这条配置已变化，请刷新后重试")
            value = rule.model_dump(exclude_none=True)
            new_id = rule_id(value)
            if any(rule_id(a) == new_id and rule_id(a) != previous_id for a in document["accounts"]):
                raise RuleConflict("该账号范围已有配置，请编辑已有规则")
            if (previous is None or rule_id(previous) != new_id) and not self.repository.observed(rule):
                raise ValueError("未找到此范围的已同步账号，请先查找账号")
            updated = dict(previous or {}, **value, updated_at=datetime.now(TZ).isoformat())
            if rule.conversation_id is None:
                updated.pop("conversation_id", None)
            document["accounts"] = [a for a in document["accounts"] if rule_id(a) != previous_id] + [updated]
            parse_sender_kinds(document)
            private_json(folder / "sender_kinds.json", document)
            return self.response(document)
