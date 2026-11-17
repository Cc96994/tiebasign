# -*- coding: utf-8 -*-
"""签到引擎离线自检（v0.5.3）。

运行：python3 scripts/tests/test_signer.py   （在仓库根目录）

覆盖的是 v0.5.3 新增/改动的三块，全部离线、不联网、不碰 Android：

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
    check("3280004 原因=操作太频繁",
          "频繁" in (tieba_codes.reason(3280004) or ""))
    check("340001 分类=被限制",
          tieba_codes.category_for_code(340001) == tieba_codes.CAT_RISK)
    check("340001 原因=吧暂时被关闭",
          "关闭" in (tieba_codes.reason(340001) or ""))
    check("340001 建议=换一个吧",
          "换一个吧" in (tieba_codes.advice(340001) or ""))
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
    def __init__(self, forums, has_more=False):
        self._forums = forums
        self.has_more = has_more

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
        self.follow_pages = None
        self.msign_err = None
        self.growth_err = None
        self.forum_behavior = {}
        self.agree_err = None
        self.agree_calls = 0
        self.active = 0
        self.max_active = 0
        self.calls = []
        self.growth_calls = 0
        self.require = None
        FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_self_info(self, require=None):
        self.require = require
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
        self.calls.append(("get_self_follow_forums", pn))
        if self.follow_pages is not None:
            return self.follow_pages.get(pn, FakeFollow([]))
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
    # signer 用 ReqUInfo.BASIC 省掉 profile 请求；测试只关心传了 BASIC。
    mod.ReqUInfo = types.SimpleNamespace(BASIC=1, ALL=2)
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


async def run_sign(forums, behavior=None, msign_err=None, growth_err=None,
                   follow_pages=None):
    client = None
    real_client_cls = FakeClient

    class TrackingClient(real_client_cls):
        def __init__(self, bduss):
            super().__init__(bduss)
            self.forums = forums
            self.follow_pages = follow_pages
            self.forum_behavior = behavior or {}
            self.msign_err = msign_err
            self.growth_err = growth_err
            nonlocal client
            client = self

    import types
    mod = types.ModuleType("aiotieba")
    mod.Client = TrackingClient
    mod.ReqUInfo = types.SimpleNamespace(BASIC=1, ALL=2)
    sys.modules["aiotieba"] = mod

    state = make_state()
    signer.SIGN_JITTER = (0, 0)
    await signer._sign_async(state, FAKE_BDUSS)
    signer._persist_report(state)
    return state, client


def test_signer_flow():
    reset_files()
    forums = [FakeForum("吧A"), FakeForum("吧B"), FakeForum("吧C", True),
              FakeForum("bad-forum"), FakeForum("risk-forum"),
              FakeForum("未知吧"), FakeForum("already-forum")]
    behavior = {
        "bad-forum": RuntimeError("server said BDUSS=" + FAKE_BDUSS),
        "risk-forum": FakeErr(340011, ""),
        "未知吧": FakeErr(999999, ""),
        "already-forum": FakeErr(160002, ""),
    }
    state, client = asyncio.run(run_sign(forums, behavior))

    check("流程状态=done", state["status"] == "done", state["status"])
    check("待签数=6（已签的吧被跳过）",
          state["forum_total"] == 6, state["forum_total"])
    check("成功=2", state["forum_success"] == 2, state["forum_success"])
    check("服务端说已签到=1（不计失败）",
          state["forum_done"] == 1, state.get("forum_done"))
    check("失败=3", state["forum_failed"] == 3, state["forum_failed"])
    check("已签到不算失败（失败列表里没有它）",
          all(f["forum"] != "already-forum" for f in state["failures"]),
          [f["forum"] for f in state["failures"]])
    check("登录只取 BASIC（S1）",
          client.require == sys.modules["aiotieba"].ReqUInfo.BASIC,
          client.require)
    check("并发生效（>=2）", client.max_active >= 2, client.max_active)
    check("并发不超限（<=4）", client.max_active <= 4, client.max_active)
    check("每个未签吧只请求一次",
          sum(1 for c in client.calls if c[0] == "sign_forum") == 6,
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
    check("报告文本含已签到数（不计失败）",
          "已签到 1" in text and "不计失败" in text, text)
    check("报告文本含 BDUSS 掩码", state["account_mask"] in text)
    check("报告文本不含完整 BDUSS", FAKE_BDUSS not in text)
    check("报告文本说明不含完整 BDUSS", "不含完整 BDUSS" in text)

    # ---- 0.6.0 P0-2：phase_ms 分段耗时 --------------------------------
    phases = state.get("phase_ms") or {}
    check("phase_ms 覆盖登录/msign/列表/补签",
          all(k in phases for k in ("login", "msign", "list", "sign")),
          phases)
    check("phase_ms 都是非负整数",
          all(isinstance(v, int) and v >= 0 for v in phases.values()),
          phases)
    check("phase_ms 记录签到请求耗时", "sign_request" in phases, phases)
    report_obj = json.loads(report_json) if report_json else {}
    check("最近报告包含 phase_ms",
          isinstance(report_obj.get("phase_ms"), dict)
          and bool(report_obj["phase_ms"]), report_obj.get("phase_ms"))
    check("报告文本有分段耗时", "分段耗时" in text and "登录" in text, text)
    check("sign.log 记录 phase_ms", "phase_ms=" in log_text)
    line = signer._fmt_phase_ms({"login": 1200, "msign": 3000, "list": 400,
                                 "sign": 9000, "sign_request": 8000,
                                 "sign_sleep": 900})
    check("分段耗时格式化",
          isinstance(line, list) and len(line) == 2
          and "登录 1.2 秒" in line[0] and "补签明细" in line[1], line)


def test_msign_warning():
    reset_files()
    forums = [FakeForum("吧A"), FakeForum("吧B")]
    # 未知错误必须继续逐吧兜底：msign 失败本身不改变整体结果。
    state, _client = asyncio.run(run_sign(
        forums, msign_err=FakeErr(999999, "")))
    check("msign 未知错误不改变整体 done", state["status"] == "done")
    check("msign 未知错误不计 forum_failed", state["forum_failed"] == 0)
    check("msign 未知错误进 warnings", len(state["warnings"]) == 1)
    warn = state["warnings"][0]
    check("warning 带 code", warn["code"] == 999999)
    check("msign 未知错误继续逐吧补签",
          state["forum_success"] == 2, state["forum_success"])
    check("msign 状态为 error", state["msign"]["status"] == "error")
    check("msign 详情非空", bool(state["msign"]["detail"]))


def test_msign_risk_stop():
    """S6：msign 撞风控/验证码时停止逐吧兜底（唯一会少签的改动）。"""
    reset_files()
    forums = [FakeForum("吧A"), FakeForum("吧B"), FakeForum("吧C")]
    state, client = asyncio.run(run_sign(
        forums, msign_err=FakeErr(340011, "")))
    check("风控类 msign 失败 -> 整体 error", state["status"] == "error",
          state["status"])
    check("风控类 msign 失败 -> 标记跳过兜底",
          state.get("fallback_skipped") is True, state)
    check("风控类 msign 失败 -> 没有逐吧请求",
          not any(c[0] == "sign_forum" for c in client.calls), client.calls)
    check("风控类 msign 失败 -> 错误分类=被限制",
          state["error"]["category"] == tieba_codes.CAT_RISK, state["error"])
    check("风控类 msign 失败 -> 错误文案说明已停止",
          "停止逐吧补签" in (state["error"]["detail"] or ""), state["error"])
    report = signer.report_text()
    check("风控即停：报告说明没有继续补签",
          "已停止逐吧补签" in report, report)

    # 验证码类同样停止
    state2, client2 = asyncio.run(run_sign(
        forums, msign_err=FakeErr(4, "")))
    check("验证码类 msign 失败 -> 停止兜底并报错",
          state2["status"] == "error" and
          state2["error"]["category"] == tieba_codes.CAT_CAPTCHA,
          state2.get("error"))
    check("验证码类 msign 失败 -> 没有逐吧请求",
          not any(c[0] == "sign_forum" for c in client2.calls),
          client2.calls)


def test_account_gate_failure():
    reset_files()

    class BadUserClient(FakeClient):
        async def get_self_info(self, require=None):
            return FakeUser(FakeErr(110000, ""))

    import types
    mod = types.ModuleType("aiotieba")
    mod.Client = BadUserClient
    mod.ReqUInfo = types.SimpleNamespace(BASIC=1, ALL=2)
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


def test_follow_pages_prefetch():
    """S4：第一页之后并行预取；最后一页之后的空页不能混进签到列表。"""
    reset_files()
    pages = {
        1: FakeFollow([FakeForum("p1-a"), FakeForum("p1-b")], has_more=True),
        2: FakeFollow([FakeForum("p2-a")], has_more=True),
        3: FakeFollow([FakeForum("p3-a")], has_more=False),
    }
    state, client = asyncio.run(run_sign([], follow_pages=pages))
    check("多页关注列表全部取到", state["forum_total"] == 4,
          state["forum_total"])
    check("页 1/2/3 都请求到了",
          {c[1] for c in client.calls
           if c[0] == "get_self_follow_forums"} >= {1, 2, 3},
          client.calls)
    check("请求没有越过分页上限", all(
        c[1] <= signer.MAX_FORUM_PAGES for c in client.calls
        if c[0] == "get_self_follow_forums"), client.calls)
    check("最后一页之后的空页没有进入补签",
          all(f["forum"] in ("p1-a", "p1-b", "p2-a", "p3-a")
              or f["forum"] == "" for f in state["failures"]), state["failures"])
    check("每个取到的吧各补签一次",
          sum(1 for c in client.calls if c[0] == "sign_forum") == 4,
          client.calls)

    # 第 2 页报错：整体报错，不能拿着半截列表去签
    reset_files()
    bad_page = FakeFollow([])
    bad_page.err = FakeErr(2280007, "")
    bad_pages = {
        1: FakeFollow([FakeForum("p1-a")], has_more=True),
        2: bad_page,
    }
    state2, client2 = asyncio.run(run_sign([], follow_pages=bad_pages))
    check("翻页失败 -> 整体 error", state2["status"] == "error",
          state2["status"])
    check("翻页失败 -> 没有任何补签请求",
          not any(c[0] == "sign_forum" for c in client2.calls),
          client2.calls)


def test_hard_stop_and_breaker():
    """M3b：验证码/登录问题硬停；限流熔断降档后继续。"""
    # 验证码 -> 硬停，剩余不签
    reset_files()
    forums = [FakeForum("cap")] + [FakeForum("ok{}".format(i))
                                   for i in range(11)]
    behavior = {"cap": FakeErr(4, "")}
    state, client = asyncio.run(run_sign(forums, behavior))
    check("验证码 -> 整体 error", state["status"] == "error",
          state["status"])
    check("验证码 -> hard_stopped 有记录",
          bool(state.get("hard_stopped")), state.get("hard_stopped"))
    check("验证码 -> 跳过了一些吧", state["forum_skipped"] > 0,
          state["forum_skipped"])
    check("验证码 -> 实际请求少于总数",
          sum(1 for c in client.calls if c[0] == "sign_forum") < 12,
          client.calls)
    check("验证码 -> 错误文案说明停止",
          "停止剩余补签" in (state["error"]["detail"] or ""), state["error"])

    # 限流 -> 熔断降到保守档，继续跑完剩下的吧
    reset_files()
    forums = [FakeForum("rl")] + [FakeForum("ok{}".format(i))
                                  for i in range(11)]
    behavior = {"rl": FakeErr(340011, "")}
    state2, _client2 = asyncio.run(run_sign(forums, behavior))
    check("限流 -> 不判整体失败", state2["status"] == "done",
          state2["status"])
    check("限流 -> 记录熔断降档", bool(state2.get("breaker")),
          state2.get("breaker"))
    check("限流 -> 熔断目标档=保守",
          state2["breaker"]["to_level"] == 0, state2["breaker"])
    check("限流 -> 其余 11 个吧继续完成",
          state2["forum_checked"] == 12 and state2["forum_success"] == 11,
          (state2["forum_checked"], state2["forum_success"]))
    check("限流 -> 用户档位不变", signer.get_settings()["level"] == 1)
    check("限流 -> 报告写明自动降速",
          "已自动降到「保守」档" in signer.report_text())

    # 保守档仍被限流 -> 硬停
    signer.save_settings({"level": 0})
    reset_files()
    forums = [FakeForum("rl")] + [FakeForum("ok{}".format(i))
                                  for i in range(7)]
    state3, _client3 = asyncio.run(run_sign(
        forums, {"rl": FakeErr(340011, "")}))
    check("保守档仍限流 -> 硬停 error", state3["status"] == "error",
          state3["status"])
    check("保守档仍限流 -> 说明原因",
          "保守档仍然被限流" in (state3["error"]["detail"] or ""),
          state3.get("error"))
    signer.save_settings({"level": 1})


def test_multi_accounts_parallel():
    """S8：多账号并行，闸门最多 2 个账号同时跑。"""
    import time as _time

    reset_files()
    signer._state = None
    active = [0]
    peak = [0]

    async def fake_sign(state, bduss):
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        try:
            await asyncio.sleep(0.05)
            state.update(status="done", step="签到完成",
                         finished_at=signer._now_ms())
        finally:
            active[0] -= 1

    old = signer._sign_async
    signer._sign_async = fake_sign
    try:
        pairs = [({"id": i, "name": "账号{}".format(i),
                   "mask": "mask{}".format(i)}, "bduss-{}".format(i))
                 for i in (1, 2, 3)]
        run_id, err = signer.start_multi(pairs)
        check("多账号任务启动成功", bool(run_id) and err is None, err)
        deadline = _time.time() + 5
        state = signer.get_state()
        while _time.time() < deadline and state.get("status") != "done":
            _time.sleep(0.01)
            state = signer.get_state()
        check("多账号任务跑完", state.get("status") == "done", state)
        check("账号并行闸门=2（不超）", peak[0] == 2, peak[0])
        check("3 个账号都完成", state.get("account_done") == 3,
              state.get("account_done"))
        check("每个账号状态都是 done",
              all(a["status"] == "done" for a in state["accounts"]),
              [a["status"] for a in state["accounts"]])
    finally:
        signer._sign_async = old
        signer._state = None


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


def test_auto_modules():
    """自动吧群签到/随机点赞由 autosign 调度，各模块有开关/时间。"""
    import autosign
    import groupsign
    import liker

    autosign._cache = None
    data = autosign._load()
    data["global_time"] = "08:00"
    data["auto_groupsign"] = True
    data["auto_like"] = True
    data["groupsign_time"] = ""
    data["like_time"] = ""
    data["module_runs"] = {}
    autosign._save(data)

    calls = []
    old_pairs = autosign._all_pairs
    old_g = groupsign.start
    old_l = liker.start
    autosign._all_pairs = lambda: [({"id": 1, "name": "A", "mask": "a"},
                                    "fake-bduss")]
    groupsign.start = lambda pairs: (calls.append("groupsign"), (1, None))[1]
    liker.start = lambda pairs: (calls.append("like"), (1, None))[1]
    try:
        autosign._run_auto_modules(autosign._load(), "2026-11-05", "08:00")
        check("自动模块：吧群签到和随机点赞都在时间到后启动",
              calls == ["groupsign", "like"], calls)
        calls[:] = []
        autosign._run_auto_modules(autosign._load(), "2026-11-05", "08:01")
        check("自动模块：当天第二次不再重复启动", calls == [], calls)
        calls[:] = []
        fresh = autosign._load()
        fresh["auto_groupsign"] = False
        fresh["module_runs"] = {}
        autosign._save(fresh)
        autosign._run_auto_modules(autosign._load(), "2026-11-05", "08:00")
        check("自动模块：关闭开关后只跑随机点赞", calls == ["like"], calls)
    finally:
        autosign._all_pairs = old_pairs
        groupsign.start = old_g
        liker.start = old_l


def test_autosign_warning_not_failure():
    """msign 失败只进 warnings；所有吧签到成功时不能判失败/弹通知。"""
    import autosign

    reset_files()
    autosign._log("selftest log path")
    check("autosign.log 落在 ANDROID_PRIVATE",
          os.path.exists(os.path.join(_PRIVATE, "autosign.log")))
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


def test_tier_settings():
    """M3a：签到档位设置、clamp、红档未解锁与并发接线。"""
    import tiers

    reset_files()
    check("档位表有 6 档", len(tiers.TIERS) == 6)
    check("红档已解锁（M3b）",
          tiers.RED_UNLOCKED is True and tiers.max_open_level() == 5)
    check("颜色按 蓝/蓝/黄/黄/红/红",
          [t.color for t in tiers.TIERS] ==
          ["blue", "blue", "amber", "amber", "red", "red"],
          [t.color for t in tiers.TIERS])
    check("clamp 99 -> 5", tiers.clamp(99) == 5)
    check("clamp -1 -> 0", tiers.clamp(-1) == 0)
    check("clamp 非数字 -> 默认档", tiers.clamp("x") == tiers.DEFAULT_LEVEL)
    check("危险+ 图标带 FE0F", "\ufe0f" in tiers.get(5).icon)
    check("标准档点赞延迟 0.25–0.50",
          tiers.get(1).like_delay == (0.25, 0.50))
    check("标准档点赞速率约 10.5 次/秒",
          abs(tiers.like_rate_per_sec(tiers.get(1)) - 10.5) < 0.2,
          tiers.like_rate_per_sec(tiers.get(1)))
    check("危险+ 点赞速率约 100 次/秒",
          abs(tiers.like_rate_per_sec(tiers.get(5)) - 103) < 2,
          tiers.like_rate_per_sec(tiers.get(5)))

    check("签到档位默认=标准", signer.get_settings()["level"] == 1)
    st = signer.save_settings({"level": 0})
    check("签到档位可保存到保守",
          st["level"] == 0 and st["sign_concurrency"] == 2, st)
    check("红档已解锁（max_open=5）",
          signer.get_settings()["max_open_level"] == 5)
    st = signer.save_settings({"level": 5})
    check("签到档位可保存到危险+",
          st["level"] == 5 and st["sign_concurrency"] == 16, st)
    # 手改配置 99：clamp 到 5 -> 显示危险+（清单 5.6 验收）
    with open(signer._data_path(signer._SETTINGS_FILENAME), "w",
              encoding="utf-8") as fh:
        json.dump({"level": 99}, fh)
    st = signer.get_settings()
    check("手改 level=99 压回危险+",
          st["stored_level"] == 5 and st["level"] == 5
          and st["name"] == "危险+", st)

    # 档位 0（并发 2）要真的限制住并写进运行状态
    signer.save_settings({"level": 0})
    forums = [FakeForum("t{}".format(i)) for i in range(4)]
    state, client = asyncio.run(run_sign(forums))
    check("保守档签到并发 <= 2", client.max_active <= 2, client.max_active)
    check("运行状态带档位", state.get("tier_level") == 0,
          state.get("tier_level"))
    signer.save_settings({"level": 1})


def main():
    install_fake_aiotieba()
    print("== 错误码表 =="); test_codes()
    print("== 脱敏 =="); test_redact()
    print("== 签到主流程 =="); test_signer_flow()
    print("== msign 警告 =="); test_msign_warning()
    print("== msign 风控即停（S6） =="); test_msign_risk_stop()
    print("== 硬停与熔断（M3b） =="); test_hard_stop_and_breaker()
    print("== 账号入口 =="); test_account_gate_failure()
    print("== 网络分类 =="); test_network_error_category()
    print("== 关注列表并行预取（S4） =="); test_follow_pages_prefetch()
    print("== 多账号并行（S8） =="); test_multi_accounts_parallel()
    print("== 成长等级签到 =="); test_growth_step()
    print("== 档位设置（M3a） =="); test_tier_settings()
    print("== 自动签到 warnings =="); test_autosign_warning_not_failure()
    print("== 自动模块 =="); test_auto_modules()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED:", ", ".join(FAILS))
        return 1
    print("ALL_SIGNER_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
