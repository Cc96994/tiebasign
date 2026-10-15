#!/usr/bin/env bash
# verify_release.sh - verify a signed release APK, then publish it safely.
#
# v2, rewritten 2026-10-15. Read this before "simplifying" it back:
#   * v1 spelled the version "0.3.0" out twice - in the APK path it verified and
#     in the release filename it wrote. After a version bump it would have
#     verified a STALE apk and still printed VERIFY_RELEASE_OK. That is the same
#     bug class that once made check_apk_manifest.py pass on nothing (see
#     scripts/app_identity.sh for that history).
#   * v1 copied the fresh APK over release/tieba-sign-assistant-0.3.0-....apk -
#     i.e. over the PUBLISHED asset - and rewrote SHA256SUMS down to one line.
#     Published bytes are now never overwritten: a conflict fails hard.
#   * v1 never ran the upgrade-compatibility gate.
#   * every input is asserted now. A missing thing fails loudly instead of
#     quietly skipping its check, because a check that cannot run must not
#     report OK.
#   * SHA256SUMS is only touched when the APK it describes is really in place.
#     (Found by a negative test: the first version of this rewrite refused to
#     overwrite the APK but rewrote the checksum file anyway, leaving a
#     published checksum that did not match the published APK.)
#   * since 0.4.0 it also runs the offline self-tests and asserts the update
#     plumbing is really in the APK (install entry methods in the dex, the
#     ApkProvider authority in the manifest, the two new permissions), because
#     those live in the p4a base OUTSIDE the repo and can vanish silently.
#
# Usage (run inside WSL):
#   bash scripts/verify_release.sh                       # default release APK of the spec version
#   bash scripts/verify_release.sh <apk>                 # verify one specific APK
#   bash scripts/verify_release.sh [<apk>] --no-publish  # verify + refresh bin_m22, never touch release/
#   bash scripts/verify_release.sh [<apk>] --verify-only # write nothing at all
#   bash scripts/verify_release.sh [<apk>] --no-tests    # skip the offline self-tests
#
# Exit: 0 = VERIFY_RELEASE_OK, 1 = a check failed, 2 = bad usage / environment.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# WSL-visible project root, self-locating on purpose. The hardcoded
# C:\Users\s7909\Desktop\... form this replaced stopped existing when the tree
# moved to G:, and every script carrying it either died or silently did nothing.
PROJ_WIN="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROJ_WSL="${TIEBA_PROJ_WSL:-/home/s7909/tieba_sign_android}"
SDK="${ANDROID_SDK_ROOT:-$HOME/.buildozer/android/platform/android-sdk}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/app_identity.sh" || exit 2

# ---- arguments -------------------------------------------------------------
APK=""
MODE="publish"   # publish | no-publish | verify-only
RUN_TESTS=1
for arg in "$@"; do
    case "$arg" in
        --no-publish)  MODE="no-publish" ;;
        --verify-only) MODE="verify-only" ;;
        --no-tests)    RUN_TESTS=0 ;;
        -h|--help)     sed -n '2,33p' "$0"; exit 0 ;;
        -*)            echo "unknown option: $arg" >&2; exit 2 ;;
        *)             if [ -n "$APK" ]; then echo "unexpected extra argument: $arg" >&2; exit 2; fi
                       APK="$arg" ;;
    esac
done
BUILT="$PROJ_WSL/bin_m22/$APP_NAME-$APP_VERSION-arm64-v8a-release.apk"
APK="${APK:-$BUILT}"

FAIL=0
fail() { echo "FAIL  $*"; FAIL=1; }
ok()   { echo "ok    $*"; }
note() { echo "note  $*"; }

echo "== identity (from $(basename -- "$APP_SPEC"))"
echo "   title   : $APP_TITLE"
echo "   package : $APP_PACKAGE"
echo "   version : $APP_VERSION   (previous published: v$APP_PREV_VERSION / code $APP_PREV_VERSION_CODE)"
echo "   apk     : $APK"
echo "   mode    : $MODE"

