# Fretsure

**给你一份在版本化模型/profile 内可证明可弹的吉他谱。**

Fretsure 是一个 agent：当前输入乐谱 / MIDI / lead sheet；mp3 只是尚未实现的未来 best-effort 前端。它输出一份在你指定难度、调弦、变调夹下、由版本化模型检查的吉他谱——HERO 是指弹独奏，也做伴奏与难度简化。前沿 LLM 提议编配，一个**确定性可弹性 oracle** 逐音硬门把关；verifier-guided repair 已实现为可选研究/兼容能力，但正式 benchmark v2 未达到它的预注册保留阈值，因此不再把“修复已挣得默认存在”作为当前产品主张。结果由 **checker（而非另一个 AI）**验证。

> 产品定位："生成模型给出编配；Fretsure 用公开、版本化的可弹性模型逐音核验。"

## 一条命令看它跑

```bash
uv sync --extra dev
uv run fretsure-demo          # 离线确定性跑通；加 --llm 用真实 LLM 编配
```

真实文件纵切已经支持受限的 MusicXML 3.1/4.0 lead sheet（未压缩
`.musicxml`/`.xml` 与安全 `.mxl`），以及严格的 melody-only Standard MIDI File 子集：

```bash
uv sync --extra musicxml
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml \
  --trace-jsonl /tmp/fretsure-trace.jsonl

uv sync --extra midi
uv run fretsure-arrange tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid
```

该入口会依次输出 typed import diagnostics、MusicIR 摘要、ASCII tab、
`oracle@0.2.0` 判决、fingerprinted profile、`tab-input@0.2.0`、独立的
`fidelity@0.3.0` 门与可回放 JSONL trace；CLI 结果头另行绑定 importer/router
版本。`--llm` 的当前默认是 canonical `gpt-5.6-sol`；CLI、trace 与 benchmark
聚合 JSON 都显式记录 model id。
与 v2 裁决一致，产品基线默认是单候选、零 repair、critic 关闭：`--n 1
--max-iters 0` 且不加 `--critic`。研究/兼容实验仍可显式使用例如
`--n 4 --max-iters 8 --critic`；这不会把观察期能力重新声明为默认价值。
MusicXML 路径当前只支持单 part/staff/voice 的 4/4 单音旋律、固定传统调号、
固定 quarter-note tempo、普通 note/rest/tie 和白名单 root+kind harmony。MusicXML 4.0
可以省略 `<mode>`：importer 不猜 major/minor，而是保留
`key-signature:fifths=N;mode=unprovided` 并发出 `KEY_MODE_UNPROVIDED`；MusicXML 3.1
省略 `<mode>` 仍 fail-closed，显式 major/minor 继续保留原来的 key 表示。
`.mxl` 只扩展容器、不扩展这些 root MusicXML 语义。MIDI 只接受 format 0/1、PPQN、固定 tick-zero
tempo、固定 4/4、最多一个单声部非打击乐 note stream；raw tick/PPQN 是精确时间真源，所有音符标为
melody，`chords=()`，不猜 track 角色、bass、和弦、key 或量化。复调、多 note streams、SMPTE、
percussion、弯音/调律、sustain、SysEx 与变拍/变速/变调均 typed fail-closed；音频仍未实现。

本地 Web、typed HTTP API 与 MCP stdio adapter 已在 Plan 6A 打通：

```bash
uv run fretsure-serve       # http://127.0.0.1:8000，默认离线确定性 engine
uv run fretsure-mcp         # stdout 只承载 MCP protocol
```

Web 可以上传同一受限 MusicXML/MXL/MIDI 输入或加载 CC0 示例，显示独立的 playability / faithfulness
证据、ASCII tab、版本 stamps 与 `agent-trace@0.2.0` 回放。MIDI 不提供 bass-root/harmony 真值时，
`fidelity@0.3.0` 把两项显示为 `N/A`，不会把“没有证据”显示成 100%。API 使用有界 raw body，不使用 multipart 或
临时文件；proxy 默认禁用，只有有效的 loopback proxy 配置加 `fretsure-serve --allow-proxy` 才可用。
端点、安装组合、MCP tools 与 Claude Desktop/Cursor 配置格式见
[`docs/WEB_API_MCP.md`](docs/WEB_API_MCP.md)；Plan 6A 的历史闭门记录见
[`docs/PLAN6A_ACCEPTANCE.md`](docs/PLAN6A_ACCEPTANCE.md)，MIDI 闭门证据与 Git receipt 见
[`docs/MIDI_ACCEPTANCE.md`](docs/MIDI_ACCEPTANCE.md)。

