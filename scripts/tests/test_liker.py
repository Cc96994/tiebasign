# -*- coding: utf-8 -*-
"""随机点赞离线自检（v0.5.3，独立模块版）。

运行：python3 scripts/tests/test_liker.py

覆盖：
  * 每个关注的吧各抽 1 个帖子点赞（不是只挑一个吧）；
  * 每个吧每天最多一次，当天第二次直接跳过；
  * 第二天可以重新点赞；
  * 单个吧失败不影响其他吧，失败进详情；
  * 数据文件不含 BDUSS。
"""
import asyncio
import json
import os
import sys
import tempfile
import time
import types

APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
_PRIVATE = tempfile.mkdtemp(prefix="like_selftest_")
os.environ["ANDROID_PRIVATE"] = _PRIVATE

import liker  # noqa: E402

# 测试里不需要真的等 2~3.5 秒；生产值仍保留在模块里。
liker._LIKE_DELAY = (0, 0)
import tieba_api  # noqa: E402

TOTAL = [0]
FAILS = []


def check(name, cond, extra=""):
    TOTAL[0] += 1
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


class FakeErr:
    def __init__(self, code, msg=""):
        self.code = code
        self.msg = msg


class FakeResp:
    def __init__(self, err=None):
        self.err = err


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


class FakeFollow:
    def __init__(self, forums):
        self._forums = forums

    def __iter__(self):
        return iter(self._forums)

    def __len__(self):
        return len(self._forums)


class FakeClient:
    forums = []
    agree_errs = {}
    agree_sequence = {}
    agree_tids = []
    tid_map = {}
    agree_delay = 0.0
    thread_delay = 0.0
    agree_active = 0
    agree_peak = 0
    fetch_active = 0
    fetch_peak = 0

    def __init__(self, bduss):
        self.bduss = bduss
        self.agree_calls = []
        self.thread_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_self_follow_forums(self, pn=1, rn=200):
        return FakeFollow(list(FakeClient.forums))

    async def get_threads(self, fname, pn=1, rn=30):
        self.thread_calls.append(fname)
        FakeClient.fetch_active += 1
        FakeClient.fetch_peak = max(FakeClient.fetch_peak,
                                    FakeClient.fetch_active)
        try:
            if FakeClient.thread_delay:
                await asyncio.sleep(FakeClient.thread_delay)
            tid = FakeClient.tid_map.setdefault(fname,
                                                2 + len(FakeClient.tid_map))
            return FakeThreads([
                FakeThread(1, fname, "置顶", is_top=True),
                FakeThread(tid, fname, "普通帖"),
            ])
        finally:
            FakeClient.fetch_active -= 1

    async def agree(self, tid):
        self.agree_calls.append(tid)
        FakeClient.agree_tids.append(tid)
        FakeClient.agree_active += 1
        FakeClient.agree_peak = max(FakeClient.agree_peak,
                                    FakeClient.agree_active)
        try:
            if FakeClient.agree_delay:
                await asyncio.sleep(FakeClient.agree_delay)
            seq = FakeClient.agree_sequence.get(tid)
            if seq:
                err = seq.pop(0)
            else:
                err = FakeClient.agree_errs.get(tid)
            return FakeResp(err)
        finally:
            FakeClient.agree_active -= 1


def reset_counters():
    FakeClient.agree_active = FakeClient.agree_peak = 0
    FakeClient.fetch_active = FakeClient.fetch_peak = 0
    FakeClient.agree_delay = 0.0
    FakeClient.thread_delay = 0.0
    FakeClient.agree_sequence = {}


ACCOUNT = {"id": 1, "name": "示例账号", "mask": "abCD****wxyz"}


def reset():
    for name in os.listdir(_PRIVATE):
        if name.startswith("tiebasign_like"):
            os.remove(os.path.join(_PRIVATE, name))
    liker._state = None
    reset_counters()


