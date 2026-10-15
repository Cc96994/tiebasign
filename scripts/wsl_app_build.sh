#!/bin/bash
# TiebaSign M2.2 WebView app build.
# Syncs the Windows project into WSL, temporarily switches buildozer.spec to
# buildozer_webview_app.spec, builds the M2.2 APK, then restores the main
# spec. Never deletes .buildozer (incremental cache must survive).
set -euo pipefail

# Project root: self-locating (this script lives in <root>/scripts/).
# Do NOT hardcode it: this line used to point at C:\Users\s7909\Desktop\... and
# the debug build died at its first cp once the tree moved to G:.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ_WIN="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROJ_WSL="${TIEBA_PROJ_WSL:-/home/s7909/tieba_sign_android}"

echo "== sync project to WSL (preserving .buildozer cache)"
mkdir -p "$PROJ_WSL"
cp -a "$PROJ_WIN"/. "$PROJ_WSL"/
# cp -a never deletes: prune files removed from the Windows tree so stale
# modules cannot leak into the APK (baidulogin.py was removed in 0.2.0).
rm -f "$PROJ_WSL/app/baidulogin.py"

cd "$PROJ_WSL"

# p4a 底座（仓库之外）里的 Java 补丁：安装入口 / ApkProvider / manifest 权限。
# 必须在 buildozer 之前跑，而且**每次**都跑：p4a 只在新建 dist 时把 bootstrap
# 的 build 目录拷进 dists/<name>/，之后复用 dist 就不再刷新，改了底座却不改
# dist 的话会静默编出旧代码（实测过一次：新方法一个都没进 dex）。
echo "== patch p4a base java (update/install entry)"
python3 "$PROJ_WSL/scripts/patch_base_java.py" \
    --p4a "${P4A_SRC:-/home/s7909/p4a-develop}" \
    --dist-root "$PROJ_WSL/.buildozer/android/platform/build-arm64-v8a/dists"

# Buildozer copies the app into .buildozer/android/app; clearing that
# generated directory keeps deleted source files out of private.tar.
rm -rf "$PROJ_WSL/.buildozer/android/app"
# Same reason: cp -a never deletes, so prune the archived _legacy/ tree
# (~337MB of old APKs) instead of dragging it into WSL on every build.
rm -rf "$PROJ_WSL/_legacy"

echo "== swap in M2.2 webview app spec"
# buildozer 只认 buildozer.spec，所以这里把现役 spec 换进去。两件必须成立的事
# （以前都不成立）：
#   * 新克隆的树里**根本没有** buildozer.spec —— 它在 .gitignore 里（历史投影
#     spec）。旧写法 `cp -a buildozer.spec ...` 在 set -e 下当场死掉，等于
#     克隆下来完全无法构建。没有 = 没有要保留的东西：下面直接生成、退出时删掉。
#   * 构建失败时旧写法会把换进去的 spec 留在树里（恢复那行在 buildozer 之后，
#     set -e 已经先退出了），下次构建就会拿错 spec。现在用 trap 兜底。
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

echo "== buildozer start: $(date)"
source "$HOME/buildozer-venv/bin/activate"
# rust toolchain for the cryptography recipe
# shellcheck disable=SC1091
source "$HOME/.cargo/env"
export VERSION_hostpython3=3.12.7
# WSL github.com is flaky: pull cryptography from the already-seeded PyPI
# sdist (basename cryptography-46.0.3.tar.gz + .mark are in packages cache).
export URL_cryptography='https://files.pythonhosted.org/packages/9f/33/c00162f49c0e2fe8064a62cb92b93e50c74a72bc370ab92f86112b33ff62/cryptography-{version}.tar.gz'
export PIP_DEFAULT_TIMEOUT=20 PIP_RETRIES=2
buildozer -v android debug

echo "== restore main spec"
restore_spec

echo "== buildozer done: $(date)"
ls -l "$PROJ_WSL/bin_m22/" 2>/dev/null || true
