#!/usr/bin/env bash
# One-time WSL setup for the TiebaSign build.
# Idempotent: safe to re-run.
#
# This script used to depend on a p4a source tarball that lived in somebody's
# Windows Temp directory; after that it existed only inside the sibling project
# tieba_sign_app/third_party/ - so a fresh machine could not set up at all. Both
# materials now ship in the repo:
#
#   third_party/p4a-dev.tar.gz                   pristine python-for-android (MIT)
#   third_party/p4a-webview-base-overlay.tar.gz  our hand edits on top of it
#
# Why two files instead of one: the base edits come in two kinds. The
# update/install block is reproducible from java_src/ via
# scripts/patch_base_java.py, but the older edits (native login hooks inside the
# WebViewClient, clear-login, keep-alive, the manifest <service>) are scattered
# through existing method bodies and cannot be re-derived by splicing - see the
# long note at the top of scripts/pack_p4a_overlay.sh. Those are snapshotted.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJ_WIN="${TIEBA_PROJ_WIN:-$(cd -- "$SCRIPT_DIR/.." && pwd)}"
PROJ_WSL="${TIEBA_PROJ_WSL:-/home/s7909/tieba_sign_android}"
P4A_SRC="${P4A_SRC:-/home/s7909/p4a-develop}"
# Self-locating: the repo carries its own copies now (see third_party/README.md).
P4A_TAR="${P4A_TAR:-$PROJ_WIN/third_party/p4a-dev.tar.gz}"
P4A_OVERLAY="${P4A_OVERLAY:-$PROJ_WIN/third_party/p4a-webview-base-overlay.tar.gz}"

BOOT_REL="pythonforandroid/bootstraps/webview/build"
PA_REL="src/main/java/org/kivy/android/PythonActivity.java"
MAN_REL="templates/AndroidManifest.tmpl.xml"
WRAPPER="$P4A_SRC/pythonforandroid/bootstraps/common/build/gradle/wrapper/gradle-wrapper.properties"
BUILD_TMPL="$P4A_SRC/pythonforandroid/bootstraps/common/build/templates/build.tmpl.gradle"

echo "== 1/5: extract p4a develop into $P4A_SRC"
if [ ! -f "$P4A_SRC/pythonforandroid/toolchain.py" ]; then
  if [ ! -f "$P4A_TAR" ]; then
    echo "MISSING p4a source: $P4A_SRC has no pythonforandroid/toolchain.py"
    echo "  and the in-repo tarball $P4A_TAR does not exist either."
    echo "  third_party/p4a-dev.tar.gz belongs to the repository - see third_party/README.md"
    exit 1
  fi
  mkdir -p "$P4A_SRC"
  tar -xzf "$P4A_TAR" --strip-components=1 -C "$P4A_SRC"
  echo "   extracted $(basename -- "$P4A_TAR")"
else
  echo "   already extracted"
fi

echo "== 2/5: apply the webview base overlay (native login / keep-alive / <service>)"
if [ ! -f "$P4A_OVERLAY" ]; then
  echo "MISSING overlay: $P4A_OVERLAY"
  echo "  regenerate it from a known-good base with scripts/pack_p4a_overlay.sh"
  exit 1
fi
tmp_overlay="$(mktemp -d)"
trap 'rm -rf "$tmp_overlay"' EXIT
tar -xzf "$P4A_OVERLAY" -C "$tmp_overlay"

OVERLAY_VER="$(sed -n 's/^p4a_version=//p' "$tmp_overlay/P4A_OVERLAY_MANIFEST.txt" | head -1)"
LIVE_VER="$(sed -n "s/^__version__ = '\([^']*\)'.*/\1/p" \
            "$P4A_SRC/pythonforandroid/__init__.py" | head -1)"
echo "   overlay p4a version : ${OVERLAY_VER:-<unknown>}"
echo "   tree    p4a version : ${LIVE_VER:-<unknown>}"
if [ -z "$OVERLAY_VER" ] || [ "$OVERLAY_VER" != "$LIVE_VER" ]; then
  echo "REFUSING to apply: the overlay was taken from p4a '${OVERLAY_VER:-?}' but this"
  echo "  tree is '${LIVE_VER:-?}'. Dropping a base file from another p4a version in"
  echo "  would be a silent downgrade (and would not compile against its neighbours)."
  echo "  Regenerate it against this p4a with scripts/pack_p4a_overlay.sh."
  exit 1
fi

applied=0; current=0; warned=0
for rel in "$PA_REL" "$MAN_REL"; do
  case "$rel" in
    "$PA_REL") marker='TiebaSignNativeLoginChrome' ;;
    *)         marker='TiebaSignKeepAliveService' ;;
  esac
  src="$tmp_overlay/webview/build/$rel"
  dst="$P4A_SRC/$BOOT_REL/$rel"
  if [ ! -f "$dst" ]; then
    mkdir -p "$(dirname -- "$dst")"; cp -f "$src" "$dst"; applied=$((applied + 1))
    echo "   applied  $rel (base file was missing)"
  elif cmp -s "$src" "$dst"; then
    current=$((current + 1))
    echo "   current  $rel (identical to the overlay)"
  elif ! grep -q -- "$marker" "$dst" 2>/dev/null; then
    # Unpatched base (fresh extract): applying is unambiguously right.
    cp -f "$src" "$dst"; applied=$((applied + 1))
    echo "   applied  $rel (base was still pristine)"
  elif [ "${P4A_OVERLAY_FORCE:-0}" = 1 ]; then
    cp -f "$src" "$dst"; applied=$((applied + 1))
    echo "   applied  $rel (FORCED over a differing, already-patched base)"
  else
    warned=$((warned + 1))
    echo "   WARN     $rel differs from the overlay and already carries our markers."
    echo "            The base was edited beyond this snapshot, or the snapshot is stale."
    echo "            Re-run with P4A_OVERLAY_FORCE=1 to take the snapshot version, or"
    echo "            regenerate the snapshot: bash scripts/pack_p4a_overlay.sh"
  fi
done
rm -rf "$tmp_overlay"; trap - EXIT
echo "   applied=$applied  already-current=$current  warned=$warned"

echo "== 3/5: patch gradle network (WSL cannot reach github/maven.google/services.gradle)"
if ! grep -q 'mirrors.cloud.tencent.com/gradle' "$WRAPPER"; then
  sed -i 's#services.gradle.org/distributions#mirrors.cloud.tencent.com/gradle#' "$WRAPPER"
fi
if ! grep -q 'maven.aliyun.com/repository/google' "$BUILD_TMPL"; then
  sed -i "s#google()#maven { url 'https://maven.aliyun.com/repository/google' }#g" "$BUILD_TMPL"
  sed -i "s#mavenCentral()#maven { url 'https://maven.aliyun.com/repository/central' }#g" "$BUILD_TMPL"
fi
grep -n 'distributionUrl' "$WRAPPER"
grep -n 'repository' "$BUILD_TMPL" | head -6

echo "== 4/5: rust toolchain (cryptography 46 is rust-based)"
if ! command -v rustup >/dev/null 2>&1; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain stable
fi
# shellcheck disable=SC1091
source "$HOME/.cargo/env"
rustup target add aarch64-linux-android

echo "== 5/5: sync project into WSL (preserving .buildozer cache)"
mkdir -p "$PROJ_WSL"
cp -a "$PROJ_WIN"/. "$PROJ_WSL"/
ls -la "$PROJ_WSL"

echo "== setup done =="