把一份 lead sheet 编成一份 **GREEN（通过版本化可弹性模型）** 的指弹谱并打印 ASCII tab、oracle 判决、忠实度门：

```
ARRANGED TAB (high-e on top)
  e|------------------------------------------------|
  B|------10----10----------12----------------------|
  G|---10-------------10-12--0-----------9----------|
  D|10-------10----10----------12-12-10----10-10-10-|
  A|12-----------8----------------------12----------|
  E|------------------------------------------------|

ORACLE VERDICT
  GREEN — 通过收紧后的版本化简化模型（checker oracle@0.2.0, profile median@0.1）
FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root 1.00   harmony 0.75   gate PASS
```

这张谱不是生成器的"意见"：确定性 oracle 对每个音/每个框逐一核过已公布的简化几何与有限时序模型才给出 GREEN。**离线 fallback 或 LLM 只提议意图；GREEN 是所选 model/profile 内的机器认证，不是真人普适保证。**

## 架构（oracle 当环境、LLM 当策略）

```
lead sheet / MIDI / IR
        │
        ▼
  LLM 提议编配 ──────────────┐  （策略：只决定音乐意图，不决定指法）
        │                    │
        ▼                    │ 可选修复循环（verifier-guided，到不动点）
  确定性指法求解（beam）      │  读 oracle 定位化诊断 → 编辑 DSL（v2: NOT_KEPT）
        │                    │
        ▼                    │
  可弹性 ORACLE ─── RED ──────┘
   （毫米几何 / active sustain / 连续换把 / 三态判决）
        │ GREEN/AMBER
        ▼
  可选 best-of-N / critic + 忠实度门
        │
        ▼
  模型内可证明可弹的 TAB ──► checker 打分 benchmark（非 LLM 评委）
```

范式：**oracle 当环境、LLM 当策略**；harness 自研（框架仅作对照基准）；每个 agent 能力用 **leave-one-out 消融**挣存在，**砍掉的组件公开**。

## Benchmark v2：当前数字不漂亮，但结论可信

正式 `gpt-5.6-sol` 运行在 500 个独立 procedural families 上以
`oracle@0.2.0 + fidelity@0.3.0` 打分，`full`（repaired best-of-4 + critic）联合成功
74/500 = **14.8%**，Wilson 95% CI **[12.0%, 18.2%]**。所有高复杂度格以及 3 个
许可 public controls 都是 0；因此不能把程序语料结果外推成真实曲目能力。

| 能力 | 配对结果 | 正式裁决 |
|---|---|---|
| repair | +5.66pp，95% CI [4.56, 6.82]，低于 10pp SESOI | **NOT_KEPT** |
| best-of-4 | +6.8pp，95% CI [4.8, 8.8]；34 improved / 0 worsened | **PROBATION_COST_UNKNOWN** |
| critic | joint −0.2pp，95% CI [−0.6, 0]；无真人听感证据 | **HUMAN_BLOCKED_PROBATION** |

