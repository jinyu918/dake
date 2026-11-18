"""集中读取环境配置；未正式公布的业务规则保留待确认的可配置缺省。"""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import os
from pathlib import Path
import re

from dotenv import dotenv_values

from .domain.item_clock import ServiceCalendar
from .domain.models import parse_time

# 配置与真实数据固定锚定仓库根，避免从 backend/ 启动时误建另一份数据目录。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"

OFFICIAL_URL = "https://ts.qiniu.io/api/mars_alert/qq/chatRecords"
UNKNOWN = "未知"
REVIEW = "待确认"

# 本机交互服务的工作量上限，仅用于资源控制，不属于官方业务或评分规则。
API_MAX_RANGE_DAYS = 31
API_MAX_WINDOWS = 2048
API_MAX_PENDING_RUNS = 32
API_RUN_LIST_LIMIT = 200


def duration(value: str) -> timedelta:
    """解析独立的周期或观察窗口，支持秒、分钟、小时、天。"""
    match = re.fullmatch(r"([1-9][0-9]*)(s|m|h|d)", value.strip())
    if not match:
        raise ValueError("周期须为正整数加 s/m/h/d，例如 15m、1h、1d")
    return timedelta(seconds=int(match[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[match[2]])


def bound(value: str, *, end: bool = False) -> datetime:
    """日期形式的结束参数包含整天；明确时间戳采用左闭右开端点。"""
    result = parse_time(value)
    return result + timedelta(days=1) if end and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else result


@dataclass(frozen=True)
class HistoryRetrievalSettings:
    """历史候选的本地资源缺省，待离线评测调整，不属于业务匹配规则。"""

    recent_days: int = 7
    page_size: int = 40
    recent_pages: int = 2
    older_pages: int = 2
    exact_limit: int = 8
    keyword_limit: int = 32
    max_candidates: int = 8
    max_chars: int = 12000
    evidence_limit: int = 8

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("历史检索预算必须是正整数")
        if self.max_chars < 2:
            raise ValueError("历史候选字符预算至少容纳空列表")


@dataclass(frozen=True)
class HistoryContextSettings:
    """旧聊天的工程资源上限；初始保护值，不代表最佳条数或业务时间规则。"""

    max_messages: int = 24
    max_chars: int = 12000
    message_chars: int = 1600
    neighbor_messages: int = 4
    neighbor_minutes: int = 30
    max_input_chars: int = 128000

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("历史上下文预算必须是正整数")
        if self.max_chars < 1024 or self.message_chars < 2 or self.max_input_chars < self.max_chars:
            raise ValueError("历史上下文及总输入字符预算不合法")


@dataclass(frozen=True)
class Settings:
    """依赖注入的运行配置；密码不参与对象文本表示，避免调试时意外输出。"""
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3307
    mysql_database: str = "dake_aftersales"
    mysql_user: str = "dake"
    mysql_password: str = field(default="", repr=False)
    chat_records_url: str = OFFICIAL_URL
    data_dir: Path = PROJECT_ROOT / "data"
    api_allowed_hosts: tuple[str, ...] = ("localhost", "127.0.0.1", "testserver")
    uid: str = "1383603926"
    start: datetime = field(default_factory=lambda: bound("2026-11-08"))
    end: datetime = field(default_factory=lambda: bound("2026-11-12", end=True))
    interval: timedelta = timedelta(minutes=15)
    context_lookback: timedelta = timedelta(days=3)
    match_window: timedelta = timedelta(days=7)
    closed_grace: timedelta = timedelta(days=7)
    ingest_lookback: timedelta = timedelta(days=2)
    history_retrieval: HistoryRetrievalSettings = field(default_factory=HistoryRetrievalSettings)
    history_context: HistoryContextSettings = field(default_factory=HistoryContextSettings)
    provider: str = "stub"
    priority_preview: bool = False
    priority_calendar: ServiceCalendar | None = None
    llm_model: str = "gpt-6.1-sol"
    llm_max_tokens: int = 8192
    llm_timeout_seconds: int = 120
    analysis_slots: int = 6
    live_reserved: int = 1
    lease_seconds: int = 30
    fetch_ahead: int = 6
    # 待确认：合理的本地缺省，不代表官方状态/等级标准。
    default_status: str = UNKNOWN
    default_severity: str = UNKNOWN
    closed_statuses: tuple[str, ...] = ()

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "Settings":
        """默认读仓库根 .env；显式文件独立读取，环境变量仍具有最高优先级。"""
        selected = Path(env_file).expanduser() if env_file is not None else DEFAULT_ENV_FILE
        values = {**dotenv_values(selected), **os.environ}
        get = lambda key, default: values.get(key) or default
        data_dir = Path(get("DATA_DIR", "data")).expanduser()
        if not data_dir.is_absolute():
            data_dir = PROJECT_ROOT / data_dir
        result = cls(
            mysql_host=get("MYSQL_HOST", "127.0.0.1"),
            mysql_port=int(get("MYSQL_PORT", "3307")),
            mysql_database=get("MYSQL_DATABASE", "dake_aftersales"),
            mysql_user=get("MYSQL_USER", "dake"),
            mysql_password=get("MYSQL_PASSWORD", ""),
            chat_records_url=get("CHAT_RECORDS_URL", OFFICIAL_URL),
            data_dir=data_dir.resolve(),
            api_allowed_hosts=tuple(host.strip() for host in get(
                "API_ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",") if host.strip()),
            uid=get("PROCESS_UID", get("TEST_UID", "1383603926")),
            start=bound(get("PROCESS_START", get("REPLAY_START", "2026-11-08"))),
            end=bound(get("PROCESS_END", get("REPLAY_END", "2026-11-12")), end=True),
            interval=duration(get("PROCESS_INTERVAL", "15m")),
            context_lookback=duration(get("CONTEXT_LOOKBACK", "3d")),
            match_window=duration(get("MATCH_WINDOW", "7d")),
            closed_grace=duration(get("CLOSED_GRACE", "7d")),
            ingest_lookback=duration(get("INGEST_LOOKBACK", "2d")),
            history_retrieval=HistoryRetrievalSettings(**{
                name: int(get("HISTORY_" + name.upper(), str(value)))
                for name, value in asdict(HistoryRetrievalSettings()).items()
            }),
            history_context=HistoryContextSettings(**{
                name: int(get("HISTORY_CONTEXT_" + name.upper(), str(value)))
                for name, value in asdict(HistoryContextSettings()).items()
            }),
            provider=get("LLM_PROVIDER", "stub"),
            priority_calendar=ServiceCalendar.load(path) if (path := values.get("PRIORITY_SERVICE_CALENDAR")) else None,
            llm_model=get("XHub_MODEL", "gpt-6.1-sol"),
            llm_max_tokens=int(get("XHub_MAX_TOKENS", "8192")),
            llm_timeout_seconds=int(get("XHub_TIMEOUT_SECONDS", "120")),
            analysis_slots=int(get("ANALYSIS_SLOTS", "6")),
            live_reserved=int(get("LIVE_RESERVED", "1")),
            lease_seconds=int(get("LEASE_SECONDS", "30")),
            fetch_ahead=int(get("FETCH_AHEAD", "6")),
            default_status=get("DEFAULT_STATUS", UNKNOWN),
            default_severity=get("DEFAULT_SEVERITY", UNKNOWN),
            closed_statuses=tuple(s.strip() for s in get("CLOSED_STATUSES", "").split(",") if s.strip()),
        )
        result.validate()
        return result

    def validate(self):
        """一期只访问指定官方源和本地数据库，真实数据限制在项目 data/ 内。"""
        if self.mysql_host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("当前开发仅允许连接本机 MySQL")
        if not 1 <= self.mysql_port <= 65535 or self.start >= self.end:
            raise ValueError("数据库端口或处理时间范围不合法")
        if self.chat_records_url != OFFICIAL_URL:
            raise ValueError("当前只支持官方 chatRecords 地址")
        if not self.api_allowed_hosts or any("*" in host for host in self.api_allowed_hosts):
            raise ValueError("API_ALLOWED_HOSTS 须填写明确的本机地址或域名")
        if not self.data_dir.resolve().is_relative_to((PROJECT_ROOT / "data").resolve()):
            raise ValueError("DATA_DIR 必须位于本项目 data/ 目录下")
        if self.provider not in {"stub", "xhub"}:
            raise ValueError("LLM_PROVIDER 仅支持 stub 或 xhub")
        if not self.llm_model or not 1 <= self.llm_max_tokens <= 8192 or not 1 <= self.llm_timeout_seconds <= 300:
            raise ValueError("模型名称、输出上限或超时配置无效")
        if self.analysis_slots < 1 or not 0 <= self.live_reserved <= self.analysis_slots or self.fetch_ahead < 1:
            raise ValueError("分析并发额度不合法")
        if not 1 <= self.lease_seconds <= 3600:
            raise ValueError("执行租约时长不合法")

    def store_kwargs(self) -> dict:
        """将数据库配置交给存储组件；调用方不得记录返回的凭据。"""
        return dict(host=self.mysql_host, port=self.mysql_port, user=self.mysql_user,
                    password=self.mysql_password, database=self.mysql_database)
