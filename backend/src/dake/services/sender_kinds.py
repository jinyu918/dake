"""已确认的人工／机器人身份，独立于内部／外部角色及原消息摘要。"""

import hashlib
import json
from pathlib import Path
from ..domain.models import SenderKind

SenderKinds = dict[tuple[str, str, str, str], SenderKind]
FILTER_VERSION = "confirmed-senders-v1"


def read_sender_kind_document(data_dir: Path) -> dict:
    """读取私有配置原文，供执行器和管理页面共用校验。"""
    try:
        value = json.loads((data_dir / "config" / "sender_kinds.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"accounts": []}
    except (OSError, ValueError):
        raise ValueError("本地人工／机器人配置无法读取") from None
    parse_sender_kinds(value)
    return value


def parse_sender_kinds(value: dict) -> SenderKinds:
    """只接受明确账号及确认依据；停用规则仍校验，但不参与身份判定。"""
    if not isinstance(value, dict) or not isinstance(value.get("accounts"), list):
        raise ValueError("本地人工／机器人配置格式无效")
    result: SenderKinds = {}
    seen = set()
    for account in value["accounts"]:
        if not isinstance(account, dict):
            raise ValueError("本地人工／机器人配置格式无效")
        key = tuple(account.get(name) for name in ("uid", "source", "sender_id"))
        kind = account.get("kind")
        basis = account.get("basis")
        if (not all(isinstance(part, str) and part.strip() for part in key)
                or kind not in ("human", "bot", "unknown")
                or not isinstance(basis, str) or not basis.strip()):
            raise ValueError("人工／机器人配置须包含有效账号、身份和确认依据")
        group = account.get("conversation_id", "")
        if not isinstance(group, str) or ("conversation_id" in account and not group.strip()):
            raise ValueError("机器人配置中的群标识无效")
        key = (*key, group)
        if key in seen:
            raise ValueError("人工／机器人配置包含重复账号")
        seen.add(key)
        if type(account.get("enabled", True)) is not bool:
            raise ValueError("机器人配置启用状态无效")
        if account.get("enabled", True):
            result[key] = kind
    return result


def load_sender_kinds(data_dir: Path) -> SenderKinds:
    """每个处理窗口读取一次配置，已开始的窗口继续使用同一份规则。"""
    return parse_sender_kinds(read_sender_kind_document(data_dir))


def sender_kind(payload: dict, kinds: SenderKinds | None = None) -> SenderKind:
    """不使用昵称或 internal_hint 推断身份，未配置即未知。"""
    key = tuple(str(payload.get(name) or "") for name in ("uid", "source", "sender_id"))
    group = str(payload.get("conversation_id") or "")
    configured = kinds or {}
    return configured.get((*key, group), configured.get((*key, ""), "unknown"))


def kinds_fingerprint(kinds: SenderKinds) -> str:
    """保存规则快照指纹供审计，不公开账号清单。"""
    return hashlib.sha256(json.dumps(sorted(kinds.items()), ensure_ascii=False).encode()).hexdigest()
