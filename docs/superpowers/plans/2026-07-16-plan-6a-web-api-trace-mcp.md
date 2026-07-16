# Plan 6A — Replay-first Web / API / Trace Viewer / MCP

> **状态（2026-07-16）**：**DONE / ACCEPTED**。本计划是完整 Plan 6 的第一个可交付薄纵切；
> 软件合同、自动化门和用户视觉验收均已关闭，闭门证据见第 8 节和
> [`PLAN6A_ACCEPTANCE.md`](../../PLAN6A_ACCEPTANCE.md)。完整 Plan 6 的音频、
> AlphaTab/真实琴颈动画、现场 A/B/榜单、导出互操作与真人 money moment 仍保持 open，不能用
> Plan 6A 的完成冒充。

**Goal:** 把已经闭门的 MusicXML/MXL → agent/solver/oracle 后端变成一个可从浏览器和 MCP
客户端真实调用、可回放“诊断 → 定点编辑 → 重查”过程的产品纵切，同时保持核心包无 Web/MCP
依赖、所有不可信边界 fail-closed，并且不制造音频、实时流式或人体保证等尚不存在的能力。

**Architecture:** 一个不依赖 FastAPI/MCP 的 application service 统一 bytes importer、pipeline、
oracle 与序列化；FastAPI 和 MCP 只做有界 transport adapter。Trace 升为公开、版本化、可回放的
checkpoint contract；React/Vite 只消费公开 API，不复制 checker 逻辑。离线 deterministic engine 是
默认值，proxy engine 只有服务启动时显式授权才可用。

## 1. 范围冻结与版本

### 1.1 本计划交付

- package 从尚未发布的 `0.2.0` 升到 `0.3.0`；已有 oracle/importer/container/fidelity/input/profile
  版本不因 adapter 新增而改义。
- `fretsure-service@0.1.0`：bytes-first import + arrange/check/render 的唯一 application seam。
- `agent-trace@0.1.0`：有 schema/version/index、结构化诊断、人话、edit 与有界 checkpoint 的公开
  trace。
- `fretsure-api@0.1.0`：capabilities、arrange 与 oracle check 的 typed HTTP API，加同源静态 Web。
- `fretsure-mcp@0.1.0`：stdio 为默认 transport 的 oracle interoperability adapter。
- `fretsure-web@0.1.0`：上传当前支持的 `.musicxml/.xml/.mxl`，显示独立 playability / faithfulness
  门、ASCII tab 与 trace 回放。
- 用户参与的视觉迭代：先交付完整第一版和真实浏览器截图，再按反馈迭代；没有用户明确认可，
  Plan 6A 不闭门。

### 1.2 明确延后，不是假实现

- MIDI、mp3/audio frontend、FluidSynth、播放同步与 `render_audio`。
- AlphaTab、GP/MusicXML/MIDI 导出、真实琴颈动画与分叉/时间旅行编辑器。
- live A/B、live leaderboard、benchmark v2、OTel/Langfuse deployment。
- WebSocket/SSE、后台队列、数据库、账户、云部署、多租户和任意远程 URL 导入。
- LangGraph/Agent SDK：设计真源后续已冻结为自研 harness；框架只可能作为未来对照，不是
  Plan 6A 运行时依赖。
- 真人 calibration、真实琴手保证与完整 money moment；UI 只能准确陈述 versioned model-relative
  GREEN，不能写“任何真人一定能弹”或“100% playable”。

## 2. 安全与产品不变量

1. Web 上传使用 raw streamed request body；禁止 `UploadFile`、multipart spool、临时文件与路径回读。
2. application seam 只接受 exact built-in `bytes` 和 inert filename；先检查格式/体积，再解析 XML、
   ZIP、JSON 或调用 LLM。
3. path importer 只负责 bounded read，之后与 bytes importer 走完全相同的解析语义；API、CLI 与
   MCP 不维护第二份 importer/checker。