# ---- 0. inputs must exist --------------------------------------------------
if [ ! -f "$APK" ]; then
    echo "APK MISSING: $APK"
    echo "  expected the release APK of spec version $APP_VERSION."
    if [ -d "$PROJ_WSL/bin_m22" ]; then
        echo "  release APKs present in $PROJ_WSL/bin_m22:"
        ls -1 "$PROJ_WSL/bin_m22"/*release*.apk 2>/dev/null | sed 's/^/    /' || echo "    (none)"
        echo "  -> run scripts/wsl_release_build.sh first, or pass the APK path explicitly."
    else
        echo "  and $PROJ_WSL/bin_m22 does not exist - has the release build ever run?"
    fi
    exit 2
fi
[ -f "$PROJ_WIN/app/main.py" ] || { echo "missing $PROJ_WIN/app/main.py (TIEBA_PROJ_WIN wrong?)"; exit 2; }
[ -f "$PROJ_WIN/app/icon.png" ] || { echo "missing $PROJ_WIN/app/icon.png (TIEBA_PROJ_WIN wrong?)"; exit 2; }

BT="$(ls -d "$SDK"/build-tools/*/ 2>/dev/null | sort -V | tail -1)"; BT="${BT%/}"
if [ -z "$BT" ] || [ ! -x "$BT/apksigner" ]; then
    echo "apksigner not found under $SDK (set ANDROID_SDK_ROOT)"; exit 2
fi
AAPT2="$BT/aapt2"
[ -x "$AAPT2" ] || AAPT2="$(ls "$SDK"/build-tools/*/aapt2 2>/dev/null | sort -V | tail -1)"
if [ -z "$AAPT2" ] || [ ! -x "$AAPT2" ]; then echo "aapt2 not found under $SDK"; exit 2; fi
echo "   build-tools: $BT"

TMPD="$(mktemp -d)"
trap 'rm -rf "$TMPD"' EXIT

echo "== size / hashes"
ls -l "$APK" | sed 's/^/   /'
SHA="$(sha256sum "$APK" | awk '{print $1}')"
echo "   sha256 : $SHA"
echo "   md5    : $(md5sum "$APK" | awk '{print $1}')"
echo "   bytes  : $(stat -c %s "$APK")"

# ---- 0.5 offline self-tests (seconds, no network) --------------------------
# 放在最前面：页面渲染炸了、状态机坏了，就不该等走完一堆 APK 检查才发现。
if [ "$RUN_TESTS" = 1 ]; then
    echo "== offline self-tests"
    if bash "$SCRIPT_DIR/run_tests.sh" > "$TMPD/tests.log" 2>&1; then
        # The Python suites print "PASS <name>"; the two bash suites (build-spec
        # swap, p4a base reconstruction) print "ok    <name>". Counting only
        # ^PASS silently under-reported the gate by ~40 assertions.
        ok "offline self-tests passed ($(grep -c -E '^(PASS|ok)' "$TMPD/tests.log" || true) checks)"
    else
        fail "offline self-tests FAILED"
        grep -a -E '^FAIL|^!!|failed:|FAILED' "$TMPD/tests.log" | head -20 | sed 's/^/      /'
    fi
else
    note "offline self-tests skipped (--no-tests)"
fi

# ---- 1. the APK must really be the version the spec claims -----------------
echo "== version identity"
read -r ID CODE VNAME <<EOF
$("$AAPT2" dump badging "$APK" 2>/dev/null | head -1 | sed -n "s/^package: name='\([^']*\)'.*versionCode='\([0-9]*\)'.*versionName='\([^']*\)'.*/\1 \2 \3/p")
EOF
echo "   applicationId=$ID  versionCode=$CODE  versionName=$VNAME"
[ -n "$ID" ] || fail "could not read the applicationId out of $APK"
[ "$ID" = "$APP_PACKAGE" ] || fail "APK applicationId is '$ID', the spec says '$APP_PACKAGE'"
[ -n "$VNAME" ] || fail "could not read versionName out of $APK"
[ "$VNAME" = "$APP_VERSION" ] || fail "APK versionName is '$VNAME' but the spec says '$APP_VERSION' - rebuild after the bump?"

# The user-visible version string is a SECOND copy of the same fact:
# app/main.py renders "贴吧签到助手 <version>". Nothing generates it, so a bump
# that forgets it ships a wrong version to users. Reported, never auto-edited.
UI_VER="$(grep -a -o -- "$APP_TITLE [0-9][0-9.]*" "$PROJ_WIN/app/main.py" 2>/dev/null | head -1 | sed "s/^$APP_TITLE //")"
if [ -z "$UI_VER" ]; then
    note "no \"$APP_TITLE <version>\" string found in app/main.py - check the UI version by hand"
