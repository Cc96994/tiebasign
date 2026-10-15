# -*- coding: utf-8 -*-
"""检查更新 + 界面接线自检（离线）。

运行：python3 scripts/tests/test_ui_update.py   （在仓库根目录）

这个文件针对的是"本轮新增的那条链路"，补的是另外两个套件没覆盖的地方：

  1. **页面渲染**：main.py 里每个 _page_* 都要能渲染出来，而且要用
     "刚装好什么都没有" / "发现新版本" / "已下载" / "检查出错" 四种状态分别
     渲染一遍。waterpost 那次就是一个 .format() 漏了参数导致整页 NameError
     白屏，只有真渲染才会暴露。
  2. **真起一个 HTTP 服务**打这些接口（不是 grep 源码）：GET 页面、
     GET 状态、POST 改设置、POST 安装（没下载时必须如实失败）。
  3. **红点**：只在"真查到东西"时出现，且只挂在设置页那个 tab 上。
  4. **JS 与页面对得上**：页面上出现的按钮 id，全部有对应的 JS 函数。
"""
import json
import os
import sys
import tempfile
import threading
import types

# 自定位：<仓库>/scripts/tests/xxx.py -> <仓库>/app
APP_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "app")
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)
os.environ["ANDROID_PRIVATE"] = tempfile.mkdtemp(prefix="ui_upd_selftest_")

# jnius 只在 Android 上存在；这里用桩，让 main.py 能在桌面上导入
for _name in ("jnius", "jnius.reflect"):
    sys.modules.setdefault(_name, types.ModuleType(_name))
sys.modules["jnius"].autoclass = lambda *a, **k: None
sys.modules["jnius"].JavaMethod = lambda *a, **k: None

import updater  # noqa: E402
import main  # noqa: E402

FAILS = []


def check(name, cond, extra=""):
    print(("PASS  " if cond else "FAIL  ") + name +
          ("" if cond else "   <- " + repr(extra)))
    if not cond:
        FAILS.append(name)


def set_state(**kw):
    """直接摆出 updater 的内存状态（比造 JSON 文件更不容易掩盖 schema 问题）。"""
    data = dict(updater._DEFAULTS)
    data.update(kw)
    updater._cache = data
    return data


