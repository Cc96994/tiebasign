# -*- coding: utf-8 -*-
"""TiebaSign M2.2.2 - account module (webview bootstrap).

Local HTTP UI on 127.0.0.1:5000. PRD pages:
  P2 账号列表  (/)          add / switch / rename / delete
  P3 添加方式  (/add)       manual BDUSS or Baidu WebView login
  P4 手动输入  (/add/manual)
  P5 WebView 登录 (/weblogin)
  settings/diag placeholders
Full BDUSS is never rendered anywhere; only first4****last4.
"""

import html
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

# MUST be set before anything imports google.protobuf.
os.environ.setdefault("PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION", "python")


def _import_marker(msg):
    """Append one line to files/app/py_import_stage.log (crash bisect)."""
    try:
        with open(os.path.join(os.getcwd(), "py_import_stage.log"), "a",
                  encoding="utf-8") as fh:
            fh.write(msg + "\n")
            fh.flush()
    except Exception:
        pass


_import_marker("py:env_set")

import diag  # noqa: E402
_import_marker("py:diag")

import keystore_crypto  # noqa: E402
_import_marker("py:keystore_crypto")

import storage  # noqa: E402
_import_marker("py:storage")

import tieba_api  # noqa: E402
_import_marker("py:tieba_api")

import signer  # noqa: E402
_import_marker("py:signer")

import autosign  # noqa: E402
_import_marker("py:autosign")

import waterpost  # noqa: E402
_import_marker("py:waterpost")

import updater  # noqa: E402
_import_marker("py:updater")

HOST = "127.0.0.1"
PORT = 5000

# PRD MD3 palette
C_PRIMARY = "#2B5CE6"
C_PRIMARY_DARK = "#7C9BFF"
C_BG = "#F6F7FB"
C_BG_DARK = "#0F1117"
C_TEXT = "#141821"
C_TEXT_DARK = "#E6E9F2"
C_CARD = "#FFFFFF"
C_CARD_DARK = "#1A1E29"
C_OK = "#0BA36B"
C_FAIL = "#E5484D"
C_WARN = "#E8930C"
C_MUTED = "#5B6270"

_CSS = """
* { box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
html, body { margin: 0; padding: 0; }
body { background: %(bg)s; color: %(text)s;
  font-family: system-ui, -apple-system, Roboto, "Noto Sans SC", sans-serif;
  -webkit-text-size-adjust: 100%%; padding-bottom: 88px; }
a { color: %(primary)s; }
.topbar { position: sticky; top: 0; z-index: 10; background: %(primary)s;
  color: #fff; padding: 18px 20px 14px; font-size: 20px; font-weight: 700; }
.topbar .sub { font-size: 12px; font-weight: 400; opacity: .85; margin-top: 2px; }
main { padding: 20px; max-width: 560px; margin: 0 auto; }
.card { background: %(card)s; border-radius: 16px; padding: 16px;
  margin-bottom: 14px; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
.card .title { font-weight: 700; font-size: 16px; }
.card .hint { color: %(muted)s; font-size: 13px; margin-top: 6px; line-height: 1.5; }
.btn { display: flex; align-items: center; justify-content: center;
  min-height: 52px; border-radius: 999px; border: 0; font-size: 16px;
  font-weight: 700; padding: 0 24px; cursor: pointer; text-decoration: none; }
.btn-primary { background: %(primary)s; color: #fff; }
.btn-ghost { background: transparent; color: %(primary)s;
  border: 1.5px solid %(primary)s; }
.btn-danger { background: transparent; color: %(fail)s;
  border: 1.5px solid %(fail)s; }
.btn-small { min-height: 40px; font-size: 14px; padding: 0 16px; }
.btn-row { display: flex; gap: 10px; margin-top: 12px; }
.btn-row .btn { flex: 1; }
.acct { display: flex; align-items: center; gap: 12px; }
.acct .avatar { width: 44px; height: 44px; border-radius: 50%%; background: %(primary)s;
  color: #fff; display: flex; align-items: center; justify-content: center;
  font-weight: 700; font-size: 18px; flex: none; }
.acct .info { flex: 1; min-width: 0; }
.acct .name { font-weight: 700; font-size: 16px; overflow: hidden;
  text-overflow: ellipsis; white-space: nowrap; }
.acct .mask { color: %(muted)s; font-size: 13px; margin-top: 2px; }
.badge { display: inline-block; background: %(ok)s; color: #fff; font-size: 11px;
  font-weight: 700; padding: 2px 10px; border-radius: 999px; }
.empty { text-align: center; padding: 48px 20px; color: %(muted)s; }
.nav { position: fixed; left: 0; right: 0; bottom: 0; height: 64px;
  background: %(card)s; border-top: 1px solid rgba(0,0,0,.06);
  display: flex; max-width: 560px; margin: 0 auto; }
.nav a { flex: 1; display: flex; flex-direction: column; align-items: center;
  justify-content: center; gap: 3px; text-decoration: none; color: %(muted)s;
  font-size: 11px; font-weight: 600; position: relative; }
.nav a.active { color: %(primary)s; }
.nav .dot { position: absolute; top: 10px; margin-left: 26px; width: 8px; height: 8px;
  border-radius: 50%%; background: %(fail)s; }
.form label { display: block; font-size: 13px; font-weight: 600; color: %(muted)s;
  margin: 14px 0 6px; }
.input { width: 100%%; min-height: 52px; border-radius: 14px; border: 1.5px solid
  rgba(0,0,0,.14); background: %(bg)s; color: %(text)s; padding: 0 16px;
  font-size: 16px; outline: none; }
.input:focus { border-color: %(primary)s; }
.pw-wrap { position: relative; }
.pw-wrap .eye { position: absolute; right: 6px; top: 6px; height: 40px; width: 52px;
  border: 0; background: transparent; color: %(primary)s; font-size: 13px;
  font-weight: 700; cursor: pointer; }
.errorbox { display: none; background: %(fail)s; color: #fff; border-radius: 12px;
  padding: 12px 14px; margin-top: 14px; font-size: 13px; line-height: 1.5;
  word-break: break-all; }
.okbox { display: none; background: %(ok)s; color: #fff; border-radius: 12px;
  padding: 12px 14px; margin-top: 14px; font-size: 13px; }
.overlay { position: fixed; inset: 0; background: rgba(15,17,23,.55);
  display: none; align-items: center; justify-content: center; z-index: 50; }
.overlay .box { background: %(card)s; color: %(text)s; border-radius: 16px;
  padding: 26px 30px; text-align: center; max-width: 84%%; }
.spinner { width: 26px; height: 26px; border: 3px solid %(primary)s;
  border-top-color: transparent; border-radius: 50%%; margin: 0 auto 14px;
  animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.menu-item { display: flex; align-items: center; justify-content: space-between;
  min-height: 52px; font-size: 15px; font-weight: 600; color: %(text)s;
  text-decoration: none; border-bottom: 1px solid rgba(0,0,0,.05); }
.menu-item:last-child { border-bottom: 0; }
.menu-item .arrow { color: %(muted)s; }
.login-head { background: linear-gradient(150deg, #2B5CE6 0%%, #7C9BFF 100%%);
  color: #fff; padding: 34px 20px 46px; }
.login-head .big { font-size: 24px; font-weight: 800; }
.login-head .sub { font-size: 13px; opacity: .9; margin-top: 6px; }
@media (prefers-color-scheme: dark) {
  body { background: %(bg_dark)s; color: %(text_dark)s; }
  .card, .nav { background: %(card_dark)s; }
  .input { background: %(card_dark)s; color: %(text_dark)s;
    border-color: rgba(255,255,255,.14); }
  a { color: %(primary_dark)s; }
  .btn-primary { background: %(primary_dark)s; color: #10131C; }
  .btn-ghost { color: %(primary_dark)s; border-color: %(primary_dark)s; }
}
""" % {
    "primary": C_PRIMARY, "primary_dark": C_PRIMARY_DARK,
    "bg": C_BG, "bg_dark": C_BG_DARK,
    "text": C_TEXT, "text_dark": C_TEXT_DARK,
    "card": C_CARD, "card_dark": C_CARD_DARK,
    "ok": C_OK, "fail": C_FAIL, "warn": C_WARN, "muted": C_MUTED,
}


def _page(title, body, active_tab="accounts", dot=False, update_dot=False):
    nav = []
    for href, label, tab in (
        ("/sign", "签到", "sign"),
        ("/", "账号", "accounts"),
        ("/settings", "设置", "settings"),
    ):
        cls = "active" if tab == active_tab else ""
        if dot and tab == "sign":
            dot_html = "<span class='dot'></span>"
        elif update_dot and tab == "settings":
            # 「发现新版本」的红点挂在设置页：更新入口在设置里，挂在别处会
            # 让人找不到；没查到东西时 update_dot 就是 False，什么都不显示。
            dot_html = "<span class='dot'></span>"
        else:
            dot_html = ""
        nav.append(
            "<a class='{}' href='{}'>{}{}</a>".format(
                cls, href, html.escape(label), dot_html)
        )
    return """<!doctype html>
<html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>{title} - 贴吧签到助手</title>
<style>{css}</style></head>
<body>{body}
<nav class='nav'>{nav}</nav>
</body></html>""".format(
        title=html.escape(title), css=_CSS, body=body, nav="".join(nav))


def _update_dot():
    """「发现新版本」的红点状态。

    读不到就当没有：少一个红点只是少一次提醒，页面崩掉是另一回事
    （waterpost 那次 NameError 白屏就是这么来的）。
    """
    try:
        return bool(updater.dot_pending())
    except Exception:  # noqa: BLE001
        return False


def _page_accounts():
    accounts = storage.list_accounts()
    rows = []
    for a in accounts:
        initial = (a["name"] or "贴")[0].upper()
        rows.append(
            "<div class='card'><div class='acct'>"
            "<div class='avatar'>{}</div>"
            "<div class='info'><div class='name'>{}{}</div>"
            "<div class='mask'>{}</div></div>"
            "</div>"
            "<div class='btn-row'>"
            "<button class='btn btn-small btn-ghost' "
            "onclick='renameAcct({}, {})'>改名</button>"
            "<button class='btn btn-small btn-primary' "
            "onclick='activateAcct({})'>{}</button>"
            "<button class='btn btn-small btn-danger' "
            "onclick='deleteAcct({}, \"{}\")'>删除</button>"
            "</div></div>".format(
                html.escape(initial),
                html.escape(a["name"] or "贴吧账号"),
                " <span class='badge'>当前</span>" if a["is_current"] else "",
                html.escape(a["mask"]),
                a["id"],
                html.escape(json.dumps(a["name"] or "")),
                a["id"],
                "当前" if a["is_current"] else "设为当前",
                a["id"],
                html.escape(a["name"] or a["mask"]),
            )
        )
    if not rows:
        rows.append(
            "<div class='empty'><p style='font-size:17px;font-weight:700'>"
            "还没有账号</p><p>先添加一个百度账号，才能开始签到。</p></div>"
        )
    add_btn = "<a class='btn btn-primary' href='/add'>＋ 添加账号</a>"
    body = ("<div class='topbar'>账号<div class='sub'>BDUSS 等同账号密码，"
            "请勿向任何人透露</div></div>"
            "<main>{}</main><div style='padding:0 20px;max-width:560px;"
            "margin:0 auto'>{}</div>"
            "<div class='overlay' id='dlg'><div class='box' id='dlgBody'>"
            "</div></div>"
            "<div id='toast' style='display:none;position:fixed;left:50%;"
            "bottom:86px;transform:translateX(-50%);background:#141821;"
            "color:#fff;border-radius:999px;padding:12px 22px;font-size:14px;"
            "max-width:84%%;z-index:60'></div>{}".format(
                "".join(rows), add_btn, _JS_ACCOUNTS))
    return _page("账号", body, "accounts", dot=autosign.dot_pending(),
                 update_dot=_update_dot())


def _page_add():
    body = (
        "<div class='topbar'>添加账号方式</div><main>"
        "<a href='/add/manual' style='text-decoration:none'>"
        "<div class='card'><div class='title'>手动输入 BDUSS</div>"
        "<div class='hint'>从浏览器 Cookie 复制 BDUSS 粘贴进来，"
        "App 验证有效性后才会保存。</div></div></a>"
        "<a href='/login' style='text-decoration:none'>"
        "<div class='card'><div class='title'>百度账号登录（App 内置）</div>"
        "<div class='hint'>在 App 内打开百度官方登录页（顶部带返回栏），"
        "密码、短信、验证码全部由百度官方页面处理，App 不接触、不保存你的"
        "密码；登录成功只从 Cookie 提取 BDUSS 并自动保存。"
        "</div></div></a>"
        "</main>"
    )
    return _page("添加方式", body, "accounts")


