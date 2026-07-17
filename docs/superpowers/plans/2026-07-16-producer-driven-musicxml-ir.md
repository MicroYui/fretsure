# Producer-driven MusicXML / IR — MuseScore mode-loss compatibility

> **状态（2026-07-17）**：**DONE**。前置 Plan 6A 提交 `b27a798` 已核对；本计划的 corpus、
> importer、产品纵切、分发包与独立终审门见 `docs/PRODUCER_MUSICXML_ACCEPTANCE.md`。
> 本计划提交推送并核对 local/remote SHA 后才允许开启 MIDI；它不代表“完整 MusicXML 支持”。

**Goal:** 只按已冻结的真实 producer failure distribution 扩大 MusicXML 保证路径：在不猜调性、
不放宽安全 envelope、不中途制造部分 IR 的前提下，接受 MusicXML 4.0 合法但未提供 `<mode>` 的传统
调号，并确保 importer 的每个 `ImportSuccess.ir` 都满足 Oracle 0.2 的完整 MusicIR 资源合同。

**Architecture:** raw XML preflight 继续是语义真源；零 error 后才重建 bounded event-only XML，让
music21 只对 divisions、harmony 与 note/rest/tie 逐事件交叉验证，credit、instrument/MIDI、visual、
lyrics/voice、key metadata 和合法额外 non-note-bearing part 不跨第三方边界。缺失 mode 不推断
major/minor，而是写入现有 textual `Meta.key` 的冻结 loss-aware descriptor，并发出定位 warning；
solver/oracle/fidelity 不解析该字符串。IR dataclass 与 notegraph shape 不扩字段，避免为一个展示上下文
过度设计新的调性系统。Importer 在 success 边界调用 strict `snapshot_music_ir`，并把 XSD 词法门、派生
Fraction 与诊断放大门同 256-bit/固定数量资源合同对齐。

## 1. 冻结的实证分布与停止线

### 1.1 分母

本轮 producer census 是以下可复核集合，不把下载量、品牌或单文件结果伪装成总体兼容率：

1. 既有 3 个原样 producer artifacts：music21 10.5.0、musicxml 1.6.1、MuseScore Studio 4.7.4。
2. 把当前 6 个已支持 golden/metamorphic MusicXML fixtures 原样交给本机
   `/opt/homebrew/bin/mscore`（`MuseScore4 4.7.4`）导出为 MusicXML 的 round-trip census。
3. 至少 1 个由同一 MuseScore 版本原样导出的 `.mxl`，用来区分 root semantic compatibility 与
   container compatibility。

导出物必须原样冻结；manifest 记录 producer/version/class、source artifact/hash、output hash、格式、
许可证、export exit code、expected result/warnings 与 provenance-free semantic digest。生成脚本只写新的
untracked 目录，不覆盖 frozen corpus。

### 1.2 观察结果（implementation 前）

- 既有 exact producer artifacts：2/3 success；library/toolkit 2/2；notation application 0/1。
- 6 个 MuseScore round-trip：5/6 只因 `<key><fifths>…</fifths></key>` 没有 `<mode>` 返回
  `UNSUPPORTED_KEY`；显式 C minor 样例 1/6 success。
- 对上述 5 个输出仅作**内存诊断替换**、补入 `<mode>major</mode>` 后，notes/chords/meter/tempo/
  duration/title/license 全与 source IR 一致；这只定位 failure bucket，不是允许推断 major 的理由。
- 已复现第二个边界：小于旧 128-char 门的 decimal 可构造 333-bit Fraction，使 importer 返回
  success、但 `snapshot_music_ir` 随后拒绝。

实验命令、逐文件 hash、before/after 表和环境信息写入
`docs/experiments/2026-07-16-producer-musicxml-census.json`；最终验收摘要写入
`docs/PRODUCER_MUSICXML_ACCEPTANCE.md`。

### 1.3 只扩这一条 sounding semantic

- MusicXML `version="4.0"` 中，`<fifths>` 是 `-7..7` 的传统调号且 `<mode>` **元素完全缺失**：success，
  `Meta.key = "key-signature:fifths=N;mode=unprovided"`，并发一个
  `KEY_MODE_UNPROVIDED` warning，location 精确到 part/measure/key。
- MusicXML `version="3.1"` 保持既有成功域：只有显式 `major` / `minor` 成功，缺失 `<mode>` 继续
  `UNSUPPORTED_KEY`；本阶段不把 4.0 producer 兼容性扩展外推到其他版本。
- 显式 `major` / `minor`：保持既有 `C` / `Am` 等 Meta.key 与 zero-warning 语义。
- 空 `<mode/>`、显式 dorian/phrygian/none 等其他 mode、越界/non-integer fifths、non-traditional key：
  继续 `UNSUPPORTED_KEY`。
- 单 staff 契约下每个 `<key>` 必须恰有一个 `<fifths>`、至多一个 `<mode>`，每 measure 至多一个 key；
  duplicate/conflicting child 或重复 key 返回 `UNSUPPORTED_KEY`，不能由 `find()` 静默取第一项。
  `musicxml@0.2.0` 曾意外接受同 measure 的重复显式 key；0.3.0 把这一歧义输入收紧为 typed failure，
  属于与成功域扩展一起发布的明确 breaking safety tightening，不把它误写成旧行为不变。
