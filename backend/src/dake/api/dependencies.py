"""从应用容器取得依赖，路由不自行创建共享数据库连接。"""

from dataclasses import dataclass

from fastapi import Request

from ..config import Settings
from ..jobs.runner import RunRunner
from ..repositories.runs import RunRepository
from ..repositories.customers import CustomerRepository
from ..services.queries import QueryService
from ..services.tasks import TaskService
from ..services.sender_rules import SenderRuleService


@dataclass
class AppServices:
    """进程级服务只保存配置或工厂，每次实际查询各自建立连接。"""

    settings: Settings
    queries: QueryService
    runs: RunRepository
    runner: RunRunner
    customers: CustomerRepository | None = None
    tasks: TaskService | None = None
    sender_rules: SenderRuleService | None = None


def services(request: Request) -> AppServices:
    """为所有接口提供同一套装配，测试可注入独立的替代实现。"""
    return request.app.state.services
