# -*- coding: utf-8 -*-
"""p4a local recipe for protobuf 7.36.0 (pure-Python parts only).

The stock `protobuf_cpp` recipe is ancient (3.6.1) and aiotieba needs
protobuf >= 4.21.  protobuf 7.36.0 ships a complete pure-Python
implementation under `google/protobuf`; the app forces it by setting
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python before the first import.

We intentionally skip the C upb extension (`google/_upb`) to avoid
cross-compiling generated upb sources; nothing on the sign-in path needs it.
"""

import shutil
from os.path import exists, join

from pythonforandroid.logger import info
from pythonforandroid.recipe import Recipe


class ProtobufRecipe(Recipe):
    name = "protobuf"
    version = "7.36.0"
    url = (
        "https://files.pythonhosted.org/packages/a7/e7/"
        "0553e21d25ca4d9f573135775348a372c3ec34a93a71d5f297c3bac38341/"
        "protobuf-{version}.tar.gz"
        "#sha256=e8e09cb0d794c6687926fa558a8a6e72aa10edb997d5ca61da0765f12a3e00ea"
    )
    depends = ["python3", "setuptools"]
    site_packages_name = "google"
    need_stl_shared = False

    def should_build(self, arch):
        site = self.ctx.get_site_packages_dir(arch)
        if exists(join(site, "google", "__init__.py")) and exists(
            join(site, "google", "protobuf", "__init__.py")
        ):
            info("google.protobuf already installed, skipping")
            return False
        return True

    def build_arch(self, arch):
        build_dir = self.get_build_dir(arch.arch)
        site = self.ctx.get_site_packages_dir(arch)

        dst = join(site, "google")
        if exists(dst):
            shutil.rmtree(dst)
        shutil.copytree(
            join(build_dir, "google"),
            dst,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        info("protobuf {} (pure python) installed to {}".format(self.version, site))


recipe = ProtobufRecipe()
