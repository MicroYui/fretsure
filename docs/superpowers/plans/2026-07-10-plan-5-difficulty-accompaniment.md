# Plan 5 —— 难度 tier + 可验证简化 + 伴奏

> **上游**：Plan 1（oracle）+ Plan 2（solver）+ Plan 3（agent）+ Plan 4（benchmark）已完成。roadmap Plan 5 = M4（难度）+ M5（伴奏）。分支 `plan-5-difficulty-accompaniment`。TDD；LLM 本地代理 `claude-opus-4-8`，FakeLLM 确定性注入。

**Goal:** 交付**可验证难度简化**（"把这首歌简化到你能弹的水平"，商业楔子）——tier 规则叠加进 oracle 作硬约束、输出经 checker 证明符合目标 tier；+ 伴奏谱输出（和弦按法 + 节奏型，过 oracle）。

**Architecture:** tier = 收紧的 Profile（把位/max_fret）+ 非几何硬约束（最大同时发声/是否横按/把位上限）。`check_tier` = check_playability(GREEN under tier.profile) AND tier 约束成立。简化复用 Plan 3 repair，oracle 换成 tier oracle。伴奏 = 和弦→声位→节奏型→solver→oracle。

## Global Constraints
- 可验证：难度输出**必过目标 tier 的 checker**（否则不是该 tier）。
- 复用：简化用 Plan 3 repair 算子；伴奏过 Plan 1 oracle；不另造平行系统。
- tier 参数是 v1 占位（`# CALIBRATION`，需 design partner）。忠实度-难度权衡曲线量化（Plan 4 指标）。
- 确定性 TDD；ruff+mypy clean；commit 无 AI trailer。

## File Structure
```
src/fretsure/difficulty/__init__.py
src/fretsure/difficulty/tiers.py        # Tier + BEGINNER/INTERMEDIATE/ADVANCED + tier_violations
src/fretsure/difficulty/checker.py      # check_tier(tab, tier) -> TierResult（可验证难度门）
src/fretsure/difficulty/simplify.py     # simplify_to_tier（复用 repair，tier oracle）
src/fretsure/difficulty/score.py        # measured_tier(tab) 难度打分（span/把位/换把）
src/fretsure/accompaniment/__init__.py
src/fretsure/accompaniment/patterns.py  # 节奏型（strum/arpeggio）
src/fretsure/accompaniment/api.py       # arrange_accompaniment(ir, goal) -> Tab
tests/difficulty/… tests/accompaniment/…
```

---

### Task 1: 难度 tier 定义
**Files:** `difficulty/__init__.py`, `difficulty/tiers.py`; Test `tests/difficulty/test_tiers.py`
**Interfaces:**
```python
@dataclass(frozen=True) Tier(name:str, profile:Profile, max_simultaneous:int, allow_barre:bool, max_position:int, max_shifts_per_bar:int)
BEGINNER, INTERMEDIATE, ADVANCED: Tier
def tier_violations(tab:Tab, tier:Tier, *, beats_per_bar:int=4) -> list[str]  # 非几何 tier 约束（同发声/横按/把位）
```
**Spec:** BEGINNER=首把位(max_position≤5)、无横按、≤2 同发声；INTERMEDIATE 放宽；ADVANCED 最宽。tier.profile 收紧 max_fret=max_position。tier_violations 检查：每帧同发声≤max_simultaneous；横按(同指多音)当 not allow_barre 违反；fret>max_position 违反。
- [ ] TDD：BEGINNER 拒横按/高把位/密和弦；ADVANCED 接受；确定 → commit `feat(difficulty): tier definitions + non-geometric tier constraints`。

---

