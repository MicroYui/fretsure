# Fretsure — 项目状态 / 恢复文档

> 目的：任何新会话读完本文件 + 设计 spec，即可无损接上。最后更新：2026-07-09。

## 0. 现状一句话
设计（brainstorming → spec）**已完成并经用户确认**；**尚未写实现计划、尚未写代码**。用户当前选择**暂不实现**，但已采用项目名 **Fretsure** 并要求建好恢复文档 + git 仓库。

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
- 默认：进入 **writing-plans**，写 **Plan 1「核心 + Oracle」** 的可执行计划（bite-sized TDD 任务）。
- 或按用户指定的 Plan 切入。
