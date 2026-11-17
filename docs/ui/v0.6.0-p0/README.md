# v0.6.0 P0 视觉证据（Before / After）

> 两张图是**真实页面 HTML**（`app/main.py` 的 `_page_sign_report()`）用无头
> Chrome 渲染的截图。页面里的账号、错误码和分段耗时全部是虚构的，不含任何
> 真实 BDUSS、掩码或账号名。

## 场景与参数

- **Before** = `origin/main`（v0.5.6，提交 `0e34bbe`）；**After** = v0.6.0 P0 分支。
- 页面：签到报告页（`/sign/report`）。
- 状态：同一份虚构报告——1 个账号，检查 16 个待签吧，成功 13 / 失败 3；
  失败码分别是 `111`（吧不存在）、`340011`（签得太快）、`340006`（目录异常）；
  另有 1 次 msign 失败进警告；`phase_ms` 的分段耗时是
  `login 1.2s / growth 0.9s / msign 2.8s / list 5.3s / sign 32.6s`
  （请求 29.5s + 抖动 3.2s），总耗时 42.6 秒。
- 视口：`500 × 900` CSS px，`devicePixelRatio = 2`（输出 1000 × 1800）。
- 主题：深色（`prefers-color-scheme: dark`），Before/After 相同。
- 渲染：真实页面 HTML + 无头 Chrome；报告 JSON 由临时脚本注入
  `ANDROID_PRIVATE`，脚本不入库（避免把测试夹具混进产品源码）。

## 文件

| 文件 | 内容 |
|---|---|
| `before-report.png` | 0.5.6 报告页：只有总耗时 42.6 秒，看不出登录 / msign / 拉列表 / 补签各花多久 |
| `after-report.png` | 0.6.0 P0 报告页：新增「分段耗时」与「补签明细（请求 + 抖动）」，其余布局与文案不变 |

## 说明

- 这两张图只证明 P0（`phase_ms` 落盘与展示、错误码保留）的界面变化，不代表
  签到/点赞提速——提速要到 M1/M2 之后再用隔天首跑的 `phase_ms` 对比。
- 老报告（0.5.6 写的 `tiebasign_sign_last.json`）没有 `phase_ms` 字段，新代码
  会跳过这两行，不会显示 0.0 秒。
