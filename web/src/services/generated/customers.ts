// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Customer Profiles 读取已保存客户，不在查询时刷新外部资料。 GET /api/v1/customers */
export async function listCustomerProfiles(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listCustomerProfilesParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.CustomerProfileList>("/api/v1/customers", {
    method: "GET",
    params: {
      // limit has a default value: 50
      limit: "50",

      // segment has a default value: all
      segment: "all",
      ...params,
    },
    ...(options || {}),
  });
}

/** List Customer Groups 查看关联群或所有待确认归属。 GET /api/v1/customers/groups */
export async function listCustomerGroups(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.listCustomerGroupsParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.CustomerGroupList>("/api/v1/customers/groups", {
    method: "GET",
    params: {
      // limit has a default value: 50
      limit: "50",
      ...params,
    },
    ...(options || {}),
  });
}

/** List Customer Syncs 刷新后继续查看已保存的同步记录。 GET /api/v1/customers/syncs */
export async function listCustomerSyncs(options?: { signal?: AbortSignal }) {
  return request<API.CustomerSyncList>("/api/v1/customers/syncs", {
    method: "GET",
    ...(options || {}),
  });
}

/** Create Customer Sync 复用运行队列，重复提交在数据库锁内拒绝。 POST /api/v1/customers/syncs */
export async function createCustomerSync(
  body: API.CustomerSyncRequest,
  options?: { signal?: AbortSignal }
) {
  return request<any>("/api/v1/customers/syncs", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    data: body,
    ...(options || {}),
  });
}

/** Get Customer Sync 只读取客户同步任务，避免混入分析运行。 GET /api/v1/customers/syncs/${param0} */
export async function getCustomerSync(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getCustomerSyncParams,
  options?: { signal?: AbortSignal }
) {
  const { run_id: param0, ...queryParams } = params;
  return request<API.CustomerSyncResponse>(
    `/api/v1/customers/syncs/${param0}`,
    {
      method: "GET",
      params: { ...queryParams },
      ...(options || {}),
    }
  );
}