- 单 staff 契约下 `<key number>` 只允许缺省或 canonical integer `1`；`2`、零/负数、空值、非整数及
  非 canonical 的 `01`/`+1` 等都在 music21 前返回 `UNSUPPORTED_KEY`。这是 0.3.0 的另一项明确
  safety tightening，不能把其他 staff 的 key 静默当作全局 key。
- 完整 raw tree 还会在 adapter 前拒绝重复权威 scalar、外部 image/resource/`xlink:href`、权威语义字段
  中的非 ASCII/XSD 小整数、非 XML Unicode whitespace 数值、错误 key child 顺序/属性/混合文本、
  过长 location 与诊断
  flood。visual metadata 按既有 loss policy 丢弃后不再由 music21 的偶然解析失败决定结果；这可能扩大
  byte-level 成功集合，但不扩大 sounding-semantic 合同。
- 缺整个 `<key>`：继续 `MISSING_KEY`。
- unknown↔explicit、major↔minor 或 fifths 变化：继续 `KEY_CHANGE_UNSUPPORTED`。
- descriptor 不从 harmony、首音、note spelling 或 music21 推断；下游不得解析它恢复 tonic。

MusicXML 4.0 的 `<key>` 参考明确把 `<fifths>` 设为 required、`<mode>` 设为 optional：
<https://www.w3.org/2021/06/musicxml40/musicxml-reference/elements/key/>。Optional 不等于默认 major；
本计划保存“未提供”事实。

### 1.4 明确延后

- 多 note-bearing part/staff/voice、polyphony/`<chord/>`、声部分离与角色推断。
- 非 4/4、pickup、key/time/tempo change、repeat/navigation。
- slash bass、N.C.、degree alteration、非零 harmony offset、复杂/其他 mode 调性分析。
- MIDI、benchmark v2、音频、AlphaTab、导出与 Plan 6B。
- proprietary/GUI-only producer 未取得的 artifact；不能用手写或改写 XML 替代。

## 2. 版本裁决

- package：`0.3.0 → 0.4.0`，因为公开成功输入域扩大且本计划独立闭门。
- importer：`musicxml@0.2.0 → musicxml@0.3.0`。
- fixture manifest：`fretsure-producer-fixtures@0.2.0 → @0.3.0`。
- `music21` runtime 收窄为已验证的 exact `10.5.0`；在新的 compatibility matrix 闭门前，不允许
  未验证的 10.x 在相同 importer stamp 下改变 chord/metadata 语义。
- 保持：`mxl-container@0.1.0`、`oracle@0.2.0`、`tab-input@0.2.0`、
  `fidelity@0.2.0`、`agent-trace@0.1.0`、`target-input@0.1.0`、`median@0.1`。
- service/API/MCP/Web 顶层 wire shape 不变，版本保持 0.1.0；score-input capabilities 与 arrangement
  result stamps 增加并强制 `importer_version=musicxml@0.3.0`。纯 Tab check/solve/render 没有经过
  importer，不盖不相关版本且保持原 shape。若实现中不得不改变其他公开 shape，则先修订本计划并升
  对应版本。
- MusicIR/benchmark notegraph shape 不变，本轮不凭空引入 IR schema；benchmark v2 必须单独冻结
  notegraph/corpus/report schema。producer semantic digest 包含现有 Meta/notes/chords，但排除文件路径和
  provenance。
- standalone JSONL trace 继续只证明 pipeline/checker/model 过程；source/importer/hash 由同一 CLI stdout
  或 API wrapper 绑定。本轮不把它冒充独立 source provenance，也不为此扩大 trace schema。

## 3. Task 1 — Corpus、manifest 与 before census

**Files:**

- `scripts/generate_producer_fixtures.py`
- `tests/fixtures/producers/**`
- `tests/importers/test_producer_fixtures.py`
- `docs/experiments/2026-07-16-producer-musicxml-census.json`

**TDD / work:**

- manifest exact schema/required fields/types；filename 与 SHA unique；manifest↔目录双向一一对应；禁止
  未登记 artifact。
- 冻结 6 个 MuseScore round-trip `.musicxml` 和至少 1 个 `.mxl`；原样 exporter identity、raw/root
  hash、root member、source hash 与 license 可复核。
- 每个 success artifact 双跑 dataclass identical；semantic digest 冻结 title/key/meter/tempo/duration/
  notes/chords/warning codes，不含 filename/raw provenance。
- 每个带 `source_file` 的 artifact 做 source→output differential：显式 minor 除 provenance 外完全相等；
  omitted-mode 只允许 key 变为 descriptor、warning 增加，其余 title/license/meter/tempo/duration/notes/
  chords 必须相等。Manifest 自填 semantic 不能替代这条因果门。
- generator 只接受 exact producer versions，fresh output only；MuseScore teardown 非零只能在完整 XML/
  ZIP、准确 exporter marker 与记录 exit code均成立时作为 artifact，不能泛化吞错。

