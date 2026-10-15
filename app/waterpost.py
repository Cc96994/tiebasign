# -*- coding: utf-8 -*-
"""TiebaSign v0.4.0 - 自动水贴 + 手动一键水贴。

用户需求（2026-10-15 确认第二轮）
  * 每个账号带**自己的一组目标帖**：一个账号只发自己那组帖（不共用一个目标列表）
  * 固定间隔默认 **5 秒**，可调 5-3600 秒，不随机
  * 次数默认 4 次/天（每个目标帖），可调
  * 内容用户自定义，内置 10 套默认文案；轮换方式可选顺序/随机
  * 错过的次数默认**不补发**，但用户可以自己打开「补发」
  * 签到页提供「一键水贴」：对勾选的每个账号，把它**当天还没发满的次数**一次
    发完（设置 4 次/天 → 每个启用的目标帖补足到 4 条）。发满后当天自动计划也
    不会再发 —— 手动与自动共用同一份"今天发了几条"的账，一天只有一份额度。
  * 发送方式：主楼回复（楼中楼不可用，见下）

能力边界（已核实 vendor/aiotieba-4.7.1.tar.gz 源码，请勿凭印象改动）
  * 主楼回复（新增楼层）= Client.add_post(fname, tid, content)        -> 可用
  * 楼中楼                      -> aiotieba 4.7.1 **没有**创建楼中楼的接口：
      - client.py 只有 get_comments / SubPostList_pb2，两者都只读
      - api/add_post/protobuf/AddPostReqIdl.proto 的全部字段中没有
        pid / quote_id / is_comment，应用协议 cmd=309731 造不出楼中楼
    所以 reply_mode="floor" 现在返回明确的“暂不支持”错误，绝不假装成功。
  * **不要升级 aiotieba**：上游 v4.8.1 已经把 api/add_post 整个删掉（404），
    升上去连主楼回复都会消失。vendor/aiotieba-4.7.1.tar.gz 属于必须锁住的依赖。

与自动签到的关系
  * 复用同一个 aiotieba 事件循环（tieba_api._ensure_loop），不另起线程池
  * **每个账号各自**在“它自己当天完成过一次签到（status=done）”之后才开始；
    自动签到与手动签到都算 —— 通过轮询 signer.get_state() 判定，不做代码耦合
  * 保活：autosign 的调度循环会检查 keepalive_needed()，水贴开启（或手动水贴
    正在执行）时同样保持前台服务，避免两次发送之间被系统回收

安全与风控（以下都是刻意设计，不是遗漏）
  * BDUSS 只在使用瞬间从 storage + keystore_crypto 取出，本文件不落盘任何凭据
  * 错过的次数**默认不补发**：App 重开后集中连发是最明显的机器特征。用户可以
    打开「补发」（makeup），此时欠下的次数会按固定间隔逐条补上（每 tick 至多
    补 _MAX_TICK_SENDS 条，避免一次爆量）
  * 同一条文案不会连续重复（轮换时自动跳过与上一条相同的那条）
  * 遇到硬性失败（需要验证码 / 账号失效 / 被限制风控 / 暂不支持）时，**该账号
    当天全部目标帖**都停止发送，避免继续撞风控；网络异常则继续（属临时问题）
  * 手动一键水贴遇到硬性失败时，同样把该账号当天的自动计划一起停掉
  * 手动**只在成功时**占用当天次数（自动计划相反：每次尝试都算，用来给自己的
    重试封顶）。手动是用户点出来的，一次网络抖动不该白丢当天的额度 —— 失败
    不扣账，再点一次就能补上
  * add_post 官方文档原文：高频率调用会导致<永久封禁屏蔽>。界面必须提示。
  * 自动与手动发送共用 _send_lock 串行化；手动执行期间自动调度让路（不丢次数，
    只是推迟）

代码结构提示（改之前先看这里）
  * 计划与结果的键统一是 "<account_id>:<target_id>"（见 _plan_key），因为同一个
    主题帖可以同时出现在两个账号的目标组里
  * 单次发送最长阻塞 _SEND_TIMEOUT 秒，期间用户完全可能改设置。所有“发送后落盘”
    都必须重新 _load() 再合并进度，不能直接 _save(旧快照)，见 _tick/_manual_loop

数据文件：<ANDROID_PRIVATE>/tiebasign_waterpost.json（不含 BDUSS）
"""

import asyncio
import json
import os
import random
import re
import threading
import time
import traceback

_DATA_FILENAME = "tiebasign_waterpost.json"
_SCHEMA = 2                 # 2 = 每账号一组目标帖 + 秒级间隔 + 补发开关

# 调度参数
_FIRST_DELAY_SEC = 60      # 签到完成后首次发送前的固定延迟
_GRACE_SEC = 300           # 迟到超过“一个间隔+抖动”即视为错过（App 被关过）
_TICK_SEC = 20             # 与自动签到同频
_MAX_TICK_SENDS = 10       # 单个 tick 内最多发几条（防一次爆量）
_SEND_TIMEOUT = 90         # 单次发送等待上限（秒）

# 输入限制
_MAX_COUNT = 50
_MIN_INTERVAL_SEC = 5
_MAX_INTERVAL_SEC = 3600
_MAX_TEXTS = 30
_MAX_TEXT_LEN = 100
_MAX_TARGETS_PER_GROUP = 20
_MAX_MANUAL_ITEMS = 300    # 手动一键水贴一次最多发几条回复（不是几个目标帖）
_MANUAL_KEEP_ITEMS = 200   # 手动结果明细最多保留多少条给界面

# 默认文案：10 套，11-13 字，口语化、彼此不同（重复内容本身是风控特征）
_DEFAULT_TEXTS = [
    "前排围观，支持一下楼主",
    "路过留个脚印，顺便混个脸熟",
    "写得挺好，感谢楼主分享",
    "日常冒泡，顺便来打个卡",
    "来晚了，帮忙顶一下这个帖子",
    "有点意思，先收藏了慢慢看",
    "支持楼主，希望继续保持更新",
    "看完了，整体感觉还不错",
    "顶帖，希望楼主多更新内容",
    "经验加三，告辞",
]

_FLOOR_UNSUPPORTED = (
    "楼中楼暂不可用：所带 aiotieba 4.7.1 没有创建楼中楼的接口"
    "（其 add_post 协议字段里没有 pid/quote_id）。"
    "需要拿到新的可用接口并实测通过后才会开放。"
)

