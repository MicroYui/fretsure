# Plan 2 —— 指法求解器 + M0 端到端纵切 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:test-driven-development`（每 task 先写失败测试→跑失败→最小实现→跑通过→commit）。步骤用 `- [ ]`。
>
> **上游**：Plan 1（已完成，分支 `plan-1-core-oracle`）提供 oracle 与共享契约。主路线图 `docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`（§B 契约、Plan 2 验收门）。冲突以 spec 为准。
>
> **执行环境说明**：本环境禁止"编辑型子 agent"、Sonnet 不可用 → 由主 Opus 直接 TDD 实现，只读 opus 子 agent 做独立审查。分支 `plan-2-solver-m0`（从 Plan 1 HEAD 切出）。

**Goal:** 交付确定性指法求解器（把目标音集反解成 oracle 判 GREEN/AMBER 的完整指法 Tab）+ M0 最短端到端纵切（lead sheet → 规则提议 → 求解 → oracle → ASCII 渲染）。

**Architecture:** 帧级 Viterbi DP。每个 pitch 在 tuning/capo 下有多个 (string,fret) 候选；每帧枚举"pitch→不同弦"指派 × 左手指可行指派（复用 Plan 1 `feasible_fingerings` CSP）× 右手 p-i-m-a 按弦序 → 得到该帧的可行 config 集；相邻帧转移代价 = 手心位移(mm) + 换弦；DP 最小化总代价；无可行 config 的帧触发 `Infeasible`。输出的 Tab 必过 Plan 1 oracle（非 RED）。

**Tech Stack:** 复用 Plan 1（`fretsure.oracle.*`, `fretsure.geometry`, `fretsure.ir`, `fretsure.tab`）。纯确定性、无新第三方依赖（MusicXML 导出 Task 7 才引 music21，可延后）。

## Global Constraints（每个 task 隐含包含，承 Plan 1）

- 复用 Plan 1 类型与函数，**不改 Plan 1 的公开签名**；求解器是 oracle 的"反向搜索"，二者对 `feasible_fingerings` 的可行性判定必须一致。
- **确定性**：同输入 → 同 Tab（含音符顺序）。候选/配置/DP 打破平局用稳定的确定性 tiebreak（先代价、再 string、再 fret、再 finger），无随机、无时间。
- **求解器产出的 Tab 必过 oracle**（`check_playability` 非 RED；理想 GREEN/AMBER）。对无解目标集**正确报 `Infeasible`，不硬凑**。
- frozen dataclass；`Fraction` 计时；毫米几何经 Plan 1 `geometry`。
- 右手：p 负责较低音弦、i/m/a 依次更高；每帧被拨音 ≤4；按弦序确定性指派。
- Git commit **不加** AI 共同作者 trailer。

## File Structure

```
src/fretsure/solver/__init__.py
src/fretsure/solver/candidates.py   # pitch -> [(string,fret)] 候选
src/fretsure/solver/frames.py       # FrameConfig + frame_configs 枚举（含右手指派）
src/fretsure/solver/cost.py         # hand_center + transition_cost
src/fretsure/solver/api.py          # Infeasible + solve_fingering（Viterbi DP）
src/fretsure/render/__init__.py
src/fretsure/render/ascii.py        # render_ascii(tab) -> str
src/fretsure/arrange/__init__.py
src/fretsure/arrange/propose.py     # propose_fingerstyle(ir) 规则 stub（melody+bass）
src/fretsure/pipeline_m0.py         # run_m0：propose→solve→oracle→render 端到端
tests/solver/…  tests/render/…  tests/test_pipeline_m0.py
（Task 7 可选）src/fretsure/render/musicxml.py  # music21 导出，best-effort
```

---

### Task 1: 候选生成 candidates

**Files:** Create `src/fretsure/solver/__init__.py`, `src/fretsure/solver/candidates.py`; Test `tests/solver/test_candidates.py`（+ `tests/solver/__init__.py`）

**Interfaces (Produces):**
```python
def candidates(pitch: int, tuning: tuple[int,...], capo: int, max_fret: int = 22) -> list[tuple[int,int]]
# 返回所有 (string, fret) 使 note_pitch(string,fret,tuning,capo)==pitch 且 0<=fret<=max_fret。
# 顺序确定：按 string 升序（低音弦优先）。
```

**Spec:** 用 Plan 1 `geometry.open_pitch`：对每根 string，`fret = pitch - open_pitch(string,tuning,capo)`；若 `0 <= fret <= max_fret` 收入。低音优先=string 升序。