4. HTTP body、Tab JSON、target JSON、query controls、trace/checkpoint 各自有显式硬上限；bool 不当
   int，NaN/Infinity、duplicate key、unknown field 与 coercion 全拒。
5. API/MCP 默认 deterministic offline engine。只有 `fretsure-serve --allow-proxy` 才公布并允许
   `gpt-5.6-sol`；请求方不能传任意 model/base URL/key。
6. public trace 不保存 system/user prompt、raw model reply、transport exception、secret 或 traceback。
   只保留稳定 reason category、结构化 edit、checker diagnostics 和 replay checkpoint。
7. 所有浏览器字段按 plain text 渲染；禁止 `dangerouslySetInnerHTML`/raw HTML 注入。
8. `GREEN` 与 faithfulness 是两个独立门；`INFEASIBLE` 不是 checker verdict；bounded solver failure
   不宣称数学不可解。
9. MCP 是互操作 adapter，不替代 agent 热循环里的进程内 oracle 调用。
10. core-only 安装继续 `import fretsure`；未安装 optional extra 时不得因顶层 import 拉入 FastAPI、
    Pydantic、Uvicorn 或 MCP。

## 3. Task 1 — Bytes-first importer 与 application service

**Files:**

- `src/fretsure/importers/musicxml.py`, `src/fretsure/importers/__init__.py`
- `src/fretsure/application/contracts.py`, `src/fretsure/application/target.py`
- `src/fretsure/application/serializers.py`, `src/fretsure/application/service.py`
- `tests/importers/test_musicxml_bytes.py`, `tests/application/*`

**Work:**

- 新增 `import_musicxml_bytes(data, filename, limits=...)`；要求 exact bytes/str，filename 只取 inert
  basename identity，拒 NUL、控制符、空名、路径段、unsupported suffix。
- `_read_bounded(path)` 以 opened fd 的 `fstat` 做 regular-file/identity/size 检查，并在读取后复核
  concurrent mutation；随后调用同一 bytes implementation。保留 path 特有的
  `FILE_NOT_FOUND/NOT_A_FILE/FILE_READ_ERROR` typed failures；如果某平台只能证明较窄保证，文档必须
  准确写窄，不能把普通 `stat→open` 说成完整 TOCTOU 防护。
- application service 定义纯 Python immutable result contracts，精确入口为
  `arrange_score_bytes(data: bytes, *, filename: str, options: ArrangeOptions,
  llm: LLMClient) -> ArrangeOutcome`；transport 的 engine string 只在 adapter 选择固定 factory，
  不进入 core。统一能力为：
  - `capabilities()`；
  - `arrange_score_bytes(...)`；
  - `check_tab_json(...)`；
  - `solve_target_json(...)`（MCP 的有界 solver tool，wire schema=`target-input@0.1.0`）；
  - `render_tab_json(...)`。
- arrange outcome wire 字段冻结为 `status=tab_produced|no_fingering_within_budget`、source/import
  provenance、score summary、effective options、
  engine/model、canonical Tab object 或 null、ASCII 或 null、playability 或 null、faithfulness 或 null、
  trace 与全部 stamps；no-fingering 是 typed outcome 而不是异常。其他结果同样使用 typed absence，
  不伪造空 GREEN。
- profile 只接受冻结 registry 名称；第一版公开 `median`，为未来 small/large 保留可版本化 registry，
  不允许请求构造任意人体参数。

**TDD / acceptance:**

- 同一 fixture 的 path/bytes importer IR、warnings、hash/provenance 完全一致。
- exact-type、恶意 filename、超限 raw XML/MXL、dependency missing、DTD/entity/external resource、ZIP
  red-team 与 mutation/TOCTOU 回归均 typed fail，且 bytes path 不触盘。
- service 与 CLI 对同一 fixture/controls 得到同一 Tab、verdict、faithfulness 与 trace bytes。
- invalid controls/Tab/target 在 importer/parser/LLM/solver 工作前失败；错误无 traceback/raw exception。

