# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成**人手可证明弹得出来**的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 逐音把关并修复 → checker 打分 benchmark"。

**当前阶段（2026-07-18）**：**Plan 1–5、MusicXML-first、Oracle 0.2、安全 `.mxl`、Plan 6A、producer-driven MusicXML/IR、strict MIDI input 与 benchmark v2 Task 1–8 均已闭门；Task 9 formal attempt-001 已 terminal `INCOMPLETE`，修正计费契约后从 fresh attempt-002 继续**。当前 package=`0.6.0`，runtime 精确锁定 `music21==10.5.0`。Task 8 attempt-002 已 COMPLETE 8/8；两次 pilot 合计已知 / corrected tight upper 为 `$0.513140` / `$27.730036`，官方 `128,000` output-token 契约下单次 pilot 机械上限为 `$513.232896`。用户已统一授权项目模型计费。formal 官方契约的机械最大值为 `1,167,905,640,000` micro-USD（`$1,167,905.640000`）；这是计费审计上限，不是本地代理预消费硬闸。历史 `$538,865.486400` gate（SHA-256=`931b5ae14d587d89511aa3b5c45c7458e96c377df54093ad6244a14948527bd9`）和 attempt-001 pre-call 保留作审计证据，不得改写或复用。Task 9 attempt-001 在 503 个 pure-solver units 和第一个 agent unit 后，因 critic visible limit `512` 与 provider billable output usage `704` 被旧 validator 误当同一上限而终止；已知 / tight upper 为 `$0.188415` / `$28.332415`，未检查私有 prompt/response。formal runner 仍在 observation/WAL/retry/network 前执行 UTF-8 bytes + 256 guard，成功调用必须返回精确 `gpt-5.6-sol` 证据，live 只发布五个 raw canonical 工件并由两个独立 full replay 生成报告。新 `benchmark-formal-budget-gate@0.3.0` 已生成且回检通过，SHA-256=`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`；新 pre-call schema 是 `benchmark-pre-call-config@0.3.0`。下一步是推送修正工件，再在 pushed runner SHA 上生成 attempt-002 pre-call。运行时不调用 Git/子进程。本阶段没有前端设计改动；若要 dashboard、图表页或 live leaderboard，先确认既有视觉基线。

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
