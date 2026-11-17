"""客户查询及同步 HTTP 边界，业务流程由服务和现有后台执行器处理。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import AppServices, services
from ..schemas import CustomerProfileList, CustomerGroupList, CustomerSyncRequest, CustomerSyncResponse, CustomerSyncList
from ...ingestion.client import validate_uid
from ...domain.customer_policy import CustomerFilter
from ...repositories.customers import SYNC_MODE, SYNC_UID
from ...services.customers import prepare_sync

router = APIRouter(prefix="/api/v1/customers", tags=["customers"])
Deps = Annotated[AppServices, Depends(services)]


@router.get("", response_model=CustomerProfileList, operation_id="list_customer_profiles")
def list_customer_profiles(deps: Deps, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100),
                           keyword: str = Query("", max_length=128), segment: CustomerFilter = "all"):
    """读取已保存客户，不在查询时刷新外部资料。"""
    return deps.customers.list_customers(offset, limit, keyword, segment=segment)


@router.get("/groups", response_model=CustomerGroupList, operation_id="list_customer_groups")
def list_customer_groups(deps: Deps, uid: str | None = Query(None, max_length=128), review: bool = False,
                         offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=100)):
    """查看关联群或所有待确认归属。"""
    if uid is not None:
        validate_uid(uid)
    return deps.customers.list_groups(uid, review, offset, limit)


@router.post("/syncs", response_model=CustomerSyncResponse, status_code=202, operation_id="create_customer_sync")
def create_customer_sync(body: CustomerSyncRequest, deps: Deps):
    """复用运行队列，重复提交在数据库锁内拒绝。"""
    request, total = prepare_sync(body.model_dump(), deps.settings.interval)
    deps.customers.list_customers(limit=1)
    run = deps.runs.create(request, total)
    deps.runner.notify()
    return run


@router.get("/syncs", response_model=CustomerSyncList, operation_id="list_customer_syncs")
def list_customer_syncs(deps: Deps):
    """刷新后继续查看已保存的同步记录。"""
    return deps.runs.list(SYNC_UID, SYNC_MODE)


@router.get("/syncs/{run_id}", response_model=CustomerSyncResponse, operation_id="get_customer_sync")
def get_customer_sync(run_id: UUID, deps: Deps):
    """只读取客户同步任务，避免混入分析运行。"""
    run = deps.runs.get(str(run_id), uid=SYNC_UID)
    if run is None or run["mode"] != SYNC_MODE:
        raise HTTPException(404, "未找到客户同步记录")
    return run
