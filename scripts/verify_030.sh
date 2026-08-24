#!/usr/bin/env bash
# verify_030.sh - 0.3.0 final app build verification (rename + icon +
# multi-account manual sign) + BDUSS leak scan.
set -u
APK=/home/s7909/tieba_sign_android/bin_m22/tiebaweb-0.3.0-arm64-v8a-debug.apk
DST="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android/bin_m22/tiebaweb-0.3.0-arm64-v8a-debug.apk"
ICON_SRC="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_app/签到.png"
PAT='BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}'

if [ ! -f "$APK" ]; then echo "APK MISSING: $APK"; exit 1; fi
SZ=$(stat -c%s "$APK"); MD5=$(md5sum "$APK" | awk '{print toupper($1)}')
echo "APK_SIZE=$SZ"; echo "APK_MD5=$MD5"

TMP=$(mktemp -d); cd "$TMP"
unzip -q "$APK" 'classes*.dex' 'assets/private.tar' 'resources.arsc' || { echo "UNZIP FAIL"; exit 1; }

DEX_HITS=0
for d in classes*.dex; do
  c=$(grep -a -c 'startNativeLogin' "$d" 2>/dev/null || true)
  if [ "${c:-0}" -gt 0 ]; then DEX_HITS=$((DEX_HITS+c)); fi
done
c2=$(grep -a -c 'native_detected' classes*.dex 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
c3=$(grep -a -c 'TiebaSignKeepAliveService' classes*.dex 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
c4=$(grep -a -c 'TiebaSignClearLoginSession' classes*.dex 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
c5=$(grep -a -c '贴吧签到助手 自动签到运行中' classes*.dex 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
c6=$(grep -a -c '贴吧签到助手 自动签到保活服务' classes*.dex 2>/dev/null | awk -F: '{s+=$2} END{print s+0}')
c7=$(grep -a -c '贴吧签到助手' resources.arsc 2>/dev/null || true)
echo "DEX_startNativeLogin=$DEX_HITS"
echo "DEX_native_detected=$c2"
echo "DEX_keepalive=$c3"
echo "DEX_clear_login_session=$c4"
echo "DEX_service_title_cn=$c5"
echo "DEX_service_desc_cn=$c6"
echo "ARSC_app_name_cn=${c7:-0}"

# private.tar must NOT contain the removed self-drawn login engine / old names.
tar -tzf assets/private.tar > listing.txt
BAD_LIST=$(grep -c 'baidulogin' listing.txt || true)
echo "PRIVATE_baidulogin=$BAD_LIST"

tar -xzf assets/private.tar main.pyc signer.pyc autosign.pyc weblogin.pyc icon.png 2>/dev/null || true
M1=$(grep -a -c '贴吧签到助手' main.pyc 2>/dev/null || true)
M2=$(grep -a -c 'aiotieba.cc/tutorial/start' main.pyc 2>/dev/null || true)
M3=$(grep -a -c '请勿向任何人透露' main.pyc 2>/dev/null || true)
M4=$(grep -a -c 'Android Keystore AES-GCM 加密' main.pyc 2>/dev/null || true)
M5=$(grep -a -c 'TiebaLite' main.pyc 2>/dev/null || true)
M6=$(grep -a -c 'account_ids' main.pyc 2>/dev/null || true)
M7=$(grep -a -c 'acct_cb' main.pyc 2>/dev/null || true)
S1=$(grep -a -c 'start_multi' signer.pyc 2>/dev/null || true)
S2=$(grep -a -c 'account_total' signer.pyc 2>/dev/null || true)
A1=$(grep -a -c '贴吧签到助手 自动签到失败' autosign.pyc 2>/dev/null || true)
W1=$(grep -a -c 'wappass.baidu.com/passport' weblogin.pyc 2>/dev/null || true)
echo "PYC_main_cn_name=$M1"; echo "PYC_tutorial_link=$M2"
echo "PYC_account_warning=$M3"; echo "PYC_settings_encryption=$M4"
echo "PYC_main_tiebalite=$M5"; echo "PYC_main_account_ids=$M6"
echo "PYC_main_acct_cb=$M7"; echo "PYC_signer_start_multi=$S1"
echo "PYC_signer_mode_multi=$S2"; echo "PYC_autosign_cn_title=$A1"
echo "PYC_weblogin_wappass=$W1"

# Icon must be the user-provided 签到.png (compare md5 of extracted asset)
ICON_MD5_APK=$(md5sum icon.png 2>/dev/null | awk '{print toupper($1)}')
ICON_MD5_SRC=$(md5sum "$ICON_SRC" 2>/dev/null | awk '{print toupper($1)}')
echo "ICON_MD5_APK=$ICON_MD5_APK"
echo "ICON_MD5_SRC=$ICON_MD5_SRC"

# manifest must still carry the keepalive service
MANIFEST_HITS=$(unzip -p "$APK" AndroidManifest.xml 2>/dev/null | iconv -f UTF-16LE -t UTF-8 2>/dev/null | grep -c 'TiebaSignKeepAliveService' || true)
echo "MANIFEST_keepalive_service=${MANIFEST_HITS:-0}"

# BDUSS leak scan on the whole APK (count only, never print content)
BDUSS_HITS=$(unzip -p "$APK" | grep -a -c -E "$PAT" 2>/dev/null || true)
echo "BDUSS_HITS=${BDUSS_HITS:-0}"

if [ -d "$(dirname "$DST")" ]; then
  cp -f "$APK" "$DST"
  DST_MD5=$(md5sum "$DST" | awk '{print toupper($1)}')
  echo "DST_MD5=$DST_MD5"
else
  echo "DST DIR MISSING"
fi

if [ "$DEX_HITS" -ge 1 ] && [ "$c2" -ge 1 ] && [ "$c3" -ge 1 ] \
   && [ "$c4" -ge 1 ] && [ "$c5" -ge 1 ] && [ "$c6" -ge 1 ] \
   && [ "$BAD_LIST" -eq 0 ] && [ "$M1" -ge 1 ] && [ "$M2" -ge 1 ] \
   && [ "$M3" -ge 1 ] && [ "$M4" -ge 1 ] && [ "$M5" -eq 0 ] \
   && [ "$M6" -ge 1 ] && [ "$M7" -ge 1 ] && [ "$S1" -ge 1 ] \
   && [ "$S2" -ge 1 ] && [ "$A1" -ge 1 ] && [ "$W1" -ge 1 ] \
   && [ "$ICON_MD5_APK" = "$ICON_MD5_SRC" ] \
   && [ "${MANIFEST_HITS:-0}" -ge 1 ] && [ "${BDUSS_HITS:-1}" -eq 0 ]; then
  echo "VERIFY_030_OK"
else
  echo "VERIFY_030_FAIL"
  exit 2
fi