### Task 2: 可验证难度门 check_tier
**Files:** `difficulty/checker.py`; Test `tests/difficulty/test_checker.py`
**Interfaces:**
```python
@dataclass(frozen=True) TierResult(meets:bool, playable:Verdict, tier_violations:tuple[str,...])
def check_tier(tab:Tab, tier:Tier, *, tempo_bpm:float=90.0) -> TierResult
# meets = check_playability(tab, tier.profile).verdict=="GREEN" AND 无 tier_violations
```
**Spec:** 这就是"没人做的可验证难度简化"的门。复用 Plan 1 oracle + Task 1 约束。
- [ ] TDD：BEGINNER 可弹简单 tab meets；难 tab（高把位/横按/大跨）not meets 且列出违反；确定 → commit `feat(difficulty): verifiable tier checker`。

---

### Task 3: 难度打分 measured_tier
**Files:** `difficulty/score.py`; Test `tests/difficulty/test_score.py`
**Interfaces:**
```python
def measured_tier(tab:Tab) -> str  # 由 max_position/横按/同发声/换把 判归 beginner/intermediate/advanced
```
**Spec:** 返回该 tab 最匹配的 tier（能通过的最低 tier）。供 Plan 4 难度准确指标。
- [ ] TDD：首把位简单 tab→beginner；高把位横按→advanced；确定 → commit `feat(difficulty): measured-tier scorer`。

---

### Task 4: 可验证简化 simplify_to_tier
**Files:** `difficulty/simplify.py`; Test `tests/difficulty/test_simplify.py`
**Interfaces:**
```python
def simplify_to_tier(target:tuple[Note,...], tier:Tier, tuning, capo, llm:LLMClient, *, tempo_bpm=90.0, max_iters=8) -> SimplifyResult
# 复用 Plan 3 repair 思路：solve+check_tier→若 not meets，LLM 读 tier 诊断下 edit（drop/octave/simplify_rhythm）→重查到 meets/预算。保 melody。
@dataclass(frozen=True) SimplifyResult(tab:Tab|None, target:tuple[Note,...], tier_result:TierResult|None, iterations:int, trace:Trace)
```
**Spec:** 与 repair 同构，但门是 check_tier（更严）。输出经 check_tier 证明符合 tier。FakeLLM 确定性 TDD + 真代理集成。
- [ ] TDD（FakeLLM）：难 target + 脚本化 edit → simplify 后 tier_result.meets、melody 保留；已 meets→0 迭代 → commit `feat(difficulty): verifiable simplify-to-tier loop`。

---

### Task 5: 伴奏输出
**Files:** `accompaniment/__init__.py`, `accompaniment/patterns.py`, `accompaniment/api.py`; Test `tests/accompaniment/test_accompaniment.py`
**Interfaces:**
```python
def strum_pattern(chord_pcs, root_pc, tuning, capo, profile, onset, beats) -> tuple[Note,...]  # 和弦声位每拍扫
def arpeggio_pattern(...) -> tuple[Note,...]
def arrange_accompaniment(ir:MusicIR, goal:ArrangeGoal, profile:Profile, *, style="arpeggio") -> Tab|Infeasible
# 逐和弦段选可弹声位 + 节奏型 → 目标音集 → solver → 过 oracle 非 RED。
```
**Spec:** T2 契约：评和声/低音忠实 + 律动可行，不评旋律承载。声位从和弦 pitch-class 在音域内挑。过 Plan 1 oracle。
- [ ] TDD：手造 lead sheet(和弦) → arrange_accompaniment → Tab 非 RED、和弦 pc 保留、确定 → commit `feat(accompaniment): chord voicings + strum/arpeggio -> playable accompaniment`。

## 执行顺序
**1(tier) → 2(check_tier) → 3(score) → 4(simplify) → 5(伴奏)。**

## Self-Review
- Spec 覆盖：tier/可验证门/打分/简化/伴奏 —— 对齐 roadmap Plan 5（M4+M5）。
- 复用 Plan 1/2/3（Profile/check_playability/repair 算子/solve_fingering/Note/Tab）。
- 诚实：tier 参数占位待校准；可验证简化"必过 checker"是硬主张；伴奏过 oracle。
