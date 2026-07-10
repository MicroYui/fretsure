# Plan 3 —— Agent 回路（自研 harness）+ verifier-guided 修复 + best-of-N + critic

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:test-driven-development`。步骤用 `- [ ]`。
>
> **上游**：Plan 1（oracle）+ Plan 2（solver + M0）已完成。主路线图 `docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`（§B 契约、Plan 3 = M2、Part B agent 深度）。
>
> **执行环境**：主 Opus 直接 TDD；只读 opus 审查。**LLM 走本地代理**：`ANTHROPIC_BASE_URL=http://localhost:4141`、`ANTHROPIC_AUTH_TOKEN=sk-1234`、model **`claude-opus-4-8`（务必无 `[1m]` 后缀，否则 model_not_supported）**。分支 `plan-3-agent-loop`。

**Goal:** 交付「oracle 当环境、LLM 当策略」的自研 agent 回路：LLM 提议编配 → solver+oracle 验证 → 读定位化诊断 → 下 edit-DSL 定点修复 → 重查到 GREEN（不动点）；配 best-of-N + musicality critic + 可解释 trace。**这是项目脊柱与护城河。**

**Architecture:** verifier-guided 修复回路。LLM 只决定「音乐意图」（提议 target 音集 + 修复取舍），确定性层（solver+oracle，Plan 1/2）决定「手怎么按 + 能不能弹」。**LLM 通过协议注入（`LLMClient`）→ 全部 harness 逻辑用 `FakeLLM` 确定性 TDD；真代理走打标集成测试。**

**Tech Stack:** 复用 Plan 1/2。新增 `anthropic` SDK（读 env 调本地代理）。`pytest` marker `integration`（需真代理，CI 无代理则 `-m "not integration"`）。

## Global Constraints（每 task 隐含）

- **oracle 是唯一可弹性真理**：agent 永不自判可弹；一切经 `check_playability`/`solve_fingering`。LLM 输出永远被确定性层兜底。
- **melody 永远保留**：edit-DSL 与修复恒保最高声部 melody 音（drop/octave 仅作用于 bass/harmony）。bass 根音尽量保、和弦 pitch-class 尽量保。
- **确定性 seam**：LLM 经 `LLMClient` 协议注入。所有非 LLM 逻辑（edit 应用、回路控制、best-of-N 选择、fidelity、trace）**纯确定性、用 `FakeLLM` TDD**。真 LLM 仅在 `@pytest.mark.integration` 测试里出现。
- **可解释**：每次修复产出 trace 步骤（plan→diagnostic→reason→edit→re-check），供 Plan 6 viewer 与 benchmark 统计。
- **预算约束**：修复回路有迭代上限（防死循环）；best-of-N 的 N 有上限。
- frozen dataclass；ruff+mypy clean；LLM model id 无 `[1m]`；commit 不加 AI trailer。

## File Structure

```
src/fretsure/llm/__init__.py
src/fretsure/llm/client.py       # LLMClient 协议 + ProxyLLM(anthropic) + FakeLLM + JSON 提取
src/fretsure/agent/__init__.py
src/fretsure/agent/edit_dsl.py   # Edit 类型 + 算子(drop_note/octave_shift/revoice/drop_inner) + apply/parse
src/fretsure/agent/trace.py      # TraceStep + Trace（可解释历史/checkpoint）
src/fretsure/agent/tools.py      # ACI：诊断→prompt 文本 + solve/oracle/fidelity 封装
src/fretsure/agent/repair.py     # verifier-guided 修复回路（脊柱）
src/fretsure/agent/arranger.py   # LLM 提议 target 编配
src/fretsure/agent/critic.py     # LLM musicality 评分
src/fretsure/agent/harness.py    # arrange()：propose→repair→best-of-N→select，出 trace
src/fretsure/metrics/__init__.py
src/fretsure/metrics/fidelity.py # melody/bass/harmony 保留（stub；Plan 4 换 DTW 版）
tests/llm/… tests/agent/… tests/metrics/…
```

