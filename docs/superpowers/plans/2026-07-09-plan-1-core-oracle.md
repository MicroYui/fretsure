# Plan 1 —— 核心 + Oracle 实现计划（Implementation Plan）

> **For agentic workers:** REQUIRED SUB-SKILL: 用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐 task 执行。每个 task 用 `superpowers:test-driven-development`：先写失败测试 → 跑到失败 → 最小实现 → 跑到通过 → commit。步骤用 checkbox（`- [ ]`）追踪。
>
> **上游**：设计 spec `docs/superpowers/specs/2026-07-09-fretsure-design.md`（§5.5/§14 A.7–A.9 为 oracle 权威）+ 主路线图 `docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`（§B 共享契约、Plan 1 验收门）。冲突以 spec 为准。

**Goal:** 交付确定性可弹性 oracle 与其自验证台——`fretsure-oracle` 可 pip 安装、绿 CI、GREEN 误接受率有 Clopper–Pearson 上界——作为全项目的可信地基。

**Architecture:** 纯确定性函数式内核。Music IR / Tab 表示 → 毫米几何 → 8 个硬谓词（各为纯函数、各自单测）→ 三态裁决（GREEN/AMBER/RED）+ 定位化类型诊断。自验证台（property/metamorphic/mutation/N-version + 差分 + 人金标混淆矩阵）证明 oracle 本身可信。**oracle 消费的是「已带指法的 Tab」并验证它；求解器（Plan 2）反向搜索指法。**

**Tech Stack:** Python 3.11（uv 管理，系统 python 为 3.9.6，**必须用 uv 建 3.11 venv**）· pytest + hypothesis · ruff · mypy · numpy · GitHub Actions CI。music21 仅用于 metamorphic 音高对照与语料（Plan 4 才重用），Plan 1 不强依赖解析。

## Global Constraints（每个 task 隐含包含）

- **毫米几何，不用品数**：`fret_x(f, L) = L·(1 − 2^(−f/12))`，L 默认 648.0 mm（古典）。span 谓词必须用**毫米欧氏距离**。有一条测试须证明「相同品数跨度、不同把位」判决可不同。
- **三态 soundness 方向**：GREEN = 在**悲观 profile**（更小手/更慢/更严）下仍全过；RED = 在**乐观 profile**（更大手/更快）下仍违反；AMBER = 之间。**优先 GREEN 的 soundness（误接受≈0）**，不放松 GREEN，用 AMBER 吸收不确定。
- **确定性**：同 `(tab, profile)` → 同 `verdict` + 同 `diagnostics`（顺序稳定）。每个 `OracleResult` 盖 `checker_version` + `profile.version`。
- **全参数化**：hand_span_mm / reach_mm / v_shift_mm_per_s / r_max_hz / string_length_mm / max_fret / tuning / capo 全部经 `Profile` 与 `Tab` 传入，无硬编码常量泄漏进谓词（几何常量集中在 `geometry.py`）。
- **frozen dataclass + 不可变**：所有 IR/Tab/Profile/Diagnostic 用 `@dataclass(frozen=True)`；集合用 `tuple`/`frozenset`。
- **谓词纯函数**：无 I/O、无全局可变状态、无随机；`Math.random`/时间不得进入判定。
- **标准调弦**：`STANDARD_TUNING = (40, 45, 50, 55, 59, 64)`（低E→高E，MIDI）。string 索引 0=最低音弦(6弦,低E)…5=最高音弦(1弦,高E)。
- **诚实范围**：仅静态几何、仅记谱速度；技巧（拇指绕/点弦/推弦）显式标 OUT → 触发 AMBER 或不支持，**绝不静默 GREEN**。
- **许可证**：避开 GPL。仅用 BSD/MIT/Apache/LGPL 依赖。
- **Git**：commit **不加** `Co-Authored-By` 之类 AI 共同作者 trailer。分支 `plan-1-core-oracle`。

## File Structure（决定分解）

```
pyproject.toml                      # 分发名 fretsure-oracle，包名 fretsure，requires-python>=3.11
.github/workflows/ci.yml            # ruff + mypy + pytest（含 hypothesis + mutation kill-rate 门）
src/fretsure/__init__.py
src/fretsure/ir.py                  # Music IR + validate_ir
src/fretsure/tab.py                 # Tab/TabNote/Frame + frames() + JSON 往返
src/fretsure/geometry.py            # 毫米建颈 + d_max + open_pitch + 弦距
src/fretsure/oracle/__init__.py
src/fretsure/oracle/diagnostics.py  # Verdict/ViolationType/Diagnostic
src/fretsure/oracle/profiles.py     # Profile + 3 预设 + pessimistic/optimistic
src/fretsure/oracle/predicates.py   # 快速谓词：range/one-string/finger-count/monotonic/shift/sustain/right-hand
src/fretsure/oracle/csp.py          # 几何 span CSP（快 + 慢 N-version spec）+ barre + feasible_fingerings
src/fretsure/oracle/core.py         # check_playability 三态 + 诊断聚合 + CHECKER_VERSION
src/fretsure/oracle/validation/mutation.py   # 变异测试驱动 + kill-rate 报告
src/fretsure/oracle/validation/stats.py      # 混淆矩阵 + Clopper–Pearson 上界
tests/…                             # 每模块对应测试；property/metamorphic 在 tests/validation/
docs/SCOPE.md                       # 诚实范围声明（§14 A.9）
data/gold/README.md                 # 金标集 provenance + schema（数据 Plan 4/design partner 填）
```

---

### Task 1: 仓库脚手架 + CI

**Files:**
- Create: `pyproject.toml`, `.github/workflows/ci.yml`, `src/fretsure/__init__.py`, `tests/test_smoke.py`, `.gitignore`, `README.md`
- Create: `.python-version`（内容 `3.11`）

