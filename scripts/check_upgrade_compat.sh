#!/usr/bin/env bash
# check_upgrade_compat.sh - prove this APK really is the next version of the
# same app, so that an already-installed v0.3.0 can be upgraded in place.
#
# Three facts must hold, or the user cannot install over the old version:
#   1) applicationId identical  - otherwise it is a different app (side by side)
#   2) versionCode not lower    - otherwise INSTALL_FAILED_VERSION_DOWNGRADE
#   3) signer certificate same  - otherwise INSTALL_FAILED_UPDATE_INCOMPATIBLE
# Any of those failing forces an uninstall, which wipes the app's private dir
# (the stored sign-in data), so this is checked before every release.
#
# The v0.3.0 facts come from scripts/app_identity.sh.
#
# Usage:
#   bash scripts/check_upgrade_compat.sh <apk>
#   bash scripts/check_upgrade_compat.sh <apk> <published-v0.3.0.apk>   # also validate the recorded facts
set -uo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$HERE/app_identity.sh"

APK="${1:-}"
[ -n "$APK" ] || { echo "usage: check_upgrade_compat.sh <apk> [reference-apk]"; exit 2; }
[ -f "$APK" ] || { echo "APK not found: $APK"; exit 2; }
REF="${2:-}"

SDK="$HOME/.buildozer/android/platform/android-sdk"
AAPT2="$(ls "$SDK"/build-tools/*/aapt2 2>/dev/null | sort -V | tail -1 || true)"
APKSIGNER="$(ls "$SDK"/build-tools/*/apksigner 2>/dev/null | sort -V | tail -1 || true)"
[ -n "$AAPT2" ] || { echo "aapt2 not found under $SDK"; exit 2; }
[ -n "$APKSIGNER" ] || { echo "apksigner not found under $SDK"; exit 2; }

read_badging() {  # <apk> -> "id code"
    "$AAPT2" dump badging "$1" 2>/dev/null \
      | sed -n "s/^package: name='\([^']*\)'.*versionCode='\([0-9]*\)'.*/\1 \2/p" | head -1
}
read_signer() {   # <apk> -> lowercase sha256 without colons
    "$APKSIGNER" verify --print-certs "$1" 2>/dev/null \
      | grep -a -o -E 'SHA-256 digest: [0-9a-fA-F:]+' | head -1 \
      | sed 's/.*digest: //' | tr -d ':' | tr 'A-F' 'a-f'
}

read -r ID CODE <<EOF
$(read_badging "$APK")
EOF
SIGNER="$(read_signer "$APK")"

echo "   previous v$APP_PREV_VERSION (versionCode $APP_PREV_VERSION_CODE)"
printf '   applicationId : %s  (want %s)\n' "${ID:-<none>}" "$APP_PACKAGE"
printf '   versionCode   : %s  (want %s %s)\n' "${CODE:-<none>}" \
    "$([ "$APP_VERSION" = "$APP_PREV_VERSION" ] && echo '==' || echo '>')" "$APP_PREV_VERSION_CODE"
printf '   signer SHA-256: %s\n' "${SIGNER:-<none>}"
printf '                   (want %s)\n' "$APP_PREV_SIGNER_SHA256"

fail=0

# ---- optional: validate the recorded v0.3.0 facts against a real reference APK
if [ -n "$REF" ]; then
    [ -f "$REF" ] || { echo "reference APK not found: $REF"; exit 2; }
    read -r RID RCODE <<EOF
$(read_badging "$REF")
EOF
    RSIGN="$(read_signer "$REF")"
    echo "   -- reference APK: $REF"
    printf '      id=%s code=%s signer=%s\n' "${RID:-<none>}" "${RCODE:-<none>}" "${RSIGN:-<none>}"
    [ "$RID" = "$APP_PACKAGE" ] || { echo "FAIL recorded APP_PACKAGE disagrees with the reference APK"; fail=1; }
    [ "$RCODE" = "$APP_PREV_VERSION_CODE" ] || { echo "FAIL recorded APP_PREV_VERSION_CODE disagrees with the reference APK"; fail=1; }
    [ "$RSIGN" = "$APP_PREV_SIGNER_SHA256" ] || { echo "FAIL recorded APP_PREV_SIGNER_SHA256 disagrees with the reference APK"; fail=1; }
fi

if [ "$ID" != "$APP_PACKAGE" ]; then
    echo "FAIL applicationId differs - this is another app, existing users cannot upgrade to it"
    fail=1
fi

if [ -z "$CODE" ]; then
    echo "FAIL could not read versionCode from the APK"
    fail=1
elif [ "$APP_VERSION" = "$APP_PREV_VERSION" ]; then
    # Rebuild of the already published version: same versionCode is expected.
    if [ "$CODE" != "$APP_PREV_VERSION_CODE" ]; then
        echo "FAIL spec version == $APP_PREV_VERSION but versionCode is $CODE, expected $APP_PREV_VERSION_CODE"
        fail=1
    fi
elif [ "$CODE" -le "$APP_PREV_VERSION_CODE" ]; then
    echo "FAIL versionCode not greater than the previous one - INSTALL_FAILED_VERSION_DOWNGRADE"
    echo "     spec version is $APP_VERSION; it must map to a code above $APP_PREV_VERSION_CODE"
    fail=1
fi

if [ "$SIGNER" != "$APP_PREV_SIGNER_SHA256" ]; then
    echo "FAIL signer differs from the previous version - INSTALL_FAILED_UPDATE_INCOMPATIBLE"
    echo "     check that sign/signing.env points at sign/$APP_KEYSTORE_NAME"
    fail=1
fi

[ "$fail" -eq 0 ] || { echo "UPGRADE_COMPAT_FAIL"; exit 1; }
echo "UPGRADE_COMPAT_OK"
