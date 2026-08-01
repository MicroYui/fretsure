# Fretsure — Claude Code 项目上下文

> 本文件在该目录启动会话时自动加载。**恢复上下文第一步：按下面“如何恢复”读取设计真源与当前实现状态。**

**产品目标一句话**：一个 agent，把一首歌（符号谱 / MIDI / lead sheet；mp3 作 best-effort 前端）编配成在版本化模型内经确定性 oracle 逐音核验的吉他谱（HERO = 指弹独奏；也做伴奏、难度简化）。核心 = "LLM 提议 → 确定性可弹性 oracle 把关 → checker 打分 benchmark"；verifier-guided repair 可选，但 v2 未过保留阈值。

**当前恢复真源（2026-07-31）**：`oracle@0.8.0` / `median@0.3`。曲目门两个模式都要报：
**固定变调夹 154/292 = 52.7%**（GREEN 117、冻结 30/56），**capo ladder 178/292 = 61.0%**
（GREEN 132、冻结 32/56）。**帧级假阴性 3.3%**、判别力 96.7 点。
按历史一直在用的 capo 那一列读，本周是 **146 → 173（50.0% → 59.2%），GREEN 89 → 129**。

**曲目门有两个模式、共用一个名字**：历史上记的 146/292 是开 `--choose-capo` 的，
07-30 之后所有数字都是固定变调夹的。我 07-30 曾断言 146「复现不出来、故改正」——
**那个断言错了，已撤回**。判据是两次测量在冻结 56 首上**逐个 id 完全一致**（capo 在那里
只救 1/31），只在扩展语料上差 33 首（capo 在那里救 6/14 = 43%）。当初排除 capo 用的正是
那 56 首子集——**拿一个子集去否定关于全语料的主张**。见
`docs/experiments/2026-07-31-gate-had-two-modes.md`。**报曲目门必须标注模式。**

**曲目门与帧级会朝相反方向动，必须一起报。** 斜角豁免把帧级从 5.3% 压到 3.3%，曲目门
却 152 → 149：`no feasible frame config` 44 不变，差额全在 `no non-red extension within
beam`（90 → 93）。没有东西变得弹不了，是 beam 更挤了。**曲目门当前不是对物理模型的
干净读数。**

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

**`FINGER_MONOTONIC` 那条已经关掉了**（`oracle@0.8.0`）：手腕斜角——高编号手指落在
更靠高音弦、且更靠近弦枕——被豁免；反向交叉仍然拒绝。07-29 曾因「远场代价一成」否掉它，
那次的两个仪器后来都被发现有缺陷，重测的真实代价是 **1.2 个点**，12 品端零代价。
**不加品数上界**：`d_max` 已按把位限制了斜角能退多远（一把位 4 品、十二把位 9 品），
再加一个就是同一条纵向限制的第三份副本。

**语料已修**（07-31）：19 首音高吉他发不出来的曲子，18 首已修正、1 首具名隔离，
音域审计 **19 → 1**。曲目门 **149 → 154/292 = 52.7%**（GREEN 114 → 117），capo ladder 模式 **173 → 178/292 = 61.0%**
（GREEN 129 → 132），"音域外"6 → 1。**按历史一直在用的 capo 那一列读，本周是 146 → 178
（50.0% → 61.0%），GREEN 89 → 132。**
帧级 3.3% → **4.1%**、判别力 96.7 → **95.9**——**指标变差是因为它现在测的是正确的数据**。
修正表在 `scripts/repair_corpus_pitches.py`（生成器 `m1_lilypond.py` 在上游、不在本仓库，
重建会把缺陷放回来），`tests/test_corpus_fits_the_instrument.py` 是那时会失败的守卫。

**原始诊断**（`scripts/audit_corpus_range.py`）：
13 首按**记谱音**录入、高了一个八度（9 首范围正好 52–88 = 空弦低音 E 与高音 E 各高八度，
含《爱的罗曼史》），5 首是**没记录的降弦调弦**（Capricho Árabe 本来就是 drop-D），
1 首不是吉他曲。转换器完全不看 clef。**门只把其中 6 首报成音域外，另外 13 首藏在
beam（9）和 frame-config（4）桶里**——这两个桶本轮的分析都被污染过。源文件不在本地，
所以只报告不修；两首纯降八度后从"音域外"直接变 GREEN，另两首**换了桶**，污染是直接演示的。

