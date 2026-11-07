# AGENTS.md

> 面向：在本仓库里干活的自动化 agent（superagent worker、编码助手、任何接了 issue 就动手的东西）。
> 先读完这一页再改文件。下面每一条都是踩过的坑，不是风格偏好。

---

## 1. 这是什么项目

Android App「贴吧签到助手」，包名 **`org.dsh.tiebaweb`**，已双源发布（GitHub + Gitee）。
技术栈不是 Kotlin，也不是 Kivy：

```
Python 3.12.7 + aiotieba 4.7.1
  ↓ python-for-android webview bootstrap（无 SDL/Kivy）
本地 HTTP 服务 127.0.0.1:5000  ← WebView 渲染 MD3 前端
  ↓ pyjnius 桥接
Android 原生：Keystore / CookieManager / 前台服务 / 通知 / 安装入口
```

- 线上正式版 **0.5.3**；**0.5.6 源码已完成，待构建与发布**（吧群消息签到已实现，机器人识别待真机确认）。
- 首期只做 Android + arm64-v8a，minSdk 23 / targetSdk 34。iOS、armeabi-v7a、跨设备迁移是 PRD 明确不需要的。

## 2. 目录地图

| 目录 | 是什么 | 能不能动 |
|---|---|---|
| `app/` | 正式版 Python 源码（main / signer / autosign / waterpost / groupsign / updater / weblogin / keystore_crypto / storage / tieba_api / tieba_codes / redact / liker / diag） | 主要工作面 |
| `java_src/org/dsh/tiebaweb/` | 自家的 Java：KeystoreHelper / TiebaSignKeepAliveService / ApkProvider | **是构建输入**，见 §4.11 |
| `p4a_local/` | 定制配方：aiotieba（NDK 编 crypto + certifi 补丁）、protobuf（纯 Python）、zlib | 谨慎 |
| `scripts/` | 构建 / 核验 / 扫描脚本，全部自定位 | 谨慎 |
| `vendor/` | `aiotieba-4.7.1.tar.gz` 锁死的 sdist | **禁止升级** |
| `third_party/` | p4a 底座源码包 + 手工改动快照 | 谨慎 |
| 根目录 `*.md` | README / ROADMAP / GITHUB_PREP / SIGNING / CREDITS / PRD_ACCEPTANCE_* | 可改，注意 §3 |

**仓库里没有**这些（本地才有，也永远不该进来）：`bin_m22/`、`_legacy/`、`release/`、
`sign/`、`_ui_shots/`、`github_src/`、`repo_upload_*/`、`release_upload_*/`、`*.log`。

## 3. 动手前先读这四份

1. `ROADMAP.md` —— **重点读"用户确认过的行为（别再按旧设计改回去）"那几张表**。它们是
   用户在真机上逐个确认过的决定，不是建议。想改其中任何一条，先停下来问。
2. `docs/DECISIONS.md` —— 每个"为什么这么定"的来龙去脉，以及被放弃的方案。
3. `docs/HISTORY.md` —— 走到今天踩过哪些坑，避免重踩。
4. `README.md` —— 构建命令、产物路径。

## 4. 硬禁区（违反即 PR 作废）

1. **绝不提交 `sign/`** 整个目录、任何 `*.keystore` / `*.jks` / `signing.env`。
   里面是真实的发布私钥，泄露等于任何人都能签出"像官方发的"更新包。
2. **绝不提交 APK**、`bin_*`、`release/`、`_legacy/`、`*.log`、`app/tiebasign.db*`、
   `app/tiebasign_accounts.json*`。APK 是 Release 资产，不进 git。
3. **绝不出现任何真实 BDUSS 值**，也不创建名字带 `BDUSS` 的文件。打包前必须
   `bash scripts/leak_scan.sh` 得到 `RESULT: CLEAN`。
4. **不改 `applicationId`**（`org.dsh.tiebaweb`）。改了就是另一个 App，老用户装不上更新。
5. **不生成新密钥**。`scripts/gen_release_keys.sh` 只适用于"从未发布过"的 App；对本项目
   运行它 = 所有老用户必须卸载重装，且本机加密保存的登录数据永久读不出来。
6. **不升级 aiotieba**。4.8.1 已删除 `api/add_post/`，升上去"自动水贴/一键水贴"整体失效。
   4.7.1 是钉死的，和包名、签名同级。
