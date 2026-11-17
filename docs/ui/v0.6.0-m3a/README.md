# v0.6.0 M3a 视觉证据（Before / After）

> 截图都是**真实页面**（`app/main.py` 的 `_page_settings()` /
> `_page_settings_like()` / `_page_settings_sign()` / `_page_sign_report()`），
> 由 App 自己的 HTTP 服务渲染，无头 Chrome 截图。账号、错误码、计数全部虚构，
> 不含真实 BDUSS、掩码或账号名。

## 场景与参数

- **Before** = `origin/main`（v0.5.6，提交 `0e34bbe`）；**After** = M3a 分支。
- 主题：**浅色**（`preferredColorScheme=light`），Before/After 相同；
  视口 `500 × 900`（设置首页/签到速度页）、`500 × 1100`（随机点赞设置页）、
  `500 × 900`（报告页）CSS px，`devicePixelRatio = 2`。
- 档位：`标准`（level 1），签到并发 4；点赞并发 5、间隔 0.25–0.50 秒。
- 随机点赞设置页：自动点赞默认开启、跟随自动签到全局时间 08:00；Before 与
  After 用同一份自动设置，差异只有新增的速度档位卡片与按钮文案
  「保存设置 → 保存自动设置」。
- 报告页：同一份虚构报告，检查 17 个吧、成功 12、服务端返回已签到 2、失败 3，
  `phase_ms` 为 login 1.2s / growth 0.9s / msign 2.8s / list 5.3s / sign 32.6s。

## 文件

| 文件 | 内容 |
|---|---|
| `before-settings.png` | 0.5.6 设置首页：没有「签到速度档位」入口 |
| `after-settings.png` | M3a 设置首页：新增「签到速度档位」入口 |
| `before-like-settings.png` | 0.5.6 随机点赞设置：只有每日自动开关/时间 |
| `after-like-settings.png` | M3a 随机点赞设置：新增速度档位滑块（蓝/黄/灰红区、折叠风险提示） |
| `after-sign-settings.png` | M3a 新增的「签到速度」页（0.5.6 没有这一页，所以只有 After） |
| `before-report.png` | 0.5.6 报告：没有速度档位行 |
| `after-report.png` | M3a 报告：新增「速度档位：标准（签到并发 4）」，并保留已签到与分段耗时 |

## 说明

- 红档（危险/危险+）在 0.6.0 只做展示：轨道红区是灰色，松手会自动弹回
  「极速」并提示未解锁；服务端也会拒绝保存 level 4/5。滑块仍是
  `min=0 max=5 step=1`，0.6.1 上硬停/熔断后解锁时只改一个开关。
- `after-sign-settings.png` 是新增页面，没有对应的 Before 页面；入口层面的
  Before/After 用设置首页那一对证明。
- 界面里的「关于」仍写 0.5.6——版本号按清单留到 0.6.0 发布阶段统一改。