**物理模型作为曲目门改进来源已经关掉了**（07-31）。修复后 154 接受、138 首被拒
= **46 帧级配置**（已关闭：20 几何、中位裕度 21.8 mm；18 无法落在不同弦上；5 历史；
3 横按占位）+ **91 beam** + 1 隔离。**没有站得住的常数够得到 21 mm**（琴颈宽度那次只动 2.5 mm）。

**弦号是 0 起、0 = 最低弦**（`score_corpus.py:177` 存 `string_count - musicxml_string`，
写在代码里，不需要校准——我 07-31 曾"校准"成 1 = 最低弦并发布，错了一位）。
**这个判据很弱**：错一位也能拿到 98.7%，因为 22 品的窗口宽到错一根弦照样包住那个音；
正确读法是 487/494 = 98.6%。它只在**差距很大**时有用，比如 capricho 按原样 202/204、
降八度 32/204。和弦归属丢失是 `python_ly_string_numbers.py` **自己第 35 行就记着的已知限制**
（python-ly 的指法路径同样丢一个），不是新发现。帧级配置那 44 帧重新归因：19 几何（裕度 3.4 / 21.3 / 67.2 mm）、
18 无法落在不同弦上（乐器事实）、4 是历史不是帧、3 是横按占位。本周的放宽已经关掉 28 中的 9，
中位裕度 26 → 21.3 mm——而琴颈宽度那次只动了 2.5 mm，span 那次动 30 mm 是靠**命名一个技术动作**
而不是追裕度。**没有站得住的常数够得到 21 mm。** 剩下的空间只有 beam 的**保留策略**，
而宽度已经测平。

**帧级仪器基本见底**：剩 7 帧全部是 `FRET_SPAN`，而其中多数所需 span 高达 1.63×（一把位
1–9 品，没有这种手），说明它们仍是**全保持假设**的假象而非手模型问题。仪器第四次缺陷
也在今天修掉：编者把**同一个手指标在两个音上**时，那两个音本来就不同时按住，这种帧
任何判定器都会拒绝，已改报「不可判定」。**下一步的空间在搜索，不在物理模型**——
`no non-red extension within beam` 是 93/292，最大的一桶。

**整曲 51% 与帧级 96.7% 正确不矛盾——误差连乘**：一首约 150 帧，`0.51 = x^150` 意味着
每帧需要 99.6%。所以追整曲覆盖率的大改动是徒劳的，有价值的是消除**系统性的每帧缺陷**——
reach 那一改 +45 首正是这样来的：改的是一条每帧都在生效的规则。但 07-30 也证明了反面：
帧级更正确不保证曲目门上升，因为 beam 会重排。

**beam 宽度在新条件下重测，结论更强**（07-30）：beam 32 是 **143/292**，比 16 的 149 更差，
而且 `no non-red extension within beam` 那一桶**几乎不动**（93 → 94）——杀死搜索的不是
「只留了 16 个」。宽度这条杠杆已经测平了，**保留策略（留哪 16 个）是另一条、且没被它涵盖**。
（beam 32 另有 5 首栽在 segment 预算上，是混淆项；即便全算成功也只有 148 < 149。）

**十一个方向被数据关闭**（beam 宽度 1/16、随机化保留 0/3、生成宽度 1/12、逐对 DP 找到的
3 条路径在完整检查器下 3/3 失败、持续音、位移速度、hand_span、保持率下限、手掌平面模型
20 个工作点无一占优、斜角四个界同一汇率、相邻指对因子 train 有效 test 无效）。其中五个
是在动手实现之前关掉的。**勿重做。**

**负样本守卫的 82 张 GREEN 已经查过了**（08-01）：全部 13 个音以上、8–32 个 onset、
13–26 个按弦音，81/82 形状互不相同——"它们只是平凡样例"这个解释不成立。但把它们和
被拒的 1439 张比，**音符数完全相同（中位 28）、把位也相同**，唯一有系统差别的是
**同时按住的最宽帧：GREEN 75.1 mm · AMBER 100.2 · RED 129.9**——正是 `d_max` 与手位窗口
限制的那个量，单调分离。75 mm 是一把位 1→3 品。**所以这 82 张不是随机认证的**；但这不证明
它们可弹（右手实用性、musicality、持续音都在检查之外），守卫仍然断言 provenance 而非
playability。见 `docs/experiments/2026-08-01-certified-model-tabs.md`。

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