def _page_login_native():
    """Built-in login: the official Baidu login page runs inside the app
    WebView with a native blue top bar (back button); no request is
    simulated, no password ever touches our code."""
    body = (
        "<div class='login-head'>"
        "<div class='big'>登录百度账号</div>"
        "<div class='sub'>百度官方登录页 · 内置在 App 中</div></div>"
        "<main>"
        "<div class='card'>"
        "<div class='title'>App 内完成登录</div>"
        "<div class='hint'>点击下方按钮后，百度官方登录页会在 App 内打开，"
        "顶部会保留蓝色的返回栏。你像平常一样登录即可：密码、短信、验证码"
        "全部由百度官方页面处理，<b>App 不接触、不保存你的密码</b>。登录成功"
        "后 App 只从 Cookie 提取 BDUSS，验证有效后自动保存并返回。</div>"
        "</div>"
        "<div class='card'>"
        "<div class='title'>需要时这样做</div>"
        "<div class='hint'>· 想取消：点左上角「‹ 返回」或按返回键<br>"
        "· 登录页停留 5 分钟会自动超时返回<br>"
        "· 登录成功判定：页面跳转到贴吧首页并拿到 BDUSS Cookie</div>"
        "</div>"
        "<div class='errorbox' id='errbox'></div>"
        "<div class='btn-row' style='margin-top:20px'>"
        "<button class='btn btn-primary' id='start_btn' type='button'>"
        "打开百度登录页</button>"
        "</div>"
        "<div class='hint' style='margin-top:16px'>"
        "<a href='/weblogin'>备用：旧版整页网页登录</a>"
        "</div>"
        "</main>"
        "<div class='overlay' id='overlay'>"
        "<div class='box'><div class='spinner'></div>"
        "<div style='font-weight:700'>正在打开百度登录页…</div>"
        "<div style='color:#5B6270;font-size:12px;margin-top:6px'>"
        "页面打开后，顶部蓝色返回栏会一直保留</div></div>"
        "</div>"
        "<script>"
        "document.getElementById('start_btn')"
        ".addEventListener('click',function(){"
        "var b=document.getElementById('start_btn');"
        "b.disabled=true;"
        "document.getElementById('overlay').style.display='flex';"
        "fetch('/api/nativelogin/start',{method:'POST',"
        "headers:{'Content-Type':'application/json'},body:'{}'})"
        ".then(function(r){return r.json();})"
        ".then(function(j){"
        "if(!j.ok){"
        "b.disabled=false;"
        "document.getElementById('overlay').style.display='none';"
        "var e=document.getElementById('errbox');"
        "e.style.display='block';e.textContent=j.detail||'启动失败';"
        "}})"
        ".catch(function(err){"
        "b.disabled=false;"
        "document.getElementById('overlay').style.display='none';"
        "var e=document.getElementById('errbox');"
        "e.style.display='block';e.textContent='请求失败：'+err;"
        "});"
        "});"
        "</script>"
    )
    return _page("百度登录", body, "accounts")



def _page_manual():
    body = """
<div class='topbar'>手动输入 BDUSS</div>
<main>
<div class='card'>
  <div class='hint'>获取方法：浏览器打开贴吧网页版并登录 → 开发者工具 /
  Cookie 里找到 <b>BDUSS</b> → 完整复制（约 190 个字符，含末尾
  <b>=</b>）。<a href='https://aiotieba.cc/tutorial/start/'
  target='_blank' rel='noopener'>图文教程：https://aiotieba.cc/tutorial/start/</a></div>
</div>
<div class='form'>
  <label>账号备注（可选）</label>
  <input class='input' id='acct_name' type='text' maxlength='60'
         placeholder='例如：主号 / 小号' autocomplete='off'>
  <label>BDUSS</label>
  <div class='pw-wrap'>
    <input class='input' id='bduss' type='password' maxlength='300'
           placeholder='粘贴 BDUSS' autocomplete='off' autocapitalize='off'
           spellcheck='false' style='padding-right:64px'>
    <button class='eye' id='eye' type='button'>显示</button>
  </div>
  <div class='hint'>输入框会自动去除首尾空格。</div>
  <div class='errorbox' id='errbox'></div>
  <div class='okbox' id='okbox'></div>
  <div class='btn-row' style='margin-top:20px'>
    <button class='btn btn-primary' id='submit_btn' type='button'>验证并保存</button>
  </div>
</div>
</main>
<div class='overlay' id='overlay'>
  <div class='box'><div class='spinner'></div>
  <div style='font-weight:700'>正在验证 BDUSS…</div>
  <div style='color:#5B6270;font-size:12px;margin-top:6px'>
  通过 aiotieba 查询账号信息，可能需要 5–20 秒</div></div>
</div>
<script>
var eye=document.getElementById('eye');
eye.addEventListener('click',function(){
  var i=document.getElementById('bduss');
  if(i.type==='password'){i.type='text';eye.textContent='隐藏';}
  else{i.type='password';eye.textContent='显示';}
});
function setBusy(b){
  document.getElementById('overlay').style.display=b?'flex':'none';
  document.getElementById('submit_btn').disabled=b;
}
function showErr(s){
  var e=document.getElementById('errbox');e.style.display='block';e.textContent=s;
  document.getElementById('okbox').style.display='none';
}
document.getElementById('submit_btn').addEventListener('click',function(){
  var name=document.getElementById('acct_name').value.trim();
  var bduss=document.getElementById('bduss').value.trim();
  if(!bduss){showErr('请先粘贴 BDUSS');return;}
  setBusy(true);
  fetch('/api/accounts',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({name:name,bduss:bduss})})
  .then(function(r){return r.json();})
  .then(function(j){
    setBusy(false);
    if(j.ok){location.href='/';}
    else{showErr((j.category||'错误')+'：'+(j.detail||''));}
  })
  .catch(function(e){setBusy(false);showErr('请求失败：'+e);});
});
</script>
"""
    return _page("手动输入 BDUSS", body, "accounts")


def _page_sign():
    accounts = storage.list_accounts()
    if not accounts:
        body = (
            "<div class='topbar'>签到</div><main>"
            "<div class='empty'><p style='font-size:17px;font-weight:700'>"
            "还没有可用账号</p><p>先添加一个百度账号，再回来签到。</p></div>"
            "<a class='btn btn-primary' href='/add'>＋ 添加账号</a></main>"
        )
        return _page("签到", body, "sign")

    current = storage.get_current_account()
    current_id = current.get("id") if current else None

    # 每个账号配了几个启用的水贴目标帖：只作提示（它是「一键水贴」的依据，
    # 与签到无关）。waterpost 出问题不能连带签到页打不开，所以整段吞异常。
    wp_counts = {}
    try:
        for group in waterpost.get_settings()["groups"]:
            if not group.get("orphan"):
                wp_counts[int(group["account_id"])] = \
                    int(group["enabled_target_count"])
    except Exception as exc:  # noqa: BLE001
        print("[app] waterpost counts unavailable: {}: {}".format(
            type(exc).__name__, exc))
        wp_counts = {}

    rows = []
    for acct in accounts:
        checked = " checked" if acct.get("id") == current_id else ""
        badge = " · 当前账号" if acct.get("is_current") else ""
        count = wp_counts.get(int(acct["id"]), 0)
        if count:
            wp_hint = ("<span style='color:#0BA36B;font-size:12px'>"
                       " · 水贴目标 {} 个</span>").format(count)
        else:
            wp_hint = ("<span style='color:#8A90A0;font-size:12px'>"
                       " · 未配置水贴目标</span>")
        rows.append(
            "<label style='display:flex;align-items:center;gap:10px;"
            "padding:10px 2px;border-bottom:1px solid rgba(127,127,127,.16)'>"
            "<input type='checkbox' class='acct_cb' value='{}'{} "
            "style='width:18px;height:18px;accent-color:#2B5CE6'>"
            "<span><b style='font-size:15px'>{}</b>"
            "<span style='color:#7C9BFF;font-size:12px;font-weight:700'>{}</span>"
            "<br><span style='color:#8A90A0;font-size:12px'>BDUSS {}</span>{}"
            "</span></label>".format(
                acct["id"], checked,
                html.escape(acct["name"] or "贴吧账号"),
                html.escape(badge),
                html.escape(acct["mask"]),
                wp_hint)
        )
    body = (
        "<div class='topbar'>签到</div><main>"
        "<div class='card'><div class='title'>选择要签到的账号（可多选）</div>"
        "<div class='hint'>默认勾选当前账号；也可手动勾选多个账号依次签到。"
        "这一组勾选同时用于下面的「一键签到」和「一键水贴」。</div>"
        "{}</div>"
        "<div class='card'><div class='title'>一键签到</div>"
        "<div class='hint'>网页一键签到 + 关注吧查漏补签，单次执行不重试。"
        "结果只显示在下方；手动签到失败不会发系统通知、不显示红点。</div>"
        "<div class='btn-row' style='margin-top:12px'>"
        "<button class='btn btn-primary' id='sign_btn' type='button'>"
        "开始一键签到</button></div></div>"
        "<div id='sign_state'></div>"
        "<div class='card'><div class='title'>一键水贴</div>"
        "<div class='hint'>立刻把勾选账号<b>今天还差几条</b>一次发完。比如设置"
        "每天 4 次，每个目标帖就补到 4 条；发完今天就不再自动发。<br>"
        "用的是「自动水贴设置」里的目标帖和文案，每条之间隔你设置的秒数；"
        "某条失败不占次数，再点一次就能补上。<br>"
        "手动失败不发通知、不显示红点。执行期间请让 App 留在前台，"
        "全部发完才会结束。</div>"
        "<div class='btn-row' style='margin-top:12px'>"
        "<button class='btn btn-primary' id='wp_btn' type='button'>"
        "开始一键水贴</button></div></div>"
        "<div id='wp_state'></div></main>{}".format(
            "".join(rows), _JS_SIGN)
    )
    return _page("签到", body, "sign")


def _page_settings():
    # 更新入口的状态子标题：只在真有新版本/已下载时才变长，平时就是一句
    # 「检查更新」。检查失败和"还没有发行版"都不在这里显示任何东西。
    update_label = "检查更新"
    try:
        ust = updater.get_state()
        latest = ust.get("latest") or {}
        done = ust.get("downloaded") or {}
        if done.get("version"):
            update_label = "检查更新 · v{} 已下载，待安装".format(
                done["version"])
        elif latest.get("version"):
            update_label = "检查更新 · 发现 v{}".format(latest["version"])
    except Exception:  # noqa: BLE001
        pass
    body = (
        "<div class='topbar'>设置</div><main>"
        "<div class='card'><a class='menu-item' href='/settings/update'>"
        "<span>{}<span class='arrow'>›</span></span></a>".format(
            html.escape(update_label))
        + "<a class='menu-item' href='/diag'>"
        "<span>运行诊断自检</span><span class='arrow'>›</span></a>"
        "<a class='menu-item' href='/settings/auto'>"
        "<span>自动签到设置</span><span class='arrow'>›</span></a>"
        "<a class='menu-item' href='/settings/waterpost'>"
        "<span>自动水贴设置</span><span class='arrow'>›</span></a>"
        "</div>"
        "<div class='card'><div class='title'>关于</div>"
        "<div class='hint'>贴吧签到助手 0.4.0 · aiotieba 4.7.1<br>"
        "BDUSS 使用 Android Keystore AES-GCM 加密，仅保存在本机。</div>"
        "</div></main>"
    )
    return _page("设置", body, "settings", dot=autosign.dot_pending(),
                 update_dot=_update_dot())


_PHASE_TEXT = {
    "idle": "还没有检查过",
    "checking": "正在检查…",
    "available": "发现新版本",
    "downloading": "正在下载",
    "ready": "已下载，可以安装",
    "error": "检查出错了",
}


