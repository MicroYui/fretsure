# Fretsure

**给你一份人手可证明弹得出来的吉他谱。**

Fretsure 是一个 agent：输入一首歌的音乐内容（乐谱 / MIDI / lead sheet；mp3 作 best-effort 前端），输出一份在你指定难度、调弦、变调夹下**可证明弹得出来**的吉他谱——HERO 是指弹独奏，也做伴奏与难度简化。前沿 LLM 提议编配，一个**确定性可弹性 oracle** 逐音硬门把关并在保住旋律/低音/和声的前提下自动修复，正确性由 **checker（而非另一个 AI）**在公开 benchmark 上验证。

> 定位（已过敌意核查）："Suno 给你一首弹不了的歌；Fretsure 给你一份人手可证明弹得出来的谱。"

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
  --n 1 --no-critic --trace-jsonl /tmp/fretsure-trace.jsonl

uv sync --extra midi
uv run fretsure-arrange tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid \
  --n 1 --no-critic
```

该入口会依次输出 typed import diagnostics、MusicIR 摘要、ASCII tab、
`oracle@0.2.0` 判决、fingerprinted profile、`tab-input@0.2.0`、独立的
`fidelity@0.3.0` 门与可回放 JSONL trace；CLI 结果头另行绑定 importer/router
版本。`--llm` 的当前默认是 canonical `gpt-5.6-sol`；CLI、trace 与 benchmark
聚合 JSON 都显式记录 model id。
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
  e|--1-5---5-------7-------0-------|
  B|1-----------6-8-----------------|
  G|2-----5---5-----0-7-7-5---5-5-5-|
  D|--------3---------------7-------|
  A|--------------------------------|
  E|--------------------------------|

ORACLE VERDICT
  GREEN — 通过收紧后的版本化简化模型（checker oracle@0.2.0, profile median@0.1）
FAITHFULNESS TO INPUT
  melody-F1 1.00   bass-root 1.00   harmony 1.00   gate PASS
```

这张谱不是生成器的"意见"：确定性 oracle 对每个音/每个框逐一核过已公布的简化几何与有限时序模型才给出 GREEN。**离线 fallback 或 LLM 只提议意图；GREEN 是所选 model/profile 内的机器认证，不是真人普适保证。**

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
   （毫米几何 / active sustain / 连续换把 / 三态判决）
        │ GREEN/AMBER
        ▼
  best-of-N 选择 + 忠实度门 + 乐感 critic
        │
        ▼
  可证明可弹的 TAB   ──►  checker 打分 benchmark（非 LLM 评委）
```

范式：**oracle 当环境、LLM 当策略**；harness 自研（框架仅作对照基准）；每个 agent 能力用 **leave-one-out 消融**挣存在，**砍掉的组件公开**。

## Benchmark 头条：修复挣得了它的存在

在程序生成、抗污染的 lead sheet 语料上做 leave-one-out 消融（`fretsure-bench`，真实 LLM）。打分用 oracle + 忠实度门，**不是 LLM 评委**。下表是 2026-07-10/11 的 legacy、未版本化 harmony metric 快照；后继 `fidelity@0.2.0` 改为 chord-segment 语义，当前 `fidelity@0.3.0` 又增加 evidence availability。benchmark v2 重跑前不能把旧数当作当前基线。

| arm | joint_success | melody-F1 | Wilson 95% |
|---|---|---|---|
| **完整 agent** | **0.81** | 1.00 | [0.57, 0.93] |
| **− 修复** | 0.31 | 0.56 | [0.14, 0.56] |
| − critic | 0.81 | 1.00 | [0.57, 0.93] |
| − best-of-N | 0.94 | 1.00 | [0.72, 0.99] |

- **修复是承重能力**：去掉它，成功率 0.81→0.31、melody-F1 1.00→0.56，两个 Wilson 区间**不重叠**；两个 seed 合并（n=32）仍不重叠（full [0.72,0.95] vs −修复 [0.18,0.49]）。
- **best-of-N 挣得一份薄利**：非配对臂符号在两 seed 间翻转（采样混淆），但**配对消融**（同一提议池，只变选择宽度）两 seed 一致 **+0.125 GREEN**——薄但真。
- **critic 尚未挣得存在**：**配对**测其本职（taste），两 seed taste 仅 **+0.01**、对 joint 门 ≤0——在此语料上可忽略。留在 agent 里**观察**，须在更难/更重口味的语料证明自己，否则砍。完整记分卡见 [`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md)。
- 一个 **"谁检查语料"** 的发现：一次 `joint_success=0` 追到的是**语料标注 bug**（0 索引音级把 `C:deg5` 误写成 V），非 agent/度量的错。全部经过见 [`docs/BENCHMARK_RESULTS.md`](docs/BENCHMARK_RESULTS.md)。

