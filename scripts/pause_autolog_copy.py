#!/usr/bin/env python3
"""Pause the startup auto-copy-logs feature (TiebaSignAutoLogV4) in all 3
PythonActivity.java copies. Idempotent: marker = TiebaSignAutoLogPaused."""
import re
import sys

FILES = [
    "/home/s7909/p4a-develop/pythonforandroid/bootstraps/webview/build/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/build/bootstrap_builds/webview/src/main/java/org/kivy/android/PythonActivity.java",
    "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/src/main/java/org/kivy/android/PythonActivity.java",
]

START = "    // TiebaSignAutoLogV4: user reported the dialog freezes the container."
END = "    // TiebaSignDiagOff: user requested popup reports disabled (silent file log only)."

REPLACEMENT = """    // TiebaSignAutoLogPaused: user requested the startup auto-copy-logs feature be disabled.
    private void showPreviousState() {
        // Paused per user request (2026): silent boot/crash logging stays, clipboard is untouched.
    }

"""

IMPORT_RE = re.compile(r"^import android\.content\.ClipData;\n", re.MULTILINE)
IMPORT_RE2 = re.compile(r"^import android\.content\.ClipboardManager;\n", re.MULTILINE)

ok = True
for path in FILES:
    try:
        with open(path, "r", encoding="utf-8") as f:
            src = f.read()
    except OSError as e:
        print(f"ERROR open {path}: {e}")
        ok = False
        continue

    if "TiebaSignAutoLogPaused" in src:
        print(f"skip (already paused): {path}")
        continue

    start = src.find(START)
    end = src.find(END, start)
    if start == -1 or end == -1:
        print(f"ERROR anchors not found in {path}: start={start} end={end}")
        ok = False
        continue

    new_src = src[:start] + REPLACEMENT + src[end:]
    new_src = IMPORT_RE.sub("", new_src)
    new_src = IMPORT_RE2.sub("", new_src)

    with open(path, "w", encoding="utf-8") as f:
        f.write(new_src)
    print(f"patched: {path}")

sys.exit(0 if ok else 1)
