# -*- coding: utf-8 -*-
"""统一错误分类器离线自检（0.6.0 P0-3）。

运行：python3 scripts/tests/test_errors.py   （在仓库根目录）

覆盖：
  * 已知码必须给出分类 / 中文原因 / 「怎么办」；
  * 未知码、空 errmsg 必须明确说「原因不明」，不能冒充「被限制」；
  * 没有码的异常按类型识别网络问题（不靠文案猜）；
  * 有码的错误按码表分类，码表未收录时退到中文关键词；
  * raw 里的 BDUSS 必须被脱敏。

全部离线、不联网、不碰 Android。
"""
import os
import sys
import tempfile

# 自定位：<仓库>/scripts/tests/xxx.py -> <仓库>/app
APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.environ["ANDROID_PRIVATE"] = tempfile.mkdtemp(prefix="errors_selftest_")

import errors  # noqa: E402
import tieba_codes  # noqa: E402

TOTAL = [0]
FAILS = []


def check(name, cond, extra=""):
    TOTAL[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


FAKE_BDUSS = "AbCdEf0123456789" + ("x" * 140) + "TEST"


class FakeErr:
    """形状与 aiotieba.exception.TiebaServerError 一致。"""

    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg


class NoCodeErr(Exception):
    pass


def test_codes():
    info = errors.classify(FakeErr(340011, ""))
    check("340011 分类=被限制", info["category"] == tieba_codes.CAT_RISK,
          info)
    check("340011 保留 code", info["code"] == 340011)
    check("340011 有中文原因", bool(info["reason"]), info)
    check("340011 有「怎么办」", bool(info["advice"]), info)
    check("340011 detail 非空", bool(info["detail"]), info)

    info = errors.classify(FakeErr(111, ""))
    check("111 分类=吧不存在",
          info["category"] == tieba_codes.CAT_NO_FORUM, info)
    info = errors.classify(FakeErr(160002, ""))
    check("160002 分类=已签到", info["category"] == tieba_codes.CAT_DONE,
          info)
    info = errors.classify(FakeErr(340006, ""))
    check("340006 分类=吧已查封",
          info["category"] == tieba_codes.CAT_FORUM_BANNED, info)
    info = errors.classify(FakeErr(340001, ""))
    check("340001 分类=吧已查封",
          info["category"] == tieba_codes.CAT_FORUM_BANNED, info)
    check("340011 仍是限流而不是查封",
          errors.classify(FakeErr(340011, ""))["category"] ==
          tieba_codes.CAT_RISK)


def test_unknown_is_not_risk():
    info = errors.classify(FakeErr(999999, ""))
    check("未知码分类=原因不明",
          info["category"] == tieba_codes.CAT_UNKNOWN, info)
    check("未知码不编原因", info["reason"] is None, info)
    check("未知码 detail 说明没有具体原因",
          "没有给出具体原因" in info["detail"], info)

    info = errors.classify(NoCodeErr(""))
    check("无码无文案=原因不明",
          info["category"] == tieba_codes.CAT_UNKNOWN, info)
    check("无码 detail 说明没有错误码",
          "没有给出错误码" in info["detail"], info)
    check("无码 code 为 None", info["code"] is None, info)


def test_keyword_fallback():
    case = FakeErr(999999, "该吧不存在")
    check("未收录码 + 关键词 -> 吧不存在",
          errors.classify(case)["category"] == tieba_codes.CAT_NO_FORUM)
    case = FakeErr(999999, "操作太频繁，请稍后再试")
    check("未收录码 + 频繁 -> 被限制",
          errors.classify(case)["category"] == tieba_codes.CAT_RISK)
    case = FakeErr(999999, "您发布的内容含有敏感词")
    check("未收录码 + 敏感词 -> 内容违规",
          errors.classify(case)["category"] == tieba_codes.CAT_CONTENT)
    case = FakeErr(999999, "该吧已被查封")
    check("未收录码 + 查封 -> 吧已查封",
          errors.classify(case)["category"] == tieba_codes.CAT_FORUM_BANNED)
    case = NoCodeErr("Need verify code")
    check("无码 + Need verify code -> 需要验证码",
          errors.classify(case)["category"] == tieba_codes.CAT_CAPTCHA)
    check("category_for 空文案 -> 原因不明",
          errors.category_for(None, "") == tieba_codes.CAT_UNKNOWN)


def test_network():
    check("TimeoutError 是网络异常",
          errors.is_network_exception(TimeoutError("timed out")))
    check("普通 RuntimeError 不是网络异常",
          not errors.is_network_exception(RuntimeError("boom")))
    info = errors.classify(TimeoutError("timed out"))
    check("网络异常分类=网络问题",
          info["category"] == tieba_codes.CAT_NETWORK, info)
    check("网络异常 detail 说明网络问题",
          "网络连接" in info["detail"], info)
    check("网络异常有自己的建议",
          "网络" in info["advice"], info)

    class ClientConnectorError(Exception):
        pass

    info = errors.classify(ClientConnectorError("nope"))
    check("按异常类型识别网络问题（ClientConnectorError）",
          info["category"] == tieba_codes.CAT_NETWORK, info)


def test_redaction():
    info = errors.classify(FakeErr(111, "BDUSS=" + FAKE_BDUSS))
    check("raw 里的 BDUSS 已脱敏", FAKE_BDUSS not in info["raw"], info["raw"])
    info = errors.classify(
        FakeErr(111, "BDUSS=" + FAKE_BDUSS), secret=FAKE_BDUSS)
    check("传入 secret 时同样脱敏",
          FAKE_BDUSS not in info["raw"] and FAKE_BDUSS not in info["detail"])


def main():
    print("== 码表分类 =="); test_codes()
    print("== 未知不冒充风控 =="); test_unknown_is_not_risk()
    print("== 关键词兜底 =="); test_keyword_fallback()
    print("== 网络异常 =="); test_network()
    print("== 脱敏 =="); test_redaction()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("ALL_ERRORS_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
