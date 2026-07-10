# Fretsure — 项目状态 / 恢复文档

> 目的：任何新会话读完本文件 + 设计 spec，即可无损接上。最后更新：2026-07-10。

## 0. 现状一句话
设计完成；**路线图 + Plan 1–5 计划已写**；**Plan 1–5 全部实现**（oracle + 求解器 + agent + benchmark + 难度简化/伴奏 = 整个后端产品）；**已收敛打磨 + merge 到 trunk + 求职 artifact 就位**。
- **Plan 1**（`plan-1-core-oracle`）：可弹性 oracle + 自验证台。终审 Ready。
- **Plan 2**（`plan-2-solver-m0`）：beam 求解器（永不返回 RED）+ M0。复核 Ready。
- **Plan 3**（`plan-3-agent-loop`）：oracle 当环境、LLM 当策略——修复脊柱 + 提议器 + critic + best-of-N。真 LLM 端到端。Ready-with-minor（已修）。
- **Plan 4**（`plan-4-benchmark`）：checker 打分 benchmark——程序生成器 + 忠实度 DTW + pass^k/Wilson + leave-one-out 消融 + checker-vs-judge + baselines + `fretsure-bench` CLI。Ready-with-minor（已修）。
- **Plan 5**（`plan-5-difficulty-accompaniment`）：**可验证难度简化**（tier/check_tier 门/measured_tier/simplify_to_tier，真 LLM 简化到 beginner 档保旋律）+ **伴奏**（声位 + arpeggio/strum 过 oracle）。独立审查 Ready-with-minor（I1/I2/M1/M2/M3/M5 已修）。
- **收敛打磨（2026-07-10，`consolidation`→已 ff 并入 `master`）**：
  - `fretsure-demo` 一条命令端到端 demo（离线确定性；`--llm` 用真代理）。
  - `docs/BENCHMARK_RESULTS.md`：真 LLM 消融真实数（n=16 seed1）+ Wilson CI；头条**修复挣得存在**（0.81 vs 0.31，CI 不重叠）；critic/best-of-N 诚实负结果公开。
  - README 首屏重写（一条命令 + 架构图 + 头条表 + Plan 1–5 状态）；`docs/DEMO_SCRIPT.md` 3 分钟脚本。
  - **"谁检查语料"发现并修**：生成器 `KEY:degN` 用 0 索引音级，`C:deg5` 实为 vi(A) 却读作 V(G)，误导 LLM 放错低音、`bass_root` 恒 0 → `joint_success` 恒 0。非 agent/度量 bug，是语料标注 bug。改为真实和弦名（`Am`/`Dm`…）与 root_pc 一致，回归测试守。
  - harness `_rank` 加 `bass_preserved`（在 critic 之上）——选择不再为口味牺牲低音。
  - solver `passes_optimistic` 快路径（~3.5×）。
  - **配对 best-of-N 消融**（`bench.paired_best_of_n` + `--paired`）：harness 拆成 `arrange_pool`+`best_of_k`，同一提议池上比 best-of-1 vs best-of-N，消除非配对采样混淆。实测两 seed 一致 **+0.125 GREEN**（非配对臂原本符号翻转）→ best-of-N 挣得薄利；critic 仍在观察名单。过 **3-lens opus 审查 workflow**（pairing/regression/stats 全清，1 个 Minor n≤0 已修）。
  - **demo overclaim 修**：AMBER 路径原误印 "machine-certified"，改为按判决门控（只 GREEN 才认证）。
- **267 单测全绿（261 离线 + 6 真 LLM 集成）、ruff+mypy(strict) clean**；每 Plan 过独立 opus 审查并修发现。
- **分支**：plan-1→2→3→4→5→`consolidation` 已**全部 ff 并入 `master`（trunk）**（trunk 原只有 spec 脚手架；现含完整后端）。
- **下一步待定**：Plan 6（UI/web/demo/MCP，前端领域跳转）/ Plan 7（stretch RL/GEPA）/ D 层真实语料 + gold 集 + 参数校准（需 design partner）/ 配对式 best-of-N 消融（现为非配对，见 RESULTS 局限）。
- **已知点**：solve_fingering 长片段仍偏慢（快路径已缓解）；tier/忠实度/难度参数占位待 design partner 校准；消融各臂对随机 LLM 非配对（大效应 repair 不受影响，小效应 best-of-N 被混淆）。

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
- **Plan 1 已完成**（分支 `plan-1-core-oracle`，99 测试绿，独立终审过并修了 Critical）。
- 默认下一步：**Plan 2「指法求解器 + M0 端到端纵切」**——写详细 TDD 计划（候选生成 + 帧级 DP/Viterbi 调 `feasible_fingerings` + `solve_fingering` + ASCII/MusicXML 渲染 + lead sheet→提议 stub→solver→oracle→渲染端到端），再逐 task 执行。
- 或按用户指定的 Plan 切入。
- 合并 `plan-1-core-oracle` 到主线：待用户决定（PR / 直接 merge）。
