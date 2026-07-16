# Pre-Plan 6 — MusicXML 输入与真实文件纵切

> **状态（2026-07-16）**：**DONE。Tasks 1–6 均已闭合，并在 `musicxml@0.1.0` 独立提交快照验收。** 当前证据为离线 `516 passed, 6 deselected`、本地代理全量 `522 passed / 522 collected`，ruff、mypy(strict, 59 source files)、lock/diff check 全绿；构建/clean-venv/CLI smoke 见下方闭门证据。producer artifact gate 冻结两个未经手工修改的 library/toolkit 正例（music21 10.5.0、musicxml 1.6.1）与 MuseScore Studio 4.7.4 原样负例；这不等于已有常见制谱软件正兼容证据。
>
> 当前独立快照版本边界：package=`0.1.0`、playability=`oracle@0.1.0`、faithfulness=`fidelity@0.2.0`、importer=`musicxml@0.1.0`、profile=`median@0.1`。Oracle 0.2 是本提交之后的独立计划。GREEN 只认证所选 model/profile 内 playability；faithfulness 是独立门，两者都过才是联合成功。

**Goal:** 交付一条可重复、可诊断的真实输入路径：

`MusicXML 文件 → MusicIR → arranger/solver/oracle → ASCII + verdict + faithfulness + trace`

这条路径的 GREEN 仍只表示通过版本化 model/profile，不是现实琴手保证。

**Architecture:** Fretsure 先用受限 XML 解析器做安全 envelope 与原始能力预检，再把规范化 XML 文本交给 `music21` 做成熟的 MusicXML 语义解析，最后在一个窄 typed adapter 边界转换为本地 frozen `MusicIR`。不把不可信路径直接交给 `music21`；不让 importer 调 agent/oracle；应用层 pipeline 负责 IR 验证、tempo 透传、编配、checker、faithfulness、ASCII 与 trace。

## 1. 冻结的首期契约

### 1.1 支持

- MusicXML 3.1 / 4.0、`score-partwise`、未压缩 `.musicxml`（兼容同内容 `.xml`）。
- 单一 note-bearing part、单 staff、单 voice 的单音旋律；普通 pitched note、rest、跨小节 tie。
- 全曲固定的正 decimal `divisions` 与正 decimal `duration`；用长度有界的严格 XSD-decimal grammar 解析，onset/duration 精确归一为以四分音符为单位的 `Fraction`。任何 `divisions` 变化 typed fail-closed。raw note/harmony event timeline 是时间权威，music21 只校验 count/pitch/tie/symbol/root，不从其浮点 offset/quarterLength 回推。这遵循 MusicXML 4.0 `positive-divisions` 的 decimal 类型，并覆盖 `1.0` 与非平凡小数而不失真。
- 显式、全曲不变的 major/minor key；固定 4/4；单一、显式、1–1000 BPM 的 quarter-note tempo。
- 至少一个 `<harmony>`；支持常见 root + kind，归一为 `ChordSymbol(symbol, pitch_classes, root_pc)`。缺少 harmony 是 typed `MISSING_HARMONY`，因为首期契约是 lead sheet 而非裸旋律谱。
- MusicXML 标准外部 DOCTYPE 可存在，但实体声明/外部实体解析一律禁止；送入 `music21` 前重序列化为无 DTD/entity 的 canonical XML。
- 视觉 layout/beam/stem/notehead/credit 不进入 `MusicIR`，作为公开的非音乐语义忽略项；lyrics/slur/dynamics/articulation 等损失以 warning 暴露。
- importer 从完整 measure timeline 填写向后兼容的 `Meta.duration_beats: Fraction | None`；它保留 trailing rest，并为最后 chord segment 与按 chord root 派生 bass 提供权威曲尾。只有源 IR 完全没有显式 bass 时才合成最低可弹的 chord-root bass，并在 chord segment 内的 melody attacks 确定性重奏/切分 sustain；显式 bass 不合成、不加倍。旧六参数 `Meta` 与旧 note-graph 没有该字段时仍兼容。

### 1.2 延后且必须 typed 拒绝

