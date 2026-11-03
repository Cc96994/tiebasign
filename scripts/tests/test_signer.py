# -*- coding: utf-8 -*-
"""签到引擎离线自检（v0.5.0）。

运行：python3 scripts/tests/test_signer.py   （在仓库根目录）

覆盖的是 v0.5.0 新增/改动的三块，全部离线、不联网、不碰 Android：

  1. 错误码表：已知码必须给出分类、普通人能看懂的原因和「怎么办」；未知码必须
     明确说「百度没有给出具体原因」，而不是编一个。
     而不是编一个原因。
  2. 并发签到：sign_forum 的并发数不得超过 SIGN_CONCURRENCY，且确实并发
     （不能退化成串行）；单个吧失败不拖垮整轮。
  3. 错误报告与脱敏：失败项带 code/reason；sign.log 与最近一次报告里
     不允许出现完整 BDUSS（只允许掩码）。

夹具里的 BDUSS 是现造的假值（164 字符），不是任何真实凭据。
"""

import asyncio
import json
import os
import sys
import tempfile

# 自定位：<仓库>/scripts/tests/xxx.py -> <仓库>/app
APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
_PRIVATE = tempfile.mkdtemp(prefix="sign_selftest_")
os.environ["ANDROID_PRIVATE"] = _PRIVATE

import redact  # noqa: E402
import signer  # noqa: E402
import tieba_codes  # noqa: E402

TOTAL = [0]
FAILS = []


def check(name, cond, extra=""):
    TOTAL[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


# 假凭据：形状像 BDUSS，但内容是现造的。
FAKE_BDUSS = "AbCdEf0123456789" + ("x" * 140) + "TEST"
assert len(FAKE_BDUSS) > 120


# ---------------------------------------------------------------- 错误码表
def test_codes():
    check("111 分类=吧不存在",
          tieba_codes.category_for_code(111) == tieba_codes.CAT_NO_FORUM)
    check("111 原因写明这个吧不存在",
          "吧不存在" in (tieba_codes.reason(111) or ""))
    check("111 展示文本带「错误码 111」",
          "错误码 111" in tieba_codes.explain(111))
    check("160002 分类=已签到",
          tieba_codes.category_for_code(160002) == tieba_codes.CAT_DONE)
    check("340011 原因=操作太快",
          "太快" in (tieba_codes.reason(340011) or ""))
    check("340006 分类=风控限制",
          tieba_codes.category_for_code(340006) == tieba_codes.CAT_RISK)
    check("110000 分类=账号失效",
          tieba_codes.category_for_code(110000) == tieba_codes.CAT_ACCOUNT)
    check("300004 分类=未关注",
          tieba_codes.category_for_code(300004) == tieba_codes.CAT_UNFOLLOWED)
    check("未收录码没有原因",
          tieba_codes.reason(999999) is None)
    check("未收录码展示文本明确写「未收录」",
          "未收录" in tieba_codes.explain(999999))
    check("None 码不抛异常",
          tieba_codes.explain(None) == "错误码未知")
    check("字符串码可以解析",
          tieba_codes.category_for_code("340011") == tieba_codes.CAT_RISK)
    check("非法码返回 None", tieba_codes.parse_code("abc") is None)
    check("空 errmsg + 未知码 = 未知错误",
          signer._classify_server_error(999999, "") ==
          tieba_codes.CAT_UNKNOWN)
    check("未知码但有中文关键词仍能分类",
          signer._classify_server_error(999999, "该吧不存在") ==
          tieba_codes.CAT_NO_FORUM)


# ---------------------------------------------------------------- 脱敏
def test_redact():
    kv = "login failed: BDUSS={}; retry".format(FAKE_BDUSS)
    out = redact.scrub(kv)
    check("BDUSS=值 被替换", FAKE_BDUSS not in out and redact.MASK in out)
    out2 = redact.scrub("raw " + FAKE_BDUSS + " end")
    check("裸长 token 被替换",
          FAKE_BDUSS not in out2 and redact.MASK in out2)
    sha = "a" * 64
    check("sha256（64 字符）不被误伤", redact.scrub(sha) == sha)
    short = "前缀 BDUSS=abc123 后缀"
    check("短 BDUSS 值同样隐藏",
          "abc123" not in redact.scrub(short))
    nested = {"a": [FAKE_BDUSS, {"b": "BDUSS=" + FAKE_BDUSS}]}
    scrubbed = redact.scrub_obj(nested)
    blob = json.dumps(scrubbed, ensure_ascii=False)
    check("scrub_obj 递归脱敏", FAKE_BDUSS not in blob)


# ---------------------------------------------------------------- 假客户端
class FakeErr:
    def __init__(self, code, msg):
        self.code = code
        self.msg = msg


class FakeResp:
    def __init__(self, err=None):
        self.err = err


class FakeUser:
    def __init__(self, err=None):
        self.err = err
        self.user_id = 1
        self.user_name = "tester"


class FakeForum:
    def __init__(self, fname, signed=False):
        self.fname = fname
        self.is_signed = signed


class FakeFollow:
    def __init__(self, forums):
        self._forums = forums
        self.has_more = False

    def __iter__(self):
        return iter(self._forums)

    def __len__(self):
        return len(self._forums)


class FakeThread:
    def __init__(self, tid, fname, title="测试帖", is_top=False,
                 is_hide=False):
        self.tid = tid
        self.fname = fname
        self.title = title
        self.is_top = is_top
        self.is_hide = is_hide


class FakeThreads(list):
    err = None


class FakeClient:
    """可配置的 aiotieba.Client 替身。"""

    instances = []

    def __init__(self, bduss):
        self.bduss = bduss
        self.forums = []
        self.msign_err = None
        self.growth_err = None
        self.forum_behavior = {}
        self.agree_err = None
        self.agree_calls = 0
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.growth_calls = 0
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_self_info(self):
        return FakeUser()

    async def sign_growth(self):
        self.growth_calls += 1
        self.calls.append(("sign_growth",))
        return FakeResp(self.growth_err)

    async def get_threads(self, fname, pn=1, rn=30):
        self.calls.append(("get_threads", fname, pn, rn))
        return FakeThreads([
            FakeThread(9001, fname, "置顶帖", is_top=True),
            FakeThread(9002, fname, "普通帖 A"),
            FakeThread(9003, fname, "普通帖 B"),
        ])

    async def agree(self, tid):
        self.agree_calls += 1
        self.calls.append(("agree", tid))
        return FakeResp(self.agree_err)

    async def sign_forums(self):
        self.calls.append(("sign_forums",))
        return FakeResp(self.msign_err)

    async def get_self_follow_forums(self, pn=1, rn=200):
        return FakeFollow(self.forums)

    async def sign_forum(self, fname):
        self.calls.append(("sign_forum", fname))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.02)
            behavior = self.forum_behavior.get(fname)
            if behavior is None:
                return FakeResp()
            if isinstance(behavior, Exception):
                raise behavior
            return FakeResp(behavior)
        finally:
            self.active -= 1


