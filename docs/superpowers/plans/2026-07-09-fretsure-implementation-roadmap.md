# Fretsure 主实现路线图（Implementation Roadmap）

> **For agentic workers:** 本文件是**全项目的实现设计**（里程碑 + 每步完整内容 + 验收门），不是单个可执行 plan。每个 Plan 的逐行 bite-sized TDD 计划是**独立文件**（`docs/superpowers/plans/YYYY-MM-DD-plan-N-*.md`），在其前置 Plan 锁定类型后即时撰写，用 `superpowers:subagent-driven-development` 或 `superpowers:executing-plans` 执行。
>
> **真源**：设计 spec `docs/superpowers/specs/2026-07-09-fretsure-design.md`（§0–§15）。本路线图**不新增设计决策**，只把 spec 落成可执行的里程碑序列与验收门；任何冲突以 spec 为准。

> **当前执行位置（2026-07-17）**：Plan 1–5、MusicXML-first、Oracle 0.2、安全 MXL、Plan 6A 与
> [`producer-driven MusicXML/IR`](2026-07-16-producer-driven-musicxml-ir.md) 已各自闭门，最终门见
> [`PRODUCER_MUSICXML_ACCEPTANCE.md`](../../PRODUCER_MUSICXML_ACCEPTANCE.md)。本阶段提交推送并核对
> local/remote SHA 后进入 MIDI，MIDI 闭门后再进入 benchmark v2。下面早期章节中的
> “Plan 1→2 补 MIDI”与“下一步写 Plan 1”只保留为
> 初始路线历史，不再控制当前顺序。真人 gold/calibration 继续限制经验主张，但不阻塞这三个软件阶段。

**Goal（一句话）**：把一首歌的符号音乐内容编配成「人手可证明弹得出来」的吉他谱——LLM 提议 → 确定性可弹性 oracle 逐音硬门 + 定位化诊断 → verifier-guided 自动修复 → checker 打分 benchmark。

**Architecture（范式）**：**oracle 当环境、LLM 当策略（policy）**。确定性 oracle / 指法求解器 / 乐理分析 / 忠实度 diff 是一套工具与环境；LLM 驱动「规划 → 用 edit-DSL 编辑 → 读定位化诊断 → 定点编辑 → 重查到不动点」的自纠回路。深度不在「图里有 LLM」，在「每个 agent 能力都用 leave-one-out 消融证明它挪动了 checker 打分的指标，否则砍掉并公开」。

**Tech Stack**：Python 3.11 + music21(BSD-3) + numpy/networkx（可选 OR-Tools CP-SAT）+ FastAPI；React/TS + Vite + AlphaTab(MPL-2.0) + 自研 SVG/Canvas 指板；FluidSynth(LGPL) + GeneralUser GS soundfont；LLM = GPT-5.6 Sol (API)；PyTorch-CPU（stretch RL）；Postgres（可选）。

---

## 全局约束（Global Constraints —— 每个 Plan 的需求都隐含包含本节）

逐条为项目级硬约束，值从 spec 逐字复制，实现时不得违背：

- **符号优先=保证路径**：MusicXML / MIDI / MusicXML-lite / lead sheet / 纯和弦谱经 music21 解析为 Music IR。**音频（mp3/wav）是 best-effort 前端、v2、明确标「近似、需校对、不保证」；转谱错误不计入产品的「保证」。**
- **oracle 只判「物理可弹 + 忠实约束」，不判「好听」**。「好听」由 LLM 提议 + musicality critic + 用户选择负责，且 critic 是有界经校准的品味评委、不进可行性门。
- **毫米几何，不用品数**：品位坐标 `x_f = L·(1 − 2^(−f/12))`，L≈648mm（古典）/643mm（钢弦）。span 谓词必须用**毫米欧氏距离**；用品数是显然错的。
- **三态输出**：GREEN（悲观 profile 下仍留边界 ε，误接受≈0，accept 方向 sound）/ RED（乐观 profile 下仍违反）/ AMBER（边缘，送修复或人审，**绝不作认证输出**）。**优先 GREEN 的 soundness**：承诺「我认证 GREEN 的就能弹」，不承诺「找出所有能弹的」；用 AMBER 带宽吸收不确定，**绝不放松 GREEN 阈值**。
- **全参数化**：hand_span H / 触及 R / 换把速度 v_shift / 右手速率 r_max / 弦长 L / 调弦 tuning / 变调夹 capo / skill tier —— 一切主张限定在**公布的 profile P** 与**静态手几何模型 M** 下（不含疲劳/肌腱耦合/音色）。
- **benchmark = checker 打分，非 LLM 评委**。任何无置信区间（Wilson / Clopper–Pearson / cluster bootstrap）的单数榜单主张不予采纳。
- **可弹 × 忠实联合（Pareto）报告**：弹得了但丢旋律 = 失败。护栏指标 = **保忠实修复率**（防「靠削音过可弹」作弊）。
- **每个 agent 能力用 leave-one-out 消融挣存在**：在留出曲上去掉它若不让某 checker 打分主指标退化超过配对 95% CI 半宽、或被更便宜能力 Pareto 支配，则**砍掉并公开**（含被哪条消融杀掉）。
- **不 overclaim**：只主张「可证明可弹 + 修复 + 机器可检 benchmark」；**不**主张发明校验/编配/指法；主动引用 SMC id55 / notave / TemPolor 作对照。
- **许可证**：避开 GPL（phonemizer/espeak，本产品无歌词、不涉及）；music21 BSD-3 / AlphaTab MPL-2.0 / FluidSynth LGPL / GeneralUser GS 允许商用。
- **每个判决盖 `checker_version + profile_version` hash**；benchmark 一条命令 `fretsure-bench --seed S` 复现。
- **Git 提交**：不追加 `Co-Authored-By: Claude ...` 之类 AI 共同作者 trailer（沿用 liyifan 偏好）。

---

## A. 排序原则与不变量（读一遍再动手）

