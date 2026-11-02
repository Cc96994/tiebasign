# BDUSS / 凭据泄露审计（v0.5.0）

> 审计时间：2026-11-03
> 对象：GitHub 仓库 `Cc96994/tiebasign`（含全部分支、tag、提交历史）与 App 源码。
> 本文只记录方法、结论和残留风险，**不包含任何真实 BDUSS、掩码或可复用的凭据**。

## 1. 为什么要做这次审计

v0.5.0 工单第 4 项要求：做好保密工作，不泄露 BDUSS，并检查之前有无泄露风险。
App 的常规路径（Keystore 加密、界面只显示掩码）从 0.3.0 起就有，但「以前有没有
漏过」需要一次独立核查，而不是只看当前代码。

## 2. 检查范围与方法

| 范围 | 方法 |
|---|---|
| 当前工作树（全部文本文件） | `bash scripts/leak_scan.sh`：`BDUSS=...` / `BDUSS%3D...` 值正则 + 文件名含 `BDUSS` 的规则；扫描根不存在或 0 文件会报错，不会假装 CLEAN |
| Git 历史（全部分支 + tag） | 新增 `bash scripts/check_history_secrets.sh`：扫 `git log -p --all` 的值模式与 `git rev-list --objects --all` 的文件名；只输出计数，不输出命中内容 |
| 已发布的 0.3.0 / 0.4.0 tag | 对每个 tag 执行 `git grep -I -E 'BDUSS(=|%3D)...'` |
| 已发布的 0.4.0 APK（GitHub Releases 资产） | 下载 20,707,486 字节的 release APK，用 Python `zipfile` 扫全部条目与嵌套的 `assets/private.tar`（13 个 pyc/png）：查 `BDUSS=` 值、文件名含 `BDUSS`/`tiebasign_accounts`/`.keystore` 的条目；再抽查 150~220 字符裸 base64 形状 |
| GitHub Release 说明 | 用 `gh release view` 读正文，只允许出现「BDUSS」这个名词，不允许出现值 |
| 源码日志路径 | 逐文件检查 `print` / 日志 / 错误详情里与 BDUSS 相关的变量；只允许长度或掩码 |
| App 数据文件 | `tiebasign_accounts.json` 只存 `bduss_enc`（Keystore AES-GCM 密文）与掩码；不存明文 |
| 本地历史目录（仓库外） | 已知 `_legacy/tieba_sign_app_proto/BDUSS.txt` 是明文凭据副本，2026-10-16 已移出到隔离目录；确认它从未进入本仓库历史 |

## 3. 结论

| 项 | 结果 |
|---|---|
| 工作树值模式 | **0 命中**（`RESULT: CLEAN`） |
| 工作树文件名规则 | **0 个**含 `BDUSS` 的文件 |
| Git 历史值模式 | **0 行命中**（审计时 10 个提交、2 个 tag；PR 提交后 13 个提交复扫仍 CLEAN） |
| Git 历史文件名 | **0 个** |
| Release 说明 | 只有功能描述，没有值 |
| 源码日志 | 只记录 `bduss_len` 或掩码 |
| 0.3.0 / 0.4.0 tag | CLEAN |
| 0.4.0 release APK | 25 个 zip 条目 + `private.tar` 13 个成员：**0 个** BDUSS 值、**0 个**账号/密钥文件；长 token 抽查只命中 `libsqlite3.so` 里的 `DATEIMME…`/`TIMESTAMP…` 常量，不是凭据 |

v0.5.0 另外做了三处加固：

1. **日志脱敏统一化**：`signer` / `autosign` / `waterpost` 的日志都经过
   `app/redact.py`：先按当前 BDUSS 精确替换，再按形状兜底（120+ 字符的
   base64-ish 连续串、`BDUSS=` / `BDUSS:` / `BDUSS%3D` 后的值）。sha256
   等 64 字符以下的正常字段不会被误伤。
2. **签到报告不含凭据**：`sign.log` 与 `tiebasign_sign_last.json` 都落在
   `ANDROID_PRIVATE`，报告只带账号名、BDUSS 掩码、错误码和原因；导出页
   `/sign/report` 明确写明「只保留掩码」。
3. **历史门禁**：`leak_scan.sh` 在 git 仓库里会自动调用
   `check_history_secrets.sh`，以后有人把凭据提交进历史会直接 FAIL，而不是
   等到发布前才靠人肉发现。

## 4. 已发现的真实风险与处置

| 风险 | 处置 |
|---|---|
| 重构前的原型目录里有一份**还活着的明文 BDUSS**（`BDUSS.txt`，192 字符裸 base64），掩码与 App 当前账号一致 | 2026-10-16 已移出项目到隔离目录；本次确认从未进入 GitHub 仓库历史。**仅删文件不会让已流出的凭据失效**——如果担心它曾被复制/上传，改百度账号密码才是真正的吊销 |
| `ROADMAP.md` 里写有**真实账号掩码、账号昵称、账号文件 md5 前缀**（A 类账号标识） | v0.5.0 已替换为虚构值（`abCD****wxyz` / `示例账号` / `00000000…`）；本次审计后仓库里不再出现真实账号标识 |
| 文档/脚本里的本机路径含 Windows/WSL 用户名（B 类本机身份） | 不属于凭据；其中一部分是构建必需（`p4a.source_dir` 等 B3 值），改了会直接构建失败。本次不机械替换，留作独立的「账号标识脱敏」工单处理 |

## 5. 残留风险（如实记录）

- 审计只能证明**当前仓库与历史里没有 BDUSS 值**，不能证明这份凭据从未被复制到
  仓库之外（聊天记录、旧备份、其他机器）。若用户认为可能外泄，唯一可靠的吊销
  方式是修改百度账号密码。
- 构建产物 APK 不参与本次 git 审计；发布前仍应按 README 跑
  `scripts/leak_scan.sh`（它会扫描本机 APK 与 dists）。
- 仓库外的本地 `_legacy/`、`_ui_shots/` 等目录不在 GitHub 上，但其中的截图可能
  含账号名；它们不在上传集内，本次不处理。
