# -*- coding: utf-8 -*-
"""p4a local recipe: build a REAL runnable libz.so from zlib source.

Why: the HarmonyOS 卓易通 Android container cannot resolve the system libz.so
that the CPython zlib extension (zlib.cpython-312.so) links against. We compile
zlib 1.3.1 with the NDK and ship libz.so inside the APK (nativeLibraryDir), so
the zlib extension and every other library NEEDED libz.so (aiohttp gzip
decompression, lxml/libxml2) can resolve it locally.

The NDK sysroot libz.so is only a linker stub and must NOT be shipped; this
recipe builds the real library from the official zlib source tarball.
"""

import glob
import sh
from os.path import basename, isfile, join
from pythonforandroid.logger import shprint
from pythonforandroid.recipe import Recipe
from pythonforandroid.util import ensure_dir


class ZlibRecipe(Recipe):
    version = "1.3.1"
    url = ("https://zlib.net/fossils/zlib-{version}.tar.gz"
           "#sha256=9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23")
    depends = []

    def should_build(self, arch):
        return not isfile(join(self.ctx.get_libs_dir(arch.arch), "libz.so"))

    def build_arch(self, arch):
        env = self.get_recipe_env(arch, with_flags_in_cc=False)
        cli = env["CC"].split()[0]
        if basename(cli) == "ccache":
            cli = env["CC"].split()[1]
        cc = sh.Command(cli)

        build_dir = self.get_build_dir(arch.arch)
        cflags = (env["CFLAGS"].split() + env["CPPFLAGS"].split()
                  + ["-fPIC", "-O3", "-I" + build_dir]
                  # We bypass zlib's configure, so zconf.h never learns these
                  # feature macros; without them gzlib.c skips <unistd.h> and
                  # lseek becomes an implicit declaration (NDK clang error).
                  + ["-DHAVE_UNISTD_H", "-DZ_HAVE_UNISTD_H",
                     "-D_LARGEFILE64_SOURCE=1"])

        objs = []
        for src in sorted(glob.glob(join(build_dir, "*.c"))):
            obj = src + ".o"
            shprint(cc, *cflags, "-c", src, "-o", obj, _env=env)
            objs.append(obj)

        libs_dir = self.ctx.get_libs_dir(arch.arch)
        ensure_dir(libs_dir)
        out = join(libs_dir, "libz.so")
        # SONAME must be exactly libz.so: CPython's zlib extension records
        # DT_NEEDED libz.so, and a mismatched soname would not resolve in the
        # container namespace.
        shprint(
            cc,
            *env["CFLAGS"].split(),
            "-shared",
            "-Wl,-soname,libz.so",
            "-o", out,
            *objs,
            *env["LDFLAGS"].split(),
            _env=env,
        )
        if env.get("STRIP"):
            strip_cmd = env["STRIP"].split()
            shprint(sh.Command(strip_cmd[0]), *strip_cmd[1:], out)


recipe = ZlibRecipe()
