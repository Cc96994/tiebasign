#!/usr/bin/env bash
# test_build_swap.sh - regression test for the buildozer.spec swap in the two
# build scripts.
#
# Why this exists: on a fresh clone `buildozer.spec` does NOT exist (it is
# .gitignored as a legacy projection spec), and the old code did
#
#     if [ ! -f buildozer.spec.main.bak ]; then
#         cp -a buildozer.spec buildozer.spec.main.bak
#     fi
#
# under `set -e` - so a cloned tree could not build at all: the very first real
# step died with "cp: cannot stat 'buildozer.spec'". A second, quieter defect sat
# on the other end: the restore line lived *after* the buildozer call, so a failed
# build left the swapped spec behind and the next build used the wrong spec.
#
# The swap block is EXTRACTED FROM THE REAL SCRIPTS and executed, so this test
# cannot drift away from what actually ships (a hand-copied block would).
#
# Usage: bash scripts/tests/test_build_swap.sh
# Exit: 0 = all cases pass.

set -uo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ="$(cd -- "$SCRIPT_DIR/../.." && pwd)"
SPEC="$PROJ/buildozer_webview_app.spec"
SPEC_BAK=buildozer.spec.main.bak
MARK_ORIG='# ORIGINAL-PROJECTION-SPEC'
MARK_LEFTOVER='# LEFTOVER-FROM-A-FAILED-RUN'

fails=0
pass() { echo "ok    $*"; }
bad()  { echo "FAIL  $*"; fails=$((fails + 1)); }

[ -f "$SPEC" ] || { echo "FAIL  missing $SPEC"; exit 1; }

# Pull the swap block out of a build script, verbatim.
# Anchored on ASCII text only, so no encoding assumptions.
extract_block() {  # <script> <out>
    awk '
        index($0, "== swap in") { inseg = 1 }
        inseg { print }
        inseg && index($0, "cp -f buildozer_webview_app.spec buildozer.spec") { exit }
    ' "$1" > "$2"
    # The trap install must have come along, or the test is not testing anything.
    grep -q 'trap restore_spec EXIT' "$2" || return 1
    grep -q 'restore_spec()' "$2" || return 1
    return 0
}

# one case: <label> <script> <have-spec yes/no> <leftover-bak yes/no> <build-fails yes/no>
run_case() {
    local label="$1" script="$2" have_spec="$3" leftover="$4" build_fails="$5"
    local d; d="$(mktemp -d)" || { bad "$label: mktemp failed"; return; }
    cp "$SPEC" "$d/buildozer_webview_app.spec"

    [ "$have_spec" = yes ] && printf 'version = 0.1.0\n%s\n' "$MARK_ORIG" > "$d/buildozer.spec"
    [ "$leftover" = yes ] && printf 'version = 0.1.0\n%s\n' "$MARK_LEFTOVER" > "$d/$SPEC_BAK"

    local run="$d/run.sh"
    if ! extract_block "$script" "$run"; then
        bad "$label: could not extract the swap block from $(basename -- "$script")"
        rm -rf "$d"; return
    fi
    if [ "$build_fails" = yes ]; then
        # Simulate the build blowing up: `set -e` aborts here, so ONLY the trap
        # can put the tree back. This is the case that used to leave the swapped
        # spec behind.
        printf 'echo "== buildozer (simulated)"\nfalse\n' >> "$run"
    else
        printf 'echo "== restore main spec"\nrestore_spec\n' >> "$run"
    fi

    local rc=0
    ( cd "$d" && bash -e "$run" ) >/dev/null 2>&1 || rc=$?

    local problems=()
    if [ "$build_fails" = yes ]; then
        [ "$rc" -ne 0 ] || problems+=("expected a non-zero exit from the simulated failure")
    else
        [ "$rc" -eq 0 ] || problems+=("expected exit 0, got $rc")
    fi

    # buildozer.spec must be back to whatever it was before the swap - and if
    # there was nothing, it must not be left behind.
    if [ "$have_spec" = yes ] || [ "$leftover" = yes ]; then
        local want="$MARK_ORIG"
        [ "$leftover" = yes ] && want="$MARK_LEFTOVER"
        if [ ! -f "$d/buildozer.spec" ]; then
            problems+=("buildozer.spec was not restored")
        elif ! grep -q "$want" "$d/buildozer.spec"; then
            problems+=("buildozer.spec was restored with the wrong content (want $want)")
        fi
    elif [ -e "$d/buildozer.spec" ]; then
        problems+=("buildozer.spec was left behind in a tree that never had one")
    fi

    [ -e "$d/$SPEC_BAK" ] && problems+=("$SPEC_BAK was left behind")

    if [ "${#problems[@]}" -eq 0 ]; then
        pass "$label"
    else
        bad "$label"
        local p; for p in "${problems[@]}"; do echo "        $p"; done
    fi
    rm -rf "$d"
}

for script in "$PROJ/scripts/wsl_app_build.sh" "$PROJ/scripts/wsl_release_build.sh"; do
    name="$(basename -- "$script")"
    echo "== $name"
    rc=0
    bash -n "$script" || { bad "$name: syntax error"; continue; }
    pass "$name: syntax ok"
    run_case "$name: fresh clone, build ok        -> nothing left behind" "$script" no  no  no
    run_case "$name: fresh clone, build FAILS     -> nothing left behind" "$script" no  no  yes
    run_case "$name: spec present, build ok       -> original restored"   "$script" yes no  no
    run_case "$name: spec present, build FAILS    -> original restored"   "$script" yes no  yes
    run_case "$name: leftover .bak, build ok      -> .bak wins"           "$script" yes yes no
done

echo
if [ "$fails" -eq 0 ]; then
    echo "BUILD_SWAP_OK"
    exit 0
fi
echo "BUILD_SWAP_FAILED ($fails)"
exit 1