- 压缩 `.mxl`（紧随本纵切实现安全 container reader，不能委托给 `music21` 的无上限 ZIP reader）。
- `score-timewise`、多 note-bearing part、多 staff、多 voice、记谱复调 `<chord/>`、自动旋律选择/声部分离。
- repeat/ending、D.C./D.S./segno/coda/fine、measure-repeat/multiple-rest。
- pickup/implicit 或不完整小节；key/time/tempo 中途变化。
- tuplet、grace、cue、unpitched/percussion、microtone、移调乐器、非常规 key。
- slash bass/inversion、N.C.、function/numeral harmony、degree alteration、非零 harmony offset 与首期白名单外的复杂和弦类型。
- ornament/tremolo/bend/glissando/fermata/harmonic/damp 等会改变 sounding/performance 语义的记号。
- MIDI 与 audio 输入（audio 始终是未来 best-effort 前端，不属于保证路径）。

所有延后项都返回稳定 code + part/measure/voice/element 位置；存在 ERROR 时不返回部分 IR，也不能继续出 GREEN。

### 1.3 资源与 provenance

- 输入 bytes、XML depth/element、measure、note、harmony 数量均有公开上限；超限是 `INPUT_LIMIT_EXCEEDED`，不是截断。
- `Meta.source` 记录文件名 + SHA-256 + importer version；title/rights 来自文件。缺 rights 诚实记为 `unprovided` 并 warning，绝不猜许可证。
- 成功结果必须满足 `validate_ir(ir) == []`、稳定排序、重复解析完全相等。
- `music21` 与 `defusedxml` 放入 `musicxml` optional extra；`dev` extra 同时安装。核心 oracle 不因缺 extra 而无法 import；输入 CLI 给可操作的 typed dependency error。
- importer 的默认资源 envelope 远大于真实 LLM 单请求容量。当前 16k 输出预算最多容纳约 **169 个 source note+chord events**；`--llm` 超限时在创建代理/发请求前显式抛 `ArrangementCapacityError`，输入不截断。长谱 LLM chunking 尚未实现；离线确定性路径仍可处理 importer envelope，文案不能建议不存在的 split 功能。
- 6 个 hand-authored fixture 负责 golden/metamorphic 语义；另有 `tests/fixtures/producers/` 的原样 exporter 输出和机器校验 manifest。正例只证明 music21 10.5.0 与 musicxml 1.6.1 两个 library/toolkit 的精确 artifact；MuseScore 4.7.4 当前是 notation-application 负例。provenance 已取得不代表常见制谱软件正兼容，后者保持 open。

## 2. 公共接口

```python
class ImportCode(StrEnum): ...
class DiagnosticSeverity(StrEnum): ERROR = "error"; WARNING = "warning"

@dataclass(frozen=True)
class SourceLocation:
    part_id: str | None = None
    measure: str | None = None
    voice: str | None = None
    element: str | None = None

@dataclass(frozen=True)
class ImportDiagnostic:
    code: ImportCode
    severity: DiagnosticSeverity
    message: str
    location: SourceLocation | None = None

@dataclass(frozen=True)
class ImportSuccess:
    ir: MusicIR
    warnings: tuple[ImportDiagnostic, ...]
    importer_version: str
    sha256: str

@dataclass(frozen=True)
class ImportFailure:
    diagnostics: tuple[ImportDiagnostic, ...]

MusicXMLImportResult = ImportSuccess | ImportFailure

def import_musicxml(path: Path, *, limits: ImportLimits = DEFAULT_LIMITS) -> MusicXMLImportResult
```

相关 IR 曲长增量（已实现且向后兼容）：

```python
@dataclass(frozen=True)
class Meta:
    key: str
    time_sig: tuple[int, int]
    tempo_bpm: float
    source: str
    title: str
    license: str
    duration_beats: Fraction | None = None
```

应用层：

```python
@dataclass(frozen=True)
class PipelineOptions:
    tuning: tuple[int, ...] = STANDARD_TUNING
    capo: int = 0
    profile: Profile = MEDIAN_HAND
    n: int = 4
    max_iters: int = 8
    use_critic: bool = True
    tempo_override_bpm: float | None = None

def run_pipeline(ir: MusicIR, llm: LLMClient, *, options: PipelineOptions) -> PipelineResult
```

source tempo 默认成为 solver/oracle 的 effective tempo；只有显式 override 才能覆盖。当前 4/4 契约使 oracle 的 `beats_per_bar=4` 定位仍正确。

## 3. TDD 执行任务

### Task 1 — typed contract、依赖与安全文件入口

**Files:**

- `src/fretsure/importers/contracts.py`
- `src/fretsure/importers/musicxml.py`
- `tests/importers/test_musicxml_io.py`
- `pyproject.toml`, `uv.lock`

**Tests first:** 文件不存在、错误后缀、`.mxl` 延后、超大文件、malformed XML、实体攻击、错误 root/version/namespace、缺 optional dependency；逐项断言稳定 code，CLI 无 traceback。

