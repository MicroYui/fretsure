# Fretsure —— 可证明可弹的吉他谱智能体（设计文档 / Design Spec）

> 产品名 **Fretsure**（fret + ensure，已定）。备选 PlayProof / Fretwright 仅存档。
> 状态（2026-07-17）：设计已锁定；Plan 1–5、受限 MusicXML、Oracle 0.2、安全 `.mxl`、Plan 6A 与 producer-driven MusicXML/IR 已闭门。strict MIDI input 的实现、exact producer corpus、repository/real-proxy/Web/distribution gates 与三轮独立 review 已完成，containing commit/push/SHA equality 的外部 Git receipt 已在 `46ff8ac` 关闭。当前组合树为 package=`0.5.0`、router=`score-input@0.1.0`、importers=`musicxml@0.3.0` / `midi@0.1.0`、faithfulness=`fidelity@0.3.0`、trace=`agent-trace@0.2.0`、service=`fretsure-service@0.2.0`、API=`fretsure-api@0.2.0`、MCP=`fretsure-mcp@0.2.0`、Web=`fretsure-web@0.2.0`；playability=`oracle@0.2.0`、公共输入=`tab-input@0.2.0`、container=`mxl-container@0.1.0` 保持不变，runtime 精确锁定 `music21==10.5.0`，默认真代理模型为 `gpt-5.6-sol`。当前按独立 benchmark-v2 计划重建版本化证据与配对消融；完整 Plan 6 的音频/琴颈/导出/live demo 仍 open。本文中的 target 数字不是实测结果。日期：2026-07-09。作者：solo founder + Claude。

---

## 0. TL;DR（一句话 + 定位）

**给它一首歌的音乐内容（乐谱/MIDI/和弦谱，或尽力而为的音频），Fretsure 用前沿 LLM 提议一版吉他编配，再用一个确定性 oracle 逐音把关"人手能不能弹"，弹不出来的当场定位并自动修复——最终产出一份在你指定难度/调弦/变调夹下"可证明弹得出来"的指弹或伴奏吉他谱，并配一套用 checker（而非 LLM 评委）打分的公开 benchmark。**

**能扛住敌意核查的唯一创新主张（moat claim）：**

> 第一个输出"人手可证明弹得出来"的吉他谱的系统——确定性 oracle 硬门卡住 fret span / 横按 / 换把 / 右手可行性，并在保住旋律/低音/和声的前提下**自动修复**任何弹不出来的地方，配一套**机器可检（非 LLM 评委）**的可弹性/难度/忠实度 benchmark。

**不主张**（这三样会被证伪，严禁写进创新点）：不主张"第一个做编配"、不主张"第一个出指法"、不主张"第一个 AI 吉他谱"。

**核心范式（agent 深度的落点，详见 §14）**：**oracle 当环境、LLM 当策略（policy）**。确定性 oracle/求解器/分析/忠实度 diff 是一套**工具/环境**；LLM 是一个**驱动"规划→用工具→读定位化诊断→定点编辑→重查"闭环**的策略。深度不在"图里有 LLM",在"LLM 驱动一个用可验证反馈自我纠错的回路",且**每个 agent 能力都用 leave-one-out 消融证明它挪动了 checker 打分的指标,否则砍掉**。

---

## 1. 背景与动机（为什么做这个）

