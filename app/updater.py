# -*- coding: utf-8 -*-
"""TiebaSign v0.4.0 - 功能一：检查更新 + 自动下载 + 唤醒系统安装界面。

用户确认（2026-10-15 第三轮，别再按旧设计改回去）
  * **双源**：GitHub 优先、Gitee 替补。用户的原话是"想让 GitHub 那边的数据好看点"。
    注意：**检查源的选择完全不影响 GitHub 的下载计数** —— GitHub 的
    download_count 只在有人真的从 GitHub 下载资产时才 +1（实测 V0.3.0 的 APK 是
    19 次、SHA256SUMS 是 1 次，计的是"谁下了哪个文件"，不是"谁看了仓库"）。
    所以"数据好看"只由 **download_mode** 决定，检查顺序与它无关，两者是独立开关。
  * **每日检查一次**；**没查到不提示**，查到了才弹提示
  * 提醒方式：系统通知（点一下直达安装界面）+ 设置页红点
  * 不做静默安装：下载 + 校验 → 唤醒系统安装界面

必须记住的边界（下面的常量都有实测依据，改之前先看注释）
  * 版本比较用 **versionCode（数字）**，不比 "0.4.0"/"0.4.1" 字符串。
    versionCode 由发布脚本用 aapt2 读真包写入 update.json，不手写。
    tag 名（V0.4.0）只是次要判据，且**本项目的 tag 是大写 V**（实测 V0.3.0）。
  * **只接受本 App 的包名 + 本项目的签名密钥**：下载完调用 Java 读 APK 的
    PackageManager 元数据；包名不是 org.dsh.tiebaweb、签名 SHA-256 不是
    84d7ed80…6e0a7b、或 versionCode 不大于当前 → 一律删掉并拒绝安装。
    这是自更新通道**唯一真正的安全边界**：sha256 也来自同一台服务器，单靠它挡不住
    被篡改的发布端，只有签名校验挡得住。
  * GitHub API 未登录限流 60 次/小时/IP。每日一次足够；403/429 一律当"这次没查到"
    **静默处理**，绝不弹错误给用户（多个用户共用运营商 NAT 出口时容易撞上）。
  * **Gitee 的 release JSON 里 assets 没有 size、也没有 digest**（实测
    gitee.com/api/v5/repos/baomidou/mybatis-plus/releases）；GitHub 两者都有
    （实测 Cc96994/tiebasign，APK 资产带 digest=sha256:be7ee57f…）。所以
    sha256 一律以 update.json 为准：它缺失时 GitHub 用 assets[].digest 兜底，
    **Gitee 则拒装**（没有可信 sha256 就不下）。
  * **Gitee 现在还没有任何 release**（实测 https://gitee.com/api/v5/repos/
    Cc96994/tiebasign/releases 返回 []）→ "源返回空" **不是错误**，只是"这个源
    没有信息"，必须静默略过；否则首次运行就会误报。
  * 两个源的列表接口都**免登录可读**（实测均 HTTP 200，无 token）。私有仓库会
    401：那时需要 token 打进 APK，等于把仓库钥匙送人 —— 所以仓库必须保持公开。

数据文件：<ANDROID_PRIVATE>/tiebasign_update.json（不含任何凭据）
"""

import hashlib
import json
import os
import threading
import time
import traceback
import urllib.error
import urllib.request

_DATA_FILENAME = "tiebasign_update.json"
_SCHEMA = 1

# ---- 当前 App 的身份（这两行是"同一个事实的第二份拷贝"，由 verify_release.sh
#      强制校验：versionCode 必须等于 APK 实测值，version 必须等于 spec 的
#      version / main.py 里用户看得见的那串。改版本号时三处一起改。）--------
_APP_VERSION = "0.4.0"
_APP_VERSION_CODE = 1023400
_PACKAGE = "org.dsh.tiebaweb"
# 只允许这个签名密钥签出来的包被装上去（与 scripts/app_identity.sh 的
# APP_PREV_SIGNER_SHA256 是同一个值）。
_SIGNER_SHA256 = "84d7ed804fb7d2124cf51a083fb566286be0f381b83fe5689b667a2d3d6e0a7b"

# ---- 更新源 ---------------------------------------------------------------
# dl 模板适用于两个源：GitHub 与 Gitee 的 release 资产下载路径形状相同。
_SOURCES = (
    {"id": "github", "label": "GitHub",
     "list": "https://api.github.com/repos/{owner}/{repo}/releases",
     "dl": "https://github.com/{owner}/{repo}/releases/download/{tag}/{name}"},
    {"id": "gitee", "label": "Gitee",
     "list": "https://gitee.com/api/v5/repos/{owner}/{repo}/releases",
     "dl": "https://gitee.com/{owner}/{repo}/releases/download/{tag}/{name}"},
)
_SOURCE_IDS = tuple(s["id"] for s in _SOURCES)

