# 贴吧签到助手 (tieba_sign_android)

本软件由AI生成，该软件仅用于网络编程、接口分析的个人技术学习研究目的，仅供学习，下载完请在24h内删除

基于 [aiotieba](https://github.com/Starry-OvO/aiotieba) 4.7.1 的百度贴吧多账号签到 Android App（首期仅 Android）。

- 一键签到：msign 网页一键签到 + 关注吧分页查漏补签，实时进度、成功/失败数、失败吧名 + 原因分类
- 账号：手动输入 BDUSS / App 内置 WebView 登录百度官方页自动提取 Cookie；多账号增删改切换
- 自动签到：多账号、全局 08:00 + 每账号自定义时间、前台服务保活、仅失败通知（系统通知 + App 内红点双独立开关）、不自动重试、仅保存最近一次结果快照
- 自动水贴：**每个账号带自己的一组目标帖**（一个账号只发自己那组）；某账号当天签到完成后才开始，按固定间隔（默认 5 秒）发主楼回复，每天每帖默认 4 次；文案可自定义并顺序轮换/随机
- 一键水贴：签到页上把勾选账号**当天还差的次数**一次发完（发满后当天不再自动发）
- 检查更新：GitHub 优先 + Gitee 备胎双源、每天自动查一次、**没查到什么都不提示**、查到才亮红点 + 弹通知；下载后校验 sha256，安装前再验包名/versionCode/**签名指纹**，然后唤醒系统安装界面（不做静默安装）
- 安全：BDUSS 只用 Android Keystore AES-GCM 加密存本机，任何页面不显示完整 BDUSS；不保存明文密码、无签到历史、无跨设备恢复

## 版本与下载
- **线上正式版：0.3.0**（arm64-v8a，minSdk 23 / Android 8.0+，targetSdk 34）——
  安装包在 **GitHub Releases** 下载：`tieba-sign-assistant-0.3.0-arm64-v8a-release.apk`（附 `SHA256SUMS`）
- **0.4.0 已在本地构建并通过 `verify_release.sh` 全部核验，等上传**：
  `release/` 下已有 `tieba-sign-assistant-0.4.0-arm64-v8a-release.apk` + `update.json` + `SHA256SUMS`。
  发布时要**三个文件一起**挂到同一个 tag 下（`update.json` 是 App 判断版本与校验的关键，见下文）
- 若手机安装过同版本 debug 测试包，请先卸载再安装正式版（签名不同）

## 技术架构
- Python 3.12.7 + aiotieba 4.7.1（NDK 编译 crypto 扩展 + certifi SSL 补丁 + 纯 Python protobuf 7.36.0）
- python-for-android **webview bootstrap**（无 Kivy/SDL）：Python 在 127.0.0.1:5000 起本地 HTTP 服务，WebView 渲染 MD3 风格前端
- pyjnius 桥接 Android Keystore / CookieManager / 前台服务 / 通知
- buildozer 1.5.0（WSL Ubuntu）打 APK

## 目录
- `app/`               正式版源码（main.py / signer.py / autosign.py / waterpost.py / **updater.py** / weblogin.py / keystore_crypto.py / storage.py / tieba_api.py / diag.py / icon.png）
- `java_src/`          App 自己的 Java（`org/dsh/tiebaweb/`：KeystoreHelper / TiebaSignKeepAliveService / **ApkProvider**）+ `PythonActivity.updater.inc` 片段
- `buildozer_webview_app.spec`  正式版构建配置（依赖全锁版）
- `p4a_local/`         定制配方：aiotieba（NDK 编 crypto + HTTPS/certifi 补丁 + `-Wl,-z,global`）、protobuf（纯 Python）、zlib
- `scripts/`           构建 / 核验 / 扫描脚本（`app_identity.sh` 标识单一来源、`wsl_app_build.sh` debug、
                       `wsl_release_build.sh` release、`leak_scan.sh`、`verify_release.sh`、`check_upgrade_compat.sh`、
                       `patch_base_java.py` 把 `java_src/` 打进 p4a 底座、`gen_update_json.sh` 生成发布用的 update.json、
                       `run_tests.sh` + `tests/` 离线自检）
- `_legacy/`           已归档的实验目录与一次性脚本（**不参与构建**，见其中 README）
- `ROADMAP.md`         下一版计划与"不可变三事实"
- `SIGNING.md`          发布签名说明（签名材料本身绝不提交到 Git）
- `CREDITS.md`         第三方致谢（含 TiebaLite 说明）

## 硬性安全约束
- 本项目及产物**绝不包含任何真实 BDUSS、BDUSS.txt 或旧脚本**；旧目录 `tieba_sign_app` 不复制、不打包、暂不修改。
- 打包前后必须执行 `scripts/leak_scan.sh`，要求 `RESULT: CLEAN`。
- 签名密钥库 `sign/tiebasign-release.keystore` 与 `sign/signing.env` 必须离线备份、绝不入库、绝不外传。

## 构建
环境：Windows + WSL2 Ubuntu，首次按 `scripts/wsl_setup.sh` 准备；日常：

```bash
cd /mnt/g/学习/aiotieba/tieba_sign_android        # 脚本自定位，路径随项目走
# 离线自检（三个套件，几秒钟，不联网）：改了 Python 先跑这个
bash scripts/run_tests.sh
# debug（快速迭代）
bash scripts/wsl_app_build.sh
# release（需 sign/signing.env 与 keystore 就绪）
bash scripts/wsl_release_build.sh
# 发布核验（默认核验 spec 版本对应的 release 包；--verify-only 不写任何东西）
bash scripts/verify_release.sh
```

产物：`bin_m22/tiebaweb-<ver>-arm64-v8a-{debug,release}.apk`。
`verify_release.sh` 通过后，`release/` 里会有可直接上传的三件套：
APK、`update.json`（版本/versionCode/sha256，App 用它判版本）、`SHA256SUMS`。

> 两个构建脚本都会先跑 `scripts/patch_base_java.py`：App 的原生能力（原生登录、
> 保活服务、Keystore、以及 0.4.0 的安装入口/ApkProvider）都住在**仓库之外**的 p4a
> 底座里。那个脚本把 `java_src/` 打进去，并且**同时刷新 p4a 已经缓存的 dist 副本** ——
> p4a 只在新建 dist 时拷贝一次，之后复用 dist 就不再刷新，只改底座会让构建静默编出旧代码
> （实测过一次：新方法一个都没进 dex）。

> 脚本全部自定位（项目搬到哪都行），不再写死 `C:\Users\s7909\...`。唯一带默认值的
> 是 WSL 侧副本目录 `/home/s7909/tieba_sign_android`，可用环境变量 `TIEBA_PROJ_WSL` 覆盖。

> 改版本号要改**三处**：`buildozer_webview_app.spec` 的 `version =`（权威）、
> `app/main.py` 里渲染给用户看的 `贴吧签到助手 <版本>`、以及 `app/updater.py` 开头的
> `_APP_VERSION` / `_APP_VERSION_CODE`（检查更新用它判断"是否更新"，写错会导致
> 永远忽略新版本或拒绝安装）。三处（加 APK 实测值）都由 `verify_release.sh` 强制比对。

> **同一版本号绝不重新构建发布**：p4a 用 `appv-<versionName>` 当"要不要重新解包
> Python 代码"的标记（底座里的 `TiebaSignAppVersionUnpack` 补丁），版本号没变就不会
> 重新解包 —— 装机后会继续跑旧代码。这也正是 `verify_release.sh` 拒绝覆盖
> `release/` 里同名 APK 的原因之一。

> `verify_release.sh` **绝不覆盖** `release/` 里已发布的 APK：若同名文件已存在且字节不同，
> 它会拒绝并报 `VERIFY_RELEASE_FAILED`（版本号已发布过就应当升版本，而不是重发同一个版本号）。

> `verify_release.sh` **绝不覆盖** `release/` 里已发布的 APK：若同名文件已存在且字节不同，
> 它会拒绝并报 `VERIFY_RELEASE_FAILED`（版本号已发布过就应当升版本，而不是重发同一个版本号）。

## 免责声明
- 本应用**非百度官方产品**，仅供个人学习与账号签到使用。
- 使用请遵守百度贴吧相关服务条款，任何账号风险（含风控、限制登录）由使用者自行承担。
- 本项目不保存签到历史，不提供跨设备恢复；卸载应用或清除数据前请自行备份账号信息。

## 许可证
- 本项目自身代码：目前未随仓库附带 LICENSE 文件；如打算公开分发，建议在创建仓库时选择 **Apache-2.0**（GitHub 会自动生成 LICENSE）。
- 依赖：aiotieba / aiohttp / lxml / protobuf / cryptography 等，按各自许可证。
- 登录形态参考 TiebaLite（GPL-3.0）：仅参考公开网址与技术思路，未复制其代码，详见 `CREDITS.md`。