def install_fake_aiotieba():
    import types
    mod = types.ModuleType("aiotieba")
    mod.Client = FakeClient
    sys.modules["aiotieba"] = mod


def make_state():
    return signer._new_state("test", {
        "id": 1, "name": "测试账号",
        "mask": FAKE_BDUSS[:4] + "****" + FAKE_BDUSS[-4:],
    })


def reset_files():
    for name in (signer._LOG_FILENAME, signer._REPORT_FILENAME):
        path = os.path.join(_PRIVATE, name)
        if os.path.exists(path):
            os.remove(path)


async def run_sign(forums, behavior=None, msign_err=None, growth_err=None):
    client = None
    real_client_cls = FakeClient

    class TrackingClient(real_client_cls):
        def __init__(self, bduss):
            super().__init__(bduss)
            self.forums = forums
            self.forum_behavior = behavior or {}
            self.msign_err = msign_err
            self.growth_err = growth_err
            nonlocal client
            client = self

    import types
    mod = types.ModuleType("aiotieba")
    mod.Client = TrackingClient
    sys.modules["aiotieba"] = mod

    state = make_state()
    signer.SIGN_CONCURRENCY = 4
    signer.SIGN_JITTER = (0, 0)
    await signer._sign_async(state, FAKE_BDUSS)
    signer._persist_report(state)
    return state, client