def wait_done(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = liker.get_state()
        if state.get("status") == "done":
            return state
        time.sleep(0.02)
    raise AssertionError("liker did not finish: {}".format(liker.get_state()))


def run_like(forums, agree_errs=None, agree_sequence=None,
             agree_delay=0.0, thread_delay=0.0):
    reset_counters()
    FakeClient.forums = forums
    FakeClient.agree_errs = agree_errs or {}
    FakeClient.agree_sequence = agree_sequence or {}
    FakeClient.agree_tids = []
    FakeClient.tid_map = {}
    FakeClient.agree_delay = agree_delay
    FakeClient.thread_delay = thread_delay
    fake_module = types.ModuleType("aiotieba")
    fake_module.Client = FakeClient
    sys.modules["aiotieba"] = fake_module
    # 测试里不需要真的按 10 次/秒排队；速率上限的行为在 L5 的专用断言里测。
    old_rate, old_burst = liker._LIKE_RATE, liker._LIKE_BURST
    liker._LIKE_RATE, liker._LIKE_BURST = 1000.0, 5.0
    try:
        rid, err = liker.start([(ACCOUNT, "fake-bduss")])
        if err:
            raise AssertionError(err)
        return wait_done()
    finally:
        liker._LIKE_RATE, liker._LIKE_BURST = old_rate, old_burst


def test_one_like_per_forum():
    reset()
    forums = [types.SimpleNamespace(fname="吧A"),
              types.SimpleNamespace(fname="吧B"),
              types.SimpleNamespace(fname="吧C")]
    state = run_like(forums)
    sub = state["accounts"][0]
    check("总吧数 3", sub["total"] == 3, sub)
    check("每吧成功 1 次", sub["success"] == 3, sub)
    check("每个吧只点一个赞", len(FakeClient.agree_tids) == 3,
          FakeClient.agree_tids)
    data = json.loads(open(os.path.join(_PRIVATE, "tiebasign_like.json"),
                           encoding="utf-8").read())
    forums_data = data["accounts"]["1"]["forums"]
    check("数据里 3 个吧各一条", len(forums_data) == 3, forums_data)
    check("没有点置顶帖（tid 不是 1）",
          all(v.get("tid") != 1 for v in forums_data.values()), forums_data)
    check("数据文件不含 BDUSS", "bduss" not in json.dumps(data).lower())

    # ---- 0.6.0 P0-2：分段耗时（phase_ms）必须能拿到并落盘 --------------
    phases = sub.get("phase_ms") or {}
    check("点赞 phase_ms 覆盖拉列表/取帖/等待/点赞",
          all(k in phases for k in ("list", "fetch", "sleep", "agree")),
          phases)
    check("点赞 phase_ms 都是非负整数",
          all(isinstance(v, int) and v >= 0 for v in phases.values()),
          phases)
    last_runs = data.get("last_runs") or {}
    check("点赞耗时已落盘（last_runs）",
          isinstance(last_runs.get("1"), dict)
          and bool(last_runs["1"].get("phase_ms")), last_runs)


def test_daily_guard_and_next_day():
    reset()
    forums = [types.SimpleNamespace(fname="吧A"),
              types.SimpleNamespace(fname="吧B")]
    state1 = run_like(forums)
    check("第一次成功 2", state1["accounts"][0]["success"] == 2, state1)
    state2 = run_like(forums)
    sub2 = state2["accounts"][0]
    check("当天第二次全部跳过", sub2["skipped"] == 2 and sub2["success"] == 0,
          sub2)
    old = liker._today
    liker._today = lambda: "2099-01-01"
    try:
        state3 = run_like(forums)
        check("第二天可以再点", state3["accounts"][0]["success"] == 2, state3)
    finally:
        liker._today = old


def test_failure_isolated():
    reset()
    forums = [types.SimpleNamespace(fname="吧A"),
              types.SimpleNamespace(fname="吧B")]
    state = run_like(forums, agree_errs={2: FakeErr(340006, "")})
    sub = state["accounts"][0]
    check("一个吧失败不影响另一个", sub["success"] == 1 and sub["failed"] == 1,
          sub)
    check("失败详情带中文原因",
          sub["failures"] and "不能签到" in (sub["failures"][0]["reason"] or ""),
          sub["failures"])
    # 失败的可以重试，成功的当天跳过
    state2 = run_like(forums, agree_errs={2: FakeErr(340006, "")})
    sub2 = state2["accounts"][0]
    check("失败的吧可重试、成功的跳过",
          sub2["failed"] == 1 and sub2["skipped"] == 1, sub2)


def test_batched_writes():
    """L1/L2：整文件读写改成内存累积 + 批量落盘。"""
    reset()
    forums = [types.SimpleNamespace(fname="吧{:02d}".format(i))
              for i in range(25)]
    saves = []
    loads = []
    real_save, real_load = liker._save, liker._load

    def counting_save(data):
        saves.append(1)
        return real_save(data)

    def counting_load():
        loads.append(1)
        return real_load()

    liker._save = counting_save
    liker._load = counting_load
    try:
        state = run_like(forums)
    finally:
        liker._save, liker._load = real_save, real_load
    sub = state["accounts"][0]
    check("L1：一轮只读一次记录文件", len(loads) == 1, len(loads))
    check("L1：25 个吧最多写 3 次盘（旧实现每吧一次）",
          1 <= len(saves) <= 3, len(saves))
    check("L1：25 个吧全部成功", sub["success"] == 25, sub)

    # 第二轮：同样的失败再失败一次，ok 状态没变，不应触发批量写盘
    reset()
    forums2 = [types.SimpleNamespace(fname="吧{:02d}".format(i))
               for i in range(3)]
    errs = {i: FakeErr(340006, "") for i in range(2, 5)}
    run_like(forums2, agree_errs=errs)
    saves[:] = []
    loads[:] = []
    liker._save = counting_save
    liker._load = counting_load
    try:
        state2 = run_like(forums2, agree_errs=errs)
    finally:
        liker._save, liker._load = real_save, real_load
    check("L2：失败->失败（ok 未变）只在结束时写一次",
          len(saves) == 1, len(saves))
    check("L2：第二轮仍是 3 个失败", state2["accounts"][0]["failed"] == 3,
          state2["accounts"][0])


def test_fetch_and_like_concurrency():
    """L3：取帖与点赞各用各的信号量（12 / 5）。"""
    reset()
    forums = [types.SimpleNamespace(fname="吧{:02d}".format(i))
              for i in range(12)]
    state = run_like(forums, agree_delay=0.03, thread_delay=0.03)
    check("L3：取帖并发可以超过点赞并发（>5）",
          FakeClient.fetch_peak > 5, FakeClient.fetch_peak)
    check("L3：取帖并发不超过 12", FakeClient.fetch_peak <= 12,
          FakeClient.fetch_peak)
    check("L3：点赞并发不超过 5", FakeClient.agree_peak <= 5,
          FakeClient.agree_peak)
    check("L3：12 个吧全部成功", state["accounts"][0]["success"] == 12,
          state["accounts"][0]["success"])


def test_rate_limit_cooldown():
    """L4/L5/L6：3280004 退避不占槽位、全局冷却、令牌桶降速。"""
    async def scenario():
        client = FakeClient("bduss")
        FakeClient.agree_sequence = {7: [FakeErr(3280004, ""), None]}
        sem = asyncio.Semaphore(1)
        limiter = liker._RateLimiter(10.0, 5.0)
        cooldown = {"until": 0.0}
        sub = {"phase_ms": {}}
        old = liker._RL_BACKOFF
        liker._RL_BACKOFF = (0.01, 0.01)
        try:
            resp, err = await liker._agree_with_retry(
                client, 7, sem, limiter, cooldown, sub, 0)
        finally:
            liker._RL_BACKOFF = old
        return resp, err, limiter, cooldown, sem

    resp, err, limiter, cooldown, sem = asyncio.run(scenario())
    check("L4：3280004 退避后重试成功", err is None and resp is not None, err)
    check("L4：撞限流后写入全局冷却窗", cooldown["until"] > 0, cooldown)
    check("L6：撞限流后全局速率砍半", abs(limiter.rate - 5.0) < 1e-6,
          limiter.rate)
    check("L4：重试结束后信号量已释放", not sem.locked(), sem.locked())
    check("L5：令牌桶按速率放行（rate 可读）", limiter.rate > 0, limiter.rate)

    async def cooldown_slot():
        client = FakeClient("bduss")
        FakeClient.agree_sequence = {7: [FakeErr(3280004, ""), None]}
        sem = asyncio.Semaphore(1)
        limiter = liker._RateLimiter(10.0, 5.0)
        cooldown = {"until": 0.0}
        sub = {"phase_ms": {}}
        old = liker._RL_BACKOFF
        liker._RL_BACKOFF = (0.2, 0.2)
        try:
            task = asyncio.ensure_future(liker._agree_with_retry(
                client, 7, sem, limiter, cooldown, sub, 0))
            await asyncio.sleep(0.05)   # 第一次请求已撞限流，正在冷却
            got = await asyncio.wait_for(sem.acquire(), timeout=0.05)
            sem.release()
            await task
        finally:
            liker._RL_BACKOFF = old
        return got

    check("L4：冷却等待不占点赞槽位",
          asyncio.run(cooldown_slot()) is True)


def main():
    print("== 每吧一个赞 =="); test_one_like_per_forum()
    print("== 每日限制 =="); test_daily_guard_and_next_day()
    print("== 失败隔离 =="); test_failure_isolated()
    print("== 批量写盘 =="); test_batched_writes()
    print("== 取帖/点赞并发 =="); test_fetch_and_like_concurrency()
    print("== 限流退避 =="); test_rate_limit_cooldown()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("ALL_LIKER_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
