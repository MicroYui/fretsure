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
strict MIDI input 与 benchmark v2 Task 1–8 均已闭门；Task 9 formal attempts 001–003 均已
terminal `INCOMPLETE`，下一次正式采集只能是 fresh attempt-004。**
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
SHA-256=`9b50fd8a271a78705e728de8f8cbb24a09e08b24eb2db9122df6a943bdd958f6`。

formal attempt-002 的 pre-call SHA-256 是
`48796200a05af2cbc9ae83d80f06a89ff437841810241954a8b7fe3f794be6eb`，绑定 execution commit
`1feeef622d96a95b187c473a40e273852cdf6a45`。它提交了 `524/10,563` 个 scheduled rows，
共发起 91 个 logical calls / 131 次 provider attempts；其中 72 次成功且 usage 完整，59 次
usage 缺失，19 个 logical calls 以 `DELEGATE_FAILED` 结束。一个合法 edit 应用后产生重复
onset/pitch，target checkpoint 的本地校验异常逸出，collector 因
`unexpected_unowned_observation` 将 run durable 终止为 `INCOMPLETE`。未检查私有
prompt/response，也未将其写入文档或 canonical 工件。attempt-002 的已知 / tight upper 为
`$0.986494` / `$416.110494`，不得恢复或覆盖。修复只把 post-edit pitch 越界 / onset-pitch
碰撞映射到既有 `MODEL_EDIT_INVALID` → `RECHECK` 路径，不改 prompt、model、corpus、schedule
或 trace schema。

formal attempt-003 使用 pre-call SHA-256
`fc3091ba8684b8d08304a3752f0662c9c82e951ee62db40131ed772b1ee65bad`，绑定 execution commit
`4dd7be9880dcccf2744d05e3617d6411d60ab4de`。完成 503 个 pure-solver rows 后，live 段暴露
系统性的 30 秒 request timeout：raw/proposal 长生成反复以三次 30 秒 attempts 加固定 backoff
失败。该 run 在 `523/10,563` rows、78 logical calls / 113 provider attempts 处终止，已知 / tight
upper 为 `$0.955113` / `$359.791113`，现为不可恢复、不可覆盖的 terminal `INCOMPLETE`。
Attempts 001–003 累计已知 / tight upper 为 `$2.130022` / `$804.234022`；再加一个完整 formal
attempt 的机械最大值后，累计审计上界为 `$1,168,709.874022`。

attempt-004 只能在 amended runner、预注册、预算绑定、crash/resume 测试、吞吐 pilot 与完整
release gates 推送后从新目录开始。正式候选默认 `4` 个 in-flight units、request timeout 为
`300` 秒；这是覆盖 pool/connect/TLS/write/read 与慢分块响应的整次 attempt 硬 deadline，另为
WAL 与 timeout delivery 预留 `10` 秒记录开销。analysis-excluded pilot 按 `2 → 4 → 8` 运行。
`8` 只能在 `4` 与 `8` 各至少八个完整
block（各 64 units）并经独立确认后选择，否则保持 `4` 并重新绑定 attempt-004 工件。正式进程
须脱离交互会话运行；durable unit 可恢复，进度只写 append-only operator log，不进入分析工件。
运行时不调用 Git 或子进程。
该 pilot 已在 execution commit `08f456d2a21b63dc01e2586fc842e9e8cb64c34a` 上完成并经
独立确认。`4` 与 `8` 各运行 8 个完整 block / 64 units；`4` 为 225.82 unit/h、0 retry，
`8` 为 221.19 unit/h、9 retries，8/4 unit 与 call 吞吐比仅 0.9795 / 0.9897，均未达到
1.35 / 1.25 门槛，因此 attempt-004 保持 4 路。comparison SHA-256 为
`452d31be314bd66a6fe73548bb8d12078c38a132c968c3b95f92b212c9901d6d`。按 4 路八块外推
10,060 个网络 units：乐观 35:29、中位 41:14、合并速率 44:33、保守 67:22；正式运行会用
真实 durable progress 持续修正。
正式启动由外部 supervisor（不是 collector runtime）使用；当前 Codex host 已验证用
`export ANTHROPIC_BASE_URL="${ANTHROPIC_BASE_URL/localhost/127.0.0.1}"` 后运行
`screen -dmS fretsure-task9-attempt004 /bin/zsh -c 'echo $$ > <attempt-004.pid>; exec <repo>/.venv/bin/fretsure-bench --live --pre-call-config <attempt-004-pre-call.json> --authorized-maximum-spend-microunits 1167905640000 --output-dir <fresh-attempt-004> >> <attempt-004.operator.log> 2>&1'`。
PID 与 log 放在 fresh output directory 的同级目录；detached 命令必须直接 `exec`
`.venv/bin/fretsure-bench`，不能套 `uv run`，否则记录的是不可靠转发 `SIGINT` 的监督进程 PID。
正式与 pilot 启动前还须把代理地址写成数值 loopback（`127.0.0.1` 或 `::1`），不能使用
`localhost`，避免名称解析落在整次 attempt deadline 之外；formal runner 与 pilot 也会在建
客户端前机械拒绝非数值 loopback。
优雅停止用 `kill -INT "$(cat <attempt-004.pid>)"`。`SIGINT` 会停止新 admission 并等待最多 4 个
已启动 unit 完整落盘，因此可能不是立即退出；必须等该 PID/`screen` 会话结束。若 canonical 已
发布且 receipt 为 `COMPLETE`，该 attempt 已完成，不应 resume；否则才以同一 output directory 和
`--resume` 重新 detached 启动。不要重复发 `SIGINT` 或改发
`SIGKILL`，除非明确要让本次 attempt 以 fail-closed 方式终止。