1. **oracle 先行的硬顺序**（spec §14 A.14 / §10）：**第 4 步「oracle 验证台」通过前，下游一切 benchmark 数字不可信。** 构建序：① 归一器+datasheet → ② 程序生成器（主测试层）→ ③ oracle 纯函数+类型诊断+3 预设 → ④ **oracle 验证台（混淆矩阵领跑）** → ⑤ 忠实度打分 → ⑥ 难度打分 → ⑦ agent → ⑧ baselines+消融同一 runner → ⑨ 统计模块 → ⑩ checker-vs-judge → ⑪ 复现包。本路线图的 Plan 顺序服从此序。
2. **每个 Plan = 可独立运行、可独立测试的软件**（writing-plans Scope Check）。Plan 之间只经**共享契约（§B）**耦合；契约里的类型名/签名是跨 Plan 一致性的唯一真源。
3. **keep/cut 只按 leave-one-out**，加法阶梯只作诊断（加法会美化一切）。每个能力预注册**预期 Δ 方向与砍线**，跑完诚实公开砍掉的。
4. **soundness > completeness**：宁可 AMBER 误拒真弹得了的，也不 GREEN 误接受弹不了的。信任指标 = **GREEN 上的误接受率**（Clopper–Pearson 单侧上界）。
5. **忠实度是可弹性的孪生约束**：所有可弹指标必须与忠实度门**联合报**，否则不成立。

---

## B. 共享契约（Shared Contract —— 跨 Plan 的类型/接口唯一真源）

> 后续每个 Plan 的「Interfaces: Consumes/Produces」都引用本节的名字。**改名 = 破坏跨 Plan 一致性**，改动须先改本节。以下为**接口签名冻结点**（实现细节在各 Plan 的详细 TDD 计划里展开，但公开签名以此为准）。

### B.1 Music IR（`fretsure/ir.py`）
```python
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

VoiceRole = Literal["melody", "bass", "harmony"]  # 修复时的保留优先级：melody 必留 > bass 尽量保 > harmony 可增删
# 契约调和：spec §5.2 主 IR 定义与 §5.6 修复算子/§A.5 忠实度用 "harmony"；§A.1 note-graph 用 "inner"——二者同义（非旋律非低音的内声部），实现统一取 spec 主定义的 "harmony"。

@dataclass(frozen=True)
class Note:
    onset: Fraction          # 拍为单位的起始
    duration: Fraction
    pitch: int               # MIDI number
    voice: VoiceRole

@dataclass(frozen=True)
class ChordSymbol:
    onset: Fraction
    symbol: str              # 如 "Cmaj7"
    pitch_classes: frozenset[int]  # 0..11
    root_pc: int             # 0..11，用于 bass-root-accuracy

@dataclass(frozen=True)
class Meta:
    key: str; time_sig: tuple[int, int]; tempo_bpm: float
    source: str              # provenance
    title: str; license: str
    duration_beats: Fraction | None = None  # 显式曲尾；兼容旧 positional constructors

@dataclass(frozen=True)
class MusicIR:
    notes: tuple[Note, ...]; chords: tuple[ChordSymbol, ...]; meta: Meta
    # 不变量：每个 Note 有 voice 角色；同一 onset 的 melody 音是最高声部。
```
**不变量校验器** `validate_ir(ir) -> list[IRViolation]`（Plan 1 提供）：voice 覆盖、onset 单调、无重叠同弦冲突（IR 层不涉弦，仅时值/音高完备性）。

### B.2 Tab 表示（`fretsure/tab.py`）
```python
RightFinger = Literal["p", "i", "m", "a"]  # 拇/食/中/无名

@dataclass(frozen=True)
class TabNote:
    onset: Fraction; duration: Fraction
    string: int              # 0=最低音弦(6弦) ... 5=最高音弦(1弦)，随 tuning 定 open pitch
    fret: int                # 0=空弦
    left_finger: int         # 0..4，0=空弦
    right_finger: RightFinger

@dataclass(frozen=True)
class Tab:
    notes: tuple[TabNote, ...]
    tuning: tuple[int, ...]  # 每弦空弦 MIDI，低→高
    capo: int                # 变调夹品位，0=无
```
`Frame` = 同一 onset 同时发声的 `TabNote` 集合（oracle 与 solver 的工作单元）。

### B.3 Oracle 契约（`fretsure/oracle/`，★核心 IP）
```python
Verdict = Literal["GREEN", "RED", "AMBER"]
ViolationType = Literal[
    "MALFORMED_FINGERING",  # fret>0 必须有指、fret==0 必须无指（良构前提）
    "RANGE", "ONE_STRING_ONE_NOTE", "FINGER_COUNT", "FINGER_MONOTONIC",
    "FRET_SPAN", "BARRE_INFEASIBLE", "SHIFT_SPEED", "RIGHT_HAND", "SUSTAIN_CONFLICT",
]

@dataclass(frozen=True)
class Diagnostic:
    measure: int; beat: Fraction
    violation_type: ViolationType
    offending_notes: tuple[int, ...]      # TabNote 索引
    overage: float                        # 超几毫米/毫秒（定位化，供修复定点）
    suggested_relaxations: tuple[str, ...] # 如 ("drop_5th", "octave_down_bass", "shift_to_pos_5")

@dataclass(frozen=True)
class Profile:                 # 语义化版本、区间取值
    version: str
    hand_span_mm: float        # H
    reach_mm: float            # R（随把位压缩）
    v_shift_mm_per_s: float    # 换把速度上限
    r_max_hz: float            # 单指重复速率上限
    string_length_mm: float    # L
    # 悲观/乐观端由 percentile 生成，用于 GREEN/RED 的方向性

@dataclass(frozen=True)
class OracleResult:
    verdict: Verdict
    diagnostics: tuple[Diagnostic, ...]   # 空=无违规
    checker_version: str; profile_version: str

def check_playability(tab: Tab, profile: Profile) -> OracleResult: ...
def feasible_fingerings(frame: "Frame", profile: Profile) -> list["FingerAssignment"]: ...  # solver/repair 用
```
**硬谓词**（可行性门，与难度软分严格分离）：RANGE / ONE_STRING_ONE_NOTE / FINGER_COUNT / FINGER_MONOTONIC / FRET_SPAN(几何 CSP，两两欧氏距 ≤ d_max(i,j,H)) / BARRE / SHIFT_SPEED(Δx/Δt ≤ v_shift + 稳定时间，含 guide-finger) / RIGHT_HAND(同时拨弦 ≤ 可用指、单指重复 ≤ r_max) / SUSTAIN。

