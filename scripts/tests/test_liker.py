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
import tiers  # noqa: E402
import tieba_api  # noqa: E402

# 测试不需要真的等 0.25–0.5 秒：把档位表查表换成一个「零延迟、5 并发」的测试档。
# 真实档位表、clamp 和设置页在 test_signer / test_ui_update 里验证。
_REAL_TIER_GET = tiers.get
_TEST_TIER = tiers.Tier(level=1, name="标准", icon="", sign_concurrency=4,
                        like_concurrency=5, like_delay=(0, 0), color="blue")
_TEST_TIER_GET = lambda level: _TEST_TIER
tiers.get = _TEST_TIER_GET

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
    thread_errs = {}
    instances = []
    agree_active = 0
    agree_peak = 0
    fetch_active = 0
    fetch_peak = 0

    def __init__(self, bduss):
        self.bduss = bduss
        self.agree_calls = []
        self.thread_calls = []
        FakeClient.instances.append(self)

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
            threads = FakeThreads([
                FakeThread(1, fname, "置顶", is_top=True),
                FakeThread(tid, fname, "普通帖"),
            ])
            threads.err = FakeClient.thread_errs.get(fname)
            return threads
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
    FakeClient.thread_errs = {}
    FakeClient.instances = []


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
             agree_delay=0.0, thread_delay=0.0, thread_errs=None):
    reset_counters()
    FakeClient.forums = forums
    FakeClient.agree_errs = agree_errs or {}
    FakeClient.agree_sequence = agree_sequence or {}
    FakeClient.thread_errs = thread_errs or {}
    FakeClient.agree_tids = []
    FakeClient.tid_map = {}
    FakeClient.agree_delay = agree_delay
    FakeClient.thread_delay = thread_delay
    fake_module = types.ModuleType("aiotieba")
    fake_module.Client = FakeClient
    sys.modules["aiotieba"] = fake_module
    rid, err = liker.start([(ACCOUNT, "fake-bduss")])
    if err:
        raise AssertionError(err)
    return wait_done()


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
    state = run_like(forums, agree_errs={2: FakeErr(999999, "")})
    sub = state["accounts"][0]
    check("一个吧失败不影响另一个", sub["success"] == 1 and sub["failed"] == 1,
          sub)
    check("失败详情带中文原因",
          sub["failures"] and bool(sub["failures"][0]["detail"]),
          sub["failures"])
    # 失败的可以重试，成功的当天跳过
    state2 = run_like(forums, agree_errs={2: FakeErr(999999, "")})
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
    check("L1：读盘按批（25 个吧 <= 4 次，不按吧读）",
          len(loads) <= 4, len(loads))
    check("L1：25 个吧最多写 3 次盘（旧实现每吧一次）",
          1 <= len(saves) <= 3, len(saves))
    check("L1：25 个吧全部成功", sub["success"] == 25, sub)

    # 第二轮：同样的失败再失败一次，ok 状态没变，不应触发批量写盘
    reset()
    forums2 = [types.SimpleNamespace(fname="吧{:02d}".format(i))
               for i in range(3)]
    errs = {i: FakeErr(999999, "") for i in range(2, 5)}
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


def test_tier_settings():
    """M3a：点赞档位设置、clamp 与红档未解锁。"""
    reset()
    tiers.get = _REAL_TIER_GET
    try:
        st = liker.get_settings()
        check("点赞档位默认=标准", st["level"] == 1, st)
        st = liker.save_settings({"level": 2})
        check("点赞档位可保存到激进", st["level"] == 2, st)
        check("点赞设置页能读回激进", liker.get_settings()["level"] == 2)
        st = liker.save_settings({"level": 5})
        check("点赞档位可保存到危险+",
              st["level"] == 5 and st["like_concurrency"] == 18, st)
        data = liker._load()
        data["level"] = 99
        liker._save(data)
        st = liker.get_settings()
        check("手改 level=99 压回 5（stored）",
              st["stored_level"] == 5 and st["level"] == 5, st)
        liker.save_settings({"level": 1})
    finally:
        tiers.get = _TEST_TIER_GET


