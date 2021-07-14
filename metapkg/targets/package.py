from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Optional
from typing import TypeVar
from typing import Union

from poetry.core.packages import package as poetry_pkg

if TYPE_CHECKING:
    from poetry.core.semver.version import Version


class SystemPackage(poetry_pkg.Package):
    def __init__(
        self,
        name: str,
        version: Union[str, Version],
        pretty_version: Optional[str] = None,
        system_name: Optional[str] = None,
    ):
        super().__init__(name, version, pretty_version=pretty_version)
        self._system_name = system_name

    @property
    def system_name(self) -> Optional[str]:
        return self._system_name

    def clone(self: SystemPackage_T) -> SystemPackage_T:
        clone = self.__class__(
            self.name, self.version, self.pretty_version, self.system_name
        )
        for dep in self.requires:
            clone.requires.append(dep)

        return clone

    def __repr__(self) -> str:
        return "<SystemPackage {}>".format(self.unique_name)


SystemPackage_T = TypeVar("SystemPackage_T", bound=SystemPackage)