**Acceptance:** 不可信 bytes 不会触发网络/实体展开；ERROR 不产生部分 IR；core import 不依赖 music21。

### Task 2 — 原始 MusicXML 能力预检

**Files:**

- `src/fretsure/importers/_musicxml_preflight.py`
- `tests/importers/test_musicxml_preflight.py`

**Tests first:** 多 part/voice/staff、`<chord/>`、repeat/ending、navigation、tuplet/grace、transpose/microtone/unpitched、pickup、key/time/tempo change、slash/complex harmony；每个 feature 单独 fixture，断言 code + measure/element。

**Acceptance:** 所有会改变 pitch/onset/duration/chord/tempo/voice 的未支持语义在 `music21` 归一化前被发现；warnings 不静默。

### Task 3 — music21 → MusicIR typed adapter

**Files:**

- `src/fretsure/importers/_music21_adapter.py`
- `tests/importers/test_musicxml_adapter.py`
- `tests/fixtures/musicxml/*.musicxml`

**Tests first:** major/minor、升降号、rest gap、tie start/continue/stop、跨小节 tie、常见 harmony、稳定排序、精确 `Fraction`、title/rights/source hash；成功 fixture 全部 `validate_ir == []`。

**Metamorphic:** divisions 等比例变化 IR 不变；长音与等价 tied fragments IR 不变；只增 layout 标签 IR 不变；同 bytes 两次结果相等；notegraph JSON round-trip 恒等。

**Acceptance:** importer 不伪造 bass/harmony notes；MusicXML melody 只映射为 `melody`，`<harmony>` 只映射为 `ChordSymbol`。

### Task 4 — 产品 pipeline 与外部文件 CLI

**Files:**

- `src/fretsure/pipeline.py`
- `src/fretsure/cli.py`
- `src/fretsure/demo.py`
- `src/fretsure/agent/arranger.py`
- `src/fretsure/arrange/propose.py`
- `tests/test_pipeline.py`
- `tests/test_cli.py`

**Required fixes:**

- source tempo 必须实际传到 `ArrangeGoal`、solver 和 oracle；输出同时显示 source/effective tempo。
- LLM prompt 包含每个 source note/chord event，note 带 onset + duration + pitch，禁止原先 64-note/32-chord 静默截断。importer 负责文件资源 envelope；真实 LLM 的更小单请求容量由 `ArrangementCapacityError` 在请求前显式拒绝，二者不能混为同一个上限。
- importer 不伪造编配。确定性 fallback 在“源 IR 没有 bass”时，从 chord root 派生吉他音域内的 bass target；已有显式 bass 时不重复。
- `fretsure-arrange FILE.musicxml [--llm] [--trace-jsonl PATH]` 输出 parse warnings、IR 摘要、ASCII、oracle verdict/version、独立 faithfulness gate 与 trace；非法输入非零退出。

**Acceptance:** 同一 fixture 离线跑两次输出一致；GREEN 才可显示 model-relative certification；AMBER/RED 不冒充认证。

**当前实现注**：prompt 不再截断，输出预算按事件数在 2k–16k 间增长；约 169 events 以上的真实 LLM 单请求明确失败并指向确定性路径，chunking 延后。LLM 返回的负 onset、非正 duration、非整数/越界 MIDI、重复 onset+pitch 均被拒绝后诚实 fallback。

### Task 5 — faithfulness 语义修正与版本化

**Files:**

- `src/fretsure/metrics/fidelity.py`
- `src/fretsure/bench/runner.py`
- 对应 metrics/bench tests 与结果文档

历史问题是 `harmony_jaccard` 只看 `ir.notes`，对“melody + `<harmony>`”会评错。现已在 `fidelity@0.2.0` 中改为按 `ChordSymbol` 的 chord segment 计算 source chord pitch-class 与输出 pitch-class Jaccard；持续音计入实际覆盖的每段，最后段使用 `Meta.duration_beats`。无 chord annotation 时保留清晰定义的旧 onset fallback；bass-root 也已改为读取 chord onset 当时正在发声的最低音。

**Acceptance（已满足；benchmark v2 是后续独立计划）:** MusicXML lead sheet 的 harmony 分数实际来自 `<harmony>`。2026-07-10/11 的真实 LLM 表固定在 `oracle@0.1.0` + 旧的、未版本化 note-onset harmony metric，只能标为 **legacy/unversioned fidelity snapshot**；当前命令会按 `fidelity@0.2.0` 新语义重算，只能复现实验形状，不能声称复现旧评分。benchmark v2 仍须重跑，但不属于本 importer 纵切的 closure gate。