---

### Task 1: LLM 客户端（协议 + 代理 + Fake）

**Files:** Create `src/fretsure/llm/__init__.py`, `src/fretsure/llm/client.py`; Test `tests/llm/test_client.py`（+ `__init__.py`）; `pyproject.toml` 加 `anthropic>=0.40`。

**Interfaces (Produces):**
```python
from typing import Protocol
class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, max_tokens: int = 1024,
                 temperature: float = 0.0) -> str: ...
class FakeLLM:  # 确定性测试用
    def __init__(self, scripted: list[str]) -> None: ...   # 按调用顺序返回；耗尽则 IndexError
    def complete(self, *, system, user, max_tokens=1024, temperature=0.0) -> str: ...
    @property
    def calls(self) -> list[dict]: ...   # 记录每次 (system,user) 供断言
class ProxyLLM:  # 真代理（anthropic SDK 读 ANTHROPIC_BASE_URL/AUTH_TOKEN）
    def __init__(self, model: str = "claude-opus-4-8") -> None: ...
    def complete(self, *, system, user, max_tokens=1024, temperature=0.0) -> str: ...
def extract_json(text: str) -> dict   # 从 LLM 文本抽第一个 JSON 对象（容忍前后缀/```json 围栏）
```

**Spec:** `ProxyLLM` 用 `anthropic.Anthropic()`（自动读 env）调 `messages.create(model, system, messages=[{role:user,content:user}], max_tokens, temperature)`，拼接 text blocks 返回。`extract_json` 用括号配平/正则找第一个 `{...}`，解析失败抛 `ValueError`。

- [ ] **Step 1: 写失败测试**（FakeLLM 按序返回 + 记录 calls；extract_json 对纯 JSON、带 ```json 围栏、带前后缀文本三种都能抽出；坏 JSON 抛 ValueError）。**ProxyLLM 的真调用放 `@pytest.mark.integration`**（断言返回非空 str）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 跑通过（`uv run pytest -m "not integration"` 绿；本地 `-m integration` 亦绿）。配 `pyproject` 加 `[tool.pytest.ini_options] markers = ["integration: needs local LLM proxy"]`。
- [ ] **Step 5: Commit** `feat(llm): LLMClient protocol + ProxyLLM + FakeLLM + extract_json`

**Acceptance:** FakeLLM 确定注入；extract_json 稳健；integration 测试本地过；CI（无代理）`-m "not integration"` 绿。

---

### Task 2: 忠实度 stub（melody/bass/harmony 保留）

**Files:** Create `src/fretsure/metrics/__init__.py`, `src/fretsure/metrics/fidelity.py`; Test `tests/metrics/test_fidelity.py`

**Interfaces (Produces):**
```python
def melody_recall(ir: MusicIR, tab: Tab) -> float   # 输入 melody 音高在 tab 中出现的比例（pitch 精确，八度不宽松）
def bass_preserved(ir: MusicIR, tab: Tab) -> float   # bass 音高保留比例
def harmony_jaccard(ir: MusicIR, tab: Tab) -> float  # 逐 onset pitch-class 集 Jaccard 平均
@dataclass(frozen=True)
class Fidelity:
    melody_recall: float; bass_preserved: float; harmony_jaccard: float
def fidelity(ir: MusicIR, tab: Tab) -> Fidelity
```

**Spec:** M0 简化版（非 DTW；Plan 4 换权威 §B.5 版）。tab 音高 = `note_pitch(string,fret,tuning,capo)`。melody_recall = |输入 melody pitch 出现在 tab| / |输入 melody|。确定。**标注 `# STUB: Plan 4 replaces with DTW-aligned Melody-F1`。**

