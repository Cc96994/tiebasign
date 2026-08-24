# -*- coding: utf-8 -*-
"""TiebaSign tech-check page (/diag).

Kept from M2.1/M2.2.1 so we can re-verify the whole runtime chain on any
device without reinstalling a special APK. No BDUSS, no network calls.
"""

import gzip
import hashlib
import html
import platform
import ssl
import zlib

PASS_COLOR = "#0BA36B"
FAIL_COLOR = "#E5484D"


def check(name, fn, results):
    try:
        detail = fn()
        results.append((name, True, str(detail)))
    except Exception as exc:  # noqa: BLE001 - report everything on screen
        results.append((name, False, "{}: {}".format(type(exc).__name__, exc)))


def check_keystore_aesgcm():
    from keystore_crypto import self_test as keystore_self_test

    return keystore_self_test()


def check_master_encrypt_roundtrip():
    """Production path: master key encrypt/decrypt with the real API."""
    from keystore_crypto import decrypt_text, encrypt_text

    pt = "tieba-master-key-roundtrip"
    blob = encrypt_text(pt)
    back = decrypt_text(blob)
    if back != pt:
        raise AssertionError("master key round-trip mismatch")
    return "master key encrypt/decrypt ok ({} chars blob)".format(len(blob))


def check_cookie_manager():
    from jnius import autoclass

    CookieManager = autoclass("android.webkit.CookieManager")
    cm = CookieManager.getInstance()
    test_cookie = "TIEBA_TEST_COOKIE=tieba_sign_m22"
    cm.setCookie("https://passport.baidu.com", test_cookie)
    cm.flush()
    got = cm.getCookie("https://passport.baidu.com")
    if not got or test_cookie not in str(got):
        raise AssertionError("set/get mismatch, got {!r}".format(str(got)[:120]))
    return "CookieManager set/get/flush ok (test cookie only)"


def check_import():
    import aiotieba
    from aiotieba import __version__ as v

    if v != "4.7.1":
        raise AssertionError("aiotieba version is {!r}, want '4.7.1'".format(v))
    return "aiotieba {} @ {}".format(v, aiotieba.__file__)


def check_crypto_extension():
    from aiotieba.helper import crypto

    exports = {"cuid_galaxy2", "c3_aid", "rc4_42", "enuid"}
    missing = exports - set(dir(crypto))
    if missing:
        raise AssertionError("missing exports: {}".format(sorted(missing)))
    return crypto.__file__


def check_compute_sign():
    from aiotieba.helper.crypto.sign import compute_sign

    sig = compute_sign([("kw", "smoke")], salt=b"m1")
    if len(sig) != 32:
        raise AssertionError("bad sign length: {}".format(len(sig)))
    return sig


def check_protobuf_impl():
    from google.protobuf import __version__ as pb_version
    from google.protobuf.internal import api_implementation

    impl = api_implementation.Type()
    if impl != "python":
        raise AssertionError("implementation is {!r}, want 'python'".format(impl))
    return "protobuf {} impl={}".format(pb_version, impl)


def check_pb2_serialize():
    from google.protobuf import descriptor_pb2

    fd = descriptor_pb2.FileDescriptorProto()
    fd.name = "smoke.proto"
    fd.package = "m22"
    data = fd.SerializeToString()
    if b"smoke.proto" not in data:
        raise AssertionError("serialized bytes lack field data")
    return "{} bytes round-trip".format(len(data))


def check_ssl_context_patch():
    import aiotieba.core.http as http

    ctx = getattr(http, "_SSL_CONTEXT", None)
    if not isinstance(ctx, ssl.SSLContext):
        raise AssertionError("_SSL_CONTEXT missing or not ssl.SSLContext")

    funcs = (
        "pack_form_request",
        "pack_proto_request",
        "pack_web_get_request",
        "pack_web_form_request",
    )
    patched = 0
    for name in funcs:
        fn = getattr(http.HttpCore, name, None)
        if fn is None:
            raise AssertionError("http.HttpCore.{} missing".format(name))
        code = getattr(fn, "__code__", None)
        if code is None or "_SSL_CONTEXT" not in code.co_names:
            raise AssertionError(
                "http.HttpCore.{} does not reference _SSL_CONTEXT "
                "(HTTPS patch missing?)".format(name)
            )
        patched += 1
    return "{} http request helpers use SSLContext".format(patched)


def check_zlib_runtime():
    raw = b"tieba-sign-m22" * 100
    comp = zlib.compress(raw)
    if zlib.decompress(comp) != raw:
        raise AssertionError("zlib round-trip mismatch")
    return "{}B -> {}B -> ok".format(len(raw), len(comp))


