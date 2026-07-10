# Plan 4 —— Benchmark & eval 台（★moat/主角）

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:test-driven-development`。步骤用 `- [ ]`。
> **上游**：Plan 1（oracle）+ Plan 2（solver）+ Plan 3（agent 回路）已完成。主路线图 `docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`（§B 契约、Plan 4 = M3、§14 Part A/C 权威）。
> **执行环境**：主 Opus 直接 TDD；只读 opus 审查；LLM 本地代理 `claude-opus-4-8`（无 `[1m]`），FakeLLM 确定性注入、真代理 `@pytest.mark.integration`。分支 `plan-4-benchmark`。

**Goal:** 交付 checker 打分（非 LLM 评委）、可复现、一条命令跑通的 benchmark：**程序生成语料（皇冠、防污染）** + 忠实度权威版 + **pass^k/Wilson CI** + **leave-one-out 消融** + **checker-vs-LLM-judge** + baselines + `fretsure-bench --seed S` 复现。

**Architecture:** 一切 checker（oracle+忠实度）打分、分层报（体裁×源层×难度）、联合 Pareto（可弹×忠实）、按曲 cluster bootstrap CI。LLM 只作被测系统（agent）与 baseline/judge，绝不作 ground truth。

**Tech Stack:** 复用 Plan 1/2/3 + scipy（已在）。程序生成器纯确定性（seeded）。无新第三方依赖（DadaGP/GuitarSet 真实语料是 D 层验证 checker 用、可选、后置）。

## Global Constraints（每 task 隐含）
- **checker 打分，非 LLM 评委**；任何榜单主张带 CI（Wilson/Clopper–Pearson/cluster bootstrap）。
- **程序生成层（E）扛头牌**（构造上不可污染，LLM 没背过其 tab）。真实层与程序层**分报**（差距=记忆效应）。
- **联合成功 = 可弹(GREEN) AND 忠实度门**（主指标）；护栏 = 保忠实修复率。
- 程序生成器 seeded 纯确定性；同 seed → 同语料。
- **消融只按 leave-one-out**（去掉某能力若不让主指标退化则砍，公开砍线）。
- ruff+mypy clean；FakeLLM 确定性 TDD；commit 无 AI trailer。

## File Structure
```
src/fretsure/bench/__init__.py
src/fretsure/bench/generator.py     # 功能和声文法 → MusicIR（程序生成，皇冠测试集）
src/fretsure/bench/corpus.py        # IR ⇄ JSON note-graph + datasheet + 分层标签
src/fretsure/metrics/fidelity.py    # 升级为权威 §B.5（DTW Melody-F1/bass-root/harmony-Jaccard/门）
src/fretsure/bench/reliability.py   # pass@k / pass^k(无偏) / Wilson CI 汇总
src/fretsure/bench/ablation.py      # leave-one-out 消融 runner（repair/critic/best-of-N 开关）
src/fretsure/bench/checker_vs_judge.py  # oracle vs LLM-judge 混淆 + McNemar
src/fretsure/bench/baselines.py     # B1 前沿 LLM 原始→tab；B2 纯求解器
src/fretsure/bench/runner.py        # 组装：语料→系统→指标→CI→报告；fretsure-bench --seed
tests/bench/…
```

---

### Task 1: 程序化 lead-sheet 生成器（★皇冠测试集）
**Files:** Create `src/fretsure/bench/__init__.py`, `src/fretsure/bench/generator.py`; Test `tests/bench/test_generator.py`
**Interfaces:**
```python
@dataclass(frozen=True) GenConfig(key:str="C", meter:tuple[int,int]=(4,4), bars:int=4, seed:int=0)
def generate_leadsheet(cfg: GenConfig) -> MusicIR
# 功能和声文法采样和弦进行（I-IV-V-I 类）+ 受和弦音/经过音约束的旋律 + 低音=和弦根。确定(seed)。
```
**Spec:** 用 `random.Random(seed)`。和弦进行从功能文法（T→S→D→T）采样；旋律每拍从当前和弦音/邻近经过音选；bass=根音。产出 MusicIR（melody+bass 声部 + chords）。**旋律 ground truth 天然精确、构造上不可污染。**
- [ ] TDD：同 seed→同 IR（确定）；旋律音全在调内/和弦语境；每小节有 bass 根音；chords 非空；validate_ir 通过 → commit `feat(bench): procedural functional-harmony lead-sheet generator`。
**Acceptance:** 确定、乐理合法、validate_ir 过、可参数化 bars/key/seed。

---

### Task 2: 语料 note-graph schema + 归一器
**Files:** Create `src/fretsure/bench/corpus.py`; Test `tests/bench/test_corpus.py`
**Interfaces:**
```python
def ir_to_notegraph(ir: MusicIR) -> dict   # {meta, notes:[{onset,duration,midi,voice}], chords:[...]}
def notegraph_to_ir(obj: dict) -> MusicIR   # 往返恒等
@dataclass(frozen=True) CorpusItem(ir:MusicIR, layer:str, genre:str, difficulty:int, item_id:str)
def datasheet(items: list[CorpusItem]) -> dict  # 计数/分层/provenance
```
**Spec:** JSON 往返恒等（Fraction→字符串）；layer∈{procedural,public_leadsheet,...}；分层标签供分报。
- [ ] TDD：往返恒等、datasheet 计数正确 → commit `feat(bench): note-graph corpus schema + normalizer + datasheet`。
**Acceptance:** 往返恒等、分层标签、datasheet 正确。

---

### Task 3: 忠实度权威版（升级 stub → §B.5）
**Files:** Modify `src/fretsure/metrics/fidelity.py`（新增权威函数，保留 stub 名兼容或迁移）；Test `tests/metrics/test_fidelity_authoritative.py`
**Interfaces:**
```python
def melody_f1(ir, tab, *, onset_grid:Fraction=Fraction(1,16)) -> float   # DTW((onset,pitch)) 对齐, recall/precision 调和
def bass_root_accuracy(ir, tab) -> float   # 强拍最低发声 pc == 源和弦根 比例
def harmony_jaccard_v2(ir, tab) -> float
@dataclass(frozen=True) FaithfulnessGate(melody_f1:float, bass_root:float, harmony:float, passed:bool)
def faithfulness(ir, tab, *, tau_m=0.9, tau_b=0.7, tau_h=0.6) -> FaithfulnessGate
```
**Spec:** §B.5 权威公式（DTW 对齐、1/16 网格、门阈值事先公布）。旧 stub `melody_recall` 等保留（Plan 3 依赖），新增权威函数。
- [ ] TDD：完美编配 f1==1；丢音降 f1；bass-root 强拍；门 AND 逻辑 → commit `feat(metrics): authoritative DTW faithfulness (Melody-F1/bass-root/gate)`。
**Acceptance:** 公式对拍、门逻辑正确、旧 stub 不破。

---

### Task 4: 可靠性统计（pass@k / pass^k / Wilson）
**Files:** Create `src/fretsure/bench/reliability.py`; Test `tests/bench/test_reliability.py`
**Interfaces:**
```python
def pass_at_k(n:int, c:int, k:int) -> float   # HumanEval 无偏：1 - C(n-c,k)/C(n,k)
def pass_hat_k(per_item_successes: list[tuple[int,int]], k:int) -> float  # 每条(n,c) → pass^k=全过 均值(独立近似)
def wilson(successes:int, n:int, conf=0.95) -> tuple[float,float]  # 复用 oracle.validation.stats.wilson_ci
```
**Spec:** pass@k 用无偏估计（n≥k）；pass^k = k 次独立全过的可靠性（用 c/n 的 k 次幂近似或组合式）。对拍已知值。
- [ ] TDD：pass_at_k(10,10,5)==1；pass_at_k(10,0,5)==0；pass_at_k 单调；已知组合值；wilson 边界 → commit `feat(bench): pass@k / pass^k unbiased estimators + Wilson`。
**Acceptance:** 估计器对拍 HumanEval 公式、单调、CI 正确。

---

### Task 5: leave-one-out 消融 runner
**Files:** Create `src/fretsure/bench/ablation.py`; Test `tests/bench/test_ablation.py`
**Interfaces:**
```python
@dataclass(frozen=True) AblationConfig(repair:bool=True, best_of_n:int=4, critic:bool=True)
def run_config(items, goal, llm_factory, cfg: AblationConfig, profile) -> ConfigMetrics
# 用 arrange(消融开关映射)跑每条 → 汇总 pass@1/联合成功/忠实/迭代/成本
def leave_one_out(items, goal, llm_factory, profile) -> dict[str, ConfigMetrics]
# full + 逐个去掉 repair/critic/best-of-N，返回各配置指标
```
**Spec:** 消融开关映射到 Plan 3 `arrange`（repair=False → max_iters=0；best_of_n=1；critic=False→跳过）。FakeLLM 确定性 TDD。**leave-one-out 才是诚实测试。**
- [ ] TDD（FakeLLM）：full vs 去-repair 在手造不可弹集上，full 联合成功更高（消融证明 repair 挣存在）；确定 → commit `feat(bench): leave-one-out ablation runner`。
**Acceptance:** 消融配置正确映射、指标汇总、FakeLLM 确定；去-repair 掉分（脊柱头牌雏形）。

---

### Task 6: checker-vs-LLM-judge 实验
**Files:** Create `src/fretsure/bench/checker_vs_judge.py`; Test `tests/bench/test_checker_vs_judge.py`
**Interfaces:**
```python
def llm_judge(tab: Tab, ir: MusicIR, llm: LLMClient) -> str   # "PLAYABLE"/"UNPLAYABLE"（zero-shot）
def checker_vs_judge(labeled: list[tuple[Tab, bool]], llm) -> JudgeComparison
# 对每条: oracle GREEN/RED vs judge；vs 人标(bool)。混淆矩阵 + 各自误接受 + McNemar
```
**Spec:** 头牌修辞：LLM 评委在对抗近失上误接受显著高于 oracle。FakeLLM 脚本化 judge（确定）；真 LLM 集成。
- [ ] TDD（FakeLLM）：构造 judge 误接受一个已知不可弹 tab、oracle 正确 RED → 混淆矩阵体现 judge 更差；McNemar 计算 → commit `feat(bench): checker-vs-LLM-judge confusion + McNemar`。
**Acceptance:** 混淆矩阵/误接受/McNemar 正确；FakeLLM 确定；真 judge 集成本地跑。

---

### Task 7: baselines（B1 前沿原始 / B2 纯求解器）
**Files:** Create `src/fretsure/bench/baselines.py`; Test `tests/bench/test_baselines.py`
**Interfaces:**
```python
def baseline_raw_llm(ir, goal, llm) -> Tab | None   # 直接要 tab（可能不可弹）→ 解析；不过 solver/repair
def baseline_pure_solver(ir, goal, profile) -> Tab | Infeasible  # 规则提议 stub + solver, 无 LLM/repair
```
**Spec:** B1 = 前沿 LLM 直出 tab（"到底需不需要 agent"对照，可能 RED）；B2 = 纯求解器上限（无编配判断）。B1 输出**不保证非 RED**（正是对照点）。
- [ ] TDD（FakeLLM）：B1 解析 LLM 的 tab JSON（可含不可弹）；B2 确定出 solver tab → commit `feat(bench): baselines (raw-LLM, pure-solver)`。
**Acceptance:** B1/B2 可跑、B1 保留"可能不可弹"性质作对照。

---

### Task 8: runner + 一条命令复现
**Files:** Create `src/fretsure/bench/runner.py`; `pyproject.toml` 加 `[project.scripts] fretsure-bench`; Test `tests/bench/test_runner.py`
**Interfaces:**
```python
def run_benchmark(*, seed:int, n_items:int, llm_factory, profile) -> BenchReport
# 生成语料(seed) → 跑系统+baselines+消融 → pass^k/联合成功/忠实/成本 + Wilson CI + checker-vs-judge → 分层报
def main() -> None  # CLI: fretsure-bench --seed S --items N  → 打印/存 JSON 报告
```
**Spec:** 一条命令 `fretsure-bench --seed S` 重建语料+全指标+CI+checker 验证+checker-vs-judge，可复现（同 seed→同数，用 FakeLLM/固定 stub 时）。每个判决盖 checker_version+profile_version。
- [ ] TDD（FakeLLM/固定）：run_benchmark(seed) 两次相等；报告含 pass^k/联合成功/CI/分层；CLI smoke → commit `feat(bench): one-command reproducible benchmark runner + CLI`。
**Acceptance:** 一条命令复现、报告分层带 CI、CLI 可跑。

---

## 执行顺序
**1(生成器) → 2(语料) → 3(忠实度) → 4(统计) → 5(消融) → 6(checker-vs-judge) → 7(baselines) → 8(runner+CLI)。**

## 两个头牌（Plan 4 交付）
- **头牌#1**：leave-one-out 去修复 → 联合成功/pass^k 塌陷（消融证明修复脊柱挣存在）。Task 5 出雏形，Task 8 出带 CI 的完整数。
- **checker-vs-judge**：LLM 评委误接受显著高于 oracle（"为什么用 checker 不用评委"的量化理由）。Task 6。

## Self-Review（作者已核）
- **Spec 覆盖**：程序生成器/语料/忠实度权威/pass^k/消融/checker-vs-judge/baselines/一条命令 —— 对齐 roadmap Plan 4（M3）与 §14 Part A/C。
- **类型一致**：复用 Plan 1/2/3（MusicIR/Tab/OracleResult/arrange/ArrangeGoal/check_playability/fidelity/LLMClient/FakeLLM）。
- **诚实**：checker 打分非评委；程序层扛头牌防污染；联合 Pareto 防削音；消融 leave-one-out 砍线公开；真实语料(D 层)后置、不阻塞。
