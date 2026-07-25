# Plan 6B 验收记录

> 状态：**CLOSED — HUMAN PLAYTHROUGH PARTIAL**  
> 软件、生产浏览器、发行包、桌面互操作、unseen-input 真实代理运行与真人签收：证据齐全。  
> 最终阻塞：无；真人结果保持 `PARTIAL`，不改写为 PASS。

## 产品与证据边界

- 上传前可选择 beginner/intermediate/advanced 目标；结果谱、AlphaTab 播放、同步指板、该目标的首次
  难度检查与全部导出绑定同一个 selected canonical Tab checkpoint。
- rejected trial 可以回放，但明确与最终导出隔离；最后一个 GREEN checkpoint 始终可见。
- best-of-N 是显式 opt-in，候选卡公开实际 model/solver call 数。Demo Lab 只有在两份 verified output
  的每输出实际 model-call 数相等时才开放 A/B；否则显示 unavailable。
- critic 只显示为 machine observation，不是人类口味、难度或 musicality 结论。
- benchmark v2 的 verifier-guided repair 保持 `NOT_KEPT`，没有恢复为默认产品主张。
- public replay trace 仍是产品证据真源；OpenTelemetry 只导出运行 spans，不建立第二 trace store。

## 前端与浏览器验收

用户于 2026-07-24 查看真实 result workspace 后确认：“做的不错，界面挺好看的”。当前视觉方向锁定。

| 项目 | 结果 | 直接观察 |
|---|---|---|
| AlphaTab | PASS | 1.8.4；backend canonical MusicXML TAB；本地 Bravura 字体 |
| 浏览器播放 | PASS | 本地 Sonivox SoundFont；position 事件驱动谱面 cursor 与 active fretboard notes |
| 桌面布局 | PASS | 生产服务 `1728×832`；24/24 SVG text/glyph 有尺寸；无 alert |
| 移动布局 | PASS | `390×844`；document `scrollWidth == clientWidth == 375`；score host 317/317；24/24 glyph 可见 |
| 导航/本地库 | PASS | Workspace、Demo Lab、Library、New score；canonical result/provenance-only localStorage schema |
| 难度目标 | PASS | 上传前显式选择；选择值直接带入结果页首次 checkpoint check，结果页仍可复检其他 tier |
| 生产 CSP | PASS | AlphaTab 两段固定 CSS 仅由 SHA-256 放行；动态 SVG style attributes 放行；script 与其余 inline blocks 仍拒绝 |
| 键盘/动效 | PASS | 结果标题 focus、标准控件语义、`prefers-reduced-motion` 关闭 animated beat cursor |

生产 CSP 的两条 AlphaTab 1.8.4 style hashes 是：

- `sha256-EIR5s3Qp1PxPxW4Koopu9nVN+I2chNMT0ImH3VG/s+c=`
- `sha256-2dgVYmA3nzp4Wj5m/tX4Izy5mFRftjUgM13wuNbiAak=`

## API、MCP 与运行时

- HTTP：difficulty check、MIDI 与 FluidSynth WAV export；WAV failures 使用稳定 typed problems。
- MCP：`check_playability`、`check_difficulty`、`feasible_fingerings`、`render_notation`、`render_audio`。
- Audio：canonical MIDI 是确定性源；FluidSynth runtime `2.5.6`；输出是机器合成预览，不是真人证据。
- OTel：可选 `opentelemetry-api`；top span `fretsure.arrangement`，model call span
  `fretsure.model_call`，共享当前 provider context。
- Verified alternatives：application/API wire 只返回已通过 GREEN oracle 的 checkpoint，并带独立
  faithfulness、ASCII、proposal/critic provenance 与实际工作量。

## 同 checkpoint 互操作

验收 checkpoint：

- source SHA-256 `a57887bc0373babb8029ef0316e4f6ab91e980576bf67273dabecdd626126984`
- canonical Tab SHA-256 `b542a7dcc30801e6419411fe524c7f78ca2219c6bf2a402affc0cfcfe48d6a07`
- oracle `GREEN`，tempo `96 BPM`，model `constant-stub`

完整 machine-readable receipt：
[`manifest.json`](../artifacts/plan6b-interoperability/manifest.json)。

| 格式 | 结构检查 | 桌面实开 |
|---|---|---|
| MusicXML | PASS | MuseScore Studio 4.7.4 PASS；可见谱面；播放控制已执行 |
| GP5 | PyGuitarPro round-trip PASS | MuseScore Studio 4.7.4 PASS；可见谱面 |
| GP7 `.gp` | AlphaTab reopen PASS | MuseScore Studio 4.7.4 PASS；可见谱面 |
| PDF | PASS | 自动结构检查；无需编辑器 gate |
| MIDI | PASS | 自动 SMF 结构检查 |
| WAV | PASS | RIFF/WAVE 与 runtime stamp |
| canonical Tab JSON | PASS | SHA 与 checkpoint 一致 |

## 自动化与发行门

