# Producer-driven MusicXML / IR — 验收记录

> **状态（2026-07-17）**：**DONE**。实现、全量/真实代理/Web/分发门与三路独立终审均已关闭；本文
> 与实现将进入同一提交，提交并推送、核对 local/remote SHA 后才能开启 MIDI。

## 1. 冻结范围与版本

- 前置 Plan 6A 提交：`b27a798ac2a6c088bcc6bfd9b3ebfc9689498c6c`，执行本阶段前 local/remote
  SHA 一致。
- package：`0.4.0`；importer：`musicxml@0.3.0`；producer manifest：
  `fretsure-producer-fixtures@0.3.0`。
- `mxl-container@0.1.0`、`oracle@0.2.0`、`tab-input@0.2.0`、`fidelity@0.2.0`、
  service/API/MCP/Web/trace 等其余公开合同不改义；MusicXML runtime 精确锁定
  `music21==10.5.0`。
- 唯一的 sounding-semantic 成功域扩展是 MusicXML 4.0 traditional `<key>` 中元素完全缺失的
  `<mode>`。输出保存
  `key-signature:fifths=N;mode=unprovided` 并发 located `KEY_MODE_UNPROVIDED`；不推断 major/minor。
- MusicXML 3.1 省略 mode、空/其他 mode、key change 与其余 deferred sounding semantics 继续 typed
  fail-closed。0.3 同批 safety tightening 还拒绝重复权威 scalar、权威语义字段中的非 ASCII/XSD
  小整数、key 的错误
  child 顺序/属性/混合文本/重复声明、非 canonical staff selector，以及所有外部 image/resource/
  `xlink:href`；这些都在 music21 前返回 typed failure。
- 完整 raw tree 的资源与语义预检是权威。只有零 error 后，importer 才重建一个固定单 part、顺序 measure
  的 bounded event-only XML，向 music21 只传 divisions、harmony root/kind、note/rest/duration/tie。credit、
  instrument/MIDI、layout/print、lyrics/voice、key visual metadata 与合法额外 non-note-bearing part 均不进入
  第三方 parser；raw warning 和 Meta metadata 仍由 preflight 保留。因此，旧版由 music21 偶然拒绝的非声音
  visual 值可能按既有 loss policy 成功，这不是额外的 sounding-semantic 扩张。

## 2. Producer census 与可复核证据

- [`tests/fixtures/producers/provenance.json`](../tests/fixtures/producers/provenance.json) 双向绑定 10 个
  原样 artifact：旧 3 个 immutable evidence、6 个 MuseScore Studio 4.7.4 XML round-trip、1 个真实
  MuseScore MXL；manifest SHA-256 为
  `ef550ee80c6e7cd9d33eef4f4b66b07dd8798d6719173ddf938c283afce91f2d`。
- [`scripts/replay_producer_census.py`](../scripts/replay_producer_census.py) 对旧提交实际回放得到
  package `0.3.0` / importer `musicxml@0.2.0`：`3/10 success, 7/10 failure`；当前树回放得到
  package `0.4.0` / importer `musicxml@0.3.0`：`10/10 success, 0 failure`。
- MuseScore 子集由 `1/8 → 8/8`；7 个 omitted-mode artifact 全部带
  `KEY_MODE_UNPROVIDED`，显式 minor 与两份旧显式-mode producer artifact 零 warning。
- 每个 source-bound round-trip 都做 source→output differential。omitted-mode 只允许 key descriptor 与
  warning 变化；显式 minor 必须逐字段相等；title/license/meter/tempo/duration/notes/chords 不得漂移。
- [`scripts/generate_producer_fixtures.py`](../scripts/generate_producer_fixtures.py) 不再重新生成含日期的旧
  music21 artifact：旧 3 个只在 byte/hash/source/semantic/manifest 全部匹配后逐字节复制，只重新导出
  本轮 6 XML + 1 MXL。每次 fresh export 都保留 producer 原始字节，不删除或归一化元数据。2026-07-17
  跨日重放确认 7/7 semantic hash 稳定，但 6/6 XML raw/root 与 MXL root 都因
  `<encoding-date>`/`creationDate` 改为当天而变化，MXL raw 另含 ZIP timestamp 变化；因此这些 raw/root
  只作 exact frozen evidence，不冒充 reproducible build。同日曾观测到的 raw/root 相等不外推到跨日。
- 完整逐文件 before/after、hash、环境、命令与诚实限制见
  [`docs/experiments/2026-07-16-producer-musicxml-census.json`](experiments/2026-07-16-producer-musicxml-census.json)。

## 3. MusicIR 资源与语义门

- 树级门限制 element/depth、数值 token 与 location scalar；只按 XML whitespace 折叠 XSD 数值，NBSP 等
  Unicode 空白不能被 Python broad strip 清洗后接受。权威 note/pitch/harmony/tempo/divisions/time scalar
  重复即失败；small-int 使用有构造上限的 ASCII lexer，不先构造攻击者控制的大整数。
- decimal 先按 XSD 词法门，再验证 reduced numerator/denominator 各自不超过 256 bit；随后独立检查
  cursor/division 派生出的 note onset/duration、tie 合并 duration、chord onset 与总 duration。raw 或
  派生的 257/333-bit 组件都在 music21、arranger、LLM 前返回 `INPUT_LIMIT_EXCEEDED`。
