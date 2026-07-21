# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成**人手可证明弹得出来**的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 逐音把关并修复 → checker 打分 benchmark"。

**当前恢复真源（2026-07-21）**：Task 9 formal attempts 001–003 均已 terminal
`INCOMPLETE`，不得 resume、覆盖或复用编号；下一次正式采集只能是 fresh attempt-004。
attempt-003 在 503 个 pure-solver rows 后暴露系统性的 30 秒 request timeout，并以
`523/10,563` rows、78 logical calls / 113 provider attempts、`$0.955113 / $359.791113`
known/tight cost 终止。三次累计 known/tight 为 `$2.130022 / $804.234022`；加一个完整
formal attempt 后的累计审计上界为 `$1,168,709.874022`。attempt-004 前须推送 300 秒整次
attempt 硬 deadline（另含 10 秒/attempt 记录开销）、
默认 4-lane 的 operational amendment；analysis-excluded pilot 按 `2 → 4 → 8` 运行，只有
`4` 与 `8` 各至少八个完整 block（各 64 units）并经独立确认后才能选择 `8`，否则保持 `4`。
正式进程 detached 运行，按 durable-unit 边界恢复，progress 只进 append-only operator log；
formal/pilot 在建客户端前要求数值 loopback，拒绝 `localhost`；运行时不调用 Git 或子进程。
普通 full stub A/B 只验证报告/字节，4-lane 恢复另由 provider-free
`scripts/task9_operational_stub_gate.py` 对完整 schedule 验证；两类最终 amended A/B 均已通过，
4-lane A 在 admitted 284 时唯一一次 `SIGINT` 后干净排空并同目录 resume，最终与不间断 B
逐字节一致。最终 provider-free release gates 也已通过：`2599 passed, 8 deselected`、
integration 边界 `8 skipped`、116-wheel/331-sdist 审计和七组 clean-install smoke 全绿。live
throughput pilot 已在 pushed commit
`08f456d2…` 完成 4/8 路各 8 blocks；8/4 unit/call 吞吐比仅 `0.9795 / 0.9897`，独立确认后正式
并发保持 `4`。comparison SHA-256=`452d31be314bd66a6fe73548bb8d12078c38a132c968c3b95f92b212c9901d6d`；
attempt-004 已绑定 execution `773c69de…` / pre-call `facafd05…` detached 启动，并通过精确
5% / 10% / 15% / 20% / 25% checkpoints。随后机器离线，main durable prefix 停在 2,622 units /
3,125 rows / 8,445 calls；4 个 active lanes 留下 open provider boundary，旧 `--resume` 生成
INCOMPLETE checkpoint。用户批准的 post-hoc orphan-lane recovery 已由 pushed operator-only 工具
按 plan `bf662a67…` 应用，receipt=`c53c1d8a…`；原 `773c69de…` runtime 已从 2,622 durable units
同目录 resume。重跑的 4 units 已全部越过，并通过恢复后的精确 30% / 35% checkpoints；最新
用户出门前的单次 `SIGINT` 曾干净排空到 3,705 admitted = READY、4,208 rows / 13,840 calls
（36.83%）；用户随后明确要求继续，PID 38427 / detached screen 的同目录 resume 已接受完整
prefix，仍为 4 lanes、无 abort，automation=`ACTIVE`。隔离的 9 attempts（5 usage 完整、4
unknown）须进最终 cost addendum，不能记零。网络改善后的 4-vs-8 pilot 已在 formal 干净暂停
期间一次性完成：comparison=`1fcfb8a3…`，8/4 unit/call ratios=`1.008834716058 /
1.070286574518`，两级均 0 retries，但仍未过 `1.35 / 1.25` 门槛，因此 formal 保持 4 lanes。
batch 已自动从 3,789-unit prefix 同目录 resume，并通过精确 40% / 45% / 50% / 55% / 60% / 65% / 70% / 75% / 80% checkpoints；最新只读快照为
8,153 READY / 8,157 admitted、8,656 rows / 35,808 calls（81.04%），extra attempts=481，
PID/screen 正常且无 terminal/abort/canonical。复测不得重复；完成前不启动 Task 10。现有
pre-call、WAL、config、历史 receipt 与金额均须保留。

**真源分工**：设计 spec 是产品/方法学决策真源；`docs/PROJECT_STATE.md` 是当前实现进度真源；代码、测试和 `docs/BENCHMARK_RESULTS.md` 是已实现能力与实测结果的最终证据。不要用历史计划中的未勾 checkbox 推断当前状态。

**CI / 合并规则**：自动 CI 只在 pull request 与 `main` push 上按 Python/Web/文档路径分别运行轻量检查；完整 benchmark、集成边界、冻结工件、依赖审计和发行包 smoke 只通过 `Full validation` 手动 workflow 在 Task 验收时运行。每个 Task 完成后必须先通过完整验收，再合并或 fast-forward 回 `main` 并推送 `main`，然后才能开始下一 Task。