def _page_settings_update():
    """检查更新页：状态 + 三个动作按钮 + 开关 + 各源结果 + 历史。"""
    st = updater.get_state()
    cur = st["current"]
    phase = st["phase"]
    latest = st.get("latest") or {}
    done = st.get("downloaded") or {}

    phase_text = _PHASE_TEXT.get(phase, phase)
    # "idle" 有两种含义：从来没查过，和查过了但已是最新。两者都写成"还没有
    # 检查过"会出现"还没有检查过"紧挨着"上次检查：16:34"这种自相矛盾的画面
    # （真机上就是这样），所以有检查记录时改说"已是最新"。
    if phase == "idle" and st.get("last_check_ts"):
        phase_text = "已是最新"
    detail = html.escape(str(st.get("detail") or ""))
    if phase == "checking" or phase == "downloading" or detail:
        state_lines = "<div class='title'>{}</div><div class='hint'>{}</div>".format(
            html.escape(phase_text), detail)
    else:
        state_lines = "<div class='title'>{}</div>".format(
            html.escape(phase_text))

    rows = ["<div class='hint'>当前版本 <b>v{}</b>（versionCode {}）</div>".format(
        html.escape(cur["version"]), cur["version_code"])]
    if st.get("last_check_ts"):
        rows.append("<div class='hint'>上次检查：{}</div>".format(
            html.escape(str(st["last_check_ts"]))))
    if latest.get("version"):
        rows.append(
            "<div class='hint'>最新版本 <b>v{}</b>（versionCode {}）"
            " · 来自 {}{}</div>".format(
                html.escape(str(latest["version"])),
                latest.get("version_code", "?"),
                html.escape(str(latest.get("source_label", ""))),
                " · 已通过 sha256 校验" if done.get("version") else ""))
    if done.get("size"):
        rows.append("<div class='hint'>已下载 {}（sha256 {}…）</div>".format(
            _size_text(done["size"]), html.escape(str(done.get("sha256", ""))[:12])))
    else:
        rows.append("<div class='hint'>下载完成后会先比对 sha256，再用系统"
                    "安装界面安装；<b>不会静默安装</b>。</div>")

    # ---- 按钮：按阶段决定给哪些，避免点了没反应的按钮 --------------------
    btns = ["<button class='btn btn-ghost' id='up_check' type='button' "
            "onclick='upCheck()'>立即检查</button>"]
    if done.get("path"):
        btns.append("<button class='btn btn-primary' id='up_install' "
                    "type='button' onclick='upInstall()'>立即安装</button>")
    elif latest.get("version"):
        btns.append("<button class='btn btn-primary' id='up_download' "
                    "type='button' onclick='upDownload()'>下载更新</button>")

    native_note = ""
    if st.get("native_ready") is False:
        native_note = ("<div class='card'><div class='title' style='color:{}'>"
                       "安装入口不可用</div><div class='hint'>{}</div></div>"
                       ).format(C_WARN, html.escape(str(st.get("native_note") or "")))

    # ---- 各源这次的结果 ---------------------------------------------------
    report_rows = []
    for rep in st.get("reports") or []:
        status = rep.get("status")
        color = {"ok": C_OK, "empty": C_MUTED}.get(status, C_WARN)
        if status == "ok":
            text = "最新 {}（共 {} 个发行版）".format(
                html.escape(str(rep.get("tag", ""))), rep.get("releases", 0))
        else:
            text = html.escape(str(rep.get("detail", status)))
        report_rows.append(
            "<div class='hint'><b style='color:{}'>{}</b> · {}</div>".format(
                color, html.escape(str(rep.get("label", ""))), text))
    if not report_rows:
        # 同一件事的第二处措辞：查过之后不该再说"还没有检查过"。
        report_rows.append("<div class='hint'>{}</div>".format(
            "本次没有记录到来源结果。" if st.get("last_check_ts")
            else "还没有检查过。"))

    # ---- 历史 -------------------------------------------------------------
    hist_rows = []
    for item in (st.get("history") or [])[:5]:
        hist_rows.append("<div class='hint'>{} · {}</div>".format(
            html.escape(str(item.get("ts", ""))),
            html.escape(str(item.get("summary", "")))))
    if not hist_rows:
        hist_rows.append("<div class='hint'>没有历史记录。</div>")

    st_settings = st["settings"]
    mode = st_settings["download_mode"]
    toggles = (
        "<label class='menu-item'><span>每天自动检查一次</span>"
        "<input type='checkbox' id='up_auto_check'{0}></label>"
        "<div class='hint'>关掉之后只有你手动点「立即检查」才会查。"
        "检查失败不弹通知、不亮红点，只有真查到新版本才有提示。</div>"
        "<label class='menu-item'><span>查到就自动下载</span>"
        "<input type='checkbox' id='up_auto_download'{1}></label>"
        "<div class='hint'>关掉后只提示有新版本，下载要你自己点。</div>"
        "<label class='menu-item'><span>只在 Wi-Fi 下自动下载</span>"
        "<input type='checkbox' id='up_wifi_only'{2}></label>"
        "<div class='hint'>移动网络下只提示、不自动下载（安装包约 20MB）。"
        "手动点「下载更新」不受这条限制。</div>"
        "<label>下载来源</label>"
        "<select class='input' id='up_mode'>"
        "<option value='auto'{3}>自动：GitHub 优先，太慢切 Gitee</option>"
        "<option value='github'{4}>只用 GitHub</option>"
        "<option value='gitee'{5}>只用 Gitee</option>"
        "</select>"
        "<div class='hint'>自动模式会先试 GitHub，开始下载后 {6} 秒内下不到 "
        "{7}KB（约 100KB/s）就断开改从 Gitee 下。</div>").format(
        " checked" if st_settings["auto_check"] else "",
        " checked" if st_settings["auto_download"] else "",
        " checked" if st_settings["wifi_only"] else "",
        " selected" if mode == "auto" else "",
        " selected" if mode == "github" else "",
        " selected" if mode == "gitee" else "",
        st_settings["limits"]["probe_sec"], st_settings["limits"]["probe_min_kb"])

    src_desc = "、".join(
        html.escape(str(s["label"])) for s in st.get("sources") or [])
    # 注意：这里**不能**对拼好的整段再调 .format()。_JS_UPDATE 里有大量
    # JavaScript 花括号（function(){...}），会被当成替换字段；report_rows 之类
    # 也已经各自 format 过了。这正是水贴页当初白屏的同一类坑，所以按同样的
    # 写法：只用 + 拼接，format 只作用在单个小片段上，JS 最后再贴上。
    body = (
        "<div class='topbar'>检查更新<div class='sub'>从 " + src_desc
        + "</div></div><main>"
          "<div class='card'>" + state_lines + "".join(rows) + "</div>"
          "<div class='card'><div class='btn-row' style='margin-top:0'>"
        + "".join(btns)
        + "</div><div id='up_out'></div></div>"
        + native_note
        + "<div class='card'><div class='title'>本次各来源的结果</div>"
        + "".join(report_rows) + "</div>"
        + "<div class='card'><div class='title'>设置</div>" + toggles + "</div>"
        + "<div class='card'><div class='title'>最近几次</div>"
        + "".join(hist_rows) + "</div>"
        + "<div class='card'><div class='title'>它是怎么验的</div>"
          "<div class='hint'>只从上面这两个仓库的<b>正式发行版</b>下载；"
          "文件下完先比对发布端的 sha256，装之前再读一遍这个 APK 的包名、"
          "versionCode 和<b>签名指纹</b>，三项都对才交给系统安装器。"
          "签名这一关是关键：sha256 也是从同一台服务器取的，挡不住被篡改的"
          "发布端，而别人的签名伪造不出来。</div></div>"
          "<div class='errorbox' id='up_err'></div>"
          "<div class='okbox' id='up_ok'></div>"
          "</main>" + _JS_UPDATE
    )
    return _page("检查更新", body, "settings")


