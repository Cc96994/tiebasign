# -*- coding: utf-8 -*-
"""waterpost 离线自检：不联网、不碰 Android，只验证逻辑与页面渲染。

运行：python test_waterpost.py   （在仓库根目录运行；日志写到临时目录，不写进项目）

覆盖：链接解析 / 文案清洗与轮换 / 设置校验 / 错误分类 / 老 store 迁移 /
      每账号分组的计划状态机 / 补发开关 / 硬失败按账号停 / 手动一键水贴 /
      目标组管理 / 两个页面渲染（含花括号防炸）
"""
import os
import re
import sys
import tempfile
import threading
import time
import types

# 自定位：<仓库>/scripts/tests/xxx.py -> <仓库>/app
APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.environ["ANDROID_PRIVATE"] = tempfile.mkdtemp(prefix="wp_selftest_")

# jnius 只存在于 Android；这里用桩替代，让 main.py 能在桌面上导入以便测页面渲染
for _name in ("jnius", "jnius.reflect"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["jnius"].autoclass = lambda *a, **k: None
sys.modules["jnius"].JavaMethod = lambda *a, **k: None

import waterpost  # noqa: E402

REAL_SEND = waterpost._send      # 后面会替换成假的，测 floor 分支时要还原
FAILS = []
CHECKS = []


def check(name, cond, extra=""):
    CHECKS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


# 让调度循环里的 time.sleep 不真的睡（interval 也就能被断言）
class FakeTime(object):
    def __init__(self, real):
        self._real = real
        self.slept = []

    def sleep(self, seconds):
        self.slept.append(seconds)

    def __getattr__(self, name):
        return getattr(self._real, name)


waterpost.time = FakeTime(time)

# ---------------------------------------------------------------- 1 链接解析
check("parse: 完整链接", waterpost.parse_target(
    "https://tieba.baidu.com/p/1234567890")[:2] == (1234567890, None))
check("parse: 分享链接", waterpost.parse_target(
    "https://tieba.baidu.com/p/1234567890?share=9105&fr=sharewise")[0] == 1234567890)
check("parse: 带 pid(#)", waterpost.parse_target(
    "https://tieba.baidu.com/p/1234567890?pid=987654321#987654321")[:2]
      == (1234567890, 987654321))
check("parse: 带 pid(&)", waterpost.parse_target(
    "https://tieba.baidu.com/p/1234567890?pn=2&pid=555666777")[:2]
      == (1234567890, 555666777))
check("parse: 纯 tid", waterpost.parse_target("1234567890")[:2]
      == (1234567890, None))
check("parse: 无关链接报错",
      waterpost.parse_target("https://example.com/abc")[2] is not None)
check("parse: 空串报错", waterpost.parse_target("   ")[2] is not None)

# ---------------------------------------------------------------- 2 文案清洗
check("texts: 空列表回落默认",
      waterpost._normalize_texts([]) == waterpost.default_texts())
check("texts: 上限 30", len(waterpost._normalize_texts(["x"] * 40)) == 30)
check("texts: 去空白", waterpost._normalize_texts(["  a  ", "", "b"]) == ["a", "b"])
check("texts: 默认 10 条", len(waterpost.default_texts()) == 10)

# ---------------------------------------------------------------- 3 文案轮换
d = waterpost._load()
d["texts"] = ["A", "B", "C"]
d["template_mode"] = "sequence"
d["text_seq"] = 0
d["last_text"] = ""
picks = [waterpost._pick_text(d, 1) for _ in range(4)]
check("pick: 顺序轮换 A B C A", picks == ["A", "B", "C", "A"], picks)

d2 = dict(d)
d2["texts"] = ["A", "B"]
d2["text_seq"] = 0
d2["last_text"] = "A"
check("pick: 跳过与上一条相同", waterpost._pick_text(d2, 1) == "B")

d3 = dict(d)
d3["texts"] = ["A"]
d3["text_seq"] = 0
d3["last_text"] = "A"
check("pick: 只有一条时不崩", waterpost._pick_text(d3, 1) == "A")

d4 = dict(d)
d4["template_mode"] = "random"
check("pick: 随机模式在列表内", waterpost._pick_text(d4, 1) in ["A", "B", "C"])

# ---------------------------------------------------------------- 4 老 store 迁移
import json  # noqa: E402

waterpost._cache = None
with open(waterpost._data_path(), "w", encoding="utf-8") as fh:
    json.dump({"enabled": True, "account_id": 7, "daily_count": 5,
               "interval_minutes": 60, "reply_mode": "thread",
               "template_mode": "random", "texts": ["旧文案"],
               "targets": [{"id": 3, "url": "u", "tid": 555, "pid": None,
                            "fname": "f", "title": "t", "enabled": True,
                            "added_at": 0}],
               "plans": {"3": {"date": waterpost._today(), "sent": 2}},
               "results": {"3": {"ok": True}}}, fh, ensure_ascii=False)
data = waterpost._load()
check("migrate: 老目标进账号组",
      data["groups"] == [{"account_id": 7, "targets": [
          {"id": 3, "url": "u", "tid": 555, "pid": None, "fname": "f",
           "title": "t", "enabled": True, "added_at": 0}]}], data["groups"])
check("migrate: 间隔改用新默认 5 秒", data["interval_sec"] == 5,
      data["interval_sec"])
check("migrate: 次数保留 5", data["daily_count"] == 5)
check("migrate: 文案保留", data["texts"] == ["旧文案"])
check("migrate: 轮换方式保留 random", data["template_mode"] == "random")
check("migrate: 计划键补账号前缀",
      data["plans"].get("7:3", {}).get("sent") == 2, data["plans"])
check("migrate: 结果键补账号前缀", "7:3" in data["results"], data["results"])
check("migrate: 版本号写入", data["version"] == 2)
check("migrate: 老键被清掉", "3" not in data["plans"] and "3" not in data["results"])

# ---------------------------------------------------------------- 5 设置校验
waterpost._save(dict(waterpost._DEFAULTS))
try:
    waterpost.save_settings({"texts": []})
    check("save: 空文案被拒绝", False)
except ValueError:
    check("save: 空文案被拒绝", True)
try:
    waterpost.save_settings({"texts": ["x" * 200]})
    check("save: 超长文案被拒绝", False)
except ValueError:
    check("save: 超长文案被拒绝", True)

st = waterpost.save_settings({"daily_count": 999, "interval_sec": 1,
                              "reply_mode": "floor", "template_mode": "random",
                              "makeup": True})
check("save: 次数夹到上限 50", st["daily_count"] == 50, st["daily_count"])
check("save: 间隔夹到下限 %d" % waterpost._MIN_INTERVAL_SEC,
      st["interval_sec"] == 5, st["interval_sec"])
st = waterpost.save_settings({"interval_sec": 99999})
check("save: 间隔夹到上限 3600", st["interval_sec"] == 3600, st["interval_sec"])
st = waterpost.save_settings({"interval_sec": 5})
check("save: 5 秒被接受（用户要的默认值）", st["interval_sec"] == 5)
check("save: floor 被记录", st["reply_mode"] == "floor")
check("save: makeup 被记录", st["makeup"] is True)
check("save: 默认就是 5 秒", waterpost._DEFAULTS["interval_sec"] == 5)
check("settings: 有必备字段", all(k in st for k in (
    "enabled", "daily_count", "interval_sec", "reply_mode", "template_mode",
    "makeup", "texts", "groups", "expected_daily_total", "floor_supported",
    "limits")), sorted(st.keys()))
check("settings: 不再是单账号模型", "account_id" not in st)

# ---------------------------------------------------------------- 6 错误分类
class E1:
    code = 4
    msg = "操作过于频繁，请稍后再试"


class E2:
    code = 1
    msg = "Need verify code"


class E3(Exception):
    def __str__(self):
        return "ClientConnectorError: nope"


class E4:
    code = 5
    msg = "您发布的内容含有敏感词"


check("classify: 频繁->被限制/风控",
      waterpost._classify(E1())["category"] == "被限制/风控")
check("classify: 验证码", waterpost._classify(E2())["category"] == "需要验证码")
check("classify: 网络异常", waterpost._classify(E3())["category"] == "网络异常")
check("classify: 内容违规", waterpost._classify(E4())["category"] == "内容违规")
check("classify: 保留错误码", "4" in waterpost._classify(E1())["detail"])

# ---------------------------------------------------------------- 7 计划状态机
fake_signer = types.ModuleType("signer")
signer_state = {"status": "done", "account_id": 7,
                "finished_at": int(time.time() * 1000)}
fake_signer.get_state = lambda: signer_state
sys.modules["signer"] = fake_signer

sent_log = []
waterpost._send = lambda bduss, target, content, mode: (
    sent_log.append((target["id"], content))
    or {"ok": True, "category": None, "detail": "回复成功"})
waterpost._bduss_for = lambda aid: ("fake-bduss", None)


def load():
    return waterpost._load()


def set_state(**kw):
    signer_state.clear()
    signer_state.update(kw)


def make_target(tid, target_id, enabled=True):
    return {"id": target_id, "url": "u{}".format(target_id), "tid": tid,
            "pid": None, "fname": "f{}".format(target_id),
            "title": "t{}".format(target_id), "enabled": enabled,
            "added_at": 0}


def reset_config(count=3, interval=5, groups=None):
    """groups: [(account_id, [target, ...]), ...]"""
    if groups is None:
        groups = [(7, [make_target(1000, 1)])]
    data = waterpost._load()
    data["enabled"] = True
    data["daily_count"] = count
    data["interval_sec"] = interval
    data["reply_mode"] = "thread"
    data["template_mode"] = "sequence"
    data["texts"] = ["A", "B", "C"]
    data["text_seq"] = 0
    data["last_text"] = ""
    data["makeup"] = False
    data["groups"] = [{"account_id": aid, "targets": [dict(t) for t in targets]}
                      for aid, targets in groups]
    data["plans"] = {}
    data["results"] = {}
    data["last_test"] = None
    waterpost._save(data)
    waterpost.time.slept = []
    del sent_log[:]


def due_now():
    data = waterpost._load()
    for plan in data["plans"].values():
        plan["next_ts"] = time.time() - 1
    waterpost._save(data)


def late_by(seconds):
    data = waterpost._load()
    for plan in data["plans"].values():
        plan["next_ts"] = time.time() - seconds
    waterpost._save(data)


reset_config()
waterpost._tick()
plan = load()["plans"]["7:1"]
check("plan: 键带账号前缀", "7:1" in load()["plans"], sorted(load()["plans"]))
check("plan: 建计划且不立刻发",
      plan["sent"] == 0 and plan["date"] == waterpost._today())
check("plan: 首次延迟 %ds" % waterpost._FIRST_DELAY_SEC,
      waterpost._FIRST_DELAY_SEC - 5 <= plan["next_ts"] - time.time()
      <= waterpost._FIRST_DELAY_SEC + 5, plan["next_ts"] - time.time())
check("plan: 未到点不发", load()["results"] == {} and sent_log == [])

due_now()
waterpost._tick()
check("plan: 到点发第一条",
      load()["plans"]["7:1"]["sent"] == 1 and len(sent_log) == 1, sent_log)
check("plan: 记录了结果", load()["results"]["7:1"]["ok"] is True)
check("plan: 结果带账号与来源",
      load()["results"]["7:1"]["account_id"] == 7
      and load()["results"]["7:1"]["source"] == "auto",
      load()["results"]["7:1"])
check("plan: 文案 A", sent_log == [(1, "A")], sent_log)

late_by(3 * 3600)          # 模拟 App 关了三小时
waterpost._tick()
check("plan: 错过不补发（跳过）",
      load()["plans"]["7:1"]["sent"] == 1 and load()["plans"]["7:1"]["skipped"] == 1,
      load()["plans"]["7:1"])
check("plan: 跳过时不发送", len(sent_log) == 1, sent_log)

for _ in range(3):
    due_now()
    waterpost._tick()
check("plan: 发满次数即停", load()["plans"]["7:1"]["sent"] == 3,
      load()["plans"]["7:1"])
check("plan: 文案依次 A B C", sent_log == [(1, "A"), (1, "B"), (1, "C")],
      sent_log)

due_now()
waterpost._tick()
check("plan: 发满后不再发", len(sent_log) == 3, sent_log)

# ---------------------------------------------------------------- 8 补发开关
reset_config(count=3)
waterpost._save(dict(waterpost._load(), makeup=True))
waterpost._tick()
due_now()
waterpost._tick()
check("makeup: 先发第一条", len(sent_log) == 1, sent_log)
late_by(3 * 3600)
waterpost._tick()
check("makeup: 迟到也发（不跳过）",
      load()["plans"]["7:1"]["sent"] == 2
      and load()["plans"]["7:1"]["skipped"] == 0, load()["plans"]["7:1"])
due_now()
waterpost._tick()
check("makeup: 继续补到满 3 次", load()["plans"]["7:1"]["sent"] == 3,
      load()["plans"]["7:1"])
due_now()
waterpost._tick()
check("makeup: 补满后不再发", load()["plans"]["7:1"]["sent"] == 3,
      load()["plans"]["7:1"])

# ---------------------------------------------------------------- 9 每账号各自开始
reset_config(count=2, groups=[(7, [make_target(1000, 1)]),
                              (8, [make_target(2000, 2)])])
set_state(status="done", mode="multi", accounts=[
    {"account_id": 7, "status": "done",
     "finished_at": int(time.time() * 1000)},
    {"account_id": 8, "status": "error",
     "finished_at": int(time.time() * 1000)}])
waterpost._tick()
check("per-account: 只给签到完成的账号建计划",
      sorted(load()["plans"].keys()) == ["7:1"], sorted(load()["plans"].keys()))
due_now()
waterpost._tick()
check("per-account: 只发那个账号的帖",
      sent_log == [(1, "A")], sent_log)
check("per-account: 另一个账号没结果", "8:2" not in load()["results"])

set_state(mode="multi", accounts=[
    {"account_id": 7, "status": "done",
     "finished_at": int(time.time() * 1000)},
    {"account_id": 8, "status": "done",
     "finished_at": int(time.time() * 1000)}])
waterpost._tick()
check("per-account: 另一个账号签到完成后补建计划",
      "8:2" in load()["plans"], sorted(load()["plans"].keys()))
due_now()
waterpost._tick()
check("per-account: 两个账号各自的帖都发",
      sorted(item[0] for item in sent_log) == [1, 1, 2], sent_log)

# ---------------------------------------------------------------- 10 硬失败按账号停
reset_config(count=2, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
waterpost._send = lambda bduss, target, content, mode: {
    "ok": False, "category": "需要验证码", "detail": "Need verify code"}
waterpost._tick()
due_now()
waterpost._tick()
plans = load()["plans"]
check("hard-stop: 第一个帖记下失败", plans["7:1"]["sent"] == 1, plans["7:1"])
check("hard-stop: 同一轮不再发这个账号的下一个帖",
      plans["7:2"]["sent"] == 0, plans["7:2"])
check("hard-stop: 该账号当天全部目标都停",
      plans["7:1"].get("stopped") == "需要验证码"
      and plans["7:2"].get("stopped") == "需要验证码", plans)
due_now()
waterpost._tick()
check("hard-stop: 后续 tick 也不发",
      load()["plans"]["7:1"]["sent"] == 1 and load()["plans"]["7:2"]["sent"] == 0,
      load()["plans"])

# ---------------------------------------------------------------- 11 网络异常继续
waterpost._send = lambda bduss, target, content, mode: {
    "ok": False, "category": "网络异常", "detail": "timeout"}
reset_config(count=3, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
waterpost._tick()
due_now()
waterpost._tick()
waterpost.time.slept = []       # 只看下面这一轮
due_now()
waterpost._tick()
plans = load()["plans"]
check("soft-fail: 网络异常继续发（两个帖都发了两轮）",
      plans["7:1"]["sent"] == 2 and plans["7:2"]["sent"] == 2
      and not plans["7:1"].get("stopped"), plans)
check("soft-fail: 同一轮两个帖之间只按间隔等一次",
      waterpost.time.slept == [5], waterpost.time.slept)

# ---------------------------------------------------------------- 12 账号凭据坏掉
reset_config(count=2)
waterpost._bduss_for = lambda aid: (None, "该账号没有可用的登录凭据，请删除后重新添加")
waterpost._tick()
due_now()
waterpost._tick()
check("bad-bduss: 记为失败并停止该账号当天",
      load()["results"]["7:1"]["ok"] is False
      and load()["plans"]["7:1"].get("stopped") == "账号失效",
      load()["results"]["7:1"])
waterpost._bduss_for = lambda aid: ("fake-bduss", None)

# ---------------------------------------------------------------- 13 并发保护
reset_config(count=2)
waterpost._send = lambda bduss, target, content, mode: (
    sent_log.append((target["id"], content))
    or {"ok": True, "category": None, "detail": "回复成功"})


def send_and_mutate(bduss, target, content, mode):
    d = waterpost._load()
    d["groups"][0]["targets"].append(make_target(999999, 9))
    waterpost._save(d)
    return {"ok": True, "category": None, "detail": "回复成功"}


waterpost._send = send_and_mutate
waterpost._tick()
due_now()
waterpost._tick()
check("并发: 发送期间新增的目标未被覆盖",
      any(int(t["id"]) == 9 for t in load()["groups"][0]["targets"]),
      [t["id"] for t in load()["groups"][0]["targets"]])
check("并发: 本次进度仍被记录", load()["plans"]["7:1"]["sent"] == 1,
      load()["plans"]["7:1"])

# ---------------------------------------------------------------- 14 楼中楼明确拒绝
waterpost._send = REAL_SEND
reset_config()
waterpost._save(dict(waterpost._load(), reply_mode="floor"))
waterpost._tick()
due_now()
waterpost._tick()
result = load()["results"]["7:1"]
check("floor: 返回暂不支持", result["category"] == "暂不支持", result)
check("floor: 不假装成功", result["ok"] is False)
check("floor: 该账号当天停止", load()["plans"]["7:1"].get("stopped") == "暂不支持",
      load()["plans"]["7:1"])

# ---------------------------------------------------------------- 15 手动一键水贴
def wait_manual(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not waterpost.manual_busy():
            return True
        time.sleep(0.02)
    return False


ok, err = waterpost.start_manual([])
check("manual: 空账号被拒绝", ok is False and "勾选" in err, err)
ok, err = waterpost.start_manual(["abc"])
check("manual: 非法账号被拒绝", ok is False and "勾选" in err, err)

reset_config(count=3)
# 手动水贴跑在后台线程里，而 time.sleep 被上面替换成不睡。于是"启动成功
# -> 立刻断言还在运行"是一场比赛：线程完全可能在断言之前就跑完了（实测在
# /mnt/g 上跑会，在 ext4 上跑不会）。这里用一道闸门把发送卡住，让"正在运行"
# 变成必然为真的事实，而不是靠运气。gate.wait 有超时，卡死也只会失败不会挂住。
_send_gate = threading.Event()


def _gated_send(bduss, target, content, mode):
    _send_gate.wait(5.0)
    sent_log.append((target["id"], content))
    return {"ok": True, "category": None, "detail": "回复成功"}


waterpost._send = _gated_send
ok, err = waterpost.start_manual([7])
check("manual: 启动成功", ok is True and err is None, err)
check("manual: 运行中立即可见", waterpost.manual_busy() is True)
ok2, err2 = waterpost.start_manual([7])
check("manual: 重复启动被拒绝", ok2 is False and "还没结束" in err2, err2)
_send_gate.set()
check("manual: 等待结束", wait_manual() is True)
state = waterpost.get_manual_state()
check("manual: 状态 done", state["status"] == "done", state["status"])
# count=3 -> 每帖把当天 3 次**一次发满**。旧语义是"每帖一条"，用户实测后
# 明确要求改成"发满设置的回复数量，并算作当天已完成"。
check("manual: 统计 3/3 成功", (state["total"], state["finished"],
                              state["success"], state["failed"])
      == (3, 3, 3, 0), state)
check("manual: 明细带账号与文案",
      state["items"][0]["account_name"] == "账号 7"
      and state["items"][0]["content"] == "A"
      and state["items"][0]["ok"] is True, state["items"])
check("manual: 明细标了第几条",
      [(i.get("seq"), i.get("of")) for i in state["items"]]
      == [(1, 3), (2, 3), (3, 3)],
      [(i.get("seq"), i.get("of")) for i in state["items"]])
check("manual: 真的发了 3 条（文案轮换，不重复）",
      sent_log == [(1, "A"), (1, "B"), (1, "C")], sent_log)
# 关键：手动发满后计划里的 sent 也必须到 count，否则自动计划到点会再发一遍，
# 一天就变成 6 条（用户选的是"发满剩余次数并算作当天已完成"）
check("manual: 计入当天次数（sent 到 3）",
      load()["plans"]["7:1"]["sent"] == 3, load()["plans"])
check("manual: 最近一次结果标了 manual",
      load()["results"]["7:1"]["source"] == "manual",
      load()["results"]["7:1"])
check("manual: 手动不发系统通知（无通知通道断言：仅确认不抛错）", True)

# 发满之后：自动计划到点也不该再补发（"算作当天已完成"的另一半）
set_state(status="done", account_id=7, finished_at=int(time.time() * 1000))
waterpost.time.slept = []
before = list(sent_log)
waterpost._tick()
waterpost._tick()
check("manual: 发满后自动计划不再补发", sent_log == before, sent_log)
check("manual: 计划仍是当天那份（没被自动重置）",
      load()["plans"]["7:1"]["date"] == waterpost._today()
      and load()["plans"]["7:1"]["sent"] == 3, load()["plans"]["7:1"])

# 再点一次：今天已经发满 -> 明确拒绝，而不是"显示已启动却什么都没做"
ok, err = waterpost.start_manual([7])
check("manual: 发满后再点被明确拒绝", ok is False and "已经发满" in err, err)

# 两个目标帖 -> 按设置的间隔串行
reset_config(count=3, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
del sent_log[:]
waterpost.time.slept = []
waterpost.start_manual([7])
check("manual: 两个帖等待结束", wait_manual() is True)
check("manual: 两个帖各发满 3 条（共 6 条）", len(sent_log) == 6, sent_log)
check("manual: 按设置间隔 5 秒串行（6 条 = 5 次间隔）",
      waterpost.time.slept == [5, 5, 5, 5, 5], waterpost.time.slept)

# 手动撞墙 -> 停该账号，剩余目标记为跳过；自动计划当天也停
reset_config(count=3, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
waterpost._send = lambda bduss, target, content, mode: {
    "ok": False, "category": "被限制/风控", "detail": "操作过于频繁"}
waterpost.start_manual([7])
check("manual: 撞墙后等待结束", wait_manual() is True)
state = waterpost.get_manual_state()
check("manual: 明细 6 条（1 失败 + 5 跳过）", len(state["items"]) == 6,
      len(state["items"]))
check("manual: 第一条是被限制/风控",
      state["items"][0]["category"] == "被限制/风控", state["items"][0])
check("manual: 后面的都记为跳过",
      all(i["category"] == "跳过" and "已停止" in i["detail"]
          for i in state["items"][1:]), state["items"][1:])
check("manual: 跳过不算失败（汇总里只该有 1 个失败）",
      (state["success"], state["failed"]) == (0, 1),
      (state["success"], state["failed"]))
plans = load()["plans"]
check("manual: 自动计划当天也停（两个目标都停）",
      plans["7:1"].get("stopped") == "被限制/风控"
      and plans["7:2"].get("stopped") == "被限制/风控", plans)

# 一个账号撞墙不影响另一个账号
def fail_by_target(bduss, target, content, mode):
    if int(target["tid"]) == 1000:
        return {"ok": False, "category": "被限制/风控", "detail": "太快了"}
    sent_log.append((target["id"], content))
    return {"ok": True, "category": None, "detail": "回复成功"}


reset_config(count=3, groups=[(7, [make_target(1000, 1)]),
                              (8, [make_target(2000, 2)])])
waterpost._send = fail_by_target
del sent_log[:]
waterpost.start_manual([7, 8])
check("manual: 一个账号撞墙等待结束", wait_manual() is True)
state = waterpost.get_manual_state()
check("manual: 明细 6 条（1 失败 + 2 跳过 + 3 成功）",
      len(state["items"]) == 6, len(state["items"]))
check("manual: 一个账号撞墙不影响另一个账号",
      len(sent_log) == 3 and all(t[0] == 2 for t in sent_log), sent_log)
check("manual: 撞墙的账号自动计划停、另一个不停",
      load()["plans"].get("7:1", {}).get("stopped") == "被限制/风控"
      and not load()["plans"].get("8:2", {}).get("stopped"),
      load()["plans"])

# 失败不占当天额度：一条网络抖动不该白丢次数，再点一次只补差的那几条
reset_config(count=3, groups=[(7, [make_target(1000, 1)])])
_flaky = {"n": 0}


def flaky_send(bduss, target, content, mode):
    _flaky["n"] += 1
    if _flaky["n"] == 1:
        return {"ok": False, "category": "网络异常", "detail": "连接超时"}
    sent_log.append((target["id"], content))
    return {"ok": True, "category": None, "detail": "回复成功"}


waterpost._send = flaky_send
waterpost.start_manual([7])
check("manual: 抖动等待结束", wait_manual() is True)
state = waterpost.get_manual_state()
check("manual: 一条失败两条成功", (state["success"], state["failed"]) == (2, 1),
      (state["success"], state["failed"]))
check("manual: 失败不占当天额度（sent=2 不是 3）",
      load()["plans"]["7:1"]["sent"] == 2, load()["plans"]["7:1"])
del sent_log[:]
ok, err = waterpost.start_manual([7])
check("manual: 没发满就还能再点", ok is True and err is None, err)
check("manual: 再点补差等待结束", wait_manual() is True)
check("manual: 补的正好是差的那 1 条", len(sent_log) == 1, sent_log)
check("manual: 补满后 sent=3", load()["plans"]["7:1"]["sent"] == 3,
      load()["plans"]["7:1"])

# 勾选的账号都没有可做的事 -> 明确报错，不要"按钮显示已启动却什么都没做"
reset_config(count=3, groups=[(7, [make_target(1000, 1)])])
data = load()
data["plans"] = {"7:1": {"date": waterpost._today(), "sent": 3, "skipped": 0,
                         "next_ts": 0, "started_ts": 0, "stopped": None}}
waterpost._save(data)
ok, err = waterpost.start_manual([7])
check("manual: 今天已发满时明确报错", ok is False and "已经发满" in err, err)
ok, err = waterpost.start_manual([9])
check("manual: 账号没有目标组时明确报错",
      ok is False and "目标帖" in err, err)

# 没有目标帖的账号 -> 记为跳过，不崩
waterpost._send = lambda bduss, target, content, mode: (
    sent_log.append((target["id"], content))
    or {"ok": True, "category": None, "detail": "回复成功"})
reset_config(count=3, groups=[(7, [make_target(1000, 1)])])
del sent_log[:]
waterpost.start_manual([7, 8])
check("manual: 无目标账号等待结束", wait_manual() is True)
state = waterpost.get_manual_state()
check("manual: 无目标账号记为跳过",
      any(item["account_id"] == 8 and item["category"] == "跳过"
          for item in state["items"]), state["items"])
check("manual: 有目标账号照常发",
      any(item["account_id"] == 7 and item["ok"] for item in state["items"]),
      state["items"])

# 上限保护
reset_config(count=3, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
old_max = waterpost._MAX_MANUAL_ITEMS
waterpost._MAX_MANUAL_ITEMS = 1
try:
    ok, err = waterpost.start_manual([7])
    check("manual: 超过上限被拒绝", ok is False and "最多发" in err, err)
finally:
    waterpost._MAX_MANUAL_ITEMS = old_max

# 手动运行时自动调度让路
reset_config(count=3)
release = __import__("threading").Event()


def blocking_send(bduss, target, content, mode):
    release.wait(3)
    return {"ok": True, "category": None, "detail": "回复成功"}


waterpost._send = blocking_send
waterpost.start_manual([7])
time.sleep(0.05)
check("manual: 运行期间保活", waterpost.keepalive_needed() is True)
waterpost._save(dict(waterpost._load(), enabled=True))
waterpost._tick()
check("manual: 运行期间自动调度不发（让路）",
      load()["plans"] == {}, load()["plans"])
release.set()
check("manual: 阻塞的发送等待结束", wait_manual() is True)
waterpost._send = lambda bduss, target, content, mode: (
    sent_log.append((target["id"], content))
    or {"ok": True, "category": None, "detail": "回复成功"})

# ---------------------------------------------------------------- 16 send_test
reset_config(count=3)
waterpost.time.slept = []
res = waterpost.send_test()
check("test: 成功返回内容",
      res["ok"] is True and res["content"] in ["A", "B", "C"], res)
check("test: 返回账号与目标", res["target"]["account_id"] == 7, res["target"])
check("test: 记录 last_test",
      load()["last_test"]["ok"] is True
      and load()["last_test"]["account_id"] == 7)
check("test: 测试不占次数", load()["plans"] == {}, load()["plans"])
check("test: 测试也不真的睡", waterpost.time.slept == [], waterpost.time.slept)
waterpost._save(dict(waterpost._load(), groups=[]))
check("test: 无目标时明确报错", waterpost.send_test()["ok"] is False)

# ---------------------------------------------------------------- 17 目标组管理
reset_config(count=3, groups=[(7, [make_target(1000, 1)]),
                              (8, [make_target(2000, 2)])])
check("target: 按账号启停",
      waterpost.set_target_enabled(7, 1, False) is True
      and load()["groups"][0]["targets"][0]["enabled"] is False)
check("target: 用错账号启停无效",
      waterpost.set_target_enabled(8, 1, False) is False)
check("target: 启停不存在的 id",
      waterpost.set_target_enabled(7, 99, False) is False)
check("target: 整组停用",
      waterpost.set_group_enabled(7, False) is True
      and load()["groups"][0]["targets"][0]["enabled"] is False)
check("target: 整组启用",
      waterpost.set_group_enabled(7, True) is True
      and load()["groups"][0]["targets"][0]["enabled"] is True)
check("target: 整组启停不存在的账号",
      waterpost.set_group_enabled(99, True) is False)
check("target: 删掉最后一个目标会移除整组",
      waterpost.delete_target(7, 1) is True
      and [g["account_id"] for g in load()["groups"]] == [8],
      load()["groups"])
check("target: 重复删除", waterpost.delete_target(7, 1) is False)
check("target: 删整组",
      waterpost.delete_group(8) is True and load()["groups"] == [])
check("target: 删不存在的整组", waterpost.delete_group(8) is False)

# 计划/结果随目标一起清理
reset_config(count=3, groups=[(7, [make_target(1000, 1), make_target(2000, 2)])])
waterpost._save(dict(waterpost._load(),
                     plans={"7:1": {"date": waterpost._today(), "sent": 1}},
                     results={"7:1": {"ok": True}}))
waterpost.delete_group(7)
check("target: 删整组会清掉计划与结果",
      load()["plans"] == {} and load()["results"] == {})

# 同一个帖可以同时出现在两个账号的组里
reset_config(count=3, groups=[(7, [make_target(1234567890, 1)])])
dup, dup_err = waterpost.add_target("https://tieba.baidu.com/p/1234567890", 7)
check("target: 同一个账号内重复链接被拒绝",
      dup is None and "已经在这个账号" in (dup_err or ""), dup_err)
same, same_err = waterpost.add_target("https://tieba.baidu.com/p/1234567890", 8)
check("target: 另一个账号加同一个帖不算重复",
      same is None and "已经在这个账号" not in (same_err or ""), same_err)
non, non_err = waterpost.add_target("https://tieba.baidu.com/p/1234567890", None)
check("target: 未选账号时报错",
      non is None and "账号" in (non_err or ""), non_err)
bad, bad_err = waterpost.add_target("https://example.com/not-a-tieba-link", 7)
check("target: 坏链接被拒绝", bad is None and bad_err, bad_err)
# 账号存在性检查要联网之前就拦下（这里把账号表打桩成“只有 7”）
old_map = waterpost._accounts_map
waterpost._accounts_map = lambda: {7: {"name": "主号", "mask": "x"}}
try:
    err, err2 = waterpost.add_target("https://tieba.baidu.com/p/1234567890", 99)
    check("target: 不存在的账号被拒绝（联网之前就拦下）",
          err is None and "不存在" in (err2 or ""), err2)
finally:
    waterpost._accounts_map = old_map

# 保活判断
reset_config(count=3, groups=[(7, [make_target(1000, 1)])])
check("keepalive: 开启+有目标 -> True", waterpost.keepalive_needed() is True)
waterpost._save(dict(waterpost._load(), enabled=False))
check("keepalive: 关闭 -> False", waterpost.keepalive_needed() is False)
reset_config(count=3, groups=[(7, [make_target(1000, 1, enabled=False)])])
check("keepalive: 目标全停用 -> False", waterpost.keepalive_needed() is False)

# ---------------------------------------------------------------- 18 页面渲染
try:
    import main

    main.storage.list_accounts = lambda: [
        {"id": 1, "name": "主号", "mask": "abcd****wxyz", "is_current": True,
         "created_at": 0, "updated_at": 0},
        {"id": 2, "name": "", "mask": "0000****9999", "is_current": False,
         "created_at": 0, "updated_at": 0},
    ]
    waterpost._save(dict(waterpost._load(),
                         groups=[
                             {"account_id": 1, "targets": [
                                 {"id": 1, "url": "u", "tid": 111, "pid": 222,
                                  "fname": "某吧",
                                  "title": "标题里有 {花括号} 也不能炸",
                                  "enabled": True, "added_at": 0}]},
                             {"account_id": 2, "targets": [
                                 {"id": 2, "url": "u2", "tid": 222, "pid": None,
                                  "fname": "另一吧", "title": "第二个帖",
                                  "enabled": False, "added_at": 0}]},
                             {"account_id": 77, "targets": [
                                 {"id": 3, "url": "u3", "tid": 333, "pid": None,
                                  "fname": "残留吧", "title": "账号已删除的帖",
                                  "enabled": True, "added_at": 0}]},
                         ],
                         plans={"1:1": {"date": waterpost._today(), "sent": 2,
                                        "skipped": 1, "next_ts": 0,
                                        "stopped": "需要验证码"}},
                         results={"1:1": {"ts": "2026-10-15 09:00:00",
                                          "ok": False,
                                          "category": "需要验证码",
                                          "detail": "Need verify code",
                                          "content": "A", "fname": "某吧",
                                          "title": "t", "account_id": 1,
                                          "source": "manual"}},
                         last_test={"ts": "2026-10-15 09:00:00", "account_id": 1,
                                    "target_id": 1, "ok": True, "content": "A",
                                    "category": None, "detail": "回复成功"}))

    html_out = main._page_settings_waterpost()
    leftovers = re.findall(r"\{\d+\}", html_out)
    check("page: 渲染成功", "<div class='topbar'>自动水贴" in html_out,
          len(html_out))
    check("page: 没有未替换的占位符", not leftovers, leftovers[:5])
    check("page: 含关键控件", all(k in html_out for k in (
        "wp_enabled", "wp_count", "wp_interval", "wp_mode", "wp_template_mode",
        "wp_makeup", "wp_url_1", "wp_add_btn_1", "wpAddTo", "wpToggle",
        "wpSetGroup", "wpDeleteGroup", "wp_texts", "wpTest", "wpSave",
        "wpResetTexts", "wpDelete(")), [
        k for k in ("wp_enabled", "wp_count", "wp_interval", "wp_mode",
                    "wp_template_mode", "wp_makeup", "wp_url_1", "wp_add_btn_1",
                    "wpAddTo", "wpToggle", "wpSetGroup", "wpDeleteGroup",
                    "wp_texts", "wpTest", "wpSave", "wpResetTexts", "wpDelete(")
        if k not in html_out])
    check("page: 未用原生 confirm()", "confirm(" not in html_out)
    check("page: 楼中楼被禁用", "value='floor' disabled" in html_out)
    check("page: 没有旧的单账号下拉框", "wp_account" not in html_out)
    check("page: 没有旧的分钟间隔", "interval_minutes" not in html_out)
    check("page: 花括号标题不炸",
          "标题里有 {花括号} 也不能炸" in html_out)
    check("page: 两个账号都有添加输入框",
          "wp_url_1" in html_out and "wp_url_2" in html_out)
    check("page: 渲染已发/跳过计数", "今天已发 2 次 · 错过跳过 1 次" in html_out)
    check("page: 渲染停止原因", "今天已停止发送：需要验证码" in html_out)
    check("page: 最近一次标了来源（手动）",
          "最近一次（手动）" in html_out)
    check("page: 渲染测试结果", "最近一次测试发送" in html_out)
    check("page: 残留账号组单独提示",
          "（账号已删除）" in html_out and "删除这组目标帖" in html_out)
    check("page: 每个账号显示自己的预计条数",
          html_out.count("预计每天") >= 3, html_out.count("预计每天"))
    check("page: 间隔单位是秒", "两条回复之间的固定间隔（秒" in html_out)
    check("page: 有补发开关与说明",
          "补发错过的次数" in html_out and "默认<b>不补发</b>" in html_out)
    check("page: 显示发完耗时估算", "内全部发完" in html_out)

    # 账号表为空但 store 里还有目标组：走“残留组”分支，仍要能渲染
    main.storage.list_accounts = lambda: []
    html_orphan = main._page_settings_waterpost()
    check("page: 只剩残留组时给出提示",
          "账号已删除" in html_orphan and "删除这组目标帖" in html_orphan)
    check("page: 残留分支无占位符残留",
          not re.findall(r"\{\d+\}", html_orphan))

    # 账号与目标都为空：走“还没有账号”分支
    saved_groups = waterpost._load()["groups"]
    waterpost._save(dict(waterpost._load(), groups=[]))
    html_none = main._page_settings_waterpost()
    check("page: 没有账号也没有目标时提示先添加账号", "还没有账号" in html_none)
    check("page: 没有账号时无占位符残留",
          not re.findall(r"\{\d+\}", html_none))
    waterpost._save(dict(waterpost._load(), groups=saved_groups))

    # ---- 签到页（一键水贴入口在这里）
    main.storage.list_accounts = lambda: [
        {"id": 1, "name": "主号{0}", "mask": "abcd****wxyz", "is_current": True,
         "created_at": 0, "updated_at": 0},
        {"id": 2, "name": "二号", "mask": "0000****9999", "is_current": False,
         "created_at": 0, "updated_at": 0},
    ]
    sign_html = main._page_sign()
    check("sign: 有一键水贴卡片", "一键水贴" in sign_html and "wp_btn" in sign_html)
    check("sign: 调用了手动水贴接口",
          "/api/waterpost/manual" in sign_html
          and "/api/waterpost/manual/state" in sign_html)
    check("sign: 说清是补满当天剩余次数", "今天还差几条" in sign_html)
    # 旧文案承诺的是"每个帖一条"，与用户实测后要求的语义相反，不能再出现
    check("sign: 不再出现旧的每帖一条说法", "每个帖一条" not in sign_html)
    check("sign: 账号行显示水贴目标数",
          "水贴目标 1 个" in sign_html and "未配置水贴目标" in sign_html,
          [s for s in ("水贴目标 1 个", "未配置水贴目标") if s not in sign_html])
    check("sign: 数据里的花括号不炸渲染", "主号{0}" in sign_html)
    check("sign: 一键签到按钮还在", "sign_btn" in sign_html)
    check("sign: 未用原生 confirm()", "confirm(" not in sign_html)

    main.storage.list_accounts = lambda: []
    empty_html = main._page_sign()
    check("sign: 没有账号时的空页面不含水贴按钮",
          "还没有可用账号" in empty_html and "wp_btn" not in empty_html)
except Exception as exc:  # noqa: BLE001
    import traceback
    check("page: 渲染成功", False, "{}: {}\n{}".format(
        type(exc).__name__, exc, traceback.format_exc()))

print()
print("checks: {}   failed: {}".format(len(CHECKS), len(FAILS)))
if FAILS:
    print("失败 {} 项：{}".format(len(FAILS), FAILS))
    sys.exit(1)
print("WATERPOST_SELFTEST_OK")