### B.4 编辑 DSL（`fretsure/edit_dsl.py`，Plan 3；此处冻结算子名）
`revoice(note, octave|inversion)` · `drop_note(note)` · `octave_shift(note, ±12)` · `reposition(note, string, fret)` · `refinger(frame, assignment)` · `rebarre(frame)` · `substitute_voicing(chord_segment, voicing)` · `simplify_rhythm(frame)`。恒保约束：melody 永远保留；bass 根音尽量保；和弦 pitch-class 集尽量保或最小改动。

### B.5 忠实度指标（`fretsure/metrics/fidelity.py`，Plan 4；公式冻结）
按 voice-role 用 DTW((onset,pitch)) 对齐：
- **Melody-F1** = recall/precision 调和平均；匹配需 MIDI 音高精确 + onset 在 1/16 网格内（另报八度等价宽松版 + 音高误差直方图）。
- **Bass-root-accuracy** = 强拍上「编配最低发声 pitch-class = 源和弦根/记谱低音」比例。
- **Harmony-Jaccard** = 逐和弦段「编配 pitch-class 集 vs 源 pitch-class 集」平均 Jaccard。
- **忠实度门** = (Melody-F1 ≥ τm) AND (Bass-acc ≥ τb) AND (Harmony-Jaccard ≥ τh)，阈值事先公布。

### B.6 Benchmark 指标列（`fretsure/bench/`，Plan 4；keep/cut 主指标）
`M1` pass@1（修复前单样可行，诊断用）· `M2` **pass^8**（8 次独立全可弹，可靠性门，无偏估计 n≥10/条）· `M3` 忠实（melody-F1+bass+harmony+onset）· `M4` 难度贴合 |实测−目标| · `M5` 修复迭代（诊断）· `M6` $/曲 · `M7` 难切 pass^8（快速/宽音域/异调弦/密复调泛化）。**keep/cut 主指标 = M2, M3, M4, M6 (+M7)；M1/M5 诊断用。** 参考目标（待测非结果）：M1≈0.75, M2≈0.92, M3≈0.97, M4|Δ|≈0.4, M5≈2.1, M6≈$0.30, M7≈0.80。

---

## C. 逐 Plan 设计（里程碑 + 完整内容 + 验收门）

> 每个 Plan 附：**交付的 §10 里程碑** · **Scope（模块+文件+职责）** · **Interfaces（消费/产出）** · **依赖** · **验证 & 消融纪律** · **验收门（可证伪、量化、勾选式）** · **诚实砍线/延后**。验收门以「一条命令产出、带 CI、可复现」为底线。

---

### Plan 1 —— 核心 + Oracle（地基，gate 一切）

**交付里程碑**：M1 的 oracle 半部 + oracle 验证台（§14 A.8）。**这是全项目最该狠验的一块——下游一切可信度 gate 在它上面。**

**Scope**
- `fretsure/ir.py`：Music IR（B.1）+ `validate_ir`。
- `fretsure/parse/`：music21 → MusicIR（先支持 lead sheet + MusicXML；MIDI 次之）。
- `fretsure/tab.py`：Tab / TabNote / Frame（B.2）+ tab ⇄ ASCII/JSON 序列化。
- `fretsure/geometry.py`：毫米建颈 `x_f = L·(1−2^(−f/12))`，弦距/触及模型，`d_max(i, j, H)`。
- `fretsure/oracle/predicates.py`：8 个硬谓词，各为纯函数、各自单测。
- `fretsure/oracle/csp.py`：FRET_SPAN 几何可行性 CSP（指尖指派存在性）+ 横按。
- `fretsure/oracle/core.py`：`check_playability` 三态裁决 + `Diagnostic` 生成 + `feasible_fingerings`。
- `fretsure/oracle/profiles.py`：语义化版本 profile + 3 预设（手围百分位）+ 悲观/乐观端。
- `fretsure/oracle/validation/`：property / metamorphic / mutation / N-version（慢穷举 spec vs 快生产版）+ 差分（DadaGP/GuitarSet）+ 人金标混淆矩阵 + Clopper–Pearson 误接受上界。
- 打包：`pyproject.toml` → **`fretsure-oracle` 可 pip 安装**；GitHub Actions 绿 CI。

**Interfaces**
- Consumes：music21 解析结果、DadaGP/GuitarSet 真人 tab（**仅验证 oracle 用，绝不作 agent 输入**）。
- Produces：B.1/B.2/B.3 全部类型 + `check_playability` / `feasible_fingerings` / `validate_ir`。**下游 Plan 2–7 全部依赖这些签名。**

**依赖**：无（地基）。

**验证 & 消融纪律**（本 Plan 的验证台就是「谁检查检查器」）
- **无标签自检**：property-based 旗舰不变量 **monotone-in-resources**（手更大/更慢/更低把位/r_max 更高 → 只能 FAIL→PASS，绝不反向）；metamorphic（变速单调、音高对 music21、移调几何不变、静态谓词时间反演不变）；mutation（注入故障看杀不杀，报 kill rate）；N-version（慢 spec vs 快版差分 fuzz）。
- **对真实语料差分**：DadaGP+GuitarSet 过 checker；**GuitarSet 上每个 RED = bug 单**（人真弹过）；与 Sayegh DP / Radicioni CSP / Fretting-Transformer 三角验证。
- **人手实弹金标集**（~300 条分层，含对抗近失，带实测手围琴手逐条弹 ~2–3h）：算误接受/误拒 + κ；**κ 定义 AMBER 带宽**。先校准后留出（test 金标绝不用于校准）。

