# v0.5.0 界面证据（Before / After）

> 这里的三张图用于 PR 的视觉对比。**页面里的账号和签到结果全部是虚构的**，
> 不含任何真实 BDUSS、掩码或账号名。

## 场景与参数

- 版本：Before = `origin/main`（0.4.0，提交 `fd99ca0`）；After = v0.5.0 分支。
- 页面：签到页（`/sign`）与新增的签到报告页（`/sign/report`）。
- 状态：同一份虚构结果——1 个账号、检查 6 个待签吧、成功 3 / 失败 3；
  失败码分别是 `111`（吧不存在）、`340011`（签得太快）、`340006`（目录异常），
  外加一次 msign 失败。After 额外显示成长等级签到与 warnings。
- 视口：500 × 1600 CSS px，`devicePixelRatio=2`（输出 1000 × 3200），
  浅色主题（`prefers-color-scheme: light`）。
- 渲染：真实页面 HTML/CSS/JS（`app/main.py` 的 `_page_sign()` /
  `_page_sign_report()`），用无头 Chrome 加载并截图；不是手绘 mockup。
- 报告页截图：500 × 900 CSS px，其余相同。

## 文件

| 文件 | 内容 |
|---|---|
| `before-sign.png` | 0.4.0 签到页：失败只剩「错误码 X」，分类一律「风控其他」；没有报告入口 |
| `after-sign.png` | 0.5.0 签到页：每个失败带错误码 + 中文原因；msign 失败进「提醒」；显示成长等级签到；有「查看 / 复制最近一次签到报告」 |
| `after-report.png` | 新增的签到报告页：可复制的纯文本，含错误码原因、警告与失败清单；明确标注只保留 BDUSS 掩码 |

## 复现方式

证据 HTML 由 `ui_evidence/render_ui.py` 生成（未入库，避免把测试夹具混进产品源码）：
它对指定版本的 `app/` 注入一份虚构状态，再用无头 Chrome 截图。关键点是
**注入的是页面自己会调用的 `renderSignState()`**，所以布局、颜色、字体、
换行都与真机页面一致。
