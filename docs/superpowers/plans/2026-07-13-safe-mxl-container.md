# Safe `.mxl` Container Reader

> **状态（2026-07-16）**：**DONE（安全容器门已关闭）**。前置 Oracle 0.2 软件信任门已经以提交 `82232a7`、`1092 passed, 6 deselected / 1098 collected / 本地代理 1098 passed` 独立关闭；本计划在其后把 importer 升为 `musicxml@0.2.0`，不回写前置闭门证据。本计划闭门树为 `1236 passed, 6 deselected / 1242 collected / 本地代理 1242 passed`。

**Goal:** 在不把 ZIP 文件解压到磁盘、也不把 archive 交给 `music21` 自行猜测的前提下，把一个严格受限的 compressed MusicXML `.mxl` 容器解析成唯一 root MusicXML bytes，再交给现有安全 XML envelope/preflight/adapter 管线。

**Version decision:** importer 从 `musicxml@0.1.0` 升到 `musicxml@0.2.0`。未压缩 `.musicxml`/`.xml` 的既有语义保持兼容；新增 `.mxl` container、rootfile provenance 与 archive resource contract。package 仍是尚未发布的 `0.2.0` 工作树版本。

## 1. 不变量

- 全程 `BytesIO`/streaming read；禁止 `extract`、`extractall`、临时目录和任意磁盘落地。
- SHA-256 永远绑定用户提交的原始 bytes：未压缩输入绑定 XML bytes，`.mxl` 绑定完整 archive bytes。
- 只有 `META-INF/container.xml` 指定的唯一 rootfile 会进入现有 MusicXML XML parser；其他 member 只做 metadata、bounded decompression 与 CRC/一致性验证。
- 不猜 root、不扫描“第一个 XML”、不跟随链接、不接受密码、不容忍 normalized alias。
- archive/container 非法是 typed import failure，绝不返回部分 `MusicIR`。

## 2. 资源 envelope

在 `ImportLimits` 中独立冻结：archive bytes、central-directory bytes、member count、member-name length、单 member uncompressed bytes、总 uncompressed bytes、container bytes 与整数 compression-ratio 上限。现有 `max_bytes` 继续限制最终 root MusicXML bytes。

顺序必须是：

1. 根据文件 stat/read 上限读取原始 archive。
2. **在构造 `ZipFile` 前**自行 bounded 解析 EOCD 与 raw central directory；不能先让 `ZipFile._RealGetContents` 为任意数量的伪造 entry 分配 `ZipInfo`。
3. raw entry count 与 EOCD count 必须一致且都不超限；拒 multi-disk、ZIP64、SFX/prefix、archive/member comment、trailing junk、central digital signature 与越界 offset/length。
4. 对每个 raw central/local header 做 name、flag、method、size、offset 与 interval 一致性检查，再构造 `ZipFile`；构造后逐项与 preflight record 对照。
5. metadata 的 per-member/total/ratio 上界先过，之后才 bounded stream-decompress；实际 byte count 与 CRC 再次核验。

## 3. Member 安全合同

- 名称按 central-directory raw bytes解码；UTF-8 flag 用 strict UTF-8，否则用 CP437。必须检查 raw name，不能只看会在 NUL 处截断的 `ZipInfo.filename`。
- 只接受安全相对 POSIX path：拒 NUL/控制符、反斜杠、绝对路径、Windows drive、空段、`.`、`..`、超长名称。
- exact duplicate、Unicode NFC normalized collision、file/directory 同名 collision 均拒绝；不用 `NameToInfo` 的 last-wins 语义。
- 拒 encrypted/strong-encrypted/patched member、symlink 与其他特殊 Unix file type；只支持 stored/deflated regular file 或空 directory。
- local/central filename、flags、compression method 与非-data-descriptor size/CRC 必须一致；local-header/data intervals 不能重叠或越过 central directory。
- extra fields 首版只允许无路径重写语义的常见 timestamp/UID/GID/NTFS 元数据；拒 ZIP64、AES、Unicode-path override 与未知 extra，后续只按真实 producer 失败分布扩展。
- 所有 regular members 都读到 EOF 并核对 declared size 与 CRC，包括未使用的附件；任何 corruption 都使整个输入失败。

## 4. `container.xml` 合同

- 必须恰好存在一个 exact `META-INF/container.xml` regular member。
- 用 `defusedxml`、DTD/entity/external 全禁，且受 container byte/depth/element 上限约束。
- root 必须是 MusicXML compressed-container schema 的**无 namespace、无 attribute** `<container>`；只允许一个无 attribute 的 `<rootfiles>`，其中恰好一个 `<rootfile>`；unknown element/attribute、namespace 与非 whitespace 文本拒绝。这里不套用 OCF namespace/version——本地 music21 corpus 的 535 个 `.mxl` 均使用该无 namespace 形态。
- `full-path` 必须满足同一 safe-relative-POSIX 合同，且后缀为 `.musicxml`/`.xml`，并 exact 指向 archive 中一个 regular member。
- `media-type="application/vnd.recordare.musicxml+xml"` 时接受；为兼容常见标准工具导出，缺失 `media-type` 只在 extension/path 其余条件都成立时接受并发稳定 warning；空值或其他 MIME 拒绝。
- rootfile bytes 仍须满足现有 `max_bytes`，并且只由现有 `_safe_parse → envelope → preflight → canonical XML → music21 adapter` 路径解释。

## 5. Provenance/API

