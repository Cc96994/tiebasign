# -*- coding: utf-8 -*-
"""随机点赞离线自检（v0.5.0，独立模块版）。

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
    agree_tids = []
    tid_map = {}

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
        tid = FakeClient.tid_map.setdefault(fname,
                                            2 + len(FakeClient.tid_map))
        return FakeThreads([
            FakeThread(1, fname, "置顶", is_top=True),
            FakeThread(tid, fname, "普通帖"),
        ])

    async def agree(self, tid):
        self.agree_calls.append(tid)
        FakeClient.agree_tids.append(tid)
        err = FakeClient.agree_errs.get(tid)
        return FakeResp(err)


ACCOUNT = {"id": 1, "name": "示例账号", "mask": "abCD****wxyz"}


def reset():
    for name in os.listdir(_PRIVATE):
        if name.startswith("tiebasign_like"):
            os.remove(os.path.join(_PRIVATE, name))
    liker._state = None
    liker._cache = None


def wait_done(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = liker.get_state()
        if state.get("status") == "done":
            return state
        time.sleep(0.02)
    raise AssertionError("liker did not finish: {}".format(liker.get_state()))


def run_like(forums, agree_errs=None):
    FakeClient.forums = forums
    FakeClient.agree_errs = agree_errs or {}
    FakeClient.agree_tids = []
    FakeClient.tid_map = {}
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


def main():
    print("== 每吧一个赞 =="); test_one_like_per_forum()
    print("== 每日限制 =="); test_daily_guard_and_next_day()
    print("== 失败隔离 =="); test_failure_isolated()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("ALL_LIKER_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
