// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Runs 任务列表提供状态与计数，完整周期详情通过单条任务接口读取。 GET /api/v1/runs */
export async function listRuns(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listRunsParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.RunListResponse>("/api/v1/runs", {
    method: "GET",
    params: {
      ...params,
    },
    ...(options || {}),
  });
}

/** Get Run 返回持久化进度、失败原因及已完成周期，刷新页面不会丢失。 GET /api/v1/runs/${param0} */
export async function getRun(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getRunParams,
  options?: { signal?: AbortSignal }
) {
  const { run_id: param0, ...queryParams } = params;
  return request<API.RunResponse>(`/api/v1/runs/${param0}`, {
    method: "GET",
    params: {
      ...queryParams,
    },
    ...(options || {}),
  });
}
