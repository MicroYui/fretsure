# Fretsure — 项目状态 / 恢复文档

> 目的：任何新会话读完本文件 + 设计 spec，即可无损接上。最后更新：2026-07-17。

## 0. 现状一句话
设计已锁定；**Plan 1–5、Pre-Plan 6 MusicXML-first、Oracle 0.2 软件信任门、安全 `.mxl`、Plan 6A Web/API/replay trace/MCP 与 producer-driven MusicXML/IR 已各自闭门**。当前版本边界是 package=`0.4.0`、service=`fretsure-service@0.1.0`、API=`fretsure-api@0.1.0`、MCP=`fretsure-mcp@0.1.0`、trace=`agent-trace@0.1.0`；playability=`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、faithfulness=`fidelity@0.2.0` 保持不变，importer=`musicxml@0.3.0`，container=`mxl-container@0.1.0`、profile=`median@0.1` 保持不变，MusicXML runtime 精确锁定 `music21==10.5.0`。默认真代理模型为 canonical `gpt-5.6-sol`，Web/API 默认确定性离线；只有显式有效的 loopback proxy 配置加启动授权才可联网。下一软件阶段是 MIDI，之后才是 benchmark v2；任何新前端视觉先与用户确认并沿用已冻结风格。完整 Plan 6 的音频、AlphaTab、真实琴颈动画、导出、live A/B/榜单与真人 money moment 仍 open。
- **Plan 1**（`plan-1-core-oracle`）：可弹性 oracle + 自验证台。终审 Ready。
- **Plan 2**（`plan-2-solver-m0`）：beam 求解器（永不返回 RED）+ M0。复核 Ready。
- **Plan 3**（`plan-3-agent-loop`）：oracle 当环境、LLM 当策略——修复脊柱 + 提议器 + critic + best-of-N。真 LLM 端到端。Ready-with-minor（已修）。
- **Plan 4**（`plan-4-benchmark`）：checker 打分 benchmark——程序生成器 + `fidelity@0.2.0` exact-onset melody/bass 与 chord-segment harmony Jaccard + pass^k/Wilson + leave-one-out 消融 + checker-vs-judge + baselines + `fretsure-bench` CLI。Ready-with-minor（已修）。
- **Plan 5**（`plan-5-difficulty-accompaniment`）：**可验证难度简化**（tier/check_tier 门/measured_tier/simplify_to_tier，真 LLM 简化到 beginner 档保旋律）+ **伴奏**（声位 + arpeggio/strum 过 oracle）。独立审查 Ready-with-minor（I1/I2/M1/M2/M3/M5 已修）。
- **收敛打磨（2026-07-10，`consolidation`→已 ff 并入 `master`）**：
  - `fretsure-demo` 一条命令端到端 demo（离线确定性；`--llm` 用真代理）。
  - `docs/BENCHMARK_RESULTS.md`：真 LLM 消融真实数（n=16 seed1）+ Wilson CI；头条**修复挣得存在**（0.81 vs 0.31，CI 不重叠）；critic/best-of-N 诚实负结果公开。
  - README 首屏重写（一条命令 + 架构图 + 头条表 + Plan 1–5 状态）；`docs/DEMO_SCRIPT.md` 3 分钟脚本。
  - **"谁检查语料"发现并修**：生成器 `KEY:degN` 用 0 索引音级，`C:deg5` 实为 vi(A) 却读作 V(G)，误导 LLM 放错低音、`bass_root` 恒 0 → `joint_success` 恒 0。非 agent/度量 bug，是语料标注 bug。改为真实和弦名（`Am`/`Dm`…）与 root_pc 一致，回归测试守。
  - harness `_rank` 加 `bass_preserved`（在 critic 之上）——选择不再为口味牺牲低音。
  - solver `passes_optimistic` 快路径（~3.5×）。
  - **配对消融（best-of-N + critic）**（`bench.paired_best_of_n`/`paired_critic` + `--paired`）：harness 拆成 `arrange_pool`+`best_of_k`（`use_critic` 可参数化），同一提议池上比选择宽度 / critic 开关，消除非配对采样混淆。实测：best-of-N 两 seed 一致 **+0.125 GREEN**（非配对臂原本符号翻转）→ 挣得薄利；critic 按**本职 taste** 测两 seed 仅 **+0.01**、对 joint ≤0 → **在此语料未挣得存在，留观察/待砍**。各过 **opus 审查 workflow**（best-of-N 3-lens 全清 1 Minor n≤0 已修；critic lens 无 Critical/Important，其 Minor "critic 应按 taste 而非 joint 评判"已采纳并改度量）。
  - **demo overclaim 修**：AMBER 路径原误印 "machine-certified"，改为按判决门控（只 GREEN 才认证）。
- **Pre-Plan 6 MusicXML-first（历史闭门记录，DONE）**：受限 MusicXML 3.1/4.0 `score-partwise` 单 part/staff/voice 单音 lead-sheet 子集贯通文件→MusicIR→agent/solver/oracle→faithfulness→ASCII/trace；unsupported sounding semantics typed fail-closed，ERROR 不返回部分 IR。
  - 全曲固定的 `divisions` 与 `duration` 按 MusicXML 4.0 的 decimal 类型用 bounded XSD-decimal grammar + exact `Fraction` 处理；`divisions` 变化 typed fail-closed。raw note/harmony event timeline 是时间权威，music21 只做逐事件语义交叉验证。非法 exponent/分数/underscore、过长 numeric/time token、浮点归一化失真、note attack/release、standalone tempo change、stacked harmony 与含糊 direction words 均已 fail-closed 回归。
  - 该阶段的 producer artifact/provenance gate 冻结 music21 10.5.0 与 musicxml 1.6.1 两个未经手改的 library/toolkit 正例，以及 MuseScore Studio 4.7.4 的原样负例和 exporter/version/hash/license。MuseScore 在该历史 importer 中因省略 key mode 稳定 `UNSUPPORTED_KEY`；这条历史证据不回写为当前行为。
  - 在该历史提交中，`.mxl` 返回 `COMPRESSED_MXL_UNSUPPORTED`；后继 safe-container 能力未回写这份历史证据。
- **Safe `.mxl` container（DONE）**：`musicxml@0.2.0` 在不落盘、不调用 extract/extractall、不让 music21 猜 archive 的前提下支持严格 `.mxl`。在 `ZipFile` 前有界验证 EOCD/central/local headers、member 路径/类型/extra/重叠与资源元数据，之后流式读完所有 member，并独立核对实际 size、CRC 与 deflate 完整性；`container.xml` 只能唯一选择一个安全 `.musicxml`/`.xml` root。
  - SHA-256 继续绑定用户原始输入；`.mxl` 另记录 root SHA-256、percent-escaped rootfile path 与 `mxl-container@0.1.0`。`ImportProvenance`、CLI rootfile 行和 trace/benchmark checker stamps 已冻结。
  - 容器只扩展 transport：root MusicXML 仍走同一 defused XML → frozen envelope → exact raw timeline/preflight → music21 交叉验证，复杂语义与 URI/resource-bearing XML 继续在 adapter 前 fail-closed。
- **Plan 6A Web/API/replay trace/MCP（DONE）**：新增不依赖 transport framework 的 bytes-first application seam；HTTP 以 raw streamed body 接受 MusicXML/MXL 与 strict Tab JSON，默认 loopback、拒 DNS rebinding Host/跨源写、typed `application/problem+json`、固定 8×16 公开预算。proxy 默认关闭，缺 loopback URL/token/dependency 时在读 body/发网络前 fail-closed；真实 `gpt-5.6-sol` API smoke 已盖实际 model id。
  - `agent-trace@0.1.0` 冻结 contiguous seq/event/candidate/iteration、逐事件 exact schema、结构化诊断/edit、canonical checkpoint digest/size/count、512 KiB aggregate state budget 与敏感键/内容 fail-closed。公开 trace 保留一个完整 winner 或有代表性的失败候选，顺序为真实 proposal→solve/oracle→reason/edit/recheck→select；terminal 同时盖 playability 与 authoritative faithfulness gate。
  - `fretsure-mcp@0.1.0` 默认 stdio，initialize 正确报告 Fretsure 版本；只提供 `check_playability`、有界 `feasible_fingerings` 与 ASCII `render_notation`，无假 `render_audio`。官方 memory session 与真实 subprocess 的三工具/invalid/oversize-survival 已通过。
  - React/Vite Web 使用同源 capabilities 作为配置真源，提供 raw upload、CC0 真实示例、独立双门、ASCII tab、typed failure 与 trace viewer。严格 CSP 下无 inline style/HTML；键盘结果焦点、retry、reduced motion、desktop/mobile 与 hostile metadata 回归已关闭。用户于 2026-07-16 明确评价“这个前端做的挺好看的”，认可带古典气质的方向；该方向冻结为后续视觉基线。截图见 `docs/assets/plan6a/`。
  - 最终门、独立审计闭环、截图与用户原话集中记录在 `docs/PLAN6A_ACCEPTANCE.md`。
- **Producer-driven MusicXML/IR（DONE）**：package 升至 `0.4.0`、importer 升至
  `musicxml@0.3.0`，其余 checker/container/service/API/MCP/trace 版本不改义。MusicXML 4.0
  traditional `<key>` 合法省略 `<mode>` 时不猜 major/minor，而把
  `key-signature:fifths=N;mode=unprovided` 写入 `Meta.key` 并发 located
  `KEY_MODE_UNPROVIDED` warning；MusicXML 3.1 省略 mode、空/其他 mode、重复 key/child 与其他延后
  语义继续 typed fail-closed。
  - producer manifest 冻结并 source-bind music21 10.5.0、musicxml 1.6.1 和精确的 MuseScore Studio
    4.7.4 XML/MXL artifacts；只主张这些 exact bytes/roots 通过，不外推到任意 MuseScore 乐谱、其他
    版本或完整 MusicXML。runtime 精确锁定 `music21==10.5.0`。
  - importer success 现在必须通过 public MusicIR snapshot；reduced Fraction numerator/denominator 各自
    256-bit 为边界，超限在 music21/arranger/LLM 前 typed fail-closed，不再返回随后被消费者拒绝的 IR。
  - 完整 raw preflight 是诊断与语义真源；零 error 后才重建 bounded event-only XML 给 music21，只保留
    divisions、harmony 与 note/rest/tie 事件。credit、instrument/MIDI、layout/print、lyrics/voice、key
    visual metadata 和合法额外 non-note-bearing part 不跨第三方边界；重复 scalar、key exact shape、
    权威语义字段的 ASCII/XSD 数值、外部资源、location/diagnostic amplification 与 adapter 后验证均有
    typed 上限。
  - 计划、逐 artifact before/after census 与最终门分别见
    [`2026-07-16-producer-driven-musicxml-ir.md`](superpowers/plans/2026-07-16-producer-driven-musicxml-ir.md)、
    [`2026-07-16-producer-musicxml-census.json`](experiments/2026-07-16-producer-musicxml-census.json) 与
    [`PRODUCER_MUSICXML_ACCEPTANCE.md`](PRODUCER_MUSICXML_ACCEPTANCE.md)。
- **Oracle 0.2 软件信任门（DONE）**：公共 Tab/profile/solver/MusicIR/tier/benchmark/gold/statistics 边界统一 typed fail-closed；非法输入不伪装成判决。validation/use 使用 detached snapshot，Tab serializer 与标准 JSON trace 在分配/编码前受资源门保护，直接 agent 循环与 pipeline 共用固定上限。MusicIR 限 20,000 notes + 20,000 chords、10 Mi 文本和 256-bit Fraction；benchmark 控制在建 corpus/调用 factory 前受 signed-63 seed、items/bars/乘积门保护。每个有效判决绑定 checker、profile version、profile canonical SHA-256 与 `tab-input@0.2.0`。
  - active sounding notes 进入全部左手几何；同弦半开区间 overlap 为 `STRING_SUSTAIN_CONFLICT`；换把使用 release-before-attack 事件流与连续 reachable hand-centre interval，实际消费 `reach_mm`。
  - solver 不静默 clamp beam/覆盖重复 onset+pitch；12,000,000 weighted-work 门在高分支枚举前拒绝，有限 finalist 重建后仍须通过完整 oracle。`Infeasible` 是 bounded search 结果，不宣称数学无解。
  - Trace 在 `json.dumps` 前精确计算含 escaping 的 compact UTF-8 字节数并与 encoder 结果交叉核对；tier 控制先深快照，横按 overlap 用保持诊断语义的 `O(6n)` 反向扫描。
  - gold 文件/内存输入具有累计 bytes/rows/notes/checker-work/lines/JSON-nodes 上限、深快照与 digest provenance；zero-GREEN false-accept 结果为 `status="no_green"` 且 rate/bound=`None`，退化 κ 与空 pass^k 同样显式 undefined。
  - 真人 gold/calibration 不阻塞软件主线，但继续阻塞现实世界 GREEN 误接受率、profile/tier→真人映射、AMBER 经验带宽、真人 musicality 与更强对外保证。
- **诚实记分卡**：历史 repair 强正信号；best-of-N 薄利；**critic 未挣得（观察/待砍）**。这些旧数来自 legacy/unversioned harmony metric，不是 `fidelity@0.2.0` benchmark 基线。
- **Plan 6A 闭门质量门（历史快照）**：收集 `1500` 项；离线 `1494 passed, 6 deselected`，真实本地 `gpt-5.6-sol` integration `6 passed, 1494 deselected`。ruff、strict mypy、`uv lock --check`、Markdown local-link 与 `git diff --check` 全绿；前端 `20 passed`、typecheck/build、`npm audit` 0 vulnerabilities，真实浏览器 desktop/mobile 的 landing/result/trace 与 focus/retry/CSP/MIME/cache 路径通过。`fretsure_oracle-0.3.0` wheel/sdist 经过路径 allowlist、字体 OFL、静态资源审计；clean core、`[musicxml]`、`[service,musicxml,agent]`、`[mcp]` 四组合安装 smoke 全绿。FastAPI 0.139 TestClient 仍发出上游 httpx2 迁移 warning，运行时代码无对应 warning。producer 阶段的新门不从这组历史数字推断，以 `docs/PRODUCER_MUSICXML_ACCEPTANCE.md` 为准。
- **分支**：plan-1→2→3→4→5→`consolidation` 已**全部 ff 并入 `master`（trunk）**（trunk 原只有 spec 脚手架；现含完整后端）。
- **下一步已冻结**：producer-driven MusicXML/IR 已独立验收；本阶段提交并推送、核对 local/remote SHA 后进入 MIDI，MIDI 闭门后才进入 benchmark v2。完整 Plan 6B 在这些底层产物成熟后补音频/AlphaTab/琴颈动画/导出/live demo；下一次真人门只在新视觉/听感/真人 calibration 发生时暂停。
- **已知点**：solve_fingering 是资源有界、非完备搜索；tier/忠实度/难度参数占位待 design partner 校准；leave-one-out 各臂对随机 LLM **非配对**（大效应 repair 不受影响；best-of-N/critic 已另有**配对**测量，见 RESULTS）。

## 1. 这是什么
一个 agent，把一首歌的**音乐内容**（符号：MusicXML/MIDI/lead sheet 为保证路径；mp3 为 best-effort 前端）编配成一份在指定难度/调弦/变调夹下**人手可证明弹得出来**的吉他谱：
- **HERO** = 指弹独奏（旋律+低音+和声揉进一把吉他）
- 商业楔子 = 难度定向简化（"把这首歌简化到你能弹的水平"）
- 标配 = 伴奏谱
核心 = **LLM 提议编配 → 确定性指法求解 → 确定性可弹性 oracle 逐音硬门 + 定位化诊断 → 自动修复（保旋律/低音/和声）→ checker 打分 benchmark**。

## 2. 决策日志（按发生顺序，含理由）
1. **目标**：同时吃下 (a) 大厂 agent 岗作品集、(b) 内部 Copilot 评比、(c) 可上线；混合评审（要 demo wow + 硬指标）。
2. **领域方向**：从"硬核基础设施"转向"**领域好玩/受众广、技术尽量深**"（用户澄清：硬核指领域，不指技术）。选定**音乐/吉他编配**。
3. **具体产品**：可证明可弹的**指弹/伴奏吉他谱**（HERO=指弹）。
4. **输入**：**符号优先=保证路径**；mp3 作 best-effort 前端、不承诺正确。
5. **核心范式**：**oracle 当环境、LLM 当策略**（因为草稿"LLM 一次性提议+经典求解器"被判定 agent 太薄；反转为 LLM 驱动"规划→edit-DSL→oracle→推理→定点编辑→到不动点"闭环）。
6. **agent 深度纪律**：每个能力（规划/工具/修复/critic/搜索/记忆/RL）用 **leave-one-out 消融**挣存在，**砍掉的组件公开**（反 LARP）。
7. **benchmark**：**checker 打分（非 LLM 评委）**；程序生成层扛头牌（防污染）；可弹×忠实**联合 Pareto**（防"削音作弊"）；"谁检查检查器"验证台先行。
8. **harness**：**自研**（不依赖 LangGraph/Claude Agent SDK；框架仅作对照基准，"自研 vs 框架"对照本身是 artifact）；**DSPy/GEPA 保留**（prompt 优化，消融把关）、**MCP** 暴露 oracle。
9. **RL**：stretch，收口为 CPU 小 **reranker**，允许诚实负结果。
10. **可展示性**（用户强调）：不能只是数字；**money moment** = 观众点歌→oracle 标红→agent 修复→真人当场弹出。**可展示=真功能**。
11. **定名**：**Fretsure**（fret+ensure）。备选 PlayProof/Fretwright 存档。
12. **默认模型迁移（2026-07-16）**：真代理从历史 `claude-opus-4-8` 切到 canonical `gpt-5.6-sol`；代理协议与 prompts 不变，6 项 integration 全绿。工程适配成本近于零，但迁移当日的本地代理 metadata 显示输入价相同、输出价约高 20%；按本项目 API 充足/质量优先约束接受。CLI、trace 与 benchmark 聚合 JSON 盖 `llm_model_id`；legacy Claude 数表只作历史证据，benchmark v2 必须独立重跑。
13. **Plan 6A 产品边界与视觉（2026-07-16）**：先交付 replay-first 本地 Web/API/MCP 薄纵切，不用假音频/假 notation 冒充完整 Plan 6；默认离线、proxy 启动时显式授权。用户认可“古典制琴工坊 × 验证仪器”方向，后续前端沿此基线迭代；视觉认可不替代真人可弹性/听感 calibration。
14. **Producer-driven MusicXML/IR（2026-07-16）**：只修 frozen producer census 中占主导的
    MusicXML 4.0 omitted-mode failure，不推断调式、不扩 IR shape；`mode=unprovided` + warning 让信息损失
    可见。`music21` 收窄到 exact 10.5.0，package/importer 分别升 0.4.0/0.3.0；下一顺序固定为
    MIDI → benchmark v2。

## 3. 诚实的新颖性裁决（红队结论，勿自欺）
- **部分开放**：无成熟上线产品做这套完整组合；但**概念不新**。
- 先例：**SMC 2024 id55**（lead sheet→指弹，Viterbi 在 ~3658 可弹形态里搜，论文非产品）；**TemPolor Melo-D**（2026-09，输出被评"弹不了"→验证痛点）；**Woolfy/THIRI/CLARA**（做和声/声部进行/可弹编排，但不做 tab 可弹性 + LLM 提议 + 修复 + benchmark 的整合）；notave/GrabTab/爱扒谱（启发式指法建议，不保证）。
- **护城河 = 执行力 + benchmark 严谨 + 覆盖广度 + 先发，不是原创。** 对求职/评比够用；对纯 startup 最弱（TAM 小众、可被追）。
- 能扛核查的**唯一创新主张**：见 spec §0。

## 4. 实现计划的 7 拆分（每个 = 可运行可测软件；顺序按"oracle 先行"）
1. **核心 + Oracle**（地基，gate 一切）：Music IR + tab 表示 + 可弹性 oracle（毫米几何/三态/profile/类型诊断）+ 自验证套件 → `fretsure-oracle` 可 pip 安装 + 绿 CI。**← 建议从这里开始。**
2. 指法求解器 + M0 端到端纵切（→ 一份确定性可弹指弹 tab）。
3. Agent 回路（自研 harness）+ verifier-guided 修复 + best-of-N + critic。
4. Benchmark & eval 台（语料+程序生成器 + 忠实度 + pass^k/Wilson + baselines + leave-one-out 消融 + checker-vs-judge + 一条命令复现）。
5. 难度简化 + 伴奏。
6. UI / trace viewer / demo：**6A 已完成 Web/API/replay viewer/stdio MCP**；完整 6B 的 AlphaTab、指板动画、live A/B/榜单、音频、导出与 money moment 仍 open。
7. (stretch) DSPy/GEPA + CPU RL reranker + verifiers env。

## 5. 未决项（不阻塞，但迟早要定）
- 首发风格/曲库范围（建议先民谣+流行指弹，M0 前定 1 个风格）。
- 难度 tier 具体参数（span/把位/tempo 阈值，需对真实琴手校准，M4 前定）。
- RL 具体形态（reranker vs 学习型代价）。
- **真人 design partner**（吉他老师/琴手校准 oracle 与难度）——能大幅降低"合成基准不真实"质疑，建议尽早找。

## 6. 已完成的调研（勿重复；结论已并入 spec）
用 workflow 跑过并已内化：① agent 产品赛道 landscape；② 创意向重筛（音乐/游戏谜题/格律写作/视觉/wildcard）；③ 音乐深挖（新颖性 + Azure TTS 可否撑 demo + 免费/付费工具链）；④ AI 编曲成熟度红队（生成成熟 vs 保证不成熟）；⑤ 吉他 tab 具体新颖性红队（TemPolor/SMC id55…）；⑥ benchmark + checker + agent 深度设计（含消融矩阵 + 两个头牌结果）；⑦ SOTA harness 选型 + 可展示 demo + 求职 artifact。**再研究前先看 spec §14/§15,多数问题已答。**

## 7. 下一步（用户说"继续"时）
- 不重做 Plan 1–5 或 MusicXML-first 纵切。
- Oracle 0.2 软件信任门已经完成；不要重做。
- 安全 `.mxl` container reader 已完成；不要重做。
- producer-driven MusicXML/IR 不再重做；完成本阶段提交/推送并核对 local/remote SHA 后实现 MIDI，MIDI 闭门后重跑 benchmark v2。无需用户持续审计，直到新的前端/音频审美、真人听感或 calibration gate 出现再暂停。
- 真人 gold/calibration 可并行，不阻塞上述软件实现；它仍阻塞现实世界 GREEN 误接受率、profile/tier 校准与“真实琴手一定能弹”的强主张。