elif [ "$UI_VER" != "$APP_VERSION" ]; then
    fail "app/main.py shows version $UI_VER but the spec says $APP_VERSION - the version users see must match"
    grep -n -- "$APP_TITLE $UI_VER" "$PROJ_WIN/app/main.py" | sed 's/^/      /'
fi
[ -n "$UI_VER" ] && [ "$UI_VER" = "$APP_VERSION" ] && ok "source UI version matches the spec ($UI_VER)"

# app/updater.py carries its OWN copy of version / versionCode / package and uses
# them to answer "is this release newer?" and "is this download really ours?".
# A bump that forgets it ships an app that either ignores the new release (its
# versionCode is too low) or refuses to install it (package/signer mismatch), so
# all three are asserted here instead of being trusted to memory.
echo "== updater identity (app/updater.py)"
UP_VER="$(grep -a -o -E '^_APP_VERSION = "[^"]+"' "$PROJ_WIN/app/updater.py" | head -1 | sed 's/.*"\(.*\)"/\1/')"
UP_CODE="$(grep -a -o -E '^_APP_VERSION_CODE = [0-9]+' "$PROJ_WIN/app/updater.py" | head -1 | sed 's/.*= //')"
UP_PKG="$(grep -a -o -E '^_PACKAGE = "[^"]+"' "$PROJ_WIN/app/updater.py" | head -1 | sed 's/.*"\(.*\)"/\1/')"
echo "   version=$UP_VER  versionCode=$UP_CODE  package=$UP_PKG"
[ -n "$UP_VER" ] || fail "could not read _APP_VERSION out of app/updater.py"
[ "$UP_VER" = "$APP_VERSION" ] || fail "app/updater.py says version '$UP_VER', the spec says '$APP_VERSION'"
[ -n "$UP_CODE" ] || fail "could not read _APP_VERSION_CODE out of app/updater.py"
[ "$UP_CODE" = "$CODE" ] || fail "app/updater.py says versionCode $UP_CODE, the APK says $CODE"
[ -n "$UP_PKG" ] || fail "could not read _PACKAGE out of app/updater.py"
[ "$UP_PKG" = "$APP_PACKAGE" ] || fail "app/updater.py says package '$UP_PKG', the spec says '$APP_PACKAGE'"
if [ "$UP_VER" = "$APP_VERSION" ] && [ "$UP_CODE" = "$CODE" ] && [ "$UP_PKG" = "$APP_PACKAGE" ]; then
    ok "updater identity matches the APK ($UP_VER / $UP_CODE / $UP_PKG)"
fi

# ---- 2. signature: same key as the published version, or nobody can upgrade -
echo "== signature"
SIGN_OUT="$("$BT/apksigner" verify --print-certs "$APK" 2>&1)"; SIGN_EXIT=$?
VERBOSE="$("$BT/apksigner" verify --verbose "$APK" 2>&1)"
SIGNER="$(printf '%s\n' "$SIGN_OUT" | grep -a -o -E 'SHA-256 digest: [0-9a-fA-F:]+' | head -1 \
          | sed 's/.*digest: //' | tr -d ':' | tr 'A-F' 'a-f')"
V1=$(printf '%s\n' "$VERBOSE" | grep -a -c 'Verified using v1 scheme (JAR signing): true' || true)
V2=$(printf '%s\n' "$VERBOSE" | grep -a -c 'Verified using v2 scheme (APK Signature Scheme v2): true' || true)
echo "   verified=$SIGN_EXIT  v1=$V1  v2=$V2"
echo "   signer SHA-256 : ${SIGNER:-<none>}"
echo "   same as v$APP_PREV_VERSION : $APP_PREV_SIGNER_SHA256"
[ "$SIGN_EXIT" = 0 ] || fail "apksigner verify failed (exit $SIGN_EXIT)"
[ "$V1" = 1 ] || fail "no v1 (JAR) signature"
[ "$V2" = 1 ] || fail "no v2 signature"
if [ "$SIGNER" != "$APP_PREV_SIGNER_SHA256" ]; then
    fail "signer is NOT the key that signed v$APP_PREV_VERSION - existing users cannot upgrade (INSTALL_FAILED_UPDATE_INCOMPATIBLE)"
    note "check that sign/signing.env points at sign/$APP_KEYSTORE_NAME"
else
    ok "signer matches the published v$APP_PREV_VERSION"
fi

# ---- 3. the code really is inside the APK ---------------------------------
echo "== dex markers"
unzip -q -o "$APK" 'classes*.dex' -d "$TMPD" 2>/dev/null || fail "could not extract classes*.dex"
DEXES=("$TMPD"/classes*.dex)
[ "${#DEXES[@]}" -gt 0 ] || [ -f "${DEXES[0]:-}" ] || fail "no classes*.dex in the APK"
require_in_dex() {  # <marker> <min hits>
    local m="$1" min="$2" n=0
    for d in "${DEXES[@]}"; do
        [ -f "$d" ] || continue
        n=$((n + $(grep -a -c -- "$m" "$d" 2>/dev/null || true)))
    done
    if [ "$n" -ge "$min" ]; then ok "dex contains $m (x$n)"
    else fail "dex is missing $m (found $n, need >= $min)"; fi
}
require_in_dex "startNativeLogin" 1
require_in_dex "native_detected" 1
require_in_dex "clearLoginSession" 1
require_in_dex "$APP_SERVICE_CLASS" 1
# 0.4.0 的"检查更新/安装"能力同样住在 p4a 底座里（仓库之外）。底座被换掉、
# 或者补丁没打进 dist，这些方法就会静默消失 —— Python 侧那时只会打印一句
# "PythonActivity.installApk 未找到"，用户点安装时才失败。所以钉在这里。
require_in_dex "installApk" 1
require_in_dex "inspectApk" 1
require_in_dex "isOnWifi" 1
require_in_dex "notifyUpdateReady" 1
require_in_dex "ApkProvider" 1
for d in "${DEXES[@]}"; do
    [ -f "$d" ] || continue
    printf '   %s: %s=%s\n' "$(basename -- "$d")" "$APP_TITLE" "$(grep -a -c -- "$APP_TITLE" "$d" 2>/dev/null || true)"
done

echo "== private.tar (python payload)"
if unzip -p "$APK" assets/private.tar > "$TMPD/private.tar" 2>/dev/null && [ -s "$TMPD/private.tar" ]; then
    mkdir -p "$TMPD/pt"
    tar -xzf "$TMPD/private.tar" -C "$TMPD/pt" 2>/dev/null || fail "assets/private.tar is not a readable tar.gz"
else
    fail "assets/private.tar is missing or empty"
fi
PYCS="$TMPD/pt"
if [ -f "$PYCS/main.pyc" ]; then ok "private.tar contains main.pyc"
else fail "private.tar has no main.pyc"; fi
if [ -f "$PYCS/signer.pyc" ]; then ok "private.tar contains signer.pyc"
else fail "private.tar has no signer.pyc"; fi
BAIDU="$(find "$PYCS" -iname 'baidulogin*' 2>/dev/null | wc -l)"
if [ "$BAIDU" = 0 ]; then ok "no baidulogin* in private.tar (removed in 0.2.0)"
else fail "baidulogin* is back in private.tar (x$BAIDU)"; fi
PYC_UI="$(grep -a -o -- "$APP_TITLE [0-9][0-9.]*" "$PYCS/main.pyc" 2>/dev/null | head -1 | sed "s/^$APP_TITLE //")"
echo "   UI version compiled into main.pyc: ${PYC_UI:-<not found>}"
if [ -n "$PYC_UI" ] && [ "$PYC_UI" != "$APP_VERSION" ]; then
    fail "the compiled UI says $PYC_UI, the spec says $APP_VERSION"
fi
if [ -f "$PYCS/main.pyc" ]; then
    printf '   marker counts: acct_cb=%s account_ids=%s TiebaLite=%s\n' \
        "$(grep -a -c 'acct_cb' "$PYCS/main.pyc" 2>/dev/null || true)" \
        "$(grep -a -c 'account_ids' "$PYCS/main.pyc" 2>/dev/null || true)" \
        "$(grep -a -c 'TiebaLite' "$PYCS/main.pyc" 2>/dev/null || true)"
fi

echo "== manifest"
MAN="$(unzip -p "$APK" AndroidManifest.xml 2>/dev/null | iconv -f utf-16le -t utf-8 2>/dev/null | tr -d '\0')"
if [ -z "$MAN" ]; then
    fail "could not read AndroidManifest.xml out of the APK"
else
    SVC=$(printf '%s' "$MAN" | grep -a -c -- "$APP_SERVICE_CLASS" || true)
    DBG=$(printf '%s' "$MAN" | grep -a -c 'android:debuggable="true"' || true)
    # 更新链路的三个声明：provider（把下载好的 APK 交给系统安装器读，Android 7
    # 起禁止 file://）、装未知应用的权限、以及"是不是 Wi-Fi"要用的网络状态权限。
    PROV=$(printf '%s' "$MAN" | grep -a -c 'apkprovider' || true)
    P_REQ=$(printf '%s' "$MAN" | grep -a -c 'REQUEST_INSTALL_PACKAGES' || true)
    P_NET=$(printf '%s' "$MAN" | grep -a -c 'ACCESS_NETWORK_STATE' || true)
    echo "   service=$SVC  debuggable=$DBG  provider=$PROV  install_perm=$P_REQ  netstate_perm=$P_NET"
    [ "$SVC" -ge 1 ] || fail "manifest does not declare $APP_SERVICE_CLASS"
    [ "$DBG" = 0 ] || fail "release APK is marked debuggable=true"
    [ "$PROV" -ge 1 ] || fail "manifest does not declare the ApkProvider authority (install cannot work)"
    [ "$P_REQ" -ge 1 ] || fail "manifest lacks REQUEST_INSTALL_PACKAGES (the installer UI cannot be opened)"
    [ "$P_NET" -ge 1 ] || fail "manifest lacks ACCESS_NETWORK_STATE (the Wi-Fi-only rule cannot be evaluated)"
fi

echo "== icon"
ICON_ENTRY="$("$AAPT2" dump badging "$APK" 2>/dev/null | sed -n "s/^application-icon-[0-9]*:'\([^']*\)'.*/\1/p" | tail -1)"
[ -n "$ICON_ENTRY" ] || ICON_ENTRY="$("$AAPT2" dump badging "$APK" 2>/dev/null | sed -n "s/^application:.*icon='\([^']*\)'.*/\1/p" | head -1)"
DIMS=""
if [ -n "$ICON_ENTRY" ]; then
    DIMS="$(unzip -p "$APK" "$ICON_ENTRY" 2>/dev/null | file - 2>/dev/null | grep -a -o -E '[0-9]+ x [0-9]+' | head -1)"
