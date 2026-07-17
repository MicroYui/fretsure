# MIDI input — strict SMF to loss-aware MusicIR

> **状态（2026-07-17）**：**DONE**。前置 producer-driven MusicXML/IR 已在提交
> `0fa6af728b1d4f483767cb54f1e9bd3151d58a7e` 推送；本阶段的 producer/parser/importer/product/docs、
> 全量 offline/real-proxy/Web/distribution gates 与三轮独立 review 均已完成。外部
> commit/push/local-tracking-remote receipt 已一致关闭在
> `46ff8ac070e97422b4aecf5c0f2a22b588a5fda4`，详见
> [`MIDI_ACCEPTANCE.md`](../../MIDI_ACCEPTANCE.md)；benchmark v2 已解锁。

**Goal:** 增加一个诚实、资源有界、可复核的 Standard MIDI File 输入纵切：把无歧义的单一单声部
note stream 精确转换为 MusicIR melody，贯通 CLI/application/API/Web/LLM/checker，同时不猜 track
角色、和弦、量化网格或缺失的 key。

**Architecture:** raw MIDI bytes 先经过 first-party 完整 SMF envelope/parser/preflight；raw tick timeline
是唯一时间真源。只有零 error 后才重建最小 format-0 event MIDI，交给精确锁定的 music21 10.5.0、
`quantizePost=False` 做逐 note 交叉验证。music21 不接收 raw 文件，不决定取哪条 stream，也不提供时间
真值。每个 success 在返回前通过 `snapshot_music_ir` 与 `validate_ir`。

本计划只做 **MIDI 输入**。MIDI/GP/MusicXML 导出、FluidSynth 播放、Lakh/benchmark corpus、自动多轨
角色识别、drop-D/capo 新控件与 Plan 6B 可视化均继续延后。

## 1. 冻结成功域

### 1.1 SMF envelope

- 后缀 `.mid` / `.midi`；raw Standard MIDI File，不接 RIFF RMID、压缩包或其他 wrapper。
- `MThd` 长度精确为 6；format 0 或 1；format 0 恰一 `MTrk`，format 1 为 1..64 tracks；声明数、
  chunk 长度、完整消费与 EOF 必须一致，未知 chunk 和尾随字节拒绝。
- 只接受 PPQN timing，division `1..32767`；SMPTE division 延后。
- delta-time 与变长长度使用 canonical 1..4-byte VLQ；缺字节、第五字节、溢出、非最短编码拒绝。
- running status 按 MIDI 1.0 语义解析；meta/system 后不能偷用失效 status；所有 data byte 必须 `0..127`。
- 每 track 必须恰有一个最终 End-of-Track，EOT 后不得有 event/data。

### 1.2 音乐语义

- 全文件只允许一个 `(track, channel)` 产生 note attacks；其他 metadata-only tracks 可存在。channel 10
  percussion、第二条 note-bearing stream、自动最高音/最低音选择与 GM program 角色推断全部拒绝。
- note stream 必须单声部：任一时刻最多一个 active note；普通 Note On/Off 与 velocity-zero Note On
  作为 Note Off 都支持。孤立 off、重复 active pitch、任意 overlap、零/负 duration、dangling note 拒绝。
- 所有 resolved notes 精确写为 `voice="melody"`；onset/duration 是 raw tick/PPQN 的 reduced
  `Fraction`，不量化、不补齐 articulation gap、不从 notation source 回推时值。
- note-bearing track 的 EOT tick 冻结 `Meta.duration_beats`，保留 trailing silence。
- 必须恰有一个 tick-0 tempo，换算后在 1..1000 BPM；缺失、重复或 change 拒绝。
- 必须恰有一个 tick-0 4/4 time signature；缺失、重复、其他 meter 或 change 拒绝。
- key signature 可恰有一个 tick-0 traditional major/minor。存在时按 `sf=-7..7` 与 `mi=0/1`
  精确映射；完全缺失时写 `key-signature:unprovided` 并发 located `MIDI_KEY_UNPROVIDED`，不从音符猜调。
