#!/usr/bin/env bash
# check_p4a_base.sh - 净室验证：仓库里的材料能不能重建出"构建用的 p4a 底座"？
#
# 为什么需要它：这个 App 的原生能力（原生登录、保活服务、Keystore、更新安装入口）
# 全部住在仓库之外的 p4a 底座里。底座一旦在某台机器上重新解压，这些能力会
# **静默消失** —— 构建照样成功，Python 侧只会打印一句"方法未找到"。
# verify_release.sh 只能在 APK 里查 dex 标记（发布时才发现）；这个脚本更早、
# 更直接：它真的做一次净室重建，然后断言该在的东西都在。
#
# 净室重建 = third_party/p4a-dev.tar.gz（pristine）+ third_party/*overlay*.tar.gz
#            + java_src/（经 patch_base_java.py）。
# 全程在临时目录里，**不碰** p4a 底座、不碰 .buildozer/dist，不改仓库任何文件。
#
# 用法（WSL 内，或任何有 python3 的地方）：
#   bash scripts/check_p4a_base.sh
# Exit: 0 = P4A_BASE_OK, 1 = 有断言失败, 2 = 环境不满足（缺材料/缺 python3）
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd -- "$SCRIPT_DIR/.." && pwd)"
P4A_TAR="$PROJ/third_party/p4a-dev.tar.gz"
OVERLAY="$PROJ/third_party/p4a-webview-base-overlay.tar.gz"
JAVA_SRC="$PROJ/java_src"

PA_REL="src/main/java/org/kivy/android/PythonActivity.java"
MAN_REL="templates/AndroidManifest.tmpl.xml"
BOOT_SUB="pythonforandroid/bootstraps/webview/build"

FAIL=0
ok()   { echo "ok    $*"; }
fail() { echo "FAIL  $*"; FAIL=1; }
note() { echo "note  $*"; }

# ---- 0. 材料与环境 ---------------------------------------------------------
echo "== materials"
for f in "$P4A_TAR" "$OVERLAY"; do
    if [ -f "$f" ]; then
        ok "$(basename -- "$f")  $(stat -c %s "$f") bytes  sha256 $(sha256sum "$f" | awk '{print $1}')"
    else
        fail "missing $f  (see third_party/README.md)"
    fi
done
[ -d "$JAVA_SRC/org" ] || fail "missing $JAVA_SRC/org - java_src/ must be committed (it is a build input, not scratch)"
PY="${PYTHON:-python3}"
command -v "$PY" >/dev/null 2>&1 || { echo "FAIL  no python3 on PATH"; exit 2; }
[ "$FAIL" = 0 ] || { echo; echo "P4A_BASE_FAILED"; exit 1; }

# ---- 1. 净室重建 -----------------------------------------------------------
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
tar -xzf "$P4A_TAR" -C "$tmp" || { echo "FAIL  cannot extract $P4A_TAR"; exit 2; }
P4A_TMP="$tmp/python-for-android-develop"
BOOT="$P4A_TMP/$BOOT_SUB"
[ -f "$BOOT/$PA_REL" ] || { echo "FAIL  the tarball does not contain $BOOT_SUB/$PA_REL"; exit 2; }

# keep the pristine copies so we can prove the overlay actually changes something
cp -f "$BOOT/$PA_REL" "$tmp/pristine-PythonActivity.java"

mkdir -p "$tmp/ov"
tar -xzf "$OVERLAY" -C "$tmp/ov" || { echo "FAIL  cannot extract $OVERLAY"; exit 2; }
OV="$tmp/ov/webview/build"
[ -f "$OV/$PA_REL" ] || { echo "FAIL  the overlay does not contain $PA_REL"; exit 2; }

echo
echo "== the overlay must actually carry something"
if cmp -s "$OV/$PA_REL" "$tmp/pristine-PythonActivity.java"; then
    fail "the overlay's PythonActivity.java is identical to the pristine one - it carries no patches"
else
    ok "overlay PythonActivity.java differs from pristine (as it must)"
fi
for m in TiebaSignNativeLoginChrome TiebaSignKeepAliveMethods TiebaSignUpdaterMethods; do
    if grep -q -- "$m" "$OV/$PA_REL"; then ok "overlay carries marker $m"
    else fail "overlay is missing marker $m"; fi
done
if grep -q 'TiebaSignKeepAliveService' "$OV/$MAN_REL"; then ok "overlay manifest carries the keep-alive <service>"
else fail "overlay manifest is missing the keep-alive <service>"; fi

