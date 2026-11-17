# v0.6.0 M3b 视觉证据（Before / After）

> 截图都是**真实页面**（`app/main.py` 的 `_page_settings_sign()` / `_page_sign()`），
> 由 App 自己的 HTTP 服务渲染，无头 Chrome 截图。账号、错误码、计数全部虚构，
> 不含真实 BDUSS、掩码或账号名。

## 场景与参数

- **Before** = M3a（提交 `08455a9`：红档未解锁）；**After** = M3b（本版 0.6.0）。
- 主题：浅色（`preferredColorScheme=light`），Before/After 相同。
- 签到速度页（`/settings/sign`）：视口 `500 × 900` CSS px、DPR 2。
  - 解锁前：level 3「极速」，红区灰色，提示「危险档（4–5）在 0.6.0 未解锁」。
  - 解锁后：同一 level 3，红区显示为红色；level 4「危险」选中时红色滑块 + 风险提示。
  - 二次确认：真实页面 HTML 强制展开的对话框（文案由页面 JS 生成；JS 行为由
    `context/tier_js_test.js` 在 Node 里逐条验证：取消不保存、确定才保存）。
- 签到页熔断（`/sign`）：12 个待签吧，其中 1 个返回 `340011`。Before 是 0.5.6
  的界面（11 成功 / 1 失败，没有提示）；After 多一行「检测到限流，已自动降到
  「保守」档；触发时还剩 8 个吧」。
- 签到页硬停（`/sign`）：12 个待签吧，第 1 个要求验证码。Before 是 0.5.6 继续
  签完（11 成功 / 1 失败）；After 在检查 4 个后停止，跳过 8 个并显示错误原因与
  「怎么办」。
- 视口 `500 × 1100` CSS px、DPR 2。

## 文件

| 文件 | 内容 |
|---|---|
| `before-sign-settings.png` | M3a：极速档，红区灰、显示未解锁提示 |
| `after-sign-settings.png` | M3b：极速档，红区正常显示，无未解锁提示 |
| `after-sign-settings-red.png` | M3b：危险档选中（红色滑块 + 风险提示） |
| `after-sign-confirm.png` | M3b：危险档二次确认对话框（取消 / 确定使用） |
| `before-sign-breaker.png` | 0.5.6：撞 340011 只记 1 个失败，继续跑完 |
| `after-sign-breaker.png` | M3b：撞 340011 自动降速到保守档，其余吧继续完成 |
| `before-sign-hardstop.png` | 0.5.6：要求验证码仍继续逐吧签完 |
| `after-sign-hardstop.png` | M3b：要求验证码立刻硬停，跳过剩余 8 个并报错 |

## 说明

- 二次确认对话框截图是把页面真实 HTML 里的 `display:none` 改成 `display:flex`
  后截的展示态；确认/取消的交互逻辑由 Node 测试覆盖（不通过 `confirm()`）。
- 危险档的并发/延迟来自 `app/tiers.py` 的唯一映射表；熔断只临时降速，不修改
  用户保存的档位。
- 版本号已改为 0.6.0（versionCode 1023600）；APK 构建产物放在
  `G:\学习\aiotieba\apk`，本 PR 不发布。
