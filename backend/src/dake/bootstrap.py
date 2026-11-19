"""集中装配存储、数据源和分析器，让 CLI、API 与后台任务共用依赖创建方式。"""

from contextlib import contextmanager
from typing import Iterator

from .analysis.providers import StubProvider
from .analysis.providers.xhub import XHubConfig, XHubProvider
from .config import Settings
from .ingestion.adapter import OfficialChatSource
from .ingestion.client import ChatRecordsClient
from .repositories.mysql import MySQLStore
from .repositories.attempts import AttemptRepository
from .services.attribute_schemas import AttributeSchemaService
from .services.processing import IncrementalService


@contextmanager
def build_service(settings: Settings, *, offline: bool = False) -> Iterator[IncrementalService]:
    """每次执行拥有独立连接，结束时释放存储和模型客户端。"""
    settings.validate()
    store = MySQLStore(**settings.store_kwargs())
    provider = None
    try:
        source = OfficialChatSource(ChatRecordsClient(settings.chat_records_url), settings.data_dir, offline or settings.priority_preview)
        provider = (XHubProvider(XHubConfig.load(settings.llm_model, max_tokens=settings.llm_max_tokens,
                                                timeout_seconds=settings.llm_timeout_seconds), settings)
                    if settings.provider == "xhub" else StubProvider())
        yield IncrementalService(store, source, provider, settings,
                                 attempts=AttemptRepository(settings),
                                 analytics_service=AttributeSchemaService(settings, store_factory=MySQLStore))
    finally:
        if provider is not None and hasattr(provider, "close"):
            provider.close()
        store.close()
