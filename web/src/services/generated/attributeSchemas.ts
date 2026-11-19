// @ts-ignore
/* eslint-disable */
import { request } from "@/services/request";

/** List Attribute Schemas GET /api/v1/attribute-schemas */
export async function listAttributeSchemas(options?: { signal?: AbortSignal }) {
  return request<API.AttributeSchemaRegistryResponse>(
    "/api/v1/attribute-schemas",
    {
      method: "GET",
      ...(options || {}),
    }
  );
}

/** Get Product Analytics GET /api/v1/attribute-schemas/analytics */
export async function getProductAnalytics(
  // 叠加生成的Param类型 (非body参数swagger默认没有生成对象)
  params: API.getProductAnalyticsParams,
  options?: { signal?: AbortSignal }
) {
  return request<API.ProductAnalyticsResponse>(
    "/api/v1/attribute-schemas/analytics",
    {
      method: "GET",
      params: {
        ...params,
      },
      ...(options || {}),
    }
  );
}

/** Backfill Attribute Schemas POST /api/v1/attribute-schemas/backfill */
export async function backfillAttributeSchemas(
  body: API.AttributeBackfillRequest,
  options?: { signal?: AbortSignal }
) {
  return request<API.AttributeBackfillResponse>(
    "/api/v1/attribute-schemas/backfill",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      data: body,
      ...(options || {}),
    }
  );
}

/** Save Attribute Field POST /api/v1/attribute-schemas/fields */
export async function saveAttributeField(
  body: API.AttributeFieldSaveRequest,
  options?: { signal?: AbortSignal }
) {
  return request<API.AttributeSchemaRegistryResponse>(
    "/api/v1/attribute-schemas/fields",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      data: body,
      ...(options || {}),
    }
  );
}

/** Delete Attribute Field POST /api/v1/attribute-schemas/fields/delete */
export async function deleteAttributeField(
  body: API.AttributeFieldDeleteRequest,
  options?: { signal?: AbortSignal }
) {
  return request<API.AttributeSchemaRegistryResponse>(
    "/api/v1/attribute-schemas/fields/delete",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      data: body,
      ...(options || {}),
    }
  );
}

/** Save Product Schema POST /api/v1/attribute-schemas/products */
export async function saveProductSchema(
  body: API.ProductSchemaSaveRequest,
  options?: { signal?: AbortSignal }
) {
  return request<API.AttributeSchemaRegistryResponse>(
    "/api/v1/attribute-schemas/products",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      data: body,
      ...(options || {}),
    }
  );
}

/** Delete Product Schema POST /api/v1/attribute-schemas/products/delete */
export async function deleteProductSchema(
  body: API.ProductSchemaDeleteRequest,
  options?: { signal?: AbortSignal }
) {
  return request<API.AttributeSchemaRegistryResponse>(
    "/api/v1/attribute-schemas/products/delete",
    {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      data: body,
      ...(options || {}),
    }
  );
}
