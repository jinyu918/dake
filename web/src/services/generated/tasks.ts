// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Tasks GET /api/v1/tasks */
export async function listTasks(options?: { signal?: AbortSignal }) {
  return request<API.TaskList>("/api/v1/tasks", {
    method: "GET",
    ...(options || {}),
  });
}

/** Create Task POST /api/v1/tasks */
export async function createTask(
  body: API.TaskConfig,
  options?: { signal?: AbortSignal }
) {
  return request<API.TaskResponse>("/api/v1/tasks", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    data: body,
    ...(options || {}),
  });
}

/** Task Action POST /api/v1/tasks/${param0}/actions */
export async function taskAction(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.taskActionParams,
  body: API.TaskAction,
  options?: { signal?: AbortSignal }
) {
  const { task_id: param0, ...queryParams } = params;
  return request<API.TaskResponse>(`/api/v1/tasks/${param0}/actions`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    params: { ...queryParams },
    data: body,
    ...(options || {}),
  });
}

/** Update Task POST /api/v1/tasks/${param0}/config */
export async function updateTask(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.updateTaskParams,
  body: API.TaskUpdate,
  options?: { signal?: AbortSignal }
) {
  const { task_id: param0, ...queryParams } = params;
  return request<API.TaskResponse>(`/api/v1/tasks/${param0}/config`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    params: { ...queryParams },
    data: body,
    ...(options || {}),
  });
}

/** List Task Executions GET /api/v1/tasks/${param0}/executions */
export async function listTaskExecutions(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listTaskExecutionsParams,
  options?: { signal?: AbortSignal }
) {
  const { task_id: param0, ...queryParams } = params;
  return request<API.TaskExecutionList>(`/api/v1/tasks/${param0}/executions`, {
    method: "GET",
    params: { ...queryParams },
    ...(options || {}),
  });
}

/** Get Task Execution GET /api/v1/tasks/executions/${param0} */
export async function getTaskExecution(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getTaskExecutionParams,
  options?: { signal?: AbortSignal }
) {
  const { execution_id: param0, ...queryParams } = params;
  return request<API.TaskExecutionResponse>(
    `/api/v1/tasks/executions/${param0}`,
    {
      method: "GET",
      params: { ...queryParams },
      ...(options || {}),
    }
  );
}

/** Preview Task POST /api/v1/tasks/preview */
export async function previewTask(
  body: API.TaskConfig,
  options?: { signal?: AbortSignal }
) {
  return request<API.TaskPreview>("/api/v1/tasks/preview", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    data: body,
    ...(options || {}),
  });
}