**验收门**（全部满足才算过 M1 oracle 半部）
- [ ] `fretsure-oracle` 可 `pip install`，`import fretsure.oracle` 即用；CI 绿（含全套自验证）。
- [ ] `check_playability` **确定性**（同输入同 profile → 同判决 + 同诊断），判决盖 `checker_version+profile_version`。
- [ ] span 谓词用毫米（不是品数）；有一条测试证明「同品数跨度、不同把位」判决不同。
- [ ] monotone-in-resources property 测试通过（≥1000 随机 tab，无反向）。
- [ ] mutation kill rate 报告 ≥ 预注册阈值（如 ≥90%）；N-version 差分 fuzz 无分歧。
- [ ] **GREEN 误接受率 Clopper–Pearson 单侧上界**在人金标 test 集上公布（形如「0/120 已知不可弹被 GREEN；97.5% 上界 ≤X%」）+ 完整混淆矩阵 + Wilson CI。
- [ ] 3 个手围预设敏感性扫描报告（judged 随 profile 单调）。
- [ ] 诚实范围声明成文（§14 A.9）：静态几何、仅记谱速度、技巧 IN/OUT 显式标（拇指绕/点弦/推弦 → AMBER 或不支持，绝不静默 GREEN）。

**诚实砍线/延后**
- MIDI 解析、drop-D/变调夹变体可延到 Plan 2 前补齐（lead sheet + MusicXML 足够开工）。
- 若 GREEN 误接受上界过高（>预注册阈值）→ **不放松 GREEN，改宽 AMBER 带宽 + 补几何模型**，并在里程碑报告里诚实标注 oracle 有效性天花板。这条**不允许用「差不多能弹」蒙混**。

---

### Plan 2 —— 指法求解器 + M0 端到端纵切

**交付里程碑**：M0（最短纵切）+ M1 的求解器半部。

**Scope**
- `fretsure/solver/candidates.py`：每个 pitch 在 tuning/capo 下的候选 (string, fret) 集。
- `fretsure/solver/dp.py`：帧级 DP/Viterbi——状态=一帧可行 (string,fret,finger) 指派；帧内可行性调 `feasible_fingerings`（Plan 1）；转移代价=手位移动+换弦+sustain 冲突；目标=最小化总代价。
- `fretsure/solver/api.py`：`solve_fingering(ir_or_targetset, tuning, capo, profile) -> Tab | Infeasible`（无解触发修复，Plan 3）。
- `fretsure/render/ascii.py` + `fretsure/render/musicxml.py`：一份可视 tab（M0 用 ASCII/MusicXML，AlphaTab 留 Plan 6）。
- `fretsure/pipeline_m0.py`：lead sheet → 简易 LLM 提议（或先用规则 stub）→ solver → oracle 子集 → 渲染，端到端。

**Interfaces**
- Consumes：B.1 MusicIR、B.3 `feasible_fingerings`/`check_playability`、B.2 Tab。
- Produces：`solve_fingering(...)`、`Infeasible(frame, reason)`（Plan 3 修复回路消费）。

**依赖**：Plan 1（oracle + 类型）。

**验证 & 消融纪律**
- 求解器对**可解样例**给出可行解（过 oracle GREEN/AMBER，不出 RED）；对**无解样例**正确报 `Infeasible` 而非硬凑。
- 与 Sayegh 最优路径/Viterbi 上限对照（这也是 baseline B2 的种子）。
- M0 是**集成 derisk**，NOT benchmark 主张来源。

**验收门**
- [ ] M0 端到端跑通、**确定**：给定一个手造 lead sheet + 「指弹/标准调弦/初学者」→ 产出一份 oracle 判 GREEN 的指弹 tab + ASCII 渲染。
- [ ] solver 在手造可解集上 100% 给可行解；在手造无解集上 100% 报 `Infeasible`。
- [ ] 所有模块边界有 smoke test；CI 绿。
- [ ] 求解器纯确定性（同输入同解或同代价最优集）。

**诚实砍线/延后**
- OR-Tools CP-SAT **可选**：先自研 DP；仅当帧内指派/横按在自研版下成为瓶颈或不正确才引入，并写清引入理由。
- M0 的 LLM 提议可先用**规则 stub**（真 LLM 提议在 Plan 3 接入）——M0 的目的是打通数据流，不是证明编配质量。

---

### Plan 3 —— Agent 回路（自研 harness）+ verifier-guided 修复 + best-of-N + critic

**交付里程碑**：M2（修复回路是**护城河与 agent 脊柱**）。

**Scope**
- `fretsure/agent/harness.py`：**自研**编排回路——plan → emit edit-DSL → oracle → reason(读定位诊断) → 定点编辑 → re-check 到不动点；状态/checkpoint/trace 自持（后续 trace viewer 的数据源）。
- `fretsure/agent/edit_dsl.py`：B.4 算子的可执行实现 + 应用/回滚。
- `fretsure/agent/planner.py`：结构分析（调/拍/段/重复）→ 逐段策略（目标把位/CAGED/织体密度/拇指低音型/何处允许难度）→ 全局一致性 pass。
- `fretsure/agent/repair.py`：verifier-guided search——读 `Diagnostic` → LLM 推理音乐取舍 → 下 edit-DSL → 重查；有界搜索（A*/beam），目标=最小化 faithfulness cost，约束=oracle 可行。可解释日志（改了什么/为什么/代价）。
- `fretsure/agent/critic.py`：musicality critic（唯一真·第二 agent，紧收口）——评声部进行/地道声位/低音走向/织体/一致性，出 rubric 锚定结构子分。
- `fretsure/agent/search.py`：best-of-N 扇出（N 个种子 → 各过修复 → oracle+critic+忠实度打分取最优）。
- `fretsure/agent/tools.py`：工具接口 `oracle.check / solver.assign_fingering / analyze.structure|key|chords / fidelity.diff / retrieve.skills`——**承重设计=诊断格式 + 紧凑 edit-DSL（ACI）**。
- `fretsure/agent/memory.py`（可选，消融把关）：按诊断类型索引已验证编辑模式的嵌入库。