## 4. Task 2 — `agent-trace@0.1.0` replay contract

**Files:**

- `src/fretsure/agent/trace.py`, `src/fretsure/agent/repair.py`
- `src/fretsure/agent/harness.py`, `src/fretsure/difficulty/simplify.py`
- `src/fretsure/application/serializers.py`
- `tests/agent/test_trace.py`, `tests/agent/test_repair.py`, `tests/application/test_service.py`

**Wire row:** 每行 canonical JSONL 精确包含 `trace_schema_version`, zero-based contiguous `seq`,
`kind`, stable `event`, `candidate_index`, `iteration`, `detail`, `data`；`kind` 仍来自冻结集合，
消费者按 `event` 分支而不解析 `detail`。服务 JSON 用 `{schema_version, steps}` 包同一 row array，不另造
Web schema；空 trace 仍有版本。

**Structured data:**

- 每个 proposal/repair step 带 `candidate_index`、`iteration` 与 stable phase identity。
- oracle diagnostics 逐条包含 measure、fraction beat、violation type、note indices、finite overage、
  原 checker 给出的 bounded `suggested_relaxations` strings，以及由 presentation formatter 产生的稳定
  人话；不把现有自由字符串悄悄升级成 code，人话也不扩大 checker 主张或臆造 overage 单位。
- infeasible 包含 stable code/onset/pitches 和安全、预定义说明；不暴露任意 exception。
- edit 包含 status (`applied/rejected/unparseable/noop`)、op/onset/pitch/arg 与 stable reason code；不存
  raw LLM reply。
- 每次 solve/recheck 提供有界 candidate checkpoint：冻结为最多 512 notes 且 128 KiB canonical
  bytes；超出任一门时只给 note count、SHA-256 与 `omitted_reason`。另有 512 KiB deterministic
  aggregate embedded-state budget，按 seq 消耗；耗尽后完整 state 全部省略并明确 `TRACE_BUDGET`，
  不输出会冒充可回放状态的 partial prefix。全 trace 仍受既有 10 MiB encoder preflight 保护。
- terminal/select 行明确 winner identity、bounded result 与两道独立 gate 状态。

**Redaction / determinism:**

- LLM transport failure 映射 stable `LLM_TRANSPORT_FAILURE`；parse/melody/no-op 等映射固定 reason。
- `Trace.add` 在 snapshot 时复制数据；后续 mutation 不改变已记录证据。
- 所有 row 仍通过现有 depth/node/scalar/output preflight；新增字段计入同一 10 MiB JSONL 总门。
- 两次 deterministic run byte-identical；schema constant 进入 API capabilities 和文档。

**Acceptance:** 至少一个可控 fixture 的公开 trace 能证明
`PLAN → PROPOSE → SOLVE/ORACLE diagnostics → REASON category → EDIT → RECHECK → SELECT`；测试验证
诊断、edit 前后 checkpoint 与最终 result 对得上，而不只是检查 kind 是否存在。

## 5. Task 3 — Typed FastAPI adapter

**Files:**

- `src/fretsure/api/app.py`, `src/fretsure/api/body.py`, `src/fretsure/api/problems.py`,
  `src/fretsure/api/cli.py`
- `tests/api/*`
- `pyproject.toml`, `uv.lock`

**Optional extra:** `service = [fastapi>=0.139,<0.140, uvicorn[standard]>=0.41,<0.42]`。HTTP tests 的
`httpx` 仅进 dev。版本锁若当前生态实际兼容性要求更窄，以最终 lock/test 证据为准并记录。

**Endpoints:**

- `GET /api/v1/capabilities`：API/trace/service/package versions、supported input suffix/MIME、limits、
  engines（含 availability/model）、profiles、controls 的 defaults/min/max、checker stamps、
  implemented/deferred capabilities。Web 只能以此为配置真源。
