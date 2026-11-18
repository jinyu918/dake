// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Alert Previews GET /api/v1/alert-previews */
export async function listAlertPreviews(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listAlertPreviewsParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.AlertPreviewList>("/api/v1/alert-previews", {
    method: "GET",
    params: {
      // limit has a default value: 100
      limit: "100",
      ...params,
    },
    ...(options || {}),
  });
}

/** List Events 按客户、分析流及可选类型、状态、优先级筛选当前事件。 GET /api/v1/events */
export async function listEvents(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listEventsParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.EventListResponse>("/api/v1/events", {
    method: "GET",
    params: {
      ...params,
    },
    ...(options || {}),
  });
}

/** Get Event 取得事件及证据历史；指定错误客户或分析流时不返回事件。 GET /api/v1/events/${param0} */
export async function getEvent(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getEventParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.EventResponse>(`/api/v1/events/${param0}`, {
    method: "GET",
    params: {
      ...queryParams,
    },
    ...(options || {}),
  });
}

/** Get Event Changes 必须明确客户和分析范围，读取已保存批次与版本；不运行处理。 GET /api/v1/events/${param0}/changes */
export async function getEventChanges(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getEventChangesParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.EventChangesResponse>(`/api/v1/events/${param0}/changes`, {
    method: "GET",
    params: {
      // include_details has a default value: true
      include_details: "true",
      ...queryParams,
    },
    ...(options || {}),
  });
}

/** Get Event Context 按需读取事件所在群聊前后邻近消息，辅助核对前置参数、日志或配图。 GET /api/v1/events/${param0}/context */
export async function getEventContext(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getEventContextParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.EventContextResponse>(`/api/v1/events/${param0}/context`, {
    method: "GET",
    params: {
      // padding_minutes has a default value: 15
      padding_minutes: "15",
      // limit has a default value: 80
      limit: "80",
      ...queryParams,
    },
    ...(options || {}),
  });
}

/** Submit Manual Decision 对分类、状态、优先级或跟进人提交原子人工裁定与加解锁。 POST /api/v1/events/${param0}/manual-decisions */
export async function submitManualDecision(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.submitManualDecisionParams,
  body: API.ManualDecisionRequest,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.ManualDecisionResponse>(
    `/api/v1/events/${param0}/manual-decisions`,
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      params: { ...queryParams },
      data: body,
      ...(options || {}),
    }
  );
}

/** Get Manual Decision 按操作编号核实人工裁定是否已落库，用于网络或提交超时后的安全查询。 GET /api/v1/events/${param0}/manual-decisions/${param1} */
export async function getManualDecision(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getManualDecisionParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, operation_id: param1, ...queryParams } = params;
  return request<API.ManualDecisionResponse>(
    `/api/v1/events/${param0}/manual-decisions/${param1}`,
    {
      method: "GET",
      params: {
        ...queryParams,
      },
      ...(options || {}),
    }
  );
}