**Interfaces**
- Consumes：B.3 oracle、Plan 2 solver、B.4 edit-DSL、B.5 fidelity（Plan 4 若未就位则先用最小忠实 stub，Plan 4 替换）。
- Produces：`arrange(ir, target) -> Tab`（agent 全回路入口）+ trace/checkpoint 结构（Plan 6 消费）。

**依赖**：Plan 1、Plan 2；忠实度打分（Plan 4）可先 stub、Plan 4 完成后回填。

**验证 & 消融纪律**（每能力预注册 Δ 与砍线，跑在 Plan 4 的消融 runner 上）
- **(c) 修复=脊柱**：消融 R3（agentic vs 固定优先级修复，同可行率）。预测：同可弹下 agentic 牺牲音更少、musicality 更高。**打平→回退固定算法并写出砍线。**
- **(b) 工具/ACI**：消融 R2（全诊断 vs 只 pass/fail）。预测：无信息接口时修复迭代猛升、定预算可行率降。
- **(a) 规划**：消融 R1（全 vs 整曲一次性）。**诚实附注：约束多为局部，规划可能不划算，预期五五开、愿意砍。**
- **(d) critic**：消融 R4（全 vs 仅 oracle）+ 报 **critic-human 一致度（κ/相关）为一等结果**。
- **(e) 搜索**：消融 R5（N∈{1,2,4,8,16} 扫 + MCTS vs best-of-N 同 token 预算）。**砍线：N=1 已饱和或 MCTS 同预算不赢 → 发 best-of-N 并说明。先建 MCTS = 过度工程破绽。**
- **(f) 记忆**：消融 R6（热库 vs 冷启动）+ **留出泛化切分证明复用非泄漏**。仅当 $/曲线弯了才留。

**验收门**（阈值在 Plan 4 baseline 出来后锁定，先记为占位）
- [ ] 不可行提议中「修成可行且忠实」的比例**显著高于「仅去 repair」消融**（配对 CI，阈值 M3 后锁定）。
- [ ] 修复回路带**可解释日志**（每步：违反哪条/改了什么/faithfulness Δ/迭代号），可导出为 trace。
- [ ] **反薄壳对照就绪**：纯求解器稻草人（启发式种子+solver+oracle+固定修复，LLM 全去掉）可跑，作为「无 LLM 判断的音乐地板」。
- [ ] harness 回路**自研、可 checkpoint、可重放**（不依赖 LangGraph/Agent SDK）。
- [ ] 每个能力的 leave-one-out 消融**接线到 Plan 4 runner**（本 Plan 只需接线 + 冒烟，数值在 M3 后正式跑）。

**诚实砍线/延后**
- 记忆库、best-of-N 之上的搜索、规划——按预注册砍线，跑完消融**诚实公开砍掉的**（这本身是反 LARP 交付物）。
- 见到即砍：>2 agent 角色 / 纯编排 agent / 该传结构化数据却用 NL 消息 / 只复述段落表的规划 agent。

---

### Plan 4 —— Benchmark & eval 台（★moat，present 主角）

**交付里程碑**：M3。**尽早，因为它是主角，且它锁定 Plan 3 的所有消融阈值。**

**Scope**
- `fretsure/bench/corpus/`：5 层语料归一器（music21/MusPy → JSON note-graph）+ datasheet + 逐文件许可审计。A 真实公有 lead sheet（Wikifonia/Nottingham/thesession）· B 公有古典（Mutopia/CPDL/Hymnary）· C Lakh MIDI（逐文件核 provenance）· D **DadaGP/GuitarSet 仅验证 checker** · E **程序生成（★皇冠测试集）**。
- `fretsure/bench/generator.py`：功能和声文法采样调/拍/乐句/和弦进行 + 受约束旋律 → 无限带标语料（构造上不可污染）。
- `fretsure/bench/contamination.py`：按曲切分、变调/改速/换声位、canary 串 + grep 泄漏、真实层 vs 程序层分报（差距=记忆效应估计，本身是头牌发现）。
- `fretsure/metrics/fidelity.py`：B.5 忠实度（DTW 对齐）。
- `fretsure/metrics/difficulty.py`：难度打分（150 条专家排序 learn-to-rank）。
- `fretsure/metrics/reliability.py`：pass@k / **pass^k**（HumanEval 式无偏估计 n≥10/条）+ Wilson CI。
- `fretsure/bench/runner.py`：**leave-one-out 消融 runner**（统一 runner 跑所有配置）+ baselines（B1 前沿 LLM 原始 / B2 纯求解器 / B3 学术 id55/TART / B4 商用往返）。
- `fretsure/bench/checker_vs_judge.py`：N≈400 带人金标+对抗近失，oracle vs GPT-5.6 Sol 主评 + 独立版本化的跨供应商前沿 comparator（均跑 zero-shot/rubric，每条 5 次测翻转率），McNemar + 误接受率 + 成本对比。
- `fretsure/bench/stats.py`：Wilson / Clopper–Pearson / **按曲 cluster bootstrap** / McNemar / Holm–Bonferroni / 预注册最小可检效应与 N。
- `fretsure-bench --seed S`：一条命令重建语料+程序测试集+oracle 配置(3 预设 hashed)+全指标+CI+checker 验证报告+checker-vs-judge。

**Interfaces**
- Consumes：Plan 1 oracle（打分器）、Plan 3 `arrange`（被测系统）。
- Produces：`fidelity.diff`（回填 Plan 3 stub）、消融 runner、可复现计分板 + 全 CI。

**依赖**：Plan 1（打分）、Plan 3（被测 agent）；程序生成器/语料/统计可与 Plan 3 **并行**开工。

**验证 & 消融纪律**
- **联合成功 = 可弹 AND 忠实度门**（主头牌数），**全部分层报**（体裁×源层×难度×复调），绝不给单一混合数。
- checker-vs-judge：设计目标结果 = LLM 评委在对抗近失上误接受显著更高 + 有非零方差，oracle 确定近完美。