- `POST /api/v1/arrangements?filename=...`：raw MusicXML/MXL bytes；显式 bounded controls 为
  `engine`, `n`, `max_iters`, `use_critic`, `tempo_bpm`。HTTP adapter 首版把核心允许的 64×64
  进一步收紧为 `n<=8`, `max_iters<=16`，以保证交互延迟与公开 trace 体积；返回完整 service result。
- `POST /api/v1/oracle/check?profile=...&tempo_bpm=...&beats_per_bar=...`：raw strict Tab JSON；
  返回 verdict/diagnostics/stamps。
- `GET /healthz` 仅说明进程存活，不冒充 dependency/readiness/质量证明。
- `/` 与 SPA fallback 只服务 wheel 内构建产物；`/api`、`/mcp`、未知 asset 不得被 SPA 吞掉。

**Boundary behavior:**

- 对 request stream 逐 chunk 计数，在 `max+1` 立即停止；拒已声明超限 `Content-Length`、冲突/
  非法 length、unsupported content type/suffix。chunked 与 declared 两条路径同门。
- query 使用明确 wire grammar，不依赖宽松 truthiness/coercion；unknown query field 拒绝。
- 所有失败为 `application/problem+json`：`api_version`, HTTP status, stable `code`, safe `title/detail`,
  optional typed diagnostics；production response 永不含 exception class、stack、path 或 secret。
- 状态映射冻结为：framing/query grammar `400`，body/resource envelope `413`，suffix/MIME `415`，
  MusicXML/Tab/target/options semantic rejection `422`，缺 optional runtime dependency `503`；proxy 未授权
  是 stable `PROXY_DISABLED`，不泄漏环境变量。
- app factory 的 proxy permission/LLM factory 是 immutable startup config；tests 可注入 inert factory。
- 默认 host `127.0.0.1`；若未来绑定非 loopback，文档明确 deployment 必须自行提供 auth/TLS/body
  limits，本计划不声称是公网多租户服务。

**Acceptance:** API/CLI/service fixture parity；deterministic double run；raw chunked/declaration oversize、
bad suffix/MIME/filename/JSON/query、LLM failure 与 internal-boundary regression 全为 typed response；
OpenAPI 可生成且不宣称 deferred capability。

## 6. Task 4 — MCP interoperability adapter

**Files:**

- `src/fretsure/mcp/server.py`, `src/fretsure/mcp/cli.py`
- `tests/mcp/*`
- `pyproject.toml`, `uv.lock`

**Optional extra:** `mcp = [mcp>=1.28.1,<1.29]`。当前 v1.28 没有公开的 server implementation
version setter，因此收窄到已验证 minor，并由 initialize 回归锁定 Fretsure 身份；升级前必须复核该适配点。

**Surface:**

- `check_playability(tab_json, profile, tempo_bpm, beats_per_bar)`：与 service/core 相同 typed result。
- `feasible_fingerings(target_json, profile, tuning, capo, tempo_bpm, beam)`：严格、有界
  `target-input@0.1.0`（canonical Fraction `num/den`、exact fields、duplicate/unknown/coercion fail），
  调用 public `solve_fingering`，首版最多返回一个已复核的非 RED fingering。响应冻结
  `search_complete=false`, `max_solutions=1`, `status=found|not_found_within_budget`；工具描述与响应都
  明确 bounded search 可能 false-negative，不是完整枚举或不可解证明。禁止直接暴露指数级 CSP
  generator。
- `render_notation(tab_json, format="ascii")`：首版只接受 `ascii`；unknown format typed reject。
- capability resource 暴露与 HTTP 同源的版本/limits/deferred 项。
- 不注册假的 `render_audio`；capabilities 明确 `render_audio: deferred`。

**Transport:** `fretsure-mcp` 默认 stdio，protocol stdout 必须无日志/banner；诊断仅 stderr。未来
HTTP mount 可复用同一 server，但不作为 Plan 6A 的默认或独立公网承诺。

