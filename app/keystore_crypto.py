# -*- coding: utf-8 -*-
"""Android Keystore AES-GCM for TiebaSign - production module (ks-gcm).

Winning strategy on HarmonyOS NEXT 卓易通: pure-pyjnius Keystore AES-GCM with
*container-runtime* constants (KeyProperties.PURPOSE_* and Cipher.*_MODE).
AOSP literal values (4/2, 1/2) are NOT safe in that container; _runtime_constants()
reads the real values through pyjnius and only falls back to AOSP literals on
standard Android.

pyjnius varargs rule (learned the hard way): call setBlockModes("GCM") /
setEncryptionPaddings("NoPadding") with SCALAR positional args only. Passing a
list/tuple/array makes pyjnius wrap the varargs argument one extra time and the
container throws java.lang.ArrayStoreException.

Storage format: "<iv_b64>.<ct_b64>" (12-byte IV, 128-bit GCM tag, key stays
inside AndroidKeyStore and never leaves it).
"""

import base64
import secrets
import time

KEYSTORE = "AndroidKeyStore"
KEY_ALGORITHM = "AES"
TRANSFORMATION = "AES/GCM/NoPadding"
GCM_TAG_BITS = 128
KEY_SIZE_BITS = 256
DEFAULT_ALIAS = "tiebasign_master"

_classes = None
_constants = None
_master_key = None
_mode = None


def _stage(msg):
    try:
        with open("keystore_stage.log", "a", encoding="utf-8") as fh:
            fh.write("[{:d}] {}\n".format(int(time.time() * 1000), msg))
            fh.flush()
    except Exception:
        pass


def _call(label, fn):
    _stage("{} begin".format(label))
    try:
        out = fn()
        _stage("{} ok".format(label))
        return out
    except Exception as exc:  # noqa: BLE001
        _stage("{} FAIL {}: {}".format(label, type(exc).__name__, exc))
        raise RuntimeError(
            "[stage:{}] {}: {}".format(label, type(exc).__name__, exc)
        ) from exc


def _c():
    global _classes
    if _classes is None:
        from jnius import autoclass

        _classes = {}
        for key, name in (
            ("KeyStore", "java.security.KeyStore"),
            ("KeyGenerator", "javax.crypto.KeyGenerator"),
            ("KeyGenParameterSpecBuilder",
             "android.security.keystore.KeyGenParameterSpec$Builder"),
            ("KeyProperties", "android.security.keystore.KeyProperties"),
            ("Cipher", "javax.crypto.Cipher"),
            ("GCMParameterSpec", "javax.crypto.spec.GCMParameterSpec"),
            ("Base64", "android.util.Base64"),
        ):
            _classes[key] = _call(
                "autoclass " + name, lambda n=name: autoclass(n))
    return _classes


def _runtime_constants():
    global _constants
    if _constants is None:
        try:
            enc = _call("KeyProperties.PURPOSE_ENCRYPT",
                        lambda: _c()["KeyProperties"].PURPOSE_ENCRYPT)
        except Exception:
            enc = 4
        try:
            dec = _call("KeyProperties.PURPOSE_DECRYPT",
                        lambda: _c()["KeyProperties"].PURPOSE_DECRYPT)
        except Exception:
            dec = 2
        try:
            enc_mode = _call("Cipher.ENCRYPT_MODE",
                             lambda: _c()["Cipher"].ENCRYPT_MODE)
        except Exception:
            enc_mode = 1
        try:
            dec_mode = _call("Cipher.DECRYPT_MODE",
                             lambda: _c()["Cipher"].DECRYPT_MODE)
        except Exception:
            dec_mode = 2
        _constants = {
            "enc": enc, "dec": dec,
            "enc_mode": enc_mode, "dec_mode": dec_mode,
        }
        _stage("runtime constants: {}".format(_constants))
    return _constants


def _b64_decode(b64):
    return _call("Base64.decode len={}".format(len(b64)),
                 lambda: _c()["Base64"].decode(b64, _c()["Base64"].NO_WRAP))


def _b64_encode(jbytes):
    if jbytes is None:
        raise ValueError("Base64.encodeToString got null byte[]")
    return _call("Base64.encodeToString",
                 lambda: _c()["Base64"].encodeToString(
                     jbytes, _c()["Base64"].NO_WRAP))


def _jbytes_to_py(jbytes):
    return base64.b64decode(_b64_encode(jbytes).encode("ascii"))


def _py_to_jbytes(b):
    return _b64_decode(base64.b64encode(b).decode("ascii"))


def _keystore():
    ks = _call("KeyStore.getInstance(AndroidKeyStore)",
               lambda: _c()["KeyStore"].getInstance(KEYSTORE))
    _call("KeyStore.load(null)", lambda: ks.load(None))
    return ks


def _delete_entry(alias):
    try:
        _keystore().deleteEntry(alias)
    except Exception:
        pass