- [ ] **Step 1: 写失败测试**（保全旋律的 tab → melody_recall==1；丢一个旋律音 → <1；空输入边界）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(metrics): fidelity stub (melody/bass/harmony preservation)`

**Acceptance:** 三指标确定、方向正确；标注为 stub。

---

### Task 3: 编辑 DSL

**Files:** Create `src/fretsure/agent/__init__.py`, `src/fretsure/agent/edit_dsl.py`; Test `tests/agent/test_edit_dsl.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class Edit:
    op: Literal["drop_note","octave_shift","revoice","drop_inner"]
    target_onset: Fraction; target_pitch: int; arg: int = 0   # arg: octave_shift 的 ±12；revoice 的新 pitch
def apply_edit(notes: tuple[Note,...], edit: Edit) -> tuple[Note,...]   # 返回新 target；melody 音拒绝 drop
def parse_edit(obj: dict) -> Edit    # 从 LLM JSON 解析；非法 op/缺字段抛 ValueError
class MelodyProtected(Exception): ...  # 试图删 melody 时
```

**Spec:** 恒保 melody：`drop_note`/`drop_inner` 作用于 voice≠melody 的音；对 melody 音抛 `MelodyProtected`（回路捕获、跳过）。`octave_shift` 改 pitch ±12（保 voice）。`revoice` 改 harmony 音 pitch。均确定、返回排序后的新 tuple。

- [ ] **Step 1: 写失败测试**（drop 一个 harmony 音→少一音；drop melody→MelodyProtected；octave_shift bass→pitch±12；parse_edit 合法/非法；apply 确定）。
- [ ] **Step 2-4:** 跑失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(agent): edit-DSL operators + apply/parse (melody-protected)`

**Acceptance:** 算子确定、保 melody；parse 稳健。

---

### Task 4: Trace（可解释历史）

**Files:** Create `src/fretsure/agent/trace.py`; Test `tests/agent/test_trace.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class TraceStep:
    kind: Literal["PLAN","PROPOSE","SOLVE","ORACLE","REASON","EDIT","RECHECK","SELECT"]
    detail: str; data: dict
@dataclass
class Trace:
    steps: list[TraceStep]
    def add(self, kind, detail, **data) -> None: ...
    def to_jsonl(self) -> str: ...   # 每步一行 JSON（供 Plan 6 viewer / OTel）
```

**Spec:** 纯记录器；确定；`to_jsonl` 稳定序列化（Fraction→字符串）。

- [ ] **Step 1-5:** TDD（add 累积、to_jsonl 往返可解析）→ commit `feat(agent): explainable Trace + JSONL`。

**Acceptance:** trace 步骤有序、可序列化。

---

### Task 5: ACI 工具层（诊断→prompt + 封装）

**Files:** Create `src/fretsure/agent/tools.py`; Test `tests/agent/test_tools.py`

**Interfaces (Produces):**
```python
def diagnostics_to_prompt(result: OracleResult | Infeasible, target: tuple[Note,...]) -> str
# 把定位化诊断/Infeasible 渲染成给 LLM 的紧凑人读文本（小节/拍/违反类型/超几毫米/建议松弛 + 当前 target 音集摘要）
def edit_schema_prompt() -> str   # 描述 edit-DSL JSON schema 给 LLM
def solve_and_check(target, tuning, capo, profile, *, tempo_bpm) -> tuple[Tab | Infeasible, OracleResult | None]
```

**Spec:** ACI 承重设计 = 诊断格式 + edit schema。`solve_and_check` 封装 Plan 2 solver + Plan 1 oracle（tab 存在则 check_playability）。确定。

- [ ] **Step 1-5:** TDD（diagnostics_to_prompt 含小节/违反/超量；solve_and_check 对可解/不可解返回正确对）→ commit `feat(agent): ACI tools (diagnostic prompting + solve/check wrapper)`。

**Acceptance:** prompt 含定位信息；封装正确。

---

### Task 6: verifier-guided 修复回路（★脊柱）

**Files:** Create `src/fretsure/agent/repair.py`; Test `tests/agent/test_repair.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class RepairResult:
    tab: Tab | None; target: tuple[Note,...]; oracle: OracleResult | None
    infeasible: Infeasible | None; iterations: int; trace: Trace
def repair(target: tuple[Note,...], tuning: int, capo: int, profile: Profile, llm: LLMClient,
           *, tempo_bpm: float = 90.0, max_iters: int = 8) -> RepairResult
```

