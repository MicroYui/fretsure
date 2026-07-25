# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成在版本化模型内经确定性 oracle 逐音核验的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 把关 → checker 打分 benchmark"；verifier-guided repair 可选，但 v2 未过保留阈值。

**当前恢复真源（2026-07-25）**：Plan 6B（performance workspace + 真人 money moment）、Plan 7A
（风格/手型/技巧/局部重生成/手动指号/本地反馈）、Plan 7B（出版谱监督的指法排序 + 独立分级估计）
与其后的授权语料扩库均已闭门，并已落库推送（`3dce531`）。之后完成了一轮 benchmark 子系统的
**over-design 清理**：删除 pricing→envelope→budget-gate→pre-call 授权链、preregistration 逐字节
重推导、power 仿真与 task8/task9 一次性运维脚本。

当前运行 stamps：package `0.6.0`；`oracle@0.3.0`、`tab-input@0.2.0`、`fidelity@0.3.0`；importers
`musicxml@0.4.0` / `midi@0.1.0` / `score-input@0.1.0` / `mxl-container@0.1.0`；
`fingering-solver@0.6.0` / `score-solver@0.4.0` / `left-hand-ergonomics@0.1.0`；
`published-fingering-ranker@0.1.0`、`published-grade-estimator@0.1.0`、`difficulty@0.1.0`；
`arrangement-style-registry@0.2.0`、`profile-registry@0.2.0`、`technique-profile-registry@0.1.0`；
service / API / Web / trace = `0.3.0`，MCP = `0.2.0`；`benchmark-preregistration@0.3.0`、
`benchmark-live-policy@0.1.0`；runtime 精确锁定 `music21==10.5.0`。

**Benchmark v2 结论未变**（数字是历史实测，不因上面的机制清理而改写）：`full` 74/500=`14.8%`
[Wilson 11.96–18.18]，所有高复杂度格与 3 个 public controls 均为 0；repair Δjoint=`+0.0566`
低于 `0.10` SESOI → `NOT_KEPT`；best-of-4 Δ=`+0.068` 但 provider token/cost 为 null →
`PROBATION_COST_UNKNOWN`；critic joint=`-0.002` 且无真人证据 → `HUMAN_BLOCKED_PROBATION`。
产品缺省因此是 `n=1, max_iters=0, use_critic=false`；search / repair / critic 只能显式 opt in。
Plan 6B 的真人试奏结论是 `PARTIAL`（`12-10-8-12` 的 `8→12` 大把位跳转不易按），不得外推为
“真人一定能弹”。

**benchmark 机制现状（2026-07-25 清理后）**：live 运行的全部授权面就是一个必须原样重复一遍的
花费上限（`--live --full-corpus --max-spend-microunits N --confirm-spend N`）；503 项语料由
`src/fretsure/bench/data/` 的 census + 3 个 pinned 源现场重建（4.8 秒）并只校验一个身份摘要；
preregistration 由语料推导、不再是提交进仓库的工件，其 `versions` 用**实时**版本号；并发是
opt-in 的 `--concurrent-units N`，stub 跑 4 lane 与串行跑逐字节相同。attempt-004 的历史 digest
仍是证据，但**已不能用当前代码复现**——这是用户明确接受的代价（项目从未发布，不为旧版本做兼容）。

**真源分工**：设计 spec 是产品/方法学决策真源；`docs/PROJECT_STATE.md` 是当前实现进度真源；代码、测试和 `docs/BENCHMARK_RESULTS.md` 是已实现能力与实测结果的最终证据。不要用历史计划中的未勾 checkbox 推断当前状态。

**CI / 合并规则**：自动 CI 只在 pull request 与 `main` push 上按 Python/Web/文档路径分别运行轻量检查；完整 benchmark、集成边界、冻结工件、依赖审计和发行包 smoke 只通过 `Full validation` 手动 workflow 在 Task 验收时运行。每个 Task 完成后必须先通过完整验收，再合并或 fast-forward 回 `main` 并推送 `main`，然后才能开始下一 Task。

## 如何恢复上下文（重启会话第一步）
1. 读 `docs/superpowers/specs/2026-07-09-fretsure-design.md`（设计真源，§14=benchmark/checker/agent 深度详版，§15=harness/demo/求职详版；其中 target 数字不是实测结果）。
2. 读 `docs/PROJECT_STATE.md`（当前实现状态、决策日志、7 拆分、下一步与未决项）。
3. 读 `docs/BENCHMARK_RESULTS.md`、`docs/BENCHMARK_V2_ACCEPTANCE.md` 与 `docs/PLAN7B_ACCEPTANCE.md`
   （已测结果、诚实限制、真人 gold 延期边界）。
4. 需要历史细节时再回看 `docs/PLAN6B_ACCEPTANCE.md` / `docs/PLAN7A_ACCEPTANCE.md` /
   `docs/LEFT_HAND_SOLVER_V2.md` 与 `docs/superpowers/plans/` 下已闭门的计划。不要重做 Plan 1–5、
   Oracle 0.2、安全 `.mxl`、Plan 6A、producer-driven MusicXML/IR、MIDI、benchmark v2、Plan 6B/7A/7B；
   旧 benchmark 数也不得冒充当前基线。