**Interfaces:**
- Produces: 可 `uv sync` + `uv run pytest` 的工程；`import fretsure` 可用；`fretsure.__version__`。

**Spec:** 用 uv 管理 Python 3.11。`pyproject.toml`：`[project] name="fretsure-oracle"`, `version="0.1.0"`, `requires-python=">=3.11"`, deps=`["numpy>=1.26"]`, optional-deps `dev=["pytest>=8","hypothesis>=6","ruff>=0.5","mypy>=1.10"]`；`[build-system]` 用 hatchling；`[tool.hatch.build.targets.wheel] packages=["src/fretsure"]`；`[tool.ruff]` + `[tool.pytest.ini_options] testpaths=["tests"]`。CI：GitHub Actions，`astral-sh/setup-uv`，跑 `uv run ruff check`、`uv run mypy src`、`uv run pytest -q`。

- [ ] **Step 1: 写冒烟测试** `tests/test_smoke.py`
```python
def test_package_imports_and_has_version():
    import fretsure
    assert isinstance(fretsure.__version__, str)
    assert fretsure.__version__
```
- [ ] **Step 2: 跑到失败** — `uv run pytest tests/test_smoke.py -q`，预期 `ModuleNotFoundError` 或 version 缺失。
- [ ] **Step 3: 最小实现** — 写 `pyproject.toml`（如上）、`src/fretsure/__init__.py`（`__version__ = "0.1.0"`）、`.python-version`、`.gitignore`（含 `.venv/`, `__pycache__/`, `.superpowers/`, `*.egg-info/`, `.pytest_cache/`, `.mypy_cache/`）、`README.md`（一句话 + `uv sync && uv run pytest`）、`.github/workflows/ci.yml`。运行 `uv sync --extra dev`。
- [ ] **Step 4: 跑到通过** — `uv run pytest -q` → 1 passed；`uv run ruff check` → clean。
- [ ] **Step 5: Commit** — `git add -A && git commit -m "chore: scaffold fretsure-oracle package + CI"`

**Acceptance:** `uv run pytest -q` 通过；`uv run ruff check`/`uv run mypy src` clean；CI 文件存在且语义正确；`pip install -e .` 亦可（`uv pip install -e .`）。

---

### Task 2: Music IR + validate_ir

**Files:** Create `src/fretsure/ir.py`; Test `tests/test_ir.py`

**Interfaces (Produces):**
```python
VoiceRole = Literal["melody", "bass", "inner"]
@dataclass(frozen=True) Note(onset: Fraction, duration: Fraction, pitch: int, voice: VoiceRole)
@dataclass(frozen=True) ChordSymbol(onset: Fraction, symbol: str, pitch_classes: frozenset[int], root_pc: int)
@dataclass(frozen=True) Meta(key: str, time_sig: tuple[int,int], tempo_bpm: float, source: str, title: str, license: str)
@dataclass(frozen=True) MusicIR(notes: tuple[Note,...], chords: tuple[ChordSymbol,...], meta: Meta)
@dataclass(frozen=True) IRViolation(kind: str, detail: str, onset: Fraction | None)
def validate_ir(ir: MusicIR) -> list[IRViolation]
```

**Spec:** `validate_ir` 检查不变量并返回违规列表（空=合法）：
- `duration>0` 每个 Note（否则 `kind="nonpositive_duration"`）。
- `pitch ∈ [0,127]`（`kind="pitch_range"`）。
- 每个 onset 至少一个 melody 音（`kind="missing_melody"`，可关：若某 onset 无 melody 但有其它声部则记 violation）。
- 同一 (voice="melody") 声部内不得有同 onset 两音高不同的重叠（melody 单声部，`kind="melody_polyphony"`）。
- `root_pc ∈ [0,11]` 且 `∈ pitch_classes`（`kind="bad_chord_root"`）。
- onset/duration 为 `Fraction`。

- [ ] **Step 1: 写失败测试** `tests/test_ir.py`
```python
from fractions import Fraction as F
from fretsure.ir import Note, ChordSymbol, Meta, MusicIR, validate_ir

def _meta(): return Meta("C", (4,4), 90.0, "unit", "t", "PD")

def test_valid_ir_has_no_violations():
    ir = MusicIR(
        notes=(Note(F(0),F(1),60,"melody"), Note(F(0),F(1),48,"bass")),
        chords=(ChordSymbol(F(0),"C",frozenset({0,4,7}),0),),
        meta=_meta())
    assert validate_ir(ir) == []

def test_nonpositive_duration_flagged():
    ir = MusicIR((Note(F(0),F(0),60,"melody"),), (), _meta())
    assert any(v.kind=="nonpositive_duration" for v in validate_ir(ir))

def test_melody_polyphony_flagged():
    ir = MusicIR((Note(F(0),F(1),60,"melody"), Note(F(0),F(1),62,"melody")), (), _meta())
    assert any(v.kind=="melody_polyphony" for v in validate_ir(ir))

def test_bad_chord_root_flagged():
    ir = MusicIR((Note(F(0),F(1),60,"melody"),),
                 (ChordSymbol(F(0),"C",frozenset({0,4,7}),2),), _meta())
    assert any(v.kind=="bad_chord_root" for v in validate_ir(ir))

def test_dataclasses_are_frozen():
    n = Note(F(0),F(1),60,"melody")
    import dataclasses, pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.pitch = 61
```
- [ ] **Step 2: 跑到失败** — `uv run pytest tests/test_ir.py -q`，预期 ImportError。
- [ ] **Step 3: 实现** `src/fretsure/ir.py`（frozen dataclass + validate_ir 逐条实现）。
- [ ] **Step 4: 跑到通过** — 全绿；ruff/mypy clean。
- [ ] **Step 5: Commit** — `git commit -am "feat(ir): Music IR types + validate_ir invariants"`

