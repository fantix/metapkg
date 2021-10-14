from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
)

from metapkg import packages as mpkg
from metapkg.packages import repository
from metapkg import targets
from metapkg.targets import generic

from . import build as winbuild

if TYPE_CHECKING:
    from poetry.core.packages import package as poetry_pkg
    from poetry.core.packages import dependency as poetry_dep


class WindowsRepository(repository.Repository):
    def find_packages(
        self,
        dependency: poetry_dep.Dependency,
    ) -> list[poetry_pkg.Package]:
        return []


class WindowsTarget(generic.GenericTarget):
    def __init__(self, version: tuple[int, ...]) -> None:
        self.version = version

    @property
    def name(self) -> str:
        return f'Windows {".".join(str(v) for v in self.version)}'

    def get_package_system_ident(
        self,
        build: targets.Build,
        package: mpkg.BundledPackage,
        include_slot: bool = False,
    ) -> str:
        if include_slot:
            return f"{package.identifier}{package.slot_suffix}"
        else:
            return package.identifier

    def get_package_repository(self) -> WindowsRepository:
        return WindowsRepository()

    def get_exe_suffix(self) -> str:
        return ".exe"


class ModernWindowsTarget(WindowsTarget):
    def get_builder(self) -> type[winbuild.Build]:
        return winbuild.Build


def get_specific_target(version: tuple[int, ...]) -> WindowsTarget:

    if version >= (10, 0):
        return ModernWindowsTarget(version)
    else:
        raise NotImplementedError(
            f'Windows version {".".join(str(v) for v in version)}'
            " is not supported"
        )