**验收门**
- [ ] **一条命令** `fretsure-bench --seed S` 跑出**可复现**计分板（同 seed → 同数）。
- [ ] checker-vs-LLM-judge 结果成立（LLM 评委误接受显著高于 oracle，带 McNemar odds ratio + CI）。
- [ ] 每个 agent 能力的 leave-one-out 消融过 CI（M2/M3/M4/M6/M7 主指标 + 配对 bootstrap CI）。
- [ ] 联合 Pareto（可弹 × 忠实）+ 保忠实修复率作为护栏，全部分层 + Wilson/CP CI。
- [ ] 污染控制成立：真实层 vs 程序层分报，canary grep 无泄漏。
- [ ] **头牌#1 就绪**：修复把 pass^8 从 ~0.01（一次性提议）拉到 ≥0.90，消融修复→塌回 ~0.01，且**无廉价补救对照**（升温度/best-of-N 不修复补不回）成立。

**诚实砍线/延后**
- baselines 里 id55/TART 若不可复现 → 诚实标注「无法复现」而非硬凑数。
- 人金标/专家排序是**有界一次性人力**（每次大改 <1 天），不阻塞自动指标。

---

### Plan 5 —— 难度 tier + 可验证简化 + 伴奏

**交付里程碑**：M4（难度）+ M5（伴奏）。

**Scope**
- `fretsure/difficulty/tiers.py`：tier 定义（Beginner/Intermediate/Advanced）——最大 fret_span、是否允许横按、把位范围、每小节最大换把、最大同时发声、tempo 上限、技巧集。
- `fretsure/difficulty/constraints.py`：把目标 tier 规则集**叠加进 oracle 作额外硬约束**（复用 Plan 1 谓词框架）。
- `fretsure/difficulty/simplify.py`：难度定向简化回路（T3）——难编配 + 目标档 d∈{1..5} → 复用 Plan 3 修复算子朝目标 tier 收敛，保旋律/和声。
- `fretsure/accompaniment/patterns.py`：伴奏（T2）——和弦按法 + 扫弦/分解节奏型（律动可行）。
- `fretsure/accompaniment/api.py`：`arrange_accompaniment(ir, target) -> Tab`，同样过 oracle。

**Interfaces**
- Consumes：Plan 1 oracle（叠加 tier 约束）、Plan 3 修复算子、Plan 4 难度打分器。
- Produces：`simplify_to_tier(tab, d) -> Tab`、`arrange_accompaniment(...)`。

**依赖**：Plan 1、Plan 3、Plan 4（难度打分 + 消融 runner）。

**验证 & 消融纪律**
- **可验证简化**：输出**必须通过目标 tier 的 checker**——这就是「没人做的可验证难度简化」。
- 忠实度-难度权衡曲线：越简单的 tier 保 melody、掉更多 harmony，benchmark 量化这条曲线。

**验收门**
- [ ] 输出经 **checker 证明符合所选 tier**（tier 约束作为硬门，实测难度 ≈ 目标 d，MAE/±1/Spearman vs learn-to-rank）。
- [ ] 忠实度-难度 Pareto 曲线成图（各 tier 的 melody/bass/harmony 保留率）。
- [ ] 伴奏输出过 oracle（和声/低音忠实 + 律动可行），T2 契约达标。
- [ ] 难度简化复用 Plan 3 修复算子（不新造平行修复系统——防重复）。

**诚实砍线/延后**
- tier 具体参数（span/把位/tempo 阈值）需**对真实琴手校准**，与 design partner 协作；M4 前定，先用占位参数并标「待校准」。
- 伴奏是**标配不主打**：若时间紧，伴奏可延到 UI（Plan 6）之后，但 HERO（指弹）+ 难度简化不可延。

---

### Plan 6 —— UI / trace viewer / demo / MCP（可展示=真功能）

> **分段状态（2026-07-16）**：Plan 6A 薄纵切已完成并独立闭门：bytes-first application seam、
> typed loopback FastAPI、`agent-trace@0.1.0` replay viewer、三个 stdio MCP tools，以及经用户审美认可的
> React Web。下面原始 Plan 6 的 AlphaTab、音频、真实琴颈动画、导出互操作、live A/B/榜单与真人
> money moment 仍全部 open，Plan 6A 不替代这些验收项。闭门证据见
> [`2026-07-16-plan-6a-web-api-trace-mcp.md`](2026-07-16-plan-6a-web-api-trace-mcp.md) 与
> [`PLAN6A_ACCEPTANCE.md`](../../PLAN6A_ACCEPTANCE.md)。

**交付里程碑**：M7（音频前端 best-effort + web UI 打磨 + 指板动画 + demo 脚本）。

**Scope**
- `web/`：React/TS + Vite + **AlphaTab**（tab 渲染/播放/GP 导入导出）。
- `web/fretboard/`：**自研 SVG/Canvas 指板动画**——(string,fret,finger) 动到真实琴颈、oracle 诊断驱动红/绿标注（「span 6 > max 4」）。
- `web/trace/`：**「看 agent 思考」trace viewer**——读自研回路 checkpoint + OTel GenAI spans，时间线 `PLAN → EMIT edit-DSL → ORACLE CALL → 定位诊断 → REASON(白话) → TARGETED EDIT → RE-CHECK`；每条 JSON 诊断叠一行人话。
- `web/demo/`：money moment（观众点歌→标红→修复→真人弹出）+ 现场 A/B（裸模型 vs 我们，同 prompt 预算、oracle 同施两边）+ live 榜单（确定性打分当场重算 + leave-one-out 消融行）。
- `fretsure/audio/`：FluidSynth + GeneralUser GS → MIDI→WAV 播放。
- `fretsure/mcp/server.py`：**MCP server** 暴露 `check_playability / feasible_fingerings / render_notation / render_audio`（互操作/演示用适配器，**热循环仍进程内直调 oracle**，不付网络延迟）。
- `fretsure/audio_frontend/`（best-effort，v2）：Basic Pitch + librosa 转谱 + 校对 UI，明确标「近似、需校对、不保证」。

**Interfaces**
- Consumes：Plan 3 trace/checkpoint、Plan 1 oracle、Plan 4 榜单数据、Plan 5 难度旋钮。
- Produces：web app、MCP server、demo 脚本。

**依赖**：Plan 1–5（demo 需要真回路 + 真榜单）。

