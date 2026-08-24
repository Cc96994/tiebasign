# 贴吧签到助手 (tieba_sign_android)

基于 [aiotieba](https://github.com/Starry-OvO/aiotieba) 4.7.1 的百度贴吧多账号签到 Android App（首期仅 Android）。

- 一键签到：msign 网页一键签到 + 关注吧分页查漏补签，实时进度、成功/失败数、失败吧名 + 原因分类
- 账号：手动输入 BDUSS / App 内置 WebView 登录百度官方页自动提取 Cookie；多账号增删改切换
- 自动签到：多账号、全局 08:00 + 每账号自定义时间、前台服务保活、仅失败通知（系统通知 + App 内红点双独立开关）、不自动重试、仅保存最近一次结果快照
- 安全：BDUSS 只用 Android Keystore AES-GCM 加密存本机，任何页面不显示完整 BDUSS；不保存明文密码、无签到历史、无跨设备恢复

## 版本与下载
- 当前正式版：**0.3.0**（arm64-v8a，minSdk 23 / Android 8.0+，targetSdk 34）
- 安装包在 **GitHub Releases** 页面下载：`tieba-sign-assistant-0.3.0-arm64-v8a-release.apk`（附 `SHA256SUMS` 校验文件）
- 若手机安装过同版本 debug 测试包，请先卸载再安装正式版（签名不同）

## 技术架构
- Python 3.12.7 + aiotieba 4.7.1（NDK 编译 crypto 扩展 + certifi SSL 补丁 + 纯 Python protobuf 7.36.0）
- python-for-android **webview bootstrap**（无 Kivy/SDL）：Python 在 127.0.0.1:5000 起本地 HTTP 服务，WebView 渲染 MD3 风格前端
- pyjnius 桥接 Android Keystore / CookieManager / 前台服务 / 通知
- buildozer 1.5.0（WSL Ubuntu）打 APK

## 目录
- `app/`               正式版源码（main.py / signer.py / autosign.py / weblogin.py / keystore_crypto.py / storage.py / tieba_api.py / diag.py / icon.png）
- `buildozer_webview_app.spec`  正式版构建配置（依赖全锁版）
- `p4a_local/`         定制配方：aiotieba（NDK 编 crypto + HTTPS/certifi 补丁 + `-Wl,-z,global`）、protobuf（纯 Python）、zlib
- `scripts/`           构建 / 核验 / 扫描脚本（wsl_app_build.sh、wsl_release_build.sh、leak_scan.sh、verify_*.sh）
- `SIGNING.md`          发布签名说明（签名材料本身绝不提交到 Git）
- `CREDITS.md`         第三方致谢（含 TiebaLite 说明）

## 硬性安全约束
- 本项目及产物**绝不包含任何真实 BDUSS、BDUSS.txt 或旧脚本**；旧目录 `tieba_sign_app` 不复制、不打包、暂不修改。
- 打包前后必须执行 `scripts/leak_scan.sh`，要求 `RESULT: CLEAN`。
- 签名密钥库 `sign/tiebasign-release.keystore` 与 `sign/signing.env` 必须离线备份、绝不入库、绝不外传。

## 构建
环境：Windows + WSL2 Ubuntu，首次按 `scripts/wsl_setup.sh` 准备；日常：

```bash
# debug（快速迭代）
bash /mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android/scripts/wsl_app_build.sh
# release（需 sign/signing.env 与 keystore 就绪）
bash /mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android/scripts/wsl_release_build.sh
```

产物：`bin_m22/tiebaweb-<ver>-arm64-v8a-{debug,release}.apk`，最终核验 `scripts/verify_release.sh`。

> 注意：脚本内的项目路径基于本机 `C:\Users\s7909\...`，克隆到其它机器需先改成自己的路径。

## 免责声明
- 本应用**非百度官方产品**，仅供个人学习与账号签到使用。
- 使用请遵守百度贴吧相关服务条款，任何账号风险（含风控、限制登录）由使用者自行承担。
- 本项目不保存签到历史，不提供跨设备恢复；卸载应用或清除数据前请自行备份账号信息。

## 许可证
- 本项目自身代码：目前未随仓库附带 LICENSE 文件；如打算公开分发，建议在创建仓库时选择 **Apache-2.0**（GitHub 会自动生成 LICENSE）。
- 依赖：aiotieba / aiohttp / lxml / protobuf / cryptography 等，按各自许可证。
- 登录形态参考 TiebaLite（GPL-3.0）：仅参考公开网址与技术思路，未复制其代码，详见 `CREDITS.md`。
