"""获用户授权的 XHub 抽取实现；复用实验合约，真实输入仅来自官方消息。"""

from ...services.sender_kinds import FILTER_VERSION, kinds_fingerprint, load_sender_kinds, sender_kind
from ...services.sender_identity import confirmed_sender_role

from dataclasses import asdict, dataclass, field
from copy import deepcopy
import hashlib
import json
import os
from time import monotonic
from uuid import uuid4

from dotenv import dotenv_values
import httpx

from ...config import DEFAULT_ENV_FILE, Settings
from ...domain.models import Event, Message, message_order, parse_time
from ...repositories.local_state import private_json
from ..attribution import AttributionOutput, EvidenceAttributionOutput, attribution_version, parse_attribution_output
from ..contract import ContractError, EvidenceExtractionOutput, ExtractionOutput, parse_output, strict_schema
from ..prompts import PRIORITY_PREVIEW_LINEAGE_V1, STREAM_LINEAGE_V1, compose_system_prompt, prompt_bundle_hash, render_repair_prompt


class XHubError(RuntimeError):
    """脱敏的模型调用错误，日志不包含请求正文、原始错误体或鉴权头。"""


class XHubContractError(XHubError):
    """仅携带本地固定校验提示，允许一次有界结构纠正。"""


@dataclass(frozen=True)
class XHubConfig:
    """从项目根配置加载密钥，不因当前目录变化而读取另一份 .env。"""

    model: str
    api_key: str = field(repr=False)
    base_url: str = "https://llmapi.qiniu.io"
    response_format: str = "json_schema"
    timeout_seconds: float = 90
    max_tokens: int = 4096

    @classmethod
    def load(cls, model: str, **overrides):
        """环境变量覆盖根 .env，密钥缺失时在发送请求之前报错。"""
        values = {**dotenv_values(DEFAULT_ENV_FILE), **os.environ}
        result = cls(model=model, api_key=values.get("XHub_API_KEY") or "",
                     base_url=values.get("XHub_BASE_URL") or "https://llmapi.qiniu.io", **overrides)
        if not result.api_key:
            raise XHubError("根 .env 缺少 XHub_API_KEY")
        return result


def xhub_identity(model: str, response_format: str, max_tokens: int, *, preview: bool = False) -> dict:
    """按流血统锚点、模型与输出合约生成稳定处理流。"""
    bundle_hash = prompt_bundle_hash(priority_preview=preview)
    lineage = PRIORITY_PREVIEW_LINEAGE_V1 if preview else STREAM_LINEAGE_V1
    model_schema = EvidenceAttributionOutput if preview else ExtractionOutput
    schema = strict_schema(model_schema.model_json_schema())
    schema_hash = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
    signature = hashlib.sha256(
        f"{model}:{lineage}:{schema_hash}:{response_format}:{max_tokens}".encode()
    ).hexdigest()[:20]
    return dict(stream=f"xhub-v1-{signature}", prompt_hash=bundle_hash, schema_hash=schema_hash)


def configured_stream(settings: Settings) -> str:
    """供任务和查询共用分析版本计算，不创建网络连接，也不读取密钥。"""
    if settings.provider == "stub":
        return "stub-v1"
    return xhub_identity(settings.llm_model, "json_schema", settings.llm_max_tokens, preview=settings.priority_preview)["stream"]


