# ROADMAP —— 下一版（v0.4.0）要做什么

最后更新：2026-10-15

## 已定下的路线（不要再动摇）

在同一款 App 上继续更新，**不换代码底座**。即：继续用本项目 `tieba_sign_android`
（已发布的 v0.3.0），把早先从它提炼出的空壳项目 `tieba_sign_app` 留作**通用模板**，
它不是前进路线。

理由：本项目业务代码完整（`app/main.py` 58 KB + signer / autosign / weblogin /
tieba_api + `p4a_local/aiotieba` 定制配方 + `java_src` 的 KeystoreHelper）。换底座
等于把这约 118 KB 业务代码全部移植过去，还要冒"老用户 Keystore 密文解不开、
必须重新登录"的风险。收益（目录更整齐）与代价不成比例。

## 不可变的三个事实（碰了就等于换了一个 App）

已安装用户必须能**原地升级**，否则他们得先卸载，而卸载会清掉本机加密保存的登录数据。

| 事实 | 值 | 破了会怎样 |
|---|---|---|
| `applicationId` | `org.dsh.tiebaweb` | 变成另一个 App，与旧版并存 |
| 签名密钥 | `sign/tiebasign-release.keystore`（`CN=TiebaSign`，SHA-256 `84d7ed80…6e0a7b`） | `INSTALL_FAILED_UPDATE_INCOMPATIBLE` |
| `versionCode` 只能增大 | v0.3.0 = 1023300 | `INSTALL_FAILED_VERSION_DOWNGRADE` |

这三条现在由 `scripts/check_upgrade_compat.sh` 在每次发布前强制校验，
`scripts/verify_release.sh` 会自动调用它。**同一把 keystore 永不能丢。**

### 第四条：aiotieba 依赖被钉死在 4.7.1，不要"顺手升级"

`vendor/aiotieba-4.7.1.tar.gz` 是**必须锁住**的依赖，和包名/签名一个级别。

- 我们用的 `Client.add_post(fname, tid, content)`（主楼回复）在 **4.7.1 里有**；
- 上游最新 **4.8.1 已经把 `src/aiotieba/api/add_post/` 整个删掉**（该路径 404，
  4.8.1 的 `client.py` import 清单里也没有 `add_post`）→ 升上去**功能二会整体失效**；
- PyPI 上根本没有 4.x（最高 3.3.0），所以这份 4.7.1 是从 GitHub tag 打的包。

核实方式（2026-10-15）：GitHub API 查 `lumina37/aiotieba` 的 `v4.7.1` 与 `v4.8.1`
两个 tag 下的 `api/add_post` 目录，以及 PyPI 的版本列表。

## 待做功能（按用户指定的顺序）

### 功能一：检查更新 + 自动更新 —— 已实现（真机已验证检查路径）
**位置**：设置 → 检查更新（设置页第一项；有新版本时该 tab 亮红点）。
实现于 `app/updater.py`（逻辑）+ `app/main.py`（页面/路由/红点）+ p4a 底座里的
四个 Java 静态方法（`installApk` / `inspectApk` / `isOnWifi` / `notifyUpdateReady`）
与 `ApkProvider`，由 `scripts/patch_base_java.py` 打进去。

**用户确认过的行为（2026-10-16，别再按旧设计改回去）**

| 项 | 决定 |
|---|---|
| 检查频率 | **每天自动查一次**（由 autosign 的调度循环驱动，不另起定时器；关掉自动签到也照查） |
| 没查到 | **什么都不提示**：不弹通知、不亮红点，只把结果写进设置页 |
| 查到了 | 才亮红点 + 弹一条系统通知（同一个版本只弹一次）；通知点进去直达安装界面 |
| 提醒方式 | 系统通知 + 设置页红点；**不做静默安装**，一律唤醒系统安装界面 |
| 下载来源 | 默认 `auto` = **GitHub 优先**，开始下载后 8 秒内不到 800KB（<100KB/s）就断开改从 Gitee 下（GitHub 只在**真的下载**时才 +1 计数，检查源不影响它） |
| 只在 Wi-Fi 自动下载 | 默认开；手动点「下载更新」不受限制 |
| Gitee 没有发行版 | 「源返回空」被当作**不是错误**（实测 Gitee 现在就是 `[]`），否则首次运行就误报 |