fi
echo "   apk entry: ${ICON_ENTRY:-<none>}   dims: ${DIMS:-<unknown>}"
echo "   src md5  : $(md5sum "$PROJ_WIN/app/icon.png" | awk '{print $1}')  (aapt2 recompresses, md5 differs by design)"
if [ -z "$ICON_ENTRY" ]; then fail "no application icon declared in the APK"
elif [ -z "$DIMS" ]; then fail "could not read the icon dimensions from $ICON_ENTRY"
else
    IW="${DIMS% x *}"; IH="${DIMS#* x }"
    if [ "$IW" = "$IH" ] && [ "$IW" -ge 192 ]; then ok "icon is square and >= 192px ($DIMS)"
    else fail "icon is not a square of at least 192px ($DIMS)"; fi
fi

# ---- 4. nothing secret ships ----------------------------------------------
echo "== BDUSS scan inside the APK"
HITS="$(unzip -p "$APK" 2>/dev/null | grep -a -c -E 'BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}' || true)"
if [ "${HITS:-0}" = 0 ]; then ok "no BDUSS pattern inside the APK"
else fail "BDUSS pattern found inside the APK (x$HITS)"; fi

# ---- 5. the gate that protects existing users -----------------------------
echo "== upgrade compatibility"
REF="$PROJ_WIN/release/tieba-sign-assistant-$APP_PREV_VERSION-arm64-v8a-release.apk"
if [ -f "$REF" ]; then
    bash "$SCRIPT_DIR/check_upgrade_compat.sh" "$APK" "$REF" | sed 's/^/   /' \
        || fail "upgrade compatibility failed against $REF"