# ---- 网络参数 -------------------------------------------------------------
_CHECK_TIMEOUT = 8        # 元数据请求（几 KB）
_UPDATE_JSON_TIMEOUT = 8  # update.json（几百字节）
_DOWNLOAD_TIMEOUT = 30    # 单次读超时；下载总时长不设上限（20MB 慢网可能要几分钟）
_PROBE_SEC = 8            # auto 模式：先下这么多秒看速度
_PROBE_MIN_BYTES = 800 * 1024   # 8 秒内不到 800KB（<100KB/s）就判定"太慢"，切源
_CHUNK = 64 * 1024
_CHECK_RETRY_SEC = 3600   # 检查失败后至少隔 1 小时再重试（成功的当天不再查）

# ---- 输入/展示限制 --------------------------------------------------------
_MAX_NOTES = 400          # release 说明最多显示多少字
_MAX_DETAIL = 200
_MIN_APK_BYTES = 1024 * 1024      # 小于 1MB 的东西绝不可能是我们的包
_MAX_APK_BYTES = 200 * 1024 * 1024

_DEFAULTS = {
    "version": _SCHEMA,
    "owner": "Cc96994",
    "repo": "tiebasign",
    "auto_check": True,          # 每天自动查一次
    "auto_download": True,       # 查到就更（可关：只提示，用户自己点下载）
    "wifi_only": True,           # 只在 Wi-Fi 下自动下载（移动网络只提示）
    "download_mode": "auto",     # auto=GitHub 优先、太慢自动切 Gitee；github；gitee
    "last_check_date": "",       # "YYYY-MM-DD"，只有**成功**的检查才写（每日一次）
    "last_attempt_ts": "",       # 最后一次尝试（成功或失败），失败后的退避依据
    "last_check_ts": "",
    "phase": "idle",             # idle|checking|available|downloading|ready|error
    "detail": "",
    "latest": None,              # 发现的可用更新（见 _normalize_latest）
    "downloaded": None,          # 已下载并通过校验的包
    "reports": [],               # 每个源这次的结果，给设置页显示
    "notified_version": "",      # 已经就哪个版本弹过通知（同一个版本只弹一次）
    "dot": False,                # 设置页红点
    "history": [],               # 最近几次检查摘要（最多 10 条）
}

_lock = threading.Lock()
_cache = None
_check_thread = None
_download_thread = None
_bridge_warned = False


# --------------------------------------------------------------------------
# 数据文件（与 waterpost.py 同一套写法）
# --------------------------------------------------------------------------

def _data_path():
    return os.path.join(
        os.environ.get("ANDROID_PRIVATE") or os.getcwd(), _DATA_FILENAME)


def _load():
    global _cache
    if _cache is not None:
        return _cache
    data = {}
    if os.path.isfile(_data_path()):
        try:
            with open(_data_path(), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    merged = dict(_DEFAULTS)
    merged.update(data)
    # 旧 store 迁移：只做加法（新键用默认值补上），不做减法
    if int(merged.get("version") or 0) < _SCHEMA:
        merged["version"] = _SCHEMA
    if merged.get("download_mode") not in ("auto", "github", "gitee"):
        merged["download_mode"] = "auto"
    _cache = merged
    return _cache


def _save(data):
    global _cache
    tmp = _data_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, _data_path())
    _cache = data


def _today():
    return time.strftime("%Y-%m-%d")


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _short(text):
    s = "{}".format(text).replace("\n", " ").strip()
    return s[:_MAX_DETAIL]


def _update(phase=None, detail=None, **kw):
    """改状态并落盘。**每次都必须重新 _load()**：下载/检查可能耗时几十秒，
    期间用户完全可能改设置（与 waterpost._tick 同一个理由）。"""
    with _lock:
        data = _load()
        if phase is not None:
            data["phase"] = phase
        if detail is not None:
            data["detail"] = _short(detail)
        for key, value in kw.items():
            data[key] = value
        _save(data)
        return dict(data)


# --------------------------------------------------------------------------
# 版本与解析（纯函数，不联网 —— 自检直接喂真实 JSON）
# --------------------------------------------------------------------------

def _version_tuple(tag):
    """'V0.4.0' / 'v0.4.0' -> (0, 4, 0)。认不出来返回 None（该 release 跳过）。

    只接受 x.y.z 三段纯数字：预发布后缀（v0.5.0-beta）与本项目无关，
    认不出来就不要猜 —— 猜错会误报有更新。
    """
    text = str(tag or "").strip()
    if text[:1] in ("v", "V"):
        text = text[1:]
    parts = text.split(".")
    if len(parts) != 3:
        return None
    out = []
    for part in parts:
        if not part.isdigit():
            return None
        out.append(int(part))
    return tuple(out)