**发布时必须上传三个资产**（用户手工上传）：
`tieba-sign-assistant-<版本>-arm64-v8a-release.apk`、`update.json`、`SHA256SUMS`。
`update.json` 由 `scripts/gen_update_json.sh` 从 APK **实测**生成（versionCode +
sha256 + apk 名 + 说明），`scripts/verify_release.sh` 在发布模式下自动调用它。
**没有 update.json 也能更新**（退回按 tag 数字比较 + assets 的 digest 当校验值），
但那样 tag 打错就会让用户去装更旧的包，所以正常流程永远带上它。

**版本号现在有四份拷贝，发布前由 `verify_release.sh` 全部强制对齐**：
`buildozer_webview_app.spec` 的 `version`、`app/main.py` 里用户看到的那串、
`app/updater.py` 的 `_APP_VERSION` / `_APP_VERSION_CODE` / `_PACKAGE`、
以及 APK 实测值。

### 功能一的技术前提（已全部落地，勿删）
- `android.permissions` 必须含 `REQUEST_INSTALL_PACKAGES`（打开安装界面）与
  `ACCESS_NETWORK_STATE`（判断是否 Wi-Fi）；
- Android 7+ 不能用 `file://` 把 APK 交给系统安装器，必须 `content://` —— 本 dist
  **没有 androidx**（用不了现成 FileProvider），所以自带一个 70 行的
  `java_src/org/dsh/tiebaweb/ApkProvider.java`（`exported=false`，且只放行
  `getFilesDir()` 下以 `.apk` 结尾的文件）；
- **版本比较只用 versionCode 数字**，绝不比 "0.3.0"/"0.4.0" 字符串；
- 安装前必须验**包名 + versionCode + 签名指纹**（`verify_release.sh` 断言这三项
  的能力真的进了 APK 的 dex/manifest）。sha256 来自发布端，挡不住被篡改的发布端；
  签名指纹才是真边界。

### 功能二：自动水贴 + 手动一键水贴 —— 已实现（已真机验证）
**位置**：设置 → 自动水贴（配置）；签到页 → 一键水贴（手动补满当天次数）。
实现于 `app/waterpost.py` + `app/main.py`（设置页 / 签到页 / 路由）+ `autosign.py` 保活。

**用户确认过的行为（2026-10-15 第二轮，别再按旧设计改回去）**

| 项 | 决定 |
|---|---|
| 账号与目标帖 | **每个账号带自己的一组目标帖**：一个账号只发自己那组帖，不共用列表 |
| 每个帖每天发几次 | 默认 **4**，可调 1–50 |
| 两条回复的间隔 | 默认 **5 秒**，可调 **5–3600 秒**，**不随机** |
| 文案 | 用户自定义，内置 10 套默认（顺序轮换 / 随机可切），所有账号共用一份 |
| 错过的次数 | **默认不补发**；用户可自行打开「补发」（每轮最多补 10 条，防一次爆量） |
| 发送方式 | **只做主楼回复**（楼中楼选 A：留空位，见下表） |
| 手动一键水贴 | 对勾选的每个账号，把它**当天还没发满的次数**一次发完（设置 4 次/天 → 每帖补到 4 条），用设置里的间隔；**发满后当天自动计划也不再发**（手动与自动共用同一份"今天发了几条"的账，一天只有一份额度）；不发通知/红点；撞硬失败会**同时停掉该账号当天的自动计划** |
| 手动失败算不算次数 | **不算**。自动计划每次尝试都计入 `sent`（给自己的重试封顶），手动只计成功 —— 手动是用户点出来的，一次网络抖动不该白丢当天的额度，没发满还能再点一次补上（2026-10-16 实测确认） |
| 自动计划的起点 | **每个账号各自**在"它自己当天签到完成"之后才开始（首次延迟 60 秒） |

**能力边界（已核实 `vendor/aiotieba-4.7.1.tar.gz` 源码，勿凭印象改动）**

| 发送方式 | 状态 | 依据 |
|---|---|---|
| 主楼回复（新增楼层） | **可用** | `Client.add_post(fname, tid, content)` |
| 楼中楼 | **暂无接口** | 4.7.1 只有 `get_comments` / `SubPostList` 两条**读**接口；`api/add_post/protobuf/AddPostReqIdl.proto` 的全部字段里没有 `pid`/`quote_id`/`is_comment`，应用协议 cmd=309731 造不出楼中楼。4.8.1 也一样没有，且它连 `add_post` 都删了 |