## 锁定的关键决定（勿重新推翻，除非用户明说）
- 领域 = 音乐 / 吉他编配（受众广、可听可视）；**领域不硬核、技术尽量硬核**。
- **核心范式：oracle 当环境、LLM 当策略（policy）**；**harness 自研**，框架（LangGraph/Claude Agent SDK 等）仅作对照基准。
- HERO = 可证明可弹的**指弹独奏**；难度简化 = 商业楔子；伴奏 = 标配。
- 输入**符号优先**（MusicXML/MIDI/lead sheet）；mp3 作 best-effort 前端（**不保证**）。
- **当前保证输入是窄合同，不是通用谱面兼容**：MusicXML 保持未压缩 `.musicxml`/`.xml` 与安全 `.mxl`
  的单 part/staff/voice lead-sheet 子集，外加 `musicxml@0.4.0` 的严格双谱表钢琴缩编
  （成功带 `PIANO_REDUCTION_DERIVED`，不声称保留声位/转位）；`midi@0.1.0` 只接受 format 0/1、PPQN、
  固定 tempo/4/4、单一非打击乐单声部 note stream。MIDI 精确保留 raw tick timing，全部标为 melody、
  `chords=()`，不猜 role/chord/key/quantization；复调、多 stream、SMPTE、percussion、sustain、
  pitch/tuning change、SysEx 等 typed fail-closed。
- **不 overclaim**：只主张"可证明可弹 + 修复 + 机器可检 benchmark"；**不**主张发明校验/编配/指法。
- benchmark **checker 打分，非 LLM 评委**；每个 agent 能力用 **ablation** 挣存在，随机选择类效应用共享候选池做配对比较，**砍掉的组件公开**。
- RL = stretch（CPU 小 reranker，允许诚实负结果）；DSPy/GEPA 保留但消融把关；Plan 6A 已通过 stdio MCP 暴露 oracle/solver/ASCII renderer/难度/FluidSynth 音频，热循环仍进程内直调。
- **可展示 = 真功能**（demo 就是产品在跑）；求职 artifact 见 spec §15 Part G。
- **认证边界**：当前 GREEN 只是在 `oracle@0.3.0` 的版本化简化几何 + active-sustain + 有限时序/速率模型及 fingerprinted profile 下的机器认证；`tab-input@0.2.0` 先拒绝无效输入。`fidelity@0.3.0` 是独立、availability-aware 的门：没有 source evidence 的分数必须是 `None`/N/A，不得伪装成 1.0；MIDI melody-only 只评 melody，bass-root/harmony 不可用。真人部分不阻塞软件开发，但阻塞现实世界误接受率、profile/tier 映射、真人 musicality 与更强对外保证。
- **不为旧版本做兼容**：项目从未发布；版本号变化时直接重新冻结金值/工件，不要新增
  `FROZEN_*` 兼容常量。语义变了仍要在文档里写清楚。

## 硬约束 / 资源
- solo builder；API 充足（GPT-5.6 Sol / embedding）；**无 GPU 训练大模型**（本地 24G，CPU 只能跑小模型 RL）；**无专有数据**（要合成/程序生成/有许可的公开谱）。
- 规划/允许的全免费技术栈（music21 / AlphaTab / FluidSynth 等）；**避开 GPL**（PyGuitarPro 是 LGPL-3.0-only 的可选 extra，不 vendored）。
- 仓库许可为 **Apache-2.0**（`LICENSE` + `NOTICE`）。

## 诚实的现实（别自欺）
- 新颖性 = **部分开放**：作为上线产品无人做，但概念有先例（SMC 2024 id55；TemPolor 输出"弹不了"正好验证痛点；Woolfy/THIRI/CLARA 做和声不做 tab 可弹）。**护城河 = 执行力 + benchmark 严谨 + 先发，不是原创。**
- **最该狠验的是 oracle 本身**（§14 A.8「谁检查检查器」）——现实世界误接受率与人体保证都 gate 在它的真人验证上。

## 目录约定
- `docs/superpowers/specs/` 设计文档（设计真源）
- `docs/superpowers/plans/` 路线图与已闭门计划（MusicXML/Oracle/MXL/Plan 6A/producer/MIDI/benchmark v2/Plan 6B/7A/7B）
- `docs/PROJECT_STATE.md` 当前项目状态 / 恢复文档
- `docs/BENCHMARK_RESULTS.md` 已跑实验与限制
- `src/fretsure/bench/data/` benchmark 语料的 census 与 3 个 pinned 许可源（包内数据，wheel 也带）

## 约定
- Git 提交：**不追加 `Co-Authored-By: Claude ...` 之类的 AI 共同作者 trailer**（沿用 liyifan 在其它项目的偏好；如需更改请明说）。
- 前端改动前先确认统一审美：已锁定“古典制琴工坊 × 验证仪器”方向（深墨底 + 暖纸面 + 铜色重点 +
  酸绿 GREEN 判决，Instrument Serif 标题 / Manrope 正文 / Bravura 记谱）。