echo
echo "== clean room: pristine + overlay + java_src"
cp -f "$OV/$PA_REL"  "$BOOT/$PA_REL"
cp -f "$OV/$MAN_REL" "$BOOT/$MAN_REL"
# --dist-root points at a path that does not exist on purpose: this script must
# not touch the real buildozer dists (they are build state, not source).
if "$PY" "$SCRIPT_DIR/patch_base_java.py" --p4a "$P4A_TMP" \
        --dist-root "$tmp/no-such-dists" > "$tmp/patch.log" 2>&1; then
    ok "patch_base_java.py ran on the reconstructed base"
    sed 's/^/        /' "$tmp/patch.log"
else
    fail "patch_base_java.py failed on the reconstructed base"
    sed 's/^/        /' "$tmp/patch.log"
fi

# ---- 2. 断言 ---------------------------------------------------------------
echo
echo "== the reconstructed base must match the snapshot byte for byte"
# patch_base_java must be idempotent with respect to the overlay: running it on an
# already-patched file may not change that file.
if cmp -s "$BOOT/$PA_REL" "$OV/$PA_REL"; then ok "$PA_REL unchanged (patch is idempotent here)"
else fail "$PA_REL was modified by the patch run - overlay and java_src disagree"; fi
if cmp -s "$BOOT/$MAN_REL" "$OV/$MAN_REL"; then ok "$MAN_REL unchanged"
else fail "$MAN_REL was modified by the patch run"; fi

echo "== java classes the repo owns must land in the base"
for src in "$JAVA_SRC"/org/dsh/tiebaweb/*.java; do
    rel="org/dsh/tiebaweb/$(basename -- "$src")"
    dst="$BOOT/src/main/java/$rel"
    if [ ! -f "$dst" ]; then fail "$rel was not copied into the base"
    elif cmp -s "$src" "$dst"; then ok "$rel"
    else fail "$rel in the base differs from java_src/"; fi
done

echo
echo "== every Java method the app reflects must have a definition"
# The list is extracted from the app, not hand-copied, so adding a reflected call
# without implementing it fails here. Patterns: updater.py uses _call_static_raw,
# autosign.py and weblogin.py use _call_static / _find_static_method.
names="$("$PY" - "$PROJ" <<'PY'
import glob, os, re, sys
proj = sys.argv[1]
pats = [r'_call_static_raw\(\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        r'_call_static\(\s*"([A-Za-z_][A-Za-z0-9_]*)"',
        r'_find_static_method\([^,]+,\s*"([A-Za-z_][A-Za-z0-9_]*)"']
found = set()
for f in glob.glob(os.path.join(proj, "app", "*.py")):
    with open(f, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    for p in pats:
        found.update(re.findall(p, text))
print("\n".join(sorted(found)))
PY
)"
n_names="$(printf '%s\n' "$names" | grep -c . || true)"
echo "   reflected names found in app/*.py: $n_names"
# Guard against a vacuous pass: if the extraction breaks, the loop below would
# assert nothing and this script would happily print OK.
if [ "${n_names:-0}" -lt 10 ]; then
    fail "only $n_names reflected names were extracted - the extractor is broken (expect >= 10), refusing to pass vacuously"
fi
for n in $names; do
    if grep -q -E "static[[:space:]]+[^[:space:]]+[[:space:]]+$n[[:space:]]*\\(" "$BOOT/$PA_REL"; then
        ok "PythonActivity.$n"
    else
        fail "PythonActivity.$n is called by the app but has no definition in the reconstructed base"
    fi
done

echo
echo "== manifest additions"
if grep -q 'TiebaSignKeepAliveService' "$BOOT/$MAN_REL"; then ok "keep-alive <service>"
else fail "keep-alive <service> missing"; fi
if grep -q 'apkprovider' "$BOOT/$MAN_REL"; then ok "ApkProvider authority"
else fail "ApkProvider authority missing"; fi

# ---- 3. 与当前底座的对照（只报告，不判失败） --------------------------------
LIVE="${P4A_SRC:-/home/s7909/p4a-develop}/$BOOT_SUB"
echo
if [ -f "$LIVE/$PA_REL" ]; then
    echo "== drift against the live base at ${P4A_SRC:-/home/s7909/p4a-develop}"
    for rel in "$PA_REL" "$MAN_REL"; do
        if cmp -s "$BOOT/$rel" "$LIVE/$rel"; then
            ok "live base matches the reconstruction: $rel"
        else
            note "live base DIFFERS from the reconstruction: $rel"
            note "  (this is not a failure: the live base may carry newer hand edits;"
            note "   if those edits are wanted, re-run scripts/pack_p4a_overlay.sh)"
        fi
    done
else
    note "no live base at ${P4A_SRC:-/home/s7909/p4a-develop} - skipping the drift comparison"
fi

echo
if [ "$FAIL" = 0 ]; then echo "P4A_BASE_OK"; else echo "P4A_BASE_FAILED"; fi
exit "$FAIL"