- [ ] **Step 1: 写失败测试**
```python
from fretsure.solver.candidates import candidates
from fretsure.geometry import STANDARD_TUNING, note_pitch

def test_candidates_all_valid_and_sorted():
    c = candidates(64, STANDARD_TUNING, 0)  # E4
    assert c  # 至少一个
    assert c == sorted(c)  # string 升序
    for s, f in c:
        assert 0 <= f <= 22
        assert note_pitch(s, f, STANDARD_TUNING, 0) == 64

def test_candidates_open_high_e():
    # 高 E 弦(索引5)空弦 = 64
    assert (5, 0) in candidates(64, STANDARD_TUNING, 0)

def test_candidates_out_of_range_empty():
    assert candidates(10, STANDARD_TUNING, 0) == []  # 太低，任何弦都 <0 品

def test_candidates_capo_shifts():
    # capo 2 时，pitch 42 = 低E弦(40)+capo2 空弦
    assert (0, 0) in candidates(42, STANDARD_TUNING, 2)
```
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过（ruff/mypy clean）。
- [ ] **Step 5: Commit** `feat(solver): pitch->(string,fret) candidate generation`

**Acceptance:** 候选全部音高正确且在品域内；string 升序确定；capo/越界正确。

---

### Task 2: 帧配置枚举 frame_configs（含右手指派）

**Files:** Create `src/fretsure/solver/frames.py`; Test `tests/solver/test_frames.py`

**Interfaces (Produces):**
```python
from fretsure.tab import RightFinger
@dataclass(frozen=True)
class Placement:
    pitch: int; string: int; fret: int; left_finger: int; right_finger: RightFinger
@dataclass(frozen=True)
class FrameConfig:
    placements: tuple[Placement, ...]   # 对齐帧内音（按 string 升序）
def frame_configs(pitches: tuple[int,...], tuning: tuple[int,...], capo: int,
                  profile: Profile, *, limit: int = 64) -> list[FrameConfig]
```

**Spec:**
- 枚举把每个 pitch 指派到其候选 (string,fret) 且**弦互不相同**（一弦一音）的组合。
- 对每种"弦指派"，取被按音（fret>0）→ 用 Plan 1 `feasible_fingerings(frame_as_TabNotes, profile, capo=capo)` 得可行左手指派；开弦 left_finger=0。
- 右手：把该帧 placements 按 string 升序，依次赋 `p,i,m,a`；若音数 >4 → 该弦指派无右手方案，跳过。
- 每个 (弦指派 × 左手指派) 生成一个 `FrameConfig`（右手确定）。确定性排序（先 hand_center，再 string 序，再 fret 序）。截断到 limit。
- 空帧（无 pitch）→ 返回 `[FrameConfig(())]`（可行、无音）。

- [ ] **Step 1: 写失败测试**（单音多候选→多 config；双音可行→非空且各 config 弦不同、右手 p<i 序；六音以上或不可行→空；每个 config 用 `check_playability` 单帧构造 Tab 应非 RED）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** `feat(solver): frame config enumeration (strings x fingering x right-hand)`

**Acceptance:** 每个返回的 config 构成的单帧 Tab 过 oracle 非 RED；弦互异；右手弦序单调；不可行帧返回空。

---

### Task 3: 代价模型 cost

**Files:** Create `src/fretsure/solver/cost.py`; Test `tests/solver/test_cost.py`

**Interfaces (Produces):**
```python
def config_hand_center(config: FrameConfig, capo: int, profile: Profile) -> float | None
# 被按音绝对 press_x 均值；全开弦返回 None（手位自由）
def config_base_cost(config: FrameConfig) -> float
# 舒适度：低把位、少手指优先（如 sum(fret) + 使用手指数）
def transition_cost(prev: FrameConfig, curr: FrameConfig, capo: int, profile: Profile) -> float
# |Δ hand_center|(mm)；任一侧 None 则 0（开弦帧不计位移）
```

**Spec:** 确定性纯函数。base_cost 打破 DP 平局朝"更易按"。

- [ ] **Step 1: 写失败测试**（hand_center 对已知 config 计算；开弦帧 None；transition_cost 对同手位=0、大跳>0、含 None=0；base_cost 低把位<高把位）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** `feat(solver): hand-center + transition/base cost model`

**Acceptance:** 代价确定、单调合理（大跳更贵、低把位更便宜）。

---

### Task 4: DP 求解器 solve_fingering

**Files:** Create `src/fretsure/solver/api.py`; Test `tests/solver/test_solve.py`

**Interfaces (Produces):**
```python
from fretsure.ir import Note
@dataclass(frozen=True)
class Infeasible:
    onset: Fraction; reason: str; pitches: tuple[int, ...]
def solve_fingering(notes: Sequence[Note], tuning: tuple[int,...], capo: int,
                    profile: Profile, *, tempo_bpm: float = 90.0) -> Tab | Infeasible
```

**Spec:**
- 按 onset 分组 notes → 帧序列（每帧 = 该 onset 的目标 pitch 集，携带 duration）。
- 每帧 `frame_configs(...)`；若某帧空列表 → 返回 `Infeasible(onset, "no feasible frame config", pitches)`（第一个不可行帧）。
- Viterbi：`dp[i][c] = min over c_prev (dp[i-1][c_prev] + transition_cost(c_prev,c)) + config_base_cost(c)`；回溯最优路径。
- 由最优路径 + 各帧 onset/duration 组装 `Tab(notes, tuning, capo)`（Placement → TabNote，duration 取该 onset 音的时值）。
- **不变量**：可解输入的输出 `check_playability(tab, profile)` 非 RED。

