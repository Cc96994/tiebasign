#!/bin/bash
# wsl_release_build.sh - signed release APK build (same dist as debug).
# Loads P4A_RELEASE_* secrets from sign/signing.env (never printed).
set -euo pipefail

# Project root: self-locating (this script lives in <root>/scripts/).
# Do NOT hardcode it: this line used to point at C:\Users\s7909\Desktop\... and
# the whole release script died at its very first cp once the tree moved to G:.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ_WIN="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROJ_WSL="/home/s7909/tieba_sign_android"
SIGN_DIR="$PROJ_WIN/sign"
WSL_KS="/home/s7909/.buildozer/tiebasign-release.keystore"

echo "== sync project to WSL (preserving .buildozer cache)"
mkdir -p "$PROJ_WSL"
cp -a "$PROJ_WIN"/. "$PROJ_WSL"/
rm -f "$PROJ_WSL/app/baidulogin.py"

cd "$PROJ_WSL"
rm -rf "$PROJ_WSL/.buildozer/android/app"
# _legacy/ is archived history (~337MB of old APKs). cp -a never deletes, so it
# would be dragged into WSL on every build; prune it like the other stale paths.
rm -rf "$PROJ_WSL/_legacy"

# p4a 底座（仓库之外）里的 Java 补丁：安装入口 / ApkProvider / manifest 权限。
# 每次构建都要跑：p4a 只在新建 dist 时把 bootstrap 拷进 dists/<name>/，之后复用
# dist 就不再刷新，改了底座却不改 dist 会静默编出旧代码。
echo "== patch p4a base java (update/install entry)"
python3 "$PROJ_WSL/scripts/patch_base_java.py" \
    --p4a "${P4A_SRC:-/home/s7909/p4a-develop}" \
    --dist-root "$PROJ_WSL/.buildozer/android/platform/build-arm64-v8a/dists"

echo "== prepare signing keystore"
[ -f "$SIGN_DIR/signing.env" ] || { echo "missing sign/signing.env"; exit 1; }
[ -f "$SIGN_DIR/tiebasign-release.keystore" ] || { echo "missing keystore"; exit 1; }
cp -f "$SIGN_DIR/tiebasign-release.keystore" "$WSL_KS"
# shellcheck disable=SC1090
set -a
source "$SIGN_DIR/signing.env"
set +a

echo "== swap in webview app spec"
# 与 wsl_app_build.sh 同源的两处修正，原因见那边的长注释：
#   1. 新克隆没有 buildozer.spec（.gitignore 里），旧的 `cp -a buildozer.spec`
#      在 set -e 下当场失败 —— 克隆下来根本发不了版；
#   2. 构建失败时旧写法会把换进去的 spec 留在树里，trap 现在负责还原。
SPEC_BAK=buildozer.spec.main.bak
_SPEC_RESTORED=0
restore_spec() {
    [ "$_SPEC_RESTORED" = 1 ] && return 0
    _SPEC_RESTORED=1
    if [ -f "$SPEC_BAK" ]; then
        mv -f "$SPEC_BAK" buildozer.spec
    else
        rm -f buildozer.spec
    fi
}
trap restore_spec EXIT
if [ -f "$SPEC_BAK" ]; then
    echo "   note: a previous run left $SPEC_BAK behind - treating it as the original"
elif [ -f buildozer.spec ]; then
    cp -a buildozer.spec "$SPEC_BAK"
else
    echo "   note: no buildozer.spec in this tree (fresh clone) - nothing to preserve"
fi
cp -f buildozer_webview_app.spec buildozer.spec

echo "== buildozer release start: $(date)"
source "$HOME/buildozer-venv/bin/activate"
# shellcheck disable=SC1091
source "$HOME/.cargo/env"
export VERSION_hostpython3=3.12.7
export URL_cryptography='https://files.pythonhosted.org/packages/9f/33/c00162f49c0e2fe8064a62cb92b93e50c74a72bc370ab92f86112b33ff62/cryptography-{version}.tar.gz'
export PIP_DEFAULT_TIMEOUT=20 PIP_RETRIES=2
buildozer -v android release

echo "== restore main spec"
restore_spec

echo "== buildozer release done: $(date)"
ls -l "$PROJ_WSL/bin_m22/"*release* 2>/dev/null || true