def _parse_releases(source, payload):
    """把两个源的 release 列表统一成候选列表。

    实测字段：GitHub 与 Gitee 都用 tag_name / prerelease / assets[].name /
    assets[].browser_download_url；draft 只有 GitHub 有。Gitee 的 assets 没有
    size / digest（见模块头），所以这里只取名字和 URL。
    """
    out = []
    if not isinstance(payload, list):
        return out
    for rel in payload:
        if not isinstance(rel, dict):
            continue
        if rel.get("draft") or rel.get("prerelease"):
            continue
        tag = str(rel.get("tag_name") or "").strip()
        version = _version_tuple(tag)
        if version is None:
            continue
        assets = {}
        for item in (rel.get("assets") or []):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if name:
                assets[name] = item
        body = str(rel.get("body") or "")
        out.append({
            "source": source["id"],
            "tag": tag,
            "version": version,
            "version_str": ".".join(str(x) for x in version),
            "notes": body[:_MAX_NOTES],
            "created_at": str(rel.get("created_at") or ""),
            "assets": assets,
        })
    return out


def _pick_newest(candidates):
    """版本最大的那个；同版本时保持先出现的源（GitHub 优先）。"""
    best = None
    for cand in candidates:
        if best is None or cand["version"] > best["version"]:
            best = cand
    return best


def _apk_name(cand):
    """本项目的发布命名约定：tieba-sign-assistant-<ver>-arm64-v8a-release.apk。
    先按约定精确找，再退化成"任意 apk"，最后按约定拼一个出来。"""
    names = list(cand["assets"].keys())
    for name in names:
        if name.endswith("-arm64-v8a-release.apk"):
            return name
    for name in names:
        if name.endswith(".apk"):
            return name
    return "tieba-sign-assistant-{}-arm64-v8a-release.apk".format(
        cand["version_str"])


def _asset_url(source, cand, name):
    item = cand["assets"].get(name)
    if isinstance(item, dict) and item.get("browser_download_url"):
        return str(item["browser_download_url"])
    return source["dl"].format(owner=_load()["owner"], repo=_load()["repo"],
                               tag=cand["tag"], name=name)


def _asset_digest(cand, name):
    """GitHub 的 assets[].digest 形如 'sha256:<hex>'；Gitee 没有这个字段。"""
    item = cand["assets"].get(name)
    if not isinstance(item, dict):
        return None
    digest = str(item.get("digest") or "")
    if digest.startswith("sha256:") and len(digest) == 71:
        return digest[7:].lower()
    return None


