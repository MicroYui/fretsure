# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成**人手可证明弹得出来**的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 逐音把关并修复 → checker 打分 benchmark"。

**当前阶段（2026-07-16）**：**Plan 1–5、MusicXML-first 受限文件纵切、Oracle 0.2 软件信任门和安全 `.mxl` container reader 已实现**，形成可运行的后端研究原型。当前 package=`0.2.0`、playability=`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、faithfulness=`fidelity@0.2.0`、importer=`musicxml@0.2.0`、container=`mxl-container@0.1.0`；profile 仍为 `median@0.1`。默认真代理模型已切到 canonical `gpt-5.6-sol`，CLI/trace/benchmark 均记录 model id；历史 Claude 实验不回写。公共输入/资源/TOCTOU 边界与 ZIP/container 完整性边界已关闭；真实 producer provenance 与真人经验有效性继续 open。最终质量门数字见 `docs/PROJECT_STATE.md`；6 项需本地 LLM 代理的 integration 测试另列。

**真源分工**：设计 spec 是产品/方法学决策真源；`docs/PROJECT_STATE.md` 是当前实现进度真源；代码、测试和 `docs/BENCHMARK_RESULTS.md` 是已实现能力与实测结果的最终证据。不要用历史计划中的未勾 checkbox 推断当前状态。

## 如何恢复上下文（重启会话第一步）
1. 读 `docs/superpowers/specs/2026-07-09-fretsure-design.md`（设计真源，§14=benchmark/checker/agent 深度详版，§15=harness/demo/求职详版；其中 target 数字不是实测结果）。
2. 读 `docs/PROJECT_STATE.md`（当前实现状态、决策日志、7 拆分、下一步与未决项）。
3. 读 `docs/BENCHMARK_RESULTS.md` 与 `docs/PLAN1_ACCEPTANCE.md`（已测结果、诚实限制、真人 gold 延期边界）。
4. 继续时读 `docs/superpowers/plans/2026-07-13-oracle-0.2-trust-gate.md`、pre-Plan6 MusicXML 计划与 `2026-07-13-safe-mxl-container.md`。不要重新二选一 MusicXML/MIDI，也不要重做 Plan 1–5/Oracle 0.2/安全 `.mxl`；下一项按 Plan 6A Web/API/trace viewer/MCP 薄纵切推进。

## 锁定的关键决定（勿重新推翻，除非用户明说）
- 领域 = 音乐 / 吉他编配（受众广、可听可视）；**领域不硬核、技术尽量硬核**。
- **核心范式：oracle 当环境、LLM 当策略（policy）**；**harness 自研**，框架（LangGraph/Claude Agent SDK 等）仅作对照基准。
- HERO = 可证明可弹的**指弹独奏**；难度简化 = 商业楔子；伴奏 = 标配。
- 输入**符号优先**（MusicXML/MIDI/lead sheet）；mp3 作 best-effort 前端（**不保证**）。
- **首发输入已冻结为 MusicXML-first**：当前支持未压缩 `.musicxml`/`.xml` 和安全 `.mxl`，限 3.1/4.0 `score-partwise` 的单 part/staff/voice 单音 lead-sheet 子集，unsupported 语义 typed fail-closed。`.mxl` 只扩容器、不扩语义；更完整 MusicXML 与 MIDI 明确延后，不是删除。
- **不 overclaim**：只主张"可证明可弹 + 修复 + 机器可检 benchmark"；**不**主张发明校验/编配/指法。
- benchmark **checker 打分，非 LLM 评委**；每个 agent 能力用 **ablation** 挣存在，随机选择类效应用共享候选池做配对比较，**砍掉的组件公开**。
- RL = stretch（CPU 小 reranker，允许诚实负结果）；DSPy/GEPA 保留但消融把关；Plan 6 计划通过 MCP 暴露 oracle（当前未实现）。
- **可展示 = 真功能**（demo 就是产品在跑）；求职 artifact 见 spec §15 Part G。
- **认证边界**：当前 GREEN 只是在 `oracle@0.2.0` 的版本化简化几何 + active-sustain + 有限时序/速率模型及 fingerprinted profile 下的机器认证；`tab-input@0.2.0` 先拒绝无效输入，`fidelity@0.2.0` 是独立门（melody/bass exact-onset，harmony chord-segment Jaccard），所以 GREEN 可以同时 faithfulness FAIL。真人部分不阻塞软件开发，但阻塞现实世界误接受率、profile/tier 映射、真人 musicality 与更强对外保证。

## 硬约束 / 资源
- solo builder；API 充足（GPT-5.6 Sol / embedding）；**无 GPU 训练大模型**（本地 24G，CPU 只能跑小模型 RL）；**无专有数据**（要合成/程序生成）。
- 规划/允许的全免费技术栈（music21 / AlphaTab / FluidSynth 等；部分尚未引入）；**避开 GPL**（phonemizer/espeak）。

## 诚实的现实（别自欺）
- 新颖性 = **部分开放**：作为上线产品无人做，但概念有先例（SMC 2024 id55；TemPolor 输出"弹不了"正好验证痛点；Woolfy/THIRI/CLARA 做和声不做 tab 可弹）。**护城河 = 执行力 + benchmark 严谨 + 先发，不是原创。**
- **最该狠验的是 oracle 本身**（§14 A.8「谁检查检查器」）——现实世界误接受率与人体保证都 gate 在它的真人验证上。

## 目录约定
- `docs/superpowers/specs/` 设计文档（设计真源）
- `docs/superpowers/plans/` 路线图、Plan 1–5 历史实现计划、pre-Plan6 MusicXML、Oracle 0.2 与安全 `.mxl` 已执行计划（Plan 6/7 详细计划待写）
- `docs/PROJECT_STATE.md` 当前项目状态 / 恢复文档
- `docs/BENCHMARK_RESULTS.md` 已跑实验与限制

## 约定
- Git 提交：**不追加 `Co-Authored-By: Claude ...` 之类的 AI 共同作者 trailer**（沿用 liyifan 在其它项目的偏好；如需更改请明说）。