- numerator 与 denominator 都有 256-bit 可接受 / 257-bit 拒绝边界；denominator 正例使用
  `1 + 1/2**255`，避免为非 producer 的浮点下溢重写 adapter。
- importer 0.2 的真实逃逸输入（`D=2**332`，divisions=`D`，duration=
  `[1,D,D,2D-1]`）已复现为先 `ImportSuccess`、后 MusicIR snapshot 失败；0.3 在 music21 前拒绝。
- 每个 importer success 在返回前必须满足 `snapshot_music_ir(ir) == ir`；adapter 的越界结果转为 typed
  `IR_INVALID`，不把非法 IR 泄漏到 service。
- raw preflight 普通诊断最多保留 256 条并追加一个 truncation sentinel；adapter 后 `validate_ir` 同样在
  256/257 精确边界封顶。异常只跨边界暴露固定类型名，不泄漏 parser/provider payload；warning/location
  flood 不能变成无界返回。
- omitted-mode 对 fifths `-7..7`、path/bytes、`.musicxml/.xml`、synthetic/real MXL 都有回归；改变旋律或
  harmony 不改变 descriptor，证明没有内容推断。

## 4. 产品纵切

- exact MuseScore XML/MXL 分别通过 importer、application、CLI、raw-body HTTP 与 Web client `File`
  边界；application/HTTP/CLI 双跑结果与 trace 相等。
- Web 测试从冻结 artifact 读取真实 bytes，先核 exact SHA-256，再构造浏览器 `File`，并在 fetch body
  复算同一 hash；组件显示 descriptor、`KEY_MODE_UNPROVIDED` 与 `musicxml@0.3.0`，不显示推断的 C/Am。
- 本地打包页面在真实浏览器中加载 `0.4.0` capabilities 与 `musicxml@0.3.0` stamps。Chrome 扩展未启用
  “Allow access to file URLs”，因此该外部自动化工具不能调用 `setFiles`；这不被写成产品成功证据，
  字节传输由上述 Web exact-File gate 与真实 HTTP raw-body gate分别证明。
- CLI stdout 与 captured LLM prompt 都包含 `mode=unprovided`，且排除 C/Am；真实代理门在第 5 节记录。

## 5. 最终质量门

以下数字在最终运行后写入，不沿用 Plan 6A 的历史数字：

- Python offline：`1683 passed, 7 deselected`；唯一 warning 是已知 Starlette/httpx2 deprecation。
- 真实本地 `gpt-5.6-sol` integrations：最终整组 `7 passed, 1683 deselected`；唯一 warning 同上。此前
  一次整组曾有 `6 passed, 1` 代理瞬时失败，失败项定向通过后仍完整重跑，未以定向重试替代整组门。
- Ruff / strict mypy / `uv lock --check` / `git diff --check` 全绿；Markdown local links `29 files`。
- Web clean `npm ci` / tests / typecheck / build / audit：`24 passed`，静态 bundle
  `index-CUAmOTuY.js`，`0 vulnerabilities`。
- importer 专项：`536 passed`；producer manifest：`11 passed`；最终 census：`10/10 success`。

## 6. 分发包与 clean-install

- 最终树重建 `fretsure_oracle-0.4.0` wheel/sdist；audit 为 wheel `88` entries、sdist `239` entries。
  wheel 保持 runtime-only；sdist 包含本计划、本验收、实验 JSON、replay/generator、manifest 与全部冻结
  MuseScore XML/MXL evidence。
- clean core、`[musicxml]`、`[service,musicxml,agent]`、`[mcp]` 四组合全部通过；core 确认
  FastAPI/MCP/music21/defusedxml/Anthropic 均未被意外拉入。

## 7. 独立终审与结论

- scope review：实现 `0 blocker / 0 unrelated narrowing`；唯一 important 是把最终 projection/safety
  合同和“唯一 sounding-semantic expansion”写入本文、实验、SCOPE/PROJECT_STATE/design，现已关闭。
- security/resource review：`0 blocker / 0 important / 2 minor`。两项只涉及受信维护脚本：fixture
  generator 子进程尚无 timeout/captured-output cap；census replay 自身依赖独立 manifest 测试，而不重复
  校验 schema/hash/basename。它们不在 runtime importer trust boundary，不扩本阶段设计处理。
- consumer/product-evidence review：`PASS，0 blocker / 0 important / 0 minor`；独立核对 importer →
  application → CLI →真实 HTTP raw body → Web `File` → LLM prompt，以及 capabilities/stamps/census/
  manifest/cross-day evidence。
- 只有所有 blocker 关闭、本文与计划状态更新为 DONE、单一提交推送且 local/remote SHA 一致后，才允许
  开启 MIDI；随后才是 benchmark v2。

## 8. 诚实边界

本阶段只证明 manifest 中精确冻结的 MuseScore Studio 4.7.4 artifacts 兼容，不证明任意 MuseScore
4.7.4 乐谱、其他版本或完整 MusicXML。它不扩展多 part/staff/voice、polyphony、复杂 harmony、导航、
pickup、变拍/变调/变速、MIDI 或音频，也不改变真人 gold/calibration 与现实 GREEN 误接受率的 open
状态。