## 4. Task 2 — Decimal / MusicIR success boundary

**Files:**

- `src/fretsure/importers/contracts.py`
- `src/fretsure/importers/_musicxml_preflight.py`
- `src/fretsure/importers/musicxml.py`
- `tests/importers/test_musicxml_preflight.py`
- `tests/importers/test_musicxml_bytes.py`

**Contract:**

- MusicXML decimal limit 与 `MAX_IR_FRACTION_COMPONENT_BITS=256` 对齐；默认/自定义 limit 不允许构造
  超出 public IR 的 numerator/denominator。词法超限在 music21 前返回 `INPUT_LIMIT_EXCEEDED`。
- adapter 后调用 `snapshot_music_ir`，任何不一致在 importer 内变成 typed `IR_INVALID`，不先返回
  success、再由 pipeline 变成泛化 502。
- property/metamorphic gate：对所有 importer success，`snapshot_music_ir(ir) == ir`；numerator 与
  denominator 分别证明 256-bit 边界可接收、257-bit 拒绝。denominator 用精确 decimal
  `1 + 1/2**scale`（同时缩放 divisions/durations 保持 ratio）覆盖，拒绝路径不得调用 music21、
  arranger 或 LLM。

## 5. Task 3 — Loss-aware optional mode

**Files:**

- `src/fretsure/importers/contracts.py`
- `src/fretsure/importers/_musicxml_preflight.py`
- `src/fretsure/importers/musicxml.py`
- `src/fretsure/importers/_music21_adapter.py`
- importer golden/metamorphic/producer tests

**TDD / work:**

- 先写 MusicXML 4.0 omitted mode 的 fifths `-7, 0, 7` golden；descriptor 精确保留 fifths，warning
  位置准确；3.1 的相同输入在 path/bytes 入口都保持 `UNSUPPORTED_KEY`。
- 改变首个 harmony/旋律但保持调号时 descriptor 不变，证明没有内容推断。
- 结构合法、单一的显式 major/minor 旧 IR 完全相同；empty/unsupported mode 和 mid-score identity
  transitions 继续 typed fail-closed；旧版偶然接受的重复 key/child 由本版明确收紧。
- omitted/explicit mode 都覆盖 `<key number>` gate：仅缺省或精确 `1` 成功，其他值在 music21 前
  `UNSUPPORTED_KEY`。
- path/bytes、`.musicxml/.xml`、synthetic MXL 与真实 MuseScore MXL parity；只允许 provenance 不同。
- `IMPORTER_VERSION`、Meta.source importer field、API source/stamps、CLI output 同步 0.3.0；container
  version保持 0.1.0。

## 6. Task 4 — 产品纵切与公开陈述

**Files:**

- application serializers/capabilities、API tests、CLI tests、Web runtime fixtures/tests
- `README.md`, `CLAUDE.md`, `docs/SCOPE.md`, `docs/PROJECT_STATE.md`, `docs/WEB_API_MCP.md`,
  `docs/DEMO_SCRIPT.md`, design/roadmap current-status sections

**Acceptance:**

- exact MuseScore 4.7.4 `.musicxml` 与 `.mxl` 分别跑通 importer → service → CLI/API/Web upload；UI/CLI/
  LLM prompt 显示 `mode=unprovided`，不能显示推断的 C/Am。
- offline deterministic result/trace 双跑相等；真实 `gpt-5.6-sol` 至少跑一个该 producer artifact，并
  盖实际 model/importer/checker/profile stamps。
- 只声明“冻结的 MuseScore Studio 4.7.4 artifacts 兼容”；不声明 MuseScore 全版本、任意谱或完整
  MusicXML 兼容。
- 路线图修正过期的 Plan1→2 MIDI/“下一步 Plan1”文字；明确自动 benchmark v2 与未来真人金标分离。
- 历史 pre-Plan6/safe-MXL/Plan6A/legacy benchmark 数字保持历史，不全局改写。

## 7. Final gates

1. producer manifest/census/semantic digests；所有 MusicXML/MXL/IR safety tests。
2. 全量 Python：offline + 7 real proxy integrations（含 1 个 exact MuseScore producer artifact）；ruff；
   strict mypy；lock；diff；Markdown links。
3. 前端：clean npm install、20+ tests、typecheck/build/audit；generated static 与 source 无漂移。
4. 真实行为：producer XML/MXL CLI、application、HTTP raw-body；warning/plain-text；真实 proxy。
5. package：final wheel/sdist audit；clean core/musicxml/service/mcp installs；musicxml/service smoke 使用本轮
   producer positive；core 不拉 adapter/musicxml dependencies。
6. 独立 scope/security/consumer review 无 blocker；所有发现写回验收/实验记录。
7. 一个可审查提交，不带 AI coauthor trailer；DONE 状态随最终提交记录，push
   `origin/codex/sequential-plans` 并确认 local/remote SHA 一致后才开启 MIDI。

## 8. 真人 gate

本计划没有审美、听感或人体 calibration 依赖，不需要用户暂停审计。它不改变真人 gold、现实 GREEN
误接受率、profile/tier 或“真实琴手一定能弹”的 open 状态。