楼中楼**选 A**：这次只做主楼回复，代码里 `reply_mode` 分支留着当空位。
`reply_mode="floor"` 现在返回明确的「暂不支持」，并**当天对该账号停止发送**，绝不假装成功。
将来拿到可用接口（新版 aiotieba 或实测通过的 Web 端点）时，只需在
`waterpost._post_async` 里加一个分支，并把设置页那个 `disabled` 去掉。

**刻意这样设计的地方（改之前先想清楚为什么）**

- **默认不补发**：与自动签到的「启动补跑」刻意相反 —— App 关几小时后重开就连发，
  是最明显的机器特征。迟到超过「一个间隔 + 300 秒」的那一次直接跳过并重新计时
  （300 秒是给"一个 tick 要串行发多个帖"留的余量，不是随手写的）。
- **硬性失败按账号停**：需要验证码 / 账号失效 / 被限制风控 / 暂不支持 →
  该账号当天**所有**目标帖都停（自动与手动共用 `_stop_account_today`）。
  网络异常属临时问题，继续按计划发。同一轮里若某账号已停，它剩下的目标帖不再发。
- **手动执行期间自动调度让路**（`manual_busy()`）：次数不丢，只是推迟。
  自动与手动发送共用 `_send_lock` 串行化。
- **保活改为「签到或水贴任一开启即保活」**（`autosign` 调度循环里检查
  `waterpost.keepalive_needed()`）；手动水贴正在跑时也算需要保活。
- 每发一条都落盘（进程被杀不丢计数）；同一条文案不会连续重复。
- **发送期间不直接 `_save(旧快照)`**：单次发送最长阻塞 90 秒，用户可能在中途改设置，
  必须重新 `_load()` 再合并进度（`_tick` 与 `_manual_loop` 都这样做，有回归测试）。
- 界面不用原生 `confirm()`（这个 WebView 里它不可靠），删除改成「点两次确认」。

**数据文件**：`<ANDROID_PRIVATE>/tiebasign_waterpost.json`（不含 BDUSS）。
schema 2；老 schema 1（单账号 + 全局 targets + `interval_minutes`）会自动迁移成
"一个账号一组"，但**间隔改用新的默认 5 秒**（用户明确要求默认 5 秒，把旧的 60 分钟
带过来只会让人以为默认没改）。计划/结果的键统一是 `"<account_id>:<target_id>"`。

**离线自检**：`bash scripts/run_tests.sh`（已从项目外的 `_scratch/` 搬进
`scripts/tests/` 并接进 `verify_release.sh`），其中 waterpost 套件 **176 项**覆盖链接解析、
文案轮换、设置校验、老 store 迁移、每账号分组的计划状态机、补发开关、按账号硬停、
手动一键水贴（含补满当天次数、失败不占额度、重复点击被拒、并发让路与上限）、
目标组管理、两个页面渲染。
**它抓出过三个真 bug**：`_manual_loop` 的 `halted` 写成了 `set()` 却当字典用
（手动撞墙时剩余目标被静默丢弃、自动计划也没停）；设置页漏了一行 `lim`；
以及设置页 `.format()` 混用 `{0}` 与 `{}` 导致整页白屏。

### 功能三：iOS 适配（先放着）
用户明确说排在后面。真要做得先解决 Python 运行时在 iOS 上的打包路线，与当前
buildozer/python-for-android 技术栈不通用，属于另起一摊的工程量。

## 下次发布（0.4.0）要做的

1. ~~两处版本号~~ **已完成（2026-10-15）**：`buildozer_webview_app.spec`
   `version = 0.4.0` 与 `app/main.py` 的 `贴吧签到助手 0.4.0`；
   versionCode **1023400**（> 1023300）。
2. **补 `README.md`**（**还没做**）：功能列表加「自动水贴」，`app/` 模块列表加 `waterpost.py`。
3. 构建 + 发布核验 **已完成**，产物
   `release/tieba-sign-assistant-0.4.0-arm64-v8a-release.apk`
   （20,707,486 字节，sha256 `3c300ce4…47cdfe`，签名者 `84d7ed80…0e0a7b`）；
   `check_upgrade_compat.sh` → **`UPGRADE_COMPAT_OK`**，`verify_release.sh` →
   **`VERIFY_RELEASE_OK`**（411 项自检）。上传三件套另放在 `release_upload_0.4.0/`
   （含 `上传说明.txt`），等手工上传。
   ⚠️ 2026-10-16 为修「一键水贴只发一条」**用同一个版本号重编过一次**（当时已查
   GitHub/Gitee API 确认 0.4.0 两边都没发布，所以没有任何用户拿到过旧字节）。
   **这条只能做一次**：p4a 的解包标记是 `appv-<versionName>`
   （`files/app/private.version`），同版本重装**不会重新解包**，会继续跑旧缓存代码 ——
   真机验证前必须删掉 `private.version` 与 `libpybundle.version`。已发布过的版本号
   绝不能再重发。
   另有**一处内部注释**仍写着旧的「各发一轮」语义：`_JS_SIGN` 里那条 JS 注释
   （它是字符串字面量，会进 `main.pyc`）。它是在 0.4.0 构建并核验**之后**才发现的，
   所以**没有**为此重编 —— 那只会让「已核验的产物」和源码对不上。下次构建时顺手改掉。
   （`_api_waterpost_manual` 的 docstring 同类，但它**不进包**：p4a 编译时会剥掉
   docstring —— 实测 `main.pyc` 里搜不到任何 docstring 文本 —— 所以那处已经改好了。）
