# 大客售后智能系统

七牛云技术支持部内部项目：从大客户售后群聊中识别并持续更新售后事件。本仓库包含可复用的增量处理后端、FastAPI 接口与事件管理网页。

开发前先读：

- [CONTRIBUTING.md](CONTRIBUTING.md)：分支开发、提交格式与 PR 合并的简单约定
- [docs/项目简报.md](docs/项目简报.md)：现况、领导与官方要求、一期要做成什么样、接口与事件约定、开发约束
- [docs/群识别与消息过滤开发方案.md](docs/群识别与消息过滤开发方案.md)：待实现的群分类、供应商与内部产品群排除规则、配置及验收要求
- [docs/多客户接入与定时增量开发方案.md](docs/多客户接入与定时增量开发方案.md)：客户发现、UID 补查、大客户筛选、负责人及定时执行规划
- [docs/变化面板优化同步说明.md](docs/变化面板优化同步说明.md)：队友同步性能优化分支、执行数据库迁移和本地验证的步骤
- [docs/大客售后智能系统-技术方案草案.docx](docs/大客售后智能系统-技术方案草案.docx)：架构与增量洗数的详细方案
- [AGENTS.md](AGENTS.md)：给 Codex / 编程助手的操作约定

一期实现官方取数、消息归一、增量抽取与跨天事件更新。处理周期可配置，默认 `15m`，将周期设为 `1d` 可以演示 2026-11-08 至 2026-11-12 五轮回放。存储使用本项目独立的本地 Docker MySQL。FastAPI 和网页复用既有业务服务。任务中心支持定时器驱动全部大客户逐窗口增量处理，当前先在本地验证，服务器部署暂缓；角色分发与日报周报仍留待后续。

数据路径：官方 `chatRecords` → 数据源适配器 → 统一消息 → 增量抽取 / 跨天匹配 → 标准事件。命令行与 HTTP 后台任务调用同一套增量和回放服务。目录职责与调用关系见 [工程架构](docs/工程架构.md)。

## 安装与启动