# ---------------------------------------------------------------- 页面渲染
def render_all():
    """每个 _page_* 都渲染一遍，四种更新状态各渲染一次。

    带必填参数的页面（_page_weblogin_done 那种）跳过：直接调会 TypeError，
    那是测试写法的问题，不是页面坏了。
    """
    import inspect

    pages = [n for n in dir(main) if n.startswith("_page_")]
    check("main 里找得到页面函数（>=8 个）", len(pages) >= 8, pages)
    callable_pages = []
    for name in pages:
        fn = getattr(main, name)
        if name == "_page" or not callable(fn):
            continue
        required = [p for p in inspect.signature(fn).parameters.values()
                    if p.default is inspect.Parameter.empty
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if required:
            print("SKIP  渲染 {}（需要参数 {}）".format(
                name, ", ".join(p.name for p in required)))
            continue
        callable_pages.append(name)

    states = (
        ("空闲", {}),
        ("发现新版本", {"phase": "available", "dot": True, "detail": "发现 v0.5.0",
                   "latest": {"version": "0.5.0", "version_code": 1023500,
                              "tag": "v0.5.0", "source": "github",
                              "source_label": "GitHub", "apk_name": "a.apk",
                              "sha256": "ab" * 32, "size": 20670000,
                              "notes": "说明", "found_at": "2026-10-16 00:00:00"}}),
        ("已下载", {"phase": "ready", "dot": True, "detail": "已下载并通过校验",
                 "downloaded": {"path": "/data/x.apk", "name": "a.apk",
                                "sha256": "cd" * 32, "size": 20670000,
                                "version": "0.5.0", "version_code": 1023500,
                                "source": "gitee", "ts": "2026-10-16 00:00:00"},
                 "latest": {"version": "0.5.0", "version_code": 1023500,
                            "source_label": "Gitee"}}),
        ("出错且各源都有报告", {"phase": "error", "detail": "网络不可达",
                        "reports": [{"source": "github", "label": "GitHub",
                                     "status": "error", "detail": "HTTP 403"},
                                    {"source": "gitee", "label": "Gitee",
                                     "status": "empty",
                                     "detail": "该源还没有发行版"},
                                    {"source": "x", "label": "X",
                                     "status": "ok", "tag": "V0.2.0",
                                     "releases": 3}],
                        "history": [{"ts": "2026-10-16 00:00:00",
                                     "summary": "查到 v0.5.0"}]}),
    )
    for label, kw in states:
        set_state(**kw)
        for name in callable_pages:
            fn = getattr(main, name)
            try:
                html_text = fn()
            except Exception as exc:  # noqa: BLE001
                check("渲染 {}（{} 状态）".format(name, label), False,
                      "{}: {}".format(type(exc).__name__, exc))
                continue
            check("渲染 {}（{} 状态）".format(name, label),
                  isinstance(html_text, str) and len(html_text) > 200,
                  len(html_text) if isinstance(html_text, str) else html_text)
    set_state()


def page_content_checks():
    set_state()
    settings_html = main._page_settings()
    check("设置页有「检查更新」入口", "/settings/update" in settings_html)
    check("设置页空闲时子标题就是「检查更新」",
          "检查更新" in settings_html and "发现 v" not in settings_html)

    set_state(phase="available", dot=True,
              latest={"version": "0.5.0", "version_code": 1023500})
    check("设置页发现新版本时显示版本号",
          "检查更新 · 发现 v0.5.0" in main._page_settings())

    set_state(phase="ready", dot=True,
              downloaded={"version": "0.5.0", "path": "/x.apk"})
    check("设置页已下载时显示待安装",
          "v0.5.0 已下载，待安装" in main._page_settings())

    # 红点：只在设置页那个 tab 上，且只在真的查到东西时
    set_state(dot=True)
    html_dot = main._page_settings()
    check("红点挂在设置 tab 上", "<span class='dot'></span>" in html_dot)
    set_state(dot=False)
    check("没有新版本时设置页不出现红点",
          "<span class='dot'></span>" not in main._page_settings())
    check("_update_dot() 读不到状态时不抛异常",
          main._update_dot() in (True, False))

    # 更新页：按钮与文案
    set_state(phase="available",
              latest={"version": "0.5.0", "version_code": 1023500})
    page = main._page_settings_update()
    check("更新页有「立即检查」按钮", "id='up_check'" in page and "立即检查" in page)
    check("更新页有「下载更新」按钮", "id='up_download'" in page)
    # 断言的是**按钮元素**，不是 'up_install' 这串字符：_JS_UPDATE 里有
    # function upInstall()，用子串判会永远为真（第一版就是这么写错的）。
    check("更新页此时不该有「立即安装」按钮", "id='up_install'" not in page)
    check("更新页把两个源的说明写出来",
          "GitHub" in page and "Gitee" in page)
    check("更新页显示当前版本",
          "当前版本" in page and "0.4.0" in page, page[:200])

    set_state(phase="ready",
              downloaded={"path": "/data/app/update/a.apk", "size": 20670000,
                          "sha256": "cd" * 32, "version": "0.5.0"})
    page = main._page_settings_update()
    check("已下载时更新页给出「立即安装」按钮", "id='up_install'" in page)
    check("已下载时更新页显示体积（19.7 MB）",
          "19.7 MB" in page, page[:400])

    # 真机上发现的措辞矛盾：查过之后 phase 还是 idle，标题就会写"还没有检查过"，
    # 而下面紧跟着"上次检查：16:34"和"已是最新版本"。
    set_state(phase="idle", detail="已是最新版本 v0.4.0",
              last_check_ts="2026-10-15 16:34:14")
    page = main._page_settings_update()
    check("查过且已是最新时不写「还没有检查过」",
          "还没有检查过" not in page and "已是最新" in page)
    set_state(phase="idle")
    check("从没查过时才写「还没有检查过」",
          "还没有检查过" in main._page_settings_update())

    # 原生入口缺失时要说出来，而不是等用户点了安装才发现
    updater.native_ready = lambda: False
    page = main._page_settings_update()
    check("原生入口不可用时页面明确提示", "安装入口不可用" in page)
    updater.native_ready = lambda: None

    # JS 与页面 id 对得上
    for fn_name in ("upCheck", "upDownload", "upInstall", "upSave"):
        check("_JS_UPDATE 里有 {}".format(fn_name), fn_name in main._JS_UPDATE)
    for el_id in ("up_auto_check", "up_auto_download", "up_wifi_only", "up_mode"):
        check("更新页有控件 {}".format(el_id), el_id in main._page_settings_update())
    check("更新页内联了 _JS_UPDATE",
          "<script>" in main._page_settings_update())
    set_state()


# ------------------------------------------------------------ 真 HTTP 服务
def http_checks():
    import urllib.error
    import urllib.request

    # 这个套件不联网：把"取发行版列表"换成直接失败，于是 /api/update/check
    # 仍然会真的起线程、真的走完流程（失败静默），但一个包都不发出去。
    real_get_json = updater._http_get_json
    updater._http_get_json = lambda *a, **k: (_ for _ in ()).throw(
        OSError("offline self-test"))

    server = main.ThreadingHTTPServer(("127.0.0.1", 0), main.AppHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = "http://127.0.0.1:{}".format(port)

    def get(path):
        with urllib.request.urlopen(base + path, timeout=10) as resp:
            return resp.status, resp.read().decode("utf-8")

    def post(path, obj):
        req = urllib.request.Request(
            base + path, data=json.dumps(obj or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode("utf-8"))

    try:
        set_state()
        status, body = get("/settings")
        check("GET /settings 200", status == 200 and "检查更新" in body)
        status, body = get("/settings/update")
        check("GET /settings/update 200 且是完整页面",
              status == 200 and "up_check" in body and "</html>" in body)
        status, body = get("/api/update/state")
        state = json.loads(body)["state"]
        check("GET /api/update/state 200",
              status == 200 and state["phase"] in
              ("idle", "checking", "available", "downloading", "ready", "error"))
        check("state 带回当前版本与包名",
              state["current"]["package"] == "org.dsh.tiebaweb"
              and state["current"]["version_code"] > 0)

        status, body = post("/api/update/settings", {"auto_check": False})
        check("POST 设置成功", status == 200 and body["ok"] is True)
        check("设置真的生效了", updater.get_settings()["auto_check"] is False)
        status, body = post("/api/update/settings", {"download_mode": "bogus"})
        check("非法下载来源被拒（400）",
              status == 400 and body["ok"] is False, (status, body))
        check("非法值没被写进去",
              updater.get_settings()["download_mode"] == "auto")

        status, body = post("/api/update/install", {})
        check("没下载时点安装如实失败",
              body["ok"] is False and "还没有下载" in body["detail"], body)

        status, body = post("/api/update/check", {})
        check("POST /api/update/check 有明确回应",
              status in (200, 409) and "ok" in body, (status, body))
        check("回应里带 state（界面靠它轮询）", "state" in body)

        status, body = post("/api/update/download", {})
        check("POST /api/update/download 有明确回应",
              status in (200, 409) and "ok" in body, (status, body))
        # 上一次 check 可能已经起了后台线程；别让它带着真网络请求继续跑
        updater._shutdown = True
        for t in (updater._check_thread, updater._download_thread):
            if t is not None:
                t.join(timeout=15)
        updater._shutdown = False
    finally:
        server.shutdown()
        server.server_close()
        updater._http_get_json = real_get_json
    set_state()


# --------------------------------------------------------------- 新加的小件
def unit_checks():
    check("_progress_text 有百分比与 MB",
          updater._progress_text("0.5.0", 5 * 1024 * 1024, 20 * 1024 * 1024)
          == "正在下载 v0.5.0… 25%（5 / 20 MB）",
          updater._progress_text("0.5.0", 5 * 1024 * 1024, 20 * 1024 * 1024))
    check("_progress_text 没 Content-Length 时只说已下多少",
          updater._progress_text("0.5.0", 3 * 1024 * 1024, 0)
          == "正在下载 v0.5.0… 已下 3 MB")
    check("_progress_text 进度不会超过 100%",
          "100%" in updater._progress_text("0.5.0", 30 * 1024 * 1024,
                                          20 * 1024 * 1024))
    set_state(dot=True)
    check("dot_pending 读到 True", updater.dot_pending() is True)
    updater.clear_dot()
    check("clear_dot 之后是 False", updater.dot_pending() is False)
    check("没有 dot 键时 dot_pending 不炸",
          (set_state() is not None) and updater.dot_pending() is False)


def main_():
    print("== 页面渲染 ==")
    render_all()
    print("== 页面内容与红点 ==")
    page_content_checks()
    print("== 真 HTTP 服务 ==")
    http_checks()
    print("== 新增小件 ==")
    unit_checks()
    print()
    print("checks: {}   failed: {}".format(_PASSED[0], len(FAILS)))
    if FAILS:
        print("FAILED: " + "; ".join(FAILS))
        return 1
    print("UI_UPDATE_SELFTEST_OK")
    return 0


_PASSED = [0]
_orig_check = check


def check(name, cond, extra=""):  # noqa: F811
    _orig_check(name, cond, extra)
    _PASSED[0] += 1


if __name__ == "__main__":
    sys.exit(main_())
