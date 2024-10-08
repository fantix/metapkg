from __future__ import annotations
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Literal,
    Mapping,
    Type,
    TypeVar,
    Union,
    overload,
)

import collections
import copy
import dataclasses
import enum
import glob
import hashlib
import inspect
import os
import pathlib
import platform
import pprint
import shlex
import sys
import textwrap

import packaging.utils

from poetry.core.packages import dependency as poetry_dep
from poetry.core.packages import dependency_group as poetry_depgroup
from poetry.core.packages import package as poetry_pkg
from poetry.core.semver import version as poetry_version
from poetry.core.version import pep440 as poetry_pep440
from poetry.core.constraints import version as poetry_constr
from poetry.core.version.pep440 import segments as poetry_pep440_segments
from poetry.core.spdx import helpers as poetry_spdx_helpers

from metapkg import tools
from . import repository
from . import sources as af_sources

if TYPE_CHECKING:
    from cleo.io import io as cleo_io
    from metapkg import targets
    from poetry.repositories import repository as poetry_repo


get_build_requirements = repository.get_build_requirements
set_build_requirements = repository.set_build_requirements
canonicalize_name = packaging.utils.canonicalize_name
NormalizedName = packaging.utils.NormalizedName


class AliasPackage(poetry_pkg.Package):
    def __repr__(self) -> str:
        return "<AliasPackage {}>".format(self.unique_name)


class PackageFileLayout(enum.IntEnum):
    REGULAR = enum.auto()
    FLAT = enum.auto()
    SINGLE_BINARY = enum.auto()


@dataclasses.dataclass
class MetaPackage:
    name: str
    description: str
    dependencies: dict[str, str]