| Gate | 当前结果 |
|---|---|
| Web tests | PASS：61 tests |
| TypeScript | PASS：`tsc -b --pretty false` |
| Production Web build | PASS；AlphaTab vendor IIFE `import.meta` 与大 chunk 为已实测可运行的上游 build warnings |
| Python full suite | PASS：2765 passed，8 integration deselected；唯一 warning 为第三方 Starlette/httpx2 deprecation |
| Ruff / strict mypy / lock | PASS：Ruff；strict mypy 108 source files；`uv lock --check` |
| wheel/sdist audit | PASS：wheel 149 entries；sdist 420 entries |
| clean-install matrix | PASS：core replay、benchmark、musicxml、midi、score、service、mcp；service 验证 OpenTelemetry import |

## 最终门（已完成）

### 真实代理运行

**结果：PASS。** 完整 machine-readable receipt 与同 checkpoint 导出见
[`manifest.json`](../artifacts/plan6b-money-moment/manifest.json)。本次使用此前未用于验收的受支持
symbolic input，并记录了 input SHA、model identity、tier、`n`、实际 model-call 数、
trial/diagnostic、最终 GREEN checkpoint、playback 结果与全部导出 hashes。

未预筛输入与运行参数已经冻结：

- 输入：[`unseen-etude.musicxml`](../artifacts/plan6b-money-moment/unseen-etude.musicxml)，CC0，
  SHA-256 `0b6568715fc62cf963f7eac66adff13e73de6c88b1a7dcd65a26fab4f2332630`。
- preflight：[`preflight.json`](../artifacts/plan6b-money-moment/preflight.json)。冻结时只做 XML well-formed
  检查；在正式 UI 提交前未交给 importer、arranger、solver、oracle、difficulty checker 或模型，
  不是 cached fallback。
- 冻结运行：`gpt-5.6-sol`、advanced、`n=2`、critic off，声明上限 2 个 model calls；没有新授权不得
  追加调用。
- 用户于 `2026-07-24T05:35:04Z` 明确授权“本批次最多 2 次真实代理模型调用”。产品 API 的
  `ProxyLLM` 已固定为每个逻辑调用最多 1 次外发 attempt，因此本次 `n=2` 不会因客户端自动重试
  超出授权；失败即停止。
- 环境中的 loopback proxy URL/token 已配置，2026-07-24 preflight 时 `localhost:4141` 正在监听；
  独立端口零调用启动已确认 capabilities 发布 `available/enabled=true` 的固定 `gpt-5.6-sol`。
- 第一次授权后执行于 `2026-07-24T05:37:03Z` 在提交前暂停：浏览器运行时没有暴露可用页面；
  arrangement endpoint 与模型均为 0 calls，临时 allow-proxy 服务已停止，授权额度未消耗。为保持
  unseen/UI-primary-action 证据，不以独立 HTTP 调用替代。
- Chrome 文件权限恢复后，正式 `fretsure-serve --allow-proxy` 于独立端口显式启动；
  `2026-07-24T07:25:42Z` 从真实 Web UI 提交冻结输入，arrangement endpoint 返回 HTTP 200。
  页面公开 **2 个逻辑 model calls**，两个候选各 1 call、均为 GREEN，critic 未运行；候选 1 被选中。
- 页面可见 Trial 6 的 `FRET_SPAN` 回滚证据；最终 selected checkpoint 为 GREEN，advanced difficulty
  为 PASS。AlphaTab 1.8.4 对最终 checkpoint 完整播放 11 秒并自动复位；页面明确显示谱面、音频与导出
  共享同一 checkpoint。
- canonical Tab SHA-256 为
  `3764d70399152d2ead40e47e8cd230797acb2249383d93b2d632d6a881187a0e`。MusicXML、GP7、GP5、
  PDF、MIDI、WAV、ASCII TAB 与 Tab JSON 的文件、bytes、结构检查及 SHA-256 均已写入 receipt。
  allow-proxy 产品服务于 `2026-07-24T07:35:17Z` 停止；没有追加调用。

### 真人 guitarist receipt

**结果：COMPLETE / PARTIAL。** 匿名琴手为业余 3 年，使用古典吉他、标准调弦、无变调夹，演奏
上述真实代理运行最终保留的 canonical checkpoint。完整结构化记录见
[`human-guitarist-receipt.json`](../artifacts/plan6b-money-moment/human-guitarist-receipt.json)。

- 整体难度不高，84 BPM 提供了足够反应时间。
- `12-10-8-12` 的大把位变化是明确局部问题，尤其最后的 `8→12` 不易按；因此 full playthrough
  诚实记录为 `PARTIAL`，不是 PASS。
- 片段仅四小节，琴手认为不足以可靠评价音乐性。
- 未提供音视频；署名为 Anonymous；未授权发布额外身份信息或试奏材料。

真人签收只覆盖这份精确谱和这位演奏者，不自动校准全部 profile/tier，也不把机器 critic 变成真人
musicality 证据。
