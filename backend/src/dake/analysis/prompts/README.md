# 模块化提示词说明（`dake.analysis.prompts`）

本目录集中管理售后聊天增量分析的全部模型提示词，取代原先单体文本与分散在多个 Python 模块中的硬编码策略字符串，保证**单点维护、章节无冲突编号、动态插槽可扩展、分析流版本稳定**。

## 目录结构

```text
backend/src/dake/analysis/prompts/
├── __init__.py                         # 公共导出接口
├── composer.py                         # 分块加载、插槽校验、自动编号与按模式组装器
├── __main__.py                         # 命令行预览入口（支持查看分块清单或完整组装输出）
├── sections/                           # 独立职责的业务规则分块（Markdown）
│   ├── 00_role_and_guardrails.md       # 角色定位与防提示注入边界（无编号前言）
│   ├── 01_evidence_and_bot_rules.md    # 增量触发、分条消息拼合与机器人通知辅助规则
│   ├── 02_slicing_and_identity.md      # 事项切片、topic_key 规范与跨场景身份延续
│   ├── 03_classification_rules.md      # 6+1 分类定义、待分类兜底与逐类正反证据门槛
│   ├── 04_typed_attributes.md          # 类型专属属性规则（含 {{typed_attribute_catalog}} 插槽）
│   ├── 05_lifecycle_status.md          # 生命周期五主态、等待原因与完结结果规则
│   ├── 06_priority_signals.md          # 客观影响面与时效紧迫度信号提案规则
│   ├── 07_history_candidates.md        # 历史候选事件（existing_events）只读边界
│   ├── 08_history_context.md           # 补充历史聊天背景（history_context）只读边界
│   ├── 09_attribution_decisions.md     # 正式四类归属（new/update/duplicate/review）与指代认回
│   ├── 10_summary_changes.md           # 局部摘要更新与变化记录（changes）纪律
│   ├── 11_recovery_and_continuity.md   # 客户确认恢复、撤销恢复与再次发生边界（continuity）
│   └── 99_output_checklist.md          # 输出协议版本（{{schema_version_rule}}）与提交前自检
└── templates/
    └── repair_feedback.md              # 结构或证据校验失败时的一次性有界修正提示词
```

## 组装拓扑与动态插槽

1. **两种组装模式**：
   - `mode="attribution"`（正式增量流水线）：加载 `00`～`07` +（有补充背景时加载 `08`）+ `09`～`11` + `99`，所有带 `# 标题` 的分块由组装器自动编排为连续中文序号（`一、…`、`二、…`），消除跳号或重号。
   - `mode="extraction"`（独立基础抽取测试）：加载 `00`～`06` + `99`。
   - `priority_preview=True` 替换 `06_priority_signals`，分别装配事项定级证据、受影响客户和服务时钟证据。修复请求附加预览证据校验规则。
2. **动态插槽渲染**：
   - `{{typed_attribute_catalog}}`：默认从 `domain.models.TYPE_ATTRIBUTE_NAMES` 自动生成，同时原生兼容结构化字段定义（`label`、`value_type`、`enum_options`、`extraction_hint`、`enabled`），后续接入界面化/MongoDB 属性模板时可直接注入。
   - 组装器对所有 `{{...}}` 插槽执行严格单遍校验，遗漏插槽或残缺括号会在请求发出前立即报错。
3. **版本审计与分析流解耦**：
   - 数据库分析流标识（`stream`）分别锚定常规与预览的显式血统常量，日常排版润色、分块维护或属性说明微调不会导致存量事件断流。
   - 调用轨迹记录 `prompt_hash`（规则集哈希）、`prompt_sections`（激活分块）和 `effective_prompt_hash`（最终系统提示词哈希）。执行审计的 `model_started` 对应记录 `prompt_bundle_hash`、`prompt_sections` 和 `prompt_hash`，并以 `request_digest` 覆盖修复消息。

## 本地预览命令

在仓库根目录执行以下命令即可快速检视组装结果：

```bash
# 查看正式归属模式激活的分块清单及各自 SHA-256 指纹
.venv/bin/python -m dake.analysis.prompts --mode attribution --with-context --list

# 预览完整组装后的系统提示词正文
.venv/bin/python -m dake.analysis.prompts --mode attribution --with-context

.venv/bin/python -m dake.analysis.prompts --mode attribution --with-context --priority-preview --list
```