**Acceptance:** 上述 5 测试通过；所有 dataclass frozen；validate_ir 覆盖 spec 全部不变量。

---

### Task 3: Tab 表示 + frames() + JSON 往返

**Files:** Create `src/fretsure/tab.py`; Test `tests/test_tab.py`

**Interfaces (Produces):**
```python
RightFinger = Literal["p","i","m","a"]
@dataclass(frozen=True) TabNote(onset: Fraction, duration: Fraction, string: int, fret: int, left_finger: int, right_finger: RightFinger)
@dataclass(frozen=True) Tab(notes: tuple[TabNote,...], tuning: tuple[int,...], capo: int)
Frame = tuple[TabNote, ...]                      # 同一 onset 同时发声
def frames(tab: Tab) -> list[Frame]              # 按 onset 升序分组，组内按 string 升序
def tab_to_json(tab: Tab) -> str
def tab_from_json(s: str) -> Tab                 # 往返恒等
```

**Spec:** `frames` 按 `onset` 分组（`Fraction` 键），返回按 onset 升序的 list；每组内 TabNote 按 `string` 升序稳定排序。JSON：Fraction 序列化为 `"num/den"` 字符串；`tab_from_json(tab_to_json(t)) == t`。

- [ ] **Step 1: 写失败测试** `tests/test_tab.py`
```python
from fractions import Fraction as F
from fretsure.tab import TabNote, Tab, frames, tab_to_json, tab_from_json

def _tab():
    return Tab(
        notes=(TabNote(F(0),F(1),0,3,3,"p"),
               TabNote(F(0),F(1),5,0,0,"a"),
               TabNote(F(1),F(1),2,2,2,"i")),
        tuning=(40,45,50,55,59,64), capo=0)

def test_frames_grouped_and_sorted():
    fr = frames(_tab())
    assert len(fr)==2
    assert [n.string for n in fr[0]]==[0,5]      # onset 0，两音，按 string 升序
    assert [n.onset for n in fr[0]]==[F(0),F(0)]
    assert [n.string for n in fr[1]]==[2]

def test_json_roundtrip_identity():
    t=_tab()
    assert tab_from_json(tab_to_json(t))==t

def test_tabnote_frozen():
    import dataclasses, pytest
    n=TabNote(F(0),F(1),0,3,3,"p")
    with pytest.raises(dataclasses.FrozenInstanceError):
        n.fret=4
```
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过（ruff/mypy clean）。
- [ ] **Step 5: Commit** — `git commit -am "feat(tab): Tab/TabNote/Frame + frames() + JSON roundtrip"`

**Acceptance:** frames 分组/排序正确；JSON 往返恒等；frozen。

---

### Task 4: 毫米几何 + d_max + open_pitch

**Files:** Create `src/fretsure/geometry.py`; Test `tests/test_geometry.py`

**Interfaces (Produces):**
```python
STANDARD_TUNING: tuple[int,...] = (40,45,50,55,59,64)
STRING_SPACING_MM: float = 10.5        # 相邻弦中心距（v1 常数，标注待校准）
DEFAULT_STRING_LENGTH_MM: float = 648.0
def fret_x(f: int, L: float = 648.0) -> float          # 品线到上弦枕距 = L*(1-2^(-f/12)); f=0→0.0
def press_x(f: int, L: float = 648.0) -> float | None  # 按弦点：f>=1 时 (fret_x(f-1)+fret_x(f))/2；f=0→None
def string_y(string: int) -> float                     # string * STRING_SPACING_MM
def fingertip_xy(string: int, fret: int, L: float = 648.0) -> tuple[float,float] | None  # None if fret==0
def euclid(a: tuple[float,float], b: tuple[float,float]) -> float
def d_max(i: int, j: int, hand_span_mm: float) -> float # i,j∈1..4；(abs(i-j)/3.0)*hand_span_mm
def open_pitch(string: int, tuning: tuple[int,...], capo: int) -> int   # tuning[string]+capo
def note_pitch(string: int, fret: int, tuning: tuple[int,...], capo: int) -> int  # open_pitch+fret
```

**Spec:** 几何模型 v1（标注 `# CALIBRATION: fit d_max/spacing against real players — roadmap D.4`）。d_max：finger 1..4 跨 3 个 gap = 满手跨度，故 `d_max(i,j,H)=(|i-j|/3)*H`。

- [ ] **Step 1: 写失败测试** `tests/test_geometry.py`
```python
import math
from fretsure.geometry import (fret_x, press_x, string_y, fingertip_xy, euclid,
    d_max, open_pitch, note_pitch, STANDARD_TUNING)

def test_fret_x_octave_is_half_scale():
    assert math.isclose(fret_x(12, 648.0), 324.0, rel_tol=1e-9)   # 12 品 = L/2
    assert fret_x(0, 648.0)==0.0

def test_fret_x_monotonic_increasing():
    xs=[fret_x(f) for f in range(0,13)]
    assert all(b>a for a,b in zip(xs,xs[1:]))

def test_press_x_open_is_none():
    assert press_x(0) is None
    assert press_x(1) is not None

def test_fingertip_open_none_fretted_xy():
    assert fingertip_xy(0,0) is None
    assert fingertip_xy(3,5) is not None

def test_d_max_monotonic_in_span_and_gap():
    assert d_max(1,4,100.0) > d_max(1,2,100.0)                    # 更大 gap → 更大上限
    assert d_max(1,4,120.0) > d_max(1,4,100.0)                    # 更大手 → 更大上限
    assert math.isclose(d_max(1,4,120.0), 120.0)                  # 1↔4 = 满手跨

def test_open_pitch_and_note_pitch():
    assert open_pitch(0, STANDARD_TUNING, 0)==40
    assert open_pitch(0, STANDARD_TUNING, 2)==42                  # capo 2
    assert note_pitch(0,3,STANDARD_TUNING,0)==43

def test_euclid():
    assert math.isclose(euclid((0,0),(3,4)),5.0)
```
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** — `git commit -am "feat(geometry): mm neck model, d_max, open_pitch"`

