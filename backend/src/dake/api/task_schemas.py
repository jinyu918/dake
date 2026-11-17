"""任务中心的输入输出契约；不返回编排内部快照或聊天正文。"""

from typing import Literal
from pydantic import Field

from .schemas import APIModel
from ..domain.tasks import TaskConfig


class TaskUpdate(APIModel):
    config: TaskConfig
    version: int = Field(ge=1)


class TaskAction(APIModel):
    action: Literal["run", "pause", "resume"]


class TaskExecutionHead(APIModel):
    execution_id: str
    task_id: str
    status: Literal["queued", "running", "succeeded", "partial", "failed"]
    stage: Literal["discovery", "analysis", "completed"]
    created_at: str
    finished_at: str | None


class TaskResponse(APIModel):
    task_id: str
    config: TaskConfig
    enabled: bool
    version: int
    cursor_at: str | None
    next_run_at: str | None
    created_at: str
    updated_at: str
    latest: TaskExecutionHead | None = None


class TaskList(APIModel):
    items: list[TaskResponse]
    total: int


class TaskExecutionList(APIModel):
    items: list[TaskExecutionHead]
    total: int


class TaskCustomerProgress(APIModel):
    uid: str
    level: str | None
    status: str
    reason: str | None
    run_id: str | None
    stream: str
    processed: int
    created: int
    updated: int
    completed_windows: int
    total_windows: int


class TaskExecutionResponse(TaskExecutionHead):
    config: TaskConfig
    start: str
    end: str
    error: str | None
    source_messages: int
    source_errors: int
    processed: int
    created_events: int
    updated_events: int
    sync_completed: int
    sync_total: int
    customers: list[TaskCustomerProgress]


class TaskPreviewCustomer(APIModel):
    uid: str
    level: str | None
    tam: str | None


class TaskPreview(APIModel):
    items: list[TaskPreviewCustomer]
    total: int
    notice: str
