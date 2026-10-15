#!/usr/bin/env bash
# pack_p4a_overlay.sh - 把 p4a webview 底座上"手工改的、无法用拼接复现"的那部分
# 打包成 third_party/p4a-webview-base-overlay.tar.gz。
#
# 为什么需要它
# ------------
# 这个 App 的原生能力大部分住在仓库之外的 p4a 底座里。底座上的改动分两类：
#
#   A. 可以复现的：`scripts/patch_base_java.py` + `java_src/`
#      —— 包括 PythonActivity.java 里的更新/安装入口块（有 begin/end 标记）和
#      manifest 里的 <provider>。任何机器都能重做。
#
#   B. 只能快照的：早期手工改上去的那批 —— 原生登录（WebViewClient 里 5 处）、
#      清登录态、保活服务、权限请求、失败通知、日志开关、manifest 里的
#      <service>。它们**不是**独立的方法块：很多是插在别的方法体内部
#      （例如 startNativeLogin 的钩子就在 WebViewClient.shouldOverrideUrlLoading
#      里），所以没法用"在锚点前插一段"的方式重建。
#
# 没有 B 的话，"新机器克隆 → 构建"出来的 APK 会**静默缺少原生登录和保活**。
# 所以 B 必须作为快照入库：pristine p4a tarball + 这份 overlay + java_src = 底座。
#
# 幂等：可反复运行，每次按当前底座重新生成。
#
# 用法（WSL 内）：
#   bash scripts/pack_p4a_overlay.sh
#   P4A_SRC=/path/to/p4a bash scripts/pack_p4a_overlay.sh
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd -- "$SCRIPT_DIR/.." && pwd)"
P4A="${P4A_SRC:-/home/s7909/p4a-develop}"
BOOT="$P4A/pythonforandroid/bootstraps/webview/build"
PA_REL="src/main/java/org/kivy/android/PythonActivity.java"
MAN_REL="templates/AndroidManifest.tmpl.xml"
OUT="$PROJ/third_party/p4a-webview-base-overlay.tar.gz"

die() { echo "pack_p4a_overlay: ERROR: $*" >&2; exit 1; }

[ -f "$BOOT/$PA_REL" ] || die "底座里没有 $PA_REL（--p4a 指对了吗？$P4A）"
[ -f "$BOOT/$MAN_REL" ] || die "底座里没有 $MAN_REL"

VER="$(sed -n "s/^__version__ = '\([^']*\)'.*/\1/p" \
        "$P4A/pythonforandroid/__init__.py" | head -1)"
[ -n "$VER" ] || die "读不出 p4a 版本（$P4A/pythonforandroid/__init__.py）"

# 底座上必须真的带着 B 类改动，否则我们就是在把"没打过补丁的底座"当补丁存下来。
missing=""
for m in TiebaSignNativeLoginChrome TiebaSignKeepAliveMethods \
         startNativeLogin clearLoginSession startKeepAliveService notifySignFail \
         TiebaSignUpdaterMethods; do
    grep -q -- "$m" "$BOOT/$PA_REL" || missing="$missing $m"
done
grep -q 'TiebaSignKeepAliveService' "$BOOT/$MAN_REL" \
    || missing="$missing manifest:<service>"
[ -z "$missing" ] || die "底座缺少预期标记，拒绝打包：$missing"

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/webview/build/src/main/java/org/kivy/android" \
         "$stage/webview/build/templates"
cp -f "$BOOT/$PA_REL"  "$stage/webview/build/$PA_REL"
cp -f "$BOOT/$MAN_REL" "$stage/webview/build/$MAN_REL"

{
    echo "# p4a webview base overlay - 手工改动快照，由 scripts/pack_p4a_overlay.sh 生成"
    echo "# 覆盖到 <p4a>/pythonforandroid/bootstraps/ 即可（见 wsl_setup.sh）"
    echo "p4a_version=$VER"
    echo "generated_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "p4a_src=$P4A"
    echo "# sha256 of the files below"
    ( cd "$stage" && sha256sum "webview/build/$PA_REL" "webview/build/$MAN_REL" )
} > "$stage/P4A_OVERLAY_MANIFEST.txt"

mkdir -p "$PROJ/third_party"
tar -czf "$OUT" -C "$stage" P4A_OVERLAY_MANIFEST.txt webview

echo "== wrote $OUT"
echo "   p4a version : $VER"
echo "   bytes       : $(stat -c %s "$OUT")"
echo "   sha256      : $(sha256sum "$OUT" | awk '{print $1}')"
echo "   contents    :"
tar -tzf "$OUT" | sed 's/^/     /'
echo "PACK_P4A_OVERLAY_OK"