- `ImportSuccess` 增加向后兼容的结构化 `ImportProvenance`；它记录 source format、outer filename、raw SHA-256、root member、root SHA-256 与 `mxl-container@0.1.0`。旧 `.sha256` 字段继续等于 raw input hash。
- `MusicIR.meta.source` 同时记录 archive filename、raw archive SHA-256、root hash、`musicxml@0.2.0`、container version 与 percent-escaped rootfile path；分隔符不能被未转义值注入。
- CLI 对 `.mxl` 成功结果显示 rootfile member；所有 untrusted path/metadata 继续 terminal-escape。

## 6. Typed diagnostics

保留历史 `COMPRESSED_MXL_UNSUPPORTED` enum 以免破坏已有消费者，但 `musicxml@0.2.0` 不再发出它。新增稳定 codes 覆盖：invalid archive、unsafe/colliding member、missing/invalid container、rootfile count、unsupported rootfile、missing rootfile、corrupt member，以及缺 MIME warning；资源超限继续统一为 `INPUT_LIMIT_EXCEEDED`。

## 7. TDD / 红队矩阵

- 正例：stored/deflated、大小写 `.MXL`、标准 MIME、缺 MIME warning、nested root path；与同一 uncompressed root 的音乐 IR 相同（仅 provenance 不同）；CLI/trace 两次确定一致。
- archive：not-ZIP、EOCD/trailing junk、forged low EOCD count、central truncation/overrun、local-central mismatch、overlap、unsupported compression、ZIP64/multi-disk。
- name：absolute、`..`、`.`、double slash、backslash、drive、control/NUL、exact duplicate、NFC collision、file/dir collision、overlong。
- member type：encrypted/strong-encrypted、symlink/special mode、directory with payload。
- budgets：archive、central directory、count、name、member、total、container、root XML、per-member/aggregate ratio；在 `ZipFile` 构造或 decompression 前分别断言 fail-fast。
- integrity：root/container/unused member CRC corruption、declared-vs-actual size、truncated compressed stream；任何一个都不能被忽略。
- container：missing/duplicate、malformed/DTD/entity、wrong root/version/namespace、unknown element/attribute、多 rootfile、unsafe/wrong-extension/missing root、wrong/empty MIME。
- dependency/API：core-only 环境仍 typed `MISSING_DEPENDENCY`；raw archive hash、rootfile_path、IR provenance、CLI line 均冻结；monkeypatch `extract/extractall` 证明从未调用。

## 8. Acceptance

1. 上述矩阵全绿，且 hostile archive 不泄漏 untyped exception/traceback。
2. 全仓 pytest、ruff、mypy(strict)、lock/diff 与 Markdown local-link check 全绿。
3. 重建 wheel/sdist；clean core venv 与 clean `[musicxml]` venv 复验。
4. clean `[musicxml]` venv 中真实 `.mxl` CLI 两次 stdout/trace byte-identical；benchmark stamps 不漂移。
5. 文档只把 `.mxl` container 称为已支持；复杂 MusicXML 语义仍由原 root XML allowlist fail-closed，不借压缩容器扩大。
6. root XML 中的 URI/resource-bearing element/attribute 在 `music21` 前 fail-closed，并用 filesystem/network spy 证明不会因 import 触发外部读取。

## 9. 闭门证据

- 全量：离线 `1236 passed, 6 deselected`；本地代理 `1242 passed`；`1242 collected`。全仓 ruff、strict mypy（61 source files）、`uv lock --check`、`git diff --check` 与 Markdown local-link check 全绿。
- ZIP/container 红队覆盖 raw EOCD/central/local preflight、ZIP64/multi-disk/SFX/signature、路径/Unicode collision、member type/flag/extra、所有 metadata/ratio/size budgets、data descriptor、CRC/deflate/actual-size 完整性、container schema/root/MIME 与禁止 extract；正常 immutable bytes 另做 20,000 次随机变异，未发现 root 内容或路径误接受。
- 独立审查发现并关闭原候选唯一 Important：bytes subclass 可用状态化 `__len__` 绕过 archive 限额。最终入口要求 exact bytes，ImportLimits 也在读取/分配前 exact validation + detached snapshot；hostile hook/TOCTOU 回归全绿。
- `fretsure_oracle-0.2.0` wheel/sdist 从最终树重建；sdist 明确 allowlist 为 163 entries，无本地配置/缓存。clean core venv 对有效 `.mxl` 返回 typed `MISSING_DEPENDENCY`；clean `[musicxml]` venv 安装成功。
- clean `[musicxml]` 中真实 `.mxl` CLI 两次 stdout/6-row trace byte-identical；输出绑定 `musicxml@0.2.0`、`mxl-container@0.1.0`、raw/root SHA-256 与 rootfile member。trace/benchmark 继续绑定 `oracle@0.2.0`、`fidelity@0.2.0`、`tab-input@0.2.0`、profile version/fingerprint。
- 原 `.musicxml`/`.xml` exact timeline、producer fixtures 与语义分歧回归全部保留；`.mxl` 外层通过后仍由同一 root XML allowlist fail-closed，未扩大 MusicXML 语义兼容声明。

## 10. 后继

完成后进入 Plan 6A 薄 Web/API/MCP 纵切；真实 producer fixtures/provenance 与真人 gold/calibration 继续并行，均不阻塞 `.mxl` 软件实现，但分别限制 producer 兼容性与现实人体保证。