**验证 & 消融纪律**
- **可展示=真功能**（spec Part F 映射表）：每个台上元素都是上线功能；只是 demo 脚手架的会被评委闻到戏 → 砍。
- 保真但有界：预筛 3–4 首候选让回路真活但不当场死锁；每个 live 步骤绑一个预缓存跑到某按键，网络抖动不毁高潮。

**验收门**
- [ ] money moment 在**未见输入**上跑通：点歌→选难度→oracle 标红→agent 修复→转绿→真人弹出 + 可听。
- [ ] 指板红/绿动画渲染**真实琴颈**（非数字格），oracle 诊断驱动颜色 + 同步播放。
- [ ] trace viewer 读**自研回路**状态历史 + OTel spans，诊断叠人话行。
- [ ] 现场 A/B **公平**（同模型/同 prompt 预算/oracle 同施两边）；live 榜单确定性当场重算 + 消融行。
- [ ] MCP server 可被 Claude Desktop/Cursor 调用；导出 GP/MusicXML/MIDI 与 Guitar Pro/TuxGuitar/MuseScore 互操作。
- [ ] Part F 映射表逐行成立（台上特性 → 真产品功能 → 底层能力）。

**诚实砍线/延后**
- 音频前端（mp3→谱）是 **best-effort/v2**，可延；符号路径完整即可 present。
- 第二 demo（OpenAI Agents SDK 调同一 MCP oracle）= 锦上添花，仅有时间且能脱稿讲才做。

---

### Plan 7 —— (stretch) DSPy/GEPA + CPU RL reranker + verifiers env

**交付里程碑**：M6（RL 小策略 + 学习曲线）+ harness stretch。**核心产品（Plan 1–6）不依赖它；允许诚实负结果。**

**Scope**
- `fretsure/opt/gepa/`（保留、消融把关）：GEPA 用书面反馈（oracle 定位诊断原样喂）进化 ①规划 prompt ②critic rubric，用确定性 checker 打分。**先跑一天 spike 再决定。**
- `fretsure/rl/reranker.py`：CPU-only ~1–3B repair-reranker/value 模型——在 oracle+critic 奖励上训，从 best-of-N 选 / 给修复编辑排序（**非生成式策略**）。
- `fretsure/rl/train.py`：PyTorch-CPU 小模型训练 + 学习曲线（playability pass@1/faithfulness vs episode）。
- `verifiers_env/`（可选）：把 oracle 发布成 Prime-Intellect `verifiers` 可验证 RL 环境。

**Interfaces**
- Consumes：Plan 4 消融 runner + oracle 奖励、Plan 3 best-of-N 候选。
- Produces：reranker（插到 Plan 3 search）、GEPA 优化后的 prompt/rubric、verifiers env 包。

**依赖**：Plan 3、Plan 4（奖励 + 消融台）。

**验证 & 消融纪律**
- **GEPA**：leave-one-out 消融（手写 prompt vs GEPA），不提升就**砍掉并说明**；GEPA 只碰 NL prompt/rubric，绝不碰 oracle/DSL/checker。
- **RL reranker**：强制对照 **RLVR reranker vs 纯 SFT 蒸馏前沿修复轨迹**——若 SFT 就拿下降本，RLVR 是装饰。仅当它以显著更低成本赢过前沿 reranker 才留。
- **RL 不上关键路径**；预注册砍线。

**验收门（二选一，允许回退）**
- [ ] **头牌#2（理想）**：~1–3B CPU RLVR repair-reranker 零专有数据，在 pass^8(≥0.90) + 忠实(≥0.97) 追平前沿提议+修复，$/曲 约 1/20，修复迭代 I0→I1。
- [ ] **诚实回退**：RL 在 CPU 出不来 → **报干净负结果**（「我们在 CPU 上试了 RLVR，这是消融，不划算」），第二头牌换成**搜索/修复 Pareto 前沿**（每 N 边际增益 + 拐点 N*）或 **SFT 蒸馏降本**。
- [ ] GEPA：消融决定 keep/cut，措辞精确（「离线 reranking」/「prompt 优化」，绝不吹「RLVR 微调大模型」）。

**诚实砍线/延后**
- 整个 Plan 7 可延；若 solo 时间不够，**核心产品在 Plan 6 结束即完整可交付**。verifiers env 发布尽力而为。

---

## D. 跨切面：验证纪律、两个头牌、诚实延后目录

### D.1 Leave-one-out 消融矩阵（keep/cut 唯一裁决，跑前预注册方向）
在 Plan 4 的统一 runner 上跑，同冻结分层套件（n≥300–500，hashed）+ 配对 bootstrap CI + 配对置换 + Holm–Bonferroni + 多种子 + 钉 oracle hash：

| 配置（移除其一） | 主指标预期 Δ | KEEP if / CUT if |
|---|---|---|
| − 规划(a) | M7/M5 动 | 过 CI 则留；否则砍（预期五五开） |
| − 工具/诊断(b) | M5 猛升、预算 Pareto | 过 CI 则留；否则砍 |
| **− 修复(c)** | **M2 pass^8 塌 0.90→~0.01** | **留（塌陷）——头牌#1** |
| − critic(d) | M3/M4 动 + 人相关 | 过 CI 且人相关则留；否则砍 |
| − 搜索(e) | M2/M6 | 仅同成本过 CI 才留；否则砍 |
| − 记忆(f) | M6 $ 弯 | $ 弯过 CI 则留；否则砍 |
| **纯求解器稻草人(−LLM)** | 全面地板 | **对「LLM 点缀」的直接反驳** |
| 换 RL reranker(g) | M6 ~1/20 保 M2/M3 | 保指标则留；否则砍（报负） |

### D.2 两个头牌结果
- **头牌#1（深度+正确）**：verifier-guided 迭代修复把指弹 pass^8 从 0.01 拉到 ≥0.90，同时 melody-F1≥0.97、在请求难度带内；消融修复→塌回 ~0.01；**无廉价补救对照**证明蛮力采样买不到 pass^8。全 checker 打分、钉 oracle hash 可复现。
- **头牌#2（成本+深度，带诚实回退）**：CPU RLVR repair-reranker 追平前沿而 $/曲 ~1/20；**回退** = 搜索/修复 Pareto 前沿或 SFT 降本；**干净可复现的负 RL 结果本身是可信作品集信号。**

