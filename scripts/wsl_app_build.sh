#!/bin/bash
# TiebaSign M2.2 WebView app build.
# Syncs the Windows project into WSL, temporarily switches buildozer.spec to
# buildozer_webview_app.spec, builds the M2.2 APK, then restores the main
# spec. Never deletes .buildozer (incremental cache must survive).
set -euo pipefail

PROJ_WIN="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android"
PROJ_WSL="/home/s7909/tieba_sign_android"

echo "== sync project to WSL (preserving .buildozer cache)"
mkdir -p "$PROJ_WSL"
cp -a "$PROJ_WIN"/. "$PROJ_WSL"/
# cp -a never deletes: prune files removed from the Windows tree so stale
# modules cannot leak into the APK (baidulogin.py was removed in 0.2.0).
rm -f "$PROJ_WSL/app/baidulogin.py"

cd "$PROJ_WSL"

# Buildozer copies the app into .buildozer/android/app; clearing that
# generated directory keeps deleted source files out of private.tar.
rm -rf "$PROJ_WSL/.buildozer/android/app"

echo "== swap in M2.2 webview app spec"
if [ ! -f buildozer.spec.main.bak ]; then
    cp -a buildozer.spec buildozer.spec.main.bak
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
mv -f buildozer.spec.main.bak buildozer.spec

echo "== buildozer done: $(date)"
ls -l "$PROJ_WSL/bin_m22/" 2>/dev/null || true
