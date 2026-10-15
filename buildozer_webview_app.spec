# TiebaSign M2.2 WebView app build (final-app runtime chain + Keystore/Cookie
# Manager tech check). NOTE: this project NEVER contains BDUSS, BDUSS.txt or
# old plain-text files. Bootstrap = webview (proven on HarmonyOS 卓易通),
# UI served by Python on http://127.0.0.1:5000/ (p4a WebViewLoader port).

[app]

title = 贴吧签到助手
package.name = tiebaweb
package.domain = org.dsh

source.dir = ./app
source.include_exts = py,png
source.exclude_dirs = p4a_local,vendor,scripts,bin,bin_probe,bin_m21,bin_m22,.buildozer,.git,__pycache__

version = 0.4.0

# Locked dependency chain (no kivy/sdl2 in the webview route).
# Recipes: python3 3.12.7 / aiotieba 4.7.1 (local recipe, NDK crypto +
#   HTTPS SSL patch) / protobuf 7.36.0 (local pure-python recipe) /
#   aiohttp 3.13.3 / lxml 4.9.3 / cryptography 46.0.3 / setuptools 80.9.0 /
#   zlib 1.3.1 (local recipe: real runnable libz.so for HarmonyOS 卓易通).
# Hyphenated package names must be written with underscores (p4a pip stage
# mismatch pitfall). C-extension modules MUST stay explicitly listed here.
requirements = python3==3.12.7,zlib,aiotieba==4.7.1,protobuf==7.36.0,aiohttp==3.13.3,lxml==4.9.3,cryptography==46.0.3,beautifulsoup4==4.13.4,setuptools==80.9.0,yarl==1.24.5,multidict==6.7.1,frozenlist==1.8.0,aiosignal==1.4.0,aiohappyeyeballs==2.7.1,async_timeout==5.0.1,attrs==26.1.0,idna==3.19,charset_normalizer==3.4.4,soupsieve==2.7,typing_extensions==4.16.0,propcache==0.5.2,six==1.17.0,requests==2.32.4,urllib3==2.5.0,certifi==2025.4.26,chardet==5.2.0,filetype==1.2.0

orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/icon.png

android.api = 34
android.minapi = 23
android.archs = arm64-v8a
android.permissions = INTERNET,FOREGROUND_SERVICE,FOREGROUND_SERVICE_DATA_SYNC,POST_NOTIFICATIONS,ACCESS_NETWORK_STATE,REQUEST_INSTALL_PACKAGES
android.accept_sdk_license = True
android.allow_backup = False
android.release_artifact = apk

# Use the WebView bootstrap (no SDL2/OpenGL). Port defaults to 5000 in p4a.
p4a.bootstrap = webview
p4a.source_dir = /home/s7909/p4a-develop
p4a.local_recipes = /home/s7909/tieba_sign_android/p4a_local
p4a.setup_py = false

[buildozer]

log_level = 2
warn_on_root = 1
bin_dir = ./bin_m22
