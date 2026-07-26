# 左手指法求解器 v2

状态：Plan 7B 软件与离线证据门已闭合；不主张普适真人保证。当前生产 stamps 为：

- `fingering-solver@0.6.0`
- `score-solver@0.6.0`
- `left-hand-ergonomics@0.1.0`
- `published-fingering-ranker@0.1.0`
- `oracle@0.3.0`
- `median@0.1`（参数值未变，仍需真人校准）

## 语义边界

难度档不参与已确定谱面的左手指法求解。同一份音符、时值、调弦、变调夹和演奏者物理 profile，默认得到同一份确定性指法；`beginner` / `intermediate` / `advanced` 只控制编配阶段产生什么谱，以及生成后的难度检查。简化循环也先用独立的 player profile 求这份候选谱的指法，再由 tier 决定是否继续改谱，不再把 `tier.profile` 传给求解器。

求解器包没有 `fretsure.difficulty` 依赖，并有架构回归测试锁定。手大小、触及范围等属于独立的演奏者物理 profile，不是谱面难度。

## v1 根因与 v2 改动

v1 只保存连续毫米手位窗口，没有音乐意义上的“第几把位”，并把每帧使用更少手指列为优化目标。这会奖励一指横按、让三品音长期使用 1 指，并在伴奏变化时让相同音在 1–4 指之间漂移。

v2 在 Oracle 的硬可行性之外加入确定性的上下文人体工学层：

- 离散把位：以 1 指通常所在品位表示，把空弦视为保持原把位；
- 自然指序：比较实际品位与 `position + finger - 1`，允许一品伸展，重罚更大的收缩/伸展；
- 连续性：相同弦/品的连续音换指产生明确负担；
- 换把：记录次数与品位距离，在小伸展和真实换把之间做有限选择；
- 横按：按覆盖弦跨度和所用手指计负担，不必要的横按会输给分指，标准 F 和弦的小横按仍可胜出；
- 同品跨弦：惩罚低音弦使用更高编号手指、同时高音弦使用更低编号手指的宽跨度交叉；
- 长谱连续性：分段搜索传递左手把位、物理手位、持续音、右手历史和 pessimistic 状态，不再在段首失忆；
- 有界性：每个形状只比较由最多四个手指推导的把位、均值把位和前一把位，没有遍历整条指板。

物理 Oracle 的相邻/隔指触及占位模型也从线性 `gap / 3` 改为单调的 `0 / 0.50 / 0.90 / 1.00` 手跨度。旧模型会把标准开放位 C、F 和弦判成 AMBER，同时把高把位替代按法判成 GREEN；`oracle@0.3.0` 已用标准和弦回归修正这一反例。GREEN 仍只是版本化模型证据，不是对所有真人的保证。

## 公开指法资料

生产模型没有使用 API 大模型或远程后训练。Plan 7B 以有许可、编辑完成的公开曲谱作为主要监督源，
不要求真人逐谱重新标注。

调研结果：