- Standard MIDI 没有可靠 chord-symbol/voice-role 合同；因此 `chords=()` 并发
  `MIDI_HARMONY_UNPROVIDED`。这不是 bass/chord/harmony 证据。

### 1.3 有界 loss policy

- note velocity、off velocity、program change、MIDI port 以及 producer census 证明所需的非时值/非音高
  setup/mix controllers，只在完整解析后聚合为一个 `MIDI_PERFORMANCE_DATA_IGNORED` warning；canonical
  adapter 不携带这些值。
- channel allowlist 精确冻结为 Note On/Off、program change，以及 controller
  `{0,1,2,6,7,10,11,32,91,93,100,101,121}`；controller payload 仍须两个 7-bit bytes。poly/channel
  pressure 拒绝。note-bearing channel 在首个 note 前、tick 0 可有至多一个 14-bit center/no-op pitch bend
  (`LSB=0, MSB=64`) 并聚合 warning；非中心、重复、其他 channel 或后续 bend 拒绝。
- Data Entry 不能借 allowlist 偷渡调律：CC6 只允许作为 note-bearing channel 在 tick 0、首 note 前、
  当前 RPN 精确为 0/0 时的 pitch-bend-sensitivity 初始化；CC100/101 只允许在该 setup 窗口形成受控
  RPN 0/0 → null 127/127 序列。其他 CC6、调律 RPN、残缺/错序列均 typed 拒绝。因实际 positive 只允许
  center bend，该初始化仍只作为 performance loss warning，不改变权威 note pitch。
- sustain/sostenuto、All Notes/Sound Off、未列 controller、SysEx、system common/realtime 与会改变
  pitch/duration 的控制一律 typed 拒绝。
- meta allowlist 精确为：EOT(len=0)、tempo(3)、time(4 且 payload 精确 `04 02 18 08`)、key(2)、
  text/track-name/copyright/instrument/lyric/marker/cue（有界 loss policy）、channel-prefix(1)、MIDI-port(1)。
  SMPTE offset、sequencer-specific 与未知 meta 拒绝；结构损坏立即停止，不能 resync。
- 文本/meta payload 先受长度门。只有 note-bearing track 的单一 tick-0 printable-ASCII track name
  进入 title；缺失/不可解释时 title 精确为空字符串。单一 tick-0 printable-ASCII copyright 只作为
  `copyright-notice:<text>` 写入 `Meta.license`，不能被称为授权许可证；缺失时为 `unprovided` 并发
  `RIGHTS_UNPROVIDED`。其他文本只聚合 `MIDI_TEXT_IGNORED`，不进入 music21、终端或 Web。
- 诊断最多保留 256 条，再追加一个 `INPUT_LIMIT_EXCEEDED` sentinel；异常跨 importer 边界只暴露固定
  类别与类型名。

### 1.4 资源上限

- raw bytes：10 MiB；tracks：64；events：250,000；resolved notes：20,000。
- absolute tick：`0..2**31-1`；PPQN：`1..32767`；单 VLQ：4 bytes。
- note-bearing track 的 EOT span：最多 4096 quarter notes；该 exact 门在 canonical music21 adapter
  前同时约束 leading rest、note duration 与 trailing silence，防止稀疏 tick timeline 放大为空小节。
- 单 text/meta payload：1 KiB；累计 text/meta payload：64 KiB。
- 所有 chunk/VLQ/event/note/text/tick 门必须在相应分配、整数放大、music21、pipeline 与 LLM 前发生。

## 2. Faithfulness 可用维度

MIDI 单 melody stream 不提供 bass-root 或 harmony 真值；旧 `fidelity@0.2.0` 对缺失维度返回 1.0，若
原样进入 Web 会把 “没有证据”显示为 100%，属于产品 overclaim。本计划先修这个共享合同：

- `FaithfulnessGate` 的 melody/bass/harmony 分数按 source evidence 可为 `None`，并显式输出
  `evaluated_dimensions` / `unavailable_dimensions`。
- melody 只在源有 melody notes 时可评；bass-root 只在有 chord annotations 时可评；harmony 在有
  chord annotations 或显式 bass/harmony notes 时可评。缺失不是 1.0。