### Task 6 — 文档、真实 producer fixtures 与全量门

- README 给出从安装 extra 到一条命令的真实文件示例。
- 更新 `PROJECT_STATE`、scope、demo script 和支持矩阵；精确列出延后项。
- 加至少两种常见 producer 的原创/可再分发短 fixture；记录 exporter/version/hash/license。若当前无法可靠取得，只能把该验收保持 open，不能用手写 XML 冒充 producer export。
- 本条冻结的是**原样 artifact + 可审计 provenance + 实际成功/失败结果**，不要求用猜测或手改把每个 producer 变成正例。当前由常用 symbolic-music toolkit `music21` 与主流 notation application MuseScore（负例）满足“两种 producer”取证，另加独立 `musicxml` library 正例；notation application 的正兼容仍是后续 open 项。
- 运行 importer/pipeline 定向测试、全量离线 pytest、ruff、mypy(strict)、wheel 安装 smoke、无 `musicxml` extra 的 core import smoke、CLI 真实实跑、`git diff --check`。

**闭门证据（2026-07-16）**：

- 6 个 hand-authored golden/metamorphic fixture 加 3 个 unedited producer fixture；producer manifest 逐文件冻结 exporter、version、SHA-256、producer license、score license 与预期结果。
- music21 10.5.0（BSD-3-Clause）与 musicxml 1.6.1（MIT）原样 library/toolkit 输出均成功且重复解析相等；后者的 decimal divisions/duration 验证 exact `Fraction` 合同。生成脚本只写 fresh untracked 目录，拒绝覆盖冻结 corpus；固定 producer part id 后双跑 byte-identical。
- MuseScore Studio 4.7.4 的原样输出现在可重复取得；它省略 `<mode>`，而 MusicXML 的 key mode 是可选且缺失时无法从 key signature 无歧义恢复，所以 importer 稳定返回 `UNSUPPORTED_KEY`。该负例限制兼容性声明，不做猜测、不手改导出物。
- hostile timeline 回归覆盖严格 decimal 词法/长度、十亿级 exponent、巨大拍号 token、mid-measure divisions change、非平凡小数精确值、music21 语义分歧、standalone tempo change、stacked/alternate harmony、含糊 direction words，以及 `<note attack/release/time-only/pizzicato>`；全部在返回 IR/GREEN 前 typed fail-closed。
- 全仓离线 `516 passed, 6 deselected`，本地代理全量 `522 passed`、`522 collected`；ruff、mypy(strict)、lock 与 diff check 全绿。
- `dist/fretsure_oracle-0.1.0.tar.gz` 与 wheel 重建；clean core venv 中 import 成功且 importer typed `MISSING_DEPENDENCY`；clean `[musicxml]` venv 安装后对 music21 producer fixture 的 CLI 双跑 stdout/JSONL byte-identical，6 条 trace 均可解析；stub benchmark stamps 为 `oracle@0.1.0`、`fidelity@0.2.0`、`median@0.1`。

## 4. 完成定义

以下全部有证据才算本大部分完成：

1. 真实未压缩 MusicXML 文件能跑完整纵切并稳定输出。
2. 每个宣称支持的语义都有 exact golden/metamorphic 证据。
3. 每个延后语义都有稳定 typed failure，而不是 silent fallback。
4. tempo、tie、rest、harmony 在解析、prompt、solver/oracle 与 faithfulness 各层没有语义断裂。
5. 安全 envelope、资源上限、provenance、dependency isolation 有测试。
6. 所有质量门全绿，文档不再声称受限未压缩 MusicXML “尚未冻结/未实现”，也不把未支持的 `.mxl`/复杂 MusicXML/MIDI 混入当前能力。

**当前完成判定**：全部 6 项完成定义均有代码、测试、原样 producer artifact、构建/安装 smoke 与文档证据，本计划闭门。

**后继顺序**：先独立关闭 Oracle 0.2 公共输入/判决/统计信任门并提交；再实现安全 `.mxl` container / `musicxml@0.2.0`。两者各自闭门后才进入 Plan 6A。

**后续状态（2026-07-16）**：Oracle 0.2 软件信任门与后继安全 `.mxl` container reader 均已按独立计划关闭；本段保留的是 `musicxml@0.1.0` 提交当时的顺序和验收数字，不回写历史版本。当前下一项为 Plan 6A Web/API/trace viewer/MCP 薄纵切。
