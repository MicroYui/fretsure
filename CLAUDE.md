# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成在版本化模型内经确定性 oracle 逐音核验的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 把关 → checker 打分 benchmark"；verifier-guided repair 可选，但 v2 未过保留阈值。

**当前恢复真源（2026-07-30）**：`oracle@0.7.0` / `median@0.3`，曲目门 **152/292 = 52.1%**
（GREEN 118），冻结基线 30/56。上一版记的 146/292 复现不出来——同一 commit 干净树跑两次
都是 107，`--choose-capo` 在 56 首上只多救 1 首，来源无法重建，故**改正**。
**没人能重新签发的数字不是基线**：曲目门一臂 27 分钟，于是它被跨会话凭记忆引用。

本轮（07-30）修的是**手位窗口与跨度规则用两个常数管同一件事**：`check_shift_speed`
的手心区间交集非空 ⟺ 最远一对沿颈 ≤ `2×reach_mm`，这与 `d_max` 是同一主张但抹掉了
手指编号；110 mm 比 `d_max(1,4)` 的 143 mm 更紧，于是**静默覆盖**它。结果是 07-29
那次「常规伸展技术」的决定**进了文档没进判定器**——仍拒绝手指 1、4 按 3 品与 7 品。
修法 `reach_mm = span/2`，用「窗口必须是精确规则的松弛」论证，穷举单帧性质测试钉住。
**没有靠肉眼判形状**：595 条放弃的拒绝全部对照过拥有同一限制的规则，495 条落在自己
那对手指的许可内，100 条的距离全在 `v_shift` 行程内（有的差一个数量级）。

**两个 v1 占位参数不需要出处**：`v_shift_mm_per_s` 在 200–2500、`r_max_hz` 在 4–40
判决完全不变。`SHIFT_SPEED` 看起来吃重只是因为一个诊断名覆盖两个常数。

上一轮（07-29）修掉的两个真缺陷：`d_max` 比琴颈还窄（G 大和弦无法认证），以及跨度
按直线而非**沿颈**计算。

建立的仪器 `scripts/measure_oracle_discrimination.py` 是最该保留的东西：正例是编者
印在谱上的指法，负例是同一段音乐**沿颈推开**，两侧都来自出版谱、不需要真人。
**07-30 修了它自己的一个缺陷**：它推的是「第一个按弦音、永远向上」，于是最低音被推
时形状是**塌缩**而不是拉开（出现两指同品），把正确的接受算成了失真。已改为把极端音
向外推，并加了回归测试。**帧级假阴性 9.8%**（全语料），判别力 90.2 点。

**唯一还开着的帧级缺陷**：`FINGER_MONOTONIC` 拒绝手腕自然斜角（23 个剩余被拒帧中
11 个由它单独挡住）。解锁条件不是更好的规则组织，是**其它编者版本**或真人测量。

**整曲 52% 与帧级 90% 正确不矛盾——误差连乘**：一首约 150 帧，`0.52 = x^150` 意味着每帧
需要 99.6%。所以追整曲覆盖率的大改动是徒劳的，有价值的是消除**系统性的每帧缺陷**——
本轮 +45 首正是这样来的：改的是一条每帧都在生效的规则。

**十一个方向被数据关闭**（beam 宽度 1/16、随机化保留 0/3、生成宽度 1/12、逐对 DP 找到的
3 条路径在完整检查器下 3/3 失败、持续音、位移速度、hand_span、保持率下限、手掌平面模型
20 个工作点无一占优、斜角四个界同一汇率、相邻指对因子 train 有效 test 无效）。其中五个
是在动手实现之前关掉的。**勿重做。**

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
- **认证边界**：当前 GREEN 只是在 `oracle@0.7.0` 的版本化简化几何 + active-sustain + 有限时序/速率模型及 fingerprinted profile（`median@0.3`）下的机器认证；`tab-input@0.3.0` 先拒绝无效输入。其中 `v_shift_mm_per_s` / `r_max_hz` 已实测在很宽范围内不改变任何判决，所以那部分「有限时序/速率模型」目前不承担认证责任。`fidelity@0.3.0` 是独立、availability-aware 的门：没有 source evidence 的分数必须是 `None`/N/A，不得伪装成 1.0；MIDI melody-only 只评 melody，bass-root/harmony 不可用。真人部分不阻塞软件开发，但阻塞现实世界误接受率、profile/tier 映射、真人 musicality 与更强对外保证。
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