def _size_text(num):
    try:
        num = int(num)
    except (TypeError, ValueError):
        return "?"
    if num >= 1024 * 1024:
        return "{:.1f} MB".format(num / 1024.0 / 1024.0)
    return "{} KB".format(max(1, num // 1024))


def _page_settings_auto():
    st = autosign.get_settings()
    acc_rows = []
    for a in st["accounts"]:
        follow = bool(a["follow_global"])
        acc_rows.append(
            "<div class='card'>"
            "<div class='acct'><div class='avatar'>{}</div>"
            "<div class='info'><div class='name'>{}</div>"
            "<div class='mask'>{}</div></div></div>"
            "<div class='form'>"
            "<label>时间来源</label>"
            "<select class='input' id='acc_mode_{}' onchange='accMode({})'>"
            "<option value='follow' {}>跟随全局（{}）</option>"
            "<option value='custom' {}>自定义时间</option>"
            "</select>"
            "<div id='acc_time_wrap_{}' style='display:{}'>"
            "<label>自定义时间</label>"
            "<input class='input' type='time' id='acc_time_{}' value='{}'>"
            "</div></div></div>".format(
                html.escape((a["name"] or "贴")[0].upper()),
                html.escape(a["name"] or "贴吧账号"),
                html.escape(a["mask"]),
                a["id"], a["id"],
                "selected" if follow else "",
                html.escape(st["global_time"]),
                "selected" if not follow else "",
                a["id"],
                "block" if not follow else "none",
                a["id"], html.escape(a["custom_time"]),
            )
        )
    if not acc_rows:
        acc_rows.append(
            "<div class='empty'><p>还没有账号</p>"
            "<p>自动签到会对全部已保存账号执行，先到「账号」页添加。</p></div>"
        )

    result_rows = []
    for r in autosign.last_results():
        color = C_OK if r["ok"] else C_FAIL
        label = "全部成功" if r["ok"] else "有失败"
        fail_txt = ""
        if r["failures"]:
            shown = "；".join("{}（{}）".format(f.get("forum", ""),
                                                f.get("category", ""))
                              for f in r["failures"][:3])
            fail_txt = ("<div class='hint'>" + html.escape(shown) + "</div>")
        result_rows.append(
            "<div class='card'><div class='acct'>"
            "<div class='info'><div class='name'>{}</div>"
            "<div class='mask'>{}</div></div>"
            "<span class='badge' style='background:{}'>{}</span></div>"
            "<div class='hint'>成功 {} · 失败 {}{}</div></div>".format(
                html.escape(r["name"] or "贴吧账号"),
                html.escape(r["ts"]),
                color, label, r["success"], r["failed"], fail_txt)
        )
    if not result_rows:
        result_rows.append(
            "<div class='empty'><p>还没有自动签到结果</p>"
            "<p>开启自动签到并到点执行后，最近一次结果会显示在这里。</p></div>"
        )

    html_part = (
        "<div class='topbar'>自动签到设置</div><main>"
        "<div class='card'><div class='title'>开关</div>"
        "<label class='menu-item'><span>启用自动签到</span>"
        "<input type='checkbox' id='enabled'{}></label>"
        "<div class='hint'>开启后会显示一条常驻通知「自动签到运行中」以保持后台运行"
        "（卓易通内保活效果可能受限，App 被彻底关闭时会在下次打开后补跑）。</div>"
        "</div>"
        "<div class='card'><div class='title'>全局时间</div>"
        "<div class='form'><label>每天签到时间（默认 08:00）</label>"
        "<input class='input' type='time' id='global_time' value='{}'>"
        "</div></div>"
        "<div class='card'><div class='title'>仅失败通知（双开关）</div>"
        "<label class='menu-item'><span>签到失败系统通知</span>"
        "<input type='checkbox' id='notify_system'{}></label>"
        "<label class='menu-item'><span>App 内失败红点</span>"
        "<input type='checkbox' id='notify_dot'{}></label>"
        "<div class='hint'>自动签到全部成功时不会提醒；只有出现失败才按上面两个"
        "开关分别通知。手动签到永不通知、永不出红点。</div></div>"
        "<div class='card'><div class='title'>各账号时间</div>{}</div>"
        "<div class='card'><div class='title'>最近一次自动签到结果</div>{}</div>"
        "<div class='btn-row'>"
        "<button class='btn btn-primary' id='save_btn' type='button'>保存设置</button>"
        "<button class='btn btn-ghost' id='run_now_btn' type='button'>"
        "立即执行一次（测试）</button></div>"
        "<div class='errorbox' id='errbox'></div>"
        "</main>"
        "<div id='toast' style='display:none;position:fixed;left:50%;"
        "bottom:86px;transform:translateX(-50%);background:#141821;"
        "color:#fff;border-radius:999px;padding:12px 22px;font-size:14px;"
        "max-width:84%;z-index:60'></div>"
    ).format(
        " checked" if st["enabled"] else "",
        html.escape(st["global_time"]),
        " checked" if st["notify_system"] else "",
        " checked" if st["notify_dot"] else "",
        "".join(acc_rows), "".join(result_rows))
    script = """
<script>
function toast(msg){var t=document.getElementById('toast');
t.textContent=msg;t.style.display='block';
setTimeout(function(){t.style.display='none';},3500);}
function accMode(id){
var m=document.getElementById('acc_mode_'+id);
document.getElementById('acc_time_wrap_'+id).style.display=
(m.value==='custom')?'block':'none';}
function showErr(s){var e=document.getElementById('errbox');
e.style.display='block';e.textContent=s;}
function collectAccounts(){
var out={};
document.querySelectorAll('[id^=acc_mode_]').forEach(function(m){
var id=m.id.split('_').pop();
out[id]={follow_global:(m.value==='follow'),
custom_time:document.getElementById('acc_time_'+id).value};});
return out;}
document.getElementById('save_btn').addEventListener('click',function(){
var payload={enabled:document.getElementById('enabled').checked,
global_time:document.getElementById('global_time').value,
notify_system:document.getElementById('notify_system').checked,
notify_dot:document.getElementById('notify_dot').checked,
accounts:collectAccounts()};
fetch('/api/auto/settings',{method:'POST',
headers:{'Content-Type':'application/json'},
body:JSON.stringify(payload)}).then(function(r){return r.json();})
.then(function(j){if(j.ok){toast('设置已保存');setTimeout(function()
{location.reload();},600);}else{showErr(j.detail||'保存失败');}})
.catch(function(e){showErr('请求失败：'+e);});});
document.getElementById('run_now_btn').addEventListener('click',function(){
var b=document.getElementById('run_now_btn');b.disabled=true;
b.textContent='已触发，稍候…';
fetch('/api/auto/run-now',{method:'POST',
headers:{'Content-Type':'application/json'},body:'{}'})
.then(function(r){return r.json();}).then(function(j){
if(j.ok){toast('已触发：对全部账号立即执行一次自动签到（结果在下方，有失败才会通知/红点）');}else{toast('触发失败：'+(j.detail||''));
b.disabled=false;b.textContent='立即执行一次（测试）';}})
.catch(function(){b.disabled=false;b.textContent='立即执行一次（测试）';});});
</script>
"""
    body = html_part + script
    return _page("自动签到设置", body, "settings")


def _duration_text(seconds):
    """把秒数说成人话（用于「预计多久发完」）。"""
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return ""
    if seconds < 90:
        return "{} 秒".format(seconds)
    if seconds < 5400:
        return "约 {} 分钟".format(int(round(seconds / 60.0)))
    return "约 {:.1f} 小时".format(seconds / 3600.0)


def _wp_group_card(account_id, name, mask, group, orphan):
    """一个账号的目标组卡片：这个账号只发下面这些帖。"""
    rows = []
    for target in group.get("targets") or []:
        pid_txt = " · pid {}".format(target["pid"]) if target["pid"] else ""
        extra = ""
        if target.get("stopped"):
            extra += ("<div class='hint' style='color:#E5484D'>今天已停止发送："
                      "{0}</div>").format(html.escape(str(target["stopped"])))
        if target.get("last"):
            last = target["last"]
            color = C_OK if last.get("ok") else C_FAIL
            src = {"manual": "（手动）", "test": "（测试）"}.get(
                last.get("source"), "")
            extra += ("<div class='hint'>最近一次{0}：<b style='color:{1}'>{2}</b>"
                      " {3}<br>{4}</div>").format(
                src, color, "成功" if last.get("ok") else "失败",
                html.escape(str(last.get("ts", ""))),
                html.escape(str(last.get("detail", ""))[:140]))
        rows.append(
            "<div style='border-bottom:1px solid rgba(127,127,127,.16);"
            "padding:10px 0'>"
            "<b style='font-size:15px'>{0}</b>"
            "<div class='hint'>{1}吧 · tid {2}{3}<br>"
            "今天已发 {4} 次 · 错过跳过 {5} 次</div>{6}"
            "<div class='btn-row' style='margin-top:8px'>"
            "<label style='display:flex;align-items:center;gap:8px;"
            "font-size:14px'>"
            "<input type='checkbox' {7} "
            "onchange='wpToggle({8},{9},this.checked)'>启用</label>"
            "<button class='btn btn-small btn-danger' type='button' "
            "style='flex:none;padding:0 18px' "
            "onclick='wpDelete({8},{9},this)'>删除</button>"
            "</div></div>".format(
                html.escape(target["title"] or "(无标题主题帖)"),
                html.escape(target["fname"]), target["tid"], pid_txt,
                target["today_sent"], target["today_skipped"], extra,
                "checked" if target["enabled"] else "", account_id,
                target["id"]))
    if not rows:
        rows.append("<div class='hint'>这个账号还没有目标帖，"
                    "把链接粘贴到下面添加。</div>")

    if orphan:
        head = ("<div class='hint' style='color:#E5484D'>这个账号已经不在账号"
                "列表里（可能被删除了），它的目标帖不会发送。</div>")
        footer = ("<div class='hint'>账号已删除，只能清理这组配置。</div>"
                  "<div class='btn-row'><button class='btn btn-small "
                  "btn-danger' type='button' "
                  "onclick='wpDeleteGroup({0},this)'>删除这组目标帖</button>"
                  "</div>").format(account_id)
    else:
        head = ("<div class='hint'>{0} 个已启用 · 预计每天 {1} 条</div>"
                ).format(int(group.get("enabled_target_count", 0)),
                         int(group.get("expected_daily_total", 0)))
        footer = (
            "<div class='form'><label>把这个主题帖添加给本账号</label>"
            "<input class='input' id='wp_url_{0}' type='text' "
            "autocomplete='off' spellcheck='false' "
            "placeholder='https://tieba.baidu.com/p/1234567890'></div>"
            "<div class='btn-row'>"
            "<button class='btn btn-small btn-primary' type='button' "
            "id='wp_add_btn_{0}' onclick='wpAddTo({0})'>解析并添加</button>"
            "<label style='display:flex;align-items:center;gap:8px;"
            "font-size:14px'><input type='checkbox' "
            "onchange='wpSetGroup({0},this.checked)'>整组启用/停用</label>"
            "</div>").format(account_id)

    mask_html = ""
    if mask:
        mask_html = " <span style='color:#8A90A0;font-size:12px'>{}</span>".format(
            html.escape(mask))
    return ("<div class='card'><div class='title'>{0}{1}</div>{2}{3}{4}</div>"
            ).format(html.escape(name), mask_html, head, "".join(rows), footer)


def _page_settings_waterpost():
    """自动水贴设置页：每个账号一组目标帖 + 全局文案与频率。"""
    st = waterpost.get_settings()
    accounts = storage.list_accounts()
    lim = st["limits"]

    groups = {}
    for group in st["groups"]:
        groups[int(group["account_id"])] = group

    cards = []
    for acct in accounts:
        aid = int(acct["id"])
        group = groups.get(aid) or {"targets": [],
                                    "enabled_target_count": 0,
                                    "expected_daily_total": 0}
        cards.append(_wp_group_card(aid, acct["name"] or "贴吧账号",
                                    acct.get("mask") or "", group, False))
    for group in st["groups"]:
        if group["orphan"]:
            cards.append(_wp_group_card(int(group["account_id"]),
                                        "（账号已删除）", "", group, True))
    if not cards:
        cards.append(
            "<div class='empty'><p>还没有账号</p>"
            "<p>先到「账号」页添加一个百度账号，再回来配置水贴目标。</p></div>")

    # ---- 文案列表 -----------------------------------------------------
    text_rows = []
    for text in st["texts"]:
        text_rows.append(
            "<div style='display:flex;gap:8px;align-items:center;"
            "margin-bottom:8px'>"
            "<input class='input wp_text' maxlength='{0}' value='{1}' "
            "style='flex:1;min-height:46px'>"
            "<button class='btn btn-small btn-ghost' type='button' "
            "style='flex:none' onclick='wpRemoveText(this)'>删除</button>"
            "</div>".format(lim["max_text_len"],
                            html.escape(text, quote=True)))

    # ---- 最近一次测试 -------------------------------------------------
    test_result = ""
    if st.get("last_test"):
        lt = st["last_test"]
        color = C_OK if lt.get("ok") else C_FAIL
        test_result = (
            "<div class='card'><div class='title'>最近一次测试发送</div>"
            "<div class='hint'><b style='color:{0}'>{1}</b> {2}<br>"
            "内容：{3}<br>{4}</div></div>").format(
            color, "成功" if lt.get("ok") else "失败",
            html.escape(str(lt.get("ts", ""))),
            html.escape(str(lt.get("content", ""))),
            html.escape(str(lt.get("detail", ""))[:200]))

    part_toggle = (
        "<label class='menu-item'><span>启用自动水贴</span>"
        "<input type='checkbox' id='wp_enabled'{0}></label>").format(
        " checked" if st["enabled"] else "")

    part_mode = (
        "<div class='form'>"
        "<label>每个主题帖每天发几次（1-{0}）</label>"
        "<input class='input' type='number' id='wp_count' min='1' max='{0}' "
        "value='{1}'>"
        "<label>两条回复之间的固定间隔（秒，{2}-{3}）</label>"
        "<input class='input' type='number' id='wp_interval' min='{2}' "
        "max='{3}' value='{4}'>"
        "<div class='hint'>不随机，就是你填的秒数。默认 5 秒等于连着发。</div>"
        "<label>发送方式</label>"
        "<select class='input' id='wp_mode'>"
        "<option value='thread'{5}>主楼回复（新增楼层）</option>"
        "<option value='floor' disabled>楼中楼（暂不可用）</option>"
        "</select>"
        "<div class='hint'>{6}</div>"
        "<label>文案使用方式</label>"
        "<select class='input' id='wp_template_mode'>"
        "<option value='sequence'{7}>按顺序轮换</option>"
        "<option value='random'{8}>随机挑选</option>"
        "</select>"
        "</div>").format(
        lim["max_count"], st["daily_count"],
        lim["min_interval_sec"], lim["max_interval_sec"], st["interval_sec"],
        " selected" if st["reply_mode"] == "thread" else "",
        html.escape(st["floor_note"]),
        " selected" if st["template_mode"] == "sequence" else "",
        " selected" if st["template_mode"] == "random" else "")

    part_makeup = (
        "<label class='menu-item'><span>补发错过的次数</span>"
        "<input type='checkbox' id='wp_makeup'{0}></label>"
        "<div class='hint'>默认<b>不补发</b>：App 被关掉期间错过的次数直接作废"
        "（重开就集中连发是最明显的机器特征）。打开后，欠下的次数会按你设的间隔"
        "一条条补上，每轮最多补 {1} 条。</div>").format(
        " checked" if st["makeup"] else "", lim["max_tick_sends"])

    total = int(st["expected_daily_total"])
    span = max(0, total - 1) * int(st["interval_sec"])
    if total:
        part_total = (
            "<div class='hint'>按当前设置，预计每天共发出 <b>{0}</b> 条"
            "（{1} 次 × {2} 个已启用的目标帖）；<br>"
            "按 {3} 秒的间隔，这些回复大约会在 <b>{4}</b> 内全部发完。</div>"
            ).format(total, st["daily_count"], st["enabled_target_count"],
                     st["interval_sec"], _duration_text(span))
    else:
        part_total = (
            "<div class='hint'>还没有启用任何目标帖，所以目前不会发帖。"
            "请在下面为账号添加目标帖并勾选「启用」。</div>")

    html_part = (
        "<div class='topbar'>自动水贴"
        "<div class='sub'>定时自动回复主题帖</div></div>"
        "<main>"
        "<div class='card'><div class='title'>它是怎么工作的</div>"
        "<div class='hint'>"
        "① 下面每个账号有<b>自己的一组目标帖</b>：一个账号只发自己这组帖；<br>"
        "② 开启开关后，某个账号<b>当天签到完成</b>（自动或手动都算）之后，"
        "它那组目标帖才开始，约 1 分钟后发出第一条；<br>"
        "③ 之后按你设定的<b>固定间隔</b>继续发，直到每个帖发满当天次数；<br>"
        "④ 文案按顺序轮换（也可改成随机），同一条不会连续重复；<br>"
        "⑤ 签到页的「一键水贴」是手动版：立刻把勾选的账号当天还差的次数发完，"
        "不用等签到；发满后自动计划当天也不会再发。"
        "</div></div>"
        "<div class='card'><div class='title' style='color:#E5480C'>"
        "风控提醒（务必看一眼）</div>"
        "<div class='hint'>aiotieba 对 add_post 的官方说明原文是："
        "「高频率调用会导致&lt;永久封禁屏蔽&gt;！请谨慎使用！」。<br>"
        "默认的 5 秒间隔 + 每天 4 次，意味着一个帖的 4 条回复会在十几秒内发完，"
        "这是最激进的档位，被判定成机器人的概率最高。"
        "长期挂机建议把间隔调到分钟级、每天不超过 4 次，并准备多条不同文案。"
        "</div></div>"
        + "<div class='card'><div class='title'>开关与参数</div>"
        + part_toggle + part_mode + part_makeup + part_total + "</div>"
        + "<div class='card'><div class='title'>账号与目标帖</div>"
          "<div class='hint'>每个账号各有一组目标帖，互不共用。把主题帖链接"
          "粘贴到<b>对应账号</b>的输入框里添加；支持完整链接、App 分享链接、"
          "带 pid 的楼中楼链接，也可以直接填数字 tid。添加时会联网读取吧名与"
          "标题（要求该账号凭据有效）。</div></div>"
        + "".join(cards)
        + "<div class='card'><div class='title'>文案（共 " +
          str(len(st["texts"])) + " 条）</div>"
          "<div class='hint'>每条最多 " + str(lim["max_text_len"]) +
          " 字。所有账号共用这一份文案。建议多准备几条彼此不同的文案，"
          "重复内容本身也是风控特征。</div>"
          "<div id='wp_texts'>" + "".join(text_rows) + "</div>"
          "<div class='btn-row'>"
          "<button class='btn btn-small btn-ghost' type='button' "
          "onclick='wpAddText()'>＋ 加一条</button>"
          "<button class='btn btn-small btn-ghost' type='button' "
          "onclick='wpResetTexts()'>恢复默认 10 条</button>"
          "</div></div>"
        + "<div class='card'><div class='title'>立即测试一次</div>"
          "<div class='hint'>不受当天次数限制，立刻给「第一个账号的第一个"
          "已启用目标帖」发一条，用来验证链接、账号与文案是否都正常。"
          "<b>这一步会真实发帖</b>。</div>"
          "<div class='btn-row'><button class='btn btn-ghost' "
          "id='wp_test_btn' type='button' onclick='wpTest()'>"
          "立即发一条（测试）</button></div>"
          "<div id='wp_test_out'></div></div>"
        + test_result
        + "<div class='btn-row'>"
          "<button class='btn btn-primary' type='button' onclick='wpSave()'>"
          "保存设置</button></div>"
          "<div class='errorbox' id='wp_err'></div>"
          "<div class='okbox' id='wp_ok'></div>"
          "</main>"
          "<div id='toast' style='display:none;position:fixed;left:50%;"
          "bottom:86px;transform:translateX(-50%);background:#141821;color:#fff;"
          "border-radius:999px;padding:12px 22px;font-size:14px;max-width:84%;"
          "z-index:60'></div>"
    )
    return _page("自动水贴设置", html_part + _JS_WATERPOST, "settings")


def _page_weblogin():
    body = """
<div class='topbar'>百度账号网页登录</div>
<main>
<div class='card'>
  <div class='hint'>点击下方按钮后，本页会跳转到<b>百度官方登录页</b>。
  你只需正常输入百度账号和密码（可能还需要短信验证码）完成登录；
  App 全程<b>不接触、不保存你的密码</b>，登录成功后只从浏览器 Cookie
  自动提取 BDUSS，验证后自动保存。</div>
</div>
<div class='card'>
  <div class='title'>注意事项</div>
  <div class='hint'>· 若长时间停在登录页，可手动完成后再等待片刻<br>
  · 验证可能需要 5–20 秒<br>· 超时 5 分钟会自动返回本 App</div>
</div>
<div class='errorbox' id='errbox'></div>
<div class='btn-row' style='margin-top:20px'>
  <button class='btn btn-primary' id='start_btn' type='button'>
  打开百度登录页</button>
</div>
</main>
<div class='overlay' id='overlay'>
  <div class='box'><div class='spinner'></div>
  <div style='font-weight:700'>正在打开百度官方登录页…</div>
  <div style='color:#5B6270;font-size:12px;margin-top:6px'>
  登录成功后 App 会自动提取 BDUSS，无需其他操作</div></div>
</div>
<script>
document.getElementById('start_btn').addEventListener('click',function(){
  var b=document.getElementById('start_btn');
  b.disabled=true;
  document.getElementById('overlay').style.display='flex';
  fetch('/api/weblogin/start',{method:'POST',
    headers:{'Content-Type':'application/json'},body:'{}'})
  .then(function(r){return r.json();})
  .then(function(j){
    if(!j.ok){
      b.disabled=false;
      document.getElementById('overlay').style.display='none';
      var e=document.getElementById('errbox');
      e.style.display='block';e.textContent=j.detail||'启动失败';
    }
  })
  .catch(function(err){
    b.disabled=false;
    document.getElementById('overlay').style.display='none';
    var e=document.getElementById('errbox');
    e.style.display='block';e.textContent='请求失败：'+err;
  });
});
</script>
"""
    return _page("百度登录", body, "accounts")


def _page_weblogin_done(ok, name, detail):
    if ok:
        box = ("<div class='okbox' style='display:block'>"
               "登录成功：账号「{}」已保存。</div>").format(
            html.escape(name or "百度账号"))
        hint = "可以到账号列表切换当前账号，然后去签到。"
    else:
        box = ("<div class='errorbox' style='display:block'>{}</div>").format(
            html.escape(detail or "登录失败"))
        hint = "返回后可以重新尝试网页登录，或改用手动输入 BDUSS。"
    body = (
        "<div class='topbar'>登录结果</div><main>"
        "<div class='card'><div class='title'>{}</div>"
        "<div class='hint'>{}</div></div>{}"
        "<div class='btn-row'>"
        "<a class='btn btn-primary' href='/'>返回账号列表</a>"
        "</div></main>".format(
            html.escape("登录成功" if ok else "登录失败"),
            html.escape(hint), box)
    )
    return _page("登录结果", body, "accounts")


def _send_json(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_html(handler, html_text, status=200):
    body = html_text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class AppHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/":
                _send_html(self, _page_accounts())
            elif path == "/add":
                _send_html(self, _page_add())
            elif path == "/add/manual":
                _send_html(self, _page_manual())
            elif path == "/sign":
                # PRD: red dot is cleared when the sign page is entered.
                try:
                    autosign.clear_dot()
                except Exception:
                    pass
                _send_html(self, _page_sign())
            elif path == "/settings":
                _send_html(self, _page_settings())
            elif path == "/settings/auto":
                _send_html(self, _page_settings_auto())
            elif path == "/settings/waterpost":
                _send_html(self, _page_settings_waterpost())
            elif path == "/settings/update":
                # 进入这一页就算看过了：红点清掉（与签到页同样的约定）。
                # 没查到东西时这里本来也没红点，所以清不清都一样。
                try:
                    updater.clear_dot()
                except Exception:
                    pass
                _send_html(self, _page_settings_update())
            elif path == "/login":
                # Reopening the page = any previous flow was abandoned.
                try:
                    import weblogin
                    weblogin.cancel_native_login()
                    weblogin.cancel_login()
                except Exception:
                    pass
                _send_html(self, _page_login_native())
            elif path == "/weblogin":
                # Legacy full-page flow, kept as an explicit fallback only.
                try:
                    import weblogin
                    weblogin.cancel_login()
                    weblogin.cancel_native_login()
                except Exception:
                    pass
                _send_html(self, _page_weblogin())
            elif path in ("/login/done", "/weblogin/done"):
                query = parse_qs(urlparse(self.path).query)
                ok = query.get("ok", ["0"])[0] == "1"
                name = query.get("name", [""])[0]
                detail = query.get("detail", [""])[0]
                _send_html(self, _page_weblogin_done(ok, name, detail))
            elif path == "/diag":
                _send_html(self, diag.render_page())
            elif path == "/api/accounts":
                _send_json(self, {"ok": True,
                                  "accounts": storage.list_accounts()})
            elif path == "/api/sign/state":
                _send_json(self, {"ok": True, "state": signer.get_state()})
            elif path == "/api/waterpost/manual/state":
                _send_json(self, {"ok": True,
                                  "state": waterpost.get_manual_state()})
            elif path == "/api/update/state":
                _send_json(self, {"ok": True, "state": updater.get_state()})
            elif path == "/api/auto/state":
                _send_json(self, {"ok": True,
                                  "settings": autosign.get_settings(),
                                  "dot": autosign.dot_pending(),
                                  "results": autosign.last_results()})
            else:
                _send_json(self, {"ok": False, "detail": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            print("[app] GET {} error: {}\n{}".format(
                path, exc, traceback.format_exc()))
            _send_json(self, {"ok": False, "detail": "server error"}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0 or length > 1024 * 1024:
                _send_json(self, {"ok": False, "detail": "bad request"}, 400)
                return
            raw = self.rfile.read(length)
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception:
                _send_json(self, {"ok": False, "detail": "invalid json"}, 400)
                return

            if path == "/api/nativelogin/start":
                self._api_native_login_start()
            elif path == "/api/weblogin/start":
                self._api_weblogin_start()
            elif path == "/api/sign/start":
                self._api_sign_start(data)
            elif path == "/api/auto/settings":
                self._api_auto_settings(data)
            elif path == "/api/auto/run-now":
                self._api_auto_run_now()
            elif path == "/api/waterpost/settings":
                self._api_waterpost_settings(data)
            elif path == "/api/waterpost/manual":
                self._api_waterpost_manual(data)
            elif path == "/api/waterpost/targets":
                self._api_waterpost_target_add(data)
            elif path.startswith("/api/waterpost/targets/"):
                self._api_waterpost_target_action(path, data)
            elif path.startswith("/api/waterpost/groups/"):
                self._api_waterpost_group_action(path, data)
            elif path == "/api/waterpost/test":
                self._api_waterpost_test(data)
            elif path == "/api/waterpost/texts/reset":
                self._api_waterpost_texts_reset()
            elif path == "/api/update/settings":
                self._api_update_settings(data)
            elif path == "/api/update/check":
                self._api_update_check()
            elif path == "/api/update/download":
                self._api_update_download()
            elif path == "/api/update/install":
                self._api_update_install()
            elif path == "/api/accounts":
                self._api_add_account(data)
            elif path.startswith("/api/accounts/"):
                self._api_account_action(path, data)
            else:
                _send_json(self, {"ok": False, "detail": "not found"}, 404)
        except Exception as exc:  # noqa: BLE001
            print("[app] POST {} error: {}\n{}".format(
                path, exc, traceback.format_exc()))
            _send_json(self, {"ok": False, "detail": "server error"}, 500)

    def _api_native_login_start(self):
        try:
            import weblogin  # lazy: pyjnius classes, fail -> JSON not crash
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False,
                              "detail": "WebView 组件初始化失败：{}".format(exc)})
            return
        started, err = weblogin.start_native_login()
        if not started:
            _send_json(self, {"ok": False, "detail": err or "启动失败"})
            return
        _send_json(self, {"ok": True})

    def _api_weblogin_start(self):
        try:
            import weblogin  # lazy: pyjnius classes, fail -> JSON not crash
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False,
                              "detail": "WebView 组件初始化失败：{}".format(exc)})
            return
        started, err = weblogin.start_login()
        if not started:
            _send_json(self, {"ok": False, "detail": err or "启动失败"})
            return
        _send_json(self, {"ok": True})


    def _api_sign_start(self, data):
        raw_ids = data.get("account_ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            _send_json(self, {"ok": False, "category": "未选择",
                              "detail": "请至少勾选一个要签到的账号"})
            return
        wanted = []
        for item in raw_ids:
            try:
                wanted.append(int(item))
            except (TypeError, ValueError):
                continue
        if not wanted:
            _send_json(self, {"ok": False, "category": "未选择",
                              "detail": "请至少勾选一个要签到的账号"})
            return
        all_accounts = storage.list_accounts()
        selected = [a for a in all_accounts if a.get("id") in wanted]
        if not selected:
            _send_json(self, {"ok": False, "category": "无账号",
                              "detail": "勾选的账号不存在，请刷新后重试"})
            return
        pairs = []
        for account in selected:
            enc = storage.get_encrypted_bduss(account["id"])
            if not enc:
                _send_json(self, {"ok": False, "category": "存储错误",
                                  "detail": "账号「{}」没有可用的登录凭据".format(
                                      account["name"] or account["mask"])})
                return
            try:
                bduss = keystore_crypto.decrypt_text(enc)
            except Exception as exc:  # noqa: BLE001
                _send_json(self, {"ok": False, "category": "存储错误",
                                  "detail": "账号「{}」BDUSS 解密失败（{}: {}），"
                                           "请删除后重新添加".format(
                                               account["name"] or account["mask"],
                                               type(exc).__name__, exc)})
                return
            fmt_err = tieba_api.format_check(bduss)
            if fmt_err:
                _send_json(self, {"ok": False, "category": "格式错误",
                                  "detail": "账号「{}」：{}".format(
                                      account["name"] or account["mask"],
                                      fmt_err)})
                return
            pairs.append((account, bduss))
        print("[app] sign start multi account_ids={} bduss_lens={}".format(
            [a["id"] for a in selected], [len(b) for _, b in pairs]))
        run_id, err = signer.start_multi(pairs)
        if err:
            _send_json(self, {"ok": False, "category": "任务冲突",
                              "detail": err})
            return
        _send_json(self, {"ok": True, "run_id": run_id,
                          "account_total": len(pairs)})

    def _api_auto_settings(self, data):
        gt = str(data.get("global_time", "")).strip()
        if gt and not (len(gt) == 5 and gt[2] == ":"):
            _send_json(self, {"ok": False, "detail": "时间格式应为 HH:MM"})
            return
        try:
            settings = autosign.save_settings({
                "enabled": data.get("enabled"),
                "global_time": gt or None,
                "notify_system": data.get("notify_system"),
                "notify_dot": data.get("notify_dot"),
                "accounts": data.get("accounts"),
            })
            if "enabled" in data:
                # Immediate: start/stop the foreground service and fire the
                # POST_NOTIFICATIONS permission request (Android 13+).
                autosign.apply_settings_immediately(bool(data["enabled"]))
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False, "detail": "保存失败：{}".format(exc)})
            return
        _send_json(self, {"ok": True, "settings": settings})

    def _api_auto_run_now(self):
        try:
            autosign.request_run_now()
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False, "detail": "触发失败：{}".format(exc)})
            return
        _send_json(self, {"ok": True})

    # ---- 自动水贴 ----------------------------------------------------

    def _api_waterpost_settings(self, data):
        payload = {}
        for key in ("enabled", "makeup", "daily_count", "interval_sec",
                    "reply_mode", "template_mode", "texts"):
            if key in data:
                payload[key] = data[key]
        try:
            settings = waterpost.save_settings(payload)
        except ValueError as exc:
            _send_json(self, {"ok": False, "detail": str(exc)})
            return
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False, "detail": "保存失败：{}".format(exc)})
            return
        _send_json(self, {"ok": True, "settings": settings})

    def _api_waterpost_target_add(self, data):
        """添加目标帖到**指定账号**自己的目标组。"""
        raw_account = data.get("account_id")
        if raw_account in (None, "", "null"):
            _send_json(self, {"ok": False, "detail": "请先选择要添加到的账号"})
            return
        try:
            account_id = int(raw_account)
        except (TypeError, ValueError):
            _send_json(self, {"ok": False, "detail": "账号选择无效"})
            return
        url = str(data.get("url", "")).strip()
        try:
            target, err = waterpost.add_target(url, account_id)
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False,
                              "detail": "解析失败：{}: {}".format(
                                  type(exc).__name__, exc)})
            return
        if err:
            _send_json(self, {"ok": False, "detail": err})
            return
        _send_json(self, {"ok": True, "target": target})

    def _api_waterpost_target_action(self, path, data):
        parts = [p for p in path.split("/") if p]
        # /api/waterpost/targets/<account_id>/<target_id>/<action>
        if len(parts) != 6:
            _send_json(self, {"ok": False, "detail": "bad route"}, 404)
            return
        try:
            account_id = int(parts[3])
            target_id = int(parts[4])
        except ValueError:
            _send_json(self, {"ok": False, "detail": "bad id"}, 400)
            return
        action = parts[5]
        if action == "toggle":
            ok = waterpost.set_target_enabled(account_id, target_id,
                                              bool(data.get("enabled")))
        elif action == "delete":
            ok = waterpost.delete_target(account_id, target_id)
        else:
            _send_json(self, {"ok": False, "detail": "unknown action"}, 404)
            return
        _send_json(self, {"ok": bool(ok),
                          "detail": None if ok else "主题帖不存在"})

    def _api_waterpost_group_action(self, path, data):
        """整组操作：/api/waterpost/groups/<account_id>/<action>"""
        parts = [p for p in path.split("/") if p]
        if len(parts) != 5:
            _send_json(self, {"ok": False, "detail": "bad route"}, 404)
            return
        try:
            account_id = int(parts[3])
        except ValueError:
            _send_json(self, {"ok": False, "detail": "bad id"}, 400)
            return
        action = parts[4]
        if action == "toggle":
            ok = waterpost.set_group_enabled(account_id,
                                             bool(data.get("enabled")))
        elif action == "delete":
            ok = waterpost.delete_group(account_id)
        else:
            _send_json(self, {"ok": False, "detail": "unknown action"}, 404)
            return
        _send_json(self, {"ok": bool(ok),
                          "detail": None if ok else "该账号还没有目标帖"})

    def _api_waterpost_test(self, data):
        target_id = data.get("target_id")
        if target_id in (None, "", "null"):
            target_id = None
        else:
            try:
                target_id = int(target_id)
            except (TypeError, ValueError):
                target_id = None
        account_id = data.get("account_id")
        if account_id in (None, "", "null"):
            account_id = None
        else:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError):
                account_id = None
        try:
            result = waterpost.send_test(account_id=account_id,
                                         target_id=target_id)
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False,
                              "detail": "测试发送异常：{}: {}".format(
                                  type(exc).__name__, exc)})
            return
        _send_json(self, result)

    def _api_waterpost_manual(self, data):
        """签到页的「一键水贴」：把勾选的账号当天还差的次数一次发完。"""
        raw = data.get("account_ids")
        if not isinstance(raw, list) or not raw:
            _send_json(self, {"ok": False, "detail": "请至少勾选一个账号"})
            return
        try:
            ok, err = waterpost.start_manual(raw)
        except Exception as exc:  # noqa: BLE001
            _send_json(self, {"ok": False,
                              "detail": "启动失败：{}: {}".format(
                                  type(exc).__name__, exc)})
            return
        if not ok:
            _send_json(self, {"ok": False, "detail": err})
            return
        _send_json(self, {"ok": True,
                          "state": waterpost.get_manual_state()})

    def _api_waterpost_texts_reset(self):
        _send_json(self, {"ok": True, "texts": waterpost.default_texts()})

    # ---- 检查更新 -----------------------------------------------------
    # 四个接口都是"起一个后台线程就返回"，界面靠 /api/update/state 轮询。
    # 检查/下载动辄几秒到几分钟，同步等会把 WebView 的请求卡死。
    def _api_update_settings(self, data):
        ok, err = updater.set_settings(data or {})
        if not ok:
            _send_json(self, {"ok": False, "detail": err}, 400)
            return
        _send_json(self, {"ok": True, "state": updater.get_state()})

    def _api_update_check(self):
        started, err = updater.check_async(by_user=True)
        _send_json(self, {"ok": bool(started), "detail": err or "",
                          "state": updater.get_state()},
                   200 if started else 409)

    def _api_update_download(self):
        started, err = updater.download_async()
        _send_json(self, {"ok": bool(started), "detail": err or "",
                          "state": updater.get_state()},
                   200 if started else 409)

    def _api_update_install(self):
        ok, err = updater.install()
        _send_json(self, {"ok": bool(ok), "detail": err or "",
                          "state": updater.get_state()})

    def _api_add_account(self, data):
        bduss = str(data.get("bduss", "")).strip()
        name = str(data.get("name", "")).strip()[:60]
        fmt_err = tieba_api.format_check(bduss)
        if fmt_err:
            _send_json(self, {"ok": False, "category": "格式错误",
                              "detail": fmt_err})
            return
        print("[app] validating BDUSS (length {}, no content logged)".format(
            len(bduss)))
        result = tieba_api.validate_bduss(bduss)
        if not result.get("ok"):
            _send_json(self, result)
            return
        if not name:
            name = result.get("display_name") or "贴吧账号"
        bduss_enc = keystore_crypto.encrypt_text(bduss)
        account = storage.add_account(name, bduss, bduss_enc)
        _send_json(self, {"ok": True, "account": account})

    def _api_account_action(self, path, data):
        parts = [p for p in path.split("/") if p]
        # /api/accounts/<id>/<action>
        if len(parts) != 4:
            _send_json(self, {"ok": False, "detail": "bad route"}, 404)
            return
        try:
            account_id = int(parts[2])
        except ValueError:
            _send_json(self, {"ok": False, "detail": "bad id"}, 400)
            return
        action = parts[3]
        if action == "activate":
            _send_json(self, {"ok": True,
                              "account": storage.activate_account(account_id)})
        elif action == "delete":
            _send_json(self, {"ok": True,
                              "accounts": storage.delete_account(account_id)})
        elif action == "rename":
            name = str(data.get("name", "")).strip()[:60]
            _send_json(self, {"ok": True,
                              "account": storage.rename_account(account_id,
                                                                name)})
        else:
            _send_json(self, {"ok": False, "detail": "unknown action"}, 404)

    def log_message(self, fmt, *args):
        print("[app-http] " + fmt % args)