class BasePackage(poetry_pkg.Package):
    @property
    def slot_suffix(self) -> str:
        return ""

    def get_sources(self) -> list[af_sources.BaseSource]:
        raise NotImplementedError

    def get_requirements(self) -> list[poetry_dep.Dependency]:
        return []

    def get_build_requirements(self) -> list[poetry_dep.Dependency]:
        return []

    def get_license_files_pattern(self) -> str:
        return "{LICENSE*,COPYING,NOTICE,COPYRIGHT}"

    def get_configure_script(self, build: targets.Build) -> str:
        raise NotImplementedError(f"{self}.configure()")

    def get_build_script(self, build: targets.Build) -> str:
        raise NotImplementedError(f"{self}.build()")

    def get_build_install_script(self, build: targets.Build) -> str:
        script = ""

        licenses = self.get_license_files_pattern()
        if licenses:
            sdir = build.get_source_dir(self, relative_to="pkgbuild")
            legaldir = build.get_install_path("legal").relative_to("/")
            lic_dest = (
                build.get_install_dir(self, relative_to="pkgbuild") / legaldir
            )
            prefix = str(lic_dest / self.name)
            script += textwrap.dedent(
                f"""\
                mkdir -p "{lic_dest}"
                for _lic_src in "{sdir}"/{licenses}; do
                    if [ -e "$_lic_src" ]; then
                        cp "$_lic_src" "{prefix}-$(basename "$_lic_src")"
                    fi
                done
                """
            )

        return script

    def get_build_tools(self, build: targets.Build) -> dict[str, pathlib.Path]:
        return {}

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        return {}

    def _get_file_list_script(
        self,
        build: targets.Build,
        listname: str,
        *,
        entries: list[str],
    ) -> str:
        if entries:
            script = self.write_file_list_script(build, listname, entries)
        else:
            script = ""

        return script

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = []
        if self.get_license_files_pattern():
            entries.append("{legaldir}/*")
        return entries

    def get_install_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_install_entries(build)
        entries += [
            str(p.relative_to("/")) for p in self.get_service_scripts(build)
        ]
        return self._get_file_list_script(build, "install", entries=entries)

    def get_file_no_install_entries(self, build: targets.Build) -> list[str]:
        return []

    def get_no_install_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_no_install_entries(build)
        return self._get_file_list_script(build, "no_install", entries=entries)

    def get_file_ignore_entries(self, build: targets.Build) -> list[str]:
        return []

    def get_ignore_list_script(self, build: targets.Build) -> str:
        entries = self.get_file_ignore_entries(build)
        return self._get_file_list_script(build, "ignore", entries=entries)

    def get_private_libraries(self, build: targets.Build) -> list[str]:
        return []

    def get_extra_system_requirements(
        self, build: targets.Build
    ) -> dict[str, list[str]]:
        return {}

    def get_before_install_script(self, build: targets.Build) -> str:
        return ""

    def get_after_install_script(self, build: targets.Build) -> str:
        return ""

    def get_service_scripts(
        self, build: targets.Build
    ) -> dict[pathlib.Path, str]:
        return {}

    def get_bin_shims(self, build: targets.Build) -> dict[str, str]:
        return {}

    def get_exposed_commands(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def get_shlib_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def get_shlibs(self, build: targets.Build) -> list[str]:
        return []

    def get_include_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return []

    def get_dep_commands(self) -> list[str]:
        return []

    def write_file_list_script(
        self, build: targets.Build, listname: str, entries: list[str]
    ) -> str:
        installdest = build.get_install_dir(self, relative_to="pkgbuild")

        paths: dict[str, str | pathlib.Path] = {}
        for aspect in ("systembin", "bin", "data", "include", "lib", "legal"):
            path = build.get_install_path(aspect).relative_to("/")
            paths[f"{aspect}dir"] = path

        paths["prefix"] = build.get_full_install_prefix().relative_to("/")
        paths["exesuffix"] = build.get_exe_suffix()

        processed_entries = []
        for entry in entries:
            processed_entries.append(
                entry.strip().format(**paths).replace("/", os.sep)
            )

        pyscript = textwrap.dedent(
            """\
            import glob
            import pathlib

            tmp = pathlib.Path({installdest!r})

            patterns = {patterns}

            for pattern in patterns:
                if pattern.endswith('/**'):
                    pattern += "/*"
                for p in tmp.glob(pattern):
                    if p.exists():
                        print(p.relative_to(tmp))
        """
        ).format(
            installdest=str(installdest),
            patterns=pprint.pformat(processed_entries),
        )

        scriptfile_name = f"_gen_{listname}_list_{self.unique_name}.py"

        return build.sh_write_python_helper(
            scriptfile_name, pyscript, relative_to="pkgbuild"
        )

    def get_package_layout(self, build: targets.Build) -> PackageFileLayout:
        return PackageFileLayout.REGULAR


BundledPackage_T = TypeVar("BundledPackage_T", bound="BundledPackage")


class BundledPackage(BasePackage):
    name: ClassVar[packaging.utils.NormalizedName]
    title: ClassVar[str | None] = None
    aliases: ClassVar[list[str] | None] = None
    description: str = ""
    license_id: ClassVar[str | None] = None
    group: ClassVar[str]
    url: ClassVar[str | None] = None
    identifier: ClassVar[str]

    source_version: str

    artifact_requirements: Union[
        list[str | poetry_dep.Dependency],
        dict[
            str | poetry_constr.VersionConstraint,
            list[str | poetry_dep.Dependency],
        ],
    ] = []
    artifact_build_requirements: list[str | poetry_dep.Dependency] = []

    options: dict[str, Any]
    metadata_tags: dict[str, str]

    sources: list[af_sources.SourceDecl]
    resolved_sources: list[af_sources.BaseSource] = []

    @property
    def slot(self) -> str:
        return ""

    @property
    def slot_suffix(self) -> str:
        if self.slot:
            return f"-{self.slot}"
        else:
            return ""

    @property
    def name_slot(self) -> str:
        return f"{self.name}{self.slot_suffix}"

    def version_includes_revision(self) -> bool:
        return True

    def version_includes_slot(self) -> bool:
        return True

    @classmethod
    def get_source_url_variables(cls, version: str) -> dict[str, str]:
        return {}

    @classmethod
    def _get_sources(cls, version: str | None) -> list[af_sources.BaseSource]:
        sources = []

        if version is None:
            version = "HEAD"
        underscore_v = version.replace(".", "_")
        dash_v = version.replace(".", "-")
        parts = version.split(".")
        major_v = parts[0]
        major_minor_v = ".".join(parts[:2])
        for source in cls.sources:
            if isinstance(source, dict):
                clsfile = inspect.getsourcefile(cls)
                if clsfile is not None:
                    clsdirname = pathlib.Path(clsfile).parent
                else:
                    clsdirname = None
                url = source["url"].format(
                    version=version,
                    underscore_version=underscore_v,
                    dash_version=dash_v,
                    major_v=major_v,
                    major_minor_v=major_minor_v,
                    dirname=clsdirname,
                    **cls.get_source_url_variables(version),
                )
                extras = source.get("extras")
                if extras:
                    if "version" not in extras:
                        extras["version"] = version
                else:
                    extras = af_sources.SourceExtraDecl({"version": version})

                if "vcs_version" not in extras:
                    extras["vcs_version"] = cls.to_vcs_version(
                        extras["version"]
                    )

                src = af_sources.source_for_url(url, extras)

                csum = source.get("csum")
                csum_url = source.get("csum_url")
                csum_algo = source.get("csum_algo")

                if csum_algo:
                    if csum_url:
                        csum_url = csum_url.format(
                            version=version,
                            underscore_version=underscore_v,
                            dash_version=dash_v,
                        )
                    csum_verify = af_sources.HashVerification(
                        csum_algo, hash_url=csum_url, hash_value=csum
                    )
                    src.add_verification(csum_verify)

            else:
                src = af_sources.source_for_url(source)

            sources.append(src)

        return sources

    @classmethod
    def to_vcs_version(cls, version: str) -> str:
        return version

    @classmethod
    def parse_vcs_version(cls, version: str) -> poetry_version.Version:
        return poetry_version.Version.parse(version)

    @classmethod
    def get_package_repository(
        cls, target: targets.Target, io: cleo_io.IO
    ) -> poetry_repo.Repository:
        return repository.bundle_repo

    @classmethod
    def version_from_source(
        cls,
        source_dir: pathlib.Path,
    ) -> str:
        raise NotImplementedError

    @classmethod
    def get_vcs_source(
        cls, ref: str | None = None
    ) -> af_sources.GitSource | None:
        sources = cls._get_sources(version=ref)
        if len(sources) == 1 and isinstance(sources[0], af_sources.GitSource):
            return sources[0]
        else:
            return None

    @classmethod
    def resolve_vcs_source(
        cls, io: cleo_io.IO, *, ref: str | None = None
    ) -> pathlib.Path:
        source = cls.get_vcs_source(ref)
        if source is None:
            raise ValueError("Unable to resolve non-git bundled package")
        return source.download(io)

    @classmethod
    def resolve_vcs_repo(
        cls,
        io: cleo_io.IO,
        version: str | None = None,
    ) -> tools.git.Git:
        repo_dir = cls.resolve_vcs_source(io, ref=version)
        return tools.git.Git(repo_dir)

    @classmethod
    def resolve_vcs_version(
        cls,
        io: cleo_io.IO,
        repo: tools.git.Git,
        version: str | None = None,
    ) -> str:
        rev: str

        if version is None:
            rev = repo.rev_parse("HEAD").strip()
        else:
            output = repo.run("ls-remote", repo.remote_url(), version)

            if output:
                rev, _ = output.split()
                # If it's a tag, resolve the underlying commit.
                if repo.run("cat-file", "-t", rev) == "tag":
                    rev = repo.run("rev-list", "-n", "1", rev)
            else:
                # The name can be a branch or tag, so we attempt to look it up
                # with ls-remote. If we don't find anything, we assume it's a
                # commit hash.
                rev = version

        return rev

    @classmethod
    def version_from_vcs_version(
        cls,
        io: cleo_io.IO,
        repo: tools.git.Git,
        vcs_version: str,
        is_release: bool,
    ) -> str:
        ver = repo.run("describe", "--tags", vcs_version).strip()
        if ver.startswith("v"):
            ver = ver[1:]

        parts = ver.rsplit("-", maxsplit=2)
        if (
            len(parts) == 3
            and parts[2].startswith("g")
            and parts[1].isdigit()
            and parts[1].isascii()
        ):
            # Have commits after the tag
            parsed_ver = cls.parse_vcs_version(parts[0]).next_major()

            if not is_release:
                commits = repo.run(
                    "rev-list",
                    "--count",
                    vcs_version,
                )

                ver = parsed_ver.replace(
                    local=None,
                    pre=None,
                    dev=poetry_pep440.ReleaseTag("dev", int(commits)),
                ).to_string(short=False)
            else:
                ver = parsed_ver.to_string(short=False)

        return ver

    @classmethod
    def resolve(
        cls: Type[BundledPackage_T],
        io: cleo_io.IO,
        *,
        version: str | None = None,
        revision: str | None = None,
        is_release: bool = False,
        target: targets.Target,
    ) -> BundledPackage_T:
        sources = cls._get_sources(version)
        is_git = cls.get_vcs_source(version) is not None

        if is_git:
            repo = cls.resolve_vcs_repo(io, version)
            if version:
                vcs_version = cls.to_vcs_version(version)
            else:
                vcs_version = None
            source_version = cls.resolve_vcs_version(io, repo, vcs_version)
            version = cls.version_from_vcs_version(
                io, repo, source_version, is_release
            )

            git_date = repo.run(
                "show",
                "-s",
                "--format=%cd",
                "--date=format-local:%Y%m%d%H",
                source_version,
                env={**os.environ, **{"TZ": "UTC", "LANG": "C"}},
            )
        elif version is not None:
            source_version = version
            git_date = ""
        elif len(sources) == 1 and isinstance(
            sources[0], af_sources.LocalSource
        ):
            source_dir = sources[0].url
            version = cls.version_from_source(pathlib.Path(source_dir))
            source_version = version
        else:
            raise ValueError("version must be specified for non-git packages")

        if not revision:
            revision = "1"

        if is_git:
            ver = cls.parse_vcs_version(version)
        else:
            ver = poetry_version.Version.parse(version)

        local = ver.local
        if isinstance(ver.local, tuple):
            local = ver.local
        elif ver.local is None:
            local = ()
        else:
            local = (ver.local,)

        if is_git:
            ver = ver.replace(
                local=local
                + (
                    f"r{revision}",
                    f"d{git_date}",
                    f"g{source_version[:9]}",
                )
            )
        else:
            ver = ver.replace(local=local + (f"r{revision}",))

        version, pretty_version = cls.format_version(ver)

        return cls(
            version=version,
            pretty_version=pretty_version,
            source_version=source_version,
            resolved_sources=sources,
        )

    @classmethod
    def format_version(cls, ver: poetry_version.Version) -> tuple[str, str]:
        full_ver = pep440_to_semver(ver)
        version_base = pep440_to_semver(ver.without_local())
        version_hash = hashlib.sha256(full_ver.encode("utf-8")).hexdigest()
        version = f"{version_base}+{version_hash[:7]}"
        pretty_version = f"{full_ver}.s{version_hash[:7]}"
        return version, pretty_version

    def get_sources(self) -> list[af_sources.BaseSource]:
        if self.resolved_sources:
            return self.resolved_sources
        else:
            return self._get_sources(version=self.source_version)

    def get_patches(
        self,
    ) -> dict[str, list[tuple[str, str]]]:
        modpath = pathlib.Path(sys.modules[self.__module__].__path__[0])
        patches_dir = modpath / "patches"

        patches = collections.defaultdict(list)
        if patches_dir.exists():
            for path in patches_dir.glob("*.patch"):
                with open(path, "r") as f:
                    pkg, _, rest = path.stem.partition("__")
                    patches[pkg].append((rest, f.read()))

            for pkg, plist in patches.items():
                plist.sort(key=lambda i: i[0])

        return patches

    def __init__(
        self,
        version: str | poetry_version.Version,
        pretty_version: str | None = None,
        *,
        source_version: str | None = None,
        requires: list[poetry_dep.Dependency] | None = None,
        options: Mapping[str, Any] | None = None,
        resolved_sources: list[af_sources.BaseSource] | None = None,
    ) -> None:
        if self.title is None:
            raise RuntimeError(
                f"{type(self)!r} does not define the required "
                f"title attribute"
            )

        super().__init__(self.name, version, pretty_version=pretty_version)

        if requires is not None:
            reqs = list(requires)
        else:
            reqs = []

        reqs.extend(self.get_requirements())

        if reqs:
            if poetry_depgroup.MAIN_GROUP not in self._dependency_groups:
                self._dependency_groups[poetry_depgroup.MAIN_GROUP] = (
                    poetry_depgroup.DependencyGroup(poetry_depgroup.MAIN_GROUP)
                )

            main_group = self._dependency_groups[poetry_depgroup.MAIN_GROUP]
            for req in reqs:
                main_group.add_dependency(req)

        if resolved_sources is not None:
            self.resolved_sources = list(resolved_sources)
        else:
            self.resolved_sources = []

        self.metadata_tags = {}

        repository.set_build_requirements(self, self.get_build_requirements())
        self.description = type(self).description
        license_id = type(self).license_id
        if license_id is not None:
            self.license = poetry_spdx_helpers.license_by_id(license_id)
        self.options = dict(options) if options is not None else {}
        if source_version is None:
            self.source_version = self.pretty_version
        else:
            self.source_version = source_version

        repository.bundle_repo.add_package(self)

        if self.aliases:
            for alias in self.aliases:
                pkg = AliasPackage(name=alias, version=self.version)
                pkg.add_dependency(
                    poetry_dep.Dependency(self.name, self.version)
                )
                repository.bundle_repo.add_package(pkg)

    def get_requirements(self) -> list[poetry_dep.Dependency]:
        reqs = []

        req_spec: list[str | poetry_dep.Dependency] = []

        if isinstance(self.artifact_requirements, dict):
            for ver, ver_reqs in self.artifact_requirements.items():
                if isinstance(ver, str):
                    ver = poetry_constr.parse_constraint(ver)
                if ver.allows(self.version):
                    req_spec = ver_reqs
                    break
            else:
                if self.artifact_requirements:
                    raise RuntimeError(
                        f"artifact_requirements for {self.name!r} are not "
                        f"empty, but don't match the requested version "
                        f"{self.version}"
                    )
        else:
            req_spec = self.artifact_requirements

        for item in req_spec:
            if isinstance(item, str):
                reqs.append(poetry_dep.Dependency.create_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def get_build_requirements(self) -> list[poetry_dep.Dependency]:
        reqs = []
        for item in self.artifact_build_requirements:
            if isinstance(item, str):
                reqs.append(poetry_dep.Dependency.create_from_pep_508(item))
            else:
                reqs.append(item)
        return reqs

    def clone(self: BundledPackage_T) -> BundledPackage_T:
        clone = self.__class__(self.version)
        clone.__dict__ = copy.deepcopy(self.__dict__)
        return clone

    def is_root(self) -> bool:
        return False

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: Literal[False]
    ) -> dict[str, str]: ...

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str
    ) -> dict[str, str]: ...

    @overload
    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: Literal[True]
    ) -> dict[str, bytes]: ...

    def read_support_files(
        self, build: targets.Build, file_glob: str, binary: bool = False
    ) -> dict[str, str] | dict[str, bytes]:
        mod = sys.modules[type(self).__module__]
        mod_file = mod.__file__
        assert mod_file is not None
        path = pathlib.Path(mod_file).parent / file_glob

        result = {}

        for pathname in glob.glob(str(path)):
            path = pathlib.Path(pathname)
            mode = "rb" if binary else "r"
            with open(path, mode) as f:
                content = f.read()
                name = path.name
                if not binary and name.endswith(".in"):
                    content = build.format_package_template(content, self)
                    name = name[:-3]
                    name = name.replace("SLOT", self.slot)
                    name = name.replace(
                        "IDENTIFIER",
                        build.target.get_package_system_ident(build, self),
                    )
                result[name] = content

        return result

    def _read_install_entries(
        self,
        build: targets.Build,
        listname: str,
    ) -> list[str]:
        mod = sys.modules[type(self).__module__]
        mod_file = mod.__file__
        assert mod_file is not None
        path = pathlib.Path(mod_file).parent / f"{listname}.list"

        entries: list[str] = []

        if path.exists():
            with open(path, "r") as f:
                entries.extend(f)

        return entries

    def get_file_install_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_install_entries(build)
        return entries + self._read_install_entries(build, "install")

    def get_file_no_install_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_no_install_entries(build)
        return entries + self._read_install_entries(build, "no_install")

    def get_file_ignore_entries(self, build: targets.Build) -> list[str]:
        entries = super().get_file_ignore_entries(build)
        return entries + self._read_install_entries(build, "ignore")

    def get_build_install_script(self, build: targets.Build) -> str:
        service_scripts = self.get_service_scripts(build)
        if service_scripts:
            install = build.sh_get_command("cp", relative_to="pkgbuild")
            extras_dir = build.get_extras_root(relative_to="pkgbuild")
            install_dir = build.get_install_dir(self, relative_to="pkgbuild")
            ensuredir = build.target.get_action("ensuredir", build)
            if TYPE_CHECKING:
                assert isinstance(ensuredir, targets.EnsureDirAction)

            commands = []

            for path, _content in service_scripts.items():
                path = path.relative_to("/")
                commands.append(
                    ensuredir.get_script(path=str((install_dir / path).parent))
                )
                args: dict[str, str | None] = {
                    str(extras_dir / path): None,
                    str(install_dir / path): None,
                }
                cmd = build.sh_format_command(install, args)
                commands.append(cmd)

            return "\n".join(commands)
        else:
            return ""

    def get_resources(self, build: targets.Build) -> dict[str, bytes]:
        return self.read_support_files(build, "resources/*", binary=True)

    def get_service_scripts(
        self, build: targets.Build
    ) -> dict[pathlib.Path, str]:
        return build.target.service_scripts_for_package(build, self)

    def get_bin_shims(self, build: targets.Build) -> dict[str, str]:
        return self.read_support_files(build, "shims/*")

    def __repr__(self) -> str:
        return "<BundledPackage {}>".format(self.unique_name)

    def get_meta_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[MetaPackage]:
        return []

    def get_conflict_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[str]:
        return []

    def get_provided_packages(
        self,
        build: targets.Build,
        root_version: str,
    ) -> list[tuple[str, str]]:
        return []

    def get_version_details(self) -> dict[str, Any]:
        pv = poetry_version.Version.parse(self.pretty_version)

        prerelease = []
        if pv.pre is not None:
            prerelease.append(
                {
                    "phase": semver_pre_tag(pv),
                    "number": pv.pre.number,
                }
            )

        if pv.dev is not None:
            prerelease.append(
                {
                    "phase": pv.dev.phase,
                    "number": pv.dev.number,
                }
            )

        if pv.local:
            local: tuple[str | int, ...]
            if isinstance(pv.local, tuple):
                local = pv.local
            elif pv.local is None:
                local = ()
            else:
                local = (pv.local,)

            ver_metadata = self.parse_version_metadata(local)
        else:
            ver_metadata = {}

        return {
            "major": pv.major,
            "minor": pv.minor,
            "patch": pv.patch,
            "prerelease": prerelease,
            "metadata": ver_metadata,
        }

    def get_artifact_metadata(self, build: targets.Build) -> dict[str, Any]:
        metadata = {
            "name": self.name,
            "version": pep440_to_semver(self.version),
            "version_details": self.get_version_details(),
            "revision": build.revision,
            "build_date": build.build_date.isoformat(),
            "target": build.target.triple,
            "architecture": build.target.machine_architecture,
            "dist": build.target.ident,
            "channel": build.channel,
            "tags": self.metadata_tags,
        }

        if self.slot:
            metadata["version_slot"] = self.slot

        return metadata

    def parse_version_metadata(
        self,
        segments: tuple[str | int, ...],
    ) -> dict[str, str]:
        result = {}
        pfx_map = self.get_version_metadata_fields()
        for segment in segments:
            segment_str = str(segment)
            for pfx_len in (1, 2):
                key = pfx_map.get(segment_str[:pfx_len])
                if key is not None:
                    result[key] = segment_str[pfx_len:]
                    break
            else:
                raise RuntimeError(
                    f"unrecognized version metadata field `{segment}`"
                )

        return result

    def get_version_metadata_fields(self) -> dict[str, str]:
        return {
            "r": "build_revision",
            "d": "source_date",
            "g": "scm_revision",
            "t": "target",
            "s": "build_hash",
            "b": "build_type",
        }

    def set_metadata_tags(self, tags: Mapping[str, str]) -> None:
        self.metadata_tags = dict(tags)