def check_hashlib_runtime():
    d = hashlib.sha256(b"tieba-sign-m22").hexdigest()
    if len(d) != 64:
        raise AssertionError("bad digest length")
    return "sha256 " + d[:16] + "..."


def check_ssl_module():
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    paths = ssl.get_default_verify_paths()
    return "SSLContext ok; cafile={!r}".format(paths.cafile)


def check_gzip_runtime():
    raw = b"gzip" * 1000
    comp = gzip.compress(raw)
    if gzip.decompress(comp) != raw:
        raise AssertionError("gzip round-trip mismatch")
    return "{}B -> {}B -> ok".format(len(raw), len(comp))


def check_lxml_runtime():
    from lxml import etree

    root = etree.fromstring(b"<a><b>x</b></a>")
    if root.findtext("b") != "x":
        raise AssertionError("etree parse mismatch")
    return "lxml.etree " + ".".join(str(v) for v in etree.LXML_VERSION)


def check_aiohttp_runtime():
    import aiohttp

    if not aiohttp.__version__:
        raise AssertionError("aiohttp version empty")
    return "aiohttp " + aiohttp.__version__


def run_checks():
    results = []
    check("Android Keystore AES-GCM", check_keystore_aesgcm, results)
    check("Keystore master key encrypt/decrypt", check_master_encrypt_roundtrip,
          results)
    check("WebView CookieManager", check_cookie_manager, results)
    check("import aiotieba 4.7.1", check_import, results)
    check("crypto.so extension", check_crypto_extension, results)
    check("crypto compute_sign", check_compute_sign, results)
    check("protobuf pure-python", check_protobuf_impl, results)
    check("protobuf serialize", check_pb2_serialize, results)
    check("HTTPS SSL patch", check_ssl_context_patch, results)
    check("zlib runtime (libz.so)", check_zlib_runtime, results)
    check("hashlib runtime", check_hashlib_runtime, results)
    check("ssl module", check_ssl_module, results)
    check("gzip runtime", check_gzip_runtime, results)
    check("lxml.etree runtime", check_lxml_runtime, results)
    check("aiohttp runtime", check_aiohttp_runtime, results)
    return results


def render_page():
    results = run_checks()
    all_ok = all(ok for _, ok, _ in results)
    status = "ALL CHECKS PASSED" if all_ok else "SOME CHECKS FAILED"
    status_color = PASS_COLOR if all_ok else FAIL_COLOR

    rows = []
    for name, ok, detail in results:
        rows.append(
            "<div class='card'>"
            "<div class='row'><span class='tag' style='background:{}'>"
            "{}</span><span class='name'>{}</span></div>"
            "<div class='detail'>{}</div></div>".format(
                PASS_COLOR if ok else FAIL_COLOR,
                "PASS" if ok else "FAIL",
                html.escape(name),
                html.escape(detail),
            )
        )

    return """<!doctype html>
<html><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width, initial-scale=1'>
<title>贴吧签到助手 诊断</title>
<style>
 body{{margin:0;background:#F6F7FB;color:#141821;font-family:system-ui,Roboto,sans-serif;
       -webkit-text-size-adjust:100%}}
 header{{background:#2B5CE6;color:#fff;padding:20px}}
 h1{{margin:0;font-size:22px}} .sub{{opacity:.85;font-size:13px;margin-top:4px}}
 main{{padding:20px}}
 .status{{background:#fff;border-radius:16px;padding:18px;font-size:20px;font-weight:700;
           color:{status_color};box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 .card{{background:#fff;border-radius:16px;padding:14px 16px;margin-top:12px;
         box-shadow:0 1px 3px rgba(0,0,0,.08)}}
 .row{{display:flex;align-items:center;gap:10px}}
 .tag{{color:#fff;font-weight:700;font-size:12px;padding:3px 10px;border-radius:999px}}
 .name{{font-weight:600;font-size:15px}}
 .detail{{color:#5B6270;font-size:12px;margin-top:6px;word-break:break-all}}
 .back{{display:inline-block;margin-top:16px;color:#2B5CE6;font-weight:600;
        text-decoration:none}}
</style></head>
<body><header><h1>贴吧签到助手 诊断</h1>
<div class='sub'>Python {py} &middot; {machine} &middot; bootstrap: webview</div></header>
<main><div class='status'>{status}</div>{rows}
<a class='back' href='/'>返回账号</a></main>
</body></html>""".format(
        py=platform.python_version(),
        machine=platform.machine(),
        status_color=status_color,
        status=status,
        rows="".join(rows),
    )