_JS_ACCOUNTS = """
<script>
function post(path, body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})}).then(function(r){return r.json();});
}
var toastTimer=null;
function toast(msg){
  var t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  if(toastTimer){clearTimeout(toastTimer);}
  toastTimer=setTimeout(function(){t.style.display='none';},3500);
}
function dlgOpen(){document.getElementById('dlg').style.display='flex';}
function dlgClose(){document.getElementById('dlg').style.display='none';}
function dlgContent(el){
  var body=document.getElementById('dlgBody');
  body.innerHTML=''; body.appendChild(el); dlgOpen();
}
function confirmDlg(msg, okLabel, okFn, danger){
  var wrap=document.createElement('div');
  var t=document.createElement('div');
  t.textContent=msg; t.style.marginBottom='14px'; t.style.lineHeight='1.5';
  var row=document.createElement('div'); row.className='btn-row';
  var c=document.createElement('button');
  c.className='btn btn-small btn-ghost'; c.textContent='取消';
  c.onclick=dlgClose;
  var o=document.createElement('button');
  o.className='btn btn-small '+(danger?'btn-danger':'btn-primary');
  o.textContent=okLabel;
  o.onclick=function(){dlgClose(); okFn();};
  row.appendChild(c); row.appendChild(o);
  wrap.appendChild(t); wrap.appendChild(row);
  dlgContent(wrap);
}
function inputDlg(title, value, okLabel, okFn){
  var wrap=document.createElement('div');
  var t=document.createElement('div');
  t.textContent=title; t.style.fontWeight='700'; t.style.marginBottom='12px';
  var inp=document.createElement('input');
  inp.className='input'; inp.id='dlgInput'; inp.maxLength=60; inp.value=value||'';
  var row=document.createElement('div'); row.className='btn-row';
  var c=document.createElement('button');
  c.className='btn btn-small btn-ghost'; c.textContent='取消';
  c.onclick=dlgClose;
  var o=document.createElement('button');
  o.className='btn btn-small btn-primary'; o.textContent=okLabel;
  o.onclick=function(){dlgClose(); okFn(inp.value.trim());};
  row.appendChild(c); row.appendChild(o);
  wrap.appendChild(t); wrap.appendChild(inp); wrap.appendChild(row);
  dlgContent(wrap);
}
function activateAcct(id){
  post('/api/accounts/'+id+'/activate').then(function(j){
    if(j.ok){location.reload();} else {toast('操作失败：'+(j.detail||''));}
  });
}
function deleteAcct(id, label){
  confirmDlg('确定删除账号「'+label+'」吗？删除后本机保存的登录凭据会被清除。',
    '删除', function(){
      post('/api/accounts/'+id+'/delete').then(function(j){
        if(j.ok){location.reload();} else {toast('删除失败：'+(j.detail||''));}
      });
    }, true);
}
function renameAcct(id, oldName){
  inputDlg('修改账号备注', oldName||'', '保存', function(name){
    post('/api/accounts/'+id+'/rename',{name:name}).then(function(j){
      if(j.ok){location.reload();} else {toast('改名失败：'+(j.detail||''));}
    });
  });
}
</script>
"""

