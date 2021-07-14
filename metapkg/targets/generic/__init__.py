from __future__ import annotations
from typing import *

import pathlib

from metapkg.packages import repository
from metapkg.targets import base as targets
from metapkg.targets.package import SystemPackage

from .build import Build

if TYPE_CHECKING:
    from poetry.core.packages import dependency as poetry_dep
    from poetry.core.packages import package as poetry_pkg


PACKAGE_WHITELIST = [
    "bison",
    "flex",
    "pam",
    "pam-dev",
    "perl",
    "uuid",
    "uuid-dev",
    "zlib",
    "zlib-dev",
]


class GenericRepository(repository.Repository):
    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:

        if dependency.name in PACKAGE_WHITELIST:
            pkg = SystemPackage(
                dependency.name,
                version="1.0",
                pretty_version="1.0",
                system_name=dependency.name,
            )
            self.add_package(pkg)

            return [pkg]
        else:
            return []


class GenericTarget(targets.FHSTarget):
    def __init__(self):
        pass

    @property
    def name(self):
        return f"Generic POSIX"

    def get_package_repository(self):
        return GenericRepository()

    def get_install_root(self, build) -> pathlib.Path:
        return pathlib.Path("/usr/local")

    def get_install_prefix(self, build) -> pathlib.Path:
        return pathlib.Path("lib") / build.root_package.name_slot

    def get_install_path(self, build, aspect) -> pathlib.Path:
        root = self.get_install_root(build)
        prefix = self.get_install_prefix(build)

        if aspect == "sysconf":
            return root / "etc"
        elif aspect == "userconf":
            return pathlib.Path("$HOME") / ".config"
        elif aspect == "data":
            return root / "share" / build.root_package.name_slot
        elif aspect == "bin":
            return root / prefix / "bin"
        elif aspect == "systembin":
            if root == pathlib.Path("/"):
                return root / "usr" / "bin"
            else:
                return root / "bin"
        elif aspect == "lib":
            return root / prefix / "lib"
        elif aspect == "include":
            return root / "include" / build.root_package.name_slot
        elif aspect == "localstate":
            return root / "var"
        elif aspect == "runstate":
            return root / "var" / "run"
        else:
            raise LookupError(f"aspect: {aspect}")

    def build(self, **kwargs):
        return Build(self, **kwargs).run()

    def service_scripts_for_package(self, build, package) -> dict:
        return {}


class GenericLinuxTarget(GenericTarget, targets.LinuxTarget):
    @property
    def name(self):
        return f"Generic Linux"
