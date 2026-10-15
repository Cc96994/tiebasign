#!/usr/bin/env bash
# app_identity.sh - single source of truth for the app identity (OLD project).
#
# Sourced by the other scripts so that no name is spelled out twice and drifts.
# History: check_apk_manifest.py used to hardcode the service class name, which
# made the manifest verification silently pass on nothing.
#
# Overridable via env: APP_SPEC / APP_SERVICE_CLASS / APP_KEY_ALIAS / APP_KEYSTORE_NAME
set -uo pipefail

_AI_HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# NOTE: in this project the real identity lives in buildozer_webview_app.spec.
# buildozer.spec is a different (probe) spec that wsl_release_build.sh swaps out.
APP_SPEC="${APP_SPEC:-$_AI_HERE/../buildozer_webview_app.spec}"

if [ ! -f "$APP_SPEC" ]; then
    echo "app_identity.sh: spec not found: $APP_SPEC" >&2
    return 1 2>/dev/null || exit 1
fi

APP_TITLE="$(sed -n 's/^title = //p' "$APP_SPEC" | head -1)"
APP_DOMAIN="$(sed -n 's/^package\.domain = //p' "$APP_SPEC" | head -1)"
APP_NAME="$(sed -n 's/^package\.name = //p' "$APP_SPEC" | head -1)"
APP_VERSION="$(sed -n 's/^version = //p' "$APP_SPEC" | head -1)"

if [ -z "$APP_TITLE" ] || [ -z "$APP_DOMAIN" ] || [ -z "$APP_NAME" ] \
   || [ -z "$APP_VERSION" ]; then
    echo "app_identity.sh: spec lacks title / package.name / package.domain / version" >&2
    return 1 2>/dev/null || exit 1
fi

APP_PACKAGE="$APP_DOMAIN.$APP_NAME"
APP_SERVICE_CLASS="${APP_SERVICE_CLASS:-TiebaSignKeepAliveService}"
APP_KEY_ALIAS="${APP_KEY_ALIAS:-tiebasign}"
# Historical name: it does NOT follow "$APP_NAME-release.keystore" on purpose.
# This is the one and only key that may sign a release for existing users.
APP_KEYSTORE_NAME="${APP_KEYSTORE_NAME:-tiebasign-release.keystore}"

# ---- immutable facts about the PUBLISHED v0.3.0 (GitHub release V0.3.0) ----
# This version is "the next version of the same app": applicationId and signer
# must match, versionCode must not go backwards, or Android refuses the
# in-place upgrade (the user would have to uninstall = lose their sign-in data).
# Measured on the published asset, not from memory. Enforced by
# scripts/check_upgrade_compat.sh before every release.
APP_PREV_VERSION="0.3.0"
APP_PREV_VERSION_CODE="${APP_PREV_VERSION_CODE:-1023300}"
APP_PREV_SIGNER_SHA256="${APP_PREV_SIGNER_SHA256:-84d7ed804fb7d2124cf51a083fb566286be0f381b83fe5689b667a2d3d6e0a7b}"
# sha256 of the published APK file itself (release/tieba-sign-assistant-0.3.0-arm64-v8a-release.apk)
APP_PREV_APK_SHA256="${APP_PREV_APK_SHA256:-be7ee57fdd3f78015653a986af046a1d6fe1c9ecc5d46562fe5802985efa115f}"

export APP_SPEC APP_TITLE APP_DOMAIN APP_NAME APP_VERSION APP_PACKAGE \
       APP_SERVICE_CLASS APP_KEY_ALIAS APP_KEYSTORE_NAME \
       APP_PREV_VERSION APP_PREV_VERSION_CODE APP_PREV_SIGNER_SHA256 APP_PREV_APK_SHA256