_JS_SIGN = """
<script>
var CAT_COLOR = {"已签到":"#E8930C", "未关注":"#E8930C",
  "账号失效":"#E5484D", "网络异常":"#E5484D", "风控其他":"#E5484D"};
function escTxt(s){
  var d=document.createElement('div');
  d.textContent=(s===null||s===undefined)?'':String(s);
  return d.innerHTML;
}
function renderSingle(s){
  var box=document.getElementById('sign_state');
  var html='<div class="card"><div class="title">'+escTxt(s.step||'')+'</div>';
  if(s.status==='starting'||s.status==='running'){
    html+='<div class="spinner" style="margin:14px 0 0"></div>';
  }
  if(s.forum_total>0){
    var pct=Math.min(100,Math.round(s.forum_checked*100/s.forum_total));
    html+='<div class="hint">补签进度 '+s.forum_checked+' / '+s.forum_total+'</div>';
    html+='<div style="height:8px;border-radius:999px;'+
      'background:rgba(127,127,127,.18);margin-top:8px">'+
      '<div style="height:8px;border-radius:999px;background:#2B5CE6;width:'+
      pct+'%"></div></div>';
  }
  if(s.msign){
    var m=s.msign;
    var color=(m.status==='success')?'#0BA36B':(CAT_COLOR[m.category]||'#E5484D');
    html+='<div class="hint" style="color:'+color+';font-weight:700;margin-top:8px">'+
      '网页一键签到：'+(m.status==='success'?'成功':
      escTxt((m.category||'')+' - '+(m.detail||'')))+'</div>';
  }
  if(s.status==='done'){
    html+='<div style="display:flex;gap:10px;margin-top:14px">';
    html+='<div style="flex:1;text-align:center;background:rgba(11,163,107,.12);'+
      'border-radius:12px;padding:12px">'+
      '<div style="font-size:22px;font-weight:700;color:#0BA36B">'+
      s.forum_success+'</div><div style="font-size:12px;color:#5B6270">补签成功</div></div>';
    html+='<div style="flex:1;text-align:center;background:rgba(229,72,77,.12);'+
      'border-radius:12px;padding:12px">'+
      '<div style="font-size:22px;font-weight:700;color:#E5484D">'+
      s.forum_failed+'</div><div style="font-size:12px;color:#5B6270">补签失败</div></div>';
    html+='</div>';
    html+='<div class="hint" style="margin-top:10px">签到完成，共检查 '+
      s.forum_total+' 个待签吧。</div>';
  }
  if(s.status==='error'){
    var e=s.error||{};
    html+='<div class="errorbox" style="display:block;margin-top:12px">'+
      '签到未完成：'+escTxt((e.category?(e.category+'：'):'')+(e.detail||''))+'</div>';
  }
  html+='</div>';
  if(s.failures && s.failures.length>0){
    html+='<div class="card"><div class="title">失败详情（'+s.failures.length+'）</div>';
    var max=Math.min(s.failures.length,100);
    for(var i=0;i<max;i++){
      var f=s.failures[i];
      var c=CAT_COLOR[f.category]||'#E5484D';
      html+='<div style="border-bottom:1px solid rgba(0,0,0,.06);padding:10px 0">'+
        '<div style="font-weight:700">'+escTxt(f.forum)+
        ' <span style="color:'+c+';font-size:12px">'+escTxt(f.category)+'</span></div>'+
        '<div class="hint">'+escTxt(f.detail)+'</div></div>';
    }
    if(s.failures.length>max){
      html+='<div class="hint">还有 '+(s.failures.length-max)+' 条未显示</div>';
    }
    html+='</div>';
  }
  return html;
}
function acctStatus(a){
  if(a.status==='done') return ['已完成','#0BA36B'];
  if(a.status==='error') return ['失败/异常','#E5484D'];
  if(a.status==='running') return ['签到中…','#2B5CE6'];
  return ['等待中','#8A90A0'];
}
function renderMulti(s){
  var html='<div class="card"><div class="title">'+escTxt(s.step||'')+'</div>';
  if(s.status==='starting'||s.status==='running'){
    html+='<div class="spinner" style="margin:14px 0 0"></div>';
  }
  var done=s.account_done||0;
  var total=s.account_total||(s.accounts?s.accounts.length:0);
  html+='<div class="hint">账号进度 '+done+' / '+total+'</div>';
  var aggS=0,aggF=0,allFails=[];
  if(s.accounts){
    for(var i=0;i<s.accounts.length;i++){
      var a=s.accounts[i];
      aggS+=(a.forum_success||0); aggF+=(a.forum_failed||0);
      var st=acctStatus(a);
      html+='<div style="border-bottom:1px solid rgba(127,127,127,.18);padding:12px 0">';
      html+='<div style="display:flex;justify-content:space-between;align-items:center">'+
        '<b style="font-size:15px">'+escTxt(a.account_name)+
        '<span style="color:#8A90A0;font-size:12px;font-weight:400"> '+
        escTxt('BDUSS '+a.account_mask)+'</span></b>'+
        '<span style="color:'+st[1]+';font-size:12px;font-weight:700">'+st[0]+'</span></div>';
      if(a.forum_total>0){
        var pct=Math.min(100,Math.round((a.forum_checked||0)*100/a.forum_total));
        html+='<div class="hint">补签进度 '+a.forum_checked+' / '+a.forum_total+'</div>';
        html+='<div style="height:6px;border-radius:999px;'+
          'background:rgba(127,127,127,.18);margin-top:6px">'+
          '<div style="height:6px;border-radius:999px;background:#2B5CE6;width:'+
          pct+'%"></div></div>';
      }
      if(a.msign){
        var m=a.msign;
        var mc=(m.status==='success')?'#0BA36B':(CAT_COLOR[m.category]||'#E5484D');
        html+='<div class="hint" style="color:'+mc+';font-weight:700;margin-top:6px">'+
          '网页一键签到：'+(m.status==='success'?'成功':
          escTxt((m.category||'')+' - '+(m.detail||'')))+'</div>';
      }
      if(a.status==='done'){
        html+='<div class="hint" style="margin-top:6px">补签成功 '+
          (a.forum_success||0)+' · 补签失败 '+(a.forum_failed||0)+'</div>';
      }
      if(a.status==='error'){
        var e=a.error||{};
        html+='<div class="errorbox" style="display:block;margin-top:8px">'+
          '签到未完成：'+escTxt((e.category?(e.category+'：'):'')+(e.detail||''))+'</div>';
      }
      html+='</div>';
      if(a.failures && a.failures.length>0){
        for(var k=0;k<a.failures.length;k++){
          var f=a.failures[k];
          allFails.push({account:a.account_name,forum:f.forum,
            category:f.category,detail:f.detail});
        }
      }
    }
  }
  if(s.status==='done'){
    html+='<div style="display:flex;gap:10px;margin-top:14px">';
    html+='<div style="flex:1;text-align:center;background:rgba(11,163,107,.12);'+
      'border-radius:12px;padding:12px">'+
      '<div style="font-size:22px;font-weight:700;color:#0BA36B">'+aggS+
      '</div><div style="font-size:12px;color:#5B6270">补签成功</div></div>';
    html+='<div style="flex:1;text-align:center;background:rgba(229,72,77,.12);'+
      'border-radius:12px;padding:12px">'+
      '<div style="font-size:22px;font-weight:700;color:#E5484D">'+aggF+
      '</div><div style="font-size:12px;color:#5B6270">补签失败</div></div>';
    html+='</div>';
  }
  html+='</div>';
  if(allFails.length>0){
    html+='<div class="card"><div class="title">失败详情（'+allFails.length+'）</div>';
    var max=Math.min(allFails.length,100);
    for(var j=0;j<max;j++){
      var f=allFails[j];
      var c=CAT_COLOR[f.category]||'#E5484D';
      html+='<div style="border-bottom:1px solid rgba(0,0,0,.06);padding:10px 0">'+
        '<div style="font-weight:700">'+escTxt(f.account+' → '+f.forum)+
        ' <span style="color:'+c+';font-size:12px">'+escTxt(f.category)+'</span></div>'+
        '<div class="hint">'+escTxt(f.detail)+'</div></div>';
    }
    if(allFails.length>max){
      html+='<div class="hint">还有 '+(allFails.length-max)+' 条未显示</div>';
    }
    html+='</div>';
  }
  return html;
}
function renderSignState(s){
  var btn=document.getElementById('sign_btn');
  var box=document.getElementById('sign_state');
  if(!s || s.status==='idle'){
    box.innerHTML='<div class="card"><div class="hint">点击「开始一键签到」，'+
      '结果会实时显示在这里。手动签到失败不会发通知、不显示红点。</div></div>';
    btn.disabled=false; btn.textContent='开始一键签到';
    return;
  }
  var busy=(s.status==='starting'||s.status==='running');
  btn.disabled=busy;
  btn.textContent=busy?'签到进行中…':'开始一键签到';
  box.innerHTML=(s.mode==='multi')?renderMulti(s):renderSingle(s);
}
function pollSign(){
  fetch('/api/sign/state').then(function(r){return r.json();}).then(function(j){
    if(j.ok){renderSignState(j.state);}
  }).catch(function(){});
}
document.getElementById('sign_btn').addEventListener('click',function(){
  var ids=[];
  document.querySelectorAll('.acct_cb:checked').forEach(function(c){
    var v=parseInt(c.value,10);
    if(!isNaN(v)){ids.push(v);}
  });
  var btn=document.getElementById('sign_btn');
  var box=document.getElementById('sign_state');
  if(ids.length===0){
    box.innerHTML='<div class="errorbox" style="display:block">请至少勾选一个账号</div>';
    return;
  }
  btn.disabled=true; btn.textContent='正在启动…';
  fetch('/api/sign/start',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account_ids:ids})})
    .then(function(r){return r.json();}).then(function(j){
    if(!j.ok){
      box.innerHTML='<div class="errorbox" style="display:block">'+
        escTxt((j.category?(j.category+'：'):'')+(j.detail||''))+'</div>';
      btn.disabled=false; btn.textContent='开始一键签到';
    }
  }).catch(function(){
    btn.disabled=false; btn.textContent='开始一键签到';
  });
  setTimeout(pollSign,400);
});
pollSign();
setInterval(pollSign,1000);

/* ---- 一键水贴：手动立即发一轮，进度轮询显示 ---- */
function renderWpState(s){
  var btn=document.getElementById('wp_btn');
  var box=document.getElementById('wp_state');
  if(!btn||!box){return;}
  if(!s||s.status==='idle'){
    box.innerHTML='<div class="card"><div class="hint">点击「开始一键水贴」，'+
      '它会把每个账号今天还差的次数一次发完，进度显示在这里。</div></div>';
    btn.disabled=false; btn.textContent='开始一键水贴';
    return;
  }
  var busy=(s.status==='running');
  btn.disabled=busy;
  btn.textContent=busy?'水贴进行中…':'开始一键水贴';
  var html='<div class="card"><div class="title">一键水贴'+
    (busy?'（进行中）':'（已完成）')+'</div>';
  html+='<div class="hint">进度 '+s.finished+' / '+s.total+
    ' · 成功 '+s.success+' · 失败 '+s.failed+'</div>';
  if(s.note){html+='<div class="hint">'+escTxt(s.note)+'</div>';}
  if(busy){
    html+='<div class="spinner" style="margin:12px 0 0"></div>';
    if(s.current){html+='<div class="hint">正在发：'+escTxt(s.current)+'</div>';}
  }
  if(s.items&&s.items.length){
    html+='<div style="margin-top:10px">';
    var max=Math.min(s.items.length,60);
    for(var i=max-1;i>=0;i--){          /* 最新的排在最上面 */
      var it=s.items[i];
      var c=it.ok?'#0BA36B':'#E5484D';
      html+='<div style="border-bottom:1px solid rgba(127,127,127,.16);'+
        'padding:8px 0">'+
        '<div style="font-weight:700;font-size:14px">'+escTxt(it.account_name)+
        ' → '+escTxt(it.title||it.fname||'')+
        (it.of?' <span style="color:#8A90A0;font-size:12px;font-weight:400">'+
          '第'+it.seq+'/'+it.of+'条</span>':'')+
        ' <span style="color:'+c+';font-size:12px">'+
        (it.ok?'成功':escTxt(it.category||'失败'))+'</span></div>';
      if(it.content){html+='<div class="hint">文案：'+escTxt(it.content)+'</div>';}
      if(!it.ok&&it.detail){html+='<div class="hint" style="color:#E5484D">'+
        escTxt(it.detail)+'</div>';}
      html+='</div>';
    }
    if(s.items.length>max){
      html+='<div class="hint">还有 '+(s.items.length-max)+' 条未显示</div>';
    }
    html+='</div>';
  }
  html+='</div>';
  box.innerHTML=html;
}
function pollWp(){
  fetch('/api/waterpost/manual/state').then(function(r){return r.json();})
  .then(function(j){if(j.ok){renderWpState(j.state);}}).catch(function(){});
}
(function(){
  var btn=document.getElementById('wp_btn');
  if(!btn){return;}
  btn.addEventListener('click',function(){
    var ids=[];
    document.querySelectorAll('.acct_cb:checked').forEach(function(c){
      var v=parseInt(c.value,10);
      if(!isNaN(v)){ids.push(v);}
    });
    var box=document.getElementById('wp_state');
    if(ids.length===0){
      box.innerHTML='<div class="errorbox" style="display:block">'+
        '请至少勾选一个账号</div>';
      return;
    }
    btn.disabled=true; btn.textContent='正在启动…';
    fetch('/api/waterpost/manual',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({account_ids:ids})})
      .then(function(r){return r.json();}).then(function(j){
      if(!j.ok){
        box.innerHTML='<div class="errorbox" style="display:block">'+
          escTxt(j.detail||'启动失败')+'</div>';
        btn.disabled=false; btn.textContent='开始一键水贴';
      }else{setTimeout(pollWp,300);}
    }).catch(function(e){
      box.innerHTML='<div class="errorbox" style="display:block">请求失败：'+
        escTxt(e)+'</div>';
      btn.disabled=false; btn.textContent='开始一键水贴';
    });
  });
})();
pollWp();
setInterval(pollWp,1000);
</script>
"""


