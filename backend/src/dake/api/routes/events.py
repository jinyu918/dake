"""事件查询、公开配置及逐字段人工裁定接口。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from ..dependencies import AppServices, services
from ..schemas import ConfigResponse, EventChangesResponse, EventListResponse, EventResponse
from ...domain.fields import FieldDecisionRequest, FieldOperation
from ...services.field_decisions import FieldDecisionService, FieldDecisionConflict
from ...domain.identity import CorrectionNotCommitted, IdentityCorrectionOperation, SameRoundCorrectionRequest
from ...services.identity_corrections import IdentityCorrectionService

router = APIRouter(prefix="/api/v1")
Deps = Annotated[AppServices, Depends(services)]


@router.post("/events/{event_id}/same-round-corrections", response_model=IdentityCorrectionOperation,
             operation_id="correct_same_round", tags=["events"])
def correct_same_round(deps: Deps, body: SameRoundCorrectionRequest,
                       event_id: str = Path(min_length=1, max_length=64)):
    try:
        operation = IdentityCorrectionService(deps.queries).submit(event_id, body)
    except FieldDecisionConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    if operation is None:
        raise HTTPException(status_code=404, detail="该客户范围内未找到两侧事项")
    return operation


@router.get("/events/{event_id}/same-round-corrections/{operation_id}", response_model=IdentityCorrectionOperation | CorrectionNotCommitted,
            operation_id="get_same_round_correction", tags=["events"])
def get_same_round_correction(deps: Deps, event_id: str = Path(min_length=1, max_length=64),
        operation_id: str = Path(min_length=1, max_length=128),
        uid: str = Query(min_length=1, max_length=128), stream: str = Query(min_length=1, max_length=128)):
    operation = IdentityCorrectionService(deps.queries).operation(event_id, operation_id, uid, stream)
    if operation is None:
        raise HTTPException(status_code=404, detail="该客户范围内未找到事项")
    return operation


@router.post("/events/{event_id}/field-decisions", response_model=EventResponse,
             operation_id="submit_field_decision", tags=["events"])
def submit_field_decision(deps: Deps, body: FieldDecisionRequest,
                          event_id: str = Path(min_length=1, max_length=64)):
    try:
        event = FieldDecisionService(deps.queries).submit(event_id, body)
    except FieldDecisionConflict as error:
        raise HTTPException(status_code=409, detail=f"{error}；本次未追加裁定") from None
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None
    if event is None:
        raise HTTPException(status_code=404, detail="该客户下未找到事项")
    return event


@router.get("/events/{event_id}/field-decisions/{operation_id}", response_model=FieldOperation,
            operation_id="get_field_decision", tags=["events"])
def get_field_decision(deps: Deps, event_id: str = Path(min_length=1, max_length=64),
                       operation_id: str = Path(min_length=1, max_length=128),
                       uid: str = Query(min_length=1, max_length=128), stream: str = Query(min_length=1, max_length=128)):
    result = FieldDecisionService(deps.queries).operation(event_id, operation_id, uid, stream)
    if result is None:
        raise HTTPException(status_code=404, detail="没有找到已提交的裁定；请核对后再操作")
    return result


@router.get("/config", response_model=ConfigResponse, operation_id="get_config", tags=["config"])
def get_config(deps: Deps):
    """读取界面缺省与客户统计，不返回敏感运行配置。"""
    return deps.queries.config()


@router.get("/events", response_model=EventListResponse, operation_id="list_events", tags=["events"])
def list_events(deps: Deps, uid: str | None = Query(default=None, max_length=128),
                stream: str | None = Query(default=None, max_length=128),
                event_type: str | None = Query(default=None, alias="type", max_length=32)):
    """按客户、分析流和可选事件类型读取当前事件。"""
    return deps.queries.events(uid, stream, event_type)


@router.get("/events/{event_id}", response_model=EventResponse, operation_id="get_event", tags=["events"])
def get_event(deps: Deps, event_id: str = Path(min_length=1, max_length=64),
              uid: str | None = Query(default=None, max_length=128),
              stream: str | None = Query(default=None, max_length=128)):
    """取得事件及证据历史；指定错误客户或分析流时不返回事件。"""
    event = deps.queries.event(event_id, uid, stream)
    if event is None:
        raise HTTPException(status_code=404, detail="该客户下未找到事件")
    return event


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