class PrePackagedPackage(BundledPackage):
    pass


class BuildSystemMakePackage(BundledPackage):
    def get_build_script(self, build: targets.Build) -> str:
        target = self.get_make_target(build)
        return self.get_make_command(build, target)

    def get_make_command(self, build: targets.Build, target: str) -> str:
        args = self.get_make_args(build)
        make = build.sh_get_command("make", args=args)
        env = self.get_make_env(build, "$(pwd)")

        return textwrap.dedent(
            f"""\
            {make} {target} {env}
            """
        )

    def get_make_args(
        self,
        build: targets.Build,
    ) -> Mapping[str, str | pathlib.Path | None]:
        return {}

    def get_make_env(self, build: targets.Build, wd: str) -> str:
        return ""

    def get_make_target(self, build: targets.Build) -> str:
        return ""

    def get_make_install_args(
        self,
        build: targets.Build,
    ) -> Mapping[str, str | pathlib.Path | None]:
        return {}

    def get_make_install_env(self, build: targets.Build, wd: str) -> str:
        return self.get_make_env(build, wd)

    def get_make_install_target(self, build: targets.Build) -> str:
        return "install"

    def sh_get_make_install_destdir(
        self,
        build: targets.Build,
        wd: str,
    ) -> str:
        instdir = build.get_install_dir(self, relative_to="pkgbuild")
        return f"{wd}/{shlex.quote(str(instdir))}"

    def get_build_install_script(self, build: targets.Build) -> str:
        script = super().get_build_install_script(build)
        install_target = self.get_make_install_target(build)

        if install_target:
            args = self.get_make_install_args(build)
            make = build.sh_get_command("make", args=args)
            env = self.get_make_install_env(build, "$(pwd)")
            destdir = self.sh_get_make_install_destdir(build, "$(pwd)")
            script += "\n" + textwrap.dedent(
                f"""\
                {make} {env} DESTDIR={destdir} "{install_target}"
                """
            )

        return script

    def get_binary_output_dir(self) -> pathlib.Path:
        """Return path relative to the build dir where the result binaries are"""
        return pathlib.Path("bin")