class XHubProvider:
    """使用 Chat Completions 的显式格式参数；不自动重试收费请求或静默降级。"""

    def __init__(self, config: XHubConfig, settings: Settings, *,
                 transport: httpx.BaseTransport | None = None, max_requests: int = 4096,
                 repair_attempts: int = 1):
        if config.base_url.rstrip("/") not in {"https://llmapi.qiniu.io", "https://llmapi.qiniu.io/v1"}:
            raise XHubError("仅允许已确认的 XHub 地址")
        if config.response_format not in {"json_schema", "json_object"}:
            raise XHubError("输出格式必须是 json_schema 或 json_object")
        if not config.model or not 1 <= config.max_tokens <= 8192 or not 1 <= config.timeout_seconds <= 300:
            raise XHubError("模型或实验资源参数无效")
        self.config, self.settings = config, settings
        self.max_requests = max_requests
        if repair_attempts not in (0, 1):
            raise XHubError("结构纠正最多允许一次")
        self.repair_attempts = repair_attempts
        self.extraction_model = EvidenceExtractionOutput if settings.priority_preview else ExtractionOutput
        self.attribution_model = EvidenceAttributionOutput if settings.priority_preview else AttributionOutput
        base_prompt = compose_system_prompt(mode="extraction", priority_preview=settings.priority_preview)
        self.prompt = base_prompt.content
        schema = strict_schema(self.extraction_model.model_json_schema())
        self.schema = schema
        self.prompt_hash = base_prompt.bundle_hash
        self.schema_hash = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
        self.name = xhub_identity(config.model, config.response_format, config.max_tokens, preview=settings.priority_preview)["stream"]
        self.client = httpx.Client(timeout=config.timeout_seconds, follow_redirects=False, transport=transport)
        self.traces: list[dict] = []
        self.sender_kinds = None
        self.sender_roles = {}
        self.history_retrieval = None
        self.history_context = None
        self.execution_observer = None
        self.candidate_validator = None

    def configure_candidate_validator(self, validator):
        """注入当前批次的无写入预校验，仍共用原有一次纠正上限。"""
        self.candidate_validator = validator

    def configure_execution_observer(self, observer):
        """共用入口注入无鉴权的记录回调，不改变请求内容或模型轮次。"""
        self.execution_observer = observer

    def close(self):
        """释放连接，独立实验结束后不留下后台请求。"""
        self.client.close()

    def configure_sender_kinds(self, kinds):
        """冻结当前窗口的身份配置，使输入标注和公共过滤使用同一规则。"""
        self.sender_kinds = dict(kinds)

    def configure_sender_roles(self, roles):
        """与公共业务入口使用同一份已确认身份；不采用页面展示回退。"""
        self.sender_roles = dict(roles)

    def configure_history_retrieval(self, result):
        """正式增量入口启用四类归属；持久化范围仍沿用已有分析流。"""
        if result.uid != self.settings.uid or result.stream != self.name:
            raise XHubError("历史候选不属于当前客户或分析流")
        self.history_retrieval = result
        self.history_context = None
        self.schema = strict_schema(self.attribution_model.model_json_schema())
        self.schema_hash = hashlib.sha256(json.dumps(self.schema, sort_keys=True).encode()).hexdigest()

    def configure_history_context(self, result):
        """冻结旧聊天背景，绝不把补取内容扩充为本轮可写入的证据集合。"""
        if result.uid != self.settings.uid or result.stream != self.name:
            raise XHubError("历史聊天不属于当前客户或分析流")
        self.history_context = deepcopy(result)

    def _input(self, new_messages: list[Message], context: list[Message], events: list[Event]) -> dict:
        """核验来源与分析流；检索通路直接采用受预算约束的候选，不重复做七天截断。"""
        kinds = self.sender_kinds if self.sender_kinds is not None else load_sender_kinds(self.settings.data_dir)
        all_messages = [*new_messages, *context]
        uids = {message.uid for message in all_messages}
        self._check_input(all_messages, events)
        start, end = min(m.sent_at for m in new_messages), max(m.sent_at for m in new_messages)
        history = self.history_retrieval
        if history is not None:
            if (tuple((m.key, m.digest) for m in new_messages) != history.input_versions
                    or any(m.sent_at >= history.before for m in all_messages)
                    or {e.event_id for e in events} != {e.event_id for e in history.events}):
                raise XHubError("历史候选与当前输入批次不一致")
            existing_values = [candidate.to_input() for candidate in history.candidates]
        else:
            visible = [event for event in events
                       if max(start - event.end_time, event.start_time - end) <= self.settings.match_window
                       and (event.closed_at is None or end - event.closed_at <= self.settings.closed_grace)]
            existing_values = [dict(
                event_id=e.event_id,
                type=e.type,
                types=e.types,
                classification_status=e.classification_status,
                topic_key=e.topic_key,
                start_time=e.start_time.isoformat(),
                end_time=e.end_time.isoformat(),
                summary=e.summary,
                status=e.lifecycle_status,
                waiting_reason=e.waiting_reason,
                resolution=e.resolution,
                priority=e.priority,
                assignees=e.assignees,
            ) for e in visible]

        def message_value(message):
            return dict(id=message.key, send_time=message.sent_at.isoformat(),
                        conversation_id=message.conversation_id, sender_id=message.sender_id,
                        sender_name=message.sender_name, content=message.content,
                        sender_kind=sender_kind(message.to_dict(), kinds),
                        confirmed_sender_role=confirmed_sender_role(message.to_dict(), getattr(self, "sender_roles", {})))

        payload = dict(
            uid=next(iter(uids)),
            notification_policy=dict(version=FILTER_VERSION, identity_config_hash=kinds_fingerprint(kinds)),
            new_messages=[message_value(m) for m in sorted(new_messages, key=message_order)],
            context_messages=[message_value(m) for m in sorted(context, key=message_order)],
            existing_events=existing_values,
        )
        if history is not None:
            payload["history_retrieval"] = history.metadata()
            payload["attribution_policy"] = dict(version=attribution_version(priority_preview=self.settings.priority_preview))
        background = getattr(self, "history_context", None)
        if background is not None:
            try:
                allowed_events = {e.event_id for e in events}
                valid_items = all(item["source"] == "official_chat"
                                  and item["id"] == f"{item['source']}:{item['message_id']}"
                                  and parse_time(item["send_time"]) < background.before
                                  and all(link["event_id"] in allowed_events for link in item["links"])
                                  for item in background.items)
            except (KeyError, ValueError, TypeError, AttributeError):
                valid_items = False
            if (history is None or background.before != history.before
                    or background.input_versions != tuple((m.key, m.digest) for m in new_messages)
                    or background.context_versions != tuple((m.key, m.digest) for m in context)
                    or set(background.event_ids) != {e.event_id for e in events}
                    or background.chars() > self.settings.history_context.max_chars
                    or not valid_items):
                raise XHubError("历史聊天背景与当前输入批次或预算不一致")
            payload["history_context"] = background.to_input()
        return payload

    def _check_input(self, messages, events):
        """真实分析必须显式启用，消息与旧事件须同客户，旧事件还须同分析流。"""
        uids = {message.uid for message in messages}
        if self.settings.provider != "xhub" or uids != {self.settings.uid} or any(
            message.source != "official_chat" for message in messages
        ) or any(event.uid not in uids or event.stream != self.name for event in events):
            raise XHubError("真实抽取仅接受已启用客户的官方消息")

    def _record_trace(self, trace):
        """真实请求正文及响应只落到被忽略的私有目录，不输出到日志或文档。"""
        path = self.settings.data_dir / "raw" / "xhub" / self.name / f"{uuid4().hex}.json"
        private_json(path, trace)

    def extract(self, new_messages, context, existing_events):
        """结构或证据校验失败时至多纠正一次；网络错误不重试，最终失败仍回滚。"""
        repair = []
        for attempt in range(self.repair_attempts + 1):
            try:
                return self._extract_once(new_messages, context, existing_events, repair)
            except XHubContractError as error:
                if attempt == self.repair_attempts:
                    raise
                repair = [
                    {"role": "assistant", "content": self.traces[-1]["output"]},
                    {"role": "user", "content": render_repair_prompt(str(error), priority_preview=self.settings.priority_preview)},
                ]

    def _extract_once(self, new_messages, context, existing_events, repair):
        """执行一个有记录的请求，任何无效结果都不写入事件或处理标记。"""
        if not new_messages:
            return []
        if len(self.traces) >= self.max_requests:
            raise XHubError("本模型已达到本轮实验调用上限")
        payload = self._input(new_messages, context, existing_events)
        visible_ids = {event["event_id"] for event in payload["existing_events"]}
        visible_events = [event for event in existing_events if event.event_id in visible_ids]
        response_format = {"type": self.config.response_format}
        if self.config.response_format == "json_schema":
            response_format["json_schema"] = dict(
                name="aftersales_attribution_v1" if self.history_retrieval is not None else "aftersales_extraction_v1",
                strict=True, schema=self.schema)
        assembled = compose_system_prompt(
            mode="attribution" if self.history_retrieval is not None else "extraction",
            include_history_context=getattr(self, "history_context", None) is not None,
            schema=self.schema,
            priority_preview=self.settings.priority_preview,
        )
        request = dict(
            model=self.config.model,
            messages=[
                {"role": "system", "content": assembled.content},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                *repair,
            ],
            response_format=response_format,
            max_tokens=self.config.max_tokens,
            stream=False,
        )
        context = self._fit_context(request, payload, context)
        base = self.config.base_url.rstrip("/")
        endpoint = (base if base.endswith("/v1") else base + "/v1") + "/chat/completions"
        trace = dict(
            model=self.config.model,
            input=payload,
            response_format=self.config.response_format,
            prompt_bundle_version=assembled.bundle_version,
            prompt_sections=list(assembled.section_ids),
            prompt_hash=assembled.bundle_hash,
            schema_hash=self.schema_hash,
            max_tokens=self.config.max_tokens,
            repair_attempt=bool(repair),
        )
        trace["effective_prompt_hash"] = assembled.content_hash
        trace["notification_policy"] = payload["notification_policy"]
        trace["attribution_policy"] = payload.get("attribution_policy")
        started = monotonic()
        observe = self.execution_observer
        if observe is not None:
            observe("model_started", input=payload, model=self.config.model,
                    prompt_hash=trace["effective_prompt_hash"], schema_hash=self.schema_hash,
                    prompt_bundle_version=assembled.bundle_version, prompt_bundle_hash=assembled.bundle_hash,
                    prompt_sections=list(assembled.section_ids),
                    request_digest=hashlib.sha256(json.dumps(request, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                    repair_attempt=bool(repair),
                    message_versions=[dict(key=m.key, source=m.source, message_id=m.message_id, digest=m.digest,
                        conversation_id=m.conversation_id, send_time=m.sent_at.isoformat()) for m in [*new_messages, *context]])
        try:
            response = self.client.post(endpoint, json=request,
                                        headers={"Authorization": f"Bearer {self.config.api_key}"})
            trace["http_status"] = response.status_code
            if response.status_code != 200:
                raise XHubError(f"XHub 请求失败（HTTP {response.status_code}）")
            body = response.json()
            choice = body["choices"][0]
            message = choice["message"]
            trace["usage"] = body.get("usage", {})
            trace["returned_model"] = body.get("model")
            trace["finish_reason"] = choice.get("finish_reason")
            if choice.get("finish_reason") != "stop" or message.get("refusal"):
                raise XHubError("XHub 输出被截断、拒绝或未正常结束")
            content = message["content"]
            if not isinstance(content, str):
                raise XHubError("XHub 未返回文本 JSON")
            trace["output"] = content
            parser = parse_attribution_output if self.history_retrieval is not None else parse_output
            normalizations = []
            candidates = parser(content, new_messages, context, visible_events,
                **({"output_model": self.attribution_model, "normalizations": normalizations,
                    "require_type_labels": True,
                    "history_items": payload.get("history_context", {}).get("items", [])}
                   if self.history_retrieval is not None else {"output_model": self.extraction_model}))
            trace["normalizations"] = normalizations
            supplied = {m.key for m in [*new_messages, *context]}
            supplied.update(item["id"] for item in payload.get("history_context", {}).get("items", []))
            if any(ref["message_id"] not in supplied for c in candidates for ref in c.basis):
                raise ContractError("归属依据只能引用本次实际提供的消息或历史片段，不能引用预算外消息")
            if self.candidate_validator is not None:
                self.candidate_validator(candidates)
            trace["ok"] = True
            if observe is not None:
                observe("model_completed", suggestions=[asdict(c) for c in candidates], validation="accepted",
                        normalizations=normalizations)
            return candidates
        except httpx.HTTPError as error:
            trace["error"] = "transport_error"
            trace["transport_error_type"] = type(error).__name__
            raise XHubError("XHub 网络请求失败或超时；未自动重试") from None
        except (KeyError, IndexError, TypeError, ValueError) as error:
            trace["error"] = "contract_error" if isinstance(error, ContractError) else "protocol_error"
            if isinstance(error, ContractError):
                trace["validation_error"] = str(error)
                raise XHubContractError(str(error)) from None
            raise XHubError("XHub 输出未通过结构或证据校验") from None
        except XHubError:
            trace["error"] = "api_error"
            raise
        finally:
            if observe is not None and trace.get("error"):
                observe("model_failed", error_type=trace["error"], validation="not_accepted")
            trace["latency_seconds"] = round(monotonic() - started, 3)
            self.traces.append(trace)
            self._record_trace(trace)

    def _fit_context(self, request, payload, context):
        """预算不足先去掉最早的可选背景，保留新增消息、机器人辅助和已定位历史依据。"""
        limit = self.settings.history_context.max_input_chars
        if len(json.dumps(request, ensure_ascii=False)) <= limit:
            return context
        protected = {item["id"] for item in payload.get("history_context", {}).get("items", [])}
        protected.update(m["id"] for m in payload["context_messages"] if m.get("sender_kind") == "bot")
        optional = [m for m in payload["context_messages"] if m["id"] not in protected]
        omitted = set()
        for message in optional:
            omitted.add(message["id"])
            payload["context_messages"] = [m for m in payload["context_messages"] if m["id"] != message["id"]]
            payload["context_budget"] = dict(omitted_messages=len(omitted), reason="optional_old_context_budget",
                                             incomplete=True)
            request["messages"][1]["content"] = json.dumps(payload, ensure_ascii=False)
            if len(json.dumps(request, ensure_ascii=False)) <= limit:
                return [m for m in context if m.key not in omitted]
        raise XHubError("本轮新增消息与必要依据已超过总输入预算；未丢弃新增消息，请缩小处理周期")
