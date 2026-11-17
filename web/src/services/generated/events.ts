// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Events 按客户、分析流和可选事件类型读取当前事件。 GET /api/v1/events */
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

/** Submit Field Decision POST /api/v1/events/${param0}/field-decisions */
export async function submitFieldDecision(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.submitFieldDecisionParams,
  body: API.FieldDecisionRequest,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.EventResponse>(
    `/api/v1/events/${param0}/field-decisions`,
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

/** Get Field Decision GET /api/v1/events/${param0}/field-decisions/${param1} */
export async function getFieldDecision(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getFieldDecisionParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, operation_id: param1, ...queryParams } = params;
  return request<API.FieldOperation>(
    `/api/v1/events/${param0}/field-decisions/${param1}`,
    {
      method: "GET",
      params: {
        ...queryParams,
      },
      ...(options || {}),
    }
  );
}

/** Correct Same Round POST /api/v1/events/${param0}/same-round-corrections */
export async function correctSameRound(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.correctSameRoundParams,
  body: API.SameRoundCorrectionRequest,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, ...queryParams } = params;
  return request<API.IdentityCorrectionOperation>(
    `/api/v1/events/${param0}/same-round-corrections`,
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

/** Get Same Round Correction GET /api/v1/events/${param0}/same-round-corrections/${param1} */
export async function getSameRoundCorrection(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getSameRoundCorrectionParams,
  options?: { signal?: AbortSignal }
) {
  const { event_id: param0, operation_id: param1, ...queryParams } = params;
  return request<API.IdentityCorrectionOperation | API.CorrectionNotCommitted>(
    `/api/v1/events/${param0}/same-round-corrections/${param1}`,
    {
      method: "GET",
      params: {
        ...queryParams,
      },
      ...(options || {}),
    }
  );
}