def test_signer_flow():
    reset_files()
    forums = [FakeForum("吧A"), FakeForum("吧B"), FakeForum("吧C", True),
              FakeForum("bad-forum"), FakeForum("risk-forum"),
              FakeForum("未知吧")]
    behavior = {
        "bad-forum": RuntimeError("server said BDUSS=" + FAKE_BDUSS),
        "risk-forum": FakeErr(340011, ""),
        "未知吧": FakeErr(999999, ""),
    }
    state, client = asyncio.run(run_sign(forums, behavior))

    check("流程状态=done", state["status"] == "done", state["status"])
    check("待签数=5（已签的吧被跳过）",
          state["forum_total"] == 5, state["forum_total"])
    check("成功=2", state["forum_success"] == 2, state["forum_success"])
    check("失败=3", state["forum_failed"] == 3, state["forum_failed"])
    check("并发生效（>=2）", client.max_active >= 2, client.max_active)
    check("并发不超限（<=4）", client.max_active <= 4, client.max_active)
    check("每个未签吧只请求一次",
          sum(1 for c in client.calls if c[0] == "sign_forum") == 5,
          client.calls)

    by_forum = {f["forum"]: f for f in state["failures"]}
    check("340011 带 code", by_forum["risk-forum"]["code"] == 340011)
    check("340011 带中文原因",
          "太快" in (by_forum["risk-forum"]["reason"] or ""))
    check("空 errmsg 不出现空 detail",
          "操作太快" in by_forum["risk-forum"]["detail"])
    check("未知码说明百度没有给出具体原因",
          "百度没有给出具体原因" in by_forum["未知吧"]["detail"])
    check("每个失败都带「怎么办」建议",
          bool(by_forum["未知吧"].get("advice"))
          and bool(by_forum["risk-forum"].get("advice")))
    check("111 的建议是检查吧名/取消关注",
          "吧名" in (tieba_codes.advice(111) or ""))
    check("未知码分类=未知错误",
          by_forum["未知吧"]["category"] == tieba_codes.CAT_UNKNOWN)

    bad_detail = by_forum["bad-forum"]["detail"]
    check("异常里的 BDUSS 已脱敏", FAKE_BDUSS not in bad_detail)
    check("失败详情不出现完整 BDUSS（state 全文）",
          FAKE_BDUSS not in json.dumps(state, ensure_ascii=False))

    log_path = os.path.join(_PRIVATE, signer._LOG_FILENAME)
    log_text = open(log_path, encoding="utf-8").read() if os.path.exists(log_path) else ""
    check("sign.log 已生成", bool(log_text))
    check("sign.log 不含完整 BDUSS", FAKE_BDUSS not in log_text)
    check("sign.log 记录了错误码", "340011" in log_text)

    report_path = os.path.join(_PRIVATE, signer._REPORT_FILENAME)
    report_json = open(report_path, encoding="utf-8").read() \
        if os.path.exists(report_path) else ""
    check("最近报告已落盘", bool(report_json))
    check("最近报告不含完整 BDUSS", FAKE_BDUSS not in report_json)
    check("最近报告保留掩码", state["account_mask"] in report_json)

    text = signer.report_text()
    check("报告文本含错误码 340011", "错误码 340011" in text)
    check("报告文本含中文原因", "操作太快" in text)
    check("报告文本含 BDUSS 掩码", state["account_mask"] in text)
    check("报告文本不含完整 BDUSS", FAKE_BDUSS not in text)
    check("报告文本说明不含完整 BDUSS", "不含完整 BDUSS" in text)


def test_msign_warning():
    reset_files()
    forums = [FakeForum("吧A"), FakeForum("吧B")]
    state, _client = asyncio.run(run_sign(
        forums, msign_err=FakeErr(340011, "")))
    check("msign 失败不改变整体 done", state["status"] == "done")
    check("msign 失败不计 forum_failed", state["forum_failed"] == 0)
    check("msign 失败进 warnings", len(state["warnings"]) == 1)
    warn = state["warnings"][0]
    check("warning 带 code", warn["code"] == 340011)
    check("warning 带 cause", "太快" in (warn["reason"] or ""))
    check("msign 状态为 error", state["msign"]["status"] == "error")
    check("msign 详情非空", bool(state["msign"]["detail"]))


