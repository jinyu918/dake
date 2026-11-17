"""任务中心 HTTP 边界；配置与动作由业务服务校验和执行。"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import AppServices, services
from ..task_schemas import (TaskAction, TaskUpdate, TaskResponse, TaskList, TaskPreview,
                            TaskExecutionList, TaskExecutionResponse)
from ...domain.tasks import TaskConfig

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])
Deps = Annotated[AppServices, Depends(services)]


def response(service, task):
    """列表只附最近执行的头信息，周期正文由详情按需读取。"""
    executions = service.tasks.executions(task["task_id"])
    return dict(task, latest=executions[0] if executions else None)


@router.get("", response_model=TaskList, operation_id="list_tasks")
def list_tasks(deps: Deps):
    items = [response(deps.tasks, task) for task in deps.tasks.tasks.list()]
    return dict(items=items, total=len(items))


@router.post("", response_model=TaskResponse, status_code=201, operation_id="create_task")
def create_task(body: TaskConfig, deps: Deps):
    return response(deps.tasks, deps.tasks.create(body.model_dump()))


@router.post("/preview", response_model=TaskPreview, operation_id="preview_task")
def preview_task(body: TaskConfig, deps: Deps):
    return deps.tasks.preview(body.model_dump())


@router.get("/executions/{execution_id}", response_model=TaskExecutionResponse, operation_id="get_task_execution")
def get_task_execution(execution_id: UUID, deps: Deps):
    execution = deps.tasks.tasks.execution(str(execution_id))
    if execution is None:
        raise HTTPException(404, "执行记录不存在")
    return deps.tasks.progress(execution)


@router.post("/{task_id}/config", response_model=TaskResponse, operation_id="update_task")
def update_task(task_id: UUID, body: TaskUpdate, deps: Deps):
    try:
        task = deps.tasks.update(str(task_id), body.config.model_dump(), body.version)
    except ValueError as error:
        from ...repositories.runs import RunConflict
        if isinstance(error, RunConflict):
            raise
        raise HTTPException(400, str(error)) from None
    return response(deps.tasks, task)


@router.post("/{task_id}/actions", response_model=TaskResponse, operation_id="task_action")
def task_action(task_id: UUID, body: TaskAction, deps: Deps):
    try:
        task = deps.tasks.action(str(task_id), body.action)
    except ValueError as error:
        from ...repositories.runs import RunConflict
        if isinstance(error, RunConflict):
            raise
        raise HTTPException(400, str(error)) from None
    deps.runner.notify()
    return response(deps.tasks, task)


@router.get("/{task_id}/executions", response_model=TaskExecutionList, operation_id="list_task_executions")
def list_task_executions(task_id: UUID, deps: Deps):
    items = deps.tasks.tasks.executions(str(task_id))
    return dict(items=items, total=len(items))