- `passed` 只对 available dimensions 应用既有阈值，并要求至少一个 available dimension。MIDI
  melody-only success 可以通过 melody fidelity，但 UI/CLI/trace 必须明确 bass/harmony 为 N/A，不能写
  “两个音乐门都以完整证据通过”。
- 内部 legacy candidate-ranking `Fidelity` 可保留 deterministic fallback；公开 authoritative gate 与
  benchmark joint-success 使用新的 availability 语义。
- 维度顺序固定为 `melody,bass_root,harmony`；evaluated/unavailable 必须无重复、完整互补。score 为
  `None` 当且仅当 unavailable；`passed` 必须可由 available scores 和冻结阈值重算，所有 serializer/
  trace/Web 入口 fail-closed 验证。既有 MusicXML/程序语料的三个分数与 passed 必须逐值保持 0.2 行为。
- benchmark 聚合对 unavailable 维度排除分母并报告 availability count；不得把 None 当 0 或 1。现有
  benchmark corpus melody 可用，因此历史 melody 数字应逐值不变。
- Web 复用现有 evidence card/metric 视觉，只把不可用项显示为 `N/A` 并修正文案；不增加页面、控件或
  新视觉系统。

## 3. 版本裁决

- package：`0.4.0 → 0.5.0`。
- 新增 dispatcher：`score-input@0.1.0`；新增 importer：`midi@0.1.0`。
- 保持 `musicxml@0.3.0`、`mxl-container@0.1.0` 与 exact `music21==10.5.0`。
- faithfulness：`fidelity@0.2.0 → @0.3.0`；availability 进入公开结果。
- trace：`agent-trace@0.1.0 → @0.2.0`。winner 事件把内部选择分数明确改名为 `ranking_*`，并另带与
  response 完全一致的 nullable authoritative `melody_f1/bass_root_accuracy/harmony_jaccard`、
  evaluated/unavailable dimensions 与 passed；不得把 legacy harmony fallback 冒充 source evidence。
- service/API/MCP/Web：`0.1.0 → 0.2.0`，因为 capabilities/importer registry 与 fidelity wire 公开变化。
- oracle、tab-input、target-input、profile、solver/search 合同不改义、不升版。
- 可选依赖保留 `[musicxml]`，新增 `[midi]`（music21 exact pin）与便捷 `[score]`；core 继续不拉
  music21/defusedxml/FastAPI/MCP/Anthropic。

## 4. Task 1 — Producer census 与冻结 evidence

**状态：完成；exact producer replay 与 final gates 已通过。**

**Files:**

- `scripts/generate_midi_fixtures.py`
- `tests/fixtures/midi/sources/**`
- `tests/fixtures/midi/producers/**`
- `tests/importers/test_midi_producer_fixtures.py`
- `docs/experiments/2026-07-17-midi-census.json`

**TDD / work:**

- 冻结两个原样 positive：music21 10.5.0 与 MuseScore Studio 4.7.4 从 checked、CC0、melody-only
  source 导出的 MIDI；manifest 双向绑定 producer/version/license/source/raw SHA、format、PPQN、
  event/note digest 与 expected warnings。
- 已冻结事实：MuseScore 产物 format-1/TPQ480/1 track，duration 为 7 beats 且每个 sounding note
  release 早 1 tick；music21 产物 format-1/TPQ10080/2 tracks，duration 为 8 beats 且保持 notation-exact
  note durations。逐 artifact 保存 raw duration，不声称跨 producer IR 相等，也不量化回 notation。
- 把含 harmony 的现有 supported_basic 导出作为 typed negative：MuseScore/music21 都会 realization 为
  多 note streams；不得挑一条或反推 chord/voice。
- generator 只写新 untracked 目录，拒绝覆盖 frozen corpus；exact producer version、source immutability、
  完整 SMF validation 与 manifest row 都通过后才接受输出。raw 只作 exact evidence，不先声称跨日可复现。

## 5. Task 2 — Strict SMF preflight

