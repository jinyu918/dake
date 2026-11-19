"""可配置产品目录、结构化字段定义与 MongoDB 聚合汇总 HTTP 数据合约。"""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from ..domain.attribute_schemas import AggOp, FieldDataType, ScopeType, UnitFamily
from .schemas import APIModel


class UnitFamilyOption(APIModel):
    """前端表单可选的数值标准量纲换算组。"""

    family: UnitFamily
    label: str
    default_unit: str


class ProductSchemaItem(APIModel):
    """单条七牛产品种类配置。"""

    product_key: str
    name: str
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    sort_order: int = 100
    enabled: bool = True
    builtin: bool = False


class AttributeFieldSchemaItem(APIModel):
    """单个结构化事件属性定义元数据。"""

    field_id: str
    scope_type: ScopeType
    scope_key: str
    field_key: str
    label: str
    data_type: FieldDataType
    unit: str = ""
    unit_family: UnitFamily = "none"
    enum_options: list[str] = Field(default_factory=list)
    agg_ops: list[AggOp] = Field(default_factory=list)
    applicable_types: list[str] = Field(default_factory=list)
    extraction_hint: str
    sort_order: int = 100
    enabled: bool = True
    builtin: bool = False


class AttributeSchemaRegistryResponse(APIModel):
    """配置中心完整响应。"""

    version: int
    updated_at: str
    products: list[ProductSchemaItem] = Field(default_factory=list)
    fields: list[AttributeFieldSchemaItem] = Field(default_factory=list)
    unit_families: list[UnitFamilyOption] = Field(default_factory=list)


class ProductSchemaInput(BaseModel):
    """保存产品种类输入参数。"""

    model_config = ConfigDict(extra="forbid")

    product_key: str = Field(min_length=2, max_length=32)
    name: str = Field(min_length=1, max_length=64)
    description: str = Field("", max_length=256)
    keywords: list[str] = Field(default_factory=list)
    sort_order: int = Field(100, ge=0, le=9999)
    enabled: bool = True


class ProductSchemaSaveRequest(BaseModel):
    """带乐观锁版本号的产品配置保存请求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    product: ProductSchemaInput


class AttributeFieldInput(BaseModel):
    """保存结构化属性字段输入参数。"""

    model_config = ConfigDict(extra="forbid")

    scope_type: ScopeType
    scope_key: str = Field(min_length=1, max_length=32)
    field_key: str = Field(min_length=2, max_length=48)
    label: str = Field(min_length=1, max_length=64)
    data_type: FieldDataType
    unit: str = Field("", max_length=32)
    unit_family: UnitFamily = "none"
    enum_options: list[str] = Field(default_factory=list)
    agg_ops: list[AggOp] = Field(default_factory=list)
    applicable_types: list[str] = Field(default_factory=list)
    extraction_hint: str = Field(min_length=2, max_length=512)
    sort_order: int = Field(100, ge=0, le=9999)
    enabled: bool = True


class AttributeFieldSaveRequest(BaseModel):
    """带乐观锁版本号的字段配置保存请求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    field_id: str | None = None
    field: AttributeFieldInput


class AttributeFieldDeleteRequest(BaseModel):
    """删除自定义结构化字段请求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    field_id: str = Field(min_length=3, max_length=128)


class ProductSchemaDeleteRequest(BaseModel):
    """删除自定义产品配置请求。"""

    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(ge=1)
    product_key: str = Field(min_length=2, max_length=32)


class AttributeBackfillRequest(BaseModel):
    """触发存量事件结构化字段补齐请求。"""

    model_config = ConfigDict(extra="forbid")

    uid: str = Field("", max_length=128)
    product_key: str = Field("", max_length=32)
    event_type: str = Field("", max_length=32)
    only_missing: bool = False
    limit: int = Field(100, ge=1, le=500)
    use_llm: bool = True


class AttributeBackfillResponse(APIModel):
    """存量事件结构化字段补齐执行结果。"""

    stream: str
    scanned: int
    updated: int
    llm_enriched: int
    failed: int
    schema_version: int


class ProductOverviewStat(APIModel):
    """单条产品线的事件与客户覆盖汇总卡片。"""

    product_key: str
    name: str
    enabled: bool = True
    event_count: int = 0
    customer_count: int = 0
    high_priority_count: int = 0


class NumericMetricStat(APIModel):
    """数值型结构化字段在 MongoDB 聚合下的求和、均值与峰值。"""

    field_id: str
    scope_type: ScopeType
    scope_key: str
    field_key: str
    label: str
    unit: str = ""
    agg_ops: list[AggOp] = Field(default_factory=list)
    hit_count: int
    sum_value: float
    avg_value: float
    max_value: float


class TermBucketStat(APIModel):
    """单个枚举值或实体词命中频次。"""

    value: str
    count: int


class TermDistributionStat(APIModel):
    """非数值结构化字段的 Top 分布统计。"""

    field_id: str
    scope_type: ScopeType
    scope_key: str
    field_key: str
    label: str
    hit_count: int
    buckets: list[TermBucketStat] = Field(default_factory=list)


class AnalyticsEventItem(APIModel):
    """聚合视图下钻展示的轻量事件摘要。"""

    event_id: str
    uid: str
    customer_level: str | None = None
    summary: str
    primary_type: str
    types: list[str] = Field(default_factory=list)
    priority: str = "待评估"
    lifecycle_status: str = "状态未明"
    primary_product: str | None = None
    products: list[str] = Field(default_factory=list)
    end_time: str
    props: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    prop_evidence: list[dict[str, JsonValue]] = Field(default_factory=list)


class ProductAnalyticsResponse(APIModel):
    """MongoDB 多维产品与动态属性聚合汇总响应。"""

    total_events: int
    total_customers: int
    selected_product: str | None = None
    selected_event_type: str | None = None
    product_summaries: list[ProductOverviewStat] = Field(default_factory=list)
    numeric_metrics: list[NumericMetricStat] = Field(default_factory=list)
    term_distributions: list[TermDistributionStat] = Field(default_factory=list)
    events: list[AnalyticsEventItem] = Field(default_factory=list)