**Spec（回路）：**
1. `solve_and_check(target)` → 若 Tab 且 GREEN → 完成（记 trace）。
2. 否则（AMBER/Infeasible）：`diagnostics_to_prompt` + `edit_schema_prompt` → `llm.complete` 要一个 edit（JSON）→ `parse_edit` → `apply_edit`（melody 保护；`MelodyProtected` 则记 trace 跳过、要下一个 edit）→ 新 target → 重查。
3. 到 GREEN、或 `max_iters`、或不动点（target 不再变化）停。
4. 每步记 trace（PROPOSE/SOLVE/ORACLE/REASON/EDIT/RECHECK）。
- **恒保 melody**；**LLM 经 `llm` 注入**（测试用 FakeLLM 脚本化 edit 序列）。

- [ ] **Step 1: 写失败测试（FakeLLM 驱动，确定）**
  - 构造一个初始 AMBER/Infeasible 的 target + FakeLLM 脚本（drop 某 harmony / octave_shift bass）→ repair 后 `oracle.verdict=="GREEN"`、melody 全保留、iterations≤max、trace 有 EDIT 步。
  - FakeLLM 给出会删 melody 的 edit → 被 MelodyProtected 挡、trace 记跳过。
  - 已 GREEN 的 target → repair 0 次迭代直接返回。
  - 达 max_iters 仍不 GREEN → 返回最后状态（非崩溃），iterations==max。
- [ ] **Step 2-4:** 跑失败 → 实现 → 通过（`-m "not integration"`）。
- [ ] **Step 5: Commit** `feat(agent): verifier-guided repair loop (spine)`
- [ ] **(可选) 集成测试** `@pytest.mark.integration`：真 ProxyLLM 修一个手造 AMBER 目标到 GREEN（本地跑）。

**Acceptance:** FakeLLM 下回路确定、到 GREEN、保 melody、有界、trace 完整；真 LLM 集成本地能修一例。

---

### Task 7: LLM 编配提议器

**Files:** Create `src/fretsure/agent/arranger.py`; Test `tests/agent/test_arranger.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class ArrangeGoal:
    style: str = "fingerstyle"; tier: str = "intermediate"; tuning: tuple[int,...] = STANDARD_TUNING
    capo: int = 0; tempo_bpm: float = 90.0
def propose_arrangement(ir: MusicIR, goal: ArrangeGoal, llm: LLMClient, *, temperature: float = 0.0) -> tuple[Note,...]
# LLM 读 IR（旋律+和弦+调/拍）→ 输出 target 音集 JSON（每音 onset/dur/pitch/voice）→ 解析为 Note tuple；melody 必含。
```

**Spec:** system prompt 说明「只定音乐意图、不定指法；旋律必留；输出结构化 JSON」。解析失败/缺 melody → 回退到 Plan 2 规则 stub `propose_fingerstyle`（诚实兜底）。FakeLLM 测解析+回退；真 LLM 集成测试。

- [ ] **Step 1-5:** TDD（FakeLLM 返回合法 JSON→解析出 Notes 含 melody；返回坏 JSON→回退 stub；集成测试真 LLM 出可解析编配）→ commit `feat(agent): LLM arrangement proposer + rule-stub fallback`。

**Acceptance:** 解析稳健、旋律必含、坏输出回退；集成本地出编配。

---

### Task 8: musicality critic

**Files:** Create `src/fretsure/agent/critic.py`; Test `tests/agent/test_critic.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class CriticScore:
    overall: float; voice_leading: float; bass_motion: float; texture: float; notes: str
def critique(ir: MusicIR, tab: Tab, llm: LLMClient) -> CriticScore
# LLM 按 rubric 评 0..1；解析失败→中性 0.5。critic 只评「好不好听」，绝不评可弹性。
```