需要 Python 3.11 及以上版本、Node.js 24、npm、Docker 与 Docker Compose。在项目根目录执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e './backend[dev]'
```

若创建环境时提示缺少 `ensurepip`，先安装与 Python 版本对应的 venv 组件；Ubuntu 通常使用 `python3-venv`。已有 `.venv` 时可直接安装项目依赖。

如需复用当前验证过的依赖版本，可将上面的安装命令替换为：

```bash
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python -m pip install -e ./backend --no-deps
```

首次使用将 `.env.example` 复制为 `.env`，分别设置 `MYSQL_PASSWORD` 和 `MYSQL_ROOT_PASSWORD` 为不同的随机密码。已有本地 `.env` 时保留原文件，再启动数据库并建表：

```bash
docker compose up -d --wait mysql
docker compose ps
.venv/bin/dake init-db
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --user="$MYSQL_USER" "$MYSQL_DATABASE"' < backend/migrations/001_processing_attempts.sql
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --user="$MYSQL_USER" "$MYSQL_DATABASE"' < backend/migrations/002_event_link_hints.sql
docker compose exec -T mysql sh -c 'MYSQL_PWD="$MYSQL_PASSWORD" exec mysql --user="$MYSQL_USER" "$MYSQL_DATABASE"' < backend/migrations/003_customer_directory.sql
```

新环境也可直接使用汇总表结构 `backend/schema.sql` 一步初始化全部 15 张表（或导入团队私下共享的 `data/exports/*.sql[.gz]` 本地快照，真实数据快照仅保存在本地 `data/`，禁止提交入库）：

```bash
.venv/bin/python scripts/db_snapshot.py import backend/schema.sql
```

MySQL 8.4 仅监听 `127.0.0.1:3307`，数据库默认 `dake_aftersales`，应用用户默认 `dake`。数据保存在 `data/mysql/`。健康检查使用应用账号执行查询，启动完成后应显示 `healthy`。

业务存储使用 `messages` 和 `events`，网页任务存于 `processing_runs`（API 启动时幂等创建），逐窗口执行与重试存于 `processing_attempts`。执行记录表由 `backend/migrations/001_processing_attempts.sql` 显式迁移；当前本地库已有此表，新环境需先迁移。消息的处理标记与事件更新在同一事务提交；`data/state/` 的水位仅帮助选择下一次窗口，丢失后可依据数据库记录去重。

`002_event_link_hints.sql` 为变化面板增加自动生成的轻量事件引用提示列，已有阶段和收据保持原样。每个库只执行一次；执行前可用 `SHOW COLUMNS FROM processing_attempts LIKE 'event_link_hints'` 确认是否已迁移。迁移会计算存量记录并可能锁表，应在无执行中任务的维护时段运行。未迁移时查询兼容原扫描方式，但性能较慢；应用启动及 GET 查询不会隐式迁移。

`docker compose stop mysql` 可停止数据库；再次运行启动命令会使用原有数据。初始化账号和密码只在空数据目录首次启动时生效，修改 `.env` 不会自动修改已有数据库密码。

## Windows 本机启动

使用 Windows Python 3.11+、Node.js 24 和已启动的 Docker Desktop（Linux 容器）。项目放在 Windows 磁盘目录；不要复制 Linux 的 `.venv`、`node_modules` 或 MySQL 数据目录。

在项目根目录的 PowerShell 执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend/requirements.lock
.\.venv\Scripts\python.exe -m pip install -e ./backend --no-deps
npm.cmd --prefix web ci
powershell -ExecutionPolicy Bypass -File scripts/start-windows.ps1 -Initialize
```

首次启动前准备根 `.env`；使用独立的本地数据库和新密码。`-Initialize` 只接受没有任何表的空库，显式执行建表及迁移，不删除数据、不创建任务、不调用外部接口。后续启动去掉 `-Initialize`。脚本打开前后端两个窗口，页面为 <http://localhost:8000>；关闭窗口即可停止对应服务。切换前先停止旧 WSL 前后端和 MySQL，避免占用 8000、8001、3307。

文件锁支持 Linux 和 Windows，时区由 `tzdata` 提供。Windows 私有配置及 `data/` 的访问权限由目录 ACL 控制，应限定当前用户；POSIX 的 0600 权限数字不能替代 Windows ACL。迁移系统不代表数据库高频短连接问题已经解决，持续运行仍需单独验证。

## 网页与接口

在两个终端分别启动服务。以下命令从仓库根目录执行。

后端：

```bash
.venv/bin/python -m uvicorn dake.main:app --host 127.0.0.1 --port 8001
```

前端：

```bash
cd web
npm ci
npm run dev
```

打开 <http://localhost:8000>。前端将 `/api/` 请求代理到本地 `127.0.0.1:8001`。后端接口说明位于 <http://localhost:8001/docs>。默认两端均仅监听本机。

需要同一局域网查看时，在根 `.env` 的 `API_ALLOWED_HOSTS` 中保留本机地址并追加当前局域网 IP（逗号分隔，无端口），重启后端，在 `web/` 执行 `npm run dev:lan`。访问 `http://<本机局域网 IP>:8000`；网页的同源代理继续连接本机后端。WSL 镜像网络还需允许 Windows / Hyper-V 防火墙的入站 TCP 8000，范围限定为本地子网。恢复仅本机展示时改回 `npm run dev`。

`web/scripts/patch-umi-host.mjs` 在安装和启动时修复锁定的 Umi 4.7.3 开发服务器未将 `HOST` 传给监听调用的问题，只修改本项目依赖。升级 Umi 时需要重新核对该补丁；版本或上游代码不符时会停止启动，避免悄悄监听所有网卡。

- **事件**：按客户和六类主类型筛选，查看事件详情、消息证据和历次变化。
- **处理记录**：选择客户、日期范围、处理周期和数据读取方式，提交后台处理，查看逐轮新建、更新与待确认。刷新后可继续查看持久化记录。

变化面板首次仅加载已保存修订、关联执行状态和提交核对结果；展开“处理详情”后再加载模型输入及判断依据。有未结束的关联执行或任务时才自动轮询，其他情况可点击“刷新”。接口 `include_details` 缺省为 `true` 以兼容现有调用，网页首屏显式传 `false`；响应的 `details_included` 区分详情未加载和确实没有记录。

真实客户抽取需显式设置 `LLM_PROVIDER=xhub`；默认 `stub` 只验证处理链路，不生成分析事件。

聊天展示身份可在私有的 `data/config/sender_roles.json` 中配置，结构为 `{"accounts":[{"uid":"synthetic-customer","source":"official_chat","sender_id":"synthetic-service-account","role":"support"}]}`（示例账号均为虚构）。按客户、来源、账号 ID 精确匹配，已确认配置优先于接口原始标记；支持 `internal`、`support`、`customer`、`external`、`unknown` 五种展示角色。按用户确认的展示口径，未配置账号沿用原规则：`1` 在右侧显示“内部”，`0` 在左侧显示“客户”，未知不显示身份标签；已确认的我方客服在右侧单独标“客服”。接口标记的完整语义仍待确认，不将展示标签作为跟进人识别依据。配置在查询时读取，刷新页面即可生效，不改写消息、证据或事件，也无需重新清洗。真实账号只保存在 `data/`，不要写入源码或文档。

运行状态的“排队、处理中、完成、失败”属于程序执行状态，与官方尚待确认的事件状态分开。任务按周期真实执行，失败保留已完成的周期和脱敏错误。当前后台执行器为本机单实例执行，使用一个 Uvicorn worker（默认值），任务中心支持本机持久化自动任务，不提供分布式执行保证。命名锁用于防止误启动的实例同时领取任务；数据库连接异常时，业务事务仍依靠客户锁和消息幂等保护事件。

### 机器人身份与通知过滤

侧栏“机器人筛选”（`/settings/bots`）支持查找已同步发送账号、确认身份、限定客户／群，以及启用或停用规则。名称只作查找线索，实际匹配账号标识；含 MAAS 的名称不会自动被认定为机器人。配置保存后从后续处理窗口生效，已开始的窗口保持原规则，历史事件不会自动重算。并发编辑通过版本校验防止覆盖。

机器人身份配置保存在私有文件 `data/config/sender_kinds.json`，与 `sender_roles.json` 中的内部／外部角色独立。缺少配置时身份为未知，不根据昵称或外部标记过滤。配置示例全部为虚构内容：

```json
{
  "accounts": [
    {
      "uid": "synthetic-customer",
      "source": "official_chat",
      "conversation_id": "synthetic-alert-group",
      "sender_id": "synthetic-alert-account",
      "kind": "bot",
      "label": "虚构告警机器人",
      "basis": "管理员确认该群内的通知账号"
    }
  ]
}
```

`kind` 可配置为 `human`、`bot` 或 `unknown`，这是技术身份分类，不是事件等级。`basis` 必填；`label` 只便于维护，不参与匹配。`enabled` 默认为 `true`，设为 `false` 后该条规则不参与判断，但仍可能命中更宽的客户范围配置。同一个机器人名称可以对应多条群与账号配置。建议填写 `conversation_id` 限定群；省略时才对该客户、来源、账号的所有群生效。更具体的群配置优先。真实账号清单只放 `data/`，待确认清单可放 `sender_kinds.pending.json`，该文件不会生效。

- 仅有已确认机器人的新增消息时，不调用抽取器，不创建或更新客户事件；原消息仍保留，并记录已处理标记，防止重试重复分析。
- 有非机器人新增消息时，机器人通知移入辅助上下文。候选必须包含本轮非机器人证据；客户主动反馈无需等待内部回复。模型规则进一步要求明确问题、诉求或相关人工讨论，不能用无关问候为告警创建事件。
- 人工稍后讨论时，可以在当前上下文回看范围内引用先前通知；超过回看范围仍依赖后续历史检索能力，当前不承诺无限期取回。
- 页面通过独立 `sender_kind` 显示机器人身份，不再把已配置的机器人标成客户；未配置账号保持既有角色展示和未知技术身份。
- 每轮 `filter_audit` 保存消息键、处理原因、过滤版本和身份配置指纹，不保存额外聊天正文。`processed` 包括已处理的机器人通知，并不等同于模型实际分析量。审计随周期结果保存，沿用现有快照／任务记录的恢复边界。

配置在每轮开始读取并冻结，查询展示时重新读取。过滤策略采用独立版本，模型记录保存实际系统提示词哈希；不更换已有事件的分析流，也不改变原始消息 digest。新增配置不会自动重洗已处理消息或删除旧事件；历史误识别需另行复核。配置格式错误会停止处理，不静默忽略。

当前提供本地文件配置，设置页面尚未实现。若需要重新评估历史通知，必须另行指定范围，不能通过修改账号配置隐式触发全量分析。

## 工程目录

| 目录 | 内容 |
| --- | --- |
| `backend/src/dake/api/` | FastAPI 接口与请求响应结构 |
| `backend/src/dake/services/` | 摄入、单周期增量、范围回放、查询 |
| `backend/src/dake/ingestion/` | 官方客户端与数据适配器 |
| `backend/src/dake/domain/` | 统一消息与事件定义 |
| `backend/src/dake/analysis/` | Provider、校验、合并、匹配和更新 |
| `backend/src/dake/repositories/` | MySQL、运行记录、水位与快照 |
| `backend/src/dake/jobs/` | 后台处理执行器 |
| `backend/tests/` | 业务、接口和存储测试 |
| `web/` | React / Umi 管理端 |
| `docs/` | 项目约束、架构与生成的接口定义 |
| `data/` | 本地私有数据，不提交 |

## 命令行运行

以下命令均在项目根目录执行。省略客户、日期和周期参数时读取 `.env`；单次命令可用参数覆盖。

### 取数与回放

取数只调用官方接口，将原始响应保存到 `data/raw/`，并通过适配器将消息归一后存入 MySQL，不做事件分析：

```bash
.venv/bin/dake fetch --uid 1383603926 --start 2026-11-08 --end 2026-11-12
```

已有对应日期的本地响应后，可以离线逐日回放五轮：

```bash
.venv/bin/dake replay --uid 1383603926 --start 2026-11-08 --end 2026-11-12 --interval 1d --offline
```

去掉 `--offline` 会通过官方接口取数。`fetch`、`process`、`replay` 都支持 `--offline`；该参数只控制官方取数使用缓存；缺少缓存时不会补抓。若配置为 XHub，抽取仍会调用模型；完全离线验证应使用 stub。

配置缺省为 `stub`，仅验证链路，不生成分析事件。获授权后在根 `.env` 设置 `LLM_PROVIDER=xhub`，配合 `XHub_API_KEY`、`XHub_BASE_URL` 和 `XHub_MODEL` 启用真实抽取。当前本地按用户要求使用 `gpt-6.1-sol`；不要把密钥写入命令行或文档。正式归属合约位于 `backend/src/dake/analysis/attribution.py`，基础字段校验位于 `analysis/contract.py`。

### 单个处理周期

```bash
.venv/bin/dake process --interval 15m
```

每次只执行一个窗口，然后退出。默认从 `data/state/` 的水位继续；首次运行从配置起始时间开始。`--start` 可以显式指定起点，`--end` 是本次允许处理到的截至时间。处理到配置结束时间后，需要更新结束时间才能继续。

例如，处理一个明确的 15 分钟窗口：

```bash
.venv/bin/dake process --start 2026-11-11T09:00:00 --end 2026-11-11T09:15:00 --interval 15m --offline
```

当前没有自动定时执行；后续调度接入同一业务服务。

在线处理通过统一窗口来源按分钟取数，再按上海时区 `[start, end)` 过滤；日期参数及 `1d` 回放继续可用。分钟原始响应保存在 `data/raw/official_chat/windows/`，缓存身份区分请求范围及 UID。离线单客户处理优先读取对应窗口缓存，缺少时兼容 `data/raw/<uid>/<日期>.json`；已有窗口缓存无效时直接报错。

来源层支持不传 UID 获取跨客户统一消息。客户页面已支持手动发现和资料同步，任务中心统一编排多客户手动及自动处理，底层分析按客户复用同一业务服务。旧的单客户网页提交接口已移除。

### 客户资料与手动同步

完成 `003_customer_directory.sql` 迁移并重启后端后，访问 `/customers`。选择上海时间范围（最多一天）并点击“开始同步”，后台按配置周期（默认 `15m`）的窗口从跨客户聊天发现群及 UID，补查缺失归属，查询月销等级和负责人。页面可按 UID 查询客户、查看关联群、待确认原因，以及刷新后继续查看同步进度。

客户同步复用 `processing_runs` 和现有后台工作线程，不调用模型，也不向消息、事件表写入分析数据。同一时间只允许一个客户同步任务。重复同步更新已有客户及群，窗口内未出现的群继续保留；查询失败保留上次成功资料并标记过期，部分查询失败时任务显示“未全部完成”。群归属冲突保留原映射及变更历史，暂停自动使用，不根据名称相似自动改绑。

客户页面默认查看大客户（L6～L10），可切换非大客户（L0～L5）、等级待确认和全部客户，并与 UID 搜索组合。等级未知、无效或刷新失败的客户列入等级待确认，旧等级仅供参考；TAM 缺失不影响月销分类。筛选在后端分页前完成，总数对应筛选结果，切换范围会返回第一页并清除所选客户，刷新保留范围。

`GET /api/v1/customers` 支持 `segment=large|non_large|pending|all`，API 缺省仍为 `all` 以兼容原调用。每条记录返回 `segment`。分类规则集中在 `domain/customer_policy.py`，不按等级字符串排序比较，不需要新增数据库迁移。

本轮提供客户资料、等级筛选和待确认结果展示，没有人工归属确认提交、模型处理准入开关或定时调度。首次发现客户不代表已允许将其聊天送入模型。客户接口为 `/api/v1/customers`、`/api/v1/customers/groups` 和 `/api/v1/customers/syncs`；资料查询均为本地只读，只有提交同步才访问官方接口。

### 输出位置

控制台只显示时间、类型、事件 ID 和计数，不打印聊天原文或事件概述。完整结果统一保存在已忽略的 `data/` 内：

| 路径 | 内容 |
| --- | --- |
| `data/raw/` | 官方接口原始响应 |
| `data/mysql/` | 本项目 MySQL 数据 |
| `data/state/` | 下次处理窗口的本地水位 |
| `data/snapshots/` | 每轮事件快照和创建、更新记录 |
| `data/run-results/` | 任务周期结果日志，用于进度写入中断后的恢复 |

快照可能含有证据和事件概述，应与原始聊天一样保留在本地，不写入 README、测试期望值或提交说明。

## 配置约定

配置默认从仓库根目录 `.env` 读取，示例见 `.env.example`。从 `backend/` 启动也使用同一 `.env` 和 `data/`；显式 `--env-file` 可指定其他配置文件，但数据仍限制在项目 `data/` 下。`PROCESS_UID`、`PROCESS_START`、`PROCESS_END` 控制客户与日期范围；`PROCESS_INTERVAL` 控制处理周期。上下文回溯、事件匹配、关闭宽限和补漏回溯分别通过 `CONTEXT_LOOKBACK`、`MATCH_WINDOW`、`CLOSED_GRACE`、`INGEST_LOOKBACK` 配置，不随周期自动改变。

业务时间统一为 `Asia/Shanghai`，内部窗口采用 `[start, end)`。日期形式的 `--end 2026-11-12` 包含当天整日；精确时间形式的 `--end 2026-11-12T09:15:00` 不包含该端点。接口按日期取数，重叠读取用于补漏，消息以 ID 去重，抽取器只看到本周期新增消息及此前的上下文。

`DEFAULT_STATUS`、`DEFAULT_SEVERITY` 默认均为「未知」，`CLOSED_STATUSES` 默认留空。这些规则仍待官方确认，不能把开发缺省当成正式枚举；确认后的关闭状态可用逗号分隔配置。

`LLM_PROVIDER=stub` 用于验证增量链路。真实模型应在确认使用范围后接入，不应只通过填写外部密钥就发送真实聊天。

## 验证

单元测试使用合成数据：

```bash
.venv/bin/python -m pytest -q backend/tests
```

本地数据库启动后，可启用 MySQL 集成测试：

```bash
RUN_MYSQL_TESTS=1 .venv/bin/python -m pytest -q backend/tests
```

前端检查（先启动后端和前端，再执行浏览器测试）：

首次执行浏览器测试前，在 `web/` 运行 `npx playwright install chromium`。本机已有兼容的 Chromium 时，也可通过 `DAKE_BROWSER_PATH` 指定浏览器可执行文件。截图默认关闭；需要合成演示截图时，将 `DAKE_SCREENSHOTS` 指定到项目 `data/screenshots/`。

```bash
cd web
npm run lint
npm run build
npm test
```

本地接口单次范围最多 31 天、2048 个周期，等待和执行中的任务合计最多 32 个；运行列表展示最近 200 条。这些技术上限集中在 `backend/src/dake/config.py`，不属于官方业务规则。

更新接口后，重新导出定义并生成前端客户端：

```bash
.venv/bin/python scripts/export_openapi.py
cd web
npm run openapi
```

`docs/api/openapi.json` 从后端代码生成，不包含真实聊天响应；`web/src/services/generated/` 由生成器维护，不手工修改。

更多参数可通过 `.venv/bin/dake --help` 或对应子命令的 `--help` 查看。

原始聊天数据和密钥不要提交进仓库。

## 任务中心

`/tasks` 提供任务配置、手动执行、启用／暂停、执行进度和最近 100 次记录。导航顺序为工作台、客户管理、任务中心，旧 `/runs` 跳转任务中心，逐轮明细由任务中心执行明细进入。

默认按 L6–L10 筛选，可选择等级、指定客户及排除客户／群。每个消息窗口先同步聊天和资料，再分析符合条件且群归属已确认的消息，保存结果后继续下一窗口。资料有效期、补漏回溯、检查频率、消息窗口、重试分别配置，默认每 5 分钟检查、每个窗口 15 分钟。手动范围最多 31 天；自动任务每次只推进一个窗口，历史积压连续追赶，追平后按检查频率等待。全等级范围分页读取全部客户，指定客户输入最多 500 位。

保存新配置不会启动任务；自动任务需点击启用。暂停在当前窗口完成后生效。后台服务存活期间无需保持网页打开；配置、游标、当次配置快照与执行结果保存到 MySQL，重启后继续读取。失败客户依据数据库待处理消息从未成功窗口续跑，其他客户继续推进；已有后续事件时，迟到消息放入当前窗口处理。提交结果未核实的客户跨定时执行保留失败状态，不能自动重新发送模型请求。数据库新增表由 `004_task_center.sql` 在服务启动时幂等创建。运行状态与官方事件状态独立。

任务中心分析已同步聊天时，会按客户范围排除不符合条件的客户，并批量跳过没有待处理消息的连续窗口。跳过结果在逐轮记录中明确标记，不创建模型分析尝试或事件快照；有消息的窗口仍保持原时间边界。迟到消息、未核实提交以及历史中断恢复范围不走该快速路径。实时取数阶段不能依据本地消息为空而跳过官方接口。

Linux / WSL 可用用户服务保持本地后端运行：先执行 `.venv/bin/python scripts/install_local_backend_service.py` 注册，再退出此前手动启动的后端，执行 `systemctl --user start dake-aftersales-backend`。服务异常退出后自动重启。通过 `systemctl --user status dake-aftersales-backend` 查看状态，`systemctl --user stop dake-aftersales-backend` 停止。系统或 WSL 关闭期间无法运行，重新启动后从持久进度继续。


### 本地事项定级与计时预览

`python -m dake.cli preview-priority --uid <UID> --start <起点> --end <终点>` 显式使用已入库且在既有授权范围内的聊天。配置 `LLM_PROVIDER=xhub` 后，模型评估和处理标记使用独立预览流。终端显示该流及批次计数。

计时预览前在本项目的隔离 MySQL 库执行 `backend/migrations/006_item_priority_deadline.sql` 一次。迁移给事件增加可空到期索引，存量事件保持空值。预览可加 `--calendar <本地日历文件>`。例如 `{"weekdays":[0,1,2,3,4],"opens":"09:00","closes":"17:00","holidays":[]}` 是合成日历，星期一为 0，时段使用上海时间。`python -m dake.cli check-priority-deadlines --calendar <本地日历文件>` 扫描一批到期事项。后台服务显式设置 `PRIORITY_SERVICE_CALENDAR` 后，协调线程也会扫描已投影的到期事项。

合成告警验收先在隔离库执行 `backend/migrations/007_alert_preview.sql`。启动后端前在 `.env` 设置 `SYNTHETIC_ALERT_SOURCE=local-fixture`，并在机器人筛选中确认该测试客户和群的发送账号为机器人。将合成消息写入隔离库，再从任务中心创建常规任务，选择已同步聊天、测试客户、授权群和对应时间窗。运行前完成配置，确保首次处理窗口使用该来源。未配置来源时，仍按常规机器人通知规则处理。

消息正文为 JSON，必填 `schema`（`dake.synthetic-alert-v1`）、`source`（与配置相同）、`alert_id`、`round_id`、`kind`（`trigger` 或 `recovery`）和 `severity`。当确认受影响客户时，`affected_uid` 须与当前上报客户 UID 一致。同一 `source`、`alert_id`、`round_id` 汇总到一个告警轮次。独立告警使用不同的 `round_id`。已识别的告警与恢复从事项模型的当前输入及旧聊天背景中排除。`SYNTHETIC_ALERT_REMINDER_MINUTES` 可设置重复预览间隔。提醒预览与轮次同事务保存，先收到恢复时，补到更早的触发仅生成按事件时间记录的历史首次预览，轮次保持已恢复。此入口只生成预览。