class BundledCPackage(BuildSystemMakePackage):
    def sh_configure(
        self,
        build: targets.Build,
        path: str | pathlib.Path,
        args: Mapping[str, str | pathlib.Path | None],
    ) -> str:
        conf_args = dict(args)
        build.sh_append_run_time_ldflags(conf_args, self)
        if "--prefix" not in args:
            conf_args["--prefix"] = str(build.get_full_install_prefix())

        conf_args = build.sh_append_global_flags(conf_args)
        return build.sh_format_command(path, conf_args, force_args_eq=True)

    def get_shlib_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return [build.get_full_install_prefix() / "lib"]

    def get_include_paths(self, build: targets.Build) -> list[pathlib.Path]:
        return [build.get_full_install_prefix() / "include"]

    def configure_dependency(
        self,
        build: targets.Build,
        configure_flags: dict[str, str | pathlib.Path | None],
        depname: str,
        var_prefix: str,
    ) -> None:
        pkg = build.get_package(depname)
        if build.is_bundled(pkg):
            root = build.get_install_dir(pkg, relative_to="pkgbuild")
            path = root / build.get_full_install_prefix().relative_to("/")
            rel_path = f'$(pwd)/"{path}"'

            dep_ldflags = build.sh_get_bundled_shlib_ldflags(
                pkg, relative_to="pkgbuild"
            )

            if var_prefix:
                build.sh_append_quoted_flags(
                    configure_flags,
                    f"{var_prefix}_CFLAGS",
                    [f"-I{rel_path}/include/"],
                )
                build.sh_append_quoted_flags(
                    configure_flags,
                    f"{var_prefix}_LIBS",
                    dep_ldflags,
                )
            else:
                build.sh_append_quoted_flags(
                    configure_flags,
                    "CFLAGS",
                    [f"-I{rel_path}/include/"],
                )
                build.sh_append_quoted_ldflags(
                    configure_flags,
                    dep_ldflags,
                )

            ldflags = [f"-L{rel_path}/lib/"]

            if platform.system() == "Darwin":
                # In case ./configure tries to compile and test a program
                # and it fails because dependency is not yet installed
                # at its install_name location.
                configure_flags["DYLD_FALLBACK_LIBRARY_PATH"] = root
            else:
                ldflags.append(f"-Wl,-rpath-link,{rel_path}/lib")

            build.sh_append_quoted_ldflags(configure_flags, ldflags)

        elif build.is_stdlib(pkg):
            configure_flags[f"{var_prefix}_CFLAGS"] = (
                f"-D_{var_prefix}_IS_SYSLIB"
            )
            std_ldflags = []
            for shlib in pkg.get_shlibs(build):
                std_ldflags.append(f"-l{shlib}")
            configure_flags[f"{var_prefix}_LIBS"] = build.sh_join_flags(
                std_ldflags
            )

    def get_configure_script(self, build: targets.Build) -> str:
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        configure = sdir / "configure"
        return self.sh_configure(build, configure, {})

    def get_build_install_script(self, build: targets.Build) -> str:
        script = super().get_build_install_script(build)
        install_target = self.get_make_install_target(build)

        if install_target:
            destdir = self.sh_get_make_install_destdir(build, "$(pwd)")
            libdir = build.get_install_path("lib")
            script += "\n" + textwrap.dedent(
                f"""\
                _d={destdir}
                find "$_d" -name '*.la' -exec sed -i -r -e \
                    "s|{libdir}|${{_d}}{libdir}|g" {{}} \;
                """
            )

        return script