若机器断电等非优雅中断留下 active lane 的 open provider boundary，普通 `--resume` 仍会先
fail-closed 并生成 abort/audit 检查点。此时不必报废全部 durable prefix；在 operator 明确授权后，
先用 `scripts/task9_recover_orphan_lanes.py --plan` 生成零修改 plan，再以同一参数加
`--apply --expected-plan-sha256 <exact-plan-sha256>`。该工具不调用 Git、子进程、代理或 provider；
它只把 active lane 的整条 WAL、可选未绑定 unit artifact 以及旧 abort/audit 原字节隔离，原索引
建立空 WAL，最后移动 abort marker。随后原 execution、pre-call、output directory 和精确金额下的
`--resume` 只会重跑最多 4 个未提交 units，已 durable 和 READY 的工作保持不变。隔离 attempts
仍须通过 recovery receipt 计入最终 cost addendum；缺失 usage 不得写成零。每次恢复都使用新的
`recovery-id`，并在 apply 后运行相同工具的 `--check`。
P1 wall-reservation amendment 之前的普通 full stub 验收覆盖全部 `10,563` rows：A 在
167 个 durable units 时收到一次
`SIGINT`、排空到 212 后同目录 resume，30:05 完成；B 连续运行 27:00 完成。两次均为
`COMPLETE`，且 7 个 canonical 文件逐字节相同。finalize 从旧路径的 22 分钟以上降到约
4–5 分钟。该命令走 legacy sequential stub 路径，因此只证明 full-rescore、普通 stub resume 与
最终字节确定性，不证明 formal 4-lane coordinator。后者由 provider-free
`scripts/task9_operational_stub_gate.py` 以同一 10,563-row schedule 单独执行中断/恢复 A/B；该
脚本保持 `stub=True`，不能构造代理客户端或产生费用。
最终 amended gates 已通过。普通 full stub 两次均经一次干净中断/恢复后成为 `COMPLETE`，总
wall time 为 28:22 / 35:59，7 个 canonical 文件逐字节相同。真正的 4-lane gate 中，A 在
admitted 284 时收到唯一一次 `SIGINT`，1 个 in-flight 在 1 秒内排空，随后同目录 resume；A / B
分别在 30:12 / 27:24 后 `COMPLETE`，均为 10,563 rows / 15,090 calls，5 个 canonical 文件
逐字节相同。共同 SHA-256 为 blobs `8f245bec…`、config `2cdb96b1…`、observations
`8dbcf25e…`、receipt `223c9f07…`、rows `cff6de86…`；普通 gate 的 report JSON / Markdown
另为 `8c9e55ae…` / `0787de06…`。
本阶段没有前端改动；若涉及前端设计，仍须先确认统一审美。详见
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
  --output-dir <fresh-attempt-004>
```

默认 model id 为 `gpt-5.6-sol`，服务端网络 engine 另须 `--allow-proxy`。
