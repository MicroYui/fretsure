# Fretsure Web、HTTP API 与 MCP

本页描述 package `0.6.0` 的本地互操作面：router=`score-input@0.1.0`，importers=
`musicxml@0.3.0` / `midi@0.1.0`，service=`fretsure-service@0.2.0`、
API=`fretsure-api@0.2.0`、MCP=`fretsure-mcp@0.2.0`、Web=`fretsure-web@0.2.0`、
trace=`agent-trace@0.2.0`，faithfulness=`fidelity@0.3.0`。这里的 `GREEN` 始终是
`oracle@0.2.0` + 指定 profile 下的版本化模型证据，不是真人保证；faithfulness 是另一道独立门。

## 本地 Web 与 API

开发仓库：

```bash
uv sync --extra dev
uv run fretsure-serve
```

默认只监听 `127.0.0.1:8000`。浏览器打开 `http://127.0.0.1:8000/`。Web 可上传当前受限
MusicXML lead-sheet 子集的 `.musicxml` / `.xml`、严格 `.mxl` container，以及 melody-only strict
SMF 的 `.mid` / `.midi`；也可加载页面内的
CC0 示例。MusicXML 4.0 traditional key 合法省略 `<mode>` 时保留
`key-signature:fifths=N;mode=unprovided` 并发 `KEY_MODE_UNPROVIDED`，不会猜 major/minor；MusicXML
3.1 省略 mode 仍拒绝。MIDI 不猜 track role、bass/chord/key/quantization；缺失的 bass-root/harmony
faithfulness 显示为 N/A。上传是原始 request body，不使用 multipart、临时文件或路径回读。
首版 MIDI 纵切只复用现有上传面、provenance/证据卡、warning 与 N/A 展示，不新增 track mapper、
时间轴、琴颈动画或独立视觉 gate。

安装包用户可安装完整的本地服务组合：

```bash
python -m pip install 'fretsure-oracle[service,score,agent]'
fretsure-serve
```

服务是本机单用户开发面，不是公网多租户部署。它拒绝非 loopback `Host` 与跨源写请求；不要用反向
代理绕过这些边界。若未来需要远程部署，认证、TLS、跨源策略和外层资源限制必须另行设计。

### HTTP 端点

- `GET /healthz`：只表示进程存活。
- `GET /api/v1/capabilities`：版本、输入格式/上限、engine availability、profile 和控制范围的配置真源。
- `POST /api/v1/arrangements?filename=...`：raw MusicXML/MXL/MIDI；控制项为 `engine`、`n`、
  `max_iters`、`use_critic`、`tempo_bpm`。
- `POST /api/v1/oracle/check`：raw canonical Tab JSON。
- `POST /api/v1/exports/midi?tempo_bpm=...`：raw canonical Tab JSON，返回可下载的 `audio/midi`。
- `POST /api/v1/exports/tab-text`：raw canonical Tab JSON，返回可下载的 UTF-8 `.txt` 吉他六线谱与
  逐音指法表。
- `POST /api/v1/exports/musicxml-tab?tempo_bpm=...`：返回可继续编辑的 MusicXML 4.0 吉他 TAB。
- `POST /api/v1/exports/guitar-pro?tempo_bpm=...`：返回真实 Guitar Pro 5.1 `.gp5` 二进制。
- `POST /api/v1/exports/pdf-tab?tempo_bpm=...`：返回可打印的 A4 矢量六线谱 PDF。

`GET /api/v1/capabilities` 是默认值真源。proxy 产品路径采用 baseline-first incremental policy：先把
source melody 的 onset/pitch/duration anchors 原样求解为 GREEN 基线，每个候选只调用一次模型提议；
允许的增量包括 bass、harmony，以及只落在源旋律真实静音 gap 内、不覆盖 source melody 发声区间的
安全 melody fills。确定性调度把全曲 bass 骨架放入 layer 1、harmony/fill 放入 layer 2。每个候选
最多进行 8 次全曲 solver/oracle 检查，跨 layer 轮转且每层内部 breadth-first，避免 bass 拆分饿死
harmony/fill；任一批次若不再 GREEN、改写原旋律 anchors、降低已有
faithfulness 或没有确定性的音乐覆盖收益，就回滚到上一个 GREEN checkpoint。模型输出不可用或没有
增量通过时，结果明确标为 deterministic baseline，不冒充 Agent 贡献。

