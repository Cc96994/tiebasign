# -*- coding: utf-8 -*-
"""百度贴吧错误码 -> 中文原因（v0.5.0）。

为什么要有这个文件：aiotieba 只把服务端的 ``error_code`` / ``error_msg``
原样透传（见 ``aiotieba/exception.py`` 的 ``TiebaServerError``），不做码表
映射；而百度很多错误只给一个数字，``error_msg`` 甚至是空串。用户看到
「错误码 340011」时无法判断发生了什么，只把它贴进 issue 也定位不了。

这里收录的是**有社区实测出处**的码，不是猜的：

* aiotieba 官方签到示例：``160002`` / ``340006`` 是终态，不重试；
* ``BANKA2017/tbsign_go`` 的 ``plugins/c_checkin.go`` 注释：
  ``1`` 用户未登录或登录失败、``110000`` 请先登录、``340006`` 贴吧目录
  出问题啦、``340011`` 你签得太快了、``2280007`` 签到服务忙、
  ``320022`` tbs check fail、``2280015`` 账号被封禁、``2280001`` 黑名单、
  ``1990055`` 未实名、``300004`` 签到未关注的贴吧；
* ``kookxiang/Tieba_Sign-Go``：``340010`` / ``160002`` / ``3`` 已签到，
  ``340008`` / ``340006`` / ``160004`` / ``1`` 失败；
* ``MoeNetwork/Tieba-Cloud-Sign``：``1101`` / ``1102`` 签到过快按已签到
  处理，``340006`` 同上的「贴吧目录出问题」；
* ``111`` = 该吧不存在：由本项目用户在 v0.5.0 工单里给出（示例错误报告）。

**没有出处的码一律不编原因**：``reason`` 返回 ``None``，界面只显示
「错误码 X（未收录）」加百度原文。这样「没查到」和「查到了」在界面上
是两种明确状态，不会把猜测伪装成结论。

本模块不碰网络、不含任何凭据，可以单独测试。
"""

import re

# 分类名。保留旧版的四个分类名（已签到 / 未关注 / 账号失效 / 网络异常 /
# 风控其他），新增更精确的「吧不存在」「需要验证码」「风控限制」「未知错误」。
CAT_DONE = "已签到"
CAT_UNFOLLOWED = "未关注"
CAT_NO_FORUM = "吧不存在"
CAT_ACCOUNT = "账号失效"
CAT_CAPTCHA = "需要验证码"
CAT_NETWORK = "网络异常"
CAT_RISK = "风控限制"
CAT_UNKNOWN = "未知错误"

#: 已知错误码 -> (分类, 原因)。原因写给人看，不超过一行。
CODE_MAP = {
    1: (CAT_ACCOUNT, "用户未登录或登录失败（也可能是百度通用错误，可重试一次）"),
    2: (CAT_ACCOUNT, "登录失败，BDUSS 可能已失效"),
    3: (CAT_DONE, "已经签过到（旧版接口的重复签到码）"),
    4: (CAT_CAPTCHA, "需要验证码，请到贴吧客户端或网页手动签到一次"),
    5: (CAT_CAPTCHA, "需要验证码，请到贴吧客户端或网页手动签到一次"),
    6: (CAT_CAPTCHA, "验证码错误或已过期，请重新验证"),
    111: (CAT_NO_FORUM, "该吧不存在（吧名写错或贴吧已被删除）"),
    257: (CAT_CAPTCHA, "需要输入验证码"),
    1101: (CAT_DONE, "签到过快，服务端按已签到处理"),
    1102: (CAT_DONE, "签到过快，服务端按已签到处理"),
    160002: (CAT_DONE, "今天已经签过到了"),
    160003: (CAT_UNKNOWN, "签到失败（服务端没有给出更具体的原因）"),
    160004: (CAT_ACCOUNT, "账号被封禁或屏蔽，无法签到"),
    160008: (CAT_UNKNOWN, "签到失败（服务端没有给出更具体的原因）"),
    300004: (CAT_UNFOLLOWED, "签到未关注的贴吧（需要先关注该吧）"),
    110000: (CAT_ACCOUNT, "未登录或登录已失效（BDUSS 过期或被踢下线）"),
    110001: (CAT_UNKNOWN, "未知错误（百度通用错误码）"),
    110003: (CAT_UNKNOWN, "服务调用失败（百度接口临时异常，可稍后重试）"),
    2280001: (CAT_ACCOUNT, "账号在黑名单中，不能操作"),
    2280007: (CAT_RISK, "签到服务忙，请稍后重试"),
    2280015: (CAT_ACCOUNT, "账号已被封禁，不能进行签到"),
    1990055: (CAT_ACCOUNT, "账号未实名，签到功能被禁用"),
    320022: (CAT_ACCOUNT, "tbs 校验失败（登录态异常，请重新登录）"),
    340006: (CAT_RISK, "贴吧目录异常（吧可能被屏蔽，或账号被风控）"),
    340008: (CAT_ACCOUNT, "账号在黑名单中"),
    340010: (CAT_DONE, "今天已经签过到了"),
    340011: (CAT_RISK, "签得太快了，先看看帖子再来签"),
}

_CODE_RE = re.compile(r"^-?\d+$")


def parse_code(value):
    """把服务端给的 code（可能是 int/str/None）解析成 int；失败返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        text = str(value).strip()
    except Exception:  # noqa: BLE001 - 服务端字段类型不可控
        return None
    if not _CODE_RE.match(text):
        return None
    try:
        return int(text)
    except ValueError:
        return None


def reason(code):
    """返回该错误码的中文原因；未收录返回 None。"""
    entry = CODE_MAP.get(parse_code(code))
    return entry[1] if entry else None


def category_for_code(code):
    """只按错误码判断分类；未收录返回 None（交给调用方的关键词兜底）。"""
    entry = CODE_MAP.get(parse_code(code))
    return entry[0] if entry else None


def explain(code):
    """错误码的展示文本，例如「错误码 111：该吧不存在（…）」。"""
    parsed = parse_code(code)
    if parsed is None:
        return "错误码未知"
    text = reason(parsed)
    if text:
        return "错误码 {}：{}".format(parsed, text)
    return "错误码 {}（未收录）".format(parsed)


def is_known(code):
    return parse_code(code) in CODE_MAP
