// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Sender Rules GET /api/v1/sender-rules */
export async function listSenderRules(options?: { signal?: AbortSignal }) {
  return request<API.SenderRuleList>("/api/v1/sender-rules", {
    method: "GET",
    ...(options || {}),
  });
}

/** Save Sender Rule POST /api/v1/sender-rules */
export async function saveSenderRule(
  body: API.SenderRuleSave,
  options?: { signal?: AbortSignal }
) {
  return request<API.SenderRuleList>("/api/v1/sender-rules", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    data: body,
    ...(options || {}),
  });
}

/** Search Sender Candidates GET /api/v1/sender-rules/candidates */
export async function searchSenderCandidates(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.searchSenderCandidatesParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.SenderCandidateList>("/api/v1/sender-rules/candidates", {
    method: "GET",
    params: {
      // limit has a default value: 25
      limit: "25",
      ...params,
    },
    ...(options || {}),
  });
}
