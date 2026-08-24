#!/usr/bin/env bash
# BDUSS leak scan for the TiebaSign project.
# Prints ONLY file paths and hit counts, never the matched content.
set -uo pipefail

ROOT="/mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android"
WSLPROJ="/home/s7909/tieba_sign_android"
BUILDER="/home/s7909/.buildozer"
P4A="/home/s7909/p4a-develop"
PAT='BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}'

count=0
scan_tree() {
  local label="$1"; shift
  echo "== scan $label =="
  while IFS= read -r -d '' f; do
    hits=$(grep -a -c -E "$PAT" "$f" 2>/dev/null || true)
    if [ "${hits:-0}" -gt 0 ]; then
      echo "HIT $hits  $f"
      count=$((count + hits))
    fi
  done < <(find "$@" -type f -print0 2>/dev/null)
}

scan_apks() {
  echo "== scan APKs (binary) =="
  while IFS= read -r apk; do
    hits=$(unzip -p "$apk" 2>/dev/null | grep -a -c -E "$PAT" || true)
    if [ "${hits:-0}" -gt 0 ]; then
      echo "HIT $hits  $apk"
      count=$((count + hits))
    else
      echo "clean $apk"
    fi
  done < <(find "$@" -name '*.apk' -print 2>/dev/null)
}

scan_tree "Windows source tree" "$ROOT" -not -path '*/vendor/*' -not -path '*/.buildozer/*' -not -name '*.png' -not -name '*.tar.gz'
scan_tree "WSL project tree" "$WSLPROJ" -size -100M -not -path '*/vendor/*' -not -path '*/.buildozer/*' -not -name '*.tar.gz'
# ~/.buildozer contains android-sdk/android-ndk (tens of thousands of binaries);
# only dists, python-installs and p4a source can carry project content.
scan_tree "WSL dists" "$BUILDER/android/platform/build-arm64-v8a/dists" -size -100M
scan_tree "WSL python-installs" "$BUILDER/android/platform/build-arm64-v8a/build/python-installs" -size -5M
scan_tree "WSL p4a source" "$P4A" -size -5M
scan_apks "$WSLPROJ/bin" "$WSLPROJ/bin_probe" "$WSLPROJ/bin_m21" "$WSLPROJ/bin_m22" "$WSLPROJ/bin_ksstage"

echo "== total BDUSS-pattern hits: $count =="
if [ "$count" -gt 0 ]; then
  echo "RESULT: FAIL (redact/fix before release)"
  exit 1
fi
echo "RESULT: CLEAN"
