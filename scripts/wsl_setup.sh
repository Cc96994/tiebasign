#!/usr/bin/env bash
# One-time WSL setup for TiebaSign M1 build.
# Idempotent: safe to re-run.
set -euo pipefail

P4A_SRC=/home/s7909/p4a-develop
P4A_TAR=/mnt/c/Users/s7909/AppData/Local/Temp/aiotieba_preflight/p4a-dev.tar.gz
WRAPPER="$P4A_SRC/pythonforandroid/bootstraps/common/build/gradle/wrapper/gradle-wrapper.properties"
BUILD_TMPL="$P4A_SRC/pythonforandroid/bootstraps/common/build/templates/build.tmpl.gradle"

echo "== 1/4: extract p4a develop into $P4A_SRC"
if [ ! -f "$P4A_SRC/pythonforandroid/toolchain.py" ]; then
  mkdir -p "$P4A_SRC"
  tar -xzf "$P4A_TAR" --strip-components=1 -C "$P4A_SRC"
fi

echo "== 2/4: patch gradle network (WSL cannot reach github/maven.google/services.gradle)"
if ! grep -q 'mirrors.cloud.tencent.com/gradle' "$WRAPPER"; then
  sed -i 's#services.gradle.org/distributions#mirrors.cloud.tencent.com/gradle#' "$WRAPPER"
fi
if ! grep -q 'maven.aliyun.com/repository/google' "$BUILD_TMPL"; then
  sed -i "s#google()#maven { url 'https://maven.aliyun.com/repository/google' }#g" "$BUILD_TMPL"
  sed -i "s#mavenCentral()#maven { url 'https://maven.aliyun.com/repository/central' }#g" "$BUILD_TMPL"
fi
grep -n 'distributionUrl' "$WRAPPER"
grep -n 'repository' "$BUILD_TMPL" | head -6

echo "== 3/4: rust toolchain (cryptography 46 is rust-based)"
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
fi
# shellcheck disable=SC1091
source "$HOME/.cargo/env"
rustup target add aarch64-linux-android

echo "== 4/4: sync project into WSL (preserving .buildozer cache)"
mkdir -p /home/s7909/tieba_sign_android
cp -a "/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android"/. /home/s7909/tieba_sign_android/
ls -la /home/s7909/tieba_sign_android

echo "== setup done =="