**Acceptance:** `fret_x(12)=L/2`；press_x 开弦 None；d_max 对 gap 与手跨单调；open_pitch/capo 正确。

---

### Task 5: 诊断类型 + 快速左手谓词（range/one-string/finger-count/monotonic）

**Files:** Create `src/fretsure/oracle/__init__.py`, `src/fretsure/oracle/diagnostics.py`, `src/fretsure/oracle/predicates.py`; Test `tests/oracle/test_predicates_lh.py`

**Interfaces (Produces):**
```python
Verdict = Literal["GREEN","RED","AMBER"]
ViolationType = Literal["RANGE","ONE_STRING_ONE_NOTE","FINGER_COUNT","FINGER_MONOTONIC",
                        "FRET_SPAN","BARRE_INFEASIBLE","SHIFT_SPEED","RIGHT_HAND","SUSTAIN_CONFLICT"]
@dataclass(frozen=True) Diagnostic(measure: int, beat: Fraction, violation_type: ViolationType,
    offending_notes: tuple[int,...], overage: float, suggested_relaxations: tuple[str,...])
# 谓词签名（均纯函数，返回 list[Diagnostic]，note 索引指向 tab.notes 全局下标）
def check_range(tab: Tab, profile: Profile, *, beats_per_bar: int=4) -> list[Diagnostic]
def check_one_string_one_note(tab, profile, *, beats_per_bar=4) -> list[Diagnostic]
def check_finger_count(tab, profile, *, beats_per_bar=4) -> list[Diagnostic]
def check_finger_monotonic(tab, profile, *, beats_per_bar=4) -> list[Diagnostic]
```
（`Profile` 由 Task 8 提供；本 task 仅用 `profile.max_fret`。先在 predicates.py `from .profiles import Profile`，Task 顺序保证 Task 8 在依赖前完成——若并行，实现 stub Profile 于 profiles.py 再由 Task 8 扩展。**执行顺序：本 task 前先做 Task 8 或同 PR。** 为解耦，Task 顺序改为：先 Task 8，再本 task。见下方「执行顺序」。）

**Spec（每谓词生成 Diagnostic，overage 语义如注）：**
- `check_range`：每 note `0 ≤ fret ≤ profile.max_fret`。越界 → RANGE，`overage=fret-max_fret`（或 `-fret`），`suggested_relaxations=("octave_down_bass",)` 若是低音。
- `check_one_string_one_note`：每 frame 同 string 出现 >1 note → ONE_STRING_ONE_NOTE，offending=该组下标，overage=冲突数。
- `check_finger_count`：每 frame 中被按（fret>0）note 的**不同 fret 数** >4 → FINGER_COUNT（单调 ⇒ 不同品需不同指），overage=distinct_frets-4。
- `check_finger_monotonic`：每 frame 内两个被按 note，`fret_a<fret_b ⇒ finger_a≤finger_b` 且 `finger_a==finger_b ⇒ fret_a==fret_b`（同指必同品=横按）。违反 → FINGER_MONOTONIC。
- measure/beat：`measure = int(onset // beats_per_bar)+1`，`beat = onset % beats_per_bar + 1`。

- [ ] **Step 1: 写失败测试** `tests/oracle/test_predicates_lh.py`（各谓词 正例返回 []、反例返回对应 ViolationType，至少 8 个用例，含 measure/beat 计算断言）。示例：
```python
from fractions import Fraction as F
from fretsure.tab import TabNote, Tab
from fretsure.oracle.profiles import MEDIAN_HAND
from fretsure.oracle.predicates import (check_range, check_one_string_one_note,
    check_finger_count, check_finger_monotonic)

TUN=(40,45,50,55,59,64)
def _t(notes): return Tab(tuple(notes),TUN,0)

def test_range_violation_over_maxfret():
    t=_t([TabNote(F(0),F(1),0,99,1,"p")])
    d=check_range(t,MEDIAN_HAND)
    assert d and d[0].violation_type=="RANGE"

def test_one_string_one_note():
    t=_t([TabNote(F(0),F(1),2,3,1,"i"),TabNote(F(0),F(1),2,5,2,"m")])
    d=check_one_string_one_note(t,MEDIAN_HAND)
    assert d and d[0].violation_type=="ONE_STRING_ONE_NOTE"

def test_finger_count_over_four_distinct_frets():
    notes=[TabNote(F(0),F(1),i,f,f,"p") for i,f in zip(range(5),[1,2,3,4,5])]
    d=check_finger_count(_t(notes),MEDIAN_HAND)
    assert d and d[0].violation_type=="FINGER_COUNT"

def test_finger_monotonic_violation():
    t=_t([TabNote(F(0),F(1),1,2,3,"p"),TabNote(F(0),F(1),2,5,1,"i")])  # 高品低指
    d=check_finger_monotonic(t,MEDIAN_HAND)
    assert d and d[0].violation_type=="FINGER_MONOTONIC"

def test_measure_beat_from_onset():
    t=_t([TabNote(F(5),F(1),0,99,1,"p")])  # onset 5，4/4 → 第2小节第2拍
    d=check_range(t,MEDIAN_HAND)[0]
    assert d.measure==2 and d.beat==F(2)
```
- [ ] **Step 2–4:** 跑失败 → 实现（diagnostics.py + predicates.py 四谓词）→ 跑通过。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): diagnostics + fast left-hand predicates"`