else
    note "published v$APP_PREV_VERSION APK not found at $REF - checking the recorded facts only"
    bash "$SCRIPT_DIR/check_upgrade_compat.sh" "$APK" | sed 's/^/   /' \
        || fail "upgrade compatibility failed"
fi

# ---- 6. deliver / publish --------------------------------------------------
if [ "$MODE" != "verify-only" ]; then
    echo "== copy into the Windows tree"
    mkdir -p "$PROJ_WIN/bin_m22"
    DST="$PROJ_WIN/bin_m22/$(basename -- "$APK")"
    if [ "$APK" != "$DST" ]; then cp -f "$APK" "$DST"; fi
    if cmp -s "$APK" "$DST"; then ok "bin_m22/$(basename -- "$DST") is identical"
    else fail "copy mismatch at $DST"; fi
fi

if [ "$MODE" = "publish" ]; then
    echo "== publish into release/ (published bytes are never overwritten)"
    mkdir -p "$PROJ_WIN/release"
    REL_NAME="tieba-sign-assistant-$APP_VERSION-arm64-v8a-release.apk"
    REL="$PROJ_WIN/release/$REL_NAME"
    PUBLISHED=0
    if [ -f "$REL" ]; then
        OLD_SHA="$(sha256sum "$REL" | awk '{print $1}')"
        if [ "$OLD_SHA" = "$SHA" ]; then
            ok "release/$REL_NAME already exists with identical bytes"
            PUBLISHED=1
        else
            fail "release/$REL_NAME already exists with DIFFERENT bytes - refusing to overwrite a published asset"
            note "existing sha256 $OLD_SHA"
            note "new      sha256 $SHA"
            note "if v$APP_VERSION is already published, bump the spec version instead of re-publishing it"
        fi
    elif cp -f "$APK" "$REL" && cmp -s "$APK" "$REL"; then
        ok "wrote release/$REL_NAME"
        PUBLISHED=1
    else
        fail "could not publish release/$REL_NAME"
    fi

    # SHA256SUMS is what users check their download against, so it may only be
    # written once the file it describes is really in place - and it is summed
    # from release/ (not from $APK), because release/ holds the authoritative
    # bytes. A refused publish must leave SHA256SUMS byte-for-byte as it was;
    # an earlier version of this script updated it anyway and published a
    # checksum that did not match the published APK.
    if [ "$PUBLISHED" = 1 ]; then
        REL_SHA="$(sha256sum "$REL" | awk '{print $1}')"
        if [ "$REL_SHA" != "$SHA" ]; then
            fail "release/$REL_NAME hashes to $REL_SHA but the verified APK hashes to $SHA"
        else
            SUMS="$PROJ_WIN/release/SHA256SUMS"
            [ -f "$SUMS" ] || : > "$SUMS"
            grep -v -F "  $REL_NAME" "$SUMS" > "$TMPD/SHA256SUMS" 2>/dev/null || : > "$TMPD/SHA256SUMS"
            printf '%s  %s\n' "$REL_SHA" "$REL_NAME" >> "$TMPD/SHA256SUMS"
            sort -k2 "$TMPD/SHA256SUMS" -o "$TMPD/SHA256SUMS"
            if cp -f "$TMPD/SHA256SUMS" "$SUMS"; then
                echo "   release/SHA256SUMS now holds $(wc -l < "$SUMS") entr(y|ies):"
                sed 's/^/     /' "$SUMS"
            else
                fail "could not update release/SHA256SUMS"
            fi
        fi
    else
        note "release/SHA256SUMS left untouched"
    fi

    # update.json 是 App 判断"要不要更新 / 这个包是不是我们的"时最权威的一份
    # 数据（versionCode + sha256 都是实测的）。只在验证全过、APK 真的在
    # release/ 里时才生成 —— 描述一个没通过验证的包比不描述更危险。
    if [ "$PUBLISHED" = 1 ] && [ "$FAIL" = 0 ]; then
        echo "== update.json"
        if bash "$SCRIPT_DIR/gen_update_json.sh" "$REL"; then
            ok "release/update.json describes $REL_NAME"
        else
            fail "could not generate release/update.json"
        fi
    elif [ "$PUBLISHED" = 1 ]; then
        note "update.json not generated: verification already failed"
    fi
fi

echo
if [ "$FAIL" = 0 ]; then echo "VERIFY_RELEASE_OK"; else echo "VERIFY_RELEASE_FAILED"; fi
exit "$FAIL"