benchmark v2 的 legacy policy 及离线兼容入口仍保留 `n=1`、`max_iters=0`、`use_critic=false` 默认值；
`max_iters` 不控制 proxy incremental policy 的 8 次确定性检查。`n` 仍控制独立 proposal 数，critic 仅在
候选已有被接受的 Agent 增量时对最终 checkpoint 运行。

离线示例：

```bash
curl --fail-with-body \
  -H 'Content-Type: application/vnd.recordare.musicxml+xml' \
  --data-binary @score.musicxml \
  'http://127.0.0.1:8000/api/v1/arrangements?filename=score.musicxml&engine=offline&n=1&max_iters=0&use_critic=false'

curl --fail-with-body \
  -H 'Content-Type: audio/midi' \
  --data-binary @melody.mid \
  'http://127.0.0.1:8000/api/v1/arrangements?filename=melody.mid&engine=offline&n=1&max_iters=0&use_critic=false'

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @arranged-tab.json \
  -o fretsure-arrangement.mid \
  'http://127.0.0.1:8000/api/v1/exports/midi?tempo_bpm=96'

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @arranged-tab.json \
  -o fretsure-guitar-tablature.txt \
  'http://127.0.0.1:8000/api/v1/exports/tab-text'

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @arranged-tab.json \
  -o fretsure-guitar-tablature.musicxml \
  'http://127.0.0.1:8000/api/v1/exports/musicxml-tab?tempo_bpm=96'

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @arranged-tab.json \
  -o fretsure-guitar-tab.gp5 \
  'http://127.0.0.1:8000/api/v1/exports/guitar-pro?tempo_bpm=96'

curl --fail-with-body \
  -H 'Content-Type: application/json' \
  --data-binary @arranged-tab.json \
  -o fretsure-guitar-tab.pdf \
  'http://127.0.0.1:8000/api/v1/exports/pdf-tab?tempo_bpm=96'
```

Web 结果页中的 MusicXML TAB 与 GP5 是可编辑交换文件；PDF 是打印谱；MIDI 是试听/播放器输入；
ASCII `.txt` 便于快速核对；canonical Tab JSON 是机器可读的精确结果，适合归档、重放或继续交给 API。
这些导出都直接读取同一份 canonical Tab，MIDI 不能代替 canonical Tab 或指法谱。

MIDI 导出是确定性的 format-0 SMF：480 PPQN、单轨、单 MIDI channel、General MIDI 尼龙弦吉他音色。
同一 onset 的和弦音保留在同一 tick；同 tick 先写 note-off 再写 note-on，以正确重奏重复音。它是符号
演奏文件而不是合成音频，实际音色由播放器的 MIDI 音源决定。SMF 的 tempo 字段只有 24 bits；对
产品域内低于其直接表示范围的慢速 tempo，导出器会确定性放大 timeline ticks 并相应提高编码 tempo，
使实际播放时长仍等于请求 tempo，而不是拒绝已经成功生成的结果。

Tab text 导出直接读取同一份 canonical Tab，不从 MIDI 音高反推。文件上半部分是 high-e 在上的标准
六线 ASCII tab；下半部分按原 Tab note 顺序逐行保留 exact onset/duration、常规吉他弦号 1..6、内部
canonical string 0..5、fret、left_finger 与 right_finger，因此视觉谱面与求解器给出的双手指法可以
交叉核对。`left_finger=0` 表示空弦，右手使用 `p/i/m/a`。

MusicXML 导出使用 4.0 `score-partwise`、六线 TAB clef/staff tuning、精确 integer divisions、多声部、
跨小节 ties，以及 `technical` 中的 string/fret/fingering/pluck。Guitar Pro 导出是真实 GP5 5.1 binary，
不是把 JSON 或 MIDI 改扩展名；自动兼容性证据是 PyGuitarPro 写出后回读，标准 GP5 5.1 预期可由
Guitar Pro/TuxGuitar 导入，但尚未把这两个桌面应用的实开结果纳入自动验收。GP5 只存整数 BPM，
因此请求的小数 tempo 会按最近整数、`.5` 向上确定性取整；CP1252 无法表示的元数据仍 typed 422。
PDF 使用 A4 矢量几何分页，包含 tempo/tuning/capo、小节与节拍、节奏 token、品位和双手指法图例。

`exports` extra 的 GP5 writer PyGuitarPro 使用 LGPL-3.0-only；PDF writer ReportLab 使用 BSD 许可。
pdfplumber（MIT）和 pypdf（BSD）只用于 `dev` 测试，依赖版本均由 `uv.lock` 固定，仓库不复制其源码。