**Acceptance:** 四谓词正/反例正确；measure/beat 计算正确；overage 有意义。

---

### Task 6: 几何 span CSP + barre + feasible_fingerings（含慢 N-version spec）

**Files:** Create `src/fretsure/oracle/csp.py`; extend `predicates.py`（`check_fret_span`, `check_barre`）; Test `tests/oracle/test_csp.py`

**Interfaces (Produces):**
```python
FingerAssignment = tuple[int,...]   # 每个「被按 note」的 left_finger（1..4），顺序对齐输入 frame 的被按 note
def feasible_finger_assignment(frame: Frame, profile: Profile) -> FingerAssignment | None   # 快版（回溯+剪枝）
def feasible_finger_assignment_bruteforce(frame: Frame, profile: Profile) -> FingerAssignment | None  # 慢 N-version spec（全枚举）
def feasible_fingerings(frame: Frame, profile: Profile) -> list[FingerAssignment]           # 供 solver/repair
def check_fret_span(tab: Tab, profile: Profile, *, beats_per_bar: int=4) -> list[Diagnostic]  # 验证 tab 给定指法的几何可行
def check_barre(tab: Tab, profile: Profile, *, beats_per_bar: int=4) -> list[Diagnostic]
```

**Spec（几何可行性，验证 tab 已给的 left_finger）：**
- `check_fret_span`：每 frame，对**不同指**的被按 note 对，`euclid(fingertip_xy(...), fingertip_xy(...)) ≤ d_max(fi,fj,profile.hand_span_mm)`（L=`profile.string_length_mm`）。超限 → FRET_SPAN，`overage = dist - d_max`（毫米），`suggested_relaxations` 含 `("drop_5th","shift_to_lower_position")`。
- `check_barre`：同指多 note 必同品（否则 BARRE_INFEASIBLE）；且横按指所在品 ≤ 其所跨弦上任何 note 的品（横按=最低品，其下无更低品需求）。违反 → BARRE_INFEASIBLE。
- `feasible_finger_assignment`：**搜索**满足〔单调 + 同指同品 + 不同指几何距 ≤ d_max + ≤4 指〕的指派；回溯 + 剪枝；无解 None。
- `feasible_finger_assignment_bruteforce`：`itertools.product(range(1,5), repeat=n)` 全枚举 + 同一合法性判定 —— **独立第二实现（N-version spec）**，慢但显然正确，用于差分。
- `feasible_fingerings`：返回所有可行指派（≤ 某上界，如 64）。

- [ ] **Step 1: 写失败测试** `tests/oracle/test_csp.py`
```python
from fractions import Fraction as F
from fretsure.tab import TabNote, Tab
from fretsure.oracle.profiles import MEDIAN_HAND, SMALL_HAND
from fretsure.oracle.csp import (feasible_finger_assignment,
    feasible_finger_assignment_bruteforce, feasible_fingerings)
from fretsure.oracle.predicates import check_fret_span
TUN=(40,45,50,55,59,64)
def _f(notes): return tuple(notes)

def test_easy_chord_feasible():
    # 相邻弦相邻品，简单和弦
    fr=_f([TabNote(F(0),F(1),0,1,1,"p"),TabNote(F(0),F(1),1,2,2,"i")])
    assert feasible_finger_assignment(fr,MEDIAN_HAND) is not None

def test_impossible_stretch_infeasible():
    # 1 品与 15 品同 frame，人手够不着
    fr=_f([TabNote(F(0),F(1),0,1,1,"p"),TabNote(F(0),F(1),1,15,4,"a")])
    assert feasible_finger_assignment(fr,MEDIAN_HAND) is None

def test_same_fret_span_different_position_differs():
    # 「相同品数跨度、不同把位」判决可不同（低把位更宽）——毫米几何而非品数
    low =_f([TabNote(F(0),F(1),0,1,1,"p"),TabNote(F(0),F(1),1,5,4,"a")])   # 品1..5，低把位
    high=_f([TabNote(F(0),F(1),0,10,1,"p"),TabNote(F(0),F(1),1,14,4,"a")]) # 品10..14，同为4品跨
    lo=feasible_finger_assignment(low,SMALL_HAND) is not None
    hi=feasible_finger_assignment(high,SMALL_HAND) is not None
    assert lo != hi or (not lo and not hi)  # 至少不因「4品跨」一刀切；高把位应更易
    assert feasible_finger_assignment(high,SMALL_HAND) is not None  # 高把位可行

def test_nversion_fast_matches_bruteforce():
    import itertools, random
    # 差分：多个随机 frame，快版与慢版判「有解/无解」一致
    for seed in range(50):
        rng=random.Random(seed)
        n=rng.randint(1,4)
        notes=[TabNote(F(0),F(1),s,rng.randint(1,12),0,"p") for s in range(n)]
        fr=_f(notes)
        a=feasible_finger_assignment(fr,MEDIAN_HAND) is not None
        b=feasible_finger_assignment_bruteforce(fr,MEDIAN_HAND) is not None
        assert a==b, f"seed {seed}: fast={a} slow={b}"

def test_check_fret_span_flags_bad_given_fingering():
    t=Tab(_f([TabNote(F(0),F(1),0,1,1,"p"),TabNote(F(0),F(1),1,15,2,"i")]),TUN,0)
    d=check_fret_span(t,MEDIAN_HAND)
    assert d and d[0].violation_type=="FRET_SPAN" and d[0].overage>0
```
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过（**N-version 差分测试必须绿**）。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): geometric span CSP + barre + N-version spec"`