def test_account_gate_failure():
    reset_files()

    class BadUserClient(FakeClient):
        async def get_self_info(self):
            return FakeUser(FakeErr(110000, ""))

    import types
    mod = types.ModuleType("aiotieba")
    mod.Client = BadUserClient
    sys.modules["aiotieba"] = mod

    state = make_state()
    asyncio.run(signer._sign_async(state, FAKE_BDUSS))
    check("账号验证失败 -> status=error", state["status"] == "error")
    check("账号验证失败带 error 分类",
          state["error"]["category"] == tieba_codes.CAT_ACCOUNT)
    check("账号验证失败带普通人能看懂的原因",
          "登录" in (state["error"]["detail"] or "")
          and "重新登录" in (state["error"]["advice"] or ""))


def test_network_error_category():
    exc = TimeoutError("timed out")
    check("超时分类=网络异常",
          signer._classify_exception(exc) == tieba_codes.CAT_NETWORK)


def test_growth_step():
    reset_files()
    forums = [FakeForum("吧A")]
    state, client = asyncio.run(run_sign(
        forums, growth_err=FakeErr(999999, "")))
    check("成长签到调用了 sign_growth", client.growth_calls == 1)
    check("成长签到失败只进 warnings",
          state["status"] == "done" and state["forum_failed"] == 0)
    check("成长签到状态=error", state["growth"]["status"] == "error")
    check("成长签到 warning 带 code",
          state["warnings"][0]["code"] == 999999)

    state2, _client2 = asyncio.run(run_sign(
        forums, growth_err=FakeErr(160002, "")))
    check("成长签到 160002 -> done", state2["growth"]["status"] == "done")
    check("成长签到 160002 不算 warning", state2["warnings"] == [])

    state3, _client3 = asyncio.run(run_sign(forums))
    check("成长签到成功 -> success", state3["growth"]["status"] == "success")


def test_autosign_warning_not_failure():
    """msign 失败只进 warnings；所有吧签到成功时不能判失败/弹通知。"""
    import autosign

    reset_files()
    notified = []
    autosign.notify_fail = lambda *a: notified.append(a)
    account = {"id": 1, "name": "测试账号", "mask": "AbCd****TEST"}
    state = {
        "status": "done",
        "forum_failed": 0,
        "forum_success": 2,
        "msign": {"status": "error", "category": tieba_codes.CAT_RISK,
                  "code": 340011, "reason": "签得太快了",
                  "detail": "错误码 340011：签得太快了"},
        "warnings": [{"forum": "网页一键签到(msign)",
                      "category": tieba_codes.CAT_RISK, "code": 340011,
                      "reason": "签得太快了",
                      "detail": "错误码 340011：签得太快了"}],
        "failures": [],
        "account_id": 1,
    }
    snap = autosign._finalize(account, state)
    check("自动签到：msign 警告不算失败", snap["ok"] is True, snap)
    check("自动签到：快照带 warnings", len(snap["warnings"]) == 1, snap)
    check("自动签到：成功时不弹通知", notified == [], notified)
    check("自动签到：成功时不亮红点", snap["dot_pending"] is False)

    # 真正有失败时：仍要通知，且文案带错误码/原因
    state2 = {
        "status": "done",
        "forum_failed": 1,
        "forum_success": 1,
        "msign": {"status": "success"},
        "warnings": [],
        "failures": [{"forum": "坏吧", "category": tieba_codes.CAT_NO_FORUM,
                      "code": 111, "reason": "该吧不存在",
                      "detail": "错误码 111：该吧不存在"}],
        "account_id": 1,
    }
    snap2 = autosign._finalize(account, state2)
    check("自动签到：真失败时 ok=False", snap2["ok"] is False)
    check("自动签到：真失败时亮红点", snap2["dot_pending"] is True)
    check("自动签到：真失败时弹通知", len(notified) == 1, notified)
    text = autosign._notify_text(account, snap2)
    check("自动签到：通知带错误码 111", "111" in text, text)
    check("自动签到：通知带中文原因", "该吧不存在" in text, text)


def main():
    install_fake_aiotieba()
    print("== 错误码表 =="); test_codes()
    print("== 脱敏 =="); test_redact()
    print("== 签到主流程 =="); test_signer_flow()
    print("== msign 警告 =="); test_msign_warning()
    print("== 账号入口 =="); test_account_gate_failure()
    print("== 网络分类 =="); test_network_error_category()
    print("== 成长等级签到 =="); test_growth_step()
    print("== 自动签到 warnings =="); test_autosign_warning_not_failure()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        return 1
    print("ALL_SIGNER_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
