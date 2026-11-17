# -*- coding: utf-8 -*-
"""失效贴吧标记（0.6.2，用户反馈第 3 点）。

有的吧已经被查封/关闭，但账号还关注着它；每次签到、点赞都会在它身上报错。
用户在设置里把这些吧标记为「失效」后，签到和点赞会直接跳过它们，不再产
生失败记录、也不会触发限流熔断。

数据文件：``<ANDROID_PRIVATE>/tiebasign_invalid.json``，只有吧名，不含凭据。
"""

import json
import os
import threading

_DATA_FILENAME = "tiebasign_invalid.json"
_MAX_NAME_LEN = 40
_MAX_COUNT = 500

_lock = threading.RLock()


def _data_path():
    base = os.environ.get("ANDROID_PRIVATE") or os.getcwd()
    return os.path.join(base, _DATA_FILENAME)


def _clean_name(name):
    text = str(name or "").strip()
    if not text:
        return None
    if len(text) > _MAX_NAME_LEN:
        text = text[:_MAX_NAME_LEN]
    return text


def _load():
    try:
        with open(_data_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        forums = data.get("forums") if isinstance(data, dict) else None
        if isinstance(forums, list):
            out = []
            for name in forums:
                cleaned = _clean_name(name)
                if cleaned and cleaned not in out:
                    out.append(cleaned)
            return {"forums": out[:_MAX_COUNT]}
    except (OSError, ValueError):
        pass
    return {"forums": []}


def _save(data):
    path = _data_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def list_forums():
    with _lock:
        return list(_load()["forums"])


def is_invalid(name):
    cleaned = _clean_name(name)
    if not cleaned:
        return False
    with _lock:
        return cleaned in _load()["forums"]


def add_forum(name):
    """添加一个失效吧；已存在时视为成功。返回当前列表。"""
    cleaned = _clean_name(name)
    if not cleaned:
        raise ValueError("吧名不能为空")
    with _lock:
        data = _load()
        if cleaned not in data["forums"]:
            if len(data["forums"]) >= _MAX_COUNT:
                raise ValueError("最多标记 {} 个失效吧".format(_MAX_COUNT))
            data["forums"].append(cleaned)
            _save(data)
        return list(data["forums"])


def remove_forum(name):
    cleaned = _clean_name(name)
    with _lock:
        data = _load()
        if cleaned in data["forums"]:
            data["forums"] = [f for f in data["forums"] if f != cleaned]
            _save(data)
        return list(data["forums"])


def filter_forums(forums):
    """把迭代对象里的失效吧过滤掉，返回 (保留, 跳过数)。

    同时兼容 SelfFollowForum 对象（取 ``fname``）和纯字符串。
    """
    marked = set(list_forums())
    kept = []
    skipped = 0
    for forum in forums:
        name = (getattr(forum, "fname", None)
                if not isinstance(forum, str) else forum)
        if name and str(name) in marked:
            skipped += 1
            continue
        kept.append(forum)
    return kept, skipped