**Acceptance:** official in-memory session 完成 initialize/list/call；真实 subprocess stdio 完成握手与三
个 tool 调用，stdout frame 可解析且无污染；同一 Tab/target 与 core/service 完全一致；invalid/oversize
输入返回 tool error 而不退出 server。至少记录一次 Claude Desktop/Cursor-compatible 配置格式；若缺少
用户本机客户端，不把未实际点击的 GUI 说成已验证。

## 7. Task 5 — `fretsure-web@0.1.0` 与审美迭代

**Files:**

- `web/package.json`, `web/package-lock.json`, `web/vite.config.ts`, `web/tsconfig*.json`
- `web/src/*`, `web/tests/*`
- generated `src/fretsure/web_static/*`
- `tests/api/test_static.py`

**Stack:** React 19 + TypeScript + Vite + Vitest/Testing Library；依赖使用 exact lock。AlphaTab 在需要
真实 notation/playback 的完整 Plan 6 再引入，Plan 6A 不用占位依赖冒充功能。

**Product flow:**

1. landing/upload：清楚说明当前支持的 MusicXML 子集与 `.mxl` container；drag/drop 与 file picker。
2. bounded controls：offline/proxy capability、candidate/repair budget、critic、tempo；默认值来自 API。
3. result：标题/provenance、ASCII tab、独立 playability card、独立 faithfulness card、版本 stamps。
4. replay：左侧 step timeline，右侧 checkpoint/诊断/edit；能逐步查看 red/amber/green、原始 typed
   data 与稳定人话，不伪装 chain-of-thought。
5. loading/empty/typed failure/retry/unsupported/deferred 状态均有真实设计，键盘与窄屏可用。

**Visual process (human gate):**

- 第一版必须是完整、有主张的视觉方向，不是默认组件库拼装；包含桌面与窄屏真实数据截图。
- 用本地浏览器逐页验证交互、console、network、focus、overflow 和 XSS fixture。
- 把截图交给用户，记录具体反馈与每轮改变；审美迭代可改 typography/color/spacing/composition/
  motion，但不能删掉安全、可访问性、诊断可读性或产品合同。
- 只有用户明确表示喜欢/认可后才勾选视觉验收；该 gate 是 Plan 6A 的真人阻塞点。

**视觉验收记录（2026-07-16）：** 用户在查看桌面与移动端真实浏览器结果后明确回复：
“这个前端做的挺好看的，就按照这种带点古典的风格来，我审核通过了”。最终截图：
[桌面入口](../../assets/plan6a/desktop-landing.jpg)、
[桌面结果](../../assets/plan6a/desktop-result.jpg)、
[桌面 trace](../../assets/plan6a/desktop-trace.jpg)、
[移动入口](../../assets/plan6a/mobile-landing.jpg)、
[移动结果](../../assets/plan6a/mobile-result.jpg)。

**Frontend acceptance:**

- unit/component tests 覆盖 upload、control serialization、result/gate、timeline replay、failure 和 XSS；
  不用 snapshots 代替关键语义断言。
- `npm test`, `npm run typecheck`, `npm run build` 全绿；build 后 worktree 不漂移；production bundle
  无 source secrets，`npm audit` 无 high/critical（若生态误报只能带可复核说明，不能静默忽略）。
- 静态资源从最终 wheel clean install 可访问；CSP/安全 headers、MIME、cache 语义与 SPA fallback 有测试。
- 真浏览器无 console error；键盘主路径、reduced motion、desktop/mobile overflow 与 hostile metadata
  plain-text regression 通过；用户视觉验收有记录。

## 8. Task 6 — Docs、交付与闭门证据

**Files:**

- `README.md`, `CLAUDE.md`, `docs/PROJECT_STATE.md`, `docs/DEMO_SCRIPT.md`
- 本计划与必要的 API/MCP usage 文档
- `.github/workflows/ci.yml`, `.gitignore`, build manifests

**Required gates:**

1. 全量 Python：离线和真实 `gpt-5.6-sol` proxy 两组 pytest；ruff；strict mypy；lock check；
   `git diff --check`；Markdown local-link checker。
