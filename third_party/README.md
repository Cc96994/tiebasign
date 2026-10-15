# third_party — 构建底座的**仓库内**副本

这个目录里两个文件是**故意入库**的（约 1.4MB）。它们解决同一个问题：
**这个 App 的原生能力住在仓库之外的 p4a 底座里，而底座以前只存在于某台机器上。**

历史上 `scripts/wsl_setup.sh` 的默认 p4a 源码包路径是一个早就不存在的 Windows
Temp 目录（`C:\Users\s7909\AppData\Local\Temp\aiotieba_preflight\p4a-dev.tar.gz`），
唯一活着的副本在兄弟项目 `tieba_sign_app/third_party/` 里 —— 换台机器就装不起来。

---

## 1. `p4a-dev.tar.gz` — pristine 上游源码

| | |
|---|---|
| 内容 | `python-for-android-develop/` 完整树，970 个成员 |
| 版本 | `pythonforandroid/__init__.py` → `__version__ = '2026.05.09'` |
| 许可 | MIT（`LICENSE`：Copyright (c) 2010-2025 Kivy Team and other contributors） |
| 字节 | 1,378,460 |
| sha256 | `8338b607e2c1dfe4278ac1789d053c4b938f1af4db91df8f2909f0edd9db0ab5` |
| 状态 | **未打任何本项目补丁**（无 `org/dsh/`、无 `ApkProvider`、无 `.inc`、无 `<provider>`） |

它是"干净底片"。要变成能构建的底座，还需要下面第 2 项和第 3 项。

## 2. `p4a-webview-base-overlay.tar.gz` — 手工改动快照

| | |
|---|---|
| 内容 | `webview/build/src/main/java/org/kivy/android/PythonActivity.java`、`webview/build/templates/AndroidManifest.tmpl.xml`、`P4A_OVERLAY_MANIFEST.txt` |
| 字节 | 14,829 |
| sha256 | `97344f44cd8a3f7ce1d15582b21edb5da9f782b223f936dee7a7c61f9b4f5f00` |
| 取自 | p4a `2026.05.09`，生成于 2026-10-16 |
| 重新生成 | `bash scripts/pack_p4a_overlay.sh`（幂等） |

**为什么它必须是快照，而不是补丁脚本**：底座上的改动分两类。

* **可复现的** → `java_src/` + `scripts/patch_base_java.py`（第 3 项）。
  PythonActivity.java 里的更新/安装入口块有 `// >>> / // <<<` 成对标记，
  manifest 里的 `<provider>` 也是插在锚点上，任何机器都能重做。
* **只能快照的** → 本次这两个文件。早期手工改上去的那批是**插在别的办法体内部**的，
  没法用"在锚点前插一段"重建：`startNativeLogin` 的钩子在 WebViewClient 的
  `shouldOverrideUrlLoading` / `onPageFinished` 里（同一批标记在文件里出现 5 次），
  还有清登录态、保活服务、权限请求、失败通知、日志开关，以及 manifest 里的
  `<service>`。它们不是独立方法块。

没有这一份，"新机器克隆 → 构建"出来的 APK 会**静默缺少原生登录和保活**。

## 3. 第三个要素不在这个目录里

`java_src/`（`org/dsh/tiebaweb/*.java` + `PythonActivity.updater.inc`）由
`scripts/patch_base_java.py` 打进底座。**注意 `java_src/` 必须入库** —— 它一度被
误列在 `.gitignore` 的"实验目录"里，那等于"克隆下来构建必然缺原生安装能力"。

三者合起来才是底座：**pristine tarball + overlay + java_src**。

---

## 验证

```bash
bash scripts/check_p4a_base.sh
```

它在一个临时目录里做净室重建（解开 pristine → 铺 overlay → 跑 patch_base_java），
然后断言：两个文件与 overlay 逐字节一致、`org/dsh/*.java` 与 `java_src/` 一致、
**App 真正会反射调用的每个 Java 方法都有定义**。方法名清单是从
`app/updater.py` 与 `app/autosign.py` 里现抓的，不是手抄的，所以新增一个反射调用
而忘了在底座里实现，会在这里失败。

## 两个已知事项（记录，不是已知良好）

1. **底座的 manifest 把 p4a 的模板钩子 `{{ args.extra_manifest_application_xml }}`
   替换掉了**，换成了写死的 `<service>`。这随 overlay 一起被保存下来。
   p4a 用这个变量注入 `args.extra_manifest_application_xml`（buildozer 的
   `android.extra_manifest_xml` 之类会落在这里）。本项目目前不用它，所以没有功能
   影响，但它确实是**上游钩子被移除**，将来若要用 p4a 的这个机制会踩到。
2. overlay 与 p4a 版本**强绑定**：`wsl_setup.sh` 会拿
   `P4A_OVERLAY_MANIFEST.txt` 里的 `p4a_version` 和树里的实际版本比对，不一致就
   **拒绝铺上去**（把别的 p4a 版本的 PythonActivity.java 盖进新树 = 静默降级）。
   将来升级 p4a 时，必须对着一份已经打好全部补丁的新底座重新
   `pack_p4a_overlay.sh`。
