# Fretsure

**给你一份在版本化模型/profile 内可证明可弹的吉他谱。**

Fretsure 是一个 agent：当前输入乐谱 / MIDI / lead sheet；mp3 只是尚未实现的未来 best-effort 前端。它输出一份在你指定难度、调弦、变调夹下、由版本化模型检查的吉他谱——HERO 是指弹独奏，也做伴奏与难度简化。前沿 LLM 提议编配，一个**确定性可弹性 oracle** 逐音硬门把关；verifier-guided repair 已实现为可选研究/兼容能力，但正式 benchmark v2 未达到它的预注册保留阈值，因此不再把“修复已挣得默认存在”作为当前产品主张。结果由 **checker（而非另一个 AI）**验证。

> 产品定位："生成模型给出编配；Fretsure 用公开、版本化的可弹性模型逐音核验。"

## 一条命令看它跑

```bash
uv sync --extra dev
uv run fretsure-demo          # 离线确定性跑通；加 --llm 用真实 LLM 编配
```

真实文件纵切已经支持受限的 MusicXML 3.1/4.0 lead sheet、可确定缩编的双谱表钢琴谱（未压缩
`.musicxml`/`.xml` 与安全 `.mxl`），以及严格的 melody-only Standard MIDI File 子集：

```bash
uv sync --extra musicxml
uv run fretsure-arrange tests/fixtures/musicxml/supported_basic.musicxml \
  --trace-jsonl /tmp/fretsure-trace.jsonl

uv sync --extra midi
uv run fretsure-arrange tests/fixtures/midi/producers/music21-10.5.0-melody_only.mid

uv sync --extra exports       # MusicXML TAB / GP5 / PDF programmatic renderers
```

`exports` 使用 LGPL-3.0-only 的 PyGuitarPro 写 GP5，并使用 BSD 许可的 ReportLab 写 PDF；
依赖由 `uv.lock` 固定而不复制进仓库。PDF 测试工具 pdfplumber（MIT）与 pypdf（BSD）仅属于 `dev` extra。

该入口会依次输出 typed import diagnostics、MusicIR 摘要、ASCII tab、
`oracle@0.3.0` 判决、fingerprinted profile、`tab-input@0.2.0`、独立的
`fidelity@0.3.0` 门与可回放 JSONL trace；CLI 结果头另行绑定 importer/router
版本。`--llm` 的当前默认是 canonical `gpt-5.6-sol`；CLI、trace 与 benchmark
聚合 JSON 都显式记录 model id。
与 v2 裁决一致，冻结 benchmark/legacy policy 仍默认单候选、零 repair、critic 关闭。真实 LLM 产品
路径则使用独立的 baseline-first incremental policy：source melody 的 onset/pitch/duration anchors 原样成为
GREEN 基线，模型每个候选只提议一次，可添加 bass、harmony，以及只落在源旋律真实静音 gap 内的安全
melody fills。确定性调度把全曲 bass 骨架放入 layer 1、harmony/fill 放入 layer 2，最多 8 次全曲
检查采用跨层轮转、层内 breadth-first，避免 bass 拆分耗尽预算而让 harmony/fill 没有被验证；失败增量立即
回滚，不会改写原旋律 anchors 或牺牲最后一个 GREEN checkpoint。`--max-iters`
只保留给不加 `--llm` 的 legacy/研究兼容路径，不控制这个增量策略；
`--n` 仍控制 proposal 数，`--critic` 只评价已真正加入 Agent 音符的最终候选。
MusicXML 路径支持单 part/staff/voice 的 4/4 单音旋律、固定传统调号、固定 quarter-note tempo、
普通 note/rest/tie 和白名单 root+kind harmony。`musicxml@0.4.0` 另支持一个严格的双谱表钢琴缩编：
每小节上谱表必须是单声部旋律，下谱表必须由一次精确 `backup` 后的同步和弦帧组成，且每个音高集合
只能对应一个受支持和弦。成功时发出 `PIANO_REDUCTION_DERIVED`，明确说明下谱表的钢琴声位与转位
没有被保留；其余复调仍 fail-closed。MusicXML 4.0
可以省略 `<mode>`：importer 不猜 major/minor，而是保留
`key-signature:fifths=N;mode=unprovided` 并发出 `KEY_MODE_UNPROVIDED`；MusicXML 3.1
省略 `<mode>` 仍 fail-closed，显式 major/minor 继续保留原来的 key 表示。
`.mxl` 只扩展容器、不扩展这些 root MusicXML 语义。MIDI 只接受 format 0/1、PPQN、固定 tick-zero
tempo、固定 4/4、最多一个单声部非打击乐 note stream；raw tick/PPQN 是精确时间真源，所有音符标为
melody，`chords=()`，不猜 track 角色、bass、和弦、key 或量化。复调、多 note streams、SMPTE、
percussion、弯音/调律、sustain、SysEx 与变拍/变速/变调均 typed fail-closed。输入仍只接受符号乐谱；
已经验证的 canonical Tab 可在浏览器用 AlphaTab 本地音源播放，也可通过 FluidSynth 导出 WAV。

本地 Web、typed HTTP API 与 MCP stdio adapter 已在 Plan 6A 打通，在 Plan 6B 扩展为演奏工作台，
由 Plan 7A 补齐风格、人体工学和编辑反馈闭环，并由 Plan 7B 接入公版专家谱监督的指法排序与独立
出版分级估计：

```bash
uv run fretsure-serve       # http://127.0.0.1:8000，默认离线确定性 engine
uv run fretsure-mcp         # stdout 只承载 MCP protocol
```

Web 可以上传同一受限 MusicXML/MXL/MIDI 输入或加载 CC0 示例，上传前分别选择编配风格、player
hand profile、技巧偏好和难度目标。风格包括 Fingerstyle/Classical/Jazz/R&B；Jazz 的节奏相位来自
CC BY 4.0 GuitarSet 的 performer-disjoint 聚合，R&B 明确使用 Funk 邻近代理而非冒充直接 R&B
监督。技巧偏好只在完整
Oracle 已通过的候选中排序。难度档会进入编配目标并改变可选织体密度，生成后再由独立 tier checker
验收；它不决定固定谱面的指号。结果页支持最多
32 小节的局部重生成与 melody/bass/harmony 锁定、只改左手 1–4 指号的事务式 Oracle 复查，以及
匿名本地评分/A-B/指号修订记录与 JSON 导出；这些本地记录不被描述成 API 模型训练。默认指法会在
完整 Oracle GREEN 候选池中使用公开专家谱训练的 guarded ranker；结果页另显示低置信度出版分级
估计，但硬 difficulty tier 与固定谱面指法仍彼此独立。

工作台显示独立的
playability / faithfulness 证据、该目标的 checkpoint difficulty check、真实 AlphaTab 五线谱+六线谱、
同步指板、Agent/确定性来源、版本 stamps 与
`agent-trace@0.3.0` checkpoint 回放。浏览器使用仓库内 Bravura/Sonivox 资源播放；候选池只比较经过
双门核验的输出，并公开实际 model-call 数，critic 只显示为机器观察。结果可保存到仅含 canonical
结果和 provenance 的本地个人库。下载包括可继续编辑的 MusicXML 4.0 TAB、真实 Guitar Pro 5.1
`.gp5`、原生 Guitar Pro 7+ `.gp`、可打印 A4 矢量 PDF、format-0 MIDI、FluidSynth WAV、ASCII TAB
`.txt` 与 canonical Tab JSON。所有格式都从同一份已经验证的 canonical Tab 直接生成；GP5、
MusicXML 与 PDF 保留弦、品和可表示的双手指法。MIDI
输入不提供 bass-root/harmony 真值时，
`fidelity@0.3.0` 把两项显示为 `N/A`，不会把“没有证据”显示成 100%。API 使用有界 raw body，不使用 multipart 或
临时文件；proxy 默认禁用，只有有效的 loopback proxy 配置加 `fretsure-serve --allow-proxy` 才可用。
端点、安装组合、MCP tools 与 Claude Desktop/Cursor 配置格式见
[`docs/WEB_API_MCP.md`](docs/WEB_API_MCP.md)；Plan 7B 的当前验收记录见
[`docs/PLAN7B_ACCEPTANCE.md`](docs/PLAN7B_ACCEPTANCE.md)，Plan 7A 的历史验收记录见
[`docs/PLAN7A_ACCEPTANCE.md`](docs/PLAN7A_ACCEPTANCE.md)，Plan 6B 的历史验收记录见
[`docs/PLAN6B_ACCEPTANCE.md`](docs/PLAN6B_ACCEPTANCE.md)，Plan 6A 的历史闭门记录见
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
  GREEN — 通过收紧后的版本化简化模型（checker oracle@0.3.0, profile median@0.1）
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