## 如何恢复上下文（重启会话第一步）
1. 读 `docs/superpowers/specs/2026-07-09-fretsure-design.md`（设计真源，§14=benchmark/checker/agent 深度详版，§15=harness/demo/求职详版；其中 target 数字不是实测结果）。
2. 读 `docs/PROJECT_STATE.md`（当前实现状态、决策日志、7 拆分、下一步与未决项）。
3. 读 `docs/BENCHMARK_RESULTS.md` 与 `docs/PLAN1_ACCEPTANCE.md`（已测结果、诚实限制、真人 gold 延期边界）。
4. 继续时读 `docs/superpowers/plans/2026-07-17-benchmark-v2.md`，再按需回看
   `docs/MIDI_ACCEPTANCE.md` 与 `docs/BENCHMARK_RESULTS.md`。不要重做 Plan 1–5、Oracle 0.2、安全
   `.mxl`、Plan 6A、producer-driven MusicXML/IR 或 MIDI；旧 benchmark 数也不得冒充当前基线。

## 锁定的关键决定（勿重新推翻，除非用户明说）
- 领域 = 音乐 / 吉他编配（受众广、可听可视）；**领域不硬核、技术尽量硬核**。
- **核心范式：oracle 当环境、LLM 当策略（policy）**；**harness 自研**，框架（LangGraph/Claude Agent SDK 等）仅作对照基准。
- HERO = 可证明可弹的**指弹独奏**；难度简化 = 商业楔子；伴奏 = 标配。
- 输入**符号优先**（MusicXML/MIDI/lead sheet）；mp3 作 best-effort 前端（**不保证**）。
- **当前保证输入是两个窄合同，不是通用谱面兼容**：MusicXML 保持未压缩 `.musicxml`/`.xml` 与安全 `.mxl` 的单 part/staff/voice lead-sheet 子集；`midi@0.1.0` 只接受 format 0/1、PPQN、固定 tempo/4/4、单一非打击乐单声部 note stream。MIDI 精确保留 raw tick timing，全部标为 melody、`chords=()`，不猜 role/chord/key/quantization；复调、多 stream、SMPTE、percussion、sustain、pitch/tuning change、SysEx 等 typed fail-closed。
- **不 overclaim**：只主张"可证明可弹 + 修复 + 机器可检 benchmark"；**不**主张发明校验/编配/指法。
- benchmark **checker 打分，非 LLM 评委**；每个 agent 能力用 **ablation** 挣存在，随机选择类效应用共享候选池做配对比较，**砍掉的组件公开**。
- RL = stretch（CPU 小 reranker，允许诚实负结果）；DSPy/GEPA 保留但消融把关；Plan 6A 已通过 stdio MCP 暴露 oracle/solver/ASCII renderer，热循环仍进程内直调。
- **可展示 = 真功能**（demo 就是产品在跑）；求职 artifact 见 spec §15 Part G。
- **认证边界**：当前 GREEN 只是在 `oracle@0.2.0` 的版本化简化几何 + active-sustain + 有限时序/速率模型及 fingerprinted profile 下的机器认证；`tab-input@0.2.0` 先拒绝无效输入。`fidelity@0.3.0` 是独立、availability-aware 的门：没有 source evidence 的分数必须是 `None`/N/A，不得伪装成 1.0；MIDI melody-only 只评 melody，bass-root/harmony 不可用。真人部分不阻塞软件开发，但阻塞现实世界误接受率、profile/tier 映射、真人 musicality 与更强对外保证。

## 硬约束 / 资源
- solo builder；API 充足（GPT-5.6 Sol / embedding）；**无 GPU 训练大模型**（本地 24G，CPU 只能跑小模型 RL）；**无专有数据**（要合成/程序生成）。
- 规划/允许的全免费技术栈（music21 / AlphaTab / FluidSynth 等；部分尚未引入）；**避开 GPL**（phonemizer/espeak）。

## 诚实的现实（别自欺）
- 新颖性 = **部分开放**：作为上线产品无人做，但概念有先例（SMC 2024 id55；TemPolor 输出"弹不了"正好验证痛点；Woolfy/THIRI/CLARA 做和声不做 tab 可弹）。**护城河 = 执行力 + benchmark 严谨 + 先发，不是原创。**
- **最该狠验的是 oracle 本身**（§14 A.8「谁检查检查器」）——现实世界误接受率与人体保证都 gate 在它的真人验证上。

## 目录约定
- `docs/superpowers/specs/` 设计文档（设计真源）
- `docs/superpowers/plans/` 路线图、历史计划、已闭门 MusicXML/Oracle/MXL/Plan 6A/producer/MIDI 计划，以及当前 benchmark-v2 计划（完整 Plan 6B/7 仍待后续）
- `docs/PROJECT_STATE.md` 当前项目状态 / 恢复文档
- `docs/BENCHMARK_RESULTS.md` 已跑实验与限制

## 约定
- Git 提交：**不追加 `Co-Authored-By: Claude ...` 之类的 AI 共同作者 trailer**（沿用 liyifan 在其它项目的偏好；如需更改请明说）。
