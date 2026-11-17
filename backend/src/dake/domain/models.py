"""与数据来源无关的消息、候选事件和事件合约；业务缺省不代表官方枚举。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Literal
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
EVENT_TYPES = ("商机", "流失", "排障", "资源需求", "功能需求", "商务需求")
EVENT_LABELS = EVENT_TYPES + ("其他",)
PENDING_TYPE = "待分类"

# 只作为属性名称的业务字典，不把缺失字段变成建项条件。
TYPE_ATTRIBUTE_NAMES = {
    "商机": ("交易对象与采购范围", "采购意向与评估事实", "计划规模", "预算或交易金额", "采购/上线计划时间", "成交事实及范围"),
    "流失": ("流失对象与范围", "风险、计划与实际变化", "原因与触发条件", "减少或转移规模", "去向与执行时间", "挽留或退出办理结果"),
    "排障": ("异常对象与环境", "症状与对照表现", "客户影响", "发生及影响范围", "排查发现与根因", "处置与验证结果", "恢复事实"),
    "资源需求": ("资源对象与交付范围", "请求动作与用途", "现有可用量", "目标总量与增减量", "资源缺口", "期望交付与承诺", "审批、交付与可用验证"),
    "功能需求": ("能力对象与使用场景", "当前行为及限制", "期望行为与改善目标", "验收条件", "期望可用时间", "评估与承诺范围", "上线与需求验证结果"),
    "商务需求": ("办理对象与事项范围", "价格与金额口径", "合同与商务条件", "续费或终止安排", "账单与发票要求", "办理期限与约定", "办理事实与结果"),
}

# 人员角色用于聊天展示；业务判断仅接受已确认账号，不属于官方事件状态枚举。
SenderRole = Literal["internal", "support", "customer", "external", "unknown"]
# 技术身份轴，不属于官方事件状态或人员内外部角色。
SenderKind = Literal["human", "bot", "unknown"]


def parse_time(value: str | datetime) -> datetime:
    """统一为上海时区；接口不带时区的时间按上海本地时间解释。"""
    result = datetime.fromisoformat(value) if isinstance(value, str) else value
    return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)


def json_value(value: Any) -> Any:
    """将嵌套时间转换为带时区的文本，供数据库 JSON 和本地快照保存。"""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


@dataclass(frozen=True)
class Message:
    """适配后的统一消息；internal_hint 只保留原始提示，不断言人员身份。"""
    uid: str
    message_id: str
    sent_at: datetime
    content: str
    source: str = "official_chat"
    conversation_id: str = ""
    conversation_name: str = ""
    sender_id: str = ""
    sender_name: str = ""
    internal_hint: int | None = None
    markers: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        """返回可持久化结构，其中正文仅可写入受保护的本地数据存储。"""
        return json_value(asdict(self))

    @classmethod
    def from_dict(cls, value: dict) -> "Message":
        """恢复数据库消息，保持发送时间的时区及附件标记。"""
        value = dict(value)
        value["sent_at"] = parse_time(value["sent_at"])
        value["markers"] = tuple(value.get("markers", ()))
        return cls(**value)

    @property
    def key(self) -> str:
        """来源与原消息 ID 共同定位证据，避免未来不同渠道的 ID 冲突。"""
        return f"{self.source}:{self.message_id}"

    @property
    def digest(self) -> str:
        """检测相同 ID 的消息修订，让修订内容能够重新进入增量分析。"""
        return hashlib.sha256(json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def message_order(message: Message) -> tuple:
    """跨群按发送时间排序；同秒数字 ID 按数值顺序，避免字符串排序歧义。"""
    mid = message.message_id
    return message.sent_at, (0, int(mid)) if mid.isdigit() else (1, mid), message.source


@dataclass(frozen=True)
class Window:
    """业务时间窗口统一使用左闭右开区间，避免相邻周期重复覆盖端点。"""
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or self.start >= self.end:
            raise ValueError("窗口必须带时区且 start < end")


def windows(start: datetime, end: datetime, interval: timedelta):
    """按任意正周期切分范围；最后一个窗口可短于完整周期。"""
    if interval.total_seconds() <= 0 or start >= end:
        raise ValueError("处理周期须大于零，起始时间须早于结束时间")
    while start < end:
        stop = min(start + interval, end)
        yield Window(start, stop)
        start = stop


@dataclass
class Candidate:
    """Provider 输出的候选；证据必须引用输入消息，主题不明时进入复核。"""
    type: str
    topic_key: str | None
    summary: str
    evidence_ids: list[str]
    status: str | None = None
    severity: str | None = None
    assignees: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    extensions: dict = field(default_factory=dict)
    ambiguous: bool = False
    referenced_event_id: str | None = None
    decision: Literal["new", "update", "duplicate", "review"] | None = None
    decision_reason: str = ""
    basis: list[dict] = field(default_factory=list)
    possible_event_ids: list[str] = field(default_factory=list)
    changes: list[dict] | None = None
    continuity: dict | None = None
    # 旧 Provider 只填 type；新 Provider 可填完整当前标签集合及逐标签依据。
    types: list[str] | None = None
    type_evidence: dict[str, list[dict]] = field(default_factory=dict)
    classification_status: str = "已分类"
    other_reason: dict | None = None
    removed_types: dict[str, list[dict]] = field(default_factory=dict)
    typed_attributes: dict[str, dict[str, dict]] = field(default_factory=dict)
    classification_conflicts: list[str] = field(default_factory=list)
    field_updates: list[dict] | None = None
    relations: list[dict] = field(default_factory=list)

    def label_types(self) -> list[str]:
        """返回当前标签集合；兼容只保存单主类型的旧候选。"""
        if self.types is not None:
            return list(dict.fromkeys(self.types))
        return [] if self.type == PENDING_TYPE else [self.type]


def normalize_classification(value: dict) -> dict:
    result = dict(value)
    result["version"] = max(result.get("version", 0), len(result.get("updated_by", [])))
    if result.get("types") is None:
        result["types"] = [result["type"]] if result.get("type") not in (None, PENDING_TYPE) else []
    for name in ("type_evidence", "removed_types", "typed_attributes"):
        if result.get(name) is None:
            result[name] = {}
    legacy_attributes = result.pop("attribute_evidence", None)
    if legacy_attributes and not result["typed_attributes"] and result.get("type") not in (None, PENDING_TYPE):
        result["typed_attributes"] = {result["type"]: legacy_attributes}
    if result.get("classification_evidence_status") is None:
        result["classification_evidence_status"] = "已记录" if result["type_evidence"] else "未记录"
    if result.get("classification_status") is None:
        result["classification_status"] = "待分类" if result.get("type") == PENDING_TYPE else "已分类"
    return result


@dataclass
class Event:
    """稳定事件身份及当前状态；证据和修订历史随更新追加，不丢失旧版本。"""
    event_id: str
    uid: str
    stream: str
    type: str
    topic_key: str | None
    start_time: datetime
    end_time: datetime
    status: str
    severity: str
    summary: str
    created_on: datetime
    assignees: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    extensions: dict = field(default_factory=dict)
    updated_by: list[dict] = field(default_factory=list)
    review_flag: str | None = None
    closed_at: datetime | None = None
    schema_version: int = 1
    recovery: dict | None = None
    recurrence_of: dict | None = None
    # 保留 type 作为旧接口主类型；types 记录完整标签集合。
    types: list[str] = field(default_factory=list)
    type_evidence: dict[str, list[dict]] = field(default_factory=dict)
    removed_types: dict[str, list[dict]] = field(default_factory=dict)
    classification_evidence_status: str = "未记录"
    classification_status: str = "已分类"
    other_reason: dict | None = None
    typed_attributes: dict[str, dict[str, dict]] = field(default_factory=dict)
    version: int = 0
    field_states: dict[str, dict] = field(default_factory=dict)
    field_issues: list[dict] = field(default_factory=list)
    manual_operations: list[dict] = field(default_factory=list)
    corrected_into: str | None = None
    identity_corrections: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """导出完整事件，只供数据库和私有快照使用，不直接打印到日志。"""
        value = asdict(self)
        value["version"] = max(self.version, len(self.updated_by))
        if not value["types"] and value["type"] != PENDING_TYPE:
            value["types"] = [value["type"]]
        return json_value(value)

    @classmethod
    def from_dict(cls, value: dict) -> "Event":
        """从持久化结构恢复事件及其时间字段。"""
        value = normalize_classification(value)
        for key in ("start_time", "end_time", "created_on", "closed_at"):
            if value.get(key) is not None:
                value[key] = parse_time(value[key])
        value.setdefault("other_reason", None)
        return cls(**value)


@dataclass
class CycleResult:
    """一次处理的变化和事件快照，供命令行与后续接口复用。"""
    uid: str
    stream: str
    window: Window
    ingested: int = 0
    processed: int = 0
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    review: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    watermark: dict = field(default_factory=dict)
    filter_audit: list[dict] = field(default_factory=list)
    attempt_id: str | None = None
    recovered_from_attempt_id: str | None = None

    def to_dict(self) -> dict:
        """导出周期结果；可能含证据，仅保存在 data/ 内。"""
        result = json_value(asdict(self))
        if self.recovered_from_attempt_id:
            result["recovered_from_history"] = True
        return result

    @classmethod
    def from_dict(cls, value: dict) -> "CycleResult":
        """恢复已保存的演示记录；额外恢复标记不参与本次业务结果。"""
        value = dict(value)
        value.pop("recovered_from_history", None)
        value.pop("counts_complete", None)
        value["window"] = Window(parse_time(value["window"]["start"]), parse_time(value["window"]["end"]))
        return cls(**value)