_NET_HINTS = (
    "ClientConnector", "ClientOSError", "ClientConnection", "ConnectionError",
    "TimeoutError", "Timeout", "SSLCertVerificationError", "SSLError",
    "CertificationError", "SocketError", "Socket", "OSError",
)
# 遇到这些类别，当天对该账号停止发送（继续撞没有意义，且会加重风控）
_HARD_STOP_CATEGORIES = ("需要验证码", "账号失效", "被限制/风控", "暂不支持")

_TID_RE = re.compile(r"/p/(\d{5,20})")
_PID_RE = re.compile(r"[?&#]pid=(\d{5,20})")
_BARE_TID_RE = re.compile(r"^\d{5,20}$")

_DEFAULTS = {
    "version": _SCHEMA,
    "enabled": False,
    "daily_count": 4,          # 每个目标帖每天发几次
    "interval_sec": 5,         # 两条回复之间的固定间隔（秒，不随机）
    "reply_mode": "thread",    # thread=主楼回复（新楼层）；floor=楼中楼（暂不支持）
    "template_mode": "sequence",   # sequence=按顺序轮换；random=随机挑
    "texts": list(_DEFAULT_TEXTS),
    "makeup": False,           # 补发：默认关（开=欠下的次数按间隔补上）
    "groups": [],              # [{"account_id": int, "targets": [目标帖, ...]}]
    "text_seq": 0,             # 文案轮换用的全局序号
    "last_text": "",           # 上一条发出去的文案（防连续重复）
    "plans": {},               # {"<account>:<target>": {date,sent,skipped,next_ts,...}}
    "results": {},             # {"<account>:<target>": 最近一次结果快照}
    "last_test": None,
}

_lock = threading.RLock()
_cache = None
_thread = None
_send_lock = threading.Lock()      # 自动与手动发送串行化

# 手动一键水贴的运行状态（只在内存里，进程重启即清空）
_manual_lock = threading.RLock()
_manual = {
    "run_id": 0,
    "status": "idle",          # idle / running / done
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "finished": 0,
    "success": 0,
    "failed": 0,
    "current": "",
    "items": [],
    "note": "",
}


# --------------------------------------------------------------------------
# 存储（沿用 storage.py / autosign.py 的写法：原子 tmp + os.replace）
# --------------------------------------------------------------------------

def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _log(msg):
    try:
        with open(os.path.join(os.getcwd(), "waterpost.log"), "a",
                  encoding="utf-8") as fh:
            fh.write("[{}] {}\n".format(time.strftime("%m-%d %H:%M:%S"), msg))
            fh.flush()
    except Exception:
        pass


def _today():
    return time.strftime("%Y-%m-%d")


def _clamp_int(value, low, high, fallback):
    try:
        num = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, num))


def _normalize_texts(raw):
    """返回清洗后的文案列表；输入不可用时回落到默认 10 套。"""
    out = []
    if isinstance(raw, list):
        for item in raw:
            text = str(item or "").strip()
            if not text:
                continue
            out.append(text[:_MAX_TEXT_LEN])
            if len(out) >= _MAX_TEXTS:
                break
    return out or list(_DEFAULT_TEXTS)


def _normalize_target(raw):
    try:
        tid = int(raw.get("tid", 0))
    except (TypeError, ValueError):
        return None
    if tid <= 0:
        return None
    pid = raw.get("pid")
    try:
        pid = int(pid) if pid not in (None, "", 0, "0") else None
    except (TypeError, ValueError):
        pid = None
    try:
        target_id = int(raw.get("id", 0))
    except (TypeError, ValueError):
        return None
    if target_id <= 0:
        return None
    return {
        "id": target_id,
        "url": str(raw.get("url", ""))[:300],
        "tid": tid,
        "pid": pid,
        "fname": str(raw.get("fname", ""))[:60],
        "title": str(raw.get("title", ""))[:120],
        "enabled": bool(raw.get("enabled", True)),
        "added_at": int(raw.get("added_at", 0) or 0),
    }


def _normalize_group(raw):
    try:
        account_id = int(raw.get("account_id", 0))
    except (TypeError, ValueError):
        return None
    if account_id <= 0:
        return None
    targets = []
    seen = set()
    for item in raw.get("targets") or []:
        if not isinstance(item, dict):
            continue
        norm = _normalize_target(item)
        if norm is None or norm["tid"] in seen:
            continue
        seen.add(norm["tid"])
        targets.append(norm)
        if len(targets) >= _MAX_TARGETS_PER_GROUP:
            break
    return {"account_id": account_id, "targets": targets}


def _migrate(loaded, data):
    """把任意历史版本的 store 转成当前 schema。

    schema 1（2026-10-15 之前）：单个 account_id + 全局 targets +
    interval_minutes。迁移时保留账号与它的目标帖，但**间隔改用新默认值**：
    0.4.0 起默认 5 秒（用户明确要求），把旧的 60 分钟带过来只会让人以为
    “默认没改”。文案、开关、次数、轮换方式则原样保留。
    """
    for key, value in _DEFAULTS.items():
        if key in ("version", "texts", "groups", "plans", "results"):
            continue
        if key in loaded and isinstance(loaded[key], type(value)):
            data[key] = loaded[key]
    data["texts"] = _normalize_texts(loaded.get("texts"))
    data["daily_count"] = _clamp_int(data["daily_count"], 1, _MAX_COUNT, 4)
    data["interval_sec"] = _clamp_int(
        data["interval_sec"], _MIN_INTERVAL_SEC, _MAX_INTERVAL_SEC, 5)
    data["enabled"] = bool(data["enabled"])
    data["makeup"] = bool(data["makeup"])
    if data["reply_mode"] not in ("thread", "floor"):
        data["reply_mode"] = "thread"
    if data["template_mode"] not in ("sequence", "random"):
        data["template_mode"] = "sequence"
    data["text_seq"] = _clamp_int(loaded.get("text_seq"), 0, 10 ** 9, 0)
    data["last_text"] = str(loaded.get("last_text") or "")[:_MAX_TEXT_LEN]
    data["last_test"] = loaded.get("last_test") \
        if isinstance(loaded.get("last_test"), dict) else None

    raw_groups = loaded.get("groups")
    legacy_account = None
    if isinstance(raw_groups, list):
        groups = []
        seen_acct = set()
        for raw in raw_groups:
            if not isinstance(raw, dict):
                continue
            norm = _normalize_group(raw)
            if norm is None or norm["account_id"] in seen_acct:
                continue
            seen_acct.add(norm["account_id"])
            groups.append(norm)
    else:
        # 老结构：单个账号 + 全局目标帖
        groups = []
        account_id = loaded.get("account_id")
        try:
            account_id = int(account_id) if account_id not in (None, "", "null") \
                else None
        except (TypeError, ValueError):
            account_id = None
        targets = []
        seen = set()
        for raw in loaded.get("targets") or []:
            if not isinstance(raw, dict):
                continue
            norm = _normalize_target(raw)
            if norm is None or norm["tid"] in seen:
                continue
            seen.add(norm["tid"])
            targets.append(norm)
        if account_id is not None and targets:
            groups.append({"account_id": account_id, "targets": targets})
            legacy_account = account_id
            _log("migrated {} target(s) to account {} (interval -> {}s)".format(
                len(targets), account_id, data["interval_sec"]))
    data["groups"] = groups

    # 计划与结果只保留仍然存在的 (账号, 目标) 键；老的无前缀键按迁移出的账号补前缀
    valid = set()
    for group in groups:
        for target in group["targets"]:
            valid.add(_plan_key(group["account_id"], target["id"]))

    def _keep(raw):
        out = {}
        for key, value in (raw or {}).items():
            if not isinstance(value, dict):
                continue
            name = str(key)
            if name not in valid and ":" not in name and legacy_account is not None:
                name = _plan_key(legacy_account, name)
            if name in valid:
                out[name] = value
        return out

    data["plans"] = _keep(loaded.get("plans"))
    data["results"] = _keep(loaded.get("results"))
    data["version"] = _SCHEMA
    return data