def _gen_aes_key(alias, purposes, block_mode="GCM", padding="NoPadding"):
    ks = _keystore()
    if bool(_call("containsAlias {}".format(alias),
                  lambda: ks.containsAlias(alias))):
        return _call("ks.getKey", lambda: ks.getKey(alias, None))
    kg = _call("KeyGenerator.getInstance(AES, AndroidKeyStore)",
               lambda: _c()["KeyGenerator"].getInstance(
                   KEY_ALGORITHM, KEYSTORE))
    builder = _call("KeyGenParameterSpec$Builder(alias, purposes)",
                    lambda: _c()["KeyGenParameterSpecBuilder"](alias, purposes))
    # scalar varargs only - never a list/tuple
    _call("builder.setBlockModes('{}')".format(block_mode),
          lambda: builder.setBlockModes(block_mode))
    _call("builder.setEncryptionPaddings('{}')".format(padding),
          lambda: builder.setEncryptionPaddings(padding))
    _call("builder.setKeySize({})".format(KEY_SIZE_BITS),
          lambda: builder.setKeySize(KEY_SIZE_BITS))
    spec = _call("builder.build()", lambda: builder.build())
    _call("kg.init(spec)", lambda: kg.init(spec))
    _call("kg.generateKey()", lambda: kg.generateKey())
    return _call("ks.getKey", lambda: ks.getKey(alias, None))


def _new_cipher():
    return _call("Cipher.getInstance(AES/GCM/NoPadding)",
                 lambda: _c()["Cipher"].getInstance(TRANSFORMATION))


def _ensure_master_key():
    global _master_key
    if _master_key is None:
        ct = _runtime_constants()
        _master_key = _gen_aes_key(DEFAULT_ALIAS, ct["enc"] | ct["dec"])
    return _master_key


def _gcm_roundtrip(alias, purposes):
    """One throwaway-key encrypt/decrypt round-trip (self-test)."""
    ct = _runtime_constants()
    key = _gen_aes_key(alias, purposes)
    pt = "tieba-gcm-{}".format(secrets.token_hex(6))

    cipher = _new_cipher()
    _call("cipher.init(ENCRYPT, key)",
          lambda: cipher.init(ct["enc_mode"], key))
    iv = _call("cipher.getIV()", lambda: cipher.getIV())
    enc = _call("cipher.doFinal (enc)",
                lambda: cipher.doFinal(_py_to_jbytes(pt.encode("utf-8"))))

    cipher2 = _new_cipher()
    gcm = _call("GCMParameterSpec(128, iv)",
                lambda: _c()["GCMParameterSpec"](GCM_TAG_BITS, iv))
    _call("cipher2.init(DECRYPT, key, gcm)",
          lambda: cipher2.init(ct["dec_mode"], key, gcm))
    dec = _call("cipher2.doFinal (dec)", lambda: cipher2.doFinal(enc))
    back = _jbytes_to_py(dec).decode("utf-8")
    if back != pt:
        raise AssertionError("GCM round-trip mismatch")
    return "Keystore AES-GCM round-trip ok (container runtime constants)"


def self_test():
    """Generate a throwaway key, prove AES-GCM round-trip, delete the key."""
    global _mode
    ct = _runtime_constants()
    alias = "tieba_selftest_" + secrets.token_hex(4)
    _stage("self_test begin alias={}".format(alias))
    try:
        detail = _gcm_roundtrip(alias, ct["enc"] | ct["dec"])
        _mode = "ks-gcm"
        _stage("self_test PASS: {}".format(detail))
        return ("PASS strategy=ks-gcm\n"
                "{} | purposes enc={} dec={} opmode enc={} dec={}".format(
                    detail, ct["enc"], ct["dec"],
                    ct["enc_mode"], ct["dec_mode"]))
    finally:
        _delete_entry(alias)


def _require_ks_gcm():
    if _mode != "ks-gcm":
        # On this device self_test() already succeeded and set _mode;
        # re-run once if it was never run.
        self_test()
    if _mode != "ks-gcm":
        raise RuntimeError("Keystore strategy is {!r}, need ks-gcm".format(_mode))
    return _mode


def encrypt_text(plaintext):
    """str -> "iv_b64.ct_b64" using the master Keystore AES-GCM key."""
    if not isinstance(plaintext, str):
        raise TypeError("plaintext must be str")
    _require_ks_gcm()
    ct = _runtime_constants()
    key = _ensure_master_key()
    cipher = _new_cipher()
    _call("cipher.init(ENCRYPT, masterKey)",
          lambda: cipher.init(ct["enc_mode"], key))
    iv = _call("cipher.getIV()", lambda: cipher.getIV())
    enc = _call("cipher.doFinal (enc)",
                lambda: cipher.doFinal(
                    _py_to_jbytes(plaintext.encode("utf-8"))))
    iv_b64 = base64.b64encode(_jbytes_to_py(iv)).decode("ascii")
    ct_b64 = base64.b64encode(_jbytes_to_py(enc)).decode("ascii")
    return iv_b64 + "." + ct_b64


def decrypt_text(blob):
    """"iv_b64.ct_b64" -> str; raises on tampering or Keystore loss."""
    if not isinstance(blob, str):
        raise TypeError("blob must be str")
    _require_ks_gcm()
    ct = _runtime_constants()
    key = _ensure_master_key()
    try:
        iv_b64, ct_b64 = blob.split(".", 1)
    except ValueError as exc:
        raise ValueError("malformed encrypted blob") from exc
    iv = _py_to_jbytes(base64.b64decode(iv_b64.encode("ascii")))
    enc = _py_to_jbytes(base64.b64decode(ct_b64.encode("ascii")))
    cipher = _new_cipher()
    gcm = _call("GCMParameterSpec(128, iv)",
                lambda: _c()["GCMParameterSpec"](GCM_TAG_BITS, iv))
    _call("cipher.init(DECRYPT, masterKey, gcm)",
          lambda: cipher.init(ct["dec_mode"], key, gcm))
    dec = _call("cipher.doFinal (dec)", lambda: cipher.doFinal(enc))
    return _jbytes_to_py(dec).decode("utf-8")
