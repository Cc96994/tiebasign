# -*- coding: utf-8 -*-
"""统一的贴吧错误分类器（0.6.0 P0-3）。

为什么要有这个文件：0.5.x 里 signer / liker / groupsign / waterpost / tieba_api
各有一份「错误对象 -> 分类 / 原因 / 建议」的代码，关键词表还不一样。结果是同一
个错误在不同页面可能显示不同分类；水贴的兜底干脆还叫「风控其他」——其实那是
「我们分类不出来」，不是百度说了风控。0.6 的 msign 风控即停（S6）和撞限流后的
熔断降速都要求「风控 / 验证码 / 账号 / 网络 / 未知」是同一套判断，所以集中到
这里，各模块只保留自己的展示文案。

分类顺序（先强后弱）：

  1. 已知错误码（``tieba_codes.CODE_MAP``）。码是服务端给的客观值，码表只收
     有实测出处的码；未收录的码不编原因。
  2. 中文关键词兜底。空 ``errmsg`` 是常态，这时只能靠码；有文案时按文案补充
     识别（例如 ``Need verify code`` 这种没有码的 ``TiebaValueError``）。
  3. 网络异常：按异常类型名 / MRO 判断传输层问题，不按文案猜。
  4. 都没有：``tieba_codes.CAT_UNKNOWN``（「原因不明」），绝不冒充「被限制」。

本模块不碰网络、不含任何凭据，可以单独测试。``raw`` 字段会被 ``redact``
脱敏；调用方不需要再处理。
"""

import tieba_codes
import redact

#: 传输层异常的特征片段（类型名 / MRO / 异常文本）。与 0.5.x 的 signer 一致。
NETWORK_HINTS = (
    "ClientConnector", "ClientOSError", "ClientConnection", "ConnectionError",
    "TimeoutError", "Timeout", "SSLCertVerificationError", "SSLError",
    "CertificationError", "SocketError", "Socket", "OSError",
)

_CAPTCHA_HINTS = ("验证码", "need verify code", "vcode", "verify code")
_CONTENT_HINTS = ("敏感", "违规", "广告", "禁止发布", "不合规", "含有")
_DONE_HINTS = ("已经签过", "已签到", "重复签到", "签到过快")
_ACCOUNT_HINTS = ("bduss", "登录已失效", "登录失效", "用户不存在", "未登录",
                  "账号失效", "帐号失效", "失效", "过期", "封禁", "黑名单",
                  "实名", "登录")
_NO_FORUM_HINTS = ("吧不存在", "贴吧不存在", "该吧不存在")
_UNFOLLOWED_HINTS = ("未关注", "关注该吧", "先关注", "取消关注", "非本吧会员",
                     "会员才能")
_FORUM_BANNED_HINTS = ("查封", "已被关闭", "吧已关闭", "该吧已关闭",
                       "目录出问题", "贴吧目录")
_RISK_HINTS = ("风控", "太快", "过快", "频繁", "限制", "禁言", "异常操作",
               "服务忙", "tbs", "操作过频")

DEFAULT_ADVICE = "稍后再试；如果一直失败，把这份签到报告发给开发者"
NETWORK_ADVICE = "检查网络后重试；如果一直失败，把这份签到报告发给开发者"


def is_network_exception(exc):
    """异常是不是传输层问题（超时 / 连接 / DNS / TLS）。"""
    if not isinstance(exc, BaseException):
        return False
    name = type(exc).__name__
    text = "{} {}".format(name, exc)
    mro_text = " ".join(c.__name__ for c in type(exc).__mro__)
    return any(k in mro_text or k in text for k in NETWORK_HINTS)


def _keyword_category(message):
    """只按中文/英文关键词分类；分不出来返回 None。"""
    msg = str(message or "")
    low = msg.lower()

    if any(k in low for k in _CAPTCHA_HINTS):
        return tieba_codes.CAT_CAPTCHA
    if any(k in msg for k in _CONTENT_HINTS):
        return tieba_codes.CAT_CONTENT
    if any(k in msg for k in _DONE_HINTS):
        return tieba_codes.CAT_DONE
    if any(k in low for k in _ACCOUNT_HINTS):
        return tieba_codes.CAT_ACCOUNT
    if any(k in msg for k in _NO_FORUM_HINTS):
        return tieba_codes.CAT_NO_FORUM
    if any(k in msg for k in _UNFOLLOWED_HINTS):
        return tieba_codes.CAT_UNFOLLOWED
    if any(k in msg for k in _FORUM_BANNED_HINTS):
        return tieba_codes.CAT_FORUM_BANNED
    if any(k in low for k in _RISK_HINTS):
        return tieba_codes.CAT_RISK
    # 空 errmsg 或完全没见过的文案：明确返回 None，由调用方落到「原因不明」。
    return None


def category_for(code, message=None):
    """错误码 -> 分类；码未收录时退回关键词；都分不出来返回 CAT_UNKNOWN。"""
    category = tieba_codes.category_for_code(code)
    if category:
        return category
    return _keyword_category(message) or tieba_codes.CAT_UNKNOWN


def classify(err, secret=None, subject="这次操作"):
    """把一个 aiotieba 错误对象 / 异常变成统一的错误描述 dict。

    返回字段：
        category: tieba_codes.CAT_* 之一
        code:     int 或 None（服务端给的真实错误码）
        reason:   tieba_codes 里该码的中文原因；未收录为 None
        advice:   给普通人的「怎么办」
        detail:   给普通人的「原因」一句话
        raw:      脱敏后的百度原文（技术细节，只进日志/报告）
    """
    code = tieba_codes.parse_code(getattr(err, "code", None))
    msg = getattr(err, "msg", None)
    if msg is None:
        msg = str(err)
    raw = redact.scrub(str(msg or "").strip(), secret)

    network = code is None and is_network_exception(err)
    if network:
        category = tieba_codes.CAT_NETWORK
    else:
        category = category_for(code, msg)

    reason = tieba_codes.reason(code)
    advice = tieba_codes.advice(code)
    if not advice:
        if category == tieba_codes.CAT_DONE:
            advice = ""
        elif category == tieba_codes.CAT_NETWORK:
            advice = NETWORK_ADVICE
        else:
            advice = DEFAULT_ADVICE

    if network:
        detail = "网络连接出了问题，{}没有完成".format(subject)
    elif code is None:
        detail = "百度没有给出错误码，{}没有成功".format(subject)
    elif reason:
        detail = reason
    else:
        detail = "百度没有给出具体原因，{}没有成功".format(subject)

    return {
        "category": category,
        "code": code,
        "reason": reason,
        "advice": advice,
        "detail": redact.scrub(detail, secret),
        "raw": raw,
    }
