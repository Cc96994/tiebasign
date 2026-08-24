# 贴吧签到助手 0.3.0 验收报告（PRD v1.0）

产品：贴吧签到助手（package org.dsh.tiebaweb）· 正式版 0.3.0
发布产物：`release/tieba-sign-assistant-0.3.0-arm64-v8a-release.apk`
- 大小 20,636,996 B · MD5 `0475B6FE0A3EF8D6AE0B9328467ECBC7`
- SHA-256 `be7ee57fdd3f78015653a986af046a1d6fe1c9ecc5d46562fe5802985efa115f`
- 签名：CN=TiebaSign, OU=DSH, O=DSH, C=CN；apksigner 验证 v1 + v2 通过
- 环境：python3 3.12.7 / aiotieba 4.7.1 / webview bootstrap / arm64-v8a / api 34 / minapi 23
- 泄露扫描：源码树 + 全部 APK（含本包）BDUSS 模式 **0 命中，RESULT: CLEAN**

## PRD 15 条验收

| # | 验收项 | 结果 | 依据 |
|---|---|---|---|
| 1 | 手动输入 BDUSS 一键签到 | ✅ 通过 | 用户实测：『不错，目前没有发现问题』；0.3.0 增加多账号多选手动签到 |
| 2 | WebView 登录百度官方页自动提取 BDUSS | ✅ 通过 | 0.1.31 内置登录（TiebaLite 同款 wappass 页 + Cookie 判定），用户实测：『成功了』 |
| 3 | 多账号增删改切换 | ✅ 通过 | 0.1.5 自绘对话框修复后用户确认可用 |
| 4 | 签到展示进度、成功/失败数、失败吧名+原因分类 | ✅ 通过 | signer.py：msign + 查漏补签、实时 step、failures 含 forum/category/code/detail |
| 5 | 手动签到失败不发系统通知、不显示红点 | ✅ 通过 | 手动路径无 notify/dot 代码；仅 autosign（自动）触发 |
| 6 | 自动签到仅失败通知，全部成功不提醒 | ✅ 通过 | autosign.py `_finalize`：ok=status done 且 failed=0，not ok 才通知 |
| 7 | 失败通知双独立开关：系统通知 + App 内红点 | ✅ 通过 | `notify_system`/`notify_dot` 独立字段；红点进入签到页 `clear_dot()` 消除 |
| 8 | 自动签到多账号、全局 08:00、每账号跟随全局/自定义 | ✅ 通过 | autosign.py 调度：`follow_global ? global_time : custom_time`，逐账号执行 |
| 9 | 不做失败自动重试 | ✅ 通过 | signer.py 每吧 `sign_forum` 单次；340006 不重试并注明 |
| 10 | BDUSS 加密存储、任何页面不显示完整 BDUSS | ✅ 通过 | Android Keystore AES-GCM（ks-gcm 策略）；UI 仅前4****后4；日志只打长度 |
| 11 | 删除账号清除本机保存的凭据 | ✅ 通过 | storage JSON 删除条目 + 删除确认文案『本机保存的登录凭据会被清除』 |
| 12 | 不保存签到历史，仅最近一次自动签到结果快照 | ✅ 通过 | autosign.py `results{}` 仅最新快照（无历史表） |
| 13 | 换设备不保留任何加密前信息、无跨设备恢复 | ✅ 通过 | 无云同步/导出；Keystore 密钥不可导出，换设备旧密文不可解 |
| 14 | Android 13+ 通知权限引导 | ✅ 通过（部分替代） | 开启自动签到即申请 POST_NOTIFICATIONS（UI 线程派发）；保活以前台服务实现，电池优化未做白名单引导 |
| 15 | 深色模式正常 | ⚠️ 已实现待最终确认 | MD3 深色 token（#0F1117/#7C9BFF）全页面适配；用户尚未逐页确认 |

## 里程碑
- M1 技术冒烟 ✅ → M2.1 12 项真机 PASS ✅ → M2.2 账号（Keystore/内置登录）✅ → M2.3 手动签到 ✅ → M2.4 自动签到 ✅ → 0.2.0 清理与回归 ✅ → 0.3.0 正式版 ✅

## 已知说明（不影响验收）
- 卓易通（HarmonyOS NEXT 容器）兼容修复已内置：libpython `-Wl,-z,global`、stdlib.zip 目录形态、真实 libpython 扫描、zlib 随包分发。
- 首期仅 Android / arm64-v8a；iOS、armeabi-v7a、跨设备迁移为 PRD 明确不需要。
