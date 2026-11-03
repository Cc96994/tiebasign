# -*- coding: utf-8 -*-
"""BDUSS / 登录凭据脱敏（v0.5.0）。

原则：**完整 BDUSS 不允许出现在任何日志、错误详情、导出报告、界面文案里**。
只允许出现前 4 + 后 4 的掩码（见 ``storage._mask_bduss``）和长度。

这里做两层保护：

1. 已知明文：调用方把当前 BDUSS 传进来，做精确替换（``scrub(..., secret)``）。
   适用于「异常文本里可能拼进了凭据」的路径。
2. 未知明文：按形状兜底。BDUSS 是 100~200 字符的 base64-ish 串，
   而正常错误文本里极少出现这么长的连续 token，所以把 120 字符以上的
   连续 ``[A-Za-z0-9+/_=-]`` 整段替换掉。短 token（吧名、错误码、URL、
   sha256 十六进制串）不受影响。

本模块不碰网络、不落盘，可单独测试。
"""

import re

MASK = "<BDUSS 已隐藏>"

#: 「BDUSS=xxx」「BDUSS: xxx」「BDUSS%3Dxxx」三种写法。
_BDUSS_KV_RE = re.compile(
    r"(?i)(BDUSS(?:\s*[=:]|%3D)\s*)([A-Za-z0-9+/_=-]{6,})")

#: 裸的长 token 兜底。下界 120：比 sha256（64）长，比真实 BDUSS（约 192）短。
#: 前后不能再接同类字符，避免把更长的串截断后留下残片。
_LONG_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9+/_=-])[A-Za-z0-9+/_=-]{120,240}(?![A-Za-z0-9+/_=-])")


def scrub(text, secret=None):
    """返回脱敏后的字符串；非字符串原样返回。

    secret 为空时只按形状兜底；secret 至少 16 字符才做精确替换（更短的
    值可能是吧名或普通单词，替换会误伤可读性）。
    """
    if not isinstance(text, str) or not text:
        return text
    if secret and isinstance(secret, str) and len(secret) >= 16:
        text = text.replace(secret, MASK)
    text = _BDUSS_KV_RE.sub(lambda m: m.group(1) + MASK, text)
    text = _LONG_TOKEN_RE.sub(MASK, text)
    return text


def scrub_obj(value, secret=None):
    """递归脱敏 dict/list/str（用于要落盘或回给前端的结构）。"""
    if isinstance(value, str):
        return scrub(value, secret)
    if isinstance(value, list):
        return [scrub_obj(item, secret) for item in value]
    if isinstance(value, tuple):
        return tuple(scrub_obj(item, secret) for item in value)
    if isinstance(value, dict):
        return {scrub_obj(k, secret): scrub_obj(v, secret)
                for k, v in value.items()}
    return value