7. **不用同一个版本号重新构建发布**。p4a 用 `appv-<versionName>` 判断要不要重新解包
   Python 代码 —— 版本号没变就不会重新解包，装上去的 App 继续跑旧代码。要改就升版本号。
8. **不降低 `versionCode`**。系统会以 `INSTALL_FAILED_VERSION_DOWNGRADE` 拒绝安装。
9. **不直接 push / merge 到 `main`**。一律开 PR，由人合并。也不要 force-push。
10. **不改签名配置、不改权限声明**，除非工单明确要求。
11. **不要"顺手"把 `java_src/` 加进 `.gitignore`**。它看着像实验目录，其实是构建输入；
    它被忽略过一次，结果是别人克隆下来构建出的 APK 静默缺少原生安装能力。

## 5. 版本号有四处拷贝，必须同时改

| # | 文件 | 改什么 |
|---|---|---|
| 1 | `buildozer_webview_app.spec` | `version =`（**权威来源**） |
| 2 | `app/main.py` | 界面显示的 `APP_VERSION` |
| 3 | `app/updater.py` | `_APP_VERSION` + `_APP_VERSION_CODE` |
| 4 | `scripts/app_identity.sh` | 把**上一版**的不可变事实更新成刚发布的这版 |

漏一处，`verify_release.sh` 会判失败；更糟的是 `_APP_VERSION_CODE` 写错会导致
"永远忽略新版本"或"拒绝安装"。

## 6. 构建（**默认不要跑**）

环境是 **Windows + WSL2 Ubuntu**，构建必须在 WSL 里做，Windows 侧跑不了 buildozer。

```bash
cd /mnt/g/学习/aiotieba/tieba_sign_android
bash scripts/run_tests.sh          # 离线自检，几秒钟，改了 Python 就先跑这个
bash scripts/wsl_app_build.sh      # debug，迭代用
bash scripts/wsl_release_build.sh  # release，需要 sign/ 就绪
bash scripts/verify_release.sh     # 核验
```

- **release 构建十几分钟起**，你的单次执行预算可能不够。除非工单明确要求出包，
  **不要主动构建 APK**。
- 提交前的最低门槛是 `bash scripts/run_tests.sh` 通过，不是"能构建出 APK"。
- 构建脚本会先跑 `scripts/patch_base_java.py`，它同时刷新 p4a 缓存里的 dist 副本。
  不要绕过它直接改底座。

## 7. 改代码的规矩

- **一个 PR 只做一件事**。修 bug 的 PR 里不要夹带重构、格式化、依赖升级。
- 不要动与工单无关的文件；不要用 `git add -A` 一次性扫进所有改动。
- 注释与文档用**中文**，与本项目现有风格一致。
- Python 侧改动后必须 `bash scripts/run_tests.sh` 通过。
- 涉及签名、更新逻辑、Keystore、权限、构建配置的改动，**先在 PR 描述里写清楚影响面**。
- 提交信息：`<模块>: <做了什么>`，例如 `waterpost: 修复手动水贴重复计次`。
  正文用中文说"为什么"。

## 8. 不确定的时候

**不要猜，也不要"改到能编译为止"。**

编译/测试失败时，正确做法是：报告失败事实 + 原始报错 + 你判断的原因，然后停手等人。

禁止为了"让它跑起来"而做这些事：

- 改 `buildozer_webview_app.spec` 里的依赖版本或 requirements
- 降低/替换依赖版本（尤其 aiotieba、protobuf、cryptography）
- 删掉构建不过的模块或测试
- 改 SDK/NDK 路径、装工具链、改系统环境
- 把断言注释掉

这类"绕过"在别的项目里可能只是脏，在这个项目里会直接把已发布 App 的升级路径弄坏。

## 9. 你在这个环境里拿不到的东西

- **没有 Android 设备或模拟器** → 任何"真机验证"都做不了，不要声称验证过。
- **通常没有 `sign/`** → 构建不出可发布的 release 包，只能跑自检。
- **没有历史产物** `bin_m22/` → 不要去找旧 APK 比对。

所以你的产出标准是：**代码正确 + 自检通过 + 说明清楚**，不是"我装到手机上试过了"。