**Acceptance:** 快版/慢版差分 50 例一致；不可能跨度判 None；「相同品数跨度不同把位」判决体现毫米几何；check_fret_span overage 为正毫米。

---

### Task 7: 时间谓词（shift-speed / sustain）

**Files:** Extend `predicates.py`; Test `tests/oracle/test_predicates_temporal.py`

**Interfaces (Produces):**
```python
def check_shift_speed(tab: Tab, profile: Profile, *, beats_per_bar: int=4) -> list[Diagnostic]
def check_sustain(tab: Tab, profile: Profile, *, beats_per_bar: int=4) -> list[Diagnostic]
```

**Spec:**
- 时间换算：`seconds(beats) = beats * 60 / profile... ` —— tempo 来自哪？Tab 无 tempo。**决定**：`check_shift_speed`/`check_sustain` 增 `tempo_bpm: float` 关键字参数（默认 90.0），`Δt_seconds = Δbeats * 60 / tempo_bpm`。core.check_playability 会把 tempo 透传。
- `check_shift_speed`：相邻 frame 的**手心位置** = 该 frame 被按 note 的 `press_x` 均值（无被按音则跳过）。`Δx=|hc2-hc1|`（mm），`Δt`=两 frame onset 差换秒。若 `Δx/Δt > profile.v_shift_mm_per_s` → SHIFT_SPEED，`overage=Δx/Δt - v_shift`。guide-finger 缓解：若两 frame 有相同 (string,fret,finger) 持续，则该次不计（v1 简化：若交集非空则放行）。
- `check_sustain`：两 note 时间重叠（`onset<other.onset+other.dur` 且反之）、`left_finger` 相同且 `(string,fret)` 不同 → SUSTAIN_CONFLICT（一根手指不能同时在两处）。

- [ ] **Step 1: 写失败测试**（含：快速大跳越限 → SHIFT_SPEED；慢速同跳 → 无；同指两处重叠 → SUSTAIN；guide-finger 放行）。
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): temporal predicates (shift-speed, sustain)"`

**Acceptance:** tempo 影响 SHIFT_SPEED（越快越易违规，单调）；sustain 冲突正确；guide-finger 放行。

---

### Task 8: Profile + 3 预设 + pessimistic/optimistic

**Files:** Create `src/fretsure/oracle/profiles.py`; Test `tests/oracle/test_profiles.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True) Profile(version:str, hand_span_mm:float, reach_mm:float,
    v_shift_mm_per_s:float, r_max_hz:float, string_length_mm:float, max_fret:int=22)
SMALL_HAND: Profile   # version "small@0.1", hand_span_mm=90
MEDIAN_HAND: Profile  # version "median@0.1", hand_span_mm=100
LARGE_HAND: Profile   # version "large@0.1", hand_span_mm=115
def pessimistic(p: Profile) -> Profile   # 更严：hand_span*0.9, reach*0.9, v_shift*0.9, r_max*0.9
def optimistic(p: Profile) -> Profile    # 更宽：*1.1
```
预设数值全部标注 `# PLACEHOLDER CALIBRATION — fit against real players (roadmap D.4)`。v_shift≈500 mm/s、r_max≈8 Hz、reach≈50、string_length_mm=648 为占位。

- [ ] **Step 1: 写失败测试**（3 预设存在、version 字符串、pessimistic 各字段严于原、optimistic 宽于原、frozen）。
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): Profile presets + pessimistic/optimistic"`

**Acceptance:** pessimistic(p) 每个「越小越严」字段 ≤ p 且「越大越严」字段 ≥ p；version 携带。**注：本 task 应在 Task 5–7 之前执行**（predicates 依赖 Profile）。见执行顺序。

---

### Task 9: check_playability 三态裁决 + 诊断聚合 + 版本戳

**Files:** Create `src/fretsure/oracle/core.py`; Test `tests/oracle/test_core.py`

**Interfaces (Produces):**
```python
CHECKER_VERSION: str = "oracle@0.1.0"
@dataclass(frozen=True) OracleResult(verdict:Verdict, diagnostics:tuple[Diagnostic,...],
    checker_version:str, profile_version:str)
ALL_PREDICATES = (check_range, check_one_string_one_note, check_finger_count,
    check_finger_monotonic, check_fret_span, check_barre, check_shift_speed,
    check_sustain, check_right_hand)   # check_right_hand 见 Task 10；本 task 先不含它，Task 10 追加
def check_playability(tab: Tab, profile: Profile, *, tempo_bpm: float=90.0, beats_per_bar: int=4) -> OracleResult
```

**Spec（三态方向 = soundness）：**
- `_all_diags(tab, prof)` = 汇总所有谓词（稳定排序：先 onset，再 violation_type，再 offending）。
- `verdict`：
  - 若 `optimistic(profile)` 下 `_all_diags` 非空 → **RED**（乐观都过不了）。
  - 否则若 `pessimistic(profile)` 下 `_all_diags` 为空 → **GREEN**（悲观仍全过）。
  - 否则 → **AMBER**。
- `diagnostics` = 用 `profile`（中位）跑出的诊断（定位用），无论 verdict。
- 盖 `CHECKER_VERSION` + `profile.version`。确定性：同输入同输出（含 diagnostics 顺序）。

- [ ] **Step 1: 写失败测试** `tests/oracle/test_core.py`
```python
# 构造三个 tab：明显可弹(GREEN)、明显不可弹(RED，15品大跨)、边缘(AMBER，介于悲观/乐观之间)
# 断言 verdict、checker_version、profile_version、确定性（跑两次相等）
```
- [ ] **Step 2–4:** 跑失败 → 实现 → 跑通过（含确定性测试：`check_playability(t,p)==check_playability(t,p)`）。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): three-state check_playability + version stamping"`

