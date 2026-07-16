# Fretsure — 项目状态 / 恢复文档

> 目的：任何新会话读完本文件 + 设计 spec，即可无损接上。最后更新：2026-07-16。

## 0. 现状一句话
设计已锁定；**Plan 1–5、Pre-Plan 6 的 MusicXML-first 文件纵切、Oracle 0.2 软件信任门与安全 `.mxl` container reader 已经实现并各自闭门**，当前是一套可运行的后端研究原型。此提交的版本边界是 package=`0.2.0`、playability=`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、faithfulness=`fidelity@0.2.0`、importer=`musicxml@0.2.0`、container=`mxl-container@0.1.0`、profile=`median@0.1`；默认真代理模型为 canonical `gpt-5.6-sol`。Web/API/MCP、扩展 MusicXML/IR、MIDI 与音频尚未进入本提交。
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
- **Pre-Plan 6 MusicXML-first（DONE）**：受限 MusicXML 3.1/4.0 `score-partwise` 单 part/staff/voice 单音 lead-sheet 子集贯通文件→MusicIR→agent/solver/oracle→faithfulness→ASCII/trace；unsupported sounding semantics typed fail-closed，ERROR 不返回部分 IR。
  - 全曲固定的 `divisions` 与 `duration` 按 MusicXML 4.0 的 decimal 类型用 bounded XSD-decimal grammar + exact `Fraction` 处理；`divisions` 变化 typed fail-closed。raw note/harmony event timeline 是时间权威，music21 只做逐事件语义交叉验证。非法 exponent/分数/underscore、过长 numeric/time token、浮点归一化失真、note attack/release、standalone tempo change、stacked harmony 与含糊 direction words 均已 fail-closed 回归。
  - producer artifact/provenance gate 已闭合：`tests/fixtures/producers/provenance.json` 冻结 music21 10.5.0 与 musicxml 1.6.1 两个未经手改的 library/toolkit 正例，以及 MuseScore Studio 4.7.4 的原样负例和 exporter/version/hash/license。MuseScore 因省略 key mode 稳定 `UNSUPPORTED_KEY`；当前没有常见 notation application 的正兼容证据，该兼容性仍 open，不能从 provenance gate 推导。
  - 在该历史提交中，`.mxl` 返回 `COMPRESSED_MXL_UNSUPPORTED`；后继 safe-container 能力未回写这份历史证据。
- **Safe `.mxl` container（DONE）**：`musicxml@0.2.0` 在不落盘、不调用 extract/extractall、不让 music21 猜 archive 的前提下支持严格 `.mxl`。在 `ZipFile` 前有界验证 EOCD/central/local headers、member 路径/类型/extra/重叠与资源元数据，之后流式读完所有 member，并独立核对实际 size、CRC 与 deflate 完整性；`container.xml` 只能唯一选择一个安全 `.musicxml`/`.xml` root。
  - SHA-256 继续绑定用户原始输入；`.mxl` 另记录 root SHA-256、percent-escaped rootfile path 与 `mxl-container@0.1.0`。`ImportProvenance`、CLI rootfile 行和 trace/benchmark checker stamps 已冻结。
  - 容器只扩展 transport：root MusicXML 仍走同一 defused XML → frozen envelope → exact raw timeline/preflight → music21 交叉验证，复杂语义与 URI/resource-bearing XML 继续在 adapter 前 fail-closed。
- **Oracle 0.2 软件信任门（DONE）**：公共 Tab/profile/solver/MusicIR/tier/benchmark/gold/statistics 边界统一 typed fail-closed；非法输入不伪装成判决。validation/use 使用 detached snapshot，Tab serializer 与标准 JSON trace 在分配/编码前受资源门保护，直接 agent 循环与 pipeline 共用固定上限。MusicIR 限 20,000 notes + 20,000 chords、10 Mi 文本和 256-bit Fraction；benchmark 控制在建 corpus/调用 factory 前受 signed-63 seed、items/bars/乘积门保护。每个有效判决绑定 checker、profile version、profile canonical SHA-256 与 `tab-input@0.2.0`。
  - active sounding notes 进入全部左手几何；同弦半开区间 overlap 为 `STRING_SUSTAIN_CONFLICT`；换把使用 release-before-attack 事件流与连续 reachable hand-centre interval，实际消费 `reach_mm`。
  - solver 不静默 clamp beam/覆盖重复 onset+pitch；12,000,000 weighted-work 门在高分支枚举前拒绝，有限 finalist 重建后仍须通过完整 oracle。`Infeasible` 是 bounded search 结果，不宣称数学无解。
  - Trace 在 `json.dumps` 前精确计算含 escaping 的 compact UTF-8 字节数并与 encoder 结果交叉核对；tier 控制先深快照，横按 overlap 用保持诊断语义的 `O(6n)` 反向扫描。
  - gold 文件/内存输入具有累计 bytes/rows/notes/checker-work/lines/JSON-nodes 上限、深快照与 digest provenance；zero-GREEN false-accept 结果为 `status="no_green"` 且 rate/bound=`None`，退化 κ 与空 pass^k 同样显式 undefined。
  - 真人 gold/calibration 不阻塞软件主线，但继续阻塞现实世界 GREEN 误接受率、profile/tier→真人映射、AMBER 经验带宽、真人 musicality 与更强对外保证。
- **诚实记分卡**：历史 repair 强正信号；best-of-N 薄利；**critic 未挣得（观察/待砍）**。这些旧数来自 legacy/unversioned harmony metric，不是 `fidelity@0.2.0` benchmark 基线。
- **当前质量门**：离线 `1242 passed, 6 deselected`，本地 `gpt-5.6-sol` 代理全量 `1248 passed`；`1248 collected`。ruff、mypy(strict, 61 source files)、`uv lock --check` 与 `git diff --check` 全绿；`fretsure_oracle-0.2.0` wheel/sdist 从最终树重建，sdist 163-entry allowlist 审计排除了本地配置与缓存；clean core/no-extra 对有效 `.mxl` typed `MISSING_DEPENDENCY`、clean `[musicxml]` 安装、真实 `.mxl` CLI 双跑/6-row JSONL 与 stub benchmark smoke 全绿。stdout、JSONL metadata row 与 benchmark JSON 均盖 `llm_model_id`、`oracle@0.2.0`、`fidelity@0.2.0`、`tab-input@0.2.0`、`median@0.1` 及 profile SHA-256；CLI 另盖 `musicxml@0.2.0`、raw/root hashes 与 rootfile member。
- **分支**：plan-1→2→3→4→5→`consolidation` 已**全部 ff 并入 `master`（trunk）**（trunk 原只有 spec 脚手架；现含完整后端）。
- **下一步已冻结**：安全 `.mxl` 独立提交后进入 Plan 6A Web/API/trace viewer/MCP 薄纵切；随后按真实 producer failure 分布扩 MusicXML/IR，再做 MIDI 与 benchmark v2。
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
6. UI / trace viewer / demo（AlphaTab + 指板动画 + live A/B + live 榜单 + 音频 + MCP server）。
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
- 下一项是 Plan 6A Web/API/trace viewer/MCP 薄纵切；之后才按 producer failure 扩 MusicXML/IR、实现 MIDI、重跑 benchmark v2。
- 真人 gold/calibration 可并行，不阻塞上述软件实现；它仍阻塞现实世界 GREEN 误接受率、profile/tier 校准与“真实琴手一定能弹”的强主张。
