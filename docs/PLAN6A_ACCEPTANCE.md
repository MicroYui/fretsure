# Plan 6A 验收记录

日期：2026-07-16

版本：`fretsure 0.3.0` / `fretsure-service@0.1.0` / `agent-trace@0.1.0` /
`fretsure-api@0.1.0` / `fretsure-mcp@0.1.0` / `fretsure-web@0.1.0`

## 结论

Plan 6A 已通过软件、边界安全、分发包和视觉验收。它交付的是 replay-first 的
MusicXML/MXL → application service → API/MCP/Web 薄纵切，不包含音频、AlphaTab、实时播放、
导出互操作、live A/B、benchmark v2 或真人可弹保证；这些项目继续保持 open。

## 最终质量门

| 门 | 结果 | 证据摘要 |
| --- | --- | --- |
| Python 离线回归 | PASS | `1494 passed, 6 deselected` |
| 真实模型集成 | PASS | `6 passed, 1494 deselected`，实际模型 `gpt-5.6-sol` |
| 静态检查 | PASS | ruff、strict mypy、`uv lock --check`、`git diff --check` |
| 文档 | PASS | Markdown 本地链接检查覆盖仓库全部文档 |
| 前端 | PASS | clean `npm ci` 后 20 tests、typecheck、production build、audit |
| API | PASS | MusicXML/MXL raw-body、typed failures、OpenAPI、static SPA、Host/Origin/CSP |
| Trace | PASS | diagnostic → edit → recheck → select 顺序、失败态、双 gate、预算与 redaction |
| MCP | PASS | in-memory initialize/list/call 与真实 stdio subprocess 三工具调用 |
| 分发包 | PASS | wheel/sdist 内容审计；core/MusicXML/service/MCP clean-install smoke |
| 浏览器 | PASS | desktop/375 px、键盘 focus、overflow、XSS、network/console |

FastAPI/Starlette TestClient 会输出一条针对未来 httpx 2 的上游迁移 warning；运行时项目代码没有产生
对应 warning，也没有被它掩盖的失败。

## 真实行为证据

- 真实 proxy HTTP arrange 返回 `tab_produced`，trace 共 6 steps，响应盖
  `gpt-5.6-sol` 和 12 个必备 checker/schema/profile stamps；本次结果为 playability `AMBER`、
  faithfulness `PASS`，证明两道门保持独立，UI 不会把它标成“双门通过”。
- bytes/path MusicXML importer 对同一输入的 IR、warning、hash 与 provenance 一致；`.mxl` 使用相同
  application seam。
- API、MCP、CLI 与 Web 不复制 checker 或 importer；它们消费同一 strict service contracts。
- public trace 不包含 prompt、raw model reply、transport exception、secret 或 traceback；完整 replay
  checkpoint 受逐项与 aggregate budgets 约束。
- 默认 engine 是 deterministic offline stub；proxy 必须在服务启动时显式授权，并且只接受 loopback
  proxy URL 与 token，不允许请求方指定任意 endpoint/key/model。

## 独立审计闭环

实现期间进行了独立的验收、安全和前端合同审计。发现并修复的主要问题包括：

- trace winner chronology、failure replay 与 terminal playability/faithfulness 双 gate；
- HTTP Host/Origin DNS-rebinding 防护、proxy fail-closed 与 OpenAPI response schemas；
- 前端对 capabilities/arrange/oracle 响应的运行时校验，以及 hostile metadata 的 plain-text 渲染。
- MCP implementation identity 依赖当前 FastMCP v1.28 的适配点，因此依赖范围收窄到已验证 minor，
  initialize 回归负责在未来升级时 fail fast。

修复后重跑对应定向测试和全量门，没有剩余发布 blocker。

## 用户视觉验收

用户在真实桌面与移动端浏览器结果上于 2026-07-16 明确认可：

> 这个前端做的挺好看的，就按照这种带点古典的风格来，我审核通过了

最终方向是偏古典制琴工坊的暖色界面，同时保留清晰的双 gate、ASCII tab 和 trace 诊断密度。

- [桌面入口](assets/plan6a/desktop-landing.jpg)
- [桌面结果](assets/plan6a/desktop-result.jpg)
- [桌面 trace](assets/plan6a/desktop-trace.jpg)
- [移动入口](assets/plan6a/mobile-landing.jpg)
- [移动结果](assets/plan6a/mobile-result.jpg)

## 后继与真人审计点

下一项是 producer-driven MusicXML/IR 扩展，之后依次是 MIDI 与 benchmark v2；这些阶段以自动化合同
和实验数据为主，不需要用户逐步审美验收。进入完整 Plan 6B 后，界面/琴颈动画、音频体验、真人
A/B/榜单和实际演奏 calibration 会再次设置明确的用户审计 gate。

使用和边界说明见 [`WEB_API_MCP.md`](WEB_API_MCP.md)，执行计划与逐项闭门清单见
[`Plan 6A`](superpowers/plans/2026-07-16-plan-6a-web-api-trace-mcp.md)。