**Acceptance:** GREEN/RED/AMBER 三例正确；版本戳正确；确定性；GREEN⊆pessimistic-pass、RED⊆optimistic-fail 的方向成立。

---

### Task 10: 右手谓词（p-i-m-a）+ 接入 core

**Files:** Extend `predicates.py`（`check_right_hand`）+ `core.py`（加入 ALL_PREDICATES）; Test `tests/oracle/test_predicates_rh.py`

**Interfaces (Produces):**
```python
def check_right_hand(tab: Tab, profile: Profile, *, tempo_bpm: float=90.0, beats_per_bar: int=4) -> list[Diagnostic]
```

**Spec:**
- 每 frame：被拨 note（全部发声 note）各需一个 right_finger；同一 frame 不得两 note 同一 right_finger（一指不能同时拨两弦）→ RIGHT_HAND。
- 弦序单调：`p` 负责较低音弦、`i/m/a` 依次更高——若 right_finger 顺序与 string 顺序矛盾（如 `a` 在比 `p` 更低的弦）→ RIGHT_HAND。
- 同时发声 note 数 > 4（p,i,m,a）→ RIGHT_HAND。
- 单指重复速率：相邻 frame 用同一 right_finger 且 `Δt_seconds < 1/profile.r_max_hz` → RIGHT_HAND，`overage = r_max - 1/Δt`。

- [ ] **Step 1: 写失败测试**（一指拨两弦、弦序倒置、>4 同发声、重复过快各一例；正例返回 []）。
- [ ] **Step 2–4:** 跑失败 → 实现 → 接入 `ALL_PREDICATES` → 跑通过（core 测试仍绿）。
- [ ] **Step 5: Commit** — `git commit -am "feat(oracle): right-hand p-i-m-a predicate + wire into core"`

**Acceptance:** 四类右手违规正确；接入 core 后 GREEN/RED/AMBER 测试仍绿。

---

### Task 11: 自验证 —— property-based（monotone-in-resources）+ metamorphic

**Files:** Create `tests/validation/test_property_monotone.py`, `tests/validation/test_metamorphic.py`

**Interfaces:** 消费 `check_playability` + geometry；用 hypothesis 生成随机 Tab。

**Spec（旗舰不变量）：**
- **monotone-in-resources**：对随机 tab，`verdict_rank(check_playability(tab, bigger_profile)) ≥ verdict_rank(check_playability(tab, smaller_profile))`，其中 `bigger` = 更大手/更快 v_shift/更高 r_max，`rank(RED)=0<AMBER=1<GREEN=2`。**只能 RED→AMBER→GREEN，绝不反向。** 用 hypothesis `@given` 生成 ≥200 例（`max_examples`）。
- **metamorphic**：
  - 变速单调：tempo 越快，SHIFT_SPEED/RIGHT_HAND 违规只增不减（verdict 不上升）。
  - 移调几何不变：所有 note fret 同时 +k（仍在 range 内）且 string 不变，静态几何谓词（span/monotonic/finger-count）判决与几何一致（沿弦平移；用 mm 检查）。
  - 时间反演不变：把 onset 序列反演（保持相对间隔），**静态**谓词（非时间类）判决不变。

- [ ] **Step 1: 写测试**（hypothesis strategies 生成合法 Tab；三条 metamorphic）。
- [ ] **Step 2: 跑** —— 若发现反例即为 oracle bug，**修 oracle**（回到相关 Task 的实现）直到 property 绿；不得放宽 property 迁就 bug。
- [ ] **Step 3: Commit** — `git commit -am "test(validation): monotone-in-resources property + metamorphic invariants"`

**Acceptance:** monotone-in-resources ≥200 例无反向；三条 metamorphic 绿；若曾发现反例，附一句 commit 说明修了什么。

---

### Task 12: 自验证 —— 变异测试驱动 + kill-rate 门

**Files:** Create `src/fretsure/oracle/validation/mutation.py`, `tests/validation/test_mutation.py`

**Interfaces (Produces):**
```python
def run_mutation_suite(seed: int=0) -> MutationReport   # 对谓词注入一组预定义故障，跑金标微测试集，返回 kill 统计
@dataclass(frozen=True) MutationReport(total:int, killed:int, survived:tuple[str,...])
def kill_rate(r: MutationReport) -> float
```

**Spec:** 内置一组「变异算子」（如：span 用 `>=` 换 `>`、d_max 乘 10、range 去掉上界、monotonic 反号、finger-count 阈值 4→99）——每个变异应让至少一个内置微测试**失败**（被 kill）。`run_mutation_suite` 施加每个变异、跑内置微测试集、统计被 kill 数。**CI 门：kill_rate ≥ 0.9**。变异实现用「注入替身谓词 + 复算」而非改源文件（保持纯净）。

- [ ] **Step 1: 写测试** `tests/validation/test_mutation.py`：`assert kill_rate(run_mutation_suite()) >= 0.9` + `assert not survived`（或列出存活并解释）。
- [ ] **Step 2–4:** 实现 mutation.py（变异算子表 + 微测试集 + runner）→ 跑到通过。若某变异存活 → 补微测试直到 kill。
- [ ] **Step 5: Commit** — `git commit -am "test(validation): mutation suite with kill-rate gate >=0.9"`

**Acceptance:** kill_rate ≥ 0.9；存活变异为空或有书面解释；CI 纳入该门。

---