def test_banned_and_invalid_forums():
    """0.6.2：吧已查封跳过；手动标记的失效吧直接过滤。"""
    import invalid

    reset()
    invalid.add_forum("badmark")
    try:
        forums = [types.SimpleNamespace(fname="badmark"),
                  types.SimpleNamespace(fname="吧A")]
        state = run_like(forums)
        sub = state["accounts"][0]
        check("点赞跳过手动标记的失效吧",
              sub["invalid_skipped"] == 1 and sub["total"] == 1, sub)
        check("失效吧没有取帖请求",
              all("badmark" not in c.thread_calls
                  for c in FakeClient.instances),
              [c.thread_calls for c in FakeClient.instances])
    finally:
        invalid.remove_forum("badmark")

    reset()
    forums = [types.SimpleNamespace(fname="吧A"),
              types.SimpleNamespace(fname="吧B")]
    state2 = run_like(forums, thread_errs={"吧B": FakeErr(340006, "")})
    sub2 = state2["accounts"][0]
    check("吧已查封跳过不计失败",
          sub2["failed"] == 0 and sub2["skipped"] >= 1, sub2)
    check("另一个吧正常点赞", sub2["success"] == 1, sub2)
    check("吧已查封自动记入失效贴吧",
          invalid.is_invalid("吧B") is True, invalid.list_forums())
    invalid.remove_forum("吧B")


def test_like_rate_cap():
    """0.6.3：点赞速率封顶 + 当天自适应降速。"""
    reset()
    real_get = _REAL_TIER_GET
    tiers.get = real_get
    try:
        check("标准档速率约 10.5",
              abs(liker._like_rate_for_tier(tiers.get(1)) - 10.5) < 0.2,
              liker._like_rate_for_tier(tiers.get(1)))
        check("极速档速率封顶 12",
              liker._like_rate_for_tier(tiers.get(3)) == 12.0)
        data = {"rate_limit": {"date": liker._today(), "rate": 3.0}}
        check("当天学到的降速优先",
              liker._starting_rate(tiers.get(3), data) == 3.0)
        data2 = {"rate_limit": {"date": "2000-01-01", "rate": 3.0}}
        check("隔天重新按档位",
              liker._starting_rate(tiers.get(3), data2) == 12.0)
    finally:
        tiers.get = _TEST_TIER_GET


def test_pause_flag():
    """0.6.2：点赞暂停标志可切换。"""
    reset()
    check("点赞默认未暂停", liker.is_paused() is False)
    liker.set_paused(True)
    check("点赞暂停标志生效", liker.is_paused() is True)
    liker.set_paused(False)
    check("点赞继续后标志清除", liker.is_paused() is False)


def test_pause_pauses_likes():
    """0.6.2：暂停后不再发新的点赞请求。"""
    reset()
    forums = [types.SimpleNamespace(fname="p{}".format(i))
              for i in range(6)]
    FakeClient.forums = forums
    FakeClient.thread_delay = 0.05
    fake_module = types.ModuleType("aiotieba")
    fake_module.Client = FakeClient
    sys.modules["aiotieba"] = fake_module
    liker.start([(ACCOUNT, "fake-bduss")])
    liker.set_paused(True)
    time.sleep(0.15)
    client = FakeClient.instances[-1] if FakeClient.instances else None
    before = len(client.agree_calls) if client else 0
    time.sleep(0.25)
    after = len(client.agree_calls) if client else 0
    check("暂停后不再发点赞请求", after == before, (before, after))
    liker.set_paused(False)
    wait_done()


def main():
    print("== 每吧一个赞 =="); test_one_like_per_forum()
    print("== 每日限制 =="); test_daily_guard_and_next_day()
    print("== 失败隔离 =="); test_failure_isolated()
    print("== 批量写盘 =="); test_batched_writes()
    print("== 取帖/点赞并发 =="); test_fetch_and_like_concurrency()
    print("== 限流退避 =="); test_rate_limit_cooldown()
    print("== 档位设置 =="); test_tier_settings()
    print("== 查封/失效吧（0.6.2） =="); test_banned_and_invalid_forums()
    print("== 点赞速率上限（0.6.3） =="); test_like_rate_cap()
    print("== 暂停标志（0.6.2） =="); test_pause_flag()
    print("== 暂停请求（0.6.2） =="); test_pause_pauses_likes()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("ALL_LIKER_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