- [ ] **Step 1: 写失败测试**
  - 单音符序列（音阶）→ 返回 Tab，`check_playability` 非 RED，音高与输入一致。
  - 旋律+低音的手造可解片段 → Tab 非 RED，melody/bass pitch 全保留。
  - 明确无解（如同帧要求两音都只能落同一根弦）→ 返回 `Infeasible`，onset 正确。
  - 确定性：两次调用相等。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** `feat(solver): Viterbi fingering solver + Infeasible`

**Acceptance:** 可解输入输出过 oracle 非 RED 且保音高；无解报 Infeasible；确定。

---

### Task 5: ASCII tab 渲染

**Files:** Create `src/fretsure/render/__init__.py`, `src/fretsure/render/ascii.py`; Test `tests/render/test_ascii.py`（+ `tests/render/__init__.py`）

**Interfaces (Produces):**
```python
def render_ascii(tab: Tab) -> str
# 6 行（高音弦在上，索引5→0），列按 onset 顺序，格子填品数，开弦=0，空位='-'。
```

**Spec:** 收集 tab 的所有 onset（升序）作为列；每根弦一行，行首标弦名（e/B/G/D/A/E）；确定性，稳定列宽。

- [ ] **Step 1: 写失败测试**（一个双音 Tab → 6 行、行含正确品数、列数=onset 数、确定）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** `feat(render): ASCII tab renderer`

**Acceptance:** 6 行、品数正确落位、确定。

---

### Task 6: 规则提议器 + M0 端到端管线

**Files:** Create `src/fretsure/arrange/__init__.py`, `src/fretsure/arrange/propose.py`, `src/fretsure/pipeline_m0.py`; Test `tests/test_pipeline_m0.py`

**Interfaces (Produces):**
```python
def propose_fingerstyle(ir: MusicIR) -> tuple[Note, ...]
# M0 规则 stub：保留 melody + bass 声部音（丢 harmony 以求最简可弹）；确定性。
@dataclass(frozen=True)
class M0Result:
    tab: Tab | None; oracle: OracleResult | None; infeasible: Infeasible | None; ascii: str | None
def run_m0(ir: MusicIR, tuning: tuple[int,...], capo: int, profile: Profile,
           *, tempo_bpm: float = 90.0) -> M0Result
# propose → solve_fingering → check_playability → render_ascii；solve 无解则 infeasible 置位。
```

**Spec:** 端到端最短纵切。M0 的"提议"是规则 stub（真 LLM 提议在 Plan 3）；目的是打通数据流。

- [ ] **Step 1: 写失败测试**
  - 手造 lead sheet IR（旋律 + 低音，标准调弦，初学者 profile 即 MEDIAN）→ `run_m0` → `result.tab` 非空、`result.oracle.verdict != "RED"`、`result.ascii` 非空、旋律音高全保留。
  - 确定性：两次 `run_m0` 相等。
  - `propose_fingerstyle`：丢弃 harmony、保留 melody+bass。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** `feat(pipeline): rule proposer + M0 end-to-end (lead sheet -> playable tab)`

**Acceptance（= M0 里程碑）：** 端到端确定跑通；手造样例产出 oracle 非 RED 的指弹 tab + ASCII；旋律保留。

---

### Task 7（可选，可延后）: MusicXML 导出

**Files:** Create `src/fretsure/render/musicxml.py`; Test `tests/render/test_musicxml.py`；`pyproject.toml` 加 `music21>=9`

**Interfaces (Produces):** `def render_musicxml(tab: Tab) -> str`（music21 stream → MusicXML）。

**Spec:** best-effort 互操作导出。music21(BSD-3)。**若引入成本高或阻塞，标注延后到 Plan 6（渲染/导出）**——M0 有 ASCII 渲染即完整，不阻塞里程碑。

- [ ] 写失败测试（往返/结构校验）→ 实现 → 通过 → commit `feat(render): MusicXML export (best-effort)`。

**Acceptance:** 导出可被 music21 重解析；或诚实标注延后。

---

## 执行顺序
**1 → 2 → 3 → 4 → 5 → 6 →（可选 7）。** Task 4 依赖 1/2/3；Task 6 依赖 4/5。

## Self-Review（作者已核）
- **Spec 覆盖**：候选/帧配置/代价/DP 求解/ASCII/M0 管线 —— 对齐 roadmap Plan 2 Scope（candidates/dp/api/render/pipeline_m0）与 M0 验收。
- **类型一致**：Note/Tab/TabNote/Profile/OracleResult/feasible_fingerings 引自 Plan 1；新增 Placement/FrameConfig/Infeasible/M0Result 本文件定义、跨 task 同名。
- **反重复**：求解器可行性判定复用 Plan 1 `feasible_fingerings`（不另写平行 CSP）；oracle 复用 `check_playability`。
- **诚实**：M0 提议是规则 stub（非 LLM），显式标注；MusicXML 可延后。