### Task 13: 自验证 —— 差分语料桩 + 混淆矩阵 + Clopper–Pearson 上界 + 范围声明

**Files:** Create `src/fretsure/oracle/validation/stats.py`, `tests/validation/test_stats.py`, `docs/SCOPE.md`, `data/gold/README.md`, `data/gold/sample_labeled.csv`（小 fixture）

**Interfaces (Produces):**
```python
@dataclass(frozen=True) ConfusionMatrix(green_playable:int, green_unplayable:int,
    red_playable:int, red_unplayable:int, amber_playable:int, amber_unplayable:int)
def confusion_from_labeled(rows: list[dict], profile: Profile) -> ConfusionMatrix
    # rows: {"tab_json":..., "human_playable": bool}；对每行跑 check_playability 归类
def green_false_accept_upper_bound(cm: ConfusionMatrix, conf: float=0.975) -> float
    # Clopper–Pearson 单侧上界：GREEN 中 human_unplayable 的比例上界；x=green_unplayable, n=green_total
def cohen_kappa(cm: ConfusionMatrix) -> float   # oracle(GREEN/RED 二分) vs human 的 κ（AMBER 排除或另算）
```

**Spec:** Clopper–Pearson 上界用 `scipy.stats.beta.ppf(conf, x+1, n-x)`（加 scipy 依赖）或纯 `statistics`/手写不完全 beta——**决定用 scipy**（`scipy>=1.11` 入 deps）。`data/gold/sample_labeled.csv` 是**小示例 fixture**（~10 行手造，含至少 1 个已知不可弹样例，标注 provenance），真实 ~300 条金标由 design partner/Plan 4 填（`data/gold/README.md` 写 schema + 采集规程 + 「test 金标绝不用于校准」）。`docs/SCOPE.md` = §14 A.9 诚实范围声明成文（静态几何/仅记谱速度/技巧 IN-OUT/主张限定 profile）。

- [ ] **Step 1: 写失败测试** `tests/validation/test_stats.py`
```python
# 构造已知 ConfusionMatrix，断言 green_false_accept_upper_bound 数值（对拍已知 Clopper–Pearson 值）
# 用 sample_labeled.csv 跑 confusion_from_labeled，断言 green_unplayable==0（示例应无 GREEN 误接受）
# 断言 0/n 时上界公式正确（如 0/10 → 0.975 上界 ≈ 0.308）
```
- [ ] **Step 2–4:** 实现 stats.py + 写 SCOPE.md + README + fixture → 跑到通过。
- [ ] **Step 5: Commit** — `git commit -am "feat(validation): confusion matrix + Clopper-Pearson false-accept bound + SCOPE"`

**Acceptance:** 上界公式对拍已知值；sample fixture 上 GREEN 误接受=0；SCOPE.md 覆盖 A.9 全部范围限制；README 写清金标采集与 train/test 隔离。

---

### Task 14: 收口 —— pip 安装验证 + CI 全绿 + README 验收清单

**Files:** Modify `.github/workflows/ci.yml`（纳入 validation 与 mutation 门）, `README.md`（架构一句话 + 验收门勾选 + `uv run` 指南）; Create `docs/PLAN1_ACCEPTANCE.md`

**Spec:** 逐条核对 roadmap「Plan 1 验收门」：pip 安装、确定性、毫米 span、monotone property、mutation kill-rate、GREEN 误接受 CP 上界+混淆矩阵、3 预设敏感性、SCOPE 声明。`docs/PLAN1_ACCEPTANCE.md` 逐门标 ✅ + 证据（测试名/命令/输出摘要）。CI 必须跑全部测试（含 validation）并绿。

- [ ] **Step 1:** 本地 `uv build` + `uv pip install dist/*.whl` 到干净 venv，`python -c "from fretsure.oracle.core import check_playability"` 成功。
- [ ] **Step 2:** `uv run pytest -q` 全绿（含 property/metamorphic/mutation/stats）；`uv run ruff check`、`uv run mypy src` clean。
- [ ] **Step 3:** 写 `docs/PLAN1_ACCEPTANCE.md`（逐门证据）+ 更新 README + CI。
- [ ] **Step 4: Commit** — `git commit -am "docs+ci: Plan 1 acceptance gate verification, green CI"`

**Acceptance:** 所有 roadmap Plan 1 验收门 ✅ 且有证据；CI 全绿；wheel 可安装即用。

---

## 执行顺序（依赖修正）

谓词依赖 `Profile`，故 **Task 8（Profile）在 Task 5–7、9、10 之前执行**。推荐顺序：
**1 → 2 → 3 → 4 → 8 → 5 → 6 → 7 → 9 → 10 → 11 → 12 → 13 → 14。**
（task-brief 用 `### Task N` 抽取；本顺序仅执行次序，编号不变。）

## Self-Review（作者已核）
- **Spec 覆盖**：ir/tab/geometry/8 谓词/三态/profile/property/metamorphic/mutation/差分+混淆矩阵+CP 上界/SCOPE/pip+CI —— 对齐 roadmap Plan 1 Scope 与验收门全部条目。
- **类型一致**：Diagnostic/Verdict/ViolationType/Profile/Tab/TabNote/Frame/FingerAssignment 跨 task 同名同签名，均引自本文件 Interfaces（= roadmap §B）。
- **无占位**：每 task 有具体 Files/Interfaces/Spec/测试代码/Acceptance/commit；测试为可运行代码。
- **风险点**：几何常量（STRING_SPACING_MM/hand_span_mm/v_shift/r_max）为 v1 占位，全部标 CALIBRATION，正确性由 property/metamorphic/mutation + 差分把关，绝对数值待 design partner 校准——**这是延后不是简化，且不影响 oracle 的方向性 soundness 验证**。
