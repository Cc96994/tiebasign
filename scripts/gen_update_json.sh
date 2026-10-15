#!/usr/bin/env bash
# gen_update_json.sh - 生成 release/update.json（发布时要一起上传的那个文件）。
#
# 为什么需要它：App 里的版本比较**用数字 versionCode，不比 "0.3.0"/"0.4.0"
# 这样的字符串**。而 GitHub/Gitee 的发行版信息里只有 tag，没有 versionCode，
# 所以发布端要把实测出来的 versionCode + sha256 写进一个小 JSON，App 取它。
# 取不到 update.json 时 App 会退回"按 tag 的数字比较、并用 assets 的 digest
# 当校验值"，但那样一旦 tag 打错就会让用户去装更旧的包 —— 所以正常发布流程
# 应该永远带上这个文件。
#
# 值是**从 APK 实测**的（aapt2 + sha256sum），不是从版本号推算的，并且会核对
# 包名与 versionName 和 spec 一致才写文件：描述一个不存在的包比不描述更糟。
#
# 用法（WSL 里跑）：
#   bash scripts/gen_update_json.sh                      # 发布目录里那个 release APK
#   bash scripts/gen_update_json.sh <apk>                # 指定 APK
#   bash scripts/gen_update_json.sh <apk> --notes "..."  # 附更新说明
#   bash scripts/gen_update_json.sh <apk> --notes-file CHANGELOG.md
#   bash scripts/gen_update_json.sh <apk> --out /tmp/u.json
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ_WIN="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
SDK="${ANDROID_SDK_ROOT:-$HOME/.buildozer/android/platform/android-sdk}"

# shellcheck disable=SC1091
source "$SCRIPT_DIR/app_identity.sh" || exit 2

APK=""
NOTES=""
NOTES_FILE=""
OUT=""
while [ $# -gt 0 ]; do
    case "$1" in
        --notes)      NOTES="${2:-}"; shift 2 ;;
        --notes-file) NOTES_FILE="${2:-}"; shift 2 ;;
        --out)        OUT="${2:-}"; shift 2 ;;
        -h|--help)    sed -n '2,26p' "$0"; exit 0 ;;
        -*)           echo "unknown option: $1" >&2; exit 2 ;;
        *)            APK="$1"; shift ;;
    esac
done

DEFAULT_APK="$PROJ_WIN/release/tieba-sign-assistant-$APP_VERSION-arm64-v8a-release.apk"
APK="${APK:-$DEFAULT_APK}"
[ -f "$APK" ] || { echo "APK not found: $APK" >&2; exit 2; }
OUT="${OUT:-$PROJ_WIN/release/update.json}"

BT="$(ls -d "$SDK"/build-tools/*/ 2>/dev/null | sort -V | tail -1)"; BT="${BT%/}"
AAPT2="$BT/aapt2"
[ -x "$AAPT2" ] || AAPT2="$(ls "$SDK"/build-tools/*/aapt2 2>/dev/null | sort -V | tail -1)"
[ -n "$AAPT2" ] && [ -x "$AAPT2" ] || { echo "aapt2 not found under $SDK" >&2; exit 2; }

read -r ID CODE VNAME <<EOF
$("$AAPT2" dump badging "$APK" 2>/dev/null | head -1 | sed -n "s/^package: name='\([^']*\)'.*versionCode='\([0-9]*\)'.*versionName='\([^']*\)'.*/\1 \2 \3/p")
EOF

echo "== measured from $APK"
echo "   applicationId=$ID  versionCode=$CODE  versionName=$VNAME"
[ -n "$ID" ] && [ -n "$CODE" ] && [ -n "$VNAME" ] || { echo "could not read the APK identity" >&2; exit 2; }
[ "$ID" = "$APP_PACKAGE" ] || { echo "package mismatch: APK '$ID' != spec '$APP_PACKAGE'" >&2; exit 2; }
[ "$VNAME" = "$APP_VERSION" ] || { echo "version mismatch: APK '$VNAME' != spec '$APP_VERSION' - 该重新构建了" >&2; exit 2; }
if [ "$CODE" -le "$APP_PREV_VERSION_CODE" ]; then
    echo "versionCode $CODE is not greater than the published $APP_PREV_VERSION_CODE" >&2
    echo "  -> 这样的包发出去，老用户点更新只会拿到 INSTALL_FAILED_VERSION_DOWNGRADE" >&2
    exit 2
fi

SHA="$(sha256sum "$APK" | awk '{print $1}')"
SIZE="$(stat -c %s "$APK")"
NAME="$(basename -- "$APK")"

if [ -n "$NOTES_FILE" ]; then
    [ -f "$NOTES_FILE" ] || { echo "notes file not found: $NOTES_FILE" >&2; exit 2; }
    NOTES="$(cat "$NOTES_FILE")"
fi
[ -n "$NOTES" ] || NOTES="v$APP_VERSION"

mkdir -p "$(dirname -- "$OUT")"
NOTES="$NOTES" SHA="$SHA" SIZE="$SIZE" NAME="$NAME" CODE="$CODE" \
VNAME="$VNAME" PREV="$APP_PREV_VERSION" OUT="$OUT" \
python3 - <<'PY'
import json
import os

notes = os.environ["NOTES"]
info = {
    "version": os.environ["VNAME"],
    "versionCode": int(os.environ["CODE"]),
    "apk": os.environ["NAME"],
    "sha256": os.environ["SHA"],
    "size": int(os.environ["SIZE"]),
    # minVersionCode 暂时恒为 0（= 谁都能升）。留着这个字段是为了以后某版
    # 改了数据结构、必须禁止从很旧的版本直接升级时，能只靠发布端表达。
    "minVersionCode": 0,
    "notes": notes[:1500],
    "previous": os.environ["PREV"],
}
path = os.environ["OUT"]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(info, fh, ensure_ascii=False, indent=2)
    fh.write("\n")
os.replace(tmp, path)
print(json.dumps(info, ensure_ascii=False, indent=2))
PY

echo
echo "wrote $OUT"
echo "发布时把这个文件和 $NAME 一起挂到 GitHub 与 Gitee 的同一个 tag 下："
echo "  GitHub : https://github.com/Cc96994/tiebasign/releases/new"
echo "  Gitee  : https://gitee.com/Cc96994/tiebasign/releases/new"
echo "  要上传的资产： $NAME"
echo "                 update.json"
echo "                 SHA256SUMS"
