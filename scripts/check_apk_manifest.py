#!/usr/bin/env python3
"""Check binary AndroidManifest.xml strings from an APK (UTF-16LE aware)."""
import subprocess, sys, tempfile, os, zipfile

APK = sys.argv[1] if len(sys.argv) > 1 else "/home/s7909/tieba_sign_android/bin_m22/tiebaweb-0.1.18-arm64-v8a-debug.apk"
with zipfile.ZipFile(APK) as z:
    data = z.read("AndroidManifest.xml")
# binary XML: try UTF-16LE decode of the whole thing, then scan
try:
    text = data.decode("utf-16-le", errors="ignore")
except Exception:
    text = data.decode("latin1", errors="ignore")
for pat in ["TiebaSignKeepAliveService", "FOREGROUND_SERVICE", "FOREGROUND_SERVICE_DATA_SYNC", "POST_NOTIFICATIONS", "INTERNET"]:
    print(f"{pat}={text.count(pat)}")