class BundledCMesonPackage(BundledCPackage):
    def sh_configure(
        self,
        build: targets.Build,
        path: str | pathlib.Path,
        args: Mapping[str, str | pathlib.Path | None],
    ) -> str:
        conf_args = dict(args)
        if "--prefix" not in args:
            conf_args["--prefix"] = str(build.get_full_install_prefix())
        if "--sysconfdir" not in args:
            conf_args["--sysconfdir"] = build.get_install_path("sysconf")
        # if "--datarootdir" not in args:
        #     conf_args["--datarootdir"] = build.get_install_path("data")
        if "--bindir" not in args:
            conf_args["--bindir"] = build.get_install_path("bin")
        if "--sbindir" not in args:
            conf_args["--sbindir"] = build.get_install_path("bin")
        if "--libdir" not in args:
            conf_args["--libdir"] = build.get_install_path("lib")
        if "--includedir" not in args:
            conf_args["--includedir"] = build.get_install_path("include")
        env_args: dict[str, str | pathlib.Path | None] = {}
        build.sh_append_run_time_ldflags(env_args, self)
        env_args = build.sh_append_global_flags(env_args)
        conf = build.sh_format_command(path, conf_args, force_args_eq=True)
        env = build.sh_format_command("env", env_args, force_args_eq=True)
        return f"{env} {conf}"

    def get_configure_script(self, build: targets.Build) -> str:
        sdir = build.get_source_dir(self, relative_to="pkgbuild")
        meson = build.sh_get_command("meson")
        configure_flags = {
            "setup": None,
            str(sdir): None,
            "build": None,
            "-Ddefault_library": "shared",
            "-Ddistro_install": "true",
            "-Dwith_INIReader": "true",
        }
        return self.sh_configure(build, meson, configure_flags)

    def get_configure_target(self, build: targets.Build) -> str:
        return self.name

    def get_build_script(self, build: targets.Build) -> str:
        meson = build.sh_get_command("meson")
        env = self.get_make_env(build, "$(pwd)")

        return textwrap.dedent(
            f"""\
            {meson} compile -C build
            """
        )

    def get_build_install_script(self, build: targets.Build) -> str:
        script = BundledPackage.get_build_install_script(self, build)
        meson = build.sh_get_command("meson")
        env = self.get_make_install_env(build, "$(pwd)")
        destdir = self.sh_get_make_install_destdir(build, "$(pwd)")
        script += "\n" + textwrap.dedent(
            f"""\
            {meson} install -C build --destdir={destdir} --no-rebuild
            """
        )

        return script


_semver_phase_spelling_map = {
    poetry_pep440_segments.RELEASE_PHASE_ID_ALPHA: "alpha",
    poetry_pep440_segments.RELEASE_PHASE_ID_BETA: "beta",
}


def semver_pre_tag(version: poetry_pep440.PEP440Version) -> str:
    pre = version.pre
    if pre is not None:
        return _semver_phase_spelling_map.get(pre.phase, pre.phase)
    else:
        return ""


def pep440_to_semver(ver: poetry_version.Version) -> str:
    version_string = ver.release.to_string()

    pre = []

    if ver.pre:
        pre.append(f"{semver_pre_tag(ver)}.{ver.pre.number}")

    if ver.post:
        pre.append(f"{ver.post.phase}.{ver.post.number}")

    if ver.dev:
        pre.append(f"{ver.dev.phase}.{ver.dev.number}")

    if pre:
        version_string = f"{version_string}-{'.'.join(pre)}"

    if ver.local:
        assert isinstance(ver.local, tuple)
        version_string += "+" + ".".join(map(str, ver.local))

    return version_string.lower()
