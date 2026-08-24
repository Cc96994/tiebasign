# 签名说明（SIGNING）

正式版 APK 使用本地生成的发布密钥签名。签名材料一律**不入库、不上传**：

- `sign/tiebasign-release.keystore` —— 发布签名密钥库（alias: tiebasign, RSA 2048, 有效期 10000 天）
- `sign/signing.env` —— 构建脚本读取的签名口令（本项目 `.gitignore` 已排除 `*keystore*` 与 `*signing.env`）

## 首次准备（新机器）

在 WSL 环境就绪后运行：

```bash
bash scripts/gen_release_keys.sh
```

脚本会在 `sign/` 生成全新密钥库与 `signing.env`，口令为随机值，不会打印到日志。

## 构建正式版

```bash
bash scripts/wsl_release_build.sh
```

产物：`bin_m22/tiebaweb-<version>-arm64-v8a-release.apk`，可用 `scripts/verify_release.sh` 核验签名与内容。

## 必须遵守

1. 立即把整个 `sign/` 目录备份到离线安全位置（加密 U 盘/加密云盘）。
2. 不要把 `signing.env` 或密钥库发到聊天、群或任何代码托管平台。
3. 以后所有升级包（0.3.x、0.4.x…）必须继续使用同一密钥库，否则用户无法覆盖安装，只能卸载重装。
4. 密钥库一旦丢失无法找回，只能更换新签名并让用户卸载重装。