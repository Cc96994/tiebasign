#!/usr/bin/env bash
# leak_scan.sh - BDUSS / credential leak scan for the TiebaSign project.
# Prints ONLY file paths, sizes and hit counts. Never the matched content.
#
# v2, rewritten 2026-10-15. What was wrong with v1:
#   * it hardcoded its Windows scan root as
#     /mnt/c/Users/s7909/Desktop/学习/aiotieba/tieba_sign_android, which stopped
#     existing when the tree moved to G:. find(1) was silenced with 2>/dev/null,
#     so the "Windows source tree" scan walked ZERO files - and the script still
#     printed RESULT: CLEAN. A scan that cannot scan must not report clean, so:
#       - every required root is asserted to exist, and the number of files it
#         actually walked is printed, so "scanned nothing" is visible;
#       - a root that yields 0 files is a hard failure;
#   * _legacy/ trees were never scanned at all (they sit outside the project);
#   * the dists / python-installs roots pointed at ~/.buildozer, which is not
#     where this project builds (it builds into <project>/.buildozer), so those
#     scans were dead too. Both layouts are discovered now.
#   * a bare credential file cannot be caught by the value pattern: measure it -
#     _legacy/tieba_sign_app_proto/BDUSS.txt is 192 chars of pure base64 with no
#     "BDUSS=" prefix, so the regex below does NOT see it. A name-based rule was
#     added for that.
#
# Usage (inside WSL):
#   bash scripts/leak_scan.sh
#   LEAK_SKIP_APKS=1 bash scripts/leak_scan.sh     # skip the (slow) APK pass
# Exit: 0 = CLEAN, 1 = attention required.
set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ_ROOT="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROJ_WSL="${TIEBA_PROJ_WSL:-/home/s7909/tieba_sign_android}"
BUILDER="${BUILDER_HOME:-$HOME/.buildozer}"
P4A="${P4A_SRC:-$HOME/p4a-develop}"
SIBLING_LEGACY="$(dirname -- "$PROJ_ROOT")/_legacy"   # outside the project on purpose
PAT='BDUSS(=|%3D)[A-Za-z0-9+/=]{16,}'
NAME_PAT='BDUSS'
LEAK_SKIP_APKS="${LEAK_SKIP_APKS:-0}"

hits_total=0
hard=0    # blocking: inside the project, or a scan that could not run
soft=0    # worth a look, but outside the project

ok()   { echo "ok    $*"; }
bad()  { echo "FAIL  $*"; hard=$((hard + 1)); }
warn() { echo "WARN  $*"; soft=$((soft + 1)); }

scan_tree() {  # <required 0|1> <label> <root> [find args...]
    local required="$1" label="$2" root="$3"; shift 3
    echo "== $label"
    if [ ! -d "$root" ]; then
        if [ "$required" = 1 ]; then bad "scan root missing: $root"
        else warn "scan root missing (outside the project): $root"; fi
        return
    fi
    local files=0 hits=0 h
    while IFS= read -r -d '' f; do
        files=$((files + 1))
        h="$(grep -a -c -E "$PAT" "$f" 2>/dev/null || true)"
        if [ "${h:-0}" -gt 0 ]; then
            echo "HIT   $h  $f"
            hits=$((hits + h)); hits_total=$((hits_total + h))
        fi
    done < <(find "$root" "$@" -type f -print0 2>/dev/null)
    echo "   files scanned: $files   hits: $hits"
    if [ "$files" -eq 0 ]; then
        bad "$label walked 0 files - the root exists but nothing matched the find filter"
    elif [ "$hits" -eq 0 ]; then ok "$label clean ($files files)"
    fi
}

scan_names() {  # <required 0|1> <label> <root>
    local required="$1" label="$2" root="$3"
    echo "== $label (filenames containing '$NAME_PAT')"
    if [ ! -d "$root" ]; then
        if [ "$required" = 1 ]; then bad "scan root missing: $root"
        else warn "scan root missing (outside the project): $root"; fi
        return
    fi
    local n=0
    while IFS= read -r -d '' f; do
        n=$((n + 1))
        if [ "$required" = 1 ]; then bad "credential-looking filename inside the project: $f"
        else warn "credential-looking filename outside the project: $f"; fi
        echo "      size=$(stat -c %s "$f" 2>/dev/null || echo '?') bytes"
    done < <(find "$root" -type f -iname "*${NAME_PAT}*" -print0 2>/dev/null)
    [ "$n" -eq 0 ] && ok "none ($label)"
}

scan_apks() {  # <required 0|1> <label> <root>
    local required="$1" label="$2" root="$3"
    echo "== $label"
    if [ "${LEAK_SKIP_APKS:-0}" = 1 ]; then echo "   skipped (LEAK_SKIP_APKS=1)"; return; fi
    if [ ! -d "$root" ]; then
        if [ "$required" = 1 ]; then bad "scan root missing: $root"
        else warn "scan root missing (outside the project): $root"; fi
        return
    fi
    local n=0 h
    while IFS= read -r apk; do
        n=$((n + 1))
        h="$(unzip -p "$apk" 2>/dev/null | grep -a -c -E "$PAT" || true)"
        if [ "${h:-0}" -gt 0 ]; then
            echo "HIT   $h  $apk"
            hits_total=$((hits_total + h))
        else
            echo "clean      $apk"
        fi
    done < <(find "$root" -name '*.apk' -print 2>/dev/null | sort)
    echo "   APKs scanned: $n"
    [ "$n" -eq 0 ] && warn "$label found no APK to scan"
}

echo "== leak_scan v2"
echo "   project root : $PROJ_ROOT"
echo "   wsl copy     : $PROJ_WSL"
echo "   sibling      : $SIBLING_LEGACY"

# ---- source trees ----------------------------------------------------------
# -size keeps the multi-hundred-MB APK pile out of this pass; scan_apks covers
# those, and grepping them twice only makes the gate slow.
scan_tree 1 "Windows project tree" "$PROJ_ROOT" \
    -size -50M -not -path '*/vendor/*' -not -path '*/.buildozer/*' \
    -not -name '*.png' -not -name '*.tar.gz' -not -name '*.apk'
scan_tree 0 "sibling _legacy tree (outside the project)" "$SIBLING_LEGACY" \
    -size -50M -not -name '*.png' -not -name '*.tar.gz' -not -name '*.apk'
scan_tree 1 "WSL project tree" "$PROJ_WSL" \
    -size -50M -not -path '*/vendor/*' -not -path '*/.buildozer/*' \
    -not -name '*.tar.gz' -not -name '*.apk'
# The buildozer platform dir is PROJECT-local here
# (<project>/.buildozer/android/platform/build-arm64-v8a) but sits under
# ~/.buildozer on other machines, and v1 hardcoded only the ~/.buildozer form -
# with find(1) silenced, so it walked 0 files and still said CLEAN. Discover
# both, and refuse to stay quiet when a build has clearly happened but no dist
# can be found (that means the discovery itself is wrong).
discover_roots() {  # <path relative to the platform dir>; prints existing dirs
    local rel="$1" base d
    for base in "$PROJ_WSL/.buildozer/android/platform" "$BUILDER/android/platform"; do
        for d in "$base"/build-*/"$rel"; do
            [ -d "$d" ] && printf '%s\n' "$d"
        done
    done
}
mapfile -t DISTS_ROOTS < <(discover_roots dists)
mapfile -t PYINST_ROOTS < <(discover_roots build/python-installs)

if [ "${#DISTS_ROOTS[@]}" -eq 0 ]; then
    if [ -d "$PROJ_WSL/.buildozer" ] || [ -d "$BUILDER/android/platform" ]; then
        bad "buildozer has been used but no dists directory was found - path discovery is wrong, refusing to report clean"
        note "looked for <project>/.buildozer/android/platform/build-*/dists and <BUILDER>/android/platform/build-*/dists"
    else
        warn "no buildozer dists yet (nothing built on this machine) - nothing to scan there"
    fi
else
    for r in "${DISTS_ROOTS[@]}"; do scan_tree 1 "dists [$r]" "$r" -size -50M; done
fi

if [ "${#PYINST_ROOTS[@]}" -eq 0 ]; then
    if [ -d "$PROJ_WSL/.buildozer" ] || [ -d "$BUILDER/android/platform" ]; then
        bad "buildozer has been used but no python-installs directory was found - refusing to report clean"
    else
        warn "no python-installs yet (nothing built on this machine) - nothing to scan there"
    fi
else
    for r in "${PYINST_ROOTS[@]}"; do scan_tree 1 "python-installs [$r]" "$r" -size -5M; done
fi

scan_tree 1 "WSL p4a source" "$P4A" -size -5M

# ---- files that are credentials by name ------------------------------------
scan_names 1 "Windows project tree" "$PROJ_ROOT"
scan_names 0 "sibling _legacy tree" "$SIBLING_LEGACY"

# ---- packaged artifacts ----------------------------------------------------
scan_apks 1 "project APKs" "$PROJ_ROOT"
scan_apks 0 "sibling _legacy APKs" "$SIBLING_LEGACY"

# ---- verdict ---------------------------------------------------------------
echo
echo "== total value-pattern hits: $hits_total =="
if [ "$hard" -gt 0 ]; then
    echo "RESULT: FAIL ($hard blocking problem(s))"
    exit 1
fi
if [ "$hits_total" -gt 0 ]; then
    echo "RESULT: FAIL ($hits_total hit(s) - redact before release)"
    exit 1
fi
if [ "$soft" -gt 0 ]; then
    echo "RESULT: CLEAN for the project, with $soft warning(s) outside it (see WARN above)"
    exit 0
fi
echo "RESULT: CLEAN"