4. **真机验证清单**（功能二）—— 2026-10-16 已在模拟器上过了一遍：
   - 每个账号能各自粘贴链接并解析出吧名与标题（需要帐号有效）；
   - 打开开关后，**各自签到完成**约 60 秒是否发出第一条，且只发自己那组帖；
   - 次数、间隔（秒）、文案轮换、补发开关是否按设置生效；
   - 签到页「一键水贴」：**点一次发的条数 = 当天还没发满的次数**（实测
     `daily_count=10` → 队列 `total=10`，修复前恒为 1）、进度与逐条结果显示是否正常、
     明细是否标出「第 n/N 条」、账号没配目标帖时是否明确提示「跳过」；
   - 「立即发一条（测试）」是否真的发出去，失败时分类是否看得懂；
   - 手动撞风控后，设置页该账号的自动计划是否显示「今天已停止发送」；
   - `waterpost.log` 里是否只有预期记录。
5. 给别人测试必须用 **release 签名**的包：debug 包签名不同，装之前得先卸载，
   而卸载会清掉本机加密保存的账号。

## 构建产物的位置（今天踩过的坑，务必记住）

构建脚本把 APK 写到 **WSL 侧**的 `~/tieba_sign_android/bin_m22/`，
**不是** Windows 项目的 `bin_m22/`。而 Windows 的 `bin_m22/` 里还留着 8/25 的
**同名**旧文件（例如 `tiebaweb-0.3.0-arm64-v8a-debug.apk`）。

本次就因此误判过一次：读了 Windows 那份 8/25 的旧包，看到 `assets/private.tar`
里没有 `waterpost.pyc`，一度以为"新模块没被打进 APK"。**取产物一律以 WSL 路径为准，
并先核对文件大小与时间。**

已验证（2026-10-15，debug 构建）：
- WSL 产物的 `assets/private.tar` 含 `waterpost.pyc`(36,208) 与新 `main.pyc`(84,019，
  内含「自动水贴」)，共 12 个成员；
- `source.include_exts = py,png` 生效：`_legacy/`、`tiebasign.db`、
  `tiebasign_accounts.json` **都没有**进包。

### 构建失败时的排查：`AAPT2 Daemon startup failed`

2026-10-15 第二轮开发时遇到过一次，Gradle 报
`AAPT2 aapt2-8.11.0-... Daemon #0: Daemon startup failed`。当时的环境是：
WSL 内存充裕（可用 5.9 GB）、`/tmp` 权限正常、无残留 aapt2 进程，
但有一个**从更早的构建一直活着的 Gradle daemon**。

处理：先杀掉旧 Gradle daemon 再重跑，第二次成功（增量构建 21 秒）。
**机制未确认**：那次同时还有无头浏览器截图在跑，所以"旧 daemon 状态坏掉"与
"瞬时资源争抢"两种解释都说得通，别把结论说死。

两个附带教训：
- **`pkill -f GradleDaemon` 会杀掉自己** —— 调用它的那条 shell 命令行里就含这个
  字符串，`pkill -f` 会匹配到自己，导致整条命令静默死掉（连日志文件都没生成）。
  用括号技巧：`pkill -f 'bootstrap.Gradle[D]aemon'`。
- 构建产物以包内 pyc 的**实际字符串**为准，不要只信文件时间戳：这次 APK 的 mtime
  一度让我以为它早于最后一处修改，最后是靠 pyc 里 `flex:none;padding:0 18px`
  与 `max_tick_sends` 两个独有字符串确认它就是最终代码。

## 模拟器能跑 arm64 包（2026-10-15 实测，别再凭架构下结论）