_JS_WATERPOST = """
<script>
function wpPost(path, body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})}).then(function(r){return r.json();});
}
function escW(s){
  var d=document.createElement('div');
  d.textContent=(s===null||s===undefined)?'':String(s);
  return d.innerHTML;
}
function toast(msg){
  var t=document.getElementById('toast');
  t.textContent=msg; t.style.display='block';
  setTimeout(function(){t.style.display='none';},3500);
}
function showErr(s){
  var e=document.getElementById('wp_err');
  e.style.display='block'; e.textContent=s;
  document.getElementById('wp_ok').style.display='none';
}
function collectTexts(){
  var out=[];
  document.querySelectorAll('.wp_text').forEach(function(i){out.push(i.value);});
  return out;
}
function wpSave(){
  var texts=collectTexts();
  if(texts.length===0){showErr('至少要保留一条文案');return;}
  var payload={enabled:document.getElementById('wp_enabled').checked,
    makeup:document.getElementById('wp_makeup').checked,
    daily_count:document.getElementById('wp_count').value,
    interval_sec:document.getElementById('wp_interval').value,
    reply_mode:document.getElementById('wp_mode').value,
    template_mode:document.getElementById('wp_template_mode').value,
    texts:texts};
  wpPost('/api/waterpost/settings',payload).then(function(j){
    if(j.ok){toast('设置已保存');setTimeout(function(){location.reload();},500);}
    else{showErr(j.detail||'保存失败');}
  }).catch(function(e){showErr('请求失败：'+e);});
}
/* 添加目标帖：必须指定账号（每个账号只管自己那组），所以按账号分开调用 */
function wpAddTo(accountId){
  var inp=document.getElementById('wp_url_'+accountId);
  var url=(inp&&inp.value||'').trim();
  if(!url){showErr('请先粘贴主题帖链接');return;}
  var btn=document.getElementById('wp_add_btn_'+accountId);
  if(btn){btn.disabled=true;btn.textContent='解析中…（最长 90 秒）';}
  wpPost('/api/waterpost/targets',{url:url,account_id:accountId})
  .then(function(j){
    if(j.ok){toast('已添加');setTimeout(function(){location.reload();},500);}
    else{
      if(btn){btn.disabled=false;btn.textContent='解析并添加';}
      showErr(j.detail||'添加失败');
    }
  }).catch(function(e){
    if(btn){btn.disabled=false;btn.textContent='解析并添加';}
    showErr('请求失败：'+e);
  });
}
function wpToggle(accountId,targetId,on){
  wpPost('/api/waterpost/targets/'+accountId+'/'+targetId+'/toggle',
    {enabled:on})
  .then(function(j){
    if(!j.ok){toast('操作失败：'+(j.detail||''));}
    else{toast(on?'已启用':'已停用');}
  }).catch(function(){toast('操作失败');});
}
function wpSetGroup(accountId,on){
  wpPost('/api/waterpost/groups/'+accountId+'/toggle',{enabled:on})
  .then(function(j){
    if(!j.ok){toast('操作失败：'+(j.detail||''));}
    else{toast(on?'整组已启用':'整组已停用');setTimeout(function(){
      location.reload();},600);}
  }).catch(function(){toast('操作失败');});
}
/* 不用原生弹窗(confirm)：这个 WebView 里它不可靠，改成点两次确认。 */
var wpArmed=null;
function wpDelete(accountId,targetId,btn){
  var key=accountId+':'+targetId;
  if(wpArmed===key){
    wpArmed=null;
    wpPost('/api/waterpost/targets/'+accountId+'/'+targetId+'/delete',{})
    .then(function(j){
      if(j.ok){location.reload();}else{toast('删除失败：'+(j.detail||''));}
    }).catch(function(){toast('删除失败');});
    return;
  }
  wpArmed=key;
  btn.textContent='再点一次确认删除';
  btn.className='btn btn-small btn-primary';
  setTimeout(function(){
    if(wpArmed===key){
      wpArmed=null;
      btn.textContent='删除';
      btn.className='btn btn-small btn-danger';
    }
  },4000);
}
function wpDeleteGroup(accountId,btn){
  var key='group:'+accountId;
  if(wpArmed===key){
    wpArmed=null;
    wpPost('/api/waterpost/groups/'+accountId+'/delete',{})
    .then(function(j){
      if(j.ok){location.reload();}else{toast('删除失败：'+(j.detail||''));}
    }).catch(function(){toast('删除失败');});
    return;
  }
  wpArmed=key;
  btn.textContent='再点一次确认删除整组';
  btn.className='btn btn-small btn-primary';
  setTimeout(function(){
    if(wpArmed===key){
      wpArmed=null;
      btn.textContent='删除这组目标帖';
      btn.className='btn btn-small btn-danger';
    }
  },4000);
}
function wpTextRow(value){
  var row=document.createElement('div');
  row.style.cssText='display:flex;gap:8px;align-items:center;margin-bottom:8px';
  var inp=document.createElement('input');
  inp.className='input wp_text'; inp.maxLength=100; inp.value=value||'';
  inp.style.cssText='flex:1;min-height:46px';
  var btn=document.createElement('button');
  btn.className='btn btn-small btn-ghost'; btn.type='button';
  btn.textContent='删除'; btn.style.flex='none';
  btn.onclick=function(){wpRemoveText(this);};
  row.appendChild(inp); row.appendChild(btn);
  return row;
}
function wpAddText(){
  document.getElementById('wp_texts').appendChild(wpTextRow(''));
}
function wpRemoveText(btn){
  var box=document.getElementById('wp_texts');
  box.removeChild(btn.parentNode);
}
function wpResetTexts(){
  wpPost('/api/waterpost/texts/reset',{}).then(function(j){
    if(!j.ok){showErr(j.detail||'操作失败');return;}
    var box=document.getElementById('wp_texts');
    box.innerHTML='';
    for(var i=0;i<j.texts.length;i++){box.appendChild(wpTextRow(j.texts[i]));}
    toast('已恢复默认文案，记得点「保存设置」');
  }).catch(function(e){showErr('请求失败：'+e);});
}
function wpTest(){
  var btn=document.getElementById('wp_test_btn');
  var out=document.getElementById('wp_test_out');
  btn.disabled=true; btn.textContent='发送中…（最长 90 秒）';
  out.innerHTML='';
  wpPost('/api/waterpost/test',{}).then(function(j){
    btn.disabled=false; btn.textContent='立即发一条（测试）';
    if(j.ok){
      out.innerHTML="<div class='okbox' style='display:block'>发送成功："+
        escW(j.content)+"</div>";
    }else{
      var r=j.result||{};
      out.innerHTML="<div class='errorbox' style='display:block'>发送失败："+
        escW((r.category?r.category+'：':'')+(r.detail||j.detail||''))+"</div>";
    }
  }).catch(function(e){
    btn.disabled=false; btn.textContent='立即发一条（测试）';
    showErr('请求失败：'+e);
  });
}
</script>
"""


