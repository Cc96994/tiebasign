# 上传 GitHub / Gitee 准备清单

仓库：

| 平台 | 地址 | 现状 |
|---|---|---|
| GitHub | https://github.com/Cc96994/tiebasign | 有 v0.3.0 Release；**源码是 2026-08-25 的网页上传快照** |
| Gitee | https://gitee.com/Cc96994/tiebasign | 有仓库；**还没有任何发行版** |

APK 走 Releases 发布，**不要 push 进 git**。

---

## 0. 先说清楚本地与仓库的关系

`G:\学习\aiotieba\tieba_sign_android` 里**没有 `.git`** —— 本地不是仓库的工作副本，
两边没有关联。所以现在的源码更新只能靠"重新整理上传"。

> `github_src/` 就是这件事的暂存目录：它是 **v0.3.0 时期手工整理**出来的副本，
> 到 0.4.0 已经严重过期（`app/` 里没有 `waterpost.py` / `updater.py`，
> `scripts/` 还是那 7 个带失效 `C:\Users\s7909\Desktop\...` 路径的旧脚本）。
> **"手工整理"正是它过期的原因**，所以它现在由脚本生成。

## 1. 上传集：由脚本生成，不要手工挑文件

```bash
bash scripts/gen_github_src.sh            # 重新生成 github_src/
bash scripts/gen_github_src.sh --check    # 只报告会传什么，不动目录
```

当前集合：**50 个文件 / 约 2.94 MB**。它包含构建真正需要的东西 —— 其中三样以前
要么缺失、要么只存在于某台机器上：

- `java_src/`（仓库自己拥有的 Java：`ApkProvider` / `KeystoreHelper` /
  `TiebaSignKeepAliveService` + `PythonActivity.updater.inc`）
- `third_party/p4a-dev.tar.gz`（pristine python-for-android，MIT）+ 手工改动快照
  `third_party/p4a-webview-base-overlay.tar.gz`（见 `third_party/README.md`）
- `vendor/*.tar.gz`（`aiotieba 4.7.1` 锁死的 sdist）

它同时是一道闸门：暂存目录里出现密钥、登录态数据库、日志或名字带 `BDUSS` 的文件，
脚本直接判失败。

## 2. ⚠️ 不要用网页拖拽上传

v0.3.0 那次就是网页上传的，后果可验证：**`.gitignore` 没有被传上去**（浏览器上传会
丢掉以点开头的文件），提交者是 `GitHub <noreply@github.com>`（web-flow）。

这不只是"少一个文件"：

- 网页上传**绕过** `.gitignore`。现在 `sign/` 里躺着一把真实的发布私钥，
  哪天拖整个目录上去就直接公开了。
- `app/tiebasign.db`（登录态相关的数据库，12 KB）就在源码目录里，一次误拖就出去了。

**用 git 命令推送**，或者至少：逐个目录确认 + 单独把 `.gitignore` 传上去。

## 3. 绝对不能提交的东西（`gen_github_src.sh` 已强制拦截）

- `sign/` 整个目录：`tiebasign-release.keystore`、`signing.env`（签名密钥 + 口令）
- `app/tiebasign.db`、`app/tiebasign_accounts.json`（本机登录态）
- 任何 `BDUSS` 值或文件名带 BDUSS 的文件
- `*.log`、`*.pyc`、`__pycache__/`
- `_legacy/`（约 337MB 历史实验目录）、`bin_*`、`release/`、`_ui_shots/`

## 4. 上传前必跑

```bash
bash scripts/run_tests.sh        # 离线自检：5 个套件
bash scripts/leak_scan.sh        # 要求 RESULT: CLEAN
```

`leak_scan.sh` 会覆盖项目根、兄弟 `_legacy/`、WSL 树、dists、python-installs 和 p4a
源码，并统计**实际扫了多少文件** —— 扫到 0 个文件会**报错而不是报干净**。
它额外用文件名规则抓"裸值的凭据文件"（`BDUSS.txt` 里可能只有 base64，值正则抓不到）。

