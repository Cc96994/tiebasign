#!/bin/bash
# verify_release.sh - signed release APK verification
set -uo pipefail
PROJ_WIN="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android"
PROJ_WSL="/home/s7909/tieba_sign_android"
APK="$PROJ_WSL/bin_m22/tiebaweb-0.3.0-arm64-v8a-release.apk"
SDK="/home/s7909/.buildozer/android/platform/android-sdk"
BT="$SDK/build-tools/37.0.0"
FAIL=0

[ -f "$APK" ] || { echo "APK MISSING"; exit 1; }

echo "== size/md5"
ls -l "$APK"
WSLMD5=$(md5sum "$APK" | awk '{print $1}')
echo "WSL_MD5=$WSLMD5"
echo "SIZE=$(stat -c %s "$APK")"

echo "== apksigner verify + cert"
SIGN_OUT=$("$BT/apksigner" verify --print-certs "$APK" 2>&1)
SIGN_EXIT=$?
echo "SIGN_EXIT=$SIGN_EXIT"
echo "$SIGN_OUT" | grep -a -E "Verified using|Signer #1 certificate DN|SHA-256" || true
echo "$SIGN_OUT" | grep -a -c "CN=TiebaSign" || true
V1=$("$BT/apksigner" verify --verbose "$APK" 2>&1 | grep -a -c 'Verified using v1 scheme (JAR signing): true' || true)
V2=$("$BT/apksigner" verify --verbose "$APK" 2>&1 | grep -a -c 'Verified using v2 scheme (APK Signature Scheme v2): true' || true)
echo "V1=$V1 V2=$V2"
[ "$SIGN_EXIT" = "0" ] && [ "$V1" = "1" ] && [ "$V2" = "1" ] || { echo "SIGN_CHECK_FAIL"; FAIL=1; }

echo "== dex markers"
TMPD=$(mktemp -d)
unzip -q -o "$APK" 'classes*.dex' -d "$TMPD"
for d in "$TMPD"/classes*.dex; do
  b=$(basename "$d")
  printf "%s startNativeLogin=%s native_detected=%s keepalive=%s clearLoginSession=%s svcCn=%s\n" \
    "$b" \
    "$(grep -a -c 'startNativeLogin' "$d" || true)" \
    "$(grep -a -c 'native_detected' "$d" || true)" \
    "$(grep -a -c 'TiebaSignKeepAliveService' "$d" || true)" \
    "$(grep -a -c 'clearLoginSession' "$d" || true)" \
    "$(grep -a -c '贴吧签到助手' "$d" || true)"
done

echo "== private.tar"
rm -rf "$TMPD/pt"; mkdir -p "$TMPD/pt"
tar -xzf <(unzip -p "$APK" assets/private.tar) -C "$TMPD/pt" 2>/dev/null || {
  unzip -p "$APK" assets/private.tar > "$TMPD/private.tar"
  tar -xzf "$TMPD/private.tar" -C "$TMPD/pt"
}
PYCS="$TMPD/pt"
echo "has_baidulogin=$(find "$PYCS" -iname 'baidulogin*' | wc -l)"
echo "main_acct_cb=$(grep -a -c 'acct_cb' "$PYCS/main.pyc" || true)"
echo "main_account_ids=$(grep -a -c 'account_ids' "$PYCS/main.pyc" || true)"
echo "main_no_tiebalite=$(grep -a -c 'TiebaLite' "$PYCS/main.pyc" || true)"
echo "signer_account_total=$(grep -a -c 'account_total' "$PYCS/signer.pyc" || true)"
echo "weblogin_startNativeLogin=$(grep -a -c 'startNativeLogin' "$PYCS/weblogin.pyc" || true)"

echo "== icon"
SRC_ICON_MD5=$(md5sum "$PROJ_WIN/app/icon.png" | awk '{print $1}')
APK_ICON_MD5=$(unzip -p "$APK" res/vI.png 2>/dev/null | md5sum | awk '{print $1}')
APK_ICON_INFO=$(unzip -p "$APK" res/vI.png 2>/dev/null | file - | grep -a -o '1280 x 1280' || true)
echo "SRC_ICON_MD5=$SRC_ICON_MD5"
echo "APK_ICON_MD5=$APK_ICON_MD5 (AAPT recompressed, md5 differs by design)"
echo "APK_ICON_INFO=$APK_ICON_INFO"
[ -n "$APK_ICON_INFO" ] && echo "ICON_DIMS_OK" || { echo "ICON_MISSING_OR_WRONG"; FAIL=1; }

echo "== manifest (utf-16)"
MAN=$(unzip -p "$APK" AndroidManifest.xml | iconv -f utf-16le -t utf-8 2>/dev/null | tr -d '\0')
echo "manifest_service=$(echo "$MAN" | grep -a -c 'TiebaSignKeepAliveService' || true)"
echo "manifest_debuggable=$(echo "$MAN" | grep -a -c 'android:debuggable="true"' || true)"

echo "== BDUSS scan (binary, hits only)"
BDUSS_HITS=$(unzip -p "$APK" | grep -a -c -E 'BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}' || true)
echo "BDUSS_HITS=$BDUSS_HITS"
[ "$BDUSS_HITS" = "0" ] || FAIL=1

echo "== copy to Windows"
DST="$PROJ_WIN/bin_m22/$(basename "$APK")"
cp -f "$APK" "$DST"
DSTMD5=$(md5sum "$DST" | awk '{print $1}')
echo "DST_MD5=$DSTMD5"
[ "$DSTMD5" = "$WSLMD5" ] || { echo "DST_MD5_MISMATCH"; FAIL=1; }

echo "== release dir + sha256"
mkdir -p "$PROJ_WIN/release"
REL="$PROJ_WIN/release/tieba-sign-assistant-0.3.0-arm64-v8a-release.apk"
cp -f "$APK" "$REL"
(cd "$PROJ_WIN/release" && sha256sum "tieba-sign-assistant-0.3.0-arm64-v8a-release.apk" > SHA256SUMS)
cat "$PROJ_WIN/release/SHA256SUMS"
echo "SHA256SUM=$("$BT/apksigner" version >/dev/null 2>&1; sha256sum "$APK" | awk '{print $1}')"

rm -rf "$TMPD"
echo "FAIL=$FAIL"
[ "$FAIL" = "0" ] && echo "VERIFY_RELEASE_OK"
exit $FAIL