**状态：完成；full suite 与独立 security review 已通过。**

**Files:**

- `src/fretsure/importers/_midi_preflight.py`
- `src/fretsure/importers/contracts.py`
- `tests/importers/test_midi_preflight.py`
- `tests/importers/test_midi_security.py`

**TDD / work:**

- 从 hostile bytes 开始覆盖 header/chunk length/count/EOF、format、PPQN、track/EOT、VLQ、running status、
  data bytes、meta lengths 与 event/tick/text/note limits。
- exact event state machine 配对 Note On/Off，证明 format0/1、running status 与 velocity-zero off parity；
  任意失败在 adapter/pipeline/LLM 前结束。
- 至少解析出一条正 duration note。相同 tick 的 off/on 先按最终半开区间分组配对，合法交接不因 raw
  event 顺序被误判为 overlap；同 tick 新开又关闭的零 duration note 仍拒绝。
- tempo 先用 exact 24-bit microseconds-per-quarter 做非零与 1..1000 BPM 有理数边界判断，最后才转
  float；不能先舍入成边界内值。
- 覆盖多 stream、polyphony、percussion、tempo/time/key change、pitch bend、sustain/control、SysEx 与未知
  events 的 typed location（track/channel/tick/event index）。
- Hypothesis arbitrary bytes 只返回 typed failure/success，不泄漏 IndexError/OverflowError/UnicodeError。
- 所有 success 都满足 exact tick timeline、stable order、diagnostic cap 与 raw source hash。

## 6. Task 3 — Canonical adapter 与 public importer

**状态：完成；package audit 与 clean-install matrix 已通过。**

**Files:**

- `src/fretsure/importers/_music21_midi_adapter.py`
- `src/fretsure/importers/midi.py`
- `src/fretsure/importers/score.py`
- `src/fretsure/importers/__init__.py`
- importer adapter/path/bytes tests

**TDD / work:**

- raw preflight 零 error 后重建单 track/single-channel canonical format-0 MIDI，只含 tempo/time/key、notes、
  EOT；spy test 证明 raw text/controllers/program/SysEx/额外 metadata 不进入 music21。
- 使用 `music21.midi.translate.midiStringToStream(..., quantizePost=False)` 交叉验证 note count/pitch/
  exact offset/duration；preflight exact events 覆盖最终 MusicIR，adapter disagreement typed fail-closed。
- music21 缺失依赖与 parser exception typed/redacted；raw parser 永不直接调用 music21。已知 music21 会接受
  trailing bytes、TPQ=0、缺 EOT、超长 VLQ/孤立 notes，因此这些必须有 first-party 回归。
- public `import_midi(_bytes)` 与 generic `import_score(_bytes)`；MusicXML public API/alias 保持兼容。
- filename/path 使用现有 regular-file/race/size/inert-basename 安全合同；`.mid`/`.midi` path/bytes parity。
- `ImportProvenance.source_format="midi"`，raw/root SHA 相同、root member/container version 为 `None`；
  location 增加 exact int：track/event index 为 0-based，channel 为用户态 1..16，tick 为绝对 0-based。
- 显式 key 对全部 `sf=-7..7 × mi=0/1` 使用与 MusicXML major/minor 相同的 `Meta.key` 字符串；缺失只用
  精确 sentinel `key-signature:unprovided`。

## 7. Task 4 — Product vertical slice 与动态 stamps

**状态：完成；offline/proxy/Web/consumer gates 已通过。**

**Files:**

- application service/contracts/serializers
- API envelope/OpenAPI/tests
- CLI/tests
- Web types/api/App/tests/static build
- trace/fidelity/pipeline/benchmark tests

**Acceptance:**

- application/CLI 改用 generic score dispatcher；错误文案不再把 MIDI 说成 MusicXML。
- capabilities 用 `score-input@0.1.0` + format→importer registry，不再以单一
  `musicxml@0.3.0` 代表全部输入；每个 arrangement response 的 importer stamp 必须与 source format 精确
  匹配。每个 arrangement stamps 与 CLI 同时绑定 `score-input@0.1.0` router version。
