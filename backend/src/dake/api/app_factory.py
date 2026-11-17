"""无模块级装配副作用的 HTTP 工厂；配置必须显式注入。"""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .dependencies import AppServices
from .routes.events import router as events_router
from .routes.runs import router as runs_router
from .routes.customers import router as customers_router
from .routes.tasks import router as tasks_router
from .routes.sender_rules import router as sender_rules_router
from ..services.sender_rules import SenderRuleService
from ..repositories.tasks import TaskRepository
from ..services.tasks import TaskService
from ..repositories.customers import CustomerRepository
from .schemas import ErrorResponse, HealthResponse
from ..config import Settings
from ..jobs.runner import RunRunner
from ..repositories.mysql import MySQLStore, StorageError
from ..repositories.runs import RunConflict, RunRepository
from ..services.queries import QueryService


def create_app(settings: Settings, *, store_factory=MySQLStore, run_repository=None,
               runner=None, query_service=None, customer_repository=None,
               task_repository=None, sender_rule_service=None, start_worker=True) -> FastAPI:
    """创建可测试的应用；仅导出 OpenAPI 时不会连接数据库或启动后台任务。"""
    runs = run_repository or RunRepository(settings, store_factory)
    worker = runner or RunRunner(settings, runs)
    queries = query_service or QueryService(settings, store_factory)
    customers = customer_repository or CustomerRepository(settings, store_factory)
    tasks = task_repository or TaskRepository(settings, store_factory)
    task_service = TaskService(settings, tasks, runs, customers)
    if isinstance(worker, RunRunner):
        worker.coordinator = task_service
        from ..repositories.attempts import AttemptRepository
        from ..repositories.pipeline import PipelineRepository
        worker.pipeline = PipelineRepository(settings, store_factory)
        worker.attempts = AttemptRepository(settings, store_factory)

    @asynccontextmanager
    async def lifespan(application):
        runs.init_schema()
        if task_repository is not None or isinstance(runs, RunRepository):
            tasks.init_schema()
        if start_worker:
            worker.start()
        try:
            yield
        finally:
            if start_worker:
                worker.stop()

    application = FastAPI(title="大客售后事件系统", version="0.2.0", lifespan=lifespan,
                          responses={400: {"model": ErrorResponse}, 409: {"model": ErrorResponse},
                                     422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}})
    application.state.services = AppServices(settings, queries, runs, worker, customers, task_service,
        sender_rule_service or SenderRuleService(settings))
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.api_allowed_hosts))

    @application.middleware("http")
    async def local_http_boundary(request: Request, call_next):
        # JSON 与同源检查共同阻止其他网页借本地服务触发任务；不开放跨源写权限。
        if request.method == "POST":
            content_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
            if content_type != "application/json":
                return JSONResponse({"detail": "提交任务需要 JSON 请求"}, status_code=415,
                                    headers={"Cache-Control": "no-store"})
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    expected = urlsplit(str(request.base_url))
                    same_origin = ((parsed.scheme, parsed.netloc) == (expected.scheme, expected.netloc)
                                   and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
                except ValueError:
                    same_origin = False
                if not same_origin:
                    return JSONResponse({"detail": "只允许当前页面提交任务"}, status_code=403,
                                        headers={"Cache-Control": "no-store"})
        try:
            response = await call_next(request)
        except Exception:
            # 不把第三方异常交给默认 traceback 输出，避免错误夹带内部正文或凭据。
            response = JSONResponse({"detail": "服务暂时不可用，请检查本地服务状态后重试"}, status_code=503)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        return JSONResponse({"detail": "请求字段或格式不正确，请检查客户、时间范围与周期"}, status_code=422)

    @application.exception_handler(RunConflict)
    async def conflict(request, error):
        return JSONResponse({"detail": "该客户已有未结束任务，或等待队列已满"}, status_code=409)

    @application.exception_handler(ValueError)
    async def invalid_value(request, error):
        return JSONResponse({"detail": "请求范围或参数无效，请检查模式、客户、日期与周期"}, status_code=400)

    @application.exception_handler(StorageError)
    async def storage_failure(request, error):
        return JSONResponse({"detail": "本地数据库暂时不可用，请检查 MySQL 后重试"}, status_code=503)

    @application.get("/health", response_model=HealthResponse, operation_id="health", tags=["system"])
    def health():
        """确认服务可访问本地数据库，不回传主机、账户或密码。"""
        with runs.connection() as store:
            store._execute("SELECT 1")
        return {"status": "ok"}

    application.include_router(events_router)
    application.include_router(runs_router)
    application.include_router(customers_router)
    application.include_router(tasks_router)
    application.include_router(sender_rules_router)

    return application
