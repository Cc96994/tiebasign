# 签名说明（SIGNING）

正式版 APK 用本地生成的发布密钥签名。签名材料一律**不入库、不上传**：

- `sign/tiebasign-release.keystore` —— 发布签名密钥库（alias `tiebasign`，RSA 2048，有效期 10000 天）
- `sign/signing.env` —— 构建脚本读取的签名口令
  （`.gitignore` 已排除 `/sign/`、`*keystore*`、`*signing.env`）

> 这份文档以前**只存在于 `github_src/`（上传暂存目录）里**，所以 0.4.0 阶段它跟着暂存
> 目录一起过期、在项目根消失了。凡是只活在暂存目录里的文件都会这样。现在它在项目根。

## 可以公开、也应公开的事实

证书（公钥）指纹——用来核对"这个包到底是不是我们发的"：

```
signer certificate SHA-256 = 84d7ed804fb7d2124cf51a083fb566286be0f381b83fe5689b667a2d3d6e0a7b
```

已发布版本的**不可变事实**，由 `scripts/check_upgrade_compat.sh` 在每次发布前强制校验
（记在 `scripts/app_identity.sh` 里）：

| | |
|---|---|
| applicationId | `org.dsh.tiebaweb` |
| 已发布版本 | v0.3.0，versionCode `1023300` |
| 已发布 APK sha256 | `be7ee57fdd3f78015653a986af046a1d6fe1c9ecc5d46562fe5802985efa115f` |
| 签名者证书 sha256 | 同上（`84d7ed80…0e0a7b`） |

## ⚠️ 新机器上**不要**运行 `gen_release_keys.sh`

`scripts/gen_release_keys.sh` 会生成一把**全新**密钥。对一个已经发布过的 App 来说，
换密钥等于换身份证：老用户装不上你的更新（Android 直接拒绝，`INSTALL_FAILED_UPDATE_INCOMPATIBLE`），
而且他们本机用 Android Keystore 加密保存的登录数据**再也读不出来**——只能卸载重装 + 重新登录。

正确做法是**把离线备份的 `sign/` 恢复回来**。只有在"这个 App 从未发布过"时才用那个脚本。

## 构建与核验

```bash
bash scripts/wsl_setup.sh          # 首次：解出 p4a 底座 + 铺手工改动快照
bash scripts/wsl_release_build.sh  # 出正式包：bin_m22/tiebaweb-<version>-arm64-v8a-release.apk
bash scripts/verify_release.sh     # 核验：签名/权限/dex 标记/离线自检/升级兼容性
```

`verify_release.sh` 会拒绝覆盖 `release/` 里已存在的同名 APK（已发布的字节永不改写），
并按文件名合并更新 `release/SHA256SUMS`。

## 必须遵守

1. 立即把整个 `sign/` 目录备份到**离线**安全位置（加密 U 盘 / 加密云盘）。
2. 不要把 `signing.env` 或密钥库发到聊天、群或任何代码托管平台。
   **也不要用 GitHub 的网页拖拽上传**：网页上传会绕过并丢掉 `.gitignore`。
3. 以后所有升级包（0.4.x、0.5.x…）必须继续使用同一密钥库。
4. 密钥库一旦丢失无法找回，只能换新签名并让所有用户卸载重装（= 丢掉登录数据）。
