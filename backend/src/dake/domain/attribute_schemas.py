"""七牛产品目录、可配置结构化事件属性元数据与确定性量纲换算规则。"""

from collections.abc import Mapping, Sequence
from copy import deepcopy
import re
from typing import Any, Literal

from .models import EVENT_TYPES

ScopeType = Literal["product", "event_type"]
FieldDataType = Literal["number", "enum", "boolean", "string"]
UnitFamily = Literal[
    "none",
    "count_scale",
    "million_tokens",
    "bandwidth_mbps",
    "storage_tb",
    "currency_cny",
    "duration_ms",
]
AggOp = Literal["sum", "avg", "max", "terms"]

FIELD_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,47}$")
PRODUCT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

UNIT_FAMILY_META: dict[str, dict[str, str]] = {
    "none": {"label": "无单位换算", "default_unit": ""},
    "count_scale": {"label": "数量/速率（自动换算千/k/万/w/百万/M/亿）", "default_unit": ""},
    "million_tokens": {"label": "Token 规模（统一换算为百万Token）", "default_unit": "百万Token"},
    "bandwidth_mbps": {"label": "网络带宽（Kbps/Mbps/Gbps/Tbps 统一为 Mbps）", "default_unit": "Mbps"},
    "storage_tb": {"label": "存储与流量容量（MB/GB/TB/PB 统一为 TB）", "default_unit": "TB"},
    "currency_cny": {"label": "人民币金额（千/万/百万统一为元）", "default_unit": "元"},
    "duration_ms": {"label": "耗时延迟（ms/秒/分钟统一为 ms）", "default_unit": "ms"},
}

DEFAULT_PRODUCTS: tuple[dict[str, Any], ...] = (
    {
        "product_key": "ai_maas",
        "name": "AI 大模型与智能体",
        "description": "AI 大模型广场（MaaS）、推理 API、Agent 运行时及灵矽语音交互服务",
        "keywords": [
            "大模型", "模型", "maas", "xhub", "llm", "token", "tpm", "rpm", "429",
            "限流", "deepseek", "qwen", "kimi", "claude", "gpt", "minimax", "gemini",
            "agent", "推理", "embedding", "向量", "tts", "asr", "灵矽",
        ],
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "kodo",
        "name": "对象存储 Kodo",
        "description": "海量云存储 Bucket、标准/低频/归档分层、跨区域同步、S3 接口与存储一体机",
        "keywords": [
            "kodo", "对象存储", "bucket", "存储空间", "归档", "解冻", "低频存储",
            "生命周期", "跨区域同步", "分片上传", "上传回调", "579", "614", "631", "s3",
        ],
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "cdn_dcdn",
        "name": "CDN 与边缘加速",
        "description": "融合 CDN（Fusion）、全站加速 DCDN、边缘缓存、回源链路与海外加速",
        "keywords": [
            "cdn", "dcdn", "融合cdn", "全站加速", "加速域名", "cname", "回源",
            "源站", "刷新", "预取", "缓存命中率", "带宽", "劫持", "502", "504", "边缘节点",
        ],
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "rtc_media",
        "name": "音视频与多媒体",
        "description": "Dora 智能转码与内容审核、Pili 直播云、Miku 快直播、QRTC 实时音视频与 QVS 监控",
        "keywords": [
            "dora", "转码", "avthumb", "锐智转码", "pfop", "内容审核", "鉴黄", "水印",
            "pili", "直播", "推流", "拉流", "断流", "miku", "快直播", "qrtc", "连麦",
            "webrtc", "qvs", "gb28181", "视频监控",
        ],
        "sort_order": 40,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "compute_gpu",
        "name": "GPU 算力与云主机",
        "description": "GPU 算力服务器、裸金属集群、云服务器 QVM 与全栈应用服务器 LAS",
        "keywords": [
            "gpu", "算力", "h800", "h100", "a100", "a800", "4090", "l40s",
            "裸金属", "云主机", "云服务器", "qvm", "las", "显存",
        ],
        "sort_order": 50,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "cert_sms_ops",
        "name": "证书域名与云通信",
        "description": "SSL 数字证书签发续期、域名解析备案、云短信 SMS 及 Pandora 日志平台",
        "keywords": [
            "ssl", "证书", "https证书", "tls", "域名备案", "解析生效",
            "云短信", "sms", "短信签名", "pandora", "日志检索",
        ],
        "sort_order": 60,
        "enabled": True,
        "builtin": True,
    },
    {
        "product_key": "general_biz",
        "name": "通用商务与账户",
        "description": "不局限于单一技术产品的总账单核对、框架合同、月结开票及账号主体事项",
        "keywords": ["总账单", "开票", "发票", "框架合同", "月结", "主体变更", "实名认证", "账户余额"],
        "sort_order": 70,
        "enabled": True,
        "builtin": True,
    },
)

