# Fretsure

**给你一份人手可证明弹得出来的吉他谱。**

Fretsure 是一个 agent：输入一首歌的音乐内容（乐谱 / MIDI / lead sheet；mp3 作 best-effort 前端），输出一份在你指定难度、调弦、变调夹下**可证明弹得出来**的吉他谱——HERO 是指弹独奏，也做伴奏与难度简化。前沿 LLM 提议编配，一个**确定性可弹性 oracle** 逐音硬门把关并在保住旋律/低音/和声的前提下自动修复，正确性由 **checker（而非另一个 AI）**在公开 benchmark 上验证。

> 定位（已过敌意核查）："Suno 给你一首弹不了的歌；Fretsure 给你一份人手可证明弹得出来的谱。"

## 一条命令看它跑

```bash
uv sync --extra dev
uv run fretsure-demo          # 离线确定性跑通；加 --llm 用真实 LLM 编配
```

把一份 lead sheet 编成一份 **GREEN（可证明可弹）** 的指弹谱并打印 ASCII tab、oracle 判决、忠实度门：

```
ARRANGED TAB (high-e on top)
  e|--1-5---5-------7-------0-------|
  B|1-----------6-8-----------------|
  G|2-----5---5-----0-7-7-5---5-5-5-|
  D|--------3---------------7-------|
  A|--------------------------------|
  E|--------------------------------|

ORACLE VERDICT
  GREEN — 由收紧后的悲观手型逐音证明可弹（checker oracle@0.1.0, profile median@0.1）
FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root 1.00   harmony 1.00   gate PASS
```

这张谱不是 LLM 的"意见"：确定性、毫米级几何的 oracle 对每个音/每个框逐一核过悲观手型才给出 GREEN。**LLM 只提议意图，可弹性由机器证明。**

## 架构（oracle 当环境、LLM 当策略）

```
lead sheet / MIDI / IR
        │
        ▼
  LLM 提议编配 ──────────────┐  （策略：只决定音乐意图，不决定指法）
        │                    │
        ▼                    │ 修复循环（verifier-guided，到不动点）
  确定性指法求解（beam）      │  读 oracle 定位化诊断 → 编辑 DSL（旋律受保护）
        │                    │
        ▼                    │
  可弹性 ORACLE ─── RED ──────┘
   （毫米几何 / 三态判决）
        │ GREEN/AMBER
        ▼
  best-of-N 选择 + 忠实度门 + 乐感 critic
        │
        ▼
  可证明可弹的 TAB   ──►  checker 打分 benchmark（非 LLM 评委）
```

范式：**oracle 当环境、LLM 当策略**；harness 自研（框架仅作对照基准）；每个 agent 能力用 **leave-one-out 消融**挣存在，**砍掉的组件公开**。

## Benchmark 头条：修复挣得了它的存在

在程序生成、抗污染的 lead sheet 语料上做 leave-one-out 消融（`fretsure-bench`，真实 LLM）。打分用 oracle + 忠实度门，**不是 LLM 评委**。

| arm | joint_success | melody-F1 | Wilson 95% |
|---|---|---|---|
| **完整 agent** | **0.81** | 1.00 | [0.57, 0.93] |
| **− 修复** | 0.31 | 0.56 | [0.14, 0.56] |
| − critic | 0.81 | 1.00 | [0.57, 0.93] |
| − best-of-N | 0.94 | 1.00 | [0.72, 0.99] |

- **修复是承重能力**：去掉它，成功率 0.81→0.31、melody-F1 1.00→0.56，两个 Wilson 区间**不重叠**；两个 seed 合并（n=32）仍不重叠（full [0.72,0.95] vs −修复 [0.18,0.49]）。
- **best-of-N 挣得一份薄利**：非配对臂符号在两 seed 间翻转（采样混淆），但**配对消融**（同一提议池，只变选择宽度）两 seed 一致 **+0.125 GREEN**——薄但真。
- **critic 仍在观察名单**：与完整 agent 打平/仅 +1 项，区间与 full 重叠，尚未挣得存在，公开挂账。完整记分卡见 [`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md)。
- 一个 **"谁检查语料"** 的发现：一次 `joint_success=0` 追到的是**语料标注 bug**（0 索引音级把 `C:deg5` 误写成 V），非 agent/度量的错。全部经过见 [`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md)。

## 状态

**Plan 1–5（完整后端）已实现**，`260` 测试全绿（254 离线确定性 + 6 集成，集成项需本地 LLM 代理）：

- **Plan 1 核心 + 可弹性 Oracle**：Music IR + Tab + 毫米几何 oracle（三态 + 定位化诊断）+ 自验证台（property/metamorphic/mutation/N-version + 混淆矩阵 + Clopper–Pearson GREEN 误接受上界）。见 [`docs/PLAN1_ACCEPTANCE.md`](docs/PLAN1_ACCEPTANCE.md)、[`docs/SCOPE.md`](docs/SCOPE.md)。
- **Plan 2 求解器**：beam-search 指法求解，每个部分谱都对真 oracle 核验 → **永不返回 RED**。
- **Plan 3 agent 循环**：LLM 编配 + 编辑 DSL（旋律保护）+ verifier-guided 修复到不动点 + best-of-N + 乐感 critic。
- **Plan 4 benchmark**：程序生成语料 + pass@k/pass^k 无偏估计 + leave-one-out 消融 + checker-vs-LLM-judge + 一条命令 `fretsure-bench`。
- **Plan 5 难度 + 伴奏**：可验证的"简化到目标 tier"（对 `check_tier` 门修复）+ 和弦声位/分解/扫弦伴奏。

设计文档是唯一真源：
- 设计 spec：[`docs/superpowers/specs/2026-07-09-fretsure-design.md`](docs/superpowers/specs/2026-07-09-fretsure-design.md)
- 实现路线图：[`docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`](docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md)
- 项目状态 / 恢复：[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)

## 开发（Build & test）

用 [uv](https://docs.astral.sh/uv/) 管理 Python 3.11 环境：

```bash
uv sync --extra dev              # 建 3.11 venv + 装依赖
uv run pytest -q -m "not integration"   # 254 离线测试
uv run ruff check                # lint
uv run mypy src                  # 类型检查（strict）
uv run fretsure-demo             # 一条命令端到端 demo
uv run fretsure-bench --seed 1 --items 16   # 复现消融（需本地 LLM 代理）
```

集成测试与 `--llm`、`fretsure-bench` 需本地 LLM 代理（`export ANTHROPIC_BASE_URL=... ANTHROPIC_AUTH_TOKEN=...`）；缺省一律走确定性离线路径，保证一条命令可跑。