def _load():
    global _cache
    with _lock:
        if _cache is not None:
            return json.loads(json.dumps(_cache))
        data = json.loads(json.dumps(_DEFAULTS))
        try:
            with open(_data_path(), "r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if not isinstance(loaded, dict):
                loaded = {}
            data = _migrate(loaded, data)
        except FileNotFoundError:
            pass
        except Exception as exc:  # noqa: BLE001 - 配置损坏则回到默认值
            _log("settings load failed, using defaults: {} {}".format(
                type(exc).__name__, exc))
        _cache = data
        return json.loads(json.dumps(data))


def _save(data):
    global _cache
    with _lock:
        _cache = json.loads(json.dumps(data))
        path = _data_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def selftest():
    """启动时确认数据目录可写（与 storage.selftest 同样的作用）。"""
    with _lock:
        data = _load()
        _save(data)
        return _data_path()


# --------------------------------------------------------------------------
# 纯函数：链接解析（可单测）
# --------------------------------------------------------------------------

def parse_target(text):
    """从粘贴内容中取出 (tid, pid, err)。

    接受：完整链接 / App 分享链接 / 带 pid 的楼中楼链接 / 纯数字 tid。
    pid 只用于将来的楼中楼模式，现在解析出来只为展示与留存。
    """
    raw = str(text or "").strip()
    if not raw:
        return None, None, "请先粘贴主题帖链接"
    if _BARE_TID_RE.match(raw):
        return int(raw), None, None
    match = _TID_RE.search(raw)
    if not match:
        return None, None, "没能在链接里找到 /p/<数字>，请确认复制的是主题帖链接"
    tid = int(match.group(1))
    pid = None
    pmatch = _PID_RE.search(raw)
    if pmatch:
        pid = int(pmatch.group(1))
    return tid, pid, None


# --------------------------------------------------------------------------
# 账号与目标组
# --------------------------------------------------------------------------

def _plan_key(account_id, target_id):
    """计划/结果的键。必须带账号，因为同一个帖可能在两个账号的组里。"""
    return "{}:{}".format(int(account_id), int(target_id))


def _find_group(data, account_id):
    if account_id is None:
        return None
    try:
        want = int(account_id)
    except (TypeError, ValueError):
        return None
    for group in data["groups"]:
        if int(group.get("account_id", -1)) == want:
            return group
    return None


def _ensure_group(data, account_id):
    group = _find_group(data, account_id)
    if group is None:
        group = {"account_id": int(account_id), "targets": []}
        data["groups"].append(group)
    return group


def _all_targets(data):
    """迭代 (group, target)。"""
    for group in data["groups"]:
        for target in group.get("targets") or []:
            yield group, target


def _next_target_id(data):
    top = 0
    for _group, target in _all_targets(data):
        try:
            top = max(top, int(target.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return top + 1


def _accounts_map():
    """{account_id: {"name":.., "mask":..}}；storage 不可用时返回 {}。"""
    try:
        import storage
        accounts = storage.list_accounts() or []
    except Exception as exc:  # noqa: BLE001
        _log("list_accounts failed: {}: {}".format(type(exc).__name__, exc))
        return {}
    out = {}
    for acct in accounts:
        try:
            out[int(acct["id"])] = {
                "name": str(acct.get("name") or "贴吧账号"),
                "mask": str(acct.get("mask") or ""),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return out


# --------------------------------------------------------------------------
# 设置 API
# --------------------------------------------------------------------------

def default_texts():
    return list(_DEFAULT_TEXTS)


def floor_supported():
    return False


def get_settings():
    """公开设置（不含任何凭据），供界面渲染。"""
    data = _load()
    today = _today()
    count = int(data["daily_count"])
    accounts = _accounts_map()

    groups_out = []
    enabled_total = 0
    for group in data["groups"]:
        aid = int(group["account_id"])
        meta = accounts.get(aid)
        targets_out = []
        enabled_here = 0
        for target in group.get("targets") or []:
            key = _plan_key(aid, target["id"])
            plan = data["plans"].get(key) or {}
            same_day = plan.get("date") == today
            if target.get("enabled", True):
                enabled_here += 1
            targets_out.append({
                "id": target["id"],
                "url": target["url"],
                "tid": target["tid"],
                "pid": target["pid"],
                "fname": target["fname"],
                "title": target["title"],
                "enabled": bool(target.get("enabled", True)),
                "added_at": target["added_at"],
                "today_sent": int(plan.get("sent", 0)) if same_day else 0,
                "today_skipped": int(plan.get("skipped", 0)) if same_day else 0,
                "stopped": plan.get("stopped") if same_day else None,
                "last": data["results"].get(key) or None,
            })
        enabled_total += enabled_here
        groups_out.append({
            "account_id": aid,
            "account_name": (meta or {}).get("name") or "",
            "account_mask": (meta or {}).get("mask") or "",
            "orphan": meta is None,
            "targets": targets_out,
            "enabled_target_count": enabled_here,
            "expected_daily_total": enabled_here * count,
        })

    return {
        "enabled": bool(data["enabled"]),
        "daily_count": count,
        "interval_sec": int(data["interval_sec"]),
        "reply_mode": data["reply_mode"],
        "template_mode": data["template_mode"],
        "makeup": bool(data["makeup"]),
        "texts": list(data["texts"]),
        "groups": groups_out,
        "account_count": len([g for g in groups_out if not g["orphan"]]),
        "enabled_target_count": enabled_total,
        "expected_daily_total": enabled_total * count,
        "floor_supported": False,
        "floor_note": _FLOOR_UNSUPPORTED,
        "last_test": data["last_test"],
        "limits": {
            "max_count": _MAX_COUNT,
            "max_interval_sec": _MAX_INTERVAL_SEC,
            "min_interval_sec": _MIN_INTERVAL_SEC,
            "max_texts": _MAX_TEXTS,
            "max_text_len": _MAX_TEXT_LEN,
            "max_targets_per_group": _MAX_TARGETS_PER_GROUP,
            "max_manual_items": _MAX_MANUAL_ITEMS,
            "max_tick_sends": _MAX_TICK_SENDS,
            "first_delay_sec": _FIRST_DELAY_SEC,
        },
    }


def save_settings(payload):
    """保存设置。文案为空时抛 ValueError（由 HTTP 层转成界面提示）。"""
    data = _load()
    if "enabled" in payload:
        data["enabled"] = bool(payload["enabled"])
    if "makeup" in payload:
        data["makeup"] = bool(payload["makeup"])
    if "daily_count" in payload:
        data["daily_count"] = _clamp_int(payload["daily_count"],
                                         1, _MAX_COUNT, 4)
    if "interval_sec" in payload:
        data["interval_sec"] = _clamp_int(payload["interval_sec"],
                                          _MIN_INTERVAL_SEC,
                                          _MAX_INTERVAL_SEC, 5)
    if payload.get("reply_mode") in ("thread", "floor"):
        data["reply_mode"] = payload["reply_mode"]
    if payload.get("template_mode") in ("sequence", "random"):
        data["template_mode"] = payload["template_mode"]
    if "texts" in payload:
        texts = []
        raw = payload["texts"]
        if isinstance(raw, list):
            for item in raw:
                text = str(item or "").strip()
                if not text:
                    continue
                if len(text) > _MAX_TEXT_LEN:
                    raise ValueError("单条文案不能超过 {} 字".format(_MAX_TEXT_LEN))
                texts.append(text)
        if not texts:
            raise ValueError("至少要保留一条文案")
        if len(texts) > _MAX_TEXTS:
            raise ValueError("最多 {} 条文案".format(_MAX_TEXTS))
        data["texts"] = texts
    _save(data)
    _log("settings saved enabled={} count={} interval={}s makeup={} mode={}".format(
        data["enabled"], data["daily_count"], data["interval_sec"],
        data["makeup"], data["reply_mode"]))
    return get_settings()


# --------------------------------------------------------------------------
# 目标帖管理（都带 account_id：每个账号只管自己那组）
# --------------------------------------------------------------------------

def list_targets():
    out = []
    for group in get_settings()["groups"]:
        for target in group["targets"]:
            item = dict(target)
            item["account_id"] = group["account_id"]
            out.append(item)
    return out


def add_target(url, account_id):
    """解析并添加一个主题帖到指定账号的目标组。阻塞（要联网解析吧名）。"""
    tid, pid, err = parse_target(url)
    if err:
        return None, err
    if account_id is None:
        return None, "请先选择要添加到的账号"
    try:
        account_id = int(account_id)
    except (TypeError, ValueError):
        return None, "账号选择无效"
    data = _load()
    accounts = _accounts_map()
    if accounts and account_id not in accounts:
        return None, "这个账号不存在，请刷新页面后重试"
    group = _find_group(data, account_id)
    if group is not None:
        if len(group["targets"]) >= _MAX_TARGETS_PER_GROUP:
            return None, "单个账号最多添加 {} 个主题帖".format(
                _MAX_TARGETS_PER_GROUP)
        for target in group["targets"]:
            if int(target["tid"]) == tid:
                return None, "这个主题帖已经在这个账号的列表里了"
    bduss, err = _bduss_for(account_id)
    if err:
        return None, err
    info, err = _resolve_target(bduss, tid)
    if err:
        return None, err
    group = _ensure_group(data, account_id)
    target = {
        "id": _next_target_id(data),
        "url": str(url).strip()[:300],
        "tid": tid,
        "pid": pid,
        "fname": info["fname"][:60],
        "title": info["title"][:120],
        "enabled": True,
        "added_at": int(time.time()),
    }
    group["targets"].append(target)
    _save(data)
    _log("target added account={} tid={} fname={}".format(
        account_id, tid, info["fname"]))
    return target, None


def set_target_enabled(account_id, target_id, enabled):
    data = _load()
    group = _find_group(data, account_id)
    if group is None:
        return False
    for target in group["targets"]:
        if int(target["id"]) == int(target_id):
            target["enabled"] = bool(enabled)
            _save(data)
            _log("target {}:{} enabled={}".format(
                account_id, target_id, bool(enabled)))
            return True
    return False


def set_group_enabled(account_id, enabled):
    """整组启用/停用（账号下所有目标帖一起）。"""
    data = _load()
    group = _find_group(data, account_id)
    if group is None:
        return False
    for target in group["targets"]:
        target["enabled"] = bool(enabled)
    _save(data)
    _log("group {} enabled={}".format(account_id, bool(enabled)))
    return True


def delete_target(account_id, target_id):
    data = _load()
    group = _find_group(data, account_id)
    if group is None:
        return False
    before = len(group["targets"])
    group["targets"] = [t for t in group["targets"]
                        if int(t["id"]) != int(target_id)]
    if len(group["targets"]) == before:
        return False
    key = _plan_key(account_id, target_id)
    data["plans"].pop(key, None)
    data["results"].pop(key, None)
    if not group["targets"]:
        data["groups"] = [g for g in data["groups"]
                          if int(g["account_id"]) != int(account_id)]
    _save(data)
    _log("target {}:{} deleted".format(account_id, target_id))
    return True


def delete_group(account_id):
    """删掉整个账号目标组（账号被删掉后的残留用它清理）。"""
    data = _load()
    group = _find_group(data, account_id)
    if group is None:
        return False
    keys = [_plan_key(account_id, t["id"]) for t in group["targets"]]
    data["groups"] = [g for g in data["groups"]
                      if int(g["account_id"]) != int(account_id)]
    for key in keys:
        data["plans"].pop(key, None)
        data["results"].pop(key, None)
    _save(data)
    _log("group {} deleted ({} target(s))".format(account_id, len(keys)))
    return True


# --------------------------------------------------------------------------
# 凭据与 aiotieba 调用
# --------------------------------------------------------------------------

def _bduss_for(account_id):
    import storage
    import keystore_crypto
    import tieba_api

    if account_id is None:
        return None, "还没有选择用于发帖的账号"
    enc = storage.get_encrypted_bduss(account_id)
    if not enc:
        return None, "该账号没有可用的登录凭据，请删除后重新添加"
    try:
        bduss = keystore_crypto.decrypt_text(enc)
    except Exception as exc:  # noqa: BLE001
        return None, "BDUSS 解密失败（{}），请重新登录该账号".format(
            type(exc).__name__)
    fmt_err = tieba_api.format_check(bduss)
    if fmt_err:
        return None, fmt_err
    return bduss, None


def _classify(err):
    """把 aiotieba 的错误对象/异常归类成界面上的中文类别。"""
    code = getattr(err, "code", None)
    msg = getattr(err, "msg", None)
    if msg is None:
        msg = str(err)
    msg = str(msg or "")
    low = msg.lower()
    text = "{} {}".format(type(err).__name__, msg)

    if "need verify code" in low or "验证码" in msg or "vcode" in low:
        category = "需要验证码"
    elif any(key in text for key in _NET_HINTS):
        category = "网络异常"
    elif any(key in msg for key in ("封禁", "禁言", "限制", "频繁", "太快",
                                    "风控", "异常操作", "禁止")) and "禁止发布" not in msg:
        category = "被限制/风控"
    elif any(key in msg for key in ("敏感", "违规", "广告", "禁止发布",
                                    "不合规", "含有")):
        category = "内容违规"
    elif any(key in msg for key in ("登录", "失效", "BDUSS", "用户不存在",
                                    "未登录", "帐号")):
        category = "账号失效"
    else:
        category = "风控其他"

    detail = msg
    if code is not None:
        detail = "{}（错误码 {}）".format(msg, code)
    return {"ok": False, "category": category, "code": code, "detail": detail}


async def _resolve_async(bduss, tid):
    """读取主题帖信息，拿 fname（add_post 必需）与标题。"""
    import aiotieba

    async with aiotieba.Client(bduss) as client:
        result = await client.get_posts(tid, 1, rn=1)
    err = getattr(result, "err", None)
    if err is not None:
        info = _classify(err)
        return None, "{}：{}".format(info["category"], info["detail"])
    thread = getattr(result, "thread", None)
    fname = str(getattr(thread, "fname", "") or "")
    if not fname:
        return None, "无法识别该帖所在的吧（帖内信息为空，可能链接不是主题帖）"
    return {
        "fname": fname,
        "fid": int(getattr(thread, "fid", 0) or 0),
        "title": str(getattr(thread, "title", "") or ""),
        "reply_num": int(getattr(thread, "reply_num", 0) or 0),
    }, None


def _resolve_target(bduss, tid):
    """阻塞式解析主题帖，从 HTTP 线程调用。返回 (info, err)。"""
    import tieba_api

    loop = tieba_api._ensure_loop()
    future = asyncio.run_coroutine_threadsafe(_resolve_async(bduss, tid), loop)
    try:
        return future.result(timeout=_SEND_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        return None, "解析主题帖超时（{}s），请检查网络后重试".format(
            _SEND_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        return None, "解析主题帖失败：{}: {}".format(type(exc).__name__, exc)


async def _post_async(bduss, fname, tid, content, reply_mode):
    """发一条回复。楼中楼在当前 aiotieba 版本下不可用（见模块 docstring）。"""
    if reply_mode == "floor":
        return {"ok": False, "category": "暂不支持", "code": None,
                "detail": _FLOOR_UNSUPPORTED}
    import aiotieba

    async with aiotieba.Client(bduss) as client:
        result = await client.add_post(fname, tid, content)
    err = getattr(result, "err", None)
    if err is None:
        return {"ok": True, "category": None, "code": None,
                "detail": "回复成功"}
    return _classify(err)


def _send(bduss, target, content, reply_mode):
    """阻塞式发送一条，返回结果 dict。任何异常都转成结果，不向上抛。"""
    import tieba_api

    loop = tieba_api._ensure_loop()
    future = asyncio.run_coroutine_threadsafe(
        _post_async(bduss, target["fname"], int(target["tid"]), content,
                    reply_mode), loop)
    try:
        return future.result(timeout=_SEND_TIMEOUT)
    except (asyncio.TimeoutError, TimeoutError):
        return {"ok": False, "category": "网络异常", "code": None,
                "detail": "发送超时（{}s），未确认是否已发出".format(
                    _SEND_TIMEOUT)}
    except Exception as exc:  # noqa: BLE001
        return _classify(exc)


def _send_serialized(bduss, target, content, reply_mode):
    """自动与手动共用的发送入口：串行化，避免两个线程同时对同一账号发帖。"""
    with _send_lock:
        return _send(bduss, target, content, reply_mode)


# --------------------------------------------------------------------------
# 文案选择
# --------------------------------------------------------------------------

def _pick_text(data, target_id):
    """选一条文案并推进序号。会就地修改 data（调用方负责 _save）。"""
    texts = data["texts"] or list(_DEFAULT_TEXTS)
    last = data.get("last_text") or ""
    if data["template_mode"] == "random":
        pick = texts[random.randrange(len(texts))]
        if len(texts) > 1 and pick == last:
            pick = texts[(texts.index(pick) + 1) % len(texts)]
    else:
        seq = int(data.get("text_seq", 0))
        pick = texts[seq % len(texts)]
        if len(texts) > 1 and pick == last:
            seq += 1
            pick = texts[seq % len(texts)]
        data["text_seq"] = seq + 1
    data["last_text"] = pick
    return pick


# --------------------------------------------------------------------------
# 自动计划与发送引擎
# --------------------------------------------------------------------------

def keepalive_needed():
    """给 autosign 的调度循环用：水贴开启（或手动正在跑）时保持前台服务。"""
    try:
        data = _load()
    except Exception:  # noqa: BLE001
        return False
    if data["enabled"]:
        return any(t.get("enabled", True) for _g, t in _all_targets(data))
    return manual_busy()


def _ts_date(ms):
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ms) / 1000.0))
    except (TypeError, ValueError):
        return None


def _signed_today(account_id):
    """指定账号当天是否完成过一次签到（自动/手动都算）。

    判定依据是 signer 的状态快照，不做代码耦合：
      status == "done" 表示账号有效性校验通过且签到流程跑完（吧内失败不影响）。
    """
    if account_id is None:
        return False
    try:
        import signer
        state = signer.get_state() or {}
    except Exception:  # noqa: BLE001
        return False
    today = _today()
    if state.get("mode") == "multi":
        for sub in state.get("accounts") or []:
            try:
                same = int(sub.get("account_id", -1)) == int(account_id)
            except (TypeError, ValueError):
                same = False
            if same and sub.get("status") == "done" and \
                    _ts_date(sub.get("finished_at")) == today:
                return True
        return False
    try:
        same = int(state.get("account_id", -1)) == int(account_id)
    except (TypeError, ValueError):
        same = False
    if not same or state.get("status") != "done":
        return False
    return _ts_date(state.get("finished_at")) == today


def _work_groups(data):
    """当天要干活的 (account_id, [启用目标]) 列表：按账号各自是否签到完成过滤。"""
    work = []
    for group in data["groups"]:
        aid = int(group["account_id"])
        targets = [t for t in group.get("targets") or []
                   if t.get("enabled", True)]
        if not targets:
            continue
        if not _signed_today(aid):
            continue
        work.append((aid, targets))
    return work


def _start_plans(data, work, now):
    """为当天建立计划。返回是否有改动。"""
    today = _today()
    changed = False
    for aid, targets in work:
        for target in targets:
            key = _plan_key(aid, target["id"])
            plan = data["plans"].get(key)
            if plan and plan.get("date") == today:
                continue
            data["plans"][key] = {
                "date": today,
                "sent": 0,
                "skipped": 0,
                "next_ts": now + _FIRST_DELAY_SEC,
                "started_ts": now,
                "stopped": None,
            }
            changed = True
            _log("plan start account={} target={} count={} interval={}s".format(
                aid, target["id"], data["daily_count"], data["interval_sec"]))
    return changed


def _store_result(data, account_id, target, content, result, source="auto"):
    data["results"][_plan_key(account_id, target["id"])] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": bool(result.get("ok")),
        "category": result.get("category"),
        "detail": result.get("detail") or "",
        "content": content[:60],
        "fname": target.get("fname", ""),
        "title": target.get("title", ""),
        "account_id": int(account_id),
        "source": source,
    }


def _notify_failure(target, content, result):
    """失败时沿用自动签到的开关与通知通道（自动计划才通知；手动不通知）。"""
    try:
        import autosign
        if not autosign.get_settings().get("notify_system"):
            return
        autosign.notify_fail(
            "贴吧签到助手 自动水贴失败",
            "《{}》回复失败：{} - {}".format(
                target.get("title") or target.get("fname") or "主题帖",
                result.get("category") or "风控其他",
                (result.get("detail") or "")[:160]))
    except Exception as exc:  # noqa: BLE001
        _log("notify failed: {}: {}".format(type(exc).__name__, exc))


def _stop_account_today(account_id, category, reason):
    """把某个账号当天所有目标帖的自动计划都置为停止。

    自动调度遇到硬失败、以及手动一键水贴遇到硬失败时都用它：同一个号已经
    撞墙了，自动计划再撞只会更糟。
    """
    data = _load()
    group = _find_group(data, account_id)
    if group is None:
        return 0
    today = _today()
    stopped = 0
    for target in group["targets"]:
        key = _plan_key(account_id, target["id"])
        plan = data["plans"].get(key)
        if not isinstance(plan, dict) or plan.get("date") != today:
            plan = {"date": today, "sent": 0, "skipped": 0,
                    "next_ts": 0, "started_ts": time.time(), "stopped": None}
            data["plans"][key] = plan
        if not plan.get("stopped"):
            plan["stopped"] = category
            stopped += 1
    if stopped:
        _save(data)
        _log("account {} stopped for today: {} ({})".format(
            account_id, category, reason))
    return stopped


def _tick():
    data = _load()
    if not data["enabled"]:
        return
    if manual_busy():
        # 手动一键水贴正在跑：让路。次数不会丢，只是推迟到下一轮。
        return

    now = time.time()
    today = _today()
    count = int(data["daily_count"])
    interval = max(_MIN_INTERVAL_SEC, int(data["interval_sec"]))
    makeup = bool(data["makeup"])

    work = _work_groups(data)
    if not work:
        return

    if _start_plans(data, work, now):
        _save(data)
        return      # 下一个 tick 再发，避免刚建完计划就立刻集中发送

    due = []
    for aid, targets in work:
        for target in targets:
            key = _plan_key(aid, target["id"])
            plan = data["plans"].get(key)
            if not isinstance(plan, dict) or plan.get("date") != today:
                continue
            if plan.get("stopped"):
                continue
            if int(plan.get("sent", 0)) >= count:
                continue
            nxt = float(plan.get("next_ts", 0) or 0)
            if now < nxt:
                continue
            late = now - nxt
            if late > interval + _GRACE_SEC and not makeup:
                # 错过的次数按默认设计不补发，重新计时
                plan["skipped"] = int(plan.get("skipped", 0)) + 1
                plan["next_ts"] = now + interval
                _log("skip missed slot account={} target={} late={:.0f}s".format(
                    aid, target["id"], late))
                _save(data)
                continue
            due.append((aid, target, plan))

    if not due:
        return
    if len(due) > _MAX_TICK_SENDS:
        # 补发打开时可能攒下很多欠账：一轮只发这么多，剩下的下一轮继续
        _log("due {} > cap {}, deferring the rest".format(
            len(due), _MAX_TICK_SENDS))
        due = due[:_MAX_TICK_SENDS]

    reply_mode = data["reply_mode"]
    halted = set()      # 本轮已因硬失败停下的账号：它剩下的目标帖不再发
    for index, (aid, target, plan) in enumerate(due):
        if aid in halted:
            _log("account {} halted this tick, skip target {}".format(
                aid, target["id"]))
            continue
        bduss, err = _bduss_for(aid)
        if err:
            _log("send skipped account={}: {}".format(aid, err))
            _store_result(data, aid, target, "", {
                "ok": False, "category": "账号失效", "detail": err})
            _save(data)
            _stop_account_today(aid, "账号失效", err)
            continue
        content = _pick_text(data, target["id"])
        result = _send_serialized(bduss, target, content, reply_mode)
        # 单次发送最长可阻塞 90 秒，这期间用户完全可能在界面上改设置（甚至加目标）。
        # 所以这里不能直接 _save(data)：那份快照已经过期，会把用户的改动覆盖掉。
        # 必须重新载入最新 store，再把本次的进度合并进去。
        category = result.get("category")
        fresh = _load()
        key = _plan_key(aid, target["id"])
        fresh_plan = fresh["plans"].get(key)
        if not isinstance(fresh_plan, dict):
            fresh_plan = {}
            fresh["plans"][key] = fresh_plan
        fresh_plan["date"] = today
        fresh_plan["sent"] = int(plan.get("sent", 0)) + 1
        fresh_plan["skipped"] = int(plan.get("skipped", 0))
        fresh_plan["started_ts"] = plan.get("started_ts", now)
        if makeup:
            # 补发模式：按原计划往后推一格，欠账留到下一轮（每轮有上限）
            fresh_plan["next_ts"] = float(plan.get("next_ts", now)) + interval
        else:
            fresh_plan["next_ts"] = time.time() + interval
        fresh_plan["stopped"] = plan.get("stopped")
        fresh["text_seq"] = data.get("text_seq", fresh.get("text_seq", 0))
        fresh["last_text"] = data.get("last_text", fresh.get("last_text", ""))
        _store_result(fresh, aid, target, content, result, source="auto")
        _save(fresh)         # 每条都落盘：进程被系统杀掉也不丢计数
        data = fresh
        if result.get("ok"):
            _log("posted account={} target={} tid={} content_len={}".format(
                aid, target["id"], target["tid"], len(content)))
        else:
            _log("post failed account={} target={} category={}".format(
                aid, target["id"], category))
            _notify_failure(target, content, result)
            if category in _HARD_STOP_CATEGORIES:
                halted.add(aid)
                _stop_account_today(aid, category, "auto hard failure")
        if index < len(due) - 1:
            time.sleep(interval)      # 两条回复之间的固定间隔


def send_test(account_id=None, target_id=None):
    """立即发送一条（测试用，阻塞最长约 90 秒）。不影响当天计划计数。"""
    data = _load()
    candidates = []
    for group in data["groups"]:
        aid = int(group["account_id"])
        for target in group.get("targets") or []:
            if target.get("enabled", True):
                candidates.append((aid, target))
    if not candidates:
        return {"ok": False, "detail": "还没有启用的主题帖，请先为某个账号添加目标帖"}
    pick = candidates[0]
    if target_id is not None:
        for item in candidates:
            try:
                if int(item[1]["id"]) == int(target_id) and (
                        account_id is None or int(item[0]) == int(account_id)):
                    pick = item
                    break
            except (TypeError, ValueError):
                continue
    elif account_id is not None:
        for item in candidates:
            try:
                if int(item[0]) == int(account_id):
                    pick = item
                    break
            except (TypeError, ValueError):
                continue
    aid, target = pick
    bduss, err = _bduss_for(aid)
    if err:
        return {"ok": False, "detail": err}
    content = _pick_text(data, target["id"])
    result = _send_serialized(bduss, target, content, data["reply_mode"])
    data["last_test"] = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "account_id": aid,
        "target_id": target["id"],
        "ok": bool(result.get("ok")),
        "content": content,
        "category": result.get("category"),
        "detail": result.get("detail") or "",
    }
    _store_result(data, aid, target, content, result, source="test")
    _save(data)
    _log("test send account={} target={} ok={}".format(
        aid, target["id"], bool(result.get("ok"))))
    return {
        "ok": bool(result.get("ok")),
        "target": {"id": target["id"], "fname": target["fname"],
                   "title": target["title"], "tid": target["tid"],
                   "account_id": aid},
        "content": content,
        "result": result,
    }


# --------------------------------------------------------------------------
# 手动一键水贴（签到页的按钮）
# --------------------------------------------------------------------------

def manual_busy():
    with _manual_lock:
        return _manual["status"] == "running"


def get_manual_state():
    with _manual_lock:
        state = {
            "run_id": _manual["run_id"],
            "status": _manual["status"],
            "started_at": _manual["started_at"],
            "finished_at": _manual["finished_at"],
            "total": _manual["total"],
            "finished": _manual["finished"],
            "success": _manual["success"],
            "failed": _manual["failed"],
            "current": _manual["current"],
            "note": _manual["note"],
            "items": list(_manual["items"]),
        }
    return state


def _manual_note(item, target, content, result):
    category = result.get("category")
    # "跳过"（账号今天已停 / 今天已发满）不是失败：别让汇总里冒出吓人的"失败 N"，
    # 用户看到的那 N 条其实一条都没发出去。
    skipped = category == "跳过"
    with _manual_lock:
        _manual["finished"] += 1
        if result.get("ok"):
            _manual["success"] += 1
        elif not skipped:
            _manual["failed"] += 1
        entry = {
            "account_id": int(item["account_id"]),
            "account_name": item.get("account_name") or "贴吧账号",
            "target_id": (int(target["id"]) if target else None),
            "fname": (target or {}).get("fname", ""),
            "title": (target or {}).get("title", ""),
            "content": content or "",
            "ok": bool(result.get("ok")),
            "category": category,
            "detail": (result.get("detail") or "")[:200],
            "seq": item.get("seq"),
            "of": item.get("of"),
            "ts": time.strftime("%H:%M:%S"),
        }
        _manual["items"].append(entry)
        if len(_manual["items"]) > _MANUAL_KEEP_ITEMS:
            del _manual["items"][:len(_manual["items"]) - _MANUAL_KEEP_ITEMS]


def _remaining_today(data, account_id, target, today, count):
    """该目标帖今天还差几条没发（没有任何计划 = 一条都还没发）。

    手动一键水贴和自动计划共用这一份账：plan["sent"] 到了 daily_count，
    两边都认为"今天做完了"（_tick 里的 `sent >= count` 判定）。
    """
    plan = data["plans"].get(_plan_key(account_id, target["id"]))
    sent = 0
    if isinstance(plan, dict) and plan.get("date") == today:
        try:
            sent = int(plan.get("sent", 0) or 0)
        except (TypeError, ValueError):
            sent = 0
    return max(0, int(count) - sent)


def start_manual(account_ids):
    """启动手动一键水贴。返回 (ok, detail)。

    对勾选的每个账号，把它**当天还没发满的次数**一次发完：设置 4 次/天，
    每个启用的目标帖就补足到 4 条。成功才计入当天次数，所以发满后自动计划
    看到 sent >= count 会自然跳过 —— 手动发过的当天不会再重复发。

    账号/帖子之间按设置里的固定间隔。手动不额外增加当天的总量（都算在同一
    份额度里），也不重置自动计划的计时。
    """
    ids = []
    for item in account_ids or []:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value not in ids:
            ids.append(value)
    if not ids:
        return False, "请至少勾选一个账号"
    with _manual_lock:
        if _manual["status"] == "running":
            return False, "上一次手动水贴还没结束，请等它跑完"

    data = _load()
    accounts = _accounts_map()
    today = _today()
    count = max(1, int(data["daily_count"]))
    queue = []
    replies = 0
    for aid in ids:
        name = (accounts.get(aid) or {}).get("name") or "账号 {}".format(aid)
        group = _find_group(data, aid)
        targets = [t for t in (group["targets"] if group else [])
                   if t.get("enabled", True)]
        if not targets:
            queue.append({"account_id": aid, "account_name": name,
                          "skip": "该账号还没有启用任何目标帖"})
            continue
        _bduss, err = _bduss_for(aid)      # 先确认凭据可用，避免跑一半才发现
        if err:
            queue.append({"account_id": aid, "account_name": name,
                          "skip": err})
            continue
        for target in targets:
            left = _remaining_today(data, aid, target, today, count)
            if left <= 0:
                queue.append({"account_id": aid, "account_name": name,
                              "skip": "今天已经发满 {} 次".format(count)})
                continue
            for seq in range(1, left + 1):
                queue.append({"account_id": aid, "account_name": name,
                              "target": target, "seq": seq, "of": left})
            replies += left
            if replies > _MAX_MANUAL_ITEMS:
                return False, (
                    "一次最多发 {} 条，当前勾选会产生更多。"
                    "请减少目标帖、降低每天次数，或分批勾选账号。".format(
                        _MAX_MANUAL_ITEMS))
    if not queue:
        return False, "勾选的账号都没有可用的目标帖"
    if not [item for item in queue if item.get("target") is not None]:
        # 全是被跳过的（没目标帖 / 今天已发满）：把最具体的原因回给用户，
        # 不要"按钮显示已启动、其实什么都没做"。
        for item in queue:
            if item.get("skip"):
                return False, item["skip"]
        return False, "勾选的账号都没有可用的目标帖"

    interval = max(_MIN_INTERVAL_SEC, int(data["interval_sec"]))
    with _manual_lock:
        _manual.update({
            "run_id": _manual["run_id"] + 1,
            "status": "running",
            "started_at": int(time.time() * 1000),
            "finished_at": None,
            "total": len(queue),
            "finished": 0,
            "success": 0,
            "failed": 0,
            "current": "",
            "items": [],
            "note": "手动一键水贴：每帖补满当天次数（间隔 {} 秒）".format(
                interval),
        })
        run_id = _manual["run_id"]
    thread = threading.Thread(target=_manual_loop,
                              args=(run_id, queue, interval),
                              name="waterpost-manual", daemon=True)
    thread.start()
    _log("manual start run={} accounts={} items={} interval={}s".format(
        run_id, ids, len(queue), interval))
    return True, None


def _manual_loop(run_id, queue, interval):
    halted = {}         # {account_id: 停止原因}：已因硬失败停下的账号
    try:
        for index, item in enumerate(queue):
            with _manual_lock:
                if run_id != _manual["run_id"]:
                    _log("manual run {} superseded, aborting".format(run_id))
                    return
            aid = item["account_id"]
            target = item.get("target")
            name = item.get("account_name") or "贴吧账号"
            if target is None:
                _manual_note(item, None, "", {
                    "ok": False, "category": "跳过",
                    "detail": item.get("skip") or "已跳过"})
                continue
            if aid in halted:
                _manual_note(item, target, "", {
                    "ok": False, "category": "跳过",
                    "detail": "该账号已停止（{}）".format(halted[aid])})
                continue
            with _manual_lock:
                _manual["current"] = "{} → 《{}》第 {}/{} 条".format(
                    name, target.get("title") or target.get("fname") or "主题帖",
                    item.get("seq") or 1, item.get("of") or 1)
            data = _load()
            content = _pick_text(data, target["id"])
            _save(data)      # 先落盘文案轮换进度，避免与自动计划互相覆盖
            bduss, err = _bduss_for(aid)
            if err:
                result = {"ok": False, "category": "账号失效", "detail": err}
            else:
                result = _send_serialized(bduss, target, content,
                                          data["reply_mode"])
            _merge_manual_result(item, target, content, result, interval)
            _manual_note(item, target, content, result)
            if not result.get("ok") and \
                    result.get("category") in _HARD_STOP_CATEGORIES:
                # 手动撞墙了：自动计划当天也别再撞
                halted[aid] = result.get("category")
                _stop_account_today(aid, result.get("category"),
                                    "manual hard failure")
            if index < len(queue) - 1:
                time.sleep(interval)
    except Exception as exc:  # noqa: BLE001
        _log("manual loop error: {} {}\n{}".format(
            type(exc).__name__, exc, traceback.format_exc()))
        with _manual_lock:
            _manual["note"] = "执行中断：{}".format(exc)
    finally:
        with _manual_lock:
            _manual["status"] = "done"
            _manual["finished_at"] = int(time.time() * 1000)
            _manual["current"] = ""
        _log("manual done run={} ok={} fail={}".format(
            run_id, _manual["success"], _manual["failed"]))


def _merge_manual_result(item, target, content, result, interval):
    """手动发送的结果写回 store：最近一次结果 + 当天计划进度。

    和 _tick 同一个理由：单次发送最长可阻塞 90 秒，期间用户完全可能改设置，
    所以必须重新 _load() 再合并，不能 _save(旧快照)。

    只有**成功**才把 sent +1。自动计划相反，每次尝试都计入 sent（那是给它自己
    的重试封顶用的）；手动是用户点出来的，一次网络抖动不该白丢当天的额度 ——
    失败不扣账，再点一次就能补上差的那几条。
    """
    account_id = item["account_id"]
    fresh = _load()
    key = _plan_key(account_id, target["id"])
    today = _today()
    plan = fresh["plans"].get(key)
    if not isinstance(plan, dict) or plan.get("date") != today:
        plan = {"date": today, "sent": 0, "skipped": 0,
                "next_ts": 0.0, "started_ts": time.time(), "stopped": None}
        fresh["plans"][key] = plan
    if result.get("ok"):
        plan["sent"] = int(plan.get("sent", 0) or 0) + 1
    # 手动刚发过：给自动计划留出一个间隔，免得它下一轮 tick 立刻接着发
    plan["next_ts"] = time.time() + interval
    _store_result(fresh, account_id, target, content, result, source="manual")
    _save(fresh)


# --------------------------------------------------------------------------
# 调度线程
# --------------------------------------------------------------------------

def _loop():
    first = True
    while True:
        try:
            if first:
                first = False
                _log("scheduler started")
            _tick()
        except Exception as exc:  # noqa: BLE001
            _log("tick error: {} {}\n{}".format(
                type(exc).__name__, exc, traceback.format_exc()))
        time.sleep(_TICK_SEC)


def start_background():
    """启动水贴调度线程（幂等）。在 app_ok 之后调用一次。"""
    global _thread
    if _thread is not None and _thread.is_alive():
        return False
    _thread = threading.Thread(target=_loop, name="waterpost-scheduler",
                               daemon=True)
    _thread.start()
    _log("scheduler thread started")
    return True