### D.3 诚实延后目录（这些是「延后」不是「简化」，前置一满足即即时补齐）
- 后 6 个 Plan 的**逐行 bite-sized TDD 代码**——被类型依赖强制，其前置 Plan 锁定类型后即时撰写（各自独立 plan 文件）。
- MIDI 解析 / drop-D / 变调夹变体（初始设想为 Plan 1→2；当前冻结为 producer-driven
  MusicXML/IR 闭门后的独立计划）。
- OR-Tools CP-SAT（仅自研 DP 成瓶颈才引）。
- 音频前端 mp3→谱（best-effort/v2，Plan 6）。
- tier 具体参数校准（需 design partner，Plan 5 前）。
- 整个 Plan 7 stretch（核心在 Plan 6 结束即完整）。

### D.4 常备人力（有界，建一次复用，每次大改 <1 天）
- musicality MOS/盲 A/B（~40 条×3 人）· 难度校准专家排序（~150 条一次）· **checker 金标集琴手实弹（~300 条 ~2–3h）**· **真人 design partner**（吉他老师/琴手，校准 oracle 与难度，建议尽早找——大幅降低「合成基准不真实」质疑）。

---

## E. 里程碑 ↔ Plan 映射 + 依赖 DAG

| §10 里程碑 | 主题 | 落在 Plan | 硬验收（摘要） |
|---|---|---|---|
| M0 | 最短纵切 | Plan 2 | 端到端确定跑通、手造样例过 oracle |
| M1 | 完整 oracle + 求解器 + **验证台** | Plan 1（oracle+验证）+ Plan 2（求解器） | **误接受上界 + 混淆矩阵领跑**、property/mutation/N-version 过、pip+绿 CI |
| M2 | 修复回路 | Plan 3 | 修成可行且忠实率 > 去 repair 消融、可解释日志 |
| M3 | Benchmark 骨架 | Plan 4 | 一条命令复现、checker-vs-judge 成立、联合 Pareto |
| M4 | 难度 tier + 可验证简化 | Plan 5 | 输出 checker 证明符合 tier、忠实-难度曲线 |
| M5 | 伴奏谱 | Plan 5 | 和弦按法+节奏型过 oracle |
| M6 | RL 小策略 + 学习曲线 | Plan 7 | 曲线上升并（理想）越过前沿；或诚实负结果 |
| M7 | 音频前端 + UI + 指板动画 + demo | Plan 6 | present 可用完整体验、money moment |

**依赖 DAG**（→ = 阻塞）：
```
Plan 1 (oracle+验证台) ──┬─→ Plan 2 (solver+M0) ──→ Plan 3 (agent 回路+修复)
                         │                              │
                         └──────────────────────────────┼─→ Plan 4 (benchmark+消融) ←── 锁定 Plan 3 阈值
                                                         │        │
                                                         └────────┴─→ Plan 5 (难度+伴奏)
                                                                  │
   Plan 1..5 ─────────────────────────────────────────────────────┴─→ Plan 6 (UI/demo/MCP)
                                                                  │
   Plan 3,4 ──────────────────────────────────────────────────────┴─→ Plan 7 (stretch: GEPA/RL)
```
**关键路径硬顺序**：Plan 1 的 oracle **验证台**（混淆矩阵/误接受上界）必须在任何 benchmark 主张（Plan 4）前领跑通过——这是第 4 步不可信原则。Plan 4 与 Plan 3 可部分并行（语料/生成器/统计先行），但 Plan 3 的消融阈值在 Plan 4 baseline 出来后才锁定。

---

## F. 全局风险 → 门（诚实清单，spec §11/§C 贯穿风险）

| 风险 | 门（哪个 Plan 的哪条验收挡住） |
|---|---|
| oracle 有效性天花板（生物力学模型≠普适可弹） | Plan 1：混淆矩阵 + 误接受 CP 上界领跑；偏 GREEN soundness；范围诚实声明 |
| 忠实度作弊（削音过可弹） | Plan 4：可弹×忠实联合 Pareto + 保忠实修复率当主护栏 |
| agent 被读作「求解器+LLM 点缀」 | Plan 3/4：oracle-as-env 反转 + leave-one-out 挣存在 + 纯求解器稻草人 + 头牌#1 无廉价补救对照 |
| 过度工程 agent LARP | Plan 3：见到即砍（>2 角色/纯编排/NL 消息/复述规划）；公开砍掉的组件 |
| 污染残留（背过 UG 名谱） | Plan 4：程序生成层扛头牌 + 变调/canary + 真实层 vs 程序层分报 |
| RL 在 CPU 出不来 | Plan 7：保持 stretch、核心不依赖、预注册砍线、诚实报负 |
| 生物力学主观（手大小/拇指绕） | 全局：全参数化 profile + 对真实琴手校准 + 主张限定公布 profile |
| pass^8 部分是定义选择 | Plan 4：报完整 pass^k 曲线 |
| 成本/延迟（best-of-N×逐编辑 oracle×critic 爆 token） | Plan 3/6：上线前验墙钟可行；热循环进程内直调 oracle |

---

## 执行入口

- **当前下一步**：本 producer-driven MusicXML/IR 提交推送并核对 local/remote SHA 后，写/执行 MIDI
  详细计划；MIDI 闭门后才写 benchmark v2。任何 MIDI 前端新视觉仍先与用户确认，沿用已冻结的
  “古典制琴工坊 × 验证仪器”基线。
- 每个阶段在前置提交的 local/remote SHA 一致后才开启；阶段末做独立 scope/security/consumer 审计。
  用户审计只在新视觉、音频/听感、真人演奏或 calibration gate 出现时暂停，不把普通代码审查误写成
  用户真人阻塞。
- **本路线图是活文档**：Plan 落地后如共享契约（§B）需要改名/改签名，先改 §B 再改下游，保持跨 Plan 类型一致性。