## 状态

**Plan 1–5、Oracle 0.2 软件信任门、安全 `.mxl`、Plan 6A、producer-driven MusicXML/IR、
strict MIDI input 与 benchmark v2 Task 1–8 均已闭门；Task 9 formal attempt-001 已终止为
`INCOMPLETE`，修正计费契约后将从 fresh attempt-002 继续。**
当前 package=`0.6.0`、router=`score-input@0.1.0`、importers=`musicxml@0.3.0` / `midi@0.1.0`、
faithfulness=`fidelity@0.3.0`，trace=`agent-trace@0.2.0`、service=`fretsure-service@0.2.0`、
API=`fretsure-api@0.2.0`、MCP=`fretsure-mcp@0.2.0`、Web=`fretsure-web@0.2.0`；playability=
`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、container=`mxl-container@0.1.0` 保持不变，
`music21==10.5.0` 精确锁定。MIDI 的两正两负 exact producer rows、资源门、诚实限制与 Git receipt
见 [`docs/MIDI_ACCEPTANCE.md`](docs/MIDI_ACCEPTANCE.md)。benchmark v2 已冻结 500 个 procedural
families + 3 个许可 public controls、机器预注册、预算、逐项 rows、统计、WAL 与 replay。Task 8
attempt-002 已 COMPLETE 8/8：27 个 logical calls、31 次 attempts、4 次 retries，provider / host
耗时分别为 `473,726,578` / `477,264,352` µs，返回模型仅 `gpt-5.6-sol`。两次
pilot 合计已知 / corrected tight upper 为 `$0.513140` / `$27.730036`；按官方 `128,000`
输出 token 契约重算的单次 pilot 机械上限为 `$513.232896`。历史
`$10.960896` / `$11.574272` 是把 visible request limit 误当 billable output cap 时的已披露授权数，
保留作为审计记录，不是当前计费上限。

formal envelope 现以官方 `gpt-5.6-sol` 模型上限冻结每个 attempt 的
`128,000` billable output tokens（包括不可见 tokens）；在该官方契约下，Task 9 机械最大值为
`1,167,905,640,000` micro-USD（`$1,167,905.640000`）。这是计费契约下的审计上限，
不是本地代理的预消费硬闸。用户已统一授权项目模型计费；历史
`$538,865.486400` gate 及 attempt-001 pre-call 保持不变，但不再用于新调用。formal attempt-001
在 503 个 pure-solver units 和第一个 agent unit 后终止：critic 的 visible limit 为 `512`，
provider 报告的 billable output usage 为 `704`；旧 validator 将两者误作同一上限。该 run 已
terminal `INCOMPLETE`，已知 / tight upper 为 `$0.188415` / `$28.332415`，不得恢复或覆盖；
未检查私有 prompt/response。formal runtime 仍在 observation/WAL/retry/network 前执行
UTF-8 bytes + 256 guard，要求 caller 重复精确 spend；live 只产五个 raw canonical 工件，
报告由两个独立 full replay 生成。新 `benchmark-formal-budget-gate@0.3.0` 已生成并通过回检，
SHA-256=`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`。后续只允许在
修正后的 clean pushed runner SHA 上创建 fresh attempt-002 pre-call；
本阶段没有前端改动。详见
[`2026-07-17-benchmark-v2.md`](docs/superpowers/plans/2026-07-17-benchmark-v2.md)。完整 Plan 6 的音频、
AlphaTab、真实琴颈动画、导出、live A/B/榜单与真人 money moment 仍 open。

- **Plan 1 核心 + 可弹性 Oracle**：Music IR + strict public Tab schema + 毫米几何/active-sustain/连续换把 oracle（三态 + 定位化诊断）+ fingerprinted profile + 自验证台（property/metamorphic/mutation/N-version + fail-closed gold/statistics）。zero-GREEN 明确是 `no_green`/`None`，不是完美的 `0.0`。见 [`docs/PLAN1_ACCEPTANCE.md`](docs/PLAN1_ACCEPTANCE.md)、[`docs/SCOPE.md`](docs/SCOPE.md)。
- **Plan 2 求解器**：beam-search 指法求解，每个部分谱都对真 oracle 核验 → **永不返回 RED**。
- **Plan 3 agent 循环**：LLM 编配 + 编辑 DSL（旋律保护）+ verifier-guided 修复到不动点 + best-of-N + 乐感 critic。
- **Plan 4 benchmark**：程序生成语料 + pass@k/pass^k 无偏估计 + leave-one-out 消融 + checker-vs-LLM-judge + 一条命令 `fretsure-bench`。
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
  --output-dir <fresh-attempt-002>
```

默认 model id 为 `gpt-5.6-sol`，服务端网络 engine 另须 `--allow-proxy`。