2. 前端：clean `npm ci` 后 test/typecheck/build/audit；生成 asset 与 source 同步。
3. 真实行为：MusicXML 与 MXL 各一条 API path；trace 含真实 diagnostic→edit→recheck；MCP in-memory
   与 subprocess stdio；浏览器 desktop/narrow screenshots、无 console error。
4. package：最终树重建 wheel/sdist 并审计内容；clean core、`[musicxml]`、`[service,musicxml,agent]`、
   `[mcp]`（及必要组合）venv smoke；core-only 不出现 adapter dependency。
5. provenance：API/trace/MCP/CLI 都盖实际 `gpt-5.6-sol` 或 deterministic stub model id 与所有相关
   checker/schema/profile hashes；历史 benchmark 数不回写。
6. 文档：只声明已经实测的支持面；完整 Plan 6 checkbox 保持 open，列清 deferred 与真人限制。
7. git：所有验收证据写回计划/状态/实验记录；一个可审查提交，不带 AI coauthor trailer；push 到
   `origin/codex/sequential-plans`，远端 SHA 与本地一致后才允许开启 producer-driven MusicXML/IR。

### 8.1 最终闭门证据

- Python 收集 `1500` 个测试；离线组 `1494 passed, 6 deselected`，真实 `gpt-5.6-sol` proxy 组
  `6 passed, 1494 deselected`。真实 HTTP arrange 返回 `tab_produced`、6 个 trace steps，并盖实际
  model id；该次独立双门为 playability `AMBER` / faithfulness `PASS`，前端不会误标为双门通过。
  唯一 warning 来自 FastAPI/Starlette TestClient 对 httpx 2 的上游迁移提示。
- ruff、strict mypy、lock、diff、Markdown local links 全绿；trace chronology、失败路径回放、双 gate
  terminal state、预算和 redaction 经过独立验收/安全审计。
- 前端 clean install 后 `20 passed`，typecheck/build/audit 全绿；桌面与 375 px 移动端浏览器完成上传、
  结果、trace、键盘 focus、overflow、CSP/XSS 与 network/console 检查。
- MCP 通过 in-memory initialize/list/call 与真实 stdio subprocess 三工具握手；API、MCP、CLI、service
  使用同一 application seam 和序列化合同。
- 最终 wheel/sdist 内容审计与 core、MusicXML、service、MCP clean-install smoke 全绿。命令、边界、
  配置与限制详见 [`WEB_API_MCP.md`](../../WEB_API_MCP.md)，完整明细见
  [`PLAN6A_ACCEPTANCE.md`](../../PLAN6A_ACCEPTANCE.md)。
- 三轮独立审计发现的 trace 顺序/失败态、HTTP Host/Origin/proxy 防护、OpenAPI response schema 与前端
  response validation 问题均已修复并回归；没有未关闭 blocker。

## 9. Plan 6A 闭门清单

- [x] Task 1 bytes/application seam 及 parity/security matrix 完成。
- [x] Task 2 replay trace contract 完成，真实 diagnostic→edit→recheck 证据成立。
- [x] Task 3 FastAPI endpoints、typed boundary 与 static serving 完成。
- [x] Task 4 MCP stdio tools/handshake/parity 完成。
- [x] Task 5 Web 行为、浏览器与 package gates 完成。
- [x] 用户明确认可 Plan 6A 的视觉方向。
- [x] Python/frontend/proxy/package/docs 全门最终树重跑并记录。
- [x] 由包含本文件的闭门提交推送，local/remote SHA 一致。

## 10. 后继顺序

Plan 6A 闭门后，严格按当前状态真源进入 producer-driven MusicXML/IR 扩展，再做 MIDI、benchmark
v2；完整 Plan 6B 在这些底层产物成熟后补 AlphaTab、真实琴颈动画、音频、导出互操作、live A/B/
榜单与 money moment。若真人演奏/审美 gate 阻塞某个后继，则停在该 gate 等用户，不用软件替身假过。