- [Mutopia 的 Carcassi Op.59 原始 LilyPond](https://github.com/MutopiaProject/MutopiaProject/blob/master/ftp/CarcassiM/O59/CarcassiMethodPreludes/CarcassiMethodPreludes.ly) 是 Public Domain，包含明确的左手 1–4 指、把位和横按标记；当前把完整 Prelude 1（含终止和弦）做成冻结对照集。
- [GuitarSet](https://zenodo.org/records/3371780) 是 CC BY 4.0，JAMS 标注包含逐弦 MIDI 音符、音高轮廓、和弦、节拍等，但没有左手 1–4 指标签；它不能监督本任务的手指编号。当前只从 train performers 的伴奏记录生成冻结的聚合节奏相位：Jazz 是直接统计，R&B 明确标为 Funk 邻近代理；原始 JAMS/音频不进入 agent prompt 或生成 target，dev/test performers 只用于分布审计。
- [PDMX](https://github.com/pnlong/PDMX) 提供大规模公版 MusicXML，但标准化表示不保留可直接训练的左手指号；原始 MXL 需要另行筛选 `<fingering>`、去重并遵循其许可冲突过滤建议，不能直接当作干净金标。

Plan 7B 闭门时冻结的 Public Domain 语料包含 21 个作品样本、3,483 个音符、707 行技术标注，
其中 689 行含明确的按弦手指 1–4。闭门后新增 35 份 Mutopia CC BY-SA 版本、37 个经语义选择的
乐章；仓库现共 58 个归一化样本、17,944 个音符、2,483 行技术标注，其中 2,362 行含明确的
按弦手指。缺失指号只表示未标注；作品、版本和作曲家分组不会跨 train/dev/test。

新增资料确实扩大了覆盖，但主要仍来自四位作曲家和同一位排版者。用三套语料重建的候选模型把
受支持窗口从 351 个增至 1,035 个、评分标签从 639 个增至 2,080 个，却在 composer-held-out
development/test 上全部 abstain，分别停在 29/60 与 30/78；因此没有替换生产模型。这个结果说明
“标签更多”有用，但不能代替作曲家、编辑版本和风格的多样性。完整审计见
[`2026-07-25-score-corpus-expansion.md`](experiments/2026-07-25-score-corpus-expansion.md)。

生产 `published-fingering-ranker@0.1.0` 仍只在完整
Oracle GREEN 候选池内比较 43 个通用人体工学与 finger/fret 计数特征，不读取曲名、作曲家、风格、
难度、调性或旋律身份。它在 held-out composer groups 上把 dev 从 29/60 提升到 30/60、test 从
30/78 提升到 32/78，Oracle 状态回退为 0；少于 4 个 onset 或少于 2 种 attack geometry 时直接沿用
规则解，且不能提高最高品位、增加 awkward events 或显著抬高左手负担。

完整 Prelude 1 的 21 个明确标注仍命中 17 个（81.0%）。四处差异都保留为真实误差：三个 D 音由
版本的 4 指变为求解器的 3 指，一个 C♯ 由 3 指变为 2 指。专家版本的选择可能服务于教学或后续乐句，
不应为了满分加入曲目特判。全曲在当前不确定性模型中为 AMBER（非 RED），主要来自持续低音上的
琶音跨度；这份对照只证明指号一致率，不把它写成 GREEN 或真人可弹保证。完整数据、模型 hash 与
评测见 [`PLAN7B_ACCEPTANCE.md`](PLAN7B_ACCEPTANCE.md)。

运行公开对照：

```bash
uv run python scripts/evaluate_left_hand_reference.py
```

## 验收覆盖

- 第一把位琶音：三品 C 使用 3 指、二品 E 使用 2 指、一品 C 使用 1 指；
- C 大调音阶：不再全用 1 指，得到连续的第一把位和后续换把指序；
- 重复按弦音：几何不变时不换指；
- 同品双音：可分指时不制造横按；
- 开放位 C / F：使用常见指型并在 `median@0.1` 下为 GREEN；
- 《欢乐颂》真实双声部导入形状：保持重复低音指法，F/G/D 等旋律音使用上下文指序，完整离线服务结果仍为 GREEN；
- Carcassi 长片段：跨有界分段传递上下文，段首不再跳到七品并造成 RED；
- 公版谱监督排序：只改完整 GREEN pool 的近似并列项，短片段与重复同形状片段自动 abstain；
- 音高、时值、非 RED、确定性与资源上限合同保持不变。
- 同一固定高音目标在 beginner / advanced 简化入口得到相同指法；两档只产生不同的 tier 验收结果。

## 证据边界与后续扩充

当前规则层是可解释的工程校准，近似并列候选再由公版专家谱统计排序。下一步数据扩充应优先增加
更多有许可的出版社、风格和分级体系，并继续按作品/版本/作曲家分组评测；无需为已有明确指号的谱子
重复人工校对。单一版本的指号不是唯一真理，真人只需抽样检验“舒适”“像某风格”等现实世界主张。
独立的手小、禁横按等演奏者偏好属于 player/technique profile，不得复用谱面难度档。