本机 AVD `Pixel_9_API_37`（SDK 在 `G:\Android\Sdk`，镜像
`system-images\android-37.1\google_apis_ps16k\x86_64`）**自身是 x86_64**，
但 `ro.product.cpu.abilist = x86_64,arm64-v8a` —— 这个镜像**自带 ARM 翻译层**。
所以只含 `arm64-v8a` 的包能直接装、能跑（实测：装好 → 启动 → Python 起 HTTP 服务 →
页面正常）。**不要**再用"host 是 x86_64 所以 arm64 包装不上"来预判，先看 `abilist`。

- `adb`：`G:\Android\Sdk\platform-tools\adb.exe`；有窗口启动：
  `G:\Android\Sdk\emulator\emulator.exe -avd Pixel_9_API_37 -no-audio -no-boot-anim`
  （无窗口跑自动化再加 `-no-window -no-snapshot-save -gpu swiftshader_indirect`）
- 翻译层下 Python 启动慢，**给 30–60 秒**再判断失败。
- 应用自己的 HTTP UI 跑在 guest 的 `127.0.0.1:5000`，用
  `adb forward tcp:<host> tcp:5000` 就能从 PC 直接抓页面验证（比截图可靠）。

**"装不上"的真实原因常常不是架构**：debug 包在模拟器上失败，报的是
`INSTALL_FAILED_UPDATE_INCOMPATIBLE`（模拟器里已经装着 release 版 0.3.0），
**不是** `INSTALL_FAILED_NO_MATCHING_ABIS`。要在模拟器上跑新代码，只能出
**release 包**（同一把 keystore）做原地升级。

**已实测（2026-10-15，0.4.0 覆盖 0.3.0）**：`adb install -r` → `Success`，
`firstInstallTime` 保持 2026-08-25 不变、`tiebasign_accounts.json` 的 md5 前后一致
（`7f0f1023…`）、`/api/accounts` 仍返回原账号（"Qq"，掩码 `c4SD****JnUH`）
→ **登录态与 Keystore 密钥都没丢**。依据：`app/main.py` 关于页写明 BDUSS 用
**Android Keystore AES-GCM** 加密，卸载会销毁 Keystore 密钥 → 覆盖安装是保住
登录态的**唯一**路径（备份 JSON 也没用，密钥不在文件里）。

## 发布链现状（2026-10-15 加固完成）

- `scripts/app_identity.sh` —— 标识与版本**单一来源**，从 `buildozer_webview_app.spec` 读取
- `scripts/check_upgrade_compat.sh` —— 升级兼容门禁
- `scripts/verify_release.sh` —— 发布核验；**绝不覆盖已发布的 APK**，版本号不再写死
- `scripts/leak_scan.sh` —— 泄漏扫描；扫描根不存在会**报错**而不是假装 CLEAN
- `scripts/wsl_app_build.sh`（debug）/ `wsl_release_build.sh`（release）—— 均自定位

### 下次改版本号时注意：版本号写在**三**个地方（外加一处"上一版事实"）

1. `buildozer_webview_app.spec` 的 `version =`（唯一权威，`app_identity.sh` 从这里读）
2. `app/main.py` 里渲染给用户看的那行 `贴吧签到助手 <版本>`
3. `app/updater.py` 的 `_APP_VERSION` + `_APP_VERSION_CODE`（更新逻辑自己那份副本）
4. `scripts/app_identity.sh` 的 `APP_PREV_*`（**上一版**的不可变事实；发布完要更新成刚发的这版）

第 2、3 处没有任何自动生成机制。`verify_release.sh` 会逐条比对上面四处与 APK，
不一致就**报错指出具体位置**，但不会自动改（业务代码不自动改）。

### 同一个版本号绝不重发（2026-10-16 实测踩过）

p4a 的解包标记是 `appv-<versionName>`：**版本号变了才重新解包**，
同一个版本号重编 APK 再装上去，App 会**继续跑旧的缓存代码**。
当时的现象是"装好了但 `/settings/update` 路由 404，而包里的 `main.pyc` 明明有它"。
`verify_release.sh` 拒绝覆盖 `release/` 里同名但字节不同的 APK，也是在防这件事。
要改内容就升版本号。

## 旧账清理（2026-10-16）

2026-10-15 记下的三条旧账，加上清理过程中发现的第四条，全部处理完，并且每一条都
**留下了会自己报警的检查**（不是"改完就算"）：