repair 的正向变化是真实的，但没有达到跑前冻结的“值得保留”线；旧 Plan 4 的“修复是承重
能力”只保留为 legacy 记录，不再代表当前证据。best-of-4 通过效果门，但 provider token/cost
不完整，所以不能判定部署 Pareto。critic 的自评分方向是结构性的，不能冒充 musicality。
完整分层、CI、Holm/McNemar、null/negative、usage availability 与 replay receipt 见
[`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md) 和
[`docs/BENCHMARK_V2_ACCEPTANCE.md`](docs/BENCHMARK_V2_ACCEPTANCE.md)。

## 状态

**Plan 1–5、Oracle 0.2 软件信任门、安全 `.mxl`、Plan 6A、producer-driven MusicXML/IR、
strict MIDI input 与 benchmark v2 Task 1–10 均已闭门。当前停在真人、许可、独立第二
provider 等明确 OPEN gate，等待下一步指令。**
当前 package=`0.6.0`、router=`score-input@0.1.0`、importers=`musicxml@0.3.0` / `midi@0.1.0`、
faithfulness=`fidelity@0.3.0`，trace=`agent-trace@0.2.0`、service=`fretsure-service@0.2.0`、
API=`fretsure-api@0.2.0`、MCP=`fretsure-mcp@0.2.0`、Web=`fretsure-web@0.2.0`；playability=
`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、container=`mxl-container@0.1.0` 保持不变，
`music21==10.5.0` 精确锁定。MIDI 的两正两负 exact producer rows、资源门、诚实限制与 Git receipt
见 [`docs/MIDI_ACCEPTANCE.md`](docs/MIDI_ACCEPTANCE.md)。benchmark v2 已完成 500 个 procedural
families + 3 个许可 public controls、机器预注册、逐项 rows、统计、WAL、正式 provider collection
与双 FULL_RESCORE replay；聚合报告和 COMPLETE receipt 已公开，完整重放包因许可/模型输出
再分发依据未记录而保持 owner-controlled，不主张 public rescore。

Formal attempts 001–003 是不可恢复的 `INCOMPLETE` 历史证据，累计 known/tight cost 为
`$2.130022 / $804.234022`；其 pre-call、WAL、abort receipt 与修复说明保留在项目状态和实验日志，
不与最终结果合并。Attempt-004 绑定 execution `773c69de…`，完成 10,060 network units、
10,563 rows、45,215 logical calls / 45,700 attempts，并通过 provider-free finalization 与两次
逐字节相同的 FULL_RESCORE。缺失 provider usage 仍为 null，不能写成零。

正式并发经两轮 4-vs-8 pilot 后保持 4：最新网络复测的 8/4 unit/call 吞吐比仅
`1.0088 / 1.0703`，未过 `1.35 / 1.25` 门槛。中断恢复只重跑未 READY units，保留 durable
prefix；隔离 usage 进入成本附录。正式 collection 已结束，原小时监控也已删除，不应再启动或
resume attempt-004。

中断/恢复、orphan-lane 隔离、普通 stub A/B、四路 coordinator A/B、raw-only finalization 和双
FULL_RESCORE 均已完成。完整 operator 证据、金额与 hashes 留在
[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md) 和
[`docs/experiments/2026-07-17-benchmark-v2-implementation-log.md`](docs/experiments/2026-07-17-benchmark-v2-implementation-log.md)，README 不重复展开。
本阶段只把 Web 控件默认值同步为同一证据基线，没有视觉设计改动；若后续涉及前端设计，仍须先确认统一审美。详见
[`2026-07-17-benchmark-v2.md`](docs/superpowers/plans/2026-07-17-benchmark-v2.md)。完整 Plan 6 的音频、
AlphaTab、真实琴颈动画、导出、live A/B/榜单与真人 money moment 仍 open。

- **Plan 1 核心 + 可弹性 Oracle**：Music IR + strict public Tab schema + 毫米几何/active-sustain/连续换把 oracle（三态 + 定位化诊断）+ fingerprinted profile + 自验证台（property/metamorphic/mutation/N-version + fail-closed gold/statistics）。zero-GREEN 明确是 `no_green`/`None`，不是完美的 `0.0`。见 [`docs/PLAN1_ACCEPTANCE.md`](docs/PLAN1_ACCEPTANCE.md)、[`docs/SCOPE.md`](docs/SCOPE.md)。
- **Plan 2 求解器**：beam-search 指法求解，每个部分谱都对真 oracle 核验 → **永不返回 RED**。
- **Plan 3 agent 循环**：LLM 编配 + 编辑 DSL + verifier-guided 可选修复 + best-of-N + 乐感 critic；v2 中 repair 未过保留阈值、search 成本未知观察、critic 等真人证据。
- **Benchmark v2**：500 procedural families + 3 分离 public controls、共享十候选池、family-level 配对推断、完整 pass@k/pass^k、WAL/replay 与公开负结果。
- **Plan 5 难度 + 伴奏**：可验证的"简化到目标 tier"（对 `check_tier` 门修复；tier 控制深快照；横按 overlap 为保持诊断语义的 `O(6n)` 扫描）+ 和弦声位/分解/扫弦伴奏。
- **Pre-Plan 6 MusicXML（历史闭门记录）**：安全 envelope + fail-closed 语义预检 + raw exact timeline + music21 语义交叉验证 + 文件 CLI。该阶段的两个未经手改的 library/toolkit exporter 正例（music21 10.5.0、musicxml 1.6.1）冻结了版本、SHA-256 与许可证；MuseScore Studio 4.7.4 原样导出在当时的 importer 中因省略 key mode 被稳定拒绝。这条历史行为不覆盖下面的后继实现。
- **Producer-driven MusicXML/IR**：`musicxml@0.3.0` 只扩 MusicXML 4.0 traditional key 中合法省略
  `<mode>` 的已观测 failure bucket；它保留 `mode=unprovided` 并发 warning，不从音符、和弦或
  music21 推断调式。MusicXML 3.1 省略 mode、其他 mode、复调与其余延后语义仍拒绝。兼容性主张只覆盖
  manifest 中精确冻结的 MuseScore Studio 4.7.4 原样 artifacts，不外推到该版本任意乐谱、其他版本或
  “完整 MusicXML”。详见[实现计划](docs/superpowers/plans/2026-07-16-producer-driven-musicxml-ir.md)、
  [producer census](docs/experiments/2026-07-16-producer-musicxml-census.json)与
  [验收记录](docs/PRODUCER_MUSICXML_ACCEPTANCE.md)。
- **Strict MIDI input（software acceptance complete）**：`score-input@0.1.0` 按 suffix 路由，`midi@0.1.0`
  先做资源有界的完整 SMF preflight，再把最小 canonical event stream 交给 music21 10.5.0、
  `quantizePost=False` 交叉验证；第三方不接触 raw hostile bytes。MuseScore 4.7.4 melody-only 正例保留
  7 beats 与每音 1 tick release gap，music21 正例保留 8 beats；两个含 realized harmony 的导出均为
  typed negatives，不挑 melody、不反推 chord/role。详见 [计划](docs/superpowers/plans/2026-07-17-midi-input.md)、
  [census](docs/experiments/2026-07-17-midi-census.json)与[验收记录](docs/MIDI_ACCEPTANCE.md)。
- **Oracle 0.2 trust gate**：不可信 Tab/profile/solver/MusicIR/tier/benchmark/gold 输入在任何几何、搜索、生成或统计工作前进入 typed validation + detached snapshot；Trace 在编码前精确核算 escaped UTF-8 大小，solver 有 12,000,000 weighted-work 上限且返回结果仍必须过完整 oracle。真人 gold 尚未采集，因此现实世界误接受率和 profile/tier 校准仍 open。
- **Safe `.mxl`**：在构造 `ZipFile` 前有界解析 EOCD/central/local records；拒 ZIP64、SFX、路径别名、特殊文件、加密与未知 extra，逐 member 流式解压并双重核对 size/CRC，只把 `container.xml` 唯一指定的 root bytes 交给既有语义管线。raw archive/root 双 SHA-256 与 rootfile provenance 均保留。
- **Plan 6A Web/API/trace/MCP**：bytes-first application seam；严格 loopback Host/Origin、raw body、typed
  problem responses 与显式 proxy permission；版本化 replay checkpoint；三个 stdio MCP tools；古典制琴工坊
  风格的 React UI。用户已于 2026-07-16 明确认可视觉方向；完整 Plan 6 未被提前勾完。见
  [`docs/PLAN6A_ACCEPTANCE.md`](docs/PLAN6A_ACCEPTANCE.md)。

设计文档是唯一真源：
- 设计 spec：[`docs/superpowers/specs/2026-07-09-fretsure-design.md`](docs/superpowers/specs/2026-07-09-fretsure-design.md)
- 实现路线图：[`docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md`](docs/superpowers/plans/2026-07-09-fretsure-implementation-roadmap.md)
- 项目状态 / 恢复：[`docs/PROJECT_STATE.md`](docs/PROJECT_STATE.md)

## 开发（Build & test）

用 [uv](https://docs.astral.sh/uv/) 管理 Python 3.11 环境：

```bash
uv sync --extra dev              # 建 3.11 venv + 装依赖
uv run pytest -q -m "not integration"
uv run ruff check                # lint
uv run mypy --strict src         # 类型检查
uv run fretsure-demo             # 一条命令端到端 demo
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml
uv run fretsure-arrange tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid
uv run fretsure-bench --stub --seed 1 --items 16 --output-dir /tmp/fretsure-stub
```

`fretsure-bench --stub` 与 replay 完全离线，并要求新的输出目录。只有集成测试、`--llm` 与
`fretsure-bench --live --pre-call-config ...` 需要显式本地 LLM 代理（loopback
`ANTHROPIC_BASE_URL` + 非空 `ANTHROPIC_AUTH_TOKEN`）；live benchmark 还要求 runner-ready、价格与
attempt-local pre-call 门，并显式重复该 attempt 的精确金额：

```bash
uv run fretsure-bench --live --pre-call-config <pre-call.json> \
  --authorized-maximum-spend-microunits 1167905640000 \
  --output-dir <fresh-attempt-004>
```

默认 model id 为 `gpt-5.6-sol`，服务端网络 engine 另须 `--allow-proxy`。
