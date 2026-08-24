#!/usr/bin/env bash
# gen_release_keys.sh - create the TiebaSign release signing keystore once.
# Keystore + signing.env live in <project>/sign (Windows side, backed up with
# the project). Passwords are random hex, written only to sign/signing.env
# with mode 600, and are never echoed by this script.
set -euo pipefail

D="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android/sign"
mkdir -p "$D"

if [ -f "$D/tiebasign-release.keystore" ]; then
    echo "KEYSTORE_EXISTS"
    ls -l "$D"
    exit 0
fi

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
