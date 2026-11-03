# -*- coding: utf-8 -*-
"""吧群签到离线自检（v0.5.3）。

运行：python3 scripts/tests/test_groupsign.py   （在仓库根目录）

覆盖：
  * 发送成功 -> 当天不再发；第二天可以再发；
  * 自定义文案（群自己的内容优先，空则用默认）；
  * 发送失败 -> 每天最多 3 次，不刷屏；
  * 账号级错误 -> 该账号剩下的群当天不再发；
  * 群分享链接解析、添加/重复添加/删除/改文案；
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
_PRIVATE = tempfile.mkdtemp(prefix="groupsign_selftest_")
os.environ["ANDROID_PRIVATE"] = _PRIVATE

import groupsign  # noqa: E402

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


class FakeClient:
    def __init__(self, send_err=None, join_err=None):
        self.send_err = send_err
        self.join_err = join_err
        self.sent = []
        self.joined = []

    async def join_chatroom(self, chatroom_id):
        self.joined.append(chatroom_id)
        return FakeResp(self.join_err)

    async def send_chatroom_msg(self, chatroom_id, fid, text):
        self.sent.append((chatroom_id, fid, text))
        return FakeResp(self.send_err)


class FakeAioClient(FakeClient):
    def __init__(self, bduss=None):
        super().__init__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


ACCOUNT = {"id": 1, "name": "示例账号", "mask": "abCD****wxyz"}


def reset():
    for name in os.listdir(_PRIVATE):
        if name.startswith("tiebasign_groupsign"):
            os.remove(os.path.join(_PRIVATE, name))
    groupsign._cache = None


def seed(groups, text="@群助手/领取今日福利"):
    data = {"enabled": True, "text": text, "accounts": {
        "1": {"groups": groups, "runs": {}}}}
    groupsign._save(data)


def group(cid, fid, name="签到欢迎群", text=""):
    return {"chatroom_id": cid, "fid": fid, "name": name,
            "forum_name": "守望先锋", "text": text}


def run(client):
    return asyncio.run(groupsign.run_account_async(client, ACCOUNT))


def test_success_and_daily_guard():
    reset()
    seed([group(8184512479, 15965047)])
    client = FakeClient()
    result = run(client)
    check("成功结果 sent=1", result["sent"] == 1, result)
    check("发送内容用默认文案",
          client.sent == [(8184512479, 15965047, "@群助手/领取今日福利")],
          client.sent)
    check("先尝试加入群", client.joined == [8184512479], client.joined)

    client2 = FakeClient()
    result2 = run(client2)
    check("当天第二次跳过", result2["skipped"] == 1 and result2["sent"] == 0,
          result2)
    check("当天第二次不发送", client2.sent == [], client2.sent)

    old = groupsign._today
    groupsign._today = lambda: "2099-01-01"
    try:
        client3 = FakeClient()
        result3 = run(client3)
        check("第二天可以重新发送", result3["sent"] == 1, result3)
        check("第二天真的发了一次", len(client3.sent) == 1, client3.sent)
    finally:
        groupsign._today = old


def test_custom_text():
    reset()
    seed([group(1, 2, text="群专属签到")], text="默认签到")
    client = FakeClient()
    run(client)
    check("群专属文案优先", client.sent[0][2] == "群专属签到", client.sent)
    data = json.loads(open(os.path.join(_PRIVATE, "tiebasign_groupsign.json"),
                          encoding="utf-8").read())
    check("数据文件不含 BDUSS", "bduss" not in json.dumps(data).lower())


def test_failure_attempt_limit():
    reset()
    seed([group(1, 2)])
    for i in range(3):
        result = run(FakeClient(send_err=FakeErr(340006, "")))
        check("失败第 {} 次有 warning".format(i + 1),
              result["failed"] == 1 and len(result["warnings"]) == 1, result)
    client = FakeClient()
    result = run(client)
    check("失败 3 次后当天跳过", result["skipped"] == 1 and not client.sent,
          result)


def test_account_error_stops_remaining():
    reset()
    seed([group(1, 2), group(3, 4)])
    client = FakeClient(join_err=FakeErr(110000, ""))
    result = run(client)
    check("账号错误 -> 失败", result["failed"] == 1, result)
    check("账号错误 -> 不再发后面的群", client.sent == [], client.sent)
    check("账号错误 -> 还有一个群被跳过",
          result["total"] == 2 and result["skipped"] == 1, result)


def test_no_groups_and_disabled():
    reset()
    seed([])
    result = run(FakeClient())
    check("没有群 -> empty", result["status"] == "empty", result)
    check("没有群 -> 有明确提示", "还没有配置" in (result.get("detail") or ""),
          result)
    groupsign._state = None
    rid, err = groupsign.start([(ACCOUNT, "x")])
    check("没有群时按钮直接说明原因",
          rid is None and "还没有配置任何群" in (err or ""), err)

    seed([group(1, 2)])
    data = groupsign._load()
    data["enabled"] = False
    groupsign._save(data)
    client = FakeClient()
    result = run(client)
    check("关闭开关 -> disabled", result["status"] == "disabled", result)
    check("关闭开关 -> 不发送", client.sent == [], client.sent)
    groupsign._state = None
    rid, err = groupsign.start([(ACCOUNT, "x")])
    check("关闭开关时按钮提示去启用",
          rid is None and "开关没有打开" in (err or ""), err)


def test_add_parse_and_manage():
    reset()
    old_fetch = groupsign._fetch_card
    groupsign._fetch_card = lambda cid: ({
        "chatroom_id": int(cid), "fid": 15965047,
        "name": "签到欢迎群", "forum_name": "守望先锋", "text": ""}, None)
    try:
        url = ("https://tieba.baidu.com/mo/q/wise-main-share/chatShare?"
               "chatroom_id=8184512479&sfc=copy")
        added, err = groupsign.add_group(1, url, "自定义")
        check("分享链接能解析出 chatroom_id", added and not err, (added, err))
        check("解析后带 fid/群名",
              added["fid"] == 15965047 and added["name"] == "签到欢迎群",
              added)
        again, err2 = groupsign.add_group(1, url)
        check("重复添加被拒", again is None and "已经添加" in err2, err2)
        groupsign.update_group_text(1, 8184512479, "新文案")
        st = groupsign.get_settings()
        check("改文案生效",
              st["accounts"][0]["groups"][0]["text"] == "新文案", st)
        check("删除群", groupsign.delete_group(1, 8184512479))
        check("删除后为空",
              groupsign.get_settings()["accounts"] == [] or
              not groupsign.get_settings()["accounts"][0]["groups"])
        manual, merr = groupsign.add_group(
            1, "", chatroom_id="8184512479", fid="15965047",
            name="签到欢迎群")
        check("读取群信息失败时可手动填吧ID添加",
              manual and not merr and manual["fid"] == 15965047, merr)
    finally:
        groupsign._fetch_card = old_fetch


def test_start_standalone():
    reset()
    seed([group(8184512479, 15965047)])
    groupsign._state = None
    fake = types.ModuleType("aiotieba")
    fake.Client = FakeAioClient
    sys.modules["aiotieba"] = fake
    rid, err = groupsign.start([(ACCOUNT, "fake-bduss")])
    check("独立模块 start 成功", rid is not None and err is None, err)
    deadline = time.time() + 5
    state = groupsign.get_state()
    while state.get("status") != "done" and time.time() < deadline:
        time.sleep(0.02)
        state = groupsign.get_state()
    check("独立模块跑完", state.get("status") == "done", state)
    sub = (state.get("accounts") or [{}])[0]
    check("独立模块发送成功 1 个群", sub.get("sent") == 1, sub)


def main():
    print("== 成功与每日限制 =="); test_success_and_daily_guard()
    print("== 自定义文案 =="); test_custom_text()
    print("== 失败次数上限 =="); test_failure_attempt_limit()
    print("== 账号错误 =="); test_account_error_stops_remaining()
    print("== 空/关闭 =="); test_no_groups_and_disabled()
    print("== 群管理 =="); test_add_parse_and_manage()
    print("== 独立模块 =="); test_start_standalone()
    print()
    print("{} checks, {} failed".format(TOTAL[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + ", ".join(FAILS))
        return 1
    print("ALL_GROUPSIGN_TESTS_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
