#!/usr/bin/env bash
# gen_release_keys.sh - create the TiebaSign release signing keystore once.
# Keystore + signing.env live in <project>/sign (Windows side, backed up with
# the project). Passwords are random hex, written only to sign/signing.env
# with mode 600, and are never echoed by this script.
set -euo pipefail

# Project root: self-locating (this script lives in <root>/scripts/).
# It used to point at C:\Users\s7909\Desktop\..., and because the "keystore
# already exists, do nothing" guard below tests THIS directory, a run after the
# move to G: would have found no keystore there and quietly minted a SECOND,
# wrong release key. Never let that guard be the only thing standing between
# this script and a new key.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
D="${TIEBA_SIGN_DIR:-$(cd -- "$SCRIPT_DIR/.." && pwd)/sign}"
mkdir -p "$D"

if [ -f "$D/tiebasign-release.keystore" ]; then
    echo "KEYSTORE_EXISTS"
    ls -l "$D"
    exit 0
fi

# Second guard, independent of the path: the real project already has a key
# committed in sign/. Minting another one would strand every installed user.
for probe in "$SCRIPT_DIR/../sign" "$SCRIPT_DIR/.."; do
    if [ -f "$probe/tiebasign-release.keystore" ] || [ -f "$probe/signing.env" ]; then
        echo "REFUSING: a release keystore / signing.env already exists at $probe"
        echo "          生成新钥会让所有已安装用户无法升级。此脚本只应在新项目里跑一次。"
        exit 1
    fi
done

SP="$(openssl rand -hex 16)"
KP="$(openssl rand -hex 16)"
WSL_KS="/home/s7909/.buildozer/tiebasign-release.keystore"

keytool -genkeypair -v \
    -keystore "$D/tiebasign-release.keystore" \
    -alias tiebasign \
    -keyalg RSA -keysize 2048 -validity 10000 \
    -storepass "$SP" -keypass "$KP" \
    -dname "CN=TiebaSign, OU=DSH, O=DSH, L=Unknown, ST=Unknown, C=CN" \
    >/dev/null 2>&1

{
    echo "P4A_RELEASE_KEYALIAS=tiebasign"
    echo "P4A_RELEASE_KEYSTORE_PASSWD=${SP}"
    echo "P4A_RELEASE_KEYSTORE=${WSL_KS}"
    echo "P4A_RELEASE_KEYALIAS_PASSWD=${KP}"
} > "$D/signing.env"

chmod 600 "$D/signing.env" "$D/tiebasign-release.keystore"
echo "KEYSTORE_CREATED"
ls -l "$D"
