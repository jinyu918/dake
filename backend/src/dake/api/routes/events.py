"""事件查询、公开配置及核心管理字段人工纠偏接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..dependencies import AppServices, services
from ..schemas import (
    AlertPreviewList,
    ConfigResponse,
    EventChangesResponse,
    EventContextResponse,
    EventListResponse,
    EventResponse,
    ManualDecisionRequest,
    ManualDecisionResponse,
)

router = APIRouter(prefix="/api/v1")
Deps = Annotated[AppServices, Depends(services)]


@router.get("/config", response_model=ConfigResponse, operation_id="get_config", tags=["config"])
def get_config(deps: Deps):
    """读取界面缺省与客户统计，不返回敏感运行配置。"""
    return deps.queries.config()


@router.get("/alert-previews", response_model=AlertPreviewList, operation_id="list_alert_previews", tags=["events"])
def list_alert_previews(deps: Deps, uid: str | None = Query(default=None, max_length=128),
                        limit: int = Query(default=100, ge=1, le=200)):
    return deps.queries.alert_previews(uid, limit)


@router.get("/events", response_model=EventListResponse, operation_id="list_events", tags=["events"])
def list_events(
    deps: Deps,
    uid: str | None = Query(default=None, max_length=128),
    stream: str | None = Query(default=None, max_length=128),
    event_type: str | None = Query(default=None, alias="type", max_length=32),
    status: str | None = Query(default=None, max_length=32),
    priority: str | None = Query(default=None, max_length=32),
):
    """按客户、分析流及可选类型、状态、优先级筛选当前事件。"""
    return deps.queries.events(uid, stream, event_type, status=status, priority=priority)


@router.get("/events/{event_id}", response_model=EventResponse, operation_id="get_event", tags=["events"])
def get_event(deps: Deps, event_id: str = Path(min_length=1, max_length=64),
              uid: str | None = Query(default=None, max_length=128),
              stream: str | None = Query(default=None, max_length=128)):
    """取得事件及证据历史；指定错误客户或分析流时不返回事件。"""
    event = deps.queries.event(event_id, uid, stream)
    if event is None:
        raise HTTPException(status_code=404, detail="该客户下未找到事件")
    return event


@router.get(
    "/events/{event_id}/context",
    response_model=EventContextResponse,
    operation_id="get_event_context",
    tags=["events"],
)
def get_event_context(
    deps: Deps,
    event_id: str = Path(min_length=1, max_length=64),
    uid: str | None = Query(default=None, max_length=128),
    stream: str | None = Query(default=None, max_length=128),
    padding_minutes: int = Query(default=15, ge=1, le=120),
    limit: int = Query(default=80, ge=1, le=200),
):
    """按需读取事件所在群聊前后邻近消息，辅助核对前置参数、日志或配图。"""
    result = deps.queries.event_context(
        event_id, uid=uid, stream=stream, padding_minutes=padding_minutes, limit=limit
    )
    if result is None:
        raise HTTPException(status_code=404, detail="该客户下未找到事件")
    return result


@router.get("/events/{event_id}/changes", response_model=EventChangesResponse, operation_id="get_event_changes", tags=["events"])
def get_event_changes(deps: Deps, event_id: str = Path(min_length=1, max_length=64),
                      uid: str = Query(min_length=1, max_length=128),
                      stream: str = Query(min_length=1, max_length=128),
                      offset: int = Query(default=0, ge=0),
                      execution_offset: int = Query(default=0, ge=0),
                      include_details: bool = Query(default=True)):
    """必须明确客户和分析范围，读取已保存批次与版本；不运行处理。"""
    result = deps.queries.event_changes(event_id, uid, stream, deps.runs, offset, execution_offset,
                                       include_details=include_details)
    if result is None:
        raise HTTPException(status_code=404, detail="该客户下未找到事件")
    return result


@router.post(
    "/events/{event_id}/manual-decisions",
    response_model=ManualDecisionResponse,
    operation_id="submit_manual_decision",
    tags=["events"],
)
def submit_manual_decision(
    body: ManualDecisionRequest,
    deps: Deps,
    event_id: str = Path(min_length=1, max_length=64),
):
    """对分类、状态、优先级或跟进人提交原子人工裁定与加解锁。"""
    if deps.manual_decisions is None:
        raise HTTPException(status_code=503, detail="人工裁定服务未就绪")
    return deps.manual_decisions.submit(event_id, body)


@router.get(
    "/events/{event_id}/manual-decisions/{operation_id}",
    response_model=ManualDecisionResponse,
    operation_id="get_manual_decision",
    tags=["events"],
)
def get_manual_decision(
    deps: Deps,
    event_id: str = Path(min_length=1, max_length=64),
    operation_id: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    uid: str | None = Query(default=None, max_length=128),
    stream: str | None = Query(default=None, max_length=128),
):
    """按操作编号核实人工裁定是否已落库，用于网络或提交超时后的安全查询。"""
    if deps.manual_decisions is None:
        raise HTTPException(status_code=503, detail="人工裁定服务未就绪")
    return deps.manual_decisions.operation(event_id, operation_id, uid=uid, stream=stream)