**Plan 1–6B、Oracle 0.2 软件信任门、安全 `.mxl`、producer-driven MusicXML/IR、strict MIDI input
与 benchmark v2 Task 1–10 均已闭门。Plan 6B 的真实代理和匿名真人签收结果为有限的 `PARTIAL`。
Plan 7A 软件、真实 HTTP 与生产 Chrome 视觉/交互门均已通过并闭合；Plan 7B 的公开谱监督、
held-out 指法评测、出版分级估计及产品接入也已闭合。**
当前 package=`0.6.0`、router=`score-input@0.1.0`、importers=`musicxml@0.4.0` / `midi@0.1.0`、
faithfulness=`fidelity@0.3.0`，trace=`agent-trace@0.3.0`、service=`fretsure-service@0.3.0`、
API=`fretsure-api@0.3.0`、MCP=`fretsure-mcp@0.2.0`、Web=`fretsure-web@0.3.0`；playability=
`oracle@0.3.0`、fingering=`fingering-solver@0.6.0`、长谱组合=`score-solver@0.4.0`、左手人体工学=
`left-hand-ergonomics@0.1.0`、公开谱排序=`published-fingering-ranker@0.1.0`、出版分级=
`published-grade-estimator@0.1.0`、公共输入=`tab-input@0.2.0`、container=`mxl-container@0.1.0`，
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
Benchmark v2 当时只同步 Web 控件默认值；其后 Plan 6B 已沿获批的古典制琴工坊视觉完成 AlphaTab、
同步指板、音频、GP7、本地库和公平 live A/B，并以真人 `PARTIAL` 闭合。Plan 7A 在同一工作台增加
版本化风格、手型/技巧偏好、局部重生成、手动指号和本地偏好证据。实时公共 leaderboard 仍不在
范围内。详见 [`PLAN7A_ACCEPTANCE.md`](docs/PLAN7A_ACCEPTANCE.md)。

- **Plan 1 核心 + 可弹性 Oracle**：Music IR + strict public Tab schema + 毫米几何/active-sustain/连续换把 oracle（三态 + 定位化诊断）+ fingerprinted profile + 自验证台（property/metamorphic/mutation/N-version + fail-closed gold/statistics）。zero-GREEN 明确是 `no_green`/`None`，不是完美的 `0.0`。见 [`docs/PLAN1_ACCEPTANCE.md`](docs/PLAN1_ACCEPTANCE.md)、[`docs/SCOPE.md`](docs/SCOPE.md)。
- **Plan 2 求解器**：beam-search 指法求解，每个部分谱都对真 oracle 核验 → **永不返回 RED**。
- **左手指法求解器 v2**：固定谱面的指法与 difficulty tier 解耦；加入离散把位、自然指序、
  重复音连续性、换把、横按和同品跨弦人体工学；Plan 7B 用 21 首公版专家谱的明确指号，在完整
  GREEN pool 内做受约束的近似并列排序。后续授权扩库把规范化语料扩大到 58 个 example、2,362 个
  按弦指标签；扩展候选未通过 held-out 增益门，因此数据保留而生产模型不强行替换。
  详见 [`docs/LEFT_HAND_SOLVER_V2.md`](docs/LEFT_HAND_SOLVER_V2.md)。
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
- **Plan 6B performance workspace（closed with human PARTIAL）**：AlphaTab 1.8.4 本地谱面与播放、
  同步六弦指板、trial checkpoint、difficulty check、verified alternatives、实时证据记分卡、本地个人库、
  FluidSynth WAV、GP7 以及同 checkpoint 多格式互操作。产品 spans 只通过可选 OpenTelemetry seam 导出，
  public replay trace 仍是唯一产品证据真源；真实代理 run 与匿名琴手签收已完成，真人结果为 `PARTIAL`。
  见 [`docs/PLAN6B_ACCEPTANCE.md`](docs/PLAN6B_ACCEPTANCE.md)。
- **Plan 7A product controls and editing loop**：Fingerstyle/Classical/Jazz/R&B、三种 player profile、
  四种 GREEN-pool 技巧偏好、带声部锁的局部重生成、事务式手动左手指号，以及明确“不等于模型训练”的
  本地评分/A-B/修订证据。Jazz 已接入 GuitarSet 训练聚合，R&B 保持明确的 Funk 邻近代理；主观风格
  像真度与人体工学权重仍留待真实反馈校准。
  见 [`docs/PLAN7A_ACCEPTANCE.md`](docs/PLAN7A_ACCEPTANCE.md)。
- **Plan 7B published-score supervision**：21 首 Public Domain 作品、707 个明确左手指号用于受约束的
  GREEN-pool ranker；独立的 Delcamp/Eric Crouch 分级估计在 composer-grouped untouched test 上达到
  88.46% within-one。后续又加入 37 个 CC BY-SA 乐章、1,673 个按弦指标签；扩展模型没有 held-out
  增益，故未上线。数据、模型审计均带版本/hash/适用范围，不把版本一致率写成普适真人保证。
  见 [`docs/PLAN7B_ACCEPTANCE.md`](docs/PLAN7B_ACCEPTANCE.md)。

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
