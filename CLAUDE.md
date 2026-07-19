# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成**人手可证明弹得出来**的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 逐音把关并修复 → checker 打分 benchmark"。

**当前恢复真源（2026-07-19）**：Task 9 formal attempts 001–003 均已 terminal
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
attempt-004 已绑定 execution `773c69de…` / pre-call `facafd05…` detached 启动，并通过精确 5%
checkpoint：503/10,060 network units、1,006/10,563 rows、2,451 calls，overall 106.64 units/h，
剩余 ETA 约 3天13小时20分–3天17小时37分。它仍在运行，尚无 terminal usage/cost receipt；下一步只
继续采集与约每 5% 记录，完成前不得启动 Task 10。现有 pre-call、WAL、config、abort receipt 与
金额均为冻结历史证据。

**真源分工**：设计 spec 是产品/方法学决策真源；`docs/PROJECT_STATE.md` 是当前实现进度真源；代码、测试和 `docs/BENCHMARK_RESULTS.md` 是已实现能力与实测结果的最终证据。不要用历史计划中的未勾 checkbox 推断当前状态。

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
