#!/usr/bin/env python3
"""Patch dists/tiebaweb/templates/AndroidManifest.tmpl.xml with TiebaSignKeepAliveService."""
import sys

TARGET = "/home/s7909/tieba_sign_android/.buildozer/android/platform/build-arm64-v8a/dists/tiebaweb/templates/AndroidManifest.tmpl.xml"
MARKER = "TiebaSignKeepAliveService"
SERVICE = """        <service android:name="org.dsh.tiebaweb.TiebaSignKeepAliveService"
                 android:exported="false"
                 android:foregroundServiceType="dataSync"
                 android:stopWithTask="false" />
"""

with open(TARGET, "r", encoding="utf-8") as f:
    text = f.read()

if MARKER in text:
    print("ALREADY_PATCHED")
    sys.exit(0)

anchor = "    </application>"
idx = text.rfind(anchor)
if idx < 0:
    print("ANCHOR_NOT_FOUND")
    sys.exit(2)

text = text[:idx] + SERVICE + text[idx:]
with open(TARGET, "w", encoding="utf-8") as f:
    f.write(text)
print("PATCHED dist templates manifest")
