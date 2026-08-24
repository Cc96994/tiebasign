#!/bin/bash
# wsl_release_build.sh - signed release APK build (same dist as debug).
# Loads P4A_RELEASE_* secrets from sign/signing.env (never printed).
set -euo pipefail

PROJ_WIN="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android"
PROJ_WSL="/home/s7909/tieba_sign_android"
SIGN_DIR="$PROJ_WIN/sign"
WSL_KS="/home/s7909/.buildozer/tiebasign-release.keystore"

echo "== sync project to WSL (preserving .buildozer cache)"
mkdir -p "$PROJ_WSL"
cp -a "$PROJ_WIN"/. "$PROJ_WSL"/
rm -f "$PROJ_WSL/app/baidulogin.py"

cd "$PROJ_WSL"
rm -rf "$PROJ_WSL/.buildozer/android/app"

echo "== prepare signing keystore"
[ -f "$SIGN_DIR/signing.env" ] || { echo "missing sign/signing.env"; exit 1; }
[ -f "$SIGN_DIR/tiebasign-release.keystore" ] || { echo "missing keystore"; exit 1; }
cp -f "$SIGN_DIR/tiebasign-release.keystore" "$WSL_KS"
# shellcheck disable=SC1090
set -a
source "$SIGN_DIR/signing.env"
set +a

echo "== swap in webview app spec"
if [ ! -f buildozer.spec.main.bak ]; then
    cp -a buildozer.spec buildozer.spec.main.bak
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
mv -f buildozer.spec.main.bak buildozer.spec

echo "== buildozer release done: $(date)"
ls -l "$PROJ_WSL/bin_m22/"*release* 2>/dev/null || true
