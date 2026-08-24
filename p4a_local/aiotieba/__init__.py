# -*- coding: utf-8 -*-
"""p4a local recipe for aiotieba 4.7.1.

Why custom:
- aiotieba uses scikit-build-core + CMake, which cannot cross-compile on
  Android, so we bypass its build backend entirely.
- The only C extension is `helper/crypto` (8 C sources + bundled headers).
  We compile it by hand with the NDK clang into crypto.so and install the
  Python package by copying it into the target site-packages.
- We apply the HTTPS fix in-place: aiohttp requests must use an explicit
  default SSLContext (ssl=False would keep the plaintext HTTP behavior that
  the PRD forbids).

No BDUSS or account data ever lives in this recipe.
"""

import shutil
from os.path import exists, join

import sh

from pythonforandroid.logger import info, shprint
from pythonforandroid.recipe import Recipe
from pythonforandroid.toolchain import current_directory
from pythonforandroid.util import ensure_dir


class AiotiebaRecipe(Recipe):
    name = "aiotieba"
    version = "4.7.1"
    url = (
        "https://files.pythonhosted.org/packages/60/6b/"
        "a821387a21650922f25cda835921365f851a262dcd56ab5aefbc267003a7/"
        "aiotieba-{version}.tar.gz"
        "#sha256=9e85fc034af1e439c153485b088d0ab5ab15d6f05b89d2b75839ea0908d5a2a1"
    )
    depends = ["python3", "setuptools", "aiohttp", "lxml", "cryptography", "protobuf"]
    site_packages_name = "aiotieba"
    need_stl_shared = False

    crypto_sources = [
        "src/aiotieba/helper/crypto/src/base32/base32.c",
        "src/aiotieba/helper/crypto/src/crc/crc32.c",
        "src/aiotieba/helper/crypto/src/mbedtls/md5.c",
        "src/aiotieba/helper/crypto/src/mbedtls/sha1.c",
        "src/aiotieba/helper/crypto/src/tbcrypto/bb64.c",
        "src/aiotieba/helper/crypto/src/tbcrypto/cuid.c",
        "src/aiotieba/helper/crypto/src/tbcrypto/lib.c",
        "src/aiotieba/helper/crypto/src/tbcrypto/rc442.c",
    ]

    def should_build(self, arch):
        site = self.ctx.get_site_packages_dir(arch)
        pkg = join(site, "aiotieba")
        if exists(join(pkg, "__init__.py")) and exists(
            join(pkg, "helper", "crypto", "crypto.so")
        ):
            info("aiotieba already installed in site-packages, skipping")
            return False
        return True

    # ---------------------------------------------------------------- patches
    def _patch_file(self, path, replacements):
        """Apply exact literal replacements; fail loudly if anchors moved."""
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        for old, new in replacements:
            if old not in text:
                raise RuntimeError(
                    "anchor not found in {}: {!r}".format(path, old[:80])
                )
            text = text.replace(old, new)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def _patch_sources(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        pkg = join(build_dir, "src", "aiotieba")

        http_path = join(pkg, "core", "http.py")
        with open(http_path, "r", encoding="utf-8") as fh:
            http_snapshot = fh.read()
        if (
            "_SSL_CONTEXT = ssl.create_default_context()" in http_snapshot
            and "ssl=False" not in http_snapshot
        ):
            # Previous build attempt already patched the unpacked sources.
            info("aiotieba sources already patched, skipping")
            return

        self._patch_file(
            join(pkg, "core", "http.py"),
            [
                ("import urllib.parse\n", "import urllib.parse\nimport ssl\n"),
                (
                    "from ..helper.crypto import APP_SALT, sign\n",
                    "from ..helper.crypto import APP_SALT, sign\n"
                    "\n"
                    "try:\n"
                    "    import certifi\n"
                    "    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())\n"
                    "except Exception:\n"
                    "    _SSL_CONTEXT = ssl.create_default_context()\n",
                ),
            ],
        )
        with open(join(pkg, "core", "http.py"), "r", encoding="utf-8") as fh:
            http_text = fh.read()
        if http_text.count("ssl=False,") != 4:
            raise RuntimeError("expected 4 ssl=False sites in core/http.py")
        self._patch_file(
            join(pkg, "core", "http.py"),
            [("ssl=False,", "ssl=_SSL_CONTEXT,")],
        )

        self._patch_file(
            join(pkg, "client.py"),
            [
                (
                    "import socket\n",
                    "import socket\nimport ssl\n"
                    "\ntry:\n"
                    "    import certifi\n"
                    "    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())\n"
                    "except Exception:\n"
                    "    _SSL_CONTEXT = ssl.create_default_context()\n",
                ),
                ("ssl=False,", "ssl=_SSL_CONTEXT,"),
            ],
        )

        self._patch_file(
            join(pkg, "api", "init_z_id", "_api.py"),
            [
                (
                    "import hashlib\n",
                    "import hashlib\nimport ssl\n"
                    "\ntry:\n"
                    "    import certifi\n"
                    "    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())\n"
                    "except Exception:\n"
                    "    _SSL_CONTEXT = ssl.create_default_context()\n",
                ),
                ("ssl=False,", "ssl=_SSL_CONTEXT,"),
            ],
        )

        self._patch_file(
            join(pkg, "core", "websocket.py"),
            [
                (
                    "import random\n",
                    "import random\nimport ssl\n"
                    "\ntry:\n"
                    "    import certifi\n"
                    "    _SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())\n"
                    "except Exception:\n"
                    "    _SSL_CONTEXT = ssl.create_default_context()\n",
                ),
                ("ssl=False,", "ssl=_SSL_CONTEXT,"),
            ],
        )

        self._patch_file(
            join(pkg, "__version__.py"),
            [('__version__ = "TBD"', '__version__ = "4.7.1"')],
        )

        # Final invariant: the patched modules never pass ssl=False.
        for rel in ("core/http.py", "client.py", "api/init_z_id/_api.py", "core/websocket.py"):
            with open(join(pkg, rel), "r", encoding="utf-8") as fh:
                if "ssl=False" in fh.read():
                    raise RuntimeError("ssl=False still present in {}".format(rel))

    # ------------------------------------------------------------------ build
    def build_arch(self, arch):
        self._patch_sources(arch)

        build_dir = self.get_build_dir(arch.arch)
        crypto_dir = join(build_dir, "src", "aiotieba", "helper", "crypto")
        include_dir = join(crypto_dir, "include")

        env = self.get_recipe_env(arch, with_flags_in_cc=False)
        cli = env["CC"].split()[0]
        if "ccache" in cli:  # CC may be prefixed by ccache
            cli = env["CC"].split()[1]
        cc = sh.Command(cli)

        cflags = env["CFLAGS"].split()
        cflags += env["CPPFLAGS"].split()
        cflags += ["-fPIC", "-O3", "-I" + include_dir]

        obj_dir = join(build_dir, "crypto_objs")
        ensure_dir(obj_dir)

        # mbedtls/alignment.h declares its unaligned helpers as bare C99
        # `inline` functions, which never emit an external definition.  With
        # -O0 those calls stay un-inlined and crypto.so ends up with UND
        # symbols (mbedtls_get_unaligned_uint32 / mbedtls_put_unaligned_uint32)
        # that nothing provides at runtime on Android.  This shim forces the
        # compiler to emit real definitions for every inline helper.
        shim_path = join(obj_dir, "mbedtls_alignment_shim.c")
        with open(shim_path, "w", encoding="utf-8") as fh:
            fh.write(
                "/* Force external definitions for the C99 inline helpers\n"
                "   declared in mbedtls/alignment.h (see recipe). */\n"
                '#include "mbedtls/alignment.h"\n'
                "\n"
                "extern inline void mbedtls_put_unaligned_uint16(void* p, uint16_t x);\n"
                "extern inline uint32_t mbedtls_get_unaligned_uint32(const void* p);\n"
                "extern inline void mbedtls_put_unaligned_uint32(void* p, uint32_t x);\n"
                "extern inline uint64_t mbedtls_get_unaligned_uint64(const void* p);\n"
                "extern inline void mbedtls_put_unaligned_uint64(void* p, uint64_t x);\n"
            )

        objects = []
        with current_directory(build_dir):
            for rel in self.crypto_sources:
                src = join(build_dir, rel)
                obj = join(obj_dir, rel.replace("/", "_").replace("\\", "_") + ".o")
                shprint(cc, *cflags, "-c", src, "-o", obj, _env=env)
                objects.append(obj)
            shim_obj = join(obj_dir, "mbedtls_alignment_shim.c.o")
            shprint(cc, *cflags, "-c", shim_path, "-o", shim_obj, _env=env)
            objects.append(shim_obj)

            out_so = join(build_dir, "crypto.so")
            ldflags = env["LDFLAGS"].split()
            ldflags += [
                "-L" + self.ctx.python_recipe.link_root(arch.arch),
                "-lpython" + self.ctx.python_recipe.link_version,
                "-Wl,--no-undefined",
            ]
            # CFLAGS carry the aarch64 target triple; reuse them at link time
            # or clang falls back to the host x86-64 target.
            shprint(
                cc,
                *env["CFLAGS"].split(),
                "-shared",
                *objects,
                "-o",
                out_so,
                *ldflags,
                _env=env,
            )

            strip = env.get("STRIP", "").split()
            if strip and not self.ctx.with_debug_symbols:
                shprint(sh.Command(strip[0]), "--strip-unneeded", out_so, _env=env)

        # ------------------------------------------------------------- install
        site = self.ctx.get_site_packages_dir(arch)
        dst_pkg = join(site, "aiotieba")
        if exists(dst_pkg):
            shutil.rmtree(dst_pkg)
        shutil.copytree(
            join(build_dir, "src", "aiotieba"),
            dst_pkg,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        shutil.copy2(out_so, join(dst_pkg, "helper", "crypto", "crypto.so"))
        info("aiotieba {} installed to {}".format(self.version, site))


recipe = AiotiebaRecipe()