> 2026-10-16：`_legacy/tieba_sign_app_proto/BDUSS.txt` 已移出到
> `G:\学习\aiotieba\_quarantine\`（**那是还活着的登录凭据的明文副本**，
> 掩码与 App 当前账号一致）。处置建议见那里的 `README.md`。

## 5. 许可证：Apache-2.0（已确定）

仓库根已有 `LICENSE`（Apache-2.0 全文，版权行 `Copyright 2026 Cc96994`），
并已写入 `scripts/gen_github_src.sh` 的采集清单与必需项断言 —— 重新生成上传集会自带。

- **两个平台都要有**：App 是双源更新，使用者可能从 GitHub 或 Gitee 任一平台获取，
  授权必须跟着每一个分发副本走。**不要只在建仓时勾选 GitHub 的 "Add a license"** ——
  那样只有 GitHub 有 LICENSE，从 Gitee 下载的人处在"默认保留所有权利"状态，
  同一个项目在两个平台的法律授权会不一致。
- **待补（排入 0.5.0）**：第三方依赖的许可证文本与署名。`chardet`（LGPL-2.1）与
  `certifi`（MPL-2.0）属弱 copyleft，要求分发时附带其许可证文本；当前 APK 内不含任何
  许可证文件。做法是增加 `THIRD-PARTY-NOTICES.md`。

## 6. 发布正式包（Release，不是 push）

仓库 → Releases → Draft a new release，**tag `V0.4.0`**，三个资产**一起挂**：

| 资产 | 为什么必须有 |
|---|---|
| `release/tieba-sign-assistant-0.4.0-arm64-v8a-release.apk` | 主体 |
| `release/update.json` | **别漏**：App 的版本比较用数字 `versionCode`，而 GitHub/Gitee 的发行版信息里只有 tag、没有 versionCode。`update.json` 提供实测的 `versionCode` + `sha256` |
| `release/SHA256SUMS` | 用户核对下载 |

**GitHub 与 Gitee 都要挂同一套**（App 默认 GitHub 优先、Gitee 备用；Gitee 现在还是空的，
备胎等于没有）。三个资产**同一次发布一起上传**：只发 APK 而漏 `update.json`，
App 就只能退回按 tag 比较，容易把更旧的包当成更新。

Release 说明可用 `release/RELEASE_NOTES_0.4.0.md`。
（`release_upload_0.4.0/` 是这四个文件同字节的一份副本，专门给"手动上传"用，见第 8 节。）

## 7. 下次更新版本时，仓库里到底要改哪些文件

**要。** 仓库是源码的唯一来源，改了业务代码就必须让仓库跟上；但 **APK 不要进 git**，
它只作为 Release 资产。版本号相关的改动点（`verify_release.sh` 会逐条校验）：

| # | 文件 | 改什么 |
|---|---|---|
| 1 | `buildozer_webview_app.spec` | `version =`（**权威来源**，`app_identity.sh` 从这里读） |
| 2 | `app/main.py` | 渲染给用户看的 `APP_VERSION` |
| 3 | `app/updater.py` | `_APP_VERSION` + `_APP_VERSION_CODE`（更新逻辑自己那份） |
| 4 | `scripts/app_identity.sh` | 把**上一版**的三个不可变事实更新成刚发布的这版（`APP_PREV_*`） |

然后：构建 → `verify_release.sh` → **重新生成 `update.json`**（`gen_update_json.sh` 已由
`verify_release.sh` 自动调用）→ 打**新 tag** 发布 → 重新生成 `github_src/` 再推源码。

三条硬规矩：

1. **同一个版本号绝不重发。** `verify_release.sh` 拒绝覆盖 `release/` 里同名但字节不同的
   APK。这不只是洁癖：p4a 的解包标记是 `appv-<versionName>`，**同版本号重编不会重新解包**，
   装上去的 App 会继续跑旧的缓存代码（实测踩过一次）。要改就升版本号。
2. **`versionCode` 只能增大**，否则 `INSTALL_FAILED_VERSION_DOWNGRADE`。
3. **同一把 keystore 永不能丢**，否则老用户只能卸载重装（= 丢掉加密保存的登录数据）。

## 8. 怎么把新源码更新到仓库（v0.4.0 起）

上传集由 `scripts/gen_github_src.sh` 从当前树生成到 `github_src/`（本次 **50 个文件**，
约 2.96 MB；准确字节数看脚本每次的输出 —— 本文件自己也在集里，写死数字必然自相矛盾）。
`repo_upload_0.4.0/` 是它的一份逐字节副本，**该目录里的全部内容就是
仓库根**（把里面的东西铺到仓库，不要把目录本身传上去）。

### 路线 A（推荐）：git 命令行

```bash
git clone https://github.com/Cc96994/tiebasign.git tiebasign-repo
cd tiebasign-repo
# 清空工作树但保留 .git，再把上传集铺进去 —— 这样 git add -A 会如实记录"删除"
find . -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
cp -a /path/to/repo_upload_0.4.0/. .
git status          # 先看清楚会删什么、改什么，再提交
git add -A
git commit -m "v0.4.0：更新源码（新增 updater/waterpost、java_src、third_party、vendor）"
git push
```

为什么推荐：`.gitignore` 不会被丢，有 diff 可核对，有历史可回退。
`V0.3.0` 这个 tag 指向旧提交 `0a704155`，**不受这次推送影响** —— 旧发行版和它的 APK
依然在、依然可下载。Gitee 同理，远端换成 `https://gitee.com/Cc96994/tiebasign.git`
（推送需要私人令牌）。

### 路线 B：网页上传

能用，但有两个已知代价：

1. **`.gitignore` 会被静默丢掉**（浏览器上传丢弃以点开头的文件）。传完必须手工补：
   仓库 → Add file → Create new file → 文件名 `.gitignore` → 粘贴本地那份内容。
2. 网页上传**没有删除操作**。下面这 9 个 v0.3.0 时期的一次性补丁脚本已经不在本地树里
   （归档在 `_legacy/scripts/`），但还留在仓库里，需要逐个打开并删除：

```
scripts/fix_webview_clear_login_api.py     scripts/patch_webview_clear_login.py
scripts/patch_manifest_dist.py             scripts/patch_webview_keepalive.py
scripts/patch_perm_ui_thread.py            scripts/patch_webview_login_compat.py
scripts/patch_webview_native_login.py      scripts/pause_autolog_copy.py
scripts/verify_030.sh
```

已核查：现役代码里**没有任何地方引用它们**，删掉是安全的。

### 两条路线都要做的收尾核对

- 仓库里 `.gitignore` 在、`sign/` **不在**、`app/tiebasign.db` / `app/tiebasign_accounts.json` **不在**。
- APK **不要**进 git —— 它是 Release 资产，仓库里只放源码。