- API 在读 body/初始化 LLM 前验证 `.mid/.midi`、canonical `audio/midi` 与 10 MiB；真实 raw-body hash 与
  importer provenance 一致。
- Web 从 capabilities 接受 MIDI suffix，`File` body hash 不变；仍用现有上传卡。hero/label/copy 改为
  “supported symbolic score / MusicXML, MXL, MIDI”，不加入轨道映射、播放时间线或新控件。
- MIDI result 显示实际 `midi@0.1.0`、loss warnings、unprovided key 与 fidelity N/A dimensions；不得显示
  推断 chord/key/bass，也不得把 N/A 渲染成 100%。状态文案写“available fidelity passed (1/3)”一类
  evidence-qualified 表达，不使用裸 “PASS” 暗示三个维度都有真值。
- CLI、application、HTTP、Web 对同一 frozen artifact 绑定相同 IR/source hash/importer；LLM prompt
  只接收同一 canonical MusicIR，不携带 raw source identity。offline 双跑 result/trace 相等，真实
  `gpt-5.6-sol` 至少跑一个 exact MIDI artifact。
- trace winner 事件删除含糊 legacy 字段名，内部选择分数只可用 `ranking_*`；authoritative nullable
  scores/dimensions/passed 与 response 逐值相等。跨格式回归先断言 MusicXML/MIDI melody timeline 相等，
  再逐项证明下游差异来自公开 source metadata/evidence availability，不能伪造 chord evidence。
- OpenAPI 对 faithfulness 冻结 exact nullable scores、两个 dimension arrays、passed 与 checker version，
  不继续使用无约束 object。

## 8. Task 5 — Docs、distribution 与停止线

**状态：完成；distribution/clean-install 已通过，Git identity 由 containing commit 的外部 receipt 记录。**

- 更新 README、CLAUDE、SCOPE、PROJECT_STATE、WEB_API_MCP、DEMO、design/roadmap；只声明 exact
  producer artifacts 与冻结 SMF 子集，不声明任意 `.mid`、通用多轨 MIDI 或完整 MIDI 1.0。
- 新建 `docs/MIDI_ACCEPTANCE.md`；记录 before/after census、资源门、fidelity availability、产品纵切、
  独立 reviews、诚实限制与最终 SHA。
- wheel runtime-only；sdist 必须包含 MIDI plan/acceptance/experiment/generator/manifest/source/evidence。
- clean install matrix：core、`[musicxml]`、`[midi]`、`[score]`、`[service,score,agent]`、`[mcp]`；core
  不意外拉 optional dependencies。

## 9. Final gates

> **软件 gates 全部完成。** 实际命令、counts、review 结论与 Git receipt 规则见
> [`MIDI_ACCEPTANCE.md`](../../MIDI_ACCEPTANCE.md)，没有从历史阶段数字推断。

1. MIDI producer manifest/census/semantic digest 与 strict parser/adapter/security/property tests。
2. 全量 Python offline + 全部 real proxy integrations；Ruff、strict mypy、lock、diff、Markdown links。
3. Web clean npm install、tests、typecheck/build/audit；generated static 与 source 无漂移。
4. frozen MIDI 的 importer/application/CLI/HTTP/Web/LLM prompt 与 fidelity N/A 实际行为。
5. final wheel/sdist audit 与全部 clean-install smoke。
6. 独立 scope/security/release review 均为 0 blocker / 0 important / 0 minor。
7. 软件树已准备为一个不带 AI coauthor trailer 的可审查提交；push
   `origin/codex/sequential-plans` 并确认 local/tracking/remote SHA 一致的外部 receipt 成功后，才把
   benchmark v2 设为下一阶段。

## 10. 真人与前端 gate

本阶段没有听感、演奏或 calibration 依赖。首版复用现有上传区和 evidence card，只增加格式能力、N/A
状态与准确文案，不构成新视觉方案。若实现需要多轨角色映射、drop-D/capo 新控件、播放时间线或任何新
页面/可视化，必须暂停并先请用户确认；不得自行扩展。