| # | 旧账 | 处理 | 怎么验证 |
|---|---|---|---|
| 1 | `github_src/` 是 8/25 手工快照，到 0.4.0 已过期 | 新增 `scripts/gen_github_src.sh`：清单写死、每次发布前重新生成；同时是安全闸门（密钥 / 登录态库 / 日志 / 名字带 BDUSS 一律判失败） | `bash scripts/gen_github_src.sh --check` → 50 个文件 / 2.94MB |
| 2 | p4a 源码包只剩兄弟项目那一份 | `third_party/p4a-dev.tar.gz` 入库（pristine，MIT，sha256 `8338b607…`）+ 手工改动快照 `p4a-webview-base-overlay.tar.gz`（sha256 `97344f44…`）；`wsl_setup.sh` 自定位，不再指向失效的 Windows Temp 路径 | `bash scripts/check_p4a_base.sh` → 净室重建**逐字节等于**当前底座 |
| 3 | `.gitignore` 忽略 `/buildozer.spec`，克隆后构建第一条 `cp -a` 就死 | 两个构建脚本能处理"文件不存在"（没有就不保留、退出时删掉），并用 `trap … EXIT` 保证**构建失败也会还原** | `bash scripts/tests/test_build_swap.sh` → 12 项；并用**旧写法负向实测**过两种失败 |
| 4 | （清理时发现，且是我自己造成的）`.gitignore` 把 `/java_src/` 当实验目录忽略，而它是**构建输入** | 移出忽略列表并写明原因；`gen_github_src.sh` 把它列为必需项 | `check_p4a_base.sh` 断言 `org/dsh/*.java` 逐个落进底座 |

第 4 条值得单独说：`patch_base_java.py` 靠 `java_src/` 才能把安装入口 / `ApkProvider` 打进
底座。忽略它 = 别人克隆下来构建出的 APK **静默缺少原生安装能力**（构建照样成功）。
现在 `check_p4a_base.sh` 还会断言 **App 真正反射调用的每个 Java 方法都有定义**，
方法名清单是从 `app/*.py` 现抓的（当前 13 个），不是手抄的 —— 新增一个反射调用却忘了
在底座里实现，会在自检里失败，而不是等用户点"安装"才失败。

### 底座到底怎么重建（三者缺一不可）

```
third_party/p4a-dev.tar.gz                        pristine python-for-android 2026.05.09 (MIT)
  + third_party/p4a-webview-base-overlay.tar.gz   手工改动快照（原生登录 / 保活 / <service>）
  + java_src/（经 scripts/patch_base_java.py）      更新安装入口 / ApkProvider / <provider>
  = 构建用的 p4a 底座
```

**为什么手工那部分必须是快照**：那些改动不是独立方法块 —— `startNativeLogin` 的钩子插在
WebViewClient 的 `shouldOverrideUrlLoading` / `onPageFinished` 里（同一批标记在文件里出现
5 次），另有清登录态、保活、权限请求、失败通知、日志开关。它们没法用"在锚点前插一段"
重建，只能快照。overlay 与 p4a 版本**强绑定**：`wsl_setup.sh` 会比对
`P4A_OVERLAY_MANIFEST.txt` 里的 `p4a_version`，不一致就拒绝铺上去（防止静默降级）。
将来升级 p4a，必须先对着一份"已打好全部补丁的新底座"重新 `pack_p4a_overlay.sh`。

### 已知事项（记录，不是已知良好）

底座的 manifest 把 p4a 的模板钩子 `{{ args.extra_manifest_application_xml }}` 换成了写死的
`<service>`，这随快照一起被保存下来。本项目不用这个机制，所以没有功能影响，
但确实是**上游钩子被移除**，将来若要用 p4a 的 manifest 扩展点会踩到。

### 凭据：`_legacy/tieba_sign_app_proto/BDUSS.txt` 已移出

2026-10-16 移到 `G:\学习\aiotieba\_quarantine\`（连同处置说明）。**它不是过期文件**：
192 字符裸 base64，掩码 `c4SD****JnUH` 与 App 当前账号（`/api/accounts` 返回的掩码）
**完全一致** → 那是一份**还活着的登录凭据的明文副本**，来自重构前的原型目录。
现在的 App 把凭据用 Android Keystore AES-GCM 加密存在 `app/tieba_sign_accounts.json`
（已进 `.gitignore`），不再落地明文。**仅删文件不会让已经流出去的凭据失效**；
若认为可能外泄，改百度账号密码才是真正的吊销。