def _asset_size(cand, name):
    """GitHub 有 size；Gitee 没有 → 返回 0（用 Content-Length 或 update.json 兜底）。"""
    item = cand["assets"].get(name)
    if not isinstance(item, dict):
        return 0
    try:
        return int(item.get("size") or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------------
# HTTP（stdlib + certifi，避免再引入依赖问题）
# --------------------------------------------------------------------------

def _user_agent():
    return "TiebaSign/{} (Android; +https://github.com/{}/{})".format(
        _APP_VERSION, _load()["owner"], _load()["repo"])


def _ssl_context():
    """Android 上 OpenSSL 的默认 CA 路径常常不存在，必须显式用 certifi 的
    CA bundle（certifi 在 spec 的 requirements 里）。取不到就退回默认上下文，
    再不行返回 None（此时 https 会失败，属可接受降级：检查更新不是核心功能）。"""
    try:
        import ssl
    except Exception:  # noqa: BLE001
        return None
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    try:
        return ssl.create_default_context()
    except Exception:  # noqa: BLE001
        return None


def _open(url, timeout, extra_headers=None):
    headers = {"User-Agent": _user_agent(), "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    ctx = _ssl_context()
    if ctx is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx)


def _http_get(url, timeout):
    resp = _open(url, timeout)
    try:
        return resp.read()
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass


def _http_get_json(url, timeout):
    return json.loads(_http_get(url, timeout).decode("utf-8"))


def _fetch_update_json(source, cand):
    """取 update.json（发布脚本用 aapt2 实测生成）。它是权威来源：
    versionCode / sha256 / apk 名 / 说明。取不到返回 (None, 原因)。"""
    url = _asset_url(source, cand, "update.json")
    try:
        raw = _http_get(url, _UPDATE_JSON_TIMEOUT)
        info = json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return None, "update.json 不可用：{}".format(_short(exc))
    if not isinstance(info, dict):
        return None, "update.json 不是对象"
    try:
        code = int(info.get("versionCode") or 0)
    except (TypeError, ValueError):
        code = 0
    sha = str(info.get("sha256") or "").strip().lower()
    if code <= 0 or len(sha) != 64:
        return None, "update.json 缺少 versionCode / sha256"
    name = str(info.get("apk") or "").strip() or _apk_name(cand)
    try:
        size = int(info.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    return {
        "version": str(info.get("version") or cand["version_str"]),
        "version_code": code,
        "sha256": sha,
        "apk_name": name,
        "size": size,
        "notes": str(info.get("notes") or cand["notes"])[:_MAX_NOTES],
        "min_version_code": int(info.get("minVersionCode") or 0),
    }, None


# --------------------------------------------------------------------------
# Java 桥（复用 autosign 的 boot-classpath 反射，见 weblogin.py 的历史注释）
# --------------------------------------------------------------------------

_METHOD_CACHE = {}


def _jstr(value):
    """把反射回来的 java.lang.String 转成 Python str（不同 pyjnius 版本行为不一）。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    for attr in ("toString",):
        fn = getattr(value, attr, None)
        if callable(fn):
            try:
                return "{}".format(fn())
            except Exception:  # noqa: BLE001
                pass
    return "{}".format(value)


def _call_static_raw(name, args):
    """调用 PythonActivity 的静态方法并**返回原始结果**（可能是 String / boolean）。

    autosign._call_static 会把结果强转成 bool，对 inspectApk 这种返回字符串的
    方法会丢信息，所以这里单独一份，只复用它的类解析与反射补丁。
    """
    try:
        import autosign
        cls, err = autosign._app_class()
    except Exception as exc:  # noqa: BLE001
        return None, "反射桥不可用：{}".format(_short(exc))
    if cls is None:
        return None, err or "activity class unavailable"
    args = list(args or [])
    key = (name, len(args))
    method = _METHOD_CACHE.get(key)
    if method is None:
        try:
            for cand in cls.getMethods():
                if cand.getName() == name and \
                        len(cand.getParameterTypes()) == len(args):
                    method = cand
                    break
        except Exception as exc:  # noqa: BLE001
            return None, "枚举方法失败：{}".format(_short(exc))
        if method is None:
            return None, "PythonActivity.{} 未找到".format(name)
        _METHOD_CACHE[key] = method
    try:
        return method.invoke(None, args), None
    except Exception as exc:  # noqa: BLE001
        return None, "{}".format(_short(exc))


def native_ready():
    """本机是否具备原生安装入口。None = 判断不了（桌面/自检环境）。"""
    if os.name != "posix" or not os.path.exists("/system/build.prop"):
        return None
    res, err = _call_static_raw("installApk", ["/nonexistent"])
    if err and "未找到" in err:
        return False
    return True


def inspect_apk(path):
    """读 APK 的包名 / versionCode / 签名 SHA-256。返回 (info, err)。

    Java 侧返回 "包名|versionCode|签名SHA256"，任一项读不到就是空串。
    """
    res, err = _call_static_raw("inspectApk", [path])
    if err:
        return None, err
    text = _jstr(res)
    parts = text.split("|")
    if len(parts) != 3 or not parts[0]:
        return None, "无法读取 APK 信息（{}）".format(_short(text) or "空结果")
    try:
        code = int(parts[1] or 0)
    except (TypeError, ValueError):
        code = 0
    return {"package": parts[0], "version_code": code,
            "signer": (parts[2] or "").lower()}, None


def _is_on_wifi():
    """True/False/None(判断不了)。方法缺失时按 None 处理 → 视为允许下载
    （fail-open：宁可让用户多下一次，也不要因为读不到网络类型就永远不更新）。"""
    res, err = _call_static_raw("isOnWifi", [])
    if err:
        return None
    try:
        return bool(res)
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# 检查
# --------------------------------------------------------------------------

def _source_by_id(source_id):
    for src in _SOURCES:
        if src["id"] == source_id:
            return src
    return _SOURCES[0]


def _list_url(source):
    data = _load()
    return source["list"].format(owner=data["owner"], repo=data["repo"])


def _normalize_latest(cand, info, source, apk_name, sha256, size):
    return {
        "version": info["version"],
        "version_code": info["version_code"],
        "tag": cand["tag"],
        "source": source["id"],
        "source_label": source["label"],
        "apk_name": apk_name,
        "sha256": sha256,
        "size": int(size or 0),
        "notes": info["notes"],
        "min_version_code": int(info.get("min_version_code") or 0),
        "found_at": _now(),
    }


def _check_worker(by_user):
    """后台线程：查两个源 → 比 versionCode → 决定是否下载。

    自动检查（by_user=False）失败时**完全静默**：不弹通知、不置红点，只写进
    reports / detail 供设置页显示。用户手动点"检查更新"时才把失败原因回显。
    """
    reports = []
    best = None
    try:
        # 这次尝试的时间戳（成功或失败都算），daily_tick 的退避用它。
        _update(last_attempt_ts=_now())
        for source in _SOURCES:
            if _shutdown:
                return
            try:
                payload = _http_get_json(_list_url(source), _CHECK_TIMEOUT)
            except urllib.error.HTTPError as exc:
                # 403/429 = 限流；404 = 仓库/接口不存在。都只是"这次没查到"。
                reports.append({"source": source["id"], "label": source["label"],
                                "status": "error",
                                "detail": "HTTP {}".format(exc.code)})
                continue
            except Exception as exc:  # noqa: BLE001
                reports.append({"source": source["id"], "label": source["label"],
                                "status": "error", "detail": _short(exc)})
                continue
            cands = _parse_releases(source, payload)
            if not cands:
                # Gitee 现在就是这种情况（返回 []）。不是错误，是"这个源没信息"。
                reports.append({"source": source["id"], "label": source["label"],
                                "status": "empty", "detail": "该源还没有发行版"})
                continue
            newest = _pick_newest(cands)
            reports.append({"source": source["id"], "label": source["label"],
                            "status": "ok", "tag": newest["tag"],
                            "version": newest["version_str"],
                            "releases": len(cands)})
            if best is None or newest["version"] > best["version"]:
                best = newest

        summary = {"ts": _now(), "reports": reports}

        if best is None:
            failed = [r for r in reports if r["status"] == "error"]
            detail = "两个源都没查到信息"
            if failed:
                detail = "检查失败：{}".format(
                    "；".join("{} {}".format(r["label"], r.get("detail", ""))
                              for r in failed))
            data = _update(phase="error" if failed else "idle", detail=detail,
                           last_check_ts=summary["ts"], reports=reports,
                           history=([summary] + list(_load()["history"]))[:10])
            if by_user:
                return
            # 自动检查失败 → 静默（不留红点、不发通知）
            _update(dot=False)
            return

        if best["version"] <= _version_tuple(_APP_VERSION):
            _update(phase="idle", detail="已是最新版本 v{}".format(_APP_VERSION),
                    latest=None, dot=False, last_check_date=_today(),
                    last_check_ts=summary["ts"], reports=reports,
                    history=([summary] + list(_load()["history"]))[:10])
            return

        source = _source_by_id(best["source"])
        apk_name = _apk_name(best)
        info, err = _fetch_update_json(source, best)
        # GitHub 的 assets 带 size、Gitee 的没有（实测）。先取一次当兜底，
        # update.json 里有 size 时以它为准。
        size = _asset_size(best, apk_name)

        # ROADMAP 定的规则：**版本比较用 versionCode（数字）**，tag 名只是次要判据。
        # tag 说"更新"而 update.json 里的 versionCode 并不更大时，以 versionCode
        # 为准 —— 否则改错 tag、或把旧包重新打成新 tag，都会骗着用户去装一个
        # 比当前更旧的包（Android 会直接 INSTALL_FAILED_VERSION_DOWNGRADE 拒绝）。
        # 注意这个判定必须在下面"没有 update.json"的兜底**之前**：兜底会把
        # version_code 置 0，放到后面就会被 0 <= 当前 误判成"已是最新"。
        if info is not None and int(info["version_code"] or 0) <= _APP_VERSION_CODE:
            _update(phase="idle",
                    detail="已是最新版本 v{}（{} 上 tag {} 的 versionCode 是 {}，"
                           "并不大于当前的 {}）".format(
                               _APP_VERSION, best["source"], best["tag"],
                               info["version_code"], _APP_VERSION_CODE),
                    latest=None, dot=False, last_check_date=_today(),
                    last_check_ts=summary["ts"], reports=reports,
                    history=([summary] + list(_load()["history"]))[:10])
            return

        if info is None:
            # 该 release 没有 update.json（Gitee 若只放置 APK，或别人的旧 release）。
            # 还能信的就只有 GitHub 的 assets[].digest（sha256）。有 digest 就允许
            # 自动下载（它与 release 同级信任），**一点校验值都没有时只提示、不下载**。
            digest = _asset_digest(best, apk_name)
            info = {"version": best["version_str"], "version_code": 0,
                    "notes": best["notes"], "min_version_code": 0}
            sha256 = digest or ""
            detail = "发现 v{}（{}）".format(best["version_str"],
                                            err or "该 release 没有 update.json")
        else:
            sha256 = info["sha256"]
            size = size or info["size"]
            detail = "发现新版本 v{}".format(info["version"])

        latest = _normalize_latest(best, info, source, apk_name, sha256, size)
        summary["found"] = latest["version"]
        _update(phase="available", detail=detail, latest=latest, dot=True,
                last_check_date=_today(), last_check_ts=summary["ts"],
                reports=reports,
                history=([summary] + list(_load()["history"]))[:10])

        _notify_once(latest)

        data = _load()
        if data["auto_download"] and sha256:
            if data["wifi_only"] and _is_on_wifi() is False:
                _update(detail="发现新版本 v{}；当前不是 Wi-Fi，"
                               "已暂停自动下载（可点「立即下载」）"
                        .format(latest["version"]))
                return
            download_async()
        elif not sha256:
            _update(detail=detail + "；该源没有提供校验值，"
                                    "请手动下载并核对 SHA256SUMS")
    except Exception as exc:  # noqa: BLE001
        _update(phase="error",
                detail="检查更新异常：{} {}".format(type(exc).__name__, _short(exc)))
        print("[updater] check error: {}\n{}".format(exc, traceback.format_exc()))
    finally:
        data = _load()
        if data["phase"] == "checking":
            _update(phase="idle", detail="检查结束")


def _notify_once(latest):
    """同一个版本只弹一次通知，避免每天检查都打扰。"""
    data = _load()
    if data["notified_version"] == latest["version"]:
        return False
    text = "点此下载并安装（当前 v{}）".format(_APP_VERSION)
    res, err = _call_static_raw(
        "notifyUpdateReady",
        ["贴吧签到助手 发现新版本 v{}".format(latest["version"]), text, ""])
    if err:
        # 原生方法还没有（本功能分两步落地）→ 只留红点，设置页里有入口
        print("[updater] notifyUpdateReady 不可用：{}".format(err))
    _update(notified_version=latest["version"])
    return True


def check_async(by_user=False):
    """启动一次后台检查。返回 (started, err)。"""
    global _check_thread
    with _lock:
        if _check_thread is not None and _check_thread.is_alive():
            return False, "正在检查中"
        if _download_thread is not None and _download_thread.is_alive():
            return False, "正在下载，请稍候"
        data = _load()
        data["phase"] = "checking"
        data["detail"] = "正在检查…" if by_user else "正在后台检查…"
        # 刻意**不**在这里写 last_check_date：只有真查到结果才算"今天查过了"。
        # 否则装在没网的地方/刚好撞上限流一次，这一整天就再也不会重试。
        # last_check_date 与 last_attempt_ts 都由 _check_worker 在结束时写，
        # 保证"这一次尝试"只有单一口径；失败后的重试由 daily_tick 的
        # _CHECK_RETRY_SEC 退避控制。
        _save(data)
    _check_thread = threading.Thread(target=_check_worker, args=(bool(by_user),),
                                     name="update-check", daemon=True)
    _check_thread.start()
    return True, None


# --------------------------------------------------------------------------
# 下载
# --------------------------------------------------------------------------

def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(_CHUNK)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def _download_dir():
    return os.path.join(
        os.environ.get("ANDROID_PRIVATE") or os.getcwd(), "update")


def _candidates(latest):
    """要尝试的下载地址，按 download_mode 排序。

    auto = GitHub 优先、下太慢自动切 Gitee（"让 GitHub 下载计数好看"与"国内
    下载别受罪"的折中：GitHub 正常时计数会涨，网络差时自动让路）。
    """
    data = _load()
    mode = data["download_mode"]
    order = ["github", "gitee"] if mode == "auto" else [mode]
    primary = latest.get("source")
    out = []
    for source_id in order:
        if source_id == primary:
            continue
        source = _source_by_id(source_id)
        out.append((source_id, source["dl"].format(
            owner=data["owner"], repo=data["repo"], tag=latest["tag"],
            name=latest["apk_name"])))
    # 发现更新的那个源排第一。**但只有它在允许的 order 里才插**：用户把
    # 「下载来源」钉成"只用 Gitee"时，绝不能因为发现源是 GitHub 就先去连 GitHub。
    if primary and primary in order:
        source = _source_by_id(primary)
        out.insert(0, (primary, source["dl"].format(
            owner=data["owner"], repo=data["repo"], tag=latest["tag"],
            name=latest["apk_name"])))
    return out


def _progress_text(version, got, declared):
    """下载进度文案。declared=0（服务端没给 Content-Length）时只说已下多少。"""
    mb = 1024 * 1024
    if declared > 0:
        pct = min(100, int(got * 100 // declared))
        return "正在下载 v{}… {}%（{} / {} MB）".format(
            version, pct, got // mb, max(1, declared // mb))
    return "正在下载 v{}… 已下 {} MB".format(version, got // mb)


def _stream_to(url, dest, expect_sha, probe=False, progress=None):
    """下载到 dest。probe=True 时前 _PROBE_SEC 秒做测速判定。

    返回 (状态, 详情)：状态是 ok / slow / error。**验证在写盘之后、改名之前**
    做，所以半截文件永远不会被当成可用安装包。

    progress(got, declared) 每秒最多回调一次 —— 它去写状态文件，不能每 64KB
    就写一遍盘。
    """
    started = time.time()
    digest = hashlib.sha256()
    total = 0
    last_report = 0.0
    resp = _open(url, _DOWNLOAD_TIMEOUT)
    try:
        declared = resp.headers.get("Content-Length")
        declared = int(declared) if (declared or "").isdigit() else 0
        if declared and not (_MIN_APK_BYTES <= declared <= _MAX_APK_BYTES):
            return "error", "包大小异常（{} 字节）".format(declared)
        with open(dest, "wb") as fh:
            while True:
                block = resp.read(_CHUNK)
                if not block:
                    break
                fh.write(block)
                digest.update(block)
                total += len(block)
                if progress is not None and time.time() - last_report >= 1.0:
                    last_report = time.time()
                    try:
                        progress(total, declared)
                    except Exception:  # noqa: BLE001 - 进度回调绝不能中断下载
                        pass
                if total > _MAX_APK_BYTES:
                    return "error", "包超过 {} 字节，拒绝继续".format(_MAX_APK_BYTES)
                if probe and time.time() - started >= _PROBE_SEC:
                    if total < _PROBE_MIN_BYTES:
                        return "slow", "{} 秒只下了 {} KB".format(
                            _PROBE_SEC, total // 1024)
                    probe = False   # 速度合格 → 之后不再判定
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

    if total < _MIN_APK_BYTES:
        return "error", "下载不完整（只有 {} 字节）".format(total)
    got = digest.hexdigest()
    if expect_sha and got != expect_sha:
        return "error", "校验失败：期望 {}…，实际 {}…".format(
            expect_sha[:12], got[:12])
    return "ok", {"sha256": got, "size": total}


def _download_worker():
    try:
        latest = _load()["latest"]
        if not latest:
            _update(phase="idle", detail="没有已知的新版本")
            return
        if not latest.get("sha256"):
            _update(phase="available",
                    detail="这个源没有提供校验值，不能自动下载")
            return

        os.makedirs(_download_dir(), exist_ok=True)
        final = os.path.join(_download_dir(), latest["apk_name"])
        part = final + ".part"
        tried = []
        cands = _candidates(latest)
        for idx, (source_id, url) in enumerate(cands):
            _update(phase="downloading",
                    detail="正在从 {} 下载 v{}…".format(
                        _source_by_id(source_id)["label"], latest["version"]))
            # 测速只用在"非最后一个"候选上：最后一个必须允许它慢慢下完，
            # 否则网络普遍慢的时候会一个源都下不完。auto 模式因此会真的先测
            # GitHub（用户要的下载计数），太慢才切 Gitee。
            try:
                state, detail = _stream_to(
                    url, part, latest["sha256"],
                    probe=(idx < len(cands) - 1),
                    progress=lambda got, decl: _update(detail=_progress_text(
                        latest["version"], got, decl)))
            except Exception as exc:  # noqa: BLE001
                state, detail = "error", _short(exc)
            if state == "ok":
                os.replace(part, final)
                _update(phase="ready",
                        detail="v{} 已下载并通过校验".format(latest["version"]),
                        downloaded={"path": final, "name": latest["apk_name"],
                                    "sha256": detail["sha256"],
                                    "size": detail["size"],
                                    "version": latest["version"],
                                    "version_code": latest["version_code"],
                                    "source": source_id,
                                    "ts": _now()},
                        dot=True)
                _notify_downloaded()
                return
            tried.append("{}：{}".format(_source_by_id(source_id)["label"], detail))
            try:
                if os.path.exists(part):
                    os.remove(part)
            except OSError:
                pass
        _update(phase="available",
                detail="下载失败（{}）".format("；".join(tried)))
    except Exception as exc:  # noqa: BLE001
        _update(phase="error",
                detail="下载异常：{} {}".format(type(exc).__name__, _short(exc)))
        print("[updater] download error: {}\n{}".format(exc, traceback.format_exc()))


def _notify_downloaded():
    latest = _load().get("latest") or {}
    path = (_load().get("downloaded") or {}).get("path") or ""
    if not path:
        return
    res, err = _call_static_raw(
        "notifyUpdateReady",
        ["贴吧签到助手 v{} 已下载".format(latest.get("version", "")),
         "点此打开系统安装界面", path])
    if err:
        print("[updater] notifyUpdateReady 不可用：{}".format(err))


def download_async():
    global _download_thread
    with _lock:
        if _download_thread is not None and _download_thread.is_alive():
            return False, "正在下载中"
        if _check_thread is not None and _check_thread.is_alive():
            return False, "正在检查，请稍候"
        data = _load()
        if not data["latest"]:
            return False, "没有已知的新版本"
        if not data["latest"].get("sha256"):
            return False, "该源没有提供校验值，不能自动下载"
        data["phase"] = "downloading"
        data["detail"] = "开始下载…"
        _save(data)
    _download_thread = threading.Thread(target=_download_worker,
                                        name="update-download", daemon=True)
    _download_thread.start()
    return True, None


# --------------------------------------------------------------------------
# 安装（唤醒系统安装界面，不做静默安装）
# --------------------------------------------------------------------------

def install():
    """把已下载的包交给系统安装器。**装之前再验一遍**（包名/签名/versionCode）。

    sha256 来自发布端，单靠它挡不住被篡改的发布端；签名校验才是真正的边界。
    """
    data = _load()
    done = data.get("downloaded")
    if not done:
        return False, "还没有下载好的安装包"
    path = done.get("path") or ""
    if not os.path.isfile(path):
        _update(phase="available", detail="安装包不见了，请重新下载",
                downloaded=None)
        return False, "安装包不存在，请重新下载"

    info, err = inspect_apk(path)
    if info is None:
        return False, "无法校验安装包：{}".format(err)
    if info["package"] != _PACKAGE:
        _reject(path, "包名不符（{}）".format(info["package"]))
        return False, "安装包不是本应用，已删除"
    if info["signer"] and info["signer"] != _SIGNER_SHA256:
        _reject(path, "签名不符（{}…）".format(info["signer"][:12]))
        return False, "安装包签名与本应用不一致，已删除"
    if info["version_code"] and info["version_code"] <= _APP_VERSION_CODE:
        _reject(path, "versionCode {} 不大于当前 {}".format(
            info["version_code"], _APP_VERSION_CODE))
        return False, "该包不比当前版本新，已删除"

    ok, err = _call_static_raw("installApk", [path])
    if err:
        return False, "无法打开系统安装界面：{}".format(err)
    if ok is not None and not bool(ok):
        return False, "系统拒绝打开安装界面（可能未允许「安装未知应用」）"
    _update(detail="已交给系统安装界面（v{}）".format(done.get("version", "")))
    return True, None


def _reject(path, why):
    """验不过就删掉，绝不把来源不明的东西留在手机上。"""
    try:
        os.remove(path)
    except OSError:
        pass
    _update(phase="available", detail="安装包被拒绝并删除：{}".format(why),
            downloaded=None)
    print("[updater] rejected downloaded apk: {}".format(why))


def needs_unknown_sources():
    """Android 8+ 需要用户允许"安装未知应用"。Java 侧在 installApk 里自行
    判断并跳转设置；这里只暴露给界面做提示。"""
    data = _load()
    return bool(data.get("downloaded"))


# --------------------------------------------------------------------------
# 设置 / 状态 / 每日调度
# --------------------------------------------------------------------------

def get_settings():
    data = _load()
    return {
        "auto_check": bool(data["auto_check"]),
        "auto_download": bool(data["auto_download"]),
        "wifi_only": bool(data["wifi_only"]),
        "download_mode": data["download_mode"],
        "owner": data["owner"],
        "repo": data["repo"],
        "limits": {"probe_sec": _PROBE_SEC,
                   "probe_min_kb": _PROBE_MIN_BYTES // 1024},
    }


def set_settings(patch):
    if not isinstance(patch, dict):
        return False, "参数无效"
    with _lock:
        data = _load()
        for key in ("auto_check", "auto_download", "wifi_only"):
            if key in patch:
                data[key] = bool(patch[key])
        mode = patch.get("download_mode")
        if mode is not None:
            if mode not in ("auto", "github", "gitee"):
                return False, "下载来源只能是 自动/GitHub/Gitee"
            data["download_mode"] = mode
        _save(data)
    return True, None


def get_state():
    data = _load()
    phase = data["phase"]
    if _check_thread is not None and _check_thread.is_alive():
        phase = "checking"
    elif _download_thread is not None and _download_thread.is_alive():
        phase = "downloading"
    return {
        "phase": phase,
        "detail": data["detail"],
        "dot": bool(data["dot"]),
        "latest": data["latest"],
        "downloaded": data["downloaded"],
        "reports": data["reports"],
        "history": data["history"],
        "last_check_date": data["last_check_date"],
        "last_check_ts": data["last_check_ts"],
        "settings": get_settings(),
        "current": {"version": _APP_VERSION, "version_code": _APP_VERSION_CODE,
                    "package": _PACKAGE},
        "sources": [{"id": s["id"], "label": s["label"]} for s in _SOURCES],
        # 本机是否具备原生安装入口；None = 判断不了（自检/桌面）
        "native_ready": native_ready(),
        "native_note": ("安装入口尚未就位（本版先做检查与下载）"
                        if native_ready() is False else ""),
    }


def dot_pending():
    """设置页是否该显示红点（与 autosign.dot_pending 同名同义）。

    只有"查到了新版本"或"新版本下载好了"才置位；检查失败**不置位**，
    所以没查到东西时界面上什么都不会多出来 —— 这是用户明确要求的。
    """
    try:
        return bool(_load()["dot"])
    except Exception:  # noqa: BLE001
        return False


def clear_dot():
    with _lock:
        data = _load()
        if data["dot"]:
            data["dot"] = False
            _save(data)


def _age_sec(ts):
    """'YYYY-MM-DD HH:MM:SS' 距现在多少秒；读不出来返回 None。"""
    if not ts:
        return None
    try:
        return max(0.0, time.time() - time.mktime(
            time.strptime(str(ts), "%Y-%m-%d %H:%M:%S")))
    except (ValueError, TypeError):
        return None


def daily_tick():
    """由 autosign 的调度循环每 20 秒调一次；自己决定现在要不要真查。

    刻意由这里（而不是新起一个线程）驱动：自动签到的循环已经在跑，多一个
    定时器只会多一处可能被系统回收的东西。

    两次门禁：成功的检查当天只做一次（last_check_date==今天 → 停）；失败的
    检查要等 _CHECK_RETRY_SEC 才重试（避免每 20 秒撞一次限流）。
    """
    try:
        data = _load()
        if not data["auto_check"]:
            return False
        if data["last_check_date"] == _today():
            return False
        age = _age_sec(data["last_attempt_ts"])
        if age is not None and age < _CHECK_RETRY_SEC:
            return False
        if _check_thread is not None and _check_thread.is_alive():
            return False
        if _download_thread is not None and _download_thread.is_alive():
            return False
        started, _err = check_async(by_user=False)
        return bool(started)
    except Exception as exc:  # noqa: BLE001
        print("[updater] daily_tick error: {} {}".format(type(exc).__name__, exc))
        return False


_shutdown = False


def selftest():
    """启动时确认数据目录可写（与 storage.selftest / waterpost.selftest 同样作用）。"""
    with _lock:
        data = _load()
        _save(data)
        return _data_path()