**Spec:** rubric 锚定；输出 JSON 分数。FakeLLM 测解析；真 LLM 集成。**明确：critic 是有界品味评委，不进可行性门。**

- [ ] **Step 1-5:** TDD（FakeLLM JSON→CriticScore；坏输出→0.5 中性；集成真评分∈[0,1]）→ commit `feat(agent): musicality critic (taste, not playability)`。

**Acceptance:** 分数∈[0,1]、解析稳健、坏输出中性回退。

---

### Task 9: best-of-N + harness（arrange 入口）

**Files:** Create `src/fretsure/agent/search.py`, `src/fretsure/agent/harness.py`; Test `tests/agent/test_harness.py`

**Interfaces (Produces):**
```python
@dataclass(frozen=True)
class ArrangeResult:
    tab: Tab | None; oracle: OracleResult | None; fidelity: Fidelity | None
    critic: CriticScore | None; trace: Trace; candidates_tried: int
def arrange(ir: MusicIR, goal: ArrangeGoal, llm: LLMClient, *, n: int = 4, max_iters: int = 8) -> ArrangeResult
# N 次 propose（temperature 递增）→ 各 repair → 过滤 GREEN → 按 (fidelity 联合门, critic) 选最优 → 出 trace。
```

**Spec:** best-of-N 选择规则：优先 GREEN & melody_recall≥τ；其次 critic.overall 高、fidelity 高、成本低。全 non-GREEN 则返回最接近的 + 诚实标注。确定性选择（FakeLLM 下）。

- [ ] **Step 1: 写失败测试（FakeLLM，确定）**
  - N 个脚本化 propose+repair → arrange 选出 GREEN & 高 fidelity 的候选；trace 含 SELECT 步；candidates_tried==n。
  - 全候选非 GREEN → 返回最优近似 + 标注。
  - 确定性：同 FakeLLM 脚本两次 arrange 相等。
- [ ] **Step 2-4:** 跑失败 → 实现 → 通过。
- [ ] **Step 5: Commit** `feat(agent): best-of-N search + arrange harness`
- [ ] **(集成)** `@pytest.mark.integration`：真 LLM arrange 一个手造 lead sheet → GREEN tab（本地端到端）。

**Acceptance:** best-of-N 选择确定合理；trace 完整；真 LLM 端到端本地出 GREEN 指弹编配。

---

## 执行顺序
**1(LLM 客户端) → 2(fidelity) → 3(edit-DSL) → 4(trace) → 5(tools) → 6(修复回路★) → 7(arranger) → 8(critic) → 9(best-of-N+harness)。**

## 消融接线说明（Plan 4 正式跑）
本 Plan 各能力（修复 c / 工具-ACI b / critic d / 搜索 e）留出 leave-one-out 开关（如 `repair(..., enabled=False)` 退化为不修、`arrange(n=1)` 退化无搜索），供 Plan 4 消融 runner 证明其挣存在。**本 Plan 只需接线 + FakeLLM 冒烟；数值在 Plan 4 出。**

## Self-Review（作者已核）
- **Spec 覆盖**：LLM 客户端/fidelity/edit-DSL/trace/ACI/修复回路/arranger/critic/best-of-N —— 对齐 roadmap Plan 3（M2）与 spec Part B（oracle-as-env/LLM-as-policy、ACI、修复脊柱、critic、搜索）。
- **类型一致**：复用 Plan 1/2（Note/MusicIR/Tab/OracleResult/Infeasible/Profile/solve_fingering/check_playability/fidelity）；新增 Edit/Trace/RepairResult/ArrangeGoal/CriticScore/ArrangeResult 本文件定义、跨 task 一致。
- **确定性 TDD**：LLM 经 `LLMClient` 注入，`FakeLLM` 驱动全部逻辑测试；真代理仅 `@pytest.mark.integration`（CI `-m "not integration"`）。
- **反 LARP/诚实**：critic 是唯一第二 agent（品味非可行性）；能力留消融开关（Plan 4 证明存在）；LLM 坏输出一律确定性回退（stub/中性）。
