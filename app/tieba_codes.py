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
「百度没有给出具体原因」+「稍后再试…」，错误码放次要位置。这样「没查到」和
「查到了」在界面上是两种明确状态，不会把猜测伪装成结论。

本模块不碰网络、不含任何凭据，可以单独测试。
"""

import re

# 分类名用普通人能看懂的说法：登录问题 / 网络问题 / 被限制 / 原因不明 /
# 吧不存在 / 需要验证码。旧快照里的「账号失效」「风控其他」仍按颜色兜底。
CAT_DONE = "已签到"
CAT_UNFOLLOWED = "未关注"
CAT_NO_FORUM = "吧不存在"
CAT_ACCOUNT = "登录问题"
CAT_CAPTCHA = "需要验证码"
CAT_NETWORK = "网络问题"
CAT_RISK = "被限制"
CAT_UNKNOWN = "原因不明"

#: 已知错误码 -> (分类, 原因, 怎么办)。原因/建议都按普通人能看懂的话写，
#: 不出现 errmsg / tbs / BDUSS 之类的术语；技术细节留给 detail/日志。
CODE_MAP = {
    1: (CAT_ACCOUNT, "登录状态不对，可能是账号掉线了",
        "重新登录这个账号（删除后重新添加 BDUSS）"),
    2: (CAT_ACCOUNT, "登录失败，账号可能已经掉线",
        "重新登录这个账号"),
    3: (CAT_DONE, "今天已经签过到了", ""),
    4: (CAT_CAPTCHA, "需要验证码才能继续",
        "打开贴吧 App 或网页手动签到一次，完成验证码后再试"),
    5: (CAT_CAPTCHA, "需要验证码才能继续",
        "打开贴吧 App 或网页手动签到一次，完成验证码后再试"),
    6: (CAT_CAPTCHA, "验证码不对或已经过期", "重新验证一次"),
    111: (CAT_NO_FORUM, "这个吧不存在，可能是吧名写错了，或者贴吧已经被删除",
          "检查吧名是否写错；如果这个吧已经不存在，取消关注即可"),
    257: (CAT_CAPTCHA, "需要验证码才能继续",
          "打开贴吧 App 或网页手动签到一次，完成验证码后再试"),
    1101: (CAT_DONE, "点得太快了，系统已经按签到处理", ""),
    1102: (CAT_DONE, "点得太快了，系统已经按签到处理", ""),
    160002: (CAT_DONE, "今天已经签过到了", ""),
    160003: (CAT_UNKNOWN, "百度没有说明具体原因，这次签到没有成功",
             "过一会儿再试；如果一直失败，把这份签到报告发给开发者"),
    160004: (CAT_ACCOUNT, "账号被封禁或屏蔽了，暂时不能签到",
             "到贴吧 App 里查看账号状态"),
    160008: (CAT_UNKNOWN, "百度没有说明具体原因，这次签到没有成功",
             "过一会儿再试；如果一直失败，把这份签到报告发给开发者"),
    300004: (CAT_UNFOLLOWED, "你还没有关注这个吧", "先在贴吧里关注这个吧"),
    110000: (CAT_ACCOUNT, "登录已经过期，需要重新登录",
             "重新登录这个账号（删除后重新添加 BDUSS）"),
    110001: (CAT_UNKNOWN, "百度返回了一个它自己也没有说明的错误",
             "过一会儿再试；如果一直失败，把报告发给开发者"),
    110003: (CAT_UNKNOWN, "百度服务暂时不可用", "过一会儿再试"),
    2280001: (CAT_ACCOUNT, "账号在黑名单里，暂时不能操作",
              "到贴吧 App 里查看账号状态"),
    2280007: (CAT_RISK, "百度服务忙，暂时签不了", "过一会儿再试"),
    2280015: (CAT_ACCOUNT, "账号被封禁了，不能签到",
              "到贴吧 App 里查看账号状态"),
    1990055: (CAT_ACCOUNT, "账号没有实名，签到功能被禁用",
              "按贴吧提示完成实名验证"),
    320022: (CAT_ACCOUNT, "登录状态校验失败，需要重新登录",
             "重新登录这个账号"),
    3280004: (CAT_RISK, "操作太频繁，被系统暂时限制",
              "等几分钟再试，不要连续点赞"),
    340006: (CAT_RISK, "这个吧暂时不能签到，可能被系统限制了",
             "今天先不要重试；如果所有吧都这样，明天或换个网络再试"),
    340008: (CAT_ACCOUNT, "账号在黑名单里", "到贴吧 App 里查看账号状态"),
    340010: (CAT_DONE, "今天已经签过到了", ""),
    340011: (CAT_RISK, "操作太快了，被系统暂时限制",
             "等几分钟再试，不要连续点签到"),
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


def advice(code):
    """返回该错误码的「怎么办」建议；没有/未收录返回空串。"""
    entry = CODE_MAP.get(parse_code(code))
    return entry[2] if entry and len(entry) > 2 else ""


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