_JS_UPDATE = """
<script>
function upPost(path, body){
  return fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body||{})}).then(function(r){return r.json();});
}
function upEsc(s){
  var d=document.createElement('div');
  d.textContent=(s===null||s===undefined)?'':String(s);
  return d.innerHTML;
}
function upErr(s){
  var e=document.getElementById('up_err');
  e.style.display='block'; e.textContent=s;
  var o=document.getElementById('up_ok');
  if(o){o.style.display='none';}
}
/* 检查/下载都跑在后台线程里，所以这里轮询状态而不是等一个长请求：
   请求超时会把浏览器/WebView 卡住，而下载 20MB 可能要好几分钟。
   阶段离开 checking/downloading 就整页刷新，让按钮和文字跟状态一致。 */
function upPoll(){
  var out=document.getElementById('up_out');
  var last='';
  var ticks=0;
  var timer=setInterval(function(){
    ticks++;
    fetch('/api/update/state').then(function(r){return r.json();})
      .then(function(j){
        var s=(j&&j.state)||{};
        var text=s.detail||s.phase||'';
        if(text!==last){
          last=text;
          out.innerHTML="<div class='hint'>"+upEsc(text)+"</div>";
        }
        if(s.phase!=='checking'&&s.phase!=='downloading'){
          clearInterval(timer);
          setTimeout(function(){location.reload();},700);
        }
      }).catch(function(){});
    if(ticks>400){clearInterval(timer);upErr('等太久了，请刷新页面看结果');}
  },1500);
}
function upBusy(id, text){
  var b=document.getElementById(id);
  if(b){b.disabled=true; b.textContent=text;}
}
function upCheck(){
  upBusy('up_check','检查中…');
  upPost('/api/update/check',{}).then(function(j){
    if(!j.ok){upBusy('up_check','立即检查'); upErr(j.detail||'无法开始检查'); return;}
    upPoll();
  }).catch(function(e){upBusy('up_check','立即检查'); upErr('请求失败：'+e);});
}
function upDownload(){
  upBusy('up_download','下载中…');
  upPost('/api/update/download',{}).then(function(j){
    if(!j.ok){upBusy('up_download','下载更新'); upErr(j.detail||'无法开始下载'); return;}
    upPoll();
  }).catch(function(e){upBusy('up_download','下载更新'); upErr('请求失败：'+e);});
}
function upInstall(){
  upBusy('up_install','正在打开安装界面…');
  upPost('/api/update/install',{}).then(function(j){
    var o=document.getElementById('up_ok');
    if(j.ok){o.style.display='block'; o.textContent='已交给系统安装界面';}
    else{upBusy('up_install','立即安装'); upErr(j.detail||'无法安装');}
  }).catch(function(e){upBusy('up_install','立即安装'); upErr('请求失败：'+e);});
}
/* 开关和下拉都是"改了就存"：这一页没有保存按钮，忘了点保存比误点一下更烦人 */
function upSave(){
  var payload={
    auto_check:document.getElementById('up_auto_check').checked,
    auto_download:document.getElementById('up_auto_download').checked,
    wifi_only:document.getElementById('up_wifi_only').checked,
    download_mode:document.getElementById('up_mode').value};
  upPost('/api/update/settings',payload).then(function(j){
    var o=document.getElementById('up_ok');
    if(j.ok){o.style.display='block'; o.textContent='设置已保存';
      document.getElementById('up_err').style.display='none';}
    else{upErr(j.detail||'保存失败');}
  }).catch(function(e){upErr('请求失败：'+e);});
}
['up_auto_check','up_auto_download','up_wifi_only','up_mode'].forEach(
  function(id){
    var el=document.getElementById(id);
    if(el){el.addEventListener('change',upSave);}
  });
</script>
"""


def _dump_python_error():
    """Write the pending exception traceback to files/app/python_error.log."""
    try:
        import traceback as _tb

        path = os.path.join(os.getcwd(), "python_error.log")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(_tb.format_exc())
    except Exception:
        pass


def main():
    try:
        with open(os.path.join(os.getcwd(), "python_started.marker"), "w",
                  encoding="utf-8") as fh:
            fh.write("m222 python {}.{}.{} cwd={}\n".format(
                *sys.version_info[:3], os.getcwd()))
    except Exception:
        pass
    print("tiebasign-m222 serving http://{}:{}".format(HOST, PORT))
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    try:
        with open(os.path.join(os.getcwd(), "webview_server.marker"), "w",
                  encoding="utf-8") as fh:
            fh.write("bound to {}:{}\n".format(HOST, PORT))
    except Exception:
        pass
    # Storage self-test AFTER the server is bound: JSON store, no native
    # modules; the next launch auto-log would show exactly how far we got
    # (webview_server.marker exists but app_ok.marker is missing).
    _import_marker("py:server_bound")
    storage.selftest()
    _import_marker("py:storage_selftest_ok")
    try:
        waterpost.selftest()
        _import_marker("py:waterpost_selftest_ok")
    except Exception as exc:  # noqa: BLE001
        _import_marker("py:waterpost_selftest_failed:{}".format(
            type(exc).__name__))
    try:
        updater.selftest()
        _import_marker("py:updater_selftest_ok")
    except Exception as exc:  # noqa: BLE001
        _import_marker("py:updater_selftest_failed:{}".format(
            type(exc).__name__))
    try:
        with open(os.path.join(os.getcwd(), "app_ok.marker"), "w",
                  encoding="utf-8") as fh:
            fh.write("healthy\n")
    except Exception:
        pass
    _import_marker("py:app_ok")
    try:
        autosign.start_background()
        _import_marker("py:autosign_scheduler_started")
    except Exception as exc:  # noqa: BLE001
        _import_marker("py:autosign_start_failed:{}".format(type(exc).__name__))
    try:
        waterpost.start_background()
        _import_marker("py:waterpost_scheduler_started")
    except Exception as exc:  # noqa: BLE001
        _import_marker("py:waterpost_start_failed:{}".format(
            type(exc).__name__))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:  # noqa: BLE001 - visible on next launch via Java auto-log
        _dump_python_error()
        raise