DEFAULT_ATTRIBUTE_FIELDS: tuple[dict[str, Any], ...] = (
    # ---- AI 大模型与智能体 (ai_maas) ----
    {
        "scope_type": "product",
        "scope_key": "ai_maas",
        "field_key": "model_name",
        "label": "调用模型名称",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "提取聊天中明确点名的具体模型标识（如 deepseek-v3、qwen-max、claude-3.7-sonnet 等），未提具体模型名则不填。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "ai_maas",
        "field_key": "tpm_quota",
        "label": "TPM 额度",
        "data_type": "number",
        "unit": "TPM",
        "unit_family": "count_scale",
        "enum_options": [],
        "agg_ops": ["sum", "max", "avg"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的每分钟 Token 限额或目标申请 TPM 数值（若既有当前值又有目标申请值，优先填目标申请值）。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "ai_maas",
        "field_key": "rpm_quota",
        "label": "RPM 额度",
        "data_type": "number",
        "unit": "RPM",
        "unit_family": "count_scale",
        "enum_options": [],
        "agg_ops": ["sum", "max", "avg"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的每分钟请求数 RPM 限额或目标申请 RPM 数值（有目标申请值时优先填目标值）。",
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "ai_maas",
        "field_key": "token_usage_m",
        "label": "Token 用量规模",
        "data_type": "number",
        "unit": "百万Token",
        "unit_family": "million_tokens",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": [],
        "extraction_hint": "提取明确提到的日/月预估或实际消耗 Token 总量、购买资源包 Token 额度（区分每分钟限额 TPM，仅在指调用总量时填写）。",
        "sort_order": 40,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "ai_maas",
        "field_key": "ai_issue_kind",
        "label": "接口问题子类",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": [
            "429限流或配额不足",
            "超时或首字延迟高",
            "5xx服务端报错",
            "鉴权或请求参数错误",
            "输出截断或格式异常",
            "计费或额度核对",
        ],
        "agg_ops": ["terms"],
        "applicable_types": ["排障", "资源需求", "商务需求"],
        "extraction_hint": "仅当聊天明确出现对应大模型调用异常或核对现象时选择对应枚举项。",
        "sort_order": 50,
        "enabled": True,
        "builtin": True,
    },
    # ---- 对象存储 Kodo (kodo) ----
    {
        "scope_type": "product",
        "scope_key": "kodo",
        "field_key": "bucket_name",
        "label": "存储空间 Bucket",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "提取聊天明确给出的七牛对象存储 Bucket 空间名称，未给出明确名称时不填。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "kodo",
        "field_key": "storage_capacity_tb",
        "label": "存储容量规模",
        "data_type": "number",
        "unit": "TB",
        "unit_family": "storage_tb",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的存储数据量、新增或迁离容量规模（如 50TB、2PB、500GB）。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "kodo",
        "field_key": "kodo_issue_kind",
        "label": "存储环节子类",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": [
            "上传或分片异常",
            "下载或外链访问异常",
            "上传回调失败(579)",
            "跨区域同步或生命周期",
            "归档或深度归档解冻",
            "签名或权限报错(401/403)",
        ],
        "agg_ops": ["terms"],
        "applicable_types": ["排障", "功能需求", "资源需求"],
        "extraction_hint": "按聊天明确讨论的对象存储具体业务环节选择。",
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    # ---- CDN 与边缘加速 (cdn_dcdn) ----
    {
        "scope_type": "product",
        "scope_key": "cdn_dcdn",
        "field_key": "accel_domain",
        "label": "加速域名",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "提取聊天中明确提及的加速域名或源站域名（多个域名时提取核心主域名）。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "cdn_dcdn",
        "field_key": "bandwidth_mbps",
        "label": "带宽规模",
        "data_type": "number",
        "unit": "Mbps",
        "unit_family": "bandwidth_mbps",
        "enum_options": [],
        "agg_ops": ["sum", "max", "avg"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的 CDN 峰值带宽、突发扩容带宽或切流带宽数值（如 20Gbps、500Mbps）。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "cdn_dcdn",
        "field_key": "http_status_code",
        "label": "HTTP 异常状态码",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["403", "404", "499", "502", "503", "504", "其他状态码"],
        "agg_ops": ["terms"],
        "applicable_types": ["排障"],
        "extraction_hint": "当聊天明确提及 CDN 访问或回源返回的具体 HTTP 错误状态码时填写。",
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    # ---- 音视频与多媒体 (rtc_media) ----
    {
        "scope_type": "product",
        "scope_key": "rtc_media",
        "field_key": "media_module",
        "label": "音视频业务模块",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": [
            "Dora音视频转码与处理",
            "内容安全审核",
            "Pili标准直播",
            "Miku快直播",
            "QRTC实时音视频",
            "QVS视频监控",
        ],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "根据明确提及的转码、审核、直播、快直播、连麦或视频监控能力归类。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "rtc_media",
        "field_key": "concurrent_or_minutes",
        "label": "并发路数或处理时长",
        "data_type": "number",
        "unit": "路/分钟",
        "unit_family": "count_scale",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的直播/连麦并发路数或音视频转码时长分钟数。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "rtc_media",
        "field_key": "media_symptom",
        "label": "流媒体异常表现",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": [
            "推流或拉流失败",
            "卡顿掉帧或花屏",
            "转码失败或队列积压",
            "首屏慢或端到端延迟高",
            "录制回放异常",
        ],
        "agg_ops": ["terms"],
        "applicable_types": ["排障"],
        "extraction_hint": "仅当聊天明确反馈对应音视频质量或任务处理异常时选择。",
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    # ---- GPU 算力与云主机 (compute_gpu) ----
    {
        "scope_type": "product",
        "scope_key": "compute_gpu",
        "field_key": "gpu_or_instance_model",
        "label": "GPU或实例规格",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的显卡型号或云主机实例规格（如 H800、A100、RTX4090 等）。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "compute_gpu",
        "field_key": "card_or_node_count",
        "label": "卡数或实例台数",
        "data_type": "number",
        "unit": "卡/台",
        "unit_family": "count_scale",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的 GPU 张数/卡数或服务器节点台数。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    # ---- 证书域名与云通信 (cert_sms_ops) ----
    {
        "scope_type": "product",
        "scope_key": "cert_sms_ops",
        "field_key": "ops_sub_service",
        "label": "子服务类型",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["SSL证书签发与替换", "域名解析与备案", "云短信SMS", "Pandora日志分析"],
        "agg_ops": ["terms"],
        "applicable_types": [],
        "extraction_hint": "根据聊天明确办理或排查的证书、域名、短信或日志服务归类。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "product",
        "scope_key": "cert_sms_ops",
        "field_key": "sms_or_cert_volume",
        "label": "证书数或短信规模",
        "data_type": "number",
        "unit": "张/条",
        "unit_family": "count_scale",
        "enum_options": [],
        "agg_ops": ["sum"],
        "applicable_types": [],
        "extraction_hint": "提取明确提及的证书数量或云短信发送条数规模。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    # ---- 六类事件通用经营与流程属性 (scope_type = event_type) ----
    {
        "scope_type": "event_type",
        "scope_key": "商机",
        "field_key": "budget_amount_cny",
        "label": "预算或报价金额",
        "data_type": "number",
        "unit": "元",
        "unit_family": "currency_cny",
        "enum_options": [],
        "agg_ops": ["sum", "max", "avg"],
        "applicable_types": ["商机"],
        "extraction_hint": "提取商机中明确提及的客户预算金额、报价金额或预计合同金额（人民币）。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "商机",
        "field_key": "sales_stage",
        "label": "商机推进阶段",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["需求咨询", "测试评估(POC)", "商务报价", "合同推进", "已成交"],
        "agg_ops": ["terms"],
        "applicable_types": ["商机"],
        "extraction_hint": "根据当前商机讨论所处的客观阶段选择。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "流失",
        "field_key": "at_risk_amount_cny",
        "label": "受影响消费规模",
        "data_type": "number",
        "unit": "元",
        "unit_family": "currency_cny",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": ["流失"],
        "extraction_hint": "提取明确提及的预计减少消费金额或迁出业务账单规模。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "流失",
        "field_key": "churn_stage",
        "label": "流失风险阶段",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["风险预警", "明确缩量或切流计划", "已停用或迁出"],
        "agg_ops": ["terms"],
        "applicable_types": ["流失"],
        "extraction_hint": "根据客户当前表态属于预警、计划切流还是实际已停用选择。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "流失",
        "field_key": "competitor_name",
        "label": "提及流向竞品",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": ["流失"],
        "extraction_hint": "仅在聊天明确提及对比或迁往的外部云厂商/竞品名称时填写。",
        "sort_order": 30,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "排障",
        "field_key": "root_cause_category",
        "label": "根因归类",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": [
            "平台侧服务异常",
            "配额或速率限制",
            "客户侧源站或配置问题",
            "外部运营商或网络链路",
            "排查定位中",
        ],
        "agg_ops": ["terms"],
        "applicable_types": ["排障"],
        "extraction_hint": "已有明确排查结论时归入对应根因，仍在核查未定论时选『排查定位中』。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "排障",
        "field_key": "observed_latency_ms",
        "label": "实测延迟或耗时",
        "data_type": "number",
        "unit": "ms",
        "unit_family": "duration_ms",
        "enum_options": [],
        "agg_ops": ["avg", "max"],
        "applicable_types": ["排障"],
        "extraction_hint": "当聊天明确给出接口耗时、首字延迟或超时秒数/毫秒数时提取（如 3500ms、12s）。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "资源需求",
        "field_key": "expected_effective_time",
        "label": "期望生效节点",
        "data_type": "string",
        "unit": "",
        "unit_family": "none",
        "enum_options": [],
        "agg_ops": ["terms"],
        "applicable_types": ["资源需求"],
        "extraction_hint": "提取客户明确要求的扩容、提额或资源交付时间节点。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "功能需求",
        "field_key": "feature_eval_status",
        "label": "需求评估结论",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["待产品评估", "已有替代方案", "已纳入排期", "暂不支持"],
        "agg_ops": ["terms"],
        "applicable_types": ["功能需求"],
        "extraction_hint": "根据内部对客户功能诉求给出的最新答复结论选择。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "商务需求",
        "field_key": "biz_matter_kind",
        "label": "商务办理事项",
        "data_type": "enum",
        "unit": "",
        "unit_family": "none",
        "enum_options": ["询价与折扣申请", "合同续签或变更", "账单与用量核对", "发票开具", "欠费延期或退款"],
        "agg_ops": ["terms"],
        "applicable_types": ["商务需求"],
        "extraction_hint": "根据具体办理的报价、合同、账单、开票或退款事项选择。",
        "sort_order": 10,
        "enabled": True,
        "builtin": True,
    },
    {
        "scope_type": "event_type",
        "scope_key": "商务需求",
        "field_key": "involved_amount_cny",
        "label": "涉及金额",
        "data_type": "number",
        "unit": "元",
        "unit_family": "currency_cny",
        "enum_options": [],
        "agg_ops": ["sum", "max"],
        "applicable_types": ["商务需求"],
        "extraction_hint": "提取明确提及的账单核对金额、开票金额、合同金额或退款金额（人民币）。",
        "sort_order": 20,
        "enabled": True,
        "builtin": True,
    },
)

_NUMBER_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")


def validate_product_definition(raw: Mapping[str, Any]) -> dict[str, Any]:
    """校验并归一化产品种类配置。"""
    product_key = str(raw.get("product_key") or "").strip()
    if not PRODUCT_KEY_PATTERN.fullmatch(product_key):
        raise ValueError("产品编码须以小写字母开头，仅含小写字母、数字与下划线（2~32位）")
    name = str(raw.get("name") or "").strip()
    if not name or len(name) > 64:
        raise ValueError("产品名称不能为空且不超过 64 个字符")
    description = str(raw.get("description") or "").strip()
    keywords_raw = raw.get("keywords") or []
    if not isinstance(keywords_raw, Sequence) or isinstance(keywords_raw, (str, bytes)):
        raise ValueError("产品识别线索词必须是字符串列表")
    keywords = list(dict.fromkeys(str(k).strip().lower() for k in keywords_raw if str(k).strip()))
    sort_order = int(raw.get("sort_order", 100))
    return {
        "product_key": product_key,
        "name": name,
        "description": description,
        "keywords": keywords,
        "sort_order": sort_order,
        "enabled": bool(raw.get("enabled", True)),
        "builtin": bool(raw.get("builtin", False)),
    }


def validate_field_definition(
    raw: Mapping[str, Any],
    *,
    valid_product_keys: set[str] | None = None,
    existing: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """校验结构化属性定义；已有字段禁止原地篡改编码或值类型，防止历史聚合污染。"""
    scope_type = str(raw.get("scope_type") or "").strip()
    if scope_type not in ("product", "event_type"):
        raise ValueError("字段挂载范围类型必须是 product 或 event_type")
    scope_key = str(raw.get("scope_key") or "").strip()
    if scope_type == "event_type" and scope_key not in EVENT_TYPES:
        raise ValueError(f"事件通用属性仅支持挂载在六类主事件上：{', '.join(EVENT_TYPES)}")
    if scope_type == "product":
        if not PRODUCT_KEY_PATTERN.fullmatch(scope_key):
            raise ValueError("所属产品编码格式不合法")
        if valid_product_keys is not None and scope_key not in valid_product_keys:
            raise ValueError(f"所属产品不存在：{scope_key}")

    field_key = str(raw.get("field_key") or "").strip()
    if not FIELD_KEY_PATTERN.fullmatch(field_key):
        raise ValueError("字段编码须以小写字母开头，仅含小写字母、数字与下划线（2~48位）")

    data_type = str(raw.get("data_type") or "").strip()
    if data_type not in ("number", "enum", "boolean", "string"):
        raise ValueError("字段值类型必须是 number、enum、boolean 或 string")

    if existing is not None:
        if (
            existing.get("scope_type") != scope_type
            or existing.get("scope_key") != scope_key
            or existing.get("field_key") != field_key
        ):
            raise ValueError("已创建字段不能更改所属范围或字段编码")
        if existing.get("data_type") != data_type:
            raise ValueError("已创建字段不能更改数据类型，如需更换类型请停用旧字段并新建字段")

    label = str(raw.get("label") or "").strip()
    if not label or len(label) > 64:
        raise ValueError("字段中文名称不能为空且不超过 64 个字符")

    unit_family = str(raw.get("unit_family") or "none").strip()
    if unit_family not in UNIT_FAMILY_META:
        raise ValueError(f"不支持的单位换算族：{unit_family}")
    if data_type != "number":
        unit_family = "none"

    unit = str(raw.get("unit") or "").strip()
    if data_type == "number" and not unit and UNIT_FAMILY_META[unit_family]["default_unit"]:
        unit = UNIT_FAMILY_META[unit_family]["default_unit"]

    enum_raw = raw.get("enum_options") or []
    if not isinstance(enum_raw, Sequence) or isinstance(enum_raw, (str, bytes)):
        raise ValueError("枚举候选项必须是字符串列表")
    enum_options = list(dict.fromkeys(str(item).strip() for item in enum_raw if str(item).strip()))
    if data_type == "enum" and len(enum_options) < 2:
        raise ValueError("单选枚举类型字段至少需要配置 2 个候选值")
    if data_type != "enum":
        enum_options = []

    agg_raw = raw.get("agg_ops") or []
    if not isinstance(agg_raw, Sequence) or isinstance(agg_raw, (str, bytes)):
        raise ValueError("聚合方式必须是列表")
    allowed_aggs = {"sum", "avg", "max"} if data_type == "number" else {"terms"}
    agg_ops = [op for op in dict.fromkeys(str(x).strip() for x in agg_raw) if op in allowed_aggs]
    if not agg_ops:
        agg_ops = ["sum", "max"] if data_type == "number" else ["terms"]

    app_raw = raw.get("applicable_types") or []
    applicable_types = [t for t in dict.fromkeys(str(x).strip() for x in app_raw) if t in EVENT_TYPES]
    if scope_type == "event_type":
        applicable_types = [scope_key]

    extraction_hint = str(raw.get("extraction_hint") or "").strip()
    if not extraction_hint:
        raise ValueError("请填写一句简明的 AI 提取条件说明，确保抽取准确")

    return {
        "field_id": f"{scope_type}:{scope_key}:{field_key}",
        "scope_type": scope_type,
        "scope_key": scope_key,
        "field_key": field_key,
        "label": label,
        "data_type": data_type,
        "unit": unit,
        "unit_family": unit_family,
        "enum_options": enum_options,
        "agg_ops": agg_ops,
        "applicable_types": applicable_types,
        "extraction_hint": extraction_hint,
        "sort_order": int(raw.get("sort_order", 100)),
        "enabled": bool(raw.get("enabled", True)),
        "builtin": bool(existing.get("builtin", False) if existing else raw.get("builtin", False)),
    }


def parse_number_with_unit(
    raw_value: str,
    unit_family: str = "none",
    explicit_number: float | int | None = None,
) -> float | None:
    """将群聊口语数值表达确定性换算为字段标准单位数值，消除『500万』与『5000000』量纲不一致。"""
    text = (raw_value or "").strip()
    if not text and explicit_number is None:
        return None

    match = _NUMBER_RE.search(text)
    if match is None:
        return float(explicit_number) if explicit_number is not None else None

    base = float(match.group(0).replace(",", ""))
    tail = text[match.end() :].strip().lower()
    full_lower = text.lower()

    if unit_family == "bandwidth_mbps":
        if "tbps" in tail or tail.startswith("t"):
            return round(base * 1_000_000.0, 4)
        if "gbps" in tail or tail.startswith("g"):
            return round(base * 1_000.0, 4)
        if "kbps" in tail or tail.startswith("k"):
            return round(base / 1_000.0, 4)
        if "bps" in tail and "mbps" not in tail:
            return round(base / 1_000_000.0, 6)
        return round(base, 4)

    if unit_family == "storage_tb":
        if "pb" in tail or tail.startswith("p"):
            return round(base * 1024.0, 4)
        if "gb" in tail or tail.startswith("g"):
            return round(base / 1024.0, 4)
        if "mb" in tail or tail.startswith("m"):
            return round(base / (1024.0 * 1024.0), 6)
        return round(base, 4)

    if unit_family == "duration_ms":
        if any(tok in tail for tok in ("min", "分钟", "分")):
            return round(base * 60_000.0, 2)
        if "ms" in tail or "毫秒" in tail:
            return round(base, 2)
        if tail.startswith("s") or "秒" in tail:
            return round(base * 1_000.0, 2)
        return round(base, 2)

    # 通用中文/英文数量级倍率识别（适配 count_scale、million_tokens、currency_cny）
    multiplier = 1.0
    if "十亿" in tail or re.match(r"^b\b", tail):
        multiplier = 1_000_000_000.0
    elif "亿" in tail:
        multiplier = 100_000_000.0
    elif "千万" in tail:
        multiplier = 10_000_000.0
    elif "百万" in tail or re.match(r"^m(?:illion)?\b", tail):
        multiplier = 1_000_000.0
    elif "万" in tail or re.match(r"^w\b", tail):
        multiplier = 10_000.0
    elif "千" in tail or re.match(r"^k\b", tail):
        multiplier = 1_000.0

    scaled = base * multiplier
    if unit_family == "million_tokens":
        # 若原文直接写了“120百万”或“5000万 token”，scaled 是绝对 Token 数；若原文只写了小数且无量级词但模型已按百万给值，保持兼容
        if multiplier >= 1_000.0 or scaled >= 10_000.0:
            return round(scaled / 1_000_000.0, 4)
        if "百万" in full_lower:
            return round(base, 4)
        return round(scaled, 4)

    return round(scaled, 4)


def normalize_structured_value(
    field_def: Mapping[str, Any],
    raw_value: str,
    *,
    numeric_hint: float | int | None = None,
    bool_hint: bool | None = None,
) -> int | float | bool | str | None:
    """按字段定义的强类型做确定性转换；不满足枚举或数值要求时返回 None 拒绝脏数据入库。"""
    dtype = field_def["data_type"]
    cleaned = (raw_value or "").strip()

    if dtype == "number":
        num = parse_number_with_unit(cleaned, field_def.get("unit_family", "none"), numeric_hint)
        if num is None:
            return None
        return int(num) if float(num).is_integer() else num

    if dtype == "enum":
        options = list(field_def.get("enum_options") or [])
        if cleaned in options:
            return cleaned
        # 容忍轻微大小写差异，但不猜测未定义的枚举
        lower_map = {opt.lower(): opt for opt in options}
        return lower_map.get(cleaned.lower())

    if dtype == "boolean":
        if isinstance(bool_hint, bool):
            return bool_hint
        low = cleaned.lower()
        if low in {"true", "是", "已触发", "存在", "1", "yes"}:
            return True
        if low in {"false", "否", "未触发", "不存在", "0", "no"}:
            return False
        return None

    if dtype == "string":
        return cleaned if cleaned else None

    return None


def match_products_by_keywords(
    text: str,
    products: Sequence[Mapping[str, Any]],
) -> list[str]:
    """基于配置的产品识别线索词做零成本候选产品路由（优先具体技术产品，兜底通用商务）。"""
    haystack = (text or "").lower()
    if not haystack.strip():
        return []
    matched: list[str] = []
    general_hit = False
    for prod in sorted(products, key=lambda p: int(p.get("sort_order", 100))):
        if not prod.get("enabled", True):
            continue
        pkey = prod["product_key"]
        kws = prod.get("keywords") or []
        if any(str(kw).lower() in haystack for kw in kws if str(kw).strip()):
            if pkey == "general_biz":
                general_hit = True
            else:
                matched.append(pkey)
    if not matched and general_hit:
        matched.append("general_biz")
    return matched


def seed_catalog_copy() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成经过校验的默认七牛产品目录与预置结构化字段副本。"""
    products = [validate_product_definition(item) for item in DEFAULT_PRODUCTS]
    valid_pkeys = {p["product_key"] for p in products}
    fields = [
        validate_field_definition(item, valid_product_keys=valid_pkeys)
        for item in DEFAULT_ATTRIBUTE_FIELDS
    ]
    return deepcopy(products), deepcopy(fields)