### 1.1 市场现状（已核实，2026-07）
- **AI 音乐生成/编曲**在"生成"层面已很成熟（Suno、Udio、网易天音、Mureka、腾讯琴乐、ACE Studio…），但它们把可弹性/声部进行/可唱性当**软目标**，**不保证**，需人工返工。
- **音频/谱 → tab 转谱**成熟且拥挤（Klangio、Songscription、Songsterr、爱扒谱、来音、酷狗、TME…），但它们**只照抄弹了什么，不编配、不保证可弹**。
- **自动指弹编配**几乎无成熟产品。唯一像样的商业尝试 **TemPolor Melo-D**（AI 吉他硬件+app，2026-09 出，[链接](https://tempolorguitar.com/products/melo-d)）做"哼唱→指弹独奏"，**输出形态与本产品一致**，但据 guitar.com 上手评测其输出"过度堆砌、无法演奏"——**恰恰因为它没有可弹性 oracle**。这是**验证而非威胁**。
- **硬门可弹性 / 可验证难度简化 / 修复回路**：**零产品**。notave 喊"永不给弹不了的按法"但只是启发式 + "请自行检查"；爱扒谱只给"指法推荐"（建议非保证）；学术界（Fretting-Transformer、MIDI-to-Tab）把可弹当**软**损失。

### 1.2 诚实的先例（prior art，必须内化，不可回避）
- **概念前身（学术）**：SMC 2024 id55 "Tablature Generation from Lead Sheets for Finger-style Solo Guitar"（Sakai 等，[PDF](https://smcnetwork.org/smc2024/papers/SMC2024_paper_id55.pdf)）——lead sheet → 指弹独奏，用 Viterbi 在 ~3658 个预筛可弹形态里搜。**它靠"限制状态空间"保证可弹，无修复、无难度、无右手 p-i-m-a、无伴奏、非产品。** 我们的差异化在于 **oracle + 修复 + 难度 + 双输出 + checker benchmark 的工程系统**，不在"想到给独奏吉他编配"。
- **相邻积木**：指法求解器（A*、research）、指法启发式（notave、GrabTab、爱扒谱）、难度简化（Ultimate Guitar "Simplify"，人工）、编配（TemPolor、人工）。**都存在，但没人拼成硬门+修复+基准的整体。**
- **相邻验证器（跨域）**：Woolfy（生成回路卡平行五度）、THIRI（给 LLM agent 用的确定性乐理 MCP）、CLARA（纯确定性可弹编排、无 LLM、无 tab）。**它们做和声/理论，不做 tab 可弹性。**

### 1.3 真实用户痛点（已核实，最硬的一条腿）
- 厂商自认："AI-generated guitar tabs often need cleanup"（Note2Tabs）；FretBench 测出多数前沿 LLM 连**读** tab 都不准（Claude Opus ~60%）。
- "如何修 Suno/AI tab"是一整个内容品类；中文社区吐槽编曲模板化、咬字、要二次加工。
- TemPolor 被公开吐槽输出弹不了——需求侧被反复证实。

### 1.4 结论
本产品的**具体组合**（LLM 提议 → 确定性指法求解 → 硬门可弹性 oracle → 自动修复 → 伴奏+指弹双输出 → checker 打分 benchmark，且符号优先输入）在 2026-07 **作为成熟产品无人占领**。moat = 执行力 + 先发 + oracle/修复/benchmark 工程，**不是点子**。

---

## 2. 目标与非目标

### 2.1 三个并行目标（取胜函数）
1. **大厂 agent/copilot 岗作品集**：展示"LLM 提议器 + 确定性验证器 + 修复回路 + 严谨 checker 基准 + 可选 RL"的完整 agent 系统。
2. **内部 Copilot 部门评比（混合评审）**：可看可听可懂的爆点 demo（指板标红→修复→绿→播放）+ 站得住的硬指标。
3. **可真上线的产品**：认真学习者/老师能用它拿到"按自己水平弹得出来"的谱。（TAM 小众、价格敏感，按**热情小众的高端工具**建模，非大众 app。）

### 2.2 非目标（防 overclaim / 防 scope 蔓延）
- **不**主张发明验证 / 编配 / 指法（见 §0）。
- **不**保证"好听 / 有品味 / 编得漂亮"——只保证"弹得出来且忠于原曲"。品味交给 LLM 提议 + 用户选择。
- **不**做音频生成 / 歌声合成 / 完整 DAW。
- **不**保证音频转谱正确（audio 前端标注为 best-effort，非保证路径）。
- **不**追求大众市场。

### 2.3 HERO 与优先级（已与 founder 锁定）
- **HERO = 可证明可弹的指弹独奏**（痛点最显、最难抄、demo 最炸）。
- **商业楔子 = 可验证的难度简化**（"把这首歌简化到你能弹的水平"）。
- **标配（做但不主打）= 伴奏谱**（和弦按法 + 扫弦/分解节奏型）。

---

## 3. 用户与用例

| 用户 | 用例 | 为何非本产品不可 |
|---|---|---|
| 认真的吉他学习者 | "我想弹《XXX》,但网上谱子太难/找不到指弹版" → 出一份**我这个水平弹得出来**的指弹谱 | 免费谱库要么没有、要么难度不对、要么弹不了 |
| 吉他老师 | 给学生按周次/水平定制**可弹**练习曲 | 手工编配耗时;现有 AI 编配不保证可弹 |
| 唱作人 | 给自己的旋律配一版**能弹**的伴奏 | 生成式工具给的是黑盒音频,不是能照着弹的谱 |
| 指弹爱好者 | 把喜欢的旋律改成**能弹**的独奏 | TemPolor 类输出弹不了 |

---

## 4. 系统架构（端到端）

```
┌──────────────┐   ┌───────────┐   ┌──────────────┐   ┌──────────────────┐   ┌────────────┐
│ 输入解析     │→ │ 音乐 IR   │→ │ LLM 编配提议 │→ │ 指法求解器       │→ │ 可弹性 oracle │
│ Input Parse  │   │ Music IR  │   │ Arranger     │   │ Fingering Solver │   │ Playability  │
│ (符号=保证,  │   │ (统一表示)│   │ (选音/声位/  │   │ (弦,品,左手指,  │   │ Oracle       │
│  音频=尽力)  │   │           │   │  织体/难度)  │   │  右手指)         │   │ (硬门+定位)  │
└──────────────┘   └───────────┘   └──────────────┘   └──────────────────┘   └──────┬──────┘
                                          ↑                                          │ RED/AMBER: 定位到帧/拍+违反项
                                          │                                          ▼
                                   ┌──────┴───────────────────────────────────┐  ┌────────────┐
                                   │ 修复回路 Repair (verifier-guided search)  │← │  否决      │
                                   │ 换声位/换把位/掉内声部/简化,保旋律&低音   │  └────────────┘
                                   └──────────────────────────────────────────┘
                                          │ GREEN
                                          ▼
┌──────────────────────┐   ┌──────────────────────────────┐   ┌────────────────────────────┐
│ 难度模型 Difficulty  │   │ 渲染/导出 Render/Export      │   │ Benchmark（moat/主角）     │
│ (tier 规则 + 可验证) │   │ AlphaTab tab + 指板动画 +    │   │ checker 打分,非 LLM 评委   │
│                      │   │ FluidSynth 播放 + GP/XML/MIDI│   │ pass^k/忠实度/难度/成本 CI │
└──────────────────────┘   └──────────────────────────────┘   └────────────────────────────┘
                                                                        │
                                                              ┌─────────┴──────────┐
                                                              │ RL stretch 模块    │
                                                              │ 小本地策略,学习曲线│
                                                              └────────────────────┘
```

**组件边界原则（可隔离、可独立测试）**：每个单元有清晰接口、可单独测试;**oracle 与求解器是纯确定性函数** → 单测即"benchmark 的 checker"。

---

## 5. 各组件详细设计

### 5.1 输入解析 Input Parser
- **目标保证路径（符号）**：MusicXML / MIDI / MusicXML-lite / lead sheet（旋律+和弦符号）/ 纯和弦谱。这里是目标集合，不表示当前全部实现。
- **当前 `musicxml@0.3.0`**：安全 envelope + fail-closed 完整 raw 语义预检后，才重建一个 bounded event-only XML 交给精确锁定的 **music21 10.5.0**（BSD-3）做交叉验证；第三方只接收 divisions、harmony root/kind 与 note/rest/duration/tie，credit、instrument/MIDI、layout/print、lyrics/voice、key visual metadata 和合法额外 non-note-bearing part 不进入该边界。支持 MusicXML 3.1/4.0 `score-partwise` 的未压缩 `.musicxml`/`.xml`，以及由 `mxl-container@0.1.0` 有界校验、全内存解压和逐 member size/CRC/完整性核验后选出的唯一 `.mxl` root。语义仍限单 note-bearing part/staff/voice、普通 note/rest/tie、全曲固定的 bounded XSD-decimal divisions 与 decimal duration、固定传统 key、4/4、1–1000 BPM quarter tempo 与白名单 root+kind harmony。显式 major/minor 维持原 key；MusicXML 4.0 traditional key 省略 `<mode>` 时保留 `key-signature:fifths=N;mode=unprovided` 并发 located `KEY_MODE_UNPROVIDED`，不从音符、和弦、spelling 或 music21 猜 mode。MusicXML 3.1 省略 mode、空/其他 mode、重复权威 scalar、错误 key shape 与 key change 继续拒绝；外部资源、权威语义数值字段中的非 ASCII/XSD 值、location/diagnostic amplification 与派生 Fraction 均在 adapter 前有界失败。raw exact event timeline 是权威，music21 只做逐事件语义交叉验证；每个 success 还须满足 256-bit Fraction 的 public MusicIR snapshot；`.mxl` 不扩语义。复调、多 note-bearing part/staff/voice、导航/重复、pickup、变拍/变调/变速、复杂 harmony/技巧与 audio 均延后并 typed fail-closed。
- producer 证据只覆盖 manifest 中未经手改、精确冻结的 artifacts：music21 10.5.0、musicxml 1.6.1 与 MuseScore Studio 4.7.4 XML/MXL rows。它不证明任意 MuseScore 4.7.4 乐谱、其他版本或完整 MusicXML 兼容；逐文件 census 与限制见 [`2026-07-16-producer-musicxml-census.json`](../../experiments/2026-07-16-producer-musicxml-census.json)，闭门证据见 [`PRODUCER_MUSICXML_ACCEPTANCE.md`](../../PRODUCER_MUSICXML_ACCEPTANCE.md)。
- **当前 `midi@0.1.0`**：`score-input@0.1.0` 只把 `.mid/.midi` 路由到 strict SMF importer。第一方 parser 在任何 music21/pipeline/LLM 工作前验证 format 0/1、PPQN、chunk/EOF/VLQ/running status/EOT、资源门、单一非打击乐单声部 note stream 与固定 tempo/4/4/key/event allowlist；raw tick/PPQN 是权威。零 error 后只重建最小 canonical SMF 给 music21 10.5.0、`quantizePost=False` 逐 note 交叉验证。所有 note 固定为 melody、`chords=()`；不猜 track role、bass/chord/key、量化或 notation timing。10 MiB/64 tracks/250k events/20k notes/tick `2**31-1`/PPQN 32767/note-track EOT 4096 quarter notes/text/VLQ/diagnostic 门先于对象放大；EOT span 同时约束 leading rest、note duration 与 trailing silence，防止 music21 稀疏小节放大。MuseScore 4.7.4 melody-only exact positive 保留 7 beats 与每音 1 tick release gap，music21 10.5.0 positive 保留 8 beats；两个 harmony-realized exact rows typed 拒绝，不声称跨 producer IR equality 或通用 MIDI 兼容。证据见 [`2026-07-17-midi-census.json`](../../experiments/2026-07-17-midi-census.json) 与 [`MIDI_ACCEPTANCE.md`](../../MIDI_ACCEPTANCE.md)。
- **尽力路径（音频,v2）**：mp3/wav → 转谱（旋律+和弦+节拍）。候选免费工具：Spotify **Basic Pitch**、librosa 节拍/和弦识别。**明确标注"近似、需校对、不保证"**;提供校对 UI。转谱错误不计入产品的"保证"。
- 输出：统一 **Music IR**。

### 5.2 音乐 IR（Music Intermediate Representation）
统一、可版本化的中间表示,后续所有组件基于它工作:
```python
@dataclass(frozen=True)
class Note:      # 一个音符
    onset: Fraction        # 以拍为单位的起始
    duration: Fraction
    pitch: int             # MIDI number
    voice: Literal["melody", "bass", "harmony"]  # 声部角色（关键:决定修复时哪些必须保）
@dataclass(frozen=True)
class ChordSymbol:      # 和弦标注
    onset: Fraction
    symbol: str
    pitch_classes: frozenset[int]
    root_pc: int
@dataclass(frozen=True)
class Meta:
    key: str
    time_sig: tuple[int, int]
    tempo_bpm: float
    source: str
    title: str
    license: str
    duration_beats: Fraction | None = None
@dataclass(frozen=True)
class MusicIR:
    notes: tuple[Note, ...]
    chords: tuple[ChordSymbol, ...]
    meta: Meta
```
- **不变量**:每个 Note 都带 voice 角色;melody = 必须保留的最高声部;bass = 尽量保;harmony = 可增删。公共入口只接受上述 exact frozen dataclass/tuple/frozenset 形状，并在使用前做有界深快照（20,000 notes + 20,000 chords、10 Mi 文本、256-bit Fraction、tempo 1..1000、拍号分子/分母 1..32 / 1..64）。

### 5.3 LLM 编配提议器 Arranger（LLM = proposer,不可信）
- **职责**:给定 IR + 目标（指弹/伴奏、难度 tier、调弦、变调夹、风格）,**提议**一版吉他编配:
  - 选哪些音进独奏（旋律必留、低音走向、内声部填充密度）;
  - 声位/音区/织体（分解 or 扫弦、bass-melody 交替 pattern 等）;
  - 难度取向（初学=稀疏、首把位）。
- **输出格式**:结构化 JSON（每拍/每帧的目标音集 + 声部角色 + 建议 pattern）,**不直接输出 tab**——tab 由确定性求解器 + oracle 决定。
- **模型**:GPT-5.6 Sol via API（canonical id `gpt-5.6-sol`）。可 best-of-N（多次提议,取 oracle 通过且 cost 最低者）。
- **关键设计**:LLM **只决定"音乐意图"**,不决定"手怎么按";把"能不能弹"完全交给下游确定性层。这样 LLM 的幻觉被 oracle 兜住。

### 5.4 指法求解器 Fingering Solver（确定性）
- **职责**:把 IR 的每个音符分配到 `(string, fret, left_finger, right_finger)`,使所有硬约束成立,并最小化代价（手部移动、舒适度）。
- **候选生成**:每个 pitch 在给定 tuning/capo 下可落在多根弦的多个品 → 候选 (string, fret) 集。
- **算法**:帧级 DP / Viterbi。
  - 状态 = 一帧（同时发声的音）的一个可行 (string,fret,finger) 指派;
  - 帧内可行性 = 左手手指指派（含横按）满足几何约束（见 §5.5),用小规模搜索/二部匹配判定;
  - 转移代价 = 相邻帧手位移动 + 换弦 + 保持音（sustain）冲突;
  - 目标 = 最小化总代价。
- **可用库**:自研为主;可选 OR-Tools CP-SAT 处理帧内指派与硬约束。
- **输出**:完整指法方案,或"在给定音集下无可行解"（触发修复）。

### 5.5 可弹性 Oracle（★核心 IP，硬门 + 定位）

> **权威详版见 §14 Part A.7–A.8**：毫米几何模型（用品数跨度是错的）、GREEN/AMBER/RED 三态、语义化版本 profile、"谁来检查检查器"的验证台。下为约束概览。
> **当前实现边界（Oracle 0.2）**：`tab-input@0.2.0` 在任何 predicate 前严格验证六弦 Tab、profile、tempo 与资源域；有效判决盖 `oracle@0.2.0`、profile version + canonical SHA-256 与 input schema。全部 active sounding notes 参与左手几何；同弦半开 sustain overlap 单独拒绝；换把用 release-before-attack 事件流传播连续 reachable hand-centre interval。solver 是有明确 work envelope 的 bounded search，返回 Tab 仍须过完整 oracle；`Infeasible` 不证明数学无解。
返回 `GREEN` / `AMBER` / `RED` 与定位化 diagnostics（frame、beat、violated constraint、notes）。约束分三类,**全部参数化**（hand_span、skill tier、tuning、capo、tempo）:

**A. 左手几何（fretting）**
- `range`:每个音在该弦音域内 `open_pitch(string,tuning,capo) ≤ pitch ≤ open_pitch+MAX_FRET`。
- `fret_span`:一帧内被按品位的 `max_fret − min_fret ≤ MaxSpan(hand_span, position)`(高把位更窄)。
- `finger_count`:被按音（非空弦）可指派给 ≤4 根左手指;同品多弦须**横按**(一指),横按可行性单独判(该指须为最低把位、其下无更低品需求)。
- `finger_monotonic`:手指 1<2<3<4 映射到非降品位(不交叉,默认硬约束、可放宽为软)。
- `one_string_one_note`:同一帧一根弦只发一个音。

**B. 右手可行性（fingerstyle p-i-m-a）**
- `pluck_assignment`:一个拨弦瞬间,每根被拨弦由不同右手指(p/i/m/a)负责,不可一指同时拨两弦;p(拇指)负责低音弦、i/m/a 负责高音弦,弦序单调。
- `simultaneous ≤` 可用右手指数。

**C. 时间/换把（temporal）**
- `shift_feasible`:相邻帧手位移动距离 / 可用时间(由 tempo 决定) ≤ `MaxShiftSpeed`;快速大跳 = 不可行。
- `sustain_hold`:需保持发声的低音,其左手指须持续按住,不得与旋律所需手指冲突。

**D. 难度 tier 约束**(见 §5.7)叠加为额外硬约束。

> **oracle 只判版本化模型内的可弹性，不判忠实或好听。** `fidelity@0.3.0` 是独立、availability-aware 的来源忠实度门；缺 source evidence 的维度是 N/A，不是 1.0。GREEN 可以同时 fidelity FAIL/REVIEW。品味仍是另一条、尚未获真人校准的轴。

### 5.6 修复回路 Repair（★真正的护城河，verifier-guided search）

> **权威详版见 §14 Part B**：修复是 agent 脊柱——LLM 读 oracle 的定位化类型诊断、推理音乐取舍、下定点编辑、重查到不动点（agentic），而非固定优先级算法；用消融 R3 证明它比固定算法挣得存在，打平则回退。下为算子概览。
当 LLM 提议的音集**即使最优指法也不可行**时,按**音乐代价从小到大**尝试编辑算子,每步用 oracle 验证:
1. `revoice`:harmony 音换八度/转位(保 pitch class,换音区)。
2. `reposition`:同音、换 string/fret 指派(求解器已探,repair 放宽到备选)。
3. `drop_inner`:删一个 harmony 内声部(保 melody + bass + 和弦根/三音)。
4. `simplify_rhythm`:减少同时发声音数。
5. `substitute_voicing`:同和弦换更易按声位。
- **恒保约束**:melody(最高声部)**永远保留**;bass 根音尽量保;和弦身份(pitch-class 集)尽量保或最小改动。
- **搜索**:在编辑算子上做有界搜索(A*/beam),目标 = 最小化对原曲的偏离(faithfulness cost),约束 = oracle 可行。
- **可解释**:每次修复产出"改了什么、为什么(违反哪条)、代价多少"——供 UI 展示和 benchmark 统计。
- **这是工程最难、也最防抄的部分,预算要给足。**

### 5.7 难度模型 Difficulty（★商业楔子，可验证简化）
- **tier 定义**(如 Beginner / Intermediate / Advanced),每档规定允许的:最大 fret_span、是否允许横按、把位范围(初学=首把位)、每小节最大换把数、最大同时发声音数、tempo 上限、技巧(击弦/勾弦/滑音)。
- **可验证简化**:目标 tier 的规则集叠加进 oracle 作为硬约束;**输出必须通过该 tier 的 checker** → 这就是"没人做的可验证难度简化"。
- **忠实度 vs 难度权衡**:越简单的 tier 保 melody、掉更多 harmony;benchmark 量化这条曲线。

### 5.8 渲染 / 导出 Render/Export
- **前端渲染**:**AlphaTab**(MPL-2.0,浏览器渲染 tab、可播放、可导入导出 GuitarPro/MusicXML) + **自研指板动画**(把 (string,fret,finger) 动到指板上,红/绿标注可弹性)。
- **音频**:**FluidSynth**(LGPL) + **GeneralUser GS soundfont**(允许商用) 做 MIDI→WAV 播放。
- **导出**:GuitarPro(.gp)、MusicXML、MIDI、ASCII tab → 与现有软件(Guitar Pro/TuxGuitar/MuseScore)互操作。

### 5.9 落地形态（已与 founder 锁定）
- **主打:独立 web app**(React/TS + AlphaTab + 指板动画),present 时对 UI/卡拉OK高亮/基准计分板完全可控。
- **互操作:导出 GP/MusicXML/MIDI**,坐实"能进你现有工作流"。
- **插件降级为可选**(tab 场景 AlphaTab 比 MuseScore 插件更合适)。

---

## 6. Benchmark（★moat，present 的主角）

> **权威详版见 §14 Part A（语料5层/污染控制/指标公式/checker-vs-judge/谁检查检查器/统计/复现）与 Part C（消融矩阵 + 两个头牌结果）。** 下为概览。

> "benchmark 本身才是你唯一能真正拥有的资产。" 它必须 **checker 打分、可复现、一条命令跑通**。

### 6.1 语料（化解"无真实数据"约束）
- **符号语料**:公有领域 lead sheet / MIDI(民谣、赞美诗 Hymnary、公有古典、MuseScore 公开谱、Wikifonia 类)。
- **自标注**:**可弹性是自标注的——checker 即 label**;忠实度 ground truth = 输入的 melody/harmony;难度 ground truth = tier 规则集。
- **程序合成**:可程序化生成受控 lead sheet + 注入难度/风格,得到无限带标语料。

### 6.2 指标
- **Playability pass rate**(输出 tab 完全通过 oracle 的比例)——头牌;**pass^k**(k 次重生成的可靠性)。
- **Faithfulness**:melody 保留(音符级 F1 vs 输入旋律)、harmony 保留(pitch-class recall)、bass 保留。
- **Difficulty accuracy**:输出是否符合请求 tier(checker) + 与人感难度的相关性。
- **Repair efficacy**:不可行提议被修成可行且忠实的比例;编辑距离。
- **Cost**:每份成功 tab 的 tokens/$、延迟。
- **checker vs LLM-judge**:证明**确定性 checker 与 LLM 评委不一致**(LLM 评委会误判弹不了的 tab 为"能弹")——这是"为什么必须确定性"的头牌结果。

### 6.3 Baselines 与消融
- 前沿 LLM 原始 prompt→tab（GPT-5.6 Sol）;
- 转谱工具(照抄,不编配);
- TemPolor(若能取到输出);学术 id55(若可复现);
- **消融**:去 oracle / 去 repair / 去难度约束 / dictionary-only(模仿 id55)。

### 6.4 统计严谨
- Wilson 置信区间;配对比较用 McNemar;按难度/风格分层报告 mean±std。

---

## 7. RL Stretch 模块（可选招牌，CPU/24G 可落地）

- **动机**:founder 想要"本地小模型 RL 学习曲线击败前沿"作为 stretch 加分;此处**奖励稠密确定性**(oracle),是四个候选里 RL 最能在 CPU 出曲线的地方。
- **形态(二选一或都做)**:
  1. **学习型指法/编配代价**:小策略(pointer-net / 小 transformer <100M,或非神经的 learned cost weights / bandit)指导求解器或提议器;
  2. **小提议器策略**:用 oracle 奖励(可行 + 忠实 + 低代价)训一个小本地模型,产出**学习曲线**(playability pass@1 / faithfulness vs 训练 episode),理想以极低成本越过前沿 LLM 原始提议线。
- **诚实附注**:CPU/24G 训练慢,用很小的模型 + 耐心(founder 接受延期);定位为"**小专用策略学会在可弹性上以 ~1/Nx 成本超过前沿原始提议**",不吹"纯 RLVR 微调大模型"。
- **若 RL 出不来**:核心产品(oracle+修复+benchmark)**不依赖它**,仍完整。

---

## 8. 技术栈（全免费、许可证干净）

> **Agent-harness 层见 §15 Part D**（★**自研编排回路/状态/trace/eval**;DSPy/GEPA 与 MCP 保留、消融把关;LangGraph/Claude Agent SDK 仅作对照基准）。下表为底层音乐/渲染/服务栈。

| 层 | 选型 | 许可证 | 用途 |
|---|---|---|---|
| 乐理/解析 | music21 | BSD-3 | MusicXML/MIDI 解析 + 乐理原语 |
| oracle/求解 | 自研 + numpy/networkx(+可选 OR-Tools CP-SAT) | 自有/Apache | 可弹性判定 + 指法求解 + 修复搜索 |
| 后端服务 | Python + FastAPI | MIT | oracle/agent 服务 |
| 前端 | React/TS + Vite + **AlphaTab** | MIT/MPL-2.0 | tab 渲染/播放/GP 导入导出 |
| 指板动画 | 自研(SVG/Canvas) | 自有 | (string,fret,finger) 动画 + 红/绿标注 |
| 音频播放 | FluidSynth + GeneralUser GS soundfont | LGPL / 允许商用 | MIDI→WAV |
| 音频转谱(v2) | Basic Pitch + librosa | 宽松 | best-effort 前端 |
| LLM | GPT-5.6 Sol (API) | 商用 | proposer |
| RL(stretch) | PyTorch(CPU) 小模型 | BSD | 学习曲线 |
| 存储(可选) | Postgres | - | 存 run/benchmark 结果 |

**许可证雷区**:歌词场景才会用到的 phonemizer/espeak 是 GPL-3.0——本产品**无歌词、不涉及**,无风险。

---

## 9. 演示（present：功能 / 使用 / 性能，三段都覆盖）

> **上台详版见 §15 Part E**（money moment：观众点歌→oracle 标红→agent 修复→真人弹出；~7 分钟脚本；"看 agent 思考"trace viewer；指板红绿动画；公平 A/B；live 榜单）与 **Part F**（可展示=真功能映射）。下为概览。

1. **功能展示**:粘贴一段 lead sheet / 哼一段(v2) → 选"指弹、标准调弦、初学者" → 得到带绿色"100% 可弹"徽章的 tab + 指板动画 + 播放。
2. **现场使用**:评委点一首歌 + 难度 → 现场生成 → **展示 oracle 抓到第 X 小节不可行(指板标红) → 自动修复 → 转绿 → 播放**。
3. **性能对比**:同一首歌,**前沿 LLM 原始生成的 tab 在指板上标红(手够不着)**,我们的绿;并排 TemPolor 片段;然后 **benchmark 计分板**:playability pass rate、pass^k、faithfulness、cost、checker-vs-LLM-judge,全带置信区间;再放 **RL 学习曲线越过 baseline**。

---

## 10. 里程碑（质量优先、可延期；顺序可调）

> **硬顺序（见 §14 A.14 / C.2）**：**先把 oracle 验证台建好、误接受率混淆矩阵领跑**——第 4 步（oracle 验证）通过前，下游一切 benchmark 数字不可信。M1 的验收因此**必须包含 §14 A.8 的 checker 自验证**（property/metamorphic/mutation/N-version + DadaGP·GuitarSet 差分 + 人金标混淆矩阵），而不仅是"单测"。每个 agent 能力（M2/M4/…）都以其 **leave-one-out 消融过 CI** 为验收门,否则砍。

| 里程碑 | 主题 | 验收标准 |
|---|---|---|
| **M0** | 最短纵切:符号 lead sheet → LLM 提议 → 求解器 → oracle(约束子集) → 渲染一份可弹指弹 tab(单风格) | 端到端跑通、确定、单个手造样例通过 |
| **M1** | 完整可弹性 oracle(左手+右手+时间)+ 求解器,充分单测 | oracle 在手标集上判定正确;求解器对可解样例给出可行解 |
| **M2** | 修复回路(verifier-guided) | 不可行提议中"修成可行且忠实"的比例显著高于"仅去 repair"的消融(阈值在 M3 baseline 出来后锁定),带可解释日志 |
| **M3** | **Benchmark 骨架**(合成语料+baselines+指标+CI)——尽早,因为它是主角 | 一条命令跑出可复现计分板;checker-vs-LLM-judge 结果成立 |
| **M4** | 难度 tier + 可验证简化 | 输出经 checker 证明符合所选 tier;忠实度-难度曲线 |
| **M5** | 伴奏谱输出(标配) | 和弦按法+节奏型,同样过 oracle |
| **M6**(stretch) | RL 小策略 + 学习曲线 | 学习曲线上升并(理想)越过前沿原始提议线 |
| **M7** | 音频前端(best-effort)+ web UI 打磨 + 指板动画 + demo 脚本 | present 可用的完整体验 |

---

## 11. 风险与缓解（诚实清单）

| 风险 | 缓解 |
|---|---|
| **TAM 小众、价格敏感、免费谱多** | 按"热情小众高端工具"建模;付费楔子=学习者/老师要的"可弹+难度对"的谱 |
| **点子可复制;TME/酷狗/网易/TemPolor 会趋同** | moat = benchmark 严谨 + 速度 + 诚实定位;把 benchmark 做成参考基准 |
| **repair 比 gate 难得多** | 这正是护城河所在,预算与设计重点放这里;先小算子集、逐步扩 |
| **生物力学主观(手大小、拇指绕)** | 全参数化(hand_span/skill/tuning);对真实琴手校准;保证限定在"我们公布的模型下" |
| **RL 在 CPU 出不来曲线** | 保持 stretch;用很小模型;核心产品不依赖它 |
| **音频转谱噪声** | 符号优先=保证路径;audio 标注 best-effort、v2 |
| **LLM 提议方差大** | oracle 兜底,这本就是设计目的;best-of-N |
| **被质疑"发明了校验"** | 严守 §0 的窄主张,主动引用 SMC id55 / notave / TemPolor 作对照 |
| **agent 含量被读作"经典求解器+LLM 点缀"** | §14 Part B 反转为 oracle-as-environment / LLM-as-policy;每个能力用 leave-one-out 消融挣存在,**公开砍掉的组件**;头牌#1 的"无廉价补救对照"证明蛮力采样买不到 pass^8 |
| **过度工程 agent LARP** | 见到即砍(>2 角色/纯编排 agent/NL 消息传递/复述式规划);组件挣存在 iff 留出曲消融挪动 checker 指标 |
| **oracle 有效性天花板(生物力学模型≠普适可弹)** | §14 A.8 混淆矩阵 + 敏感性扫领跑;偏 GREEN soundness;范围诚实声明(静态几何、不含疲劳/音色) |
| **忠实度作弊(削音过可弹)** | 可弹×忠实**联合 Pareto** + **保忠实修复率**当主指标护栏 |

---

## 12. 成功标准（对齐三目标）

- **作品集**:干净仓库 + 一条命令可复现的 benchmark + 写清"LLM 提议器 + 确定性验证器 + 修复回路 + RL"的技术文章 → 能拿到 agent 岗面试的 artifact。
- **评比**:一个可视化"击败前沿 + 修复"现场 demo + 站得住的指标故事。
- **上线**:吉他手能用它拿到"按自己水平弹得出来"的谱;学习者/老师的高端付费楔子。

---

## 13. 待定/延后决策（不阻塞开始）

1. ~~**产品定名**~~ → **已定：Fretsure**（fret + ensure，"保证可弹"）。备选 PlayProof/Fretwright 存档。
2. **首发风格/曲库范围**(先民谣+流行指弹?)——M0 前定 1 个风格即可。
3. **难度 tier 具体参数**(span/把位/tempo 阈值)——需对真实琴手校准,M4 前定。
4. **RL 具体形态**(学习型代价 vs 小提议器)——M6 前定。
5. **是否要真人 design partner**(一个吉他老师/琴手)校准 oracle 与难度——能显著降低"合成基准不真实"的质疑,建议尽早找。

---

## 附:一句话对外定位（营销用，已过敌意核查）

> "Suno 给你一首**弹不了**的歌;Fretsure 给你一份**人手可证明弹得出来**的谱——AI 提议、确定性引擎逐音把关并自动修复,并在公开 benchmark 上用机器(而非另一个 AI)证明它真的能弹。"

---

## §14 附录：Benchmark、Checker、Agent 深度（权威详版，覆盖并细化 §5.5/§5.6/§6/§10）

> 本节是 benchmark/checker/agent 内核的**权威版本**;正文 §5–§6 的对应处以本节为准。"target 数字"是待测的设计目标,不是已有结果。

### Part A — Benchmark & Checker（可落地）

**A.0 统领一切的诚实规则**：可弹性是**物理/几何事实**,故用**确定性 checker(oracle)打分,绝不用 LLM 评委**。但 checker 只在**给定手模型**下认证可行;所有对外主张都限定在该模型,且该模型对真实琴手校准(A.8)。**oracle 没验证之前,下游一切数字不可信 → 先建并验证 checker。**

**A.1 语料（零专有数据，5 层）** 全部符号输入:
- **A 真实公有领域 lead sheet**(旋律+和弦):Enhanced Wikifonia(~5k,逐文件许可审计)、Nottingham、thesession.org。
- **B 公有古典**(更丰富的低音/和声 ground truth):Mutopia、CPDL/ChoralWiki、Hymnary 公有赞美诗。
- **C 大规模 MIDI**(逐文件核 provenance):Lakh MIDI。
- **D 真人演奏吉他谱——只用于验证 checker,绝不作 agent 输入**:DadaGP(~26k GuitarPro)、GuitarSet(360 条带弦/品标注的录音)。人真弹过=已知可弹。
- **E 程序生成曲（★测试集皇冠）**:功能和声文法采样 调/拍/乐句/和弦进行 + 受和弦音+经过音约束的旋律。**这些曲从没存在过→LLM 不可能背过其 tab;且旋律/低音/和声 ground truth 天然精确。**

统一用 music21/MusPy 归一成 JSON note-graph:`{onset,duration,midi_pitch,voice_role∈{melody,bass,inner},chord_segment}`。附 datasheet + 许可审计。

**A.2 切分与污染控制**:**按曲**切分(不按小节)。前沿 LLM 背过 UG 名谱,故:(a)**程序生成层(E)当主测试**——构造上不可污染;(b)真实曲变调到怪调/改速/换声位;(c)偏冷门公有作品;(d)埋 canary 串,grep 输出查泄漏。**每个指标分"真实公有领域层 vs 程序生成层"报,差距=记忆效应估计,本身是头牌发现。** 封测只走 zero-data-retention 端点;发布种子+切分清单,可从 seed+下载脚本重建。

**A.3 三个任务(各有精确 I/O 契约)**:
- **T1 指弹独奏(HERO)**:lead sheet → 单吉他承载 旋律+低音+和声填充,逐音 (string,fret,finger)+右手 p-i-m-a。标准调弦默认;drop-D/变调夹变体。
- **T2 伴奏**:lead sheet → 可弹扫弦/分解伴奏;评 和声/低音忠实 + 律动可行,不评旋律承载。
- **T3 难度定向简化**:难编配 + 目标档 d∈{1..5} → 简化编配,实测难度≈d 且尽量保旋律/和声。

**A.4 ground truth——自标注 vs 需人**:
- **自标注(无人)**:可弹性(oracle 即 label,合法因为是物理谓词);忠实度(对符号源精确计算,程序生成输入下完美)。
- **需人(有界,建一次复用)**:musicality(~40 条×3 人 MOS + 盲 A/B);难度校准(专家排 ~150 条一次,拟合 learn-to-rank);**checker 金标集(~300 条分层,一名琴手逐条实弹 ~2–3 小时)**。**总常备人力 ≈ 每次大改 <1 天。**

**A.5 忠实度指标（目标规格）**：目标按 voice-role 用 DTW((onset,pitch)) 对齐；DTW 与 1/16 网格宽松匹配尚未实现。当前 `fidelity@0.3.0` 保留 melody/bass exact-onset 与 active chord-segment harmony Jaccard，并增加 nullable scores、evaluated/unavailable dimensions 与可重算 passed；旧 benchmark 尚未按 0.3 重跑，详见 `docs/BENCHMARK_RESULTS.md`。
- **Melody-F1** = recall 与 precision 的调和平均,匹配需 MIDI 音高精确 + onset 在 1/16 网格内;另报八度等价宽松版 + 音高误差直方图。
- **Bass-root-accuracy** = 强拍上"编配最低发声 pitch-class = 源和弦根/记谱低音"的比例。
- **Harmony-Jaccard** = 逐和弦段"编配 pitch-class 集 vs 源和弦 pitch-class 集"的平均 Jaccard。
- **忠实度门** = (Melody-F1≥τm) AND (Bass-acc≥τb) AND (Harmony-Jaccard≥τh),阈值事先公布。
- **关键**:可弹性与忠实度**联合(Pareto)报**——弹得了但丢了旋律=失败。这挡住"靠简化成空弦来过关"。

**A.6 指标**:playability pass rate + **Wilson 95% CI**;**pass@k**(k 次中至少一次过,含修复回路时相关)AND **pass^k**(k 次独立全过——"可证明可弹"要的可靠性,用 HumanEval 式无偏估计,n≥10/条);**联合成功=可弹 AND 忠实度门**(主头牌数);难度准确(MAE/±1/Spearman);**修复效率**(修复产出率、**保忠实修复率**〔防删音〕、收敛迭代数、Δ忠实);成本/延迟(墙钟、tokens/$、oracle CPU-ms、每美元联合成功)。**全部分层报(体裁×源层×难度×复调),绝不给单一混合数。**

**A.7 checker(oracle)具体规格**：
- **毫米建颈**:`x_f = L·(1−2^(−f/12))`(L≈648mm 古典/643mm 钢弦)。**span 谓词必须用毫米——用品数是显然错的,好琴手一眼看穿。**
- tab 表示为 `(onset,dur,string,fret,left_finger∈{0..4},right_finger∈{p,i,m,a})`;在**语义化版本、区间取值的 profile**(手跨 H/触及/换把速度 v_shift/右手速率 r_max/弦长/调弦/变调夹)上出**三态**:GREEN 可证可弹(悲观 profile 下仍留边界 ε,accept 方向 sound,误接受≈0)/ RED 可证弹不了(乐观 profile 下仍违反)/ AMBER 边缘(送修复或人审,绝不作认证输出)。
- **硬谓词(可行性门,与难度软分严格分离)**:①range/tuning;②一指一品(横按=同指同品多弦);③手指-品单调/无不可能跨弦;④**span=几何可行性 CSP**(存在指尖指派使两两欧氏距 ≤ d_max(i,j,H),触及随把位压缩);⑤横按可行;⑥**按速换把**(手心位移 Δx/Δt ≤ v_shift + 稳定时间,含 guide-finger 缓解);⑦右手(同时拨弦 ≤ 可用指;单指重复 ≤ r_max);⑧sustain(需保持的音,其手指被别处需要=FAIL)。
- **输出=GREEN/AMBER/RED 三态 + 定位化类型诊断** `{measure,beat,violation_type,offending_notes,超几毫米/毫秒,suggested_relaxations:[drop_5th,octave_down_bass,shift_to_pos_5]}`。**这份诊断=agent 的环境信号(让修复定点、非盲搜)**。checker 暴露为可调用工具;基准跑 3 个手围百分位报敏感性。
- **soundness vs completeness**:**优先 GREEN 的 soundness(误接受≈0)**。承诺是"我认证的就能弹",不是"我找出所有能弹的"。**用 AMBER 带宽吸收不确定,绝不放松 GREEN 阈值。**
- **Oracle 0.2 已实现修订**：非法公共 Tab/profile/tempo 先 typed fail，不获得三态 verdict；active sustain 进入 finger-count/monotonic/barre/span，换把使用连续 reachable interval 并实际消费 `reach_mm`。solver 的 bounded/non-complete search 有 12,000,000 weighted-work 上限，有限 finalist 重建后必须通过完整 oracle。

**A.8 谁来检查检查器(验证 oracle 本身,方法学核心)**：
1. **无标签自检**:property-based(旗舰不变量 **monotone-in-resources**:手更大/更慢/更低把位/r_max 更高,只能 FAIL→PASS 绝不反向)、metamorphic(变速单调、音高对 music21、变调/移调几何不变、静态谓词时间反演不变)、mutation(注入故障看测试杀不杀,报 kill rate)、N-version(每谓词写两遍:慢穷举 spec + 快生产版,差分 fuzz)。
2. **对真实语料差分(头牌信任数)**:DadaGP+GuitarSet 过 checker;**GuitarSet 上每个 RED=bug 单**(人真弹过);再与 Sayegh DP / Radicioni CSP / Fretting-Transformer 三角验证。
3. **人手实弹金标集（规模由 pilot/功效决定）**：含对抗近失样本；带实测手围的琴手在规定 tempo 下尝试展示的精确指法。当前尚未采集；没有第二 rater/retest 就不能报 κ，当前固定 AMBER transform 也不能冒充由 κ 学得。
4. **先校准后留出**:拟合 d_max/v_shift 使 GREEN⊆真弹过、RED⊆全弹不了;人标 train/dev/**test** 切分,test 金标绝不用于校准。
5. **信任指标=GREEN 上的误接受率**,报 Clopper–Pearson 单侧上界(如 "0/120 已知不可弹被认证 GREEN;97.5% 上界 3%")+ 混淆矩阵 + Wilson CI。

**A.9 诚实范围声明**：公布 "凡 Fretsure 认证 GREEN 的谱,在符合公布 profile P(手跨 H/触及 R/换把速 v/右手速率 r)的琴手、指定乐器/调弦/弦长、在我们记录的静态手几何模型 M 下,以记谱速度可弹,实测误接受 ≤X%(95% CI)、真人演奏曲上误拒 Y%。" 把**数学主张**(在 M 上 sound 的判定过程)与**经验主张**(M 对真实校准)分开;列范围限制:profile 相关(出预设,用户选手围)、**仅静态几何**(建模触及+换把动力学,不含肌腱耦合/疲劳/音色——疲劳只标记不认证)、仅记谱速度、认证的是"我们指派的指法存在可行解"而非"最地道"。技巧显式标 IN/OUT(拇指绕/点弦/混合拨弦/推弦/半横按→AMBER/不支持,绝不静默 GREEN)。

**A.10 checker-vs-LLM-judge 实验(基准修辞中心)**：在 N≈400 带人金标+对抗近失的 tab 上,收 (J1) 确定性 oracle 与 (J2) LLM 评委（GPT-5.6 Sol 主评 + 一个独立版本化的跨供应商前沿 comparator，均跑 zero-shot 与带 rubric）判决;对人金标报准确率、尤其**误接受率**(评委说能弹其实不能——危险错误)+ κ;LLM 评委每条**跑 5 次(temp>0)测翻转率**(oracle 恰为 0);McNemar 配对检验报 odds ratio+CI;报成本(评委 $/条 vs oracle CPU-ms/条)。**设计目标结果**:LLM 评委在对抗近失上误接受显著更高 + 有非零方差,oracle 确定近完美——这就是"benchmark 用 checker 打分而非评委"的量化理由。(注:LLM 有正当评判角色——**音乐品味**,非可行性,见 Part B 的 critic。)

**A.11 统计严谨**:Wilson CI;pass@k/pass^k 无偏估计 n≥10/条;配对系统比较用 **McNemar**;**按曲 cluster bootstrap**(不按音/小节,避免伪重复);分层报 + Holm–Bonferroni;预注册最小可检效应与 N(程序生成器让 N 便宜→目标 ±3% CI);发布种子+估计器代码+逐条原始表。**任何无 CI 的单数榜单主张不予采纳。**

**A.12 Baselines**:B1 前沿 LLM 原始(直接要 tab,"到底需不需要 agent"对照);B2 纯确定性求解器(Sayegh 最优路径/Viterbi,无编配步,纯求解上限);B3 学术(SMC-2024 MIDI→tab;TART);B4 商用往返(MusicXML→GuitarPro 自动 tab)。

**A.13 一条命令复现**:`fretsure-bench --seed S` 重建语料与程序测试集；当前 aggregate JSON/trace 盖 LLM model id、checker、fidelity、profile version + SHA-256 与 input schema。完整五层下载/CI/checker-vs-judge runner 与逐 item 配对原始表仍是后续，不得把目标写成现状。

**A.14 构建顺序(第 4 步前下游一律不可信)**:1 归一器+datasheet;2 程序生成器(主测试层);3 oracle 纯函数+类型诊断+3 预设;4 **oracle 验证台(A.8),混淆矩阵领跑排期**;5 忠实度打分;6 难度打分(150 条 learn-to-rank);7 agent(Part B);8 baselines+消融同一 runner;9 统计模块;10 checker-vs-judge;11 复现包。

---

### Part B — Agent 深度（重设计）

**B.0 诚实裁决:当前草稿太薄。** 三个破绽:①LLM 基数=1;②智力质量全是经典 CS(Viterbi 指法/tab linter/手写修复);③抽换测试失败(去掉 LLM 还能跑=点缀)。**根因:oracle 只把关可行性(近乎已解决),而编配高度欠定(几百万可弹解、绝大多数不好听);草稿全花在"能不能弹",没花在"是不是好编配"——真深度住在这。**

**B.1 反转:oracle 当环境,LLM 当策略。** 把 oracle+指法求解器+乐理分析+忠实度 diff 变**工具/环境**;LLM 变策略:规划 → 用**编辑 DSL**(revoice/drop_note/octave_shift/reposition/refinger/rebarre)写编配 → 调 oracle 拿定位化类型报告 → 逐失败点推理音乐取舍 → 下定点编辑 → 重查,循环到 oracle 过 + critic 满意,受预算约束。**oracle=编译器/单测,编配=代码,修复=SWE-agent 编辑回路。** 求解器不消失,变成 agent 调的一个工具。

**B.2 挣得存在的能力(各附消融;leave-one-out 不过 CI 就砍)**：
- **(c) verifier-guided 迭代修复=脊柱**:oracle 出定位违规证书 → LLM 推理取舍(丢五音 vs 低音降八度 vs 换高把位缓 span 但伤音色)→ 定点编辑 → 重查到不动点。**固定算法对所有情形套同一松弛序、无视音乐语境;agent 能在稀疏民谣里保五音换把、在密集段落里丢五音——求解器做不到的语境相关选择。** Reflexion 式用环境结构反馈当信号。**消融 R3**:agentic vs 固定优先级修复,拉到同可行率;预测:同可弹下 agentic 牺牲音更少、musicality 更高。**若打平→agentic 修复没挣到→回退固定算法,明确写出这条砍线。**
- **(b) 工具使用=设计好的 ACI(评委最认的中心)**:工具 `oracle.check→类型判决 / solver.assign_fingering / analyze.structure|key|chords / fidelity.diff→逐声部保留报告 / retrieve.skills`。**承重设计=诊断格式(不是"fail",是定位化 {小节,拍,违反,超几毫米/毫秒,建议松弛})+ 紧凑编辑 DSL。** SWE-agent 原话:"模型与环境的接口本身就是贡献"。**消融 R2**:全诊断 vs 只 pass/fail;预测:接口无信息时修复迭代猛升、定预算可行率降——直接证明 ACI 挣到存在(拿 agent-infra 岗的正是这故事)。
- **(a) 规划/全局一致性**:结构分析(调/拍/段/重复)→ 逐段策略(目标把位/CAGED/织体密度/拇指低音型/何处允许难度)→ 全局一致性 pass(重复段一致)。**消融 R1**:全 vs 整曲一次性;预测:一致性 Elo↑、难切 pass↓、迭代↓。**诚实附注:约束多为局部(span/一指一品),规划可能不划算——预期五五开,愿意砍。**
- **(d) musicality critic=唯一真·第二 agent(紧收口)**:oracle 判不了"好不好听";critic LLM 评声部进行/地道声位/低音走向/织体/一致性,出 rubric 锚定的结构子分供编排者行动。**拒绝多 agent LARP:不是七个角色扮演 agent。** 对齐小规模盲测人群、**报 critic-human 一致度(κ/相关)为一等结果**——它也是 checker-vs-judge 里"品味"那一半(oracle=可行性确定性 checker;critic=有界、经校准的品味评委,且明确各自可信边界)。**消融 R4**:全 vs 仅 oracle;预测:人评 musicality↑、忠实/可行大致平——证明"可弹"与"好听"是两轴。
- **(f) 技能/记忆库(Voyager 式,便宜高杠杆)**:攒可复用已验证件——声位/形状库(键 (和弦,旋律音,把位))、修复策略库(哪种松弛在哪种语境解了哪类违规)、用户偏好(调弦/变调夹/最大 stretch);嵌入检索。**主要是省成本**。**消融 R6**:热库 vs 冷启动;预测:迭代↓、tokens↓、母题共享曲更地道——**带明确留出泛化切分(形状不在库里的曲)证明复用非泄漏**。仅当 $/曲线弯了才留。
- **(e) 测试时搜索(先浅,被迫才升)**:best-of-N 采 N 个种子→各过修复→oracle+critic+忠实度打分取最优。**只有当它达到修复达不到的多样性才合法;它与修复抢同一预算,须在同算力下赢在修复之上。** **消融 R5**:N∈{1,2,4,8,16} 扫;再 MCTS vs best-of-N 同 token 预算。**诚实砍线:若 N=1 已饱和可行率、MCTS 同预算不赢,就发 best-of-N 并说明。先建 MCTS=过度工程破绽。**

**B.3 诚实的线——一边反薄壳,一边反 LARP**：
- **反薄壳**:LLM 必须**驱动回路**、做求解器做不了的音乐决定(保哪些声部/音区织体/地道声位/为命中难度在哪削和声)。**"纯求解器稻草人"行(启发式种子+求解器+oracle+固定修复,LLM 全去掉)是直接反驳**,展示无 LLM 判断时的音乐地板。
- **反过度工程 LARP——见到即砍**:>2 个 agent 角色;只负责调其他 agent 的"编排 agent";本可传结构化数据却用自然语言消息传递;只复述段落表的"规划 agent"。**写进文章的规则:一个组件挣得存在 iff 在留出曲的 leave-one-out 消融里去掉它会让某 checker 打分指标退化;其余皆戏。公开"我们砍了哪些+被哪条消融杀掉"本身就是最强反 LARP 信号。**
- **推迟/警惕**:小模型 RLVR(无 GPU 下 LARP 风险最高);MCTS(仅当 best-of-N plateau)。

**B.4 对齐大厂 agent 招聘认的信号(2026)**:①verifier-in-the-loop 迭代修复(AlphaCodium/RepairAgent)→(c);②设计好的 ACI 而非只 prompt(SWE-agent)→(b);③记忆/verbal-RL 自纠(Reflexion)→(c)+(f);④测试时算力/带验证器搜索→(e);⑤技能库/终身复用(Voyager)→(f)。**领跑叙事用"一个形式化可验证环境 + 一个对它规划、用工具、搜索、带 verifier-guided 自纠的 agent",不是"一个写吉他谱的 LLM"。**

**B.5 RLVR(小本地模型,CPU/24G)——推迟,若做则狠收口**:文献给了说法(RLVR 主要**锐化**基模已有分布而非扩能力)。**建议推迟生成式策略**;要 RL 学分就收到最小可验证赢——一个 **reranker/value 模型**(在 oracle+critic 奖励上训,从 best-of-N 里选/给修复编辑排序),非生成式策略。**强制对照:RLVR reranker vs 纯 SFT 蒸馏前沿修复轨迹**——若 SFT 就拿下降本,RLVR 是装饰。仅当它以显著更低成本赢过前沿 reranker 才留。**干净负结果"我们在 CPU 上试了 RLVR,这是消融,不划算"比复现不了的脆弱策略对 2026 评委更可信。预注册砍线。RL 不上关键路径;LLM 策略+验证器回路无需训练就是交付物。**

---

### Part C — 消融矩阵 + 两个头牌结果

**C.1 keep/cut 纪律(跑前声明)**:一个能力"深"仅当其 **leave-one-out 移除让某主指标退化超过冻结套件配对 95% CI 半宽**,且不被更便宜能力 Pareto 支配。加法阶梯会美化一切;**leave-one-out 才是诚实测试。** 每行预注册预期方向与砍线。

**指标列(全确定性 checker 打分)**:`M1` pass@1(修复前单样可行,诊断用)·`M2` **pass^8**(8 次独立全可弹,可靠性门;55% 对的提议器给 0.55^8≈0.008)·`M3` 忠实(melody-F1+低音+和声+onset)·`M4` 难度贴合 |实测−目标|·`M5` 修复迭代(诊断)·`M6` $/曲·`M7` 难切 pass^8(快速/宽音域/异调弦/密复调泛化)。**keep/cut 主指标=M2,M3,M4,M6(+M7);M1/M5 诊断用。**

**C.2 leave-one-out 矩阵(格=移除时预注册预期 Δ,待测非结果)**。完整系统参考目标:M1≈0.75,M2≈0.92,M3≈0.97,M4|Δ|≈0.4,M5≈2.1,M6≈$0.30,M7≈0.80。

| 配置(移除其一) | M1 | M2 pass^8 | M3 fid | M4 |Δ| | M5 迭代 | M6 $ | M7 难切 | KEEP if / CUT if |
|---|---|---|---|---|---|---|---|---|
| **完整系统(参考)** | 0.75 | 0.92 | 0.97 | 0.4 | 2.1 | 0.30 | 0.80 | 参考 |
| − 规划(a) | −0.10 | −0.03 | ~0 | +0.1 | +0.8 | ~0 | −0.12 | M7/M5 动过 CI 则留;否则砍 |
| − 工具/诊断(b) | −0.15 | ~0/−0.04 | ~0 | ~0 | **+0.5 猛升** | −0.05 | ~0 | 迭代↑/预算 Pareto 则留;否则砍 |
| − **修复(c)** | n/a | **−0.90→~0.01** | −0.05 | +0.3 | n/a | −0.10 | −0.78 | **留(塌陷)——头牌#1** |
| − critic(d) | ~0 | ~0 | −0.05 | −0.3 | ~0 | −0.03 | ~0 | M3/M4 过 CI 且人相关则留;否则并入砍 |
| − 搜索 best-of-N(e) | −0.05 | −0.04 | −0.03 | −0.2 | ~0 | −0.15 | −0.05 | 仅同成本下过 CI 才留;否则砍 |
| − 记忆(f) | ~0 | ~0 | −0.02* | ~0 | ~0 | +0.12 | ~0 | $ 弯过 CI 则留;否则砍 |
| **纯求解器稻草人(−LLM)** | — | 地板 | 更低 | 差 | — | 低 | 差 | **对"LLM 点缀"的直接反驳** |
| 换 RL reranker(g) | ~0 | ~0 | ~0 | ~0 | −0.3 | −0.28(~20×) | ~0 | ~1/20 成本保 M2/M3 则留;否则砍(报负) |

另跑加法阶梯(B0 贪心无 LLM → B1 一次性提议 → +修复 → +critic → +搜索 → +记忆 → +RL)看地板与形状,但**keep/cut 只按 leave-one-out**。统计:同冻结分层套件(n≥300–500,hashed)配对 bootstrap CI + 配对置换检验 + Holm–Bonferroni + 多种子 + 钉住 oracle hash。

**C.3 预注册的 LARP-砍预测(跑前承诺)**:预期**留**——修复(c,决定性)、critic(d,若人验证)、记忆-省成本(f,若 $/曲线弯);**有砍风险**——规划(a,局部约束或与修复冗余)、超出诊断的交织工具用、修复之上的搜索(e,修复常先饱和 pass^8)、RL(g,CPU 或输给前沿+修复或被 SFT 追平)。**先承诺再砍掉没挪指标的,正是"纪律工程师而非 agent LARP"的信号;砍掉的部分是交付物,不是失败。**

**C.4 头牌#1(一句话同证深度+正确)**：
> "verifier-guided 迭代修复把指弹 pass^8 从 0.01(一次性前沿提议)拉到 ≥0.90,同时 melody-recall F1≥0.97、在请求难度带内。消融修复回路→pass^8 塌回 ~0.01;且预注册的**无廉价补救对照**证明:光升温度/best-of-N 而不修复补不回来。"
> 末句击碎"只是 LLM 点缀":**pass^8 这种联合可靠性没法用蛮力采样买到——联合门惩罚每个不可靠单样,只有闭环、按 certificate 定点修订才逐个修好。** 全 checker 打分、钉 oracle hash 可复现。

**C.5 头牌#2(成本+深度,带诚实回退)**：
> "一个 ~1–3B 本地 RLVR repair-reranker,只在确定性 oracle 奖励上训、零专有数据,在 pass^8(≥0.90)与忠实(≥0.97)上追平前沿提议+修复,而 $/曲 约 1/20,并把平均修复迭代从 I0→I1。"
> **RL 在 CPU 上不达标(很可能)的回退**:诚实报负,把第二头牌换成**搜索/修复 Pareto 前沿**(每 N 的边际 pass^8/musicality 增益 + 拐点 N*)或 **SFT 蒸馏降本**。消融而非主张决定故事;干净可复现的负 RL 结果本身是可信的作品集信号。

**贯穿风险**:①oracle 有效性天花板(在生物力学模型下认证,非普适可弹;人金标误接受高则"可证明可弹"减弱→混淆矩阵+敏感性扫领跑);②忠实度作弊(靠削音过可弹→联合 Pareto + 保忠实修复率当护栏、当主指标);③污染残留(变调公有曲或部分被背→程序生成层扛头牌);④agentic 修复优势是真赌注(调好的固定优先级可能打平→让 R3 决定、打平诚实回退);⑤pass^8 部分是定义选择(报完整 pass^k 曲线);⑥成本/延迟(best-of-N×逐编辑 oracle 调用×critic 可能爆 token→上线前验墙钟可行)。

---

## §15 附录：可展示性、Demo 与 SOTA Agent-Harness 栈（Part D–G）

> 统领规则：**每个框架都必须在回路里干真活;不干就砍——强工程评委识别"简历装饰(LARP)"比识别"短而诚实的栈"更快。** 你的价值是 oracle+edit-DSL+checker(自研),框架只让它可讲、可复用、可追踪。

### Part D — Agent-Harness 栈（★founder 决策：自研 harness，框架降级为参考对照）

> **决策更新(founder 定,覆盖下方的"建在框架上"表述)**：**编排 harness / 回路 / 状态与 trace / eval 台一律自研**,**不把 LangGraph、Claude Agent SDK 等编排框架作为核心依赖**。理由:①一线 agent harness(SWE-agent 等)本就自研,自研=对大厂 agent 岗更强的深度信号、且彻底避开框架 LARP;②回路形状(plan→emit edit→oracle→reason→edit→re-check 到不动点 + best-of-N 扇出 + critic)简单且完全在掌控内,自己写更可控、更好演示;③**"自研回路 vs LangGraph/Agent SDK 的对照基准"本身是一个漂亮的作品集 artifact**——实现时反复测试、拿数据选出最好的 harness 设计。
>
> 因此下方框架清单**只作参考设计 + 对照基准**(实现时 benchmark against,自研版不明显更差就不引入),**不是要采用的依赖**。保留为参考/可选、且都用消融把关的:**DSPy/GEPA**(prompt 优化,皇冠研究信号——它是优化器不是 harness,值得保留试)、**MCP**(把 oracle 暴露成标准工具,互操作/演示用,便宜高信号)。自研的 trace/eval 可发 **OTel GenAI spans** 以便用现成 viewer(如自托管 Langfuse)看,但**格式与回路自持**。

**以下为参考设计对照(非依赖)**：

1. **自研 harness — 已采用的运行时(orchestration)**。回路是明确、有界的环（plan→emit edit→oracle→reason→edit→re-check 到不动点）加 best-of-N 扇出；状态、checkpoint、公开 `agent-trace@0.2.0` 与 eval 合同均由项目持有。Plan 6A 已证明它可直接驱动 replay viewer，不需要把 LangGraph/Claude Agent SDK 引入关键路径。LangGraph/Agent SDK 只保留为未来同预算对照实验；没有消融收益就不引入。
   > 历史备选记录：早期曾在 LangGraph 与 Claude Agent SDK 间二选一；founder 后续决定已覆盖该方向，不能再把历史备选写成当前依赖。
2. **DSPy 3.x + GEPA — prompt 优化(★皇冠研究信号)**。*建,但用消融把关。* GEPA 用**书面反馈**(而非数值奖励)进化 prompt,是 ICLR 2026 oral、以 ~35× 更少 rollout 胜过 RL(GRPO)、**不需要 GPU**(API+CPU),正好卡你硬件。**契合近乎教科书:你的 oracle 已经吐定位化类型诊断→把诊断原样喂给 GEPA,进化 ①编排者规划 prompt ②critic rubric,用你的确定性 checker 打分。这就是无 GPU 的 RL 风味胜利。** 招聘信号:全栈最高的"我做了真研究工程"。反 LARP:GEPA 只碰自然语言 prompt/rubric,**绝不碰 oracle/DSL/checker**;**用 leave-one-out 消融把关**(手写 prompt vs GEPA),不提升就**砍掉并说明**。先跑一天 spike 再决定。
3. **Inspect-AI(UK AISI)— eval/verifier 台(可信度倍增器)**。*把 benchmark 建在它上面。* 它的词汇 Task/Solver/Scorer 让你的"checker 打分 + leave-one-out 消融"用标准语言可读:oracle 包成确定性 **Scorer**、每首歌是 **Task**、best-of-N+修复到不动点是 **Solver**。招聘信号:对研究倾向岗比任何厂商可观测工具都高(UK AISI 背书)。
4. **MCP — 已完成 Plan 6A 软件纵切**。暴露 `check_playability/feasible_fingerings/render_notation`；`render_audio` 明确 deferred，不注册假工具。叙事:"oracle 是任何 agent 能调的 MCP server"+ bring-your-own-arranger 分发故事。反 LARP:**热循环里进程内直调 oracle,别每次不动点迭代付网络延迟**;MCP server 是互操作/演示用的适配器,不是热路径。
5. **一个 tracing 工具 — 自托管 Langfuse(或 LangSmith)**。发 **OpenTelemetry GenAI spans**,在 Langfuse(开源、自托管、有 Agent Graphs 视图)渲染——**同时是 Part E 的 trace viewer 和你真正的 dev 遥测**。**只用一个,别 Langfuse+LangSmith+Braintrust 同时上。**

**可选加分(真站起来才提)**:**Prime-Intellect `verifiers`——把 oracle 发布成可验证 RL 环境**。把你从"app 作者"重构成"环境+eval 作者"(2026 最高身份信号),且**无需训练大模型就展示 RL-readiness**(你的 oracle 本就是确定性奖励函数)。仅当你能脱稿讲它才做,光提一句=LARP。对 RL 倾向岗强。

**自己建、别引框架**:**测试时搜索**(best-of-N + verifier-guided/DVTS)是你的差异化 IP,从零写远比依赖值钱(可引 HF `search-and-learn` 当借鉴的先例);**记忆/技能库**自建一个"按诊断类型索引已验证编辑模式"的嵌入库(**可选** Mem0 当可插拔检索层,但**消融把关**,且台上说清这是**检索、不是模型"学会"**)。

**明确拒绝(此处不契合/低信号)**:AutoGen(已并入 MS Agent Framework,基本 sunset)、CrewAI/smolagents(demo 级,撑不起旗舰)、Google ADK(非 Gemini/GCP 原生不划算)、Letta 当运行时(自带控制循环,会和自研 harness 打架=架构异味)、Zep/重型记忆平台(吉他编辑模式用时序知识图=过度工程)、OpenAI 可视化 Agent Builder(2026-11-30 关停;要 OpenAI 信号用**代码版** Agents SDK)。**评委最认的元信号:一个每块都承重的小栈（自研 harness + 通过消融的优化/eval + MCP + 一个 tracer）胜过一长串框架清单。**

### Part E — 演示与 Demo 规格（可上台、不只是数字）

**★唯一 MONEY MOMENT**：**观众点歌 + 选难度 → agent 现场编配 → oracle 抓到一个人手弹不了的小节(真实指板上闪红)→ agent 在屏上推理音乐取舍 → 定点修改 → 转绿 → 一个真人当场把修好的小节弹出来、你听得见。** 最后这一下(可听/物理)就是可传播的画面("可证明人手可弹,而且真人当场证明"),同时打消你两个顾虑:不是一堆数字(可见/可听/可交互),也不可能 LARP(是真产品跑未见输入)。**保真但有界:预筛 3–4 首候选让回路真活但不会当场死锁;每个 live 步骤都绑一个"预缓存跑"到某个按键,网络抖动不毁高潮。**

**~7 分钟上台序列(混合评审)**:0:00 钩子(45s,放 AI 生成 tab 的音频"好听吧?"→ 真手够不着 → 一句论点"前沿模型写出人弹不了的音乐,我们保证可弹");0:45 交互(60s,评委点歌+选难度,符号输入是可靠默认,哼唱转谱是"成了就是魔法"的可选花活带 fallback);1:45 **回路 live**(2.5m,分屏:左指板/谱,右"看 agent 思考"trace;编配出现→oracle 标红→trace 显示 plan→诊断→白话推理→定点编辑→重查→转绿);4:15 payoff(60s,真人弹修好的小节,**可听证明**);5:15 **现场 A/B**(60s,同歌同难度过**裸** Opus/GPT 无 oracle,当场把两者过 oracle,裸模型多处标红,"模型单独证明补不了这个 gap");6:15 **live 榜单**(45s,观众那首结果当场追加;显示消融行:去 critic/去 best-of-N/去记忆,评委**看见**哪个组件挣到存在);7:00 收("oracle 当环境、LLM 当策略。可证明,不是靠感觉")。

**"看 agent 思考" trace viewer**:直接读**自研回路的状态历史(checkpoint)** + OTel spans 的类型步骤时间线 `PLAN → EMIT edit-DSL → ORACLE CALL → 定位诊断(如 FRET_SPAN_EXCEEDED@bar12.beat3, span=6, max=4) → REASON(白话音乐取舍) → TARGETED EDIT → RE-CHECK`,每次判决红/绿让眼睛追不动点收敛;**给产品评委的技巧:每条原始 JSON 诊断叠一行人话("第 12 小节要在按横按时跨 6 品——没有人手做得到")。这份 trace 就是把"agent"和"带 system prompt 的聊天机器人"区分开的东西。**

**指板红/绿动画**:渲染**真实琴颈**(不是数字格),逐小节动画指位、同步播放;oracle 诊断驱动颜色(标红问题品位 + 标注"span 6 > max 4",修复后同小节重演成绿并播放)。**物理不可能在指板上人人秒懂,JSON 报错做不到。** 用 alphaTab / VexTab 渲谱 + 自研 SVG/Canvas 叠手型;先放"弹不了版"的音频(听着没问题,MIDI 没有手)再证明手够不着——**"听着没问题"与"没人弹得了"之间的落差就是产品论点。**

**现场 A/B**:做成**公平对决**(同模型、同 prompt 预算、oracle 同样施于两边);作弊的 A/B 会被识破、赔的可信度比赚的多。它证明的点:**护城河不是更好的模型,是回路里的验证器补上了前沿模型单独补不了的 gap。** **live 榜单**:因为打分**确定性**,当场重算;列 `%可证明可弹 / 每首违规数 / musicality-critic 分 / 到不动点编辑数` + **leave-one-out 消融行**;像 SWE-bench Verified 那样"固定可执行评分器"才是数字可信的原因。**数字服务于故事:用 live UI 讲,而非静态幻灯片。**

### Part F — 可展示=真功能（presentability = product）

每个台上元素都是**上线功能**;若只是 demo 脚手架,技术评委会闻到戏。映射:**台上 show 特性 → 真产品功能 → 底层能力**:

| 台上 show 特性 | 真产品功能(上线) | 底层能力 |
|---|---|---|
| 诊断在谱上红→绿 | **产品内透明**:用户看到某段为何难、怎么变可弹(信任+教学) | oracle 定位化类型诊断 + 修复回路 |
| 人在环打断"保留横按 or 简化?" | **交互难度旋钮**:逐段"变简单"控件 | 自研回路 interrupt + checkpoint 续跑;难度简化 |
| 前后音频播放 | **应用内 tab 播放**(标配) | 音频渲染(MCP 工具)+ 编配 DSL |
| best-of-N 候选并排 | **"备选编配"**用户比较挑选 | 测试时 best-of-N + 验证器搜索 |
| 指板红→绿动画 | **核心产品 UI**(琴颈而非数字格) | oracle 可行性门驱动渲染 |
| 记忆"学会"相似曲更快 | **成长的个人库**(可复用声位/乐句),相似曲更快 | 嵌入索引技能/记忆库 |
| "任何 MCP 客户端能调我们 oracle" | **集成/bring-your-own-arranger**(Cursor/Claude Desktop) | oracle 暴露为 MCP server |
| 消融-delta 幻灯 | **产品质量看板**(首次即可弹率)——给买家和评委的信任故事 | checker 打分 benchmark + 消融台 |
| trace 回放/分叉某个历史决策 | **版本历史 + 分支/对比编配**(真编辑器功能) | 自研回路 checkpoint 时间旅行 + 分叉 |
| "看 agent 思考"面板 | **"解释这版编配"**功能(琴手想知道某小节为何被简化) | plan→oracle→edit 回路的 OTel spans |

**对齐就是全部:demo 准备就是产品工作,而 demo 不可伪造——它就是产品在跑。**

### Part G — 求职 Artifact 清单

**必须(先做)**:①**`/oracle` 做成纯确定性、可 pip 安装的库**(property-based 测试 + 绿色 CI,全仓最好覆盖率——皇冠);②**公开 checker 打分榜单**(冻结版本化测试集 + provenance/许可 + 留出切分 + 日期截止,`make bench` 一条命令重生成所有数,明说 checker 验证非模型自报);③**leave-one-out 消融表**(去 oracle/critic/best-of-N/记忆,你最有说服力的单图);④**README 首屏**(架构图 + money-moment GIF + 头牌榜单数 + 消融表);⑤**~3 分钟 demo 视频**(以 失败→修复→播放音频 收尾);⑥**回路由真自研 harness 驱动**，oracle+edit-DSL 通过进程内 typed seam 与 MCP adapter 暴露。
**高价值(其次)**:⑦oracle 打包成 Prime-Intellect `verifiers` 格式可验证环境(Hub 发布尽力而为);⑧OTel-GenAI spans 上的 **trace viewer**(自托管 Langfuse,诊断叠在渲染谱上);⑨**技术写作**(以"oracle 当环境"领起,一个带真实诊断+修复的失败例、消融表+诚实解读,**外加"什么没成功"一节**——读起来很资深)。
**锦上添花(有时间且诚实)**:⑩CPU-only reranker(在 oracle 验证的 rollout 上训,**精确措辞、绝不叫"RL"**);⑪一条命令 Docker/uv + 托管 live demo URL;⑫可选第二 demo:一个 OpenAI Agents SDK agent 调**同一个** MCP oracle(证明边界跨生态可移植)。

**一段叙事(README/简历/面试前 30 秒)**：
> "我建了一个**形式化可验证的编配环境**——一个吐定位化类型诊断的确定性可弹性 oracle——和一个**规划、经受约束 DSL 编辑、自我纠错到 checker 验证不动点的 LLM 策略**。正确性由外部检查,不是模型自评。我把环境发布成可复用 RL 基础设施 + 一个防污染、checker 打分、带 leave-one-out 消融的榜单,证明哪些组件挣得存在。我选了消融证明足够的最简架构,并能准确告诉你哪里加 agent 复杂度划算、哪里不划算。"

一次命中 agent 团队筛的四点:①你建**环境和 eval**(稀缺高身份技能);②你的 agent 规划/用工具/对真反馈自纠(copilot 回路能力);③你有**消融纪律**(严谨信号,也是对"你是不是靠大模型运气好"的直接反驳);④正确性外部检查(可靠/安全信号)。

**要避免的反信号(每个你都已有解药)**:薄壳无核(让 oracle/env/bench 无法忽视)、无 eval/只靠感觉(留出集 checker 榜单)、LLM-as-judge 当 ground truth(硬门保持确定性并说明)、benchmark 剧场(留出切分+provenance+可复现)、框架 LARP(只加消融证明有益的复杂度、点名你故意不做的:多 agent/全 RL/RAG)、过度声称"RL"(精确描述离线 reranking)、长工具清单(小而承重才是品味)。

### 两条对创始人的坦白
1. **LARP 恐惧是站得住的。** 若干 2026"必备"名字在其原生场景干真活,但塞进一个 solo 吉他谱 agent 就是简历填充;上面的栈逐个标了 build-yourself vs adopt 正是为此。**强 agent 工程评委罚长工具清单比罚短的更快。**
2. **数字始终是真正的证明。** harness 和 viewer 让证明**可讲、可交互**,但绝不能稀释或替代它。**整条求职叙事最深的风险是 oracle 没有设计声称的那么确定/完整——最该狠验的就是它**(§14 A.8)。且在押 DSPy+GEPA 前先跑一天 spike。
