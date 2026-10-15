# -*- coding: utf-8 -*-
"""updater 离线自检：不联网、不碰 Android，只验证解析 / 判定 / 校验 / 调度。

运行：python test_updater.py     （在仓库根目录运行；日志写到临时目录，不写进项目）

夹具来自**真实服务端响应**，不是编的：
  * GITHUB_REAL  = https://api.github.com/repos/Cc96994/tiebasign/releases
                   的真实响应（2026-10-15 抓取，只保留本模块用到的字段，
                   数值一个没改：tag V0.3.0、APK 20636996 B、digest be7ee57f…、
                   download_count 19 / SHA256SUMS 1）。
  * GITEE_REAL   = https://gitee.com/api/v5/repos/baomidou/mybatis-plus/releases
                   的真实字段形状（Gitee 官方仓库自己还没有 release，返回 []，
                   所以拿别人的仓库验字段：assets 里**没有 size、也没有 digest**，
                   而且**没有 draft 字段**）。
  * GITHUB_EMPTY / GITEE_EMPTY = 真实响应就是 []（我们的 Gitee 现在正是这样）。
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import types
import urllib.error

# 自定位：<仓库>/scripts/tests/xxx.py -> <仓库>/app
APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.environ["ANDROID_PRIVATE"] = tempfile.mkdtemp(prefix="upd_selftest_")

# jnius 只存在于 Android；这里用桩替代。updater 本身在模块层不碰 Android，
# 但 _call_static_raw 会 import autosign，而 autosign 会尝试 import jnius。
for _name in ("jnius", "jnius.reflect"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["jnius"].autoclass = lambda *a, **k: None
sys.modules["jnius"].JavaMethod = lambda *a, **k: None

import updater  # noqa: E402

REAL_HTTP_GET = updater._http_get
REAL_OPEN = updater._open
REAL_CALL = updater._call_static_raw
REAL_INSPECT = updater.inspect_apk
REAL_DOWNLOAD_ASYNC = updater.download_async
REAL_CHECK_ASYNC = updater.check_async
REAL_IS_WIFI = updater._is_on_wifi
REAL_TIME = updater.time

TOTAL = [0]
FAILS = []


def check(name, cond, extra=""):
    TOTAL[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


# --------------------------------------------------------------- 真实夹具
GITHUB_REAL = [{
    "url": "https://api.github.com/repos/Cc96994/tiebasign/releases/375899663",
    "id": 375899663,
    "tag_name": "V0.3.0",
    "target_commitish": "main",
    "name": "贴吧签到助手 v0.3.0",
    "draft": False,
    "prerelease": False,
    "created_at": "2026-08-24T18:05:51Z",
    "published_at": "2026-08-24T18:18:58Z",
    "body": "# 贴吧签到助手 v0.3.0\r\n\r\n基于 aiotieba 的开源 Android 贴吧签到工具。",
    "assets": [
        {"name": "SHA256SUMS", "size": 115, "download_count": 1,
         "digest": "sha256:f56c4b8f2a2a7fb0d25fc8e9e56595a9ed277b55e01785730d69d134bd549284",
         "browser_download_url": "https://github.com/Cc96994/tiebasign/releases/download/V0.3.0/SHA256SUMS"},
        {"name": "tieba-sign-assistant-0.3.0-arm64-v8a-release.apk",
         "size": 20636996, "download_count": 19,
         "digest": "sha256:be7ee57fdd3f78015653a986af046a1d6fe1c9ecc5d46562fe5802985efa115f",
         "browser_download_url": "https://github.com/Cc96994/tiebasign/releases/download/V0.3.0/tieba-sign-assistant-0.3.0-arm64-v8a-release.apk"},
    ],
}]

GITEE_REAL = [
    {"id": 51373, "tag_name": "v3.2.1.1-SNAPSHOT", "target_commitish": "e86681bf",
     "prerelease": True, "name": "3.2.1.1-SNAPSHOT", "body": "预发布",
     "created_at": "2019-11-22T14:35:06+08:00",
     "assets": [{"browser_download_url":
                 "https://gitee.com/baomidou/mybatis-plus/archive/refs/tags/v3.2.1.1-SNAPSHOT.zip",
                 "name": "v3.2.1.1-SNAPSHOT.zip"}]},
    {"id": 360517, "tag_name": "v3.5.5", "target_commitish": "0240b1fd",
     "prerelease": False, "name": "v3.5.5", "body": "fix: 修复配置 databaseId 失效",
     "created_at": "2023-12-25T10:02:45+08:00",
     "assets": [{"browser_download_url":
                 "https://gitee.com/baomidou/mybatis-plus/archive/refs/tags/v3.5.5.zip",
                 "name": "v3.5.5.zip"},
                {"browser_download_url":
                 "https://gitee.com/baomidou/mybatis-plus/archive/refs/tags/v3.5.5.tar.gz",
                 "name": "v3.5.5.tar.gz"}]},
]

GH_LIST = "https://api.github.com/repos/Cc96994/tiebasign/releases"
GITEE_LIST = "https://gitee.com/api/v5/repos/Cc96994/tiebasign/releases"


def gh_dl(tag, name):
    return "https://github.com/Cc96994/tiebasign/releases/download/{}/{}".format(tag, name)


def gitee_dl(tag, name):
    return "https://gitee.com/Cc96994/tiebasign/releases/download/{}/{}".format(tag, name)


def gh_release(version="0.4.1", code=1023500, with_update=True, digest=None,
               apk_size=21000000):
    """在真实响应之上追加一个"更新的版本"，用来测发现更新这条路径。"""
    tag = "V" + version
    apk = "tieba-sign-assistant-{}-arm64-v8a-release.apk".format(version)
    assets = []
    if with_update:
        assets.append({"name": "update.json", "size": 300,
                       "digest": "sha256:" + "0" * 64,
                       "browser_download_url": gh_dl(tag, "update.json")})
    apk_asset = {"name": apk, "size": apk_size, "download_count": 0,
                 "browser_download_url": gh_dl(tag, apk)}
    if digest is not None:
        apk_asset["digest"] = "sha256:" + digest
    assets.append(apk_asset)
    return {"id": 999, "tag_name": tag, "prerelease": False, "draft": False,
            "name": "贴吧签到助手 v" + version, "body": "## 更新\n- 修了点东西",
            "created_at": "2026-10-16T00:00:00Z", "assets": assets}


def gitee_release(version="0.4.2"):
    """Gitee 侧：assets 没有 size、没有 digest（真实形状）。"""
    tag = "V" + version
    apk = "tieba-sign-assistant-{}-arm64-v8a-release.apk".format(version)
    return {"id": 4242, "tag_name": tag, "prerelease": False, "name": tag,
            "body": "Gitee 上的说明", "created_at": "2026-10-17T00:00:00Z",
            "assets": [{"name": apk, "browser_download_url": gitee_dl(tag, apk)}]}


def update_body(version="0.4.1", code=1023500, sha=None):
    apk = "tieba-sign-assistant-{}-arm64-v8a-release.apk".format(version)
    return {"version": version, "versionCode": code, "sha256": sha or "b" * 64,
            "apk": apk, "size": 21000000, "notes": "修了点东西",
            "minVersionCode": 1023300}


# --------------------------------------------------------------- 工具
def reset(**over):
    updater._cache = None
    data = dict(updater._DEFAULTS)
    data.update(over)
    with open(updater._data_path(), "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False)
    updater._cache = None
    return updater._load()


class HTTP(object):
    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.hits = []

    def __call__(self, url, timeout=None):
        self.hits.append(url)
        val = self.routes.get(url)
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        if isinstance(val, (dict, list)):
            return json.dumps(val).encode("utf-8")
        return val


def install_http(routes):
    http = HTTP(routes)
    updater._http_get = http
    return http


class Resp(object):
    def __init__(self, data, declared=None, chunk=None):
        self.data = data
        self.pos = 0
        self._chunk = chunk
        size = len(data) if declared is None else declared
        self.headers = {"Content-Length": str(size)} if size else {}

    def read(self, size):
        if self._chunk:
            size = self._chunk
        if self.pos >= len(self.data):
            return b""
        out = self.data[self.pos:self.pos + size]
        self.pos += len(out)
        return out

    def close(self):
        pass


def install_open(data, declared=None, chunk=None):
    resp = Resp(data, declared, chunk)
    updater._open = lambda url, timeout, extra_headers=None: resp
    return resp


class FakeTime(object):
    def __init__(self, real, step=9.0):
        self._real = real
        self._step = step
        self.t = 1000.0

    def time(self):
        self.t += self._step
        return self.t

    def __getattr__(self, name):
        return getattr(self._real, name)


# =============================================================== 1 版本解析
check("version: V0.4.0 -> (0,4,0)", updater._version_tuple("V0.4.0") == (0, 4, 0))
check("version: 小写 v 也认", updater._version_tuple("v0.4.0") == (0, 4, 0))
check("version: 没有前缀也认", updater._version_tuple("0.4.0") == (0, 4, 0))
check("version: V1.2.3", updater._version_tuple("V1.2.3") == (1, 2, 3))
check("version: 带空格", updater._version_tuple(" V0.4.0 ") == (0, 4, 0))
check("version: 预发布后缀不认", updater._version_tuple("V0.5.0-beta") is None)
check("version: 两段不认", updater._version_tuple("V1.0") is None)
check("version: 四段不认", updater._version_tuple("v3.2.1.1") is None)
check("version: 空串不认", updater._version_tuple("") is None)
check("version: 非数字不认", updater._version_tuple("nightly") is None)

# ======================================================= 2 解析真实 GitHub
src_gh = updater._SOURCES[0]
src_gitee = updater._SOURCES[1]
cands = updater._parse_releases(src_gh, GITHUB_REAL)
check("gh: 真实响应解析出 1 个候选", len(cands) == 1, cands)
check("gh: 版本 (0,3,0)", cands[0]["version"] == (0, 3, 0) if cands else False)
check("gh: tag 保留原样 V0.3.0", cands[0]["tag"] == "V0.3.0" if cands else False)
cand = cands[0]
apk = updater._apk_name(cand)
check("gh: apk 名按约定精确匹配",
      apk == "tieba-sign-assistant-0.3.0-arm64-v8a-release.apk", apk)
check("gh: digest 取到 sha256（去掉 sha256: 前缀、转小写）",
      updater._asset_digest(cand, apk)
      == "be7ee57fdd3f78015653a986af046a1d6fe1c9ecc5d46562fe5802985efa115f",
      updater._asset_digest(cand, apk))
check("gh: size 取到 20636996", updater._asset_size(cand, apk) == 20636996,
      updater._asset_size(cand, apk))
check("gh: SHA256SUMS 的 digest 不会被当成 APK 的",
      updater._asset_digest(cand, apk) !=
      "f56c4b8f2a2a7fb0d25fc8e9e56595a9ed277b55e01785730d69d134bd549284")
check("gh: 有 browser_download_url 时用服务器的地址",
      updater._asset_url(src_gh, cand, apk).startswith("https://github.com/"))
# draft / prerelease 必须过滤
draft = [dict(GITHUB_REAL[0], draft=True)]
check("gh: draft 被过滤", updater._parse_releases(src_gh, draft) == [])
pre = [dict(GITHUB_REAL[0], prerelease=True)]
check("gh: prerelease 被过滤", updater._parse_releases(src_gh, pre) == [])
check("gh: 非法 tag 的 release 被跳过",
      updater._parse_releases(src_gh, [dict(GITHUB_REAL[0], tag_name="nightly")]) == [])

# ======================================================== 3 解析真实 Gitee
gcands = updater._parse_releases(src_gitee, GITEE_REAL)
check("gitee: 真实响应里预发布被过滤，只剩 1 个", len(gcands) == 1,
      [c["tag"] for c in gcands])
check("gitee: 版本 (3,5,5)",
      gcands[0]["version"] == (3, 5, 5) if gcands else False)
grel = gcands[0]
gapk = updater._apk_name(grel)
check("gitee: 没有 digest 字段 -> None", updater._asset_digest(grel, gapk) is None)
check("gitee: 没有 size 字段 -> 0", updater._asset_size(grel, gapk) == 0)
check("gitee: 没有 apk 资产时按约定拼出名字",
      gapk == "tieba-sign-assistant-3.5.5-arm64-v8a-release.apk", gapk)
check("gitee: 无资产时按 gitee 模板拼下载地址",
      updater._asset_url(src_gitee, grel, gapk).startswith("https://gitee.com/"))
check("gitee: 没有 draft 字段也不报错",
      len(updater._parse_releases(src_gitee,
                                  [dict(GITEE_REAL[1], prerelease=False)])) == 1)
check("payload 不是 list -> 空",
      updater._parse_releases(src_gh, {"message": "Not Found"}) == [])
check("真实空响应 [] -> 空", updater._parse_releases(src_gitee, []) == [])

# ============================================================ 4 选最新
older = updater._parse_releases(src_gh, GITHUB_REAL)[0]
newer = updater._parse_releases(src_gh, [gh_release("0.4.1")])[0]
check("pick: 取版本大的",
      updater._pick_newest([older, newer])["version"] == (0, 4, 1))
check("pick: 同版本时保留先出现的源（GitHub 优先）",
      updater._pick_newest([older, dict(older, source="gitee")])["source"] == "github")
check("pick: 空列表 -> None", updater._pick_newest([]) is None)

# =================================================== 5 当前版本判定（今天真实情况）
reset()
install_http({GH_LIST: GITHUB_REAL, GITEE_LIST: []})
updater._call_static_raw = lambda name, args: (True, None)
updater._check_worker(by_user=True)
st = updater._load()
check("今天真实情况: V0.3.0 < 当前 0.4.0 -> 不报更新", st["phase"] == "idle", st["phase"])
check("今天真实情况: 不置红点", st["dot"] is False)
check("今天真实情况: latest 为空", st["latest"] is None)
check("今天真实情况: 成功的检查写了 last_check_date",
      st["last_check_date"] == updater._today(), st["last_check_date"])
reports = {r["source"]: r for r in st["reports"]}
check("今天真实情况: GitHub 报 ok", reports["github"]["status"] == "ok", reports)
check("今天真实情况: Gitee 返 [] 记为 empty 而不是 error",
      reports["gitee"]["status"] == "empty", reports)
check("今天真实情况: 详情里写明已是最新",
      "已是最新" in st["detail"], st["detail"])

# =================================================== 6 两个源都失败 -> 静默
reset()
install_http({})   # 所有 URL -> 404
updater._check_worker(by_user=False)
st = updater._load()
check("全失败: phase=error", st["phase"] == "error", st["phase"])
check("全失败: **不留红点**（自动检查失败必须静默）", st["dot"] is False)
check("全失败: 不写 last_check_date（要能重试）",
      st["last_check_date"] == "", st["last_check_date"])
check("全失败: 写了 last_attempt_ts（退避依据）",
      st["last_attempt_ts"] != "", st["last_attempt_ts"])
check("全失败: 原因留在 reports 里",
      all(r["status"] == "error" for r in st["reports"]), st["reports"])
check("全失败: latest 仍为空", st["latest"] is None)

# =================================================== 7 发现新版本（含 update.json）
reset(auto_download=False)
notify = []
updater._call_static_raw = lambda name, args: (notify.append((name, args)), (True, None))[1]
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1", digest="a" * 64)],
              GITEE_LIST: [],
              gh_dl("V0.4.1", "update.json"): update_body()})
updater._download_async = REAL_DOWNLOAD_ASYNC
updater.check_async = REAL_CHECK_ASYNC
updater._check_worker(by_user=True)
st = updater._load()
check("新版本: phase=available", st["phase"] == "available", st["phase"])
check("新版本: version=0.4.1", (st["latest"] or {}).get("version") == "0.4.1",
      st["latest"])
check("新版本: versionCode 来自 update.json", 
      (st["latest"] or {}).get("version_code") == 1023500, st["latest"])
check("新版本: sha256 来自 update.json（不是 assets 的 digest）",
      (st["latest"] or {}).get("sha256") == "b" * 64, st["latest"])
check("新版本: 来源标为 github", (st["latest"] or {}).get("source") == "github")
check("新版本: 置红点", st["dot"] is True)
check("新版本: 弹了一次通知", len(notify) == 1, notify)
check("新版本: 通知走的是 notifyUpdateReady",
      bool(notify) and notify[0][0] == "notifyUpdateReady", notify)
check("新版本: 记下 notified_version 防止天天打扰",
      st["notified_version"] == "0.4.1", st["notified_version"])
updater._check_worker(by_user=True)
check("新版本: 第二次检查不再重复通知", len(notify) == 1, len(notify))

# =================================================== 8 versionCode 是硬判据
reset(auto_download=False)
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1", digest="a" * 64)],
              GITEE_LIST: [],
              # tag 说 0.4.1，update.json 的 versionCode 却还是 0.3.0 那个
              gh_dl("V0.4.1", "update.json"): update_body("0.4.1", code=1023300)})
updater._check_worker(by_user=True)
st = updater._load()
check("versionCode 防线: tag 更新但 versionCode 不更大 -> 不算更新",
      st["phase"] == "idle" and st["latest"] is None, st)
check("versionCode 防线: 详情说明原因", "versionCode" in st["detail"], st["detail"])
check("versionCode 防线: 不置红点", st["dot"] is False)

# =================================================== 9 没有 update.json 的兜底
reset(auto_download=False)
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1", with_update=False,
                                                digest="c" * 64)],
              GITEE_LIST: []})
updater._check_worker(by_user=True)
st = updater._load()
check("无 update.json: 仍能发现更新", st["phase"] == "available", st["phase"])
check("无 update.json: sha256 退回 assets 的 digest",
      (st["latest"] or {}).get("sha256") == "c" * 64, st["latest"])
check("无 update.json: versionCode 未知记为 0",
      (st["latest"] or {}).get("version_code") == 0, st["latest"])

# =================================================== 10 Gitee 兜底 + 没有校验值
reset(auto_download=True)
is_wifi_calls = []
updater._is_on_wifi = lambda: (is_wifi_calls.append(1), True)[1]
downloaded = []
updater.download_async = lambda: (downloaded.append(1), (True, None))[1]
install_http({GH_LIST: GITHUB_REAL, GITEE_LIST: [gitee_release("0.4.2")]})
updater._check_worker(by_user=True)
st = updater._load()
check("Gitee 兜底: GitHub 只有旧版时用 Gitee 的最新版",
      (st["latest"] or {}).get("version") == "0.4.2", st["latest"])
check("Gitee 兜底: 来源标为 gitee", (st["latest"] or {}).get("source") == "gitee")
check("Gitee 兜底: 没有 sha256 -> 不自动下载", not downloaded, downloaded)
check("Gitee 兜底: 明确告诉用户要手动核对",
      "SHA256SUMS" in st["detail"], st["detail"])

# 有 sha256、Wi-Fi 开关开着但当前不是 Wi-Fi -> 暂停下载
reset(auto_download=True, wifi_only=True)
downloaded = []
updater.download_async = lambda: (downloaded.append(1), (True, None))[1]
updater._is_on_wifi = lambda: False
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1")], GITEE_LIST: [],
              gh_dl("V0.4.1", "update.json"): update_body(sha="d" * 64)})
updater._check_worker(by_user=True)
st = updater._load()
check("非 Wi-Fi: 不自动下载", not downloaded, downloaded)
check("非 Wi-Fi: 提示已暂停且可手动下载", "Wi-Fi" in st["detail"], st["detail"])

# 有 sha256、是 Wi-Fi -> 自动下载
reset(auto_download=True, wifi_only=True)
downloaded = []
updater.download_async = lambda: (downloaded.append(1), (True, None))[1]
updater._is_on_wifi = lambda: True
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1")], GITEE_LIST: [],
              gh_dl("V0.4.1", "update.json"): update_body(sha="e" * 64)})
updater._check_worker(by_user=True)
check("是 Wi-Fi: 自动开始下载", downloaded == [1], downloaded)

# 判断不了网络类型（原生方法还没就位）-> fail-open，允许下载
reset(auto_download=True, wifi_only=True)
downloaded = []
updater.download_async = lambda: (downloaded.append(1), (True, None))[1]
updater._is_on_wifi = lambda: None
install_http({GH_LIST: GITHUB_REAL + [gh_release("0.4.1")], GITEE_LIST: [],
              gh_dl("V0.4.1", "update.json"): update_body(sha="f" * 64)})
updater._check_worker(by_user=True)
check("网络类型未知: fail-open 仍下载（否则可能永远收不到更新）",
      downloaded == [1], downloaded)

# =================================================== 11 下载流与校验
BIG = os.urandom(0) + b"\x5a" * (2 * 1024 * 1024)   # 2MB
BIG_SHA = hashlib.sha256(BIG).hexdigest()
dest = os.path.join(updater._download_dir(), "probe.apk")
os.makedirs(updater._download_dir(), exist_ok=True)

install_open(BIG)
state, detail = updater._stream_to("http://x/apk", dest, BIG_SHA)
check("下载: 正常完成", state == "ok", (state, detail))
check("下载: sha256 对得上", state == "ok" and detail["sha256"] == BIG_SHA, detail)
check("下载: 字节数对得上",
      state == "ok" and detail["size"] == len(BIG), detail)

install_open(b"\x00" * 100, declared=100)
state, detail = updater._stream_to("http://x/apk", dest, BIG_SHA)
check("下载: Content-Length 异常（<1MB 或 >200MB）直接拒绝",
      state == "error" and "大小异常" in detail, (state, detail))

install_open(b"\x00" * 100, declared=0)
state, detail = updater._stream_to("http://x/apk", dest, BIG_SHA)
check("下载: 实际太小 -> 判定不完整",
      state == "error" and "不完整" in detail, (state, detail))

install_open(BIG)
state, detail = updater._stream_to("http://x/apk", dest, "0" * 64)
check("下载: sha256 不符 -> 校验失败",
      state == "error" and "校验失败" in detail, (state, detail))

updater.time = FakeTime(time, step=9.0)
install_open(BIG, chunk=32 * 1024)
state, detail = updater._stream_to("http://x/apk", dest, BIG_SHA, probe=True)
check("下载: 8 秒内太慢 -> slow（交给上层切源）",
      state == "slow", (state, detail))
updater.time = REAL_TIME

updater.time = FakeTime(time, step=0.001)
install_open(BIG, chunk=32 * 1024)
state, detail = updater._stream_to("http://x/apk", dest, BIG_SHA, probe=True)
check("下载: 速度合格 -> 不误判为 slow", state == "ok", (state, detail))
updater.time = REAL_TIME

# =================================================== 12 候选地址顺序（GitHub 优先）
reset(download_mode="auto")
latest = {"tag": "V0.4.1", "apk_name": "a.apk", "source": "github"}
c = updater._candidates(latest)
check("候选: auto 模式 GitHub 在前、Gitee 在后",
      [x[0] for x in c] == ["github", "gitee"], c)
reset(download_mode="gitee")
check("候选: 只用 Gitee 时不出现 GitHub",
      [x[0] for x in updater._candidates(latest)] == ["gitee"])
reset(download_mode="github")
check("候选: 只用 GitHub 时不出现 Gitee",
      [x[0] for x in updater._candidates(latest)] == ["github"])
reset(download_mode="auto")
c = updater._candidates({"tag": "V0.4.2", "apk_name": "a.apk", "source": "gitee"})
check("候选: 发现源是 Gitee 时它排第一", [x[0] for x in c] == ["gitee", "github"], c)

# =================================================== 13 安装前三道校验
def make_apk(name="dl.apk", data=b"fakeapk"):
    path = os.path.join(updater._download_dir(), name)
    os.makedirs(updater._download_dir(), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def stage(path):
    reset(downloaded={"path": path, "version": "0.4.1", "version_code": 1023500,
                      "sha256": "a" * 64, "size": 10, "name": "dl.apk",
                      "source": "github", "ts": updater._now()})


p = make_apk("reject_pkg.apk")
stage(p)
updater.inspect_apk = lambda path: ({"package": "com.evil.app",
                                     "version_code": 9999999,
                                     "signer": updater._SIGNER_SHA256}, None)
ok, err = updater.install()
check("安装校验: 包名不符 -> 拒绝", ok is False and "不是本应用" in err, (ok, err))
check("安装校验: 包名不符 -> 文件已删除", not os.path.exists(p))

p = make_apk("reject_sign.apk")
stage(p)
updater.inspect_apk = lambda path: ({"package": updater._PACKAGE,
                                     "version_code": 9999999,
                                     "signer": "deadbeef" * 8}, None)
ok, err = updater.install()
check("安装校验: 签名不符 -> 拒绝", ok is False and "签名" in err, (ok, err))
check("安装校验: 签名不符 -> 文件已删除", not os.path.exists(p))

p = make_apk("reject_old.apk")
stage(p)
updater.inspect_apk = lambda path: ({"package": updater._PACKAGE,
                                     "version_code": updater._APP_VERSION_CODE,
                                     "signer": updater._SIGNER_SHA256}, None)
ok, err = updater.install()
check("安装校验: versionCode 不大于当前 -> 拒绝",
      ok is False and "不比当前版本新" in err, (ok, err))
check("安装校验: 降级包 -> 文件已删除", not os.path.exists(p))

p = make_apk("good.apk")
stage(p)
updater.inspect_apk = lambda path: ({"package": updater._PACKAGE,
                                     "version_code": 1023500,
                                     "signer": updater._SIGNER_SHA256}, None)
called = []
updater._call_static_raw = lambda name, args: (called.append((name, args)), (True, None))[1]
ok, err = updater.install()
check("安装校验: 三项都过 -> 调用原生安装入口",
      ok is True and called and called[0][0] == "installApk", (ok, err, called))
check("安装校验: 合格包不会被删掉", os.path.exists(p))

updater.inspect_apk = lambda path: (None, "native 未就位")
stage(p)
ok, err = updater.install()
check("安装校验: 读不到 APK 信息 -> 明确失败", ok is False and "无法校验" in err,
      (ok, err))
updater.inspect_apk = REAL_INSPECT
reset()
ok, err = updater.install()
check("安装校验: 没有下载记录 -> 明确失败",
      ok is False and "还没有下载" in err, (ok, err))

# =================================================== 14 每日调度与退避
calls = []
updater.check_async = lambda by_user=False: (calls.append(by_user), (True, None))[1]
reset(last_check_date=updater._today(), auto_check=True)
check("调度: 今天已成功查过 -> 不再查", updater.daily_tick() is False and not calls,
      calls)
reset(last_check_date="", last_attempt_ts=updater._now(), auto_check=True)
check("调度: 刚失败过 -> 1 小时内不重试",
      updater.daily_tick() is False and not calls, calls)
old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 7200))
reset(last_check_date="", last_attempt_ts=old, auto_check=True)
check("调度: 失败已过 2 小时 -> 允许重试",
      updater.daily_tick() is True and calls == [False], calls)
reset(auto_check=False)
calls = []
check("调度: 关掉自动检查 -> 什么都不做",
      updater.daily_tick() is False and not calls, calls)

# =================================================== 15 设置
reset()
ok, err = updater.set_settings({"download_mode": "gitee"})
check("设置: download_mode 生效",
      ok is True and updater.get_settings()["download_mode"] == "gitee", err)
ok, err = updater.set_settings({"download_mode": "baidu"})
check("设置: 非法 download_mode 被拒", ok is False and err, (ok, err))
ok, err = updater.set_settings({"auto_check": False, "wifi_only": False})
settings = updater.get_settings()
check("设置: 布尔开关生效",
      ok is True and settings["auto_check"] is False
      and settings["wifi_only"] is False, settings)
ok, err = updater.set_settings("not a dict")
check("设置: 非 dict 被拒", ok is False, (ok, err))
reset()
updater.set_settings({"auto_check": True})
check("老 store 迁移: 缺键用默认值补齐",
      updater.get_settings()["auto_check"] is True
      and updater.get_settings()["auto_download"] is True)

# =================================================== 16 状态 / 红点 / 自检
reset()
path = updater.selftest()
check("selftest: 落盘成功且文件存在", os.path.isfile(path), path)
state = updater.get_state()
check("get_state: 当前版本与 spec 一致",
      state["current"]["version"] == "0.4.0"
      and state["current"]["version_code"] == 1023400, state["current"])
check("get_state: 包名是 org.dsh.tiebaweb",
      state["current"]["package"] == "org.dsh.tiebaweb")
check("get_state: 两个源都在", [s["id"] for s in state["sources"]] == ["github", "gitee"])
check("get_state: 桌面环境 native_ready=None（判断不了，不是 False）",
      state["native_ready"] is None, state["native_ready"])
check("get_state: limits 里有测速参数",
      state["settings"]["limits"]["probe_min_kb"] == 800, state["settings"])
reset(dot=True)
updater.clear_dot()
check("clear_dot: 清掉红点", updater._load()["dot"] is False)

# --------------------------------------------------------------- 收尾
updater._http_get = REAL_HTTP_GET
updater._open = REAL_OPEN
updater.inspect_apk = REAL_INSPECT
updater._is_on_wifi = REAL_IS_WIFI
updater.time = REAL_TIME

print("")
print("checks: {}   failed: {}".format(TOTAL[0], len(FAILS)))
if FAILS:
    print("FAILED:")
    for name in FAILS:
        print("  - " + name)
    sys.exit(1)
print("UPDATER_SELFTEST_OK")