成功结果明确区分 `tab_produced` 与 `no_fingering_within_budget`，并返回 source provenance、import
warnings、独立 playability / availability-aware faithfulness、ASCII tab、`agent-trace@0.2.0` replay rows
和全部版本 stamps；score capabilities 盖 `score-input@0.1.0` 的 format→importer registry，arrangement
结果同时盖 router 与实际 `musicxml@0.3.0` / `midi@0.1.0`，纯 Tab check/solve/render
不冒充经过 importer。失败使用
`application/problem+json`，不会返回 provider exception、traceback、secret 或任意本机路径。

Producer 兼容性只由 exact artifacts 证明：当前 manifest 中冻结的 MuseScore Studio 4.7.4 XML/MXL
bytes/root 可贯通 Web/API/application；这不代表该版本的任意乐谱、其他 MuseScore 版本或完整
MusicXML。runtime 精确锁定 `music21==10.5.0`。范围、逐文件 census 与最终门见
[`SCOPE.md`](SCOPE.md)、
[`2026-07-16-producer-musicxml-census.json`](experiments/2026-07-16-producer-musicxml-census.json) 与
[`PRODUCER_MUSICXML_ACCEPTANCE.md`](PRODUCER_MUSICXML_ACCEPTANCE.md)。

MIDI corpus 另外冻结两正两负：MuseScore Studio 4.7.4 melody-only 正例保留 7 beats 与每音 1 tick
release gap，music21 10.5.0 正例保留 8 beats；两个 realized-harmony 导出 typed 拒绝。它们不证明任意
MuseScore/music21 文件、通用多轨 MIDI 或完整 MIDI 1.0。逐文件证据和待最终关闭的 gates 见
[`2026-07-17-midi-census.json`](experiments/2026-07-17-midi-census.json) 与
[`MIDI_ACCEPTANCE.md`](MIDI_ACCEPTANCE.md)。HTTP 对 `.mid/.midi` 只接受 canonical `audio/midi`，
10 MiB 上限以及 suffix/media 门在 body/engine 前执行。

### 显式启用本地代理

代理默认关闭。只有同时提供 loopback proxy URL、非空 token，并在启动时加 `--allow-proxy`，服务才会
公布并接受固定的 canonical `gpt-5.6-sol`：

```bash
export ANTHROPIC_BASE_URL='http://127.0.0.1:8317/v1'
export ANTHROPIC_AUTH_TOKEN='...'
uv run fretsure-serve --allow-proxy
```

缺 URL、缺 token、非 loopback URL、缺可选依赖或未显式授权都会在任何模型网络请求前失败。请求方不能
覆盖 model、base URL、token 或任意 provider 参数。

## MCP stdio server

```bash
uv sync --extra mcp
uv run fretsure-mcp
```

stdout 只承载 MCP protocol。server initialize identity 是 `Fretsure` / `fretsure-mcp@0.2.0`，提供：

- `check_playability`：复用 application/core 的 strict Tab JSON checker。
- `feasible_fingerings`：严格 `target-input@0.1.0` 的有界搜索；最多一个解，永远返回
  `search_complete=false`，未找到不等于不可解证明。
- `render_notation`：Plan 6A 只支持 `ascii`。
- `fretsure://capabilities`：版本、资源上限与 deferred 能力。

没有 `render_audio`，也没有远程 URL 导入。

已通过官方 in-memory session 和真实 stdio subprocess 的 initialize/list/call/invalid-call-survival
验证。下面是 Claude Desktop / Cursor 兼容的配置格式；本阶段未把“写过配置”冒充成在用户本机 GUI 中
实际点击验证：

```json
{
  "mcpServers": {
    "fretsure": {
      "command": "uv",
      "args": [
        "--directory",
        "/absolute/path/to/fretsure",
        "run",
        "fretsure-mcp"
      ]
    }
  }
}
```

若已安装 wheel，可把 `command` 换成该虚拟环境中 `fretsure-mcp` 的绝对路径，并删除 `args`。

## Plan 6A 之后仍未实现

通用多轨/角色映射 MIDI 输入、AlphaTab、音频/FluidSynth、播放同步、真实琴颈动画、
原生 GP7 `.gp`、WebSocket/SSE、live A/B、
live leaderboard、账户/数据库/云部署、真人 calibration 与完整 Plan 6 money moment 均保持 open。
