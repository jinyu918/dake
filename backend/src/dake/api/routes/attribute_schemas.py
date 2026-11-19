"""可配置产品目录、结构化字段管理、存量 AI 补齐与 MongoDB 聚合查询 HTTP 路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from ...repositories.mongo_analytics import AnalyticsStorageError, SchemaConflictError
from ..attribute_schema_schemas import (
    AttributeBackfillRequest,
    AttributeBackfillResponse,
    AttributeFieldDeleteRequest,
    AttributeFieldSaveRequest,
    AttributeSchemaRegistryResponse,
    ProductAnalyticsResponse,
    ProductSchemaDeleteRequest,
    ProductSchemaSaveRequest,
)
from ..dependencies import AppServices, services

router = APIRouter(prefix="/api/v1/attribute-schemas", tags=["attribute_schemas"])
Deps = Annotated[AppServices, Depends(services)]


@router.get("", response_model=AttributeSchemaRegistryResponse, operation_id="list_attribute_schemas")
def list_attribute_schemas(deps: Deps):
    try:
        return deps.attribute_schemas.list_registry()
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/products", response_model=AttributeSchemaRegistryResponse, operation_id="save_product_schema")
def save_product_schema(body: ProductSchemaSaveRequest, deps: Deps):
    try:
        return deps.attribute_schemas.save_product(body.product.model_dump(), body.expected_version)
    except SchemaConflictError as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/products/delete", response_model=AttributeSchemaRegistryResponse, operation_id="delete_product_schema")
def delete_product_schema(body: ProductSchemaDeleteRequest, deps: Deps):
    try:
        return deps.attribute_schemas.delete_product(body.product_key, body.expected_version)
    except SchemaConflictError as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/fields", response_model=AttributeSchemaRegistryResponse, operation_id="save_attribute_field")
def save_attribute_field(body: AttributeFieldSaveRequest, deps: Deps):
    try:
        return deps.attribute_schemas.save_field(
            body.field.model_dump(),
            body.expected_version,
            field_id=body.field_id,
        )
    except SchemaConflictError as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/fields/delete", response_model=AttributeSchemaRegistryResponse, operation_id="delete_attribute_field")
def delete_attribute_field(body: AttributeFieldDeleteRequest, deps: Deps):
    try:
        return deps.attribute_schemas.delete_field(body.field_id, body.expected_version)
    except SchemaConflictError as error:
        raise HTTPException(409, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/backfill", response_model=AttributeBackfillResponse, operation_id="backfill_attribute_schemas")
def backfill_attribute_schemas(body: AttributeBackfillRequest, deps: Deps):
    try:
        return deps.attribute_schemas.backfill(
            uid=body.uid,
            product_key=body.product_key,
            event_type=body.event_type,
            only_missing=body.only_missing,
            limit=body.limit,
            use_llm=body.use_llm,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None


@router.get("/analytics", response_model=ProductAnalyticsResponse, operation_id="get_product_analytics")
def get_product_analytics(
    deps: Deps,
    product_key: str = Query("", max_length=32),
    event_type: str = Query("", max_length=32),
    stream: str | None = Query(None, max_length=128),
):
    try:
        return deps.attribute_schemas.product_analytics(
            stream=stream,
            product_key=product_key,
            event_type=event_type,
        )
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except AnalyticsStorageError as error:
        raise HTTPException(503, str(error)) from None
