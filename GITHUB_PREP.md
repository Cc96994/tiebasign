# 上传 GitHub 准备清单

> 当前所在的 `github_src` 文件夹就是**待提交内容全集**：直接把它里的文件作为仓库根目录提交即可（或整夹改名后 `git init`）。`github_src/` 本身已被 `.gitignore` 排除，不会被二次打包进仓库。

按下面顺序做，可以安全地把**源码**上传 GitHub；APK 走 Releases 发布。

## 1. 绝对不能提交的内容（已写入 `.gitignore`）
- `sign/` 整个目录：`tiebasign-release.keystore`、`signing.env`（签名密钥库 + 密码）
  - 泄露 = 任何人可伪造你的应用更新包。
  - 务必**离线备份**（U 盘 / 加密压缩包）后不放进仓库。
- 任何 BDUSS / `BDUSS.txt` / 旧脚本 `tieba_sign_app`（本项目本来就没有，上传前再跑一次扫描）。

## 2. 上传前必跑
```bash
bash /mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android/scripts/leak_scan.sh
```
要求输出 `RESULT: CLEAN`（最近一次全量扫描：0 命中）。

## 3. 许可证选择（你决定，三选一）
- **Apache-2.0**：推荐，宽松且明确专利授权，App 类项目常用。
- MIT：最简单，但无专利条款。
- GPL-3.0：如果未来搬运了 TiebaLite 代码才必须；目前没搬代码，不必强制。
决定后在仓库根放 `LICENSE` 文件即可。README 里已注明“暂未选择许可证”。

## 4. 建仓库并推送
```bash
git init
git add .                     # .gitignore 会自动排除 sign/.buildozer/bin_*
git status                    # 人工确认 sign/ 等未出现在待提交列表
git commit -m "贴吧签到助手 0.3.0"
git branch -M main
git remote add origin https://github.com/<你的用户名>/tieba-sign-assistant.git
git push -u origin main
```
注意：脚本里硬编码了 `s7909` 的 Windows/WSL 路径，他人克隆后需按自己机器修改，README 已说明。

## 5. 发布正式包（GitHub Releases，不要 push 进 git）
- 仓库页面 → Releases → Draft a new release → tag `v0.3.0`
- 附件上传两个文件：
  - `release/tieba-sign-assistant-0.3.0-arm64-v8a-release.apk`
  - `release/SHA256SUMS`
- Release 说明：版本号、arm64-v8a、Android 7+（minSdk 23）、安装方式、免责声明（第三方客户端，与百度无关；遵守百度服务条款）。

## 6. 其它建议
- 仓库设为 Public 前，先在 GitHub 的 "Commits" / "Settings → Secrets" 里全文搜一遍 `BDUSS=`、`signing`、`keystore`。
- 不要把华为/卓易通兼容性相关的诊断目录（`bin_probe/`、`dltest` 等）提交，`.gitignore` 已默认忽略。
- 后续升级：改 `buildozer_webview_app.spec` 的 `version`，debug 测试通过后跑 `wsl_release_build.sh` + `verify_release.sh`，再发新 Release；**同一 keystore 永不能丢**，否则无法给老用户发升级包。
