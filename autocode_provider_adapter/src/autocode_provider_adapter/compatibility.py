"""Versioned, structural compatibility checks for an untouched upstream tree."""

from __future__ import annotations

from dataclasses import dataclass
import ast
import hashlib
import json
from pathlib import Path
from importlib.resources import files
import re
from typing import Any


class CompatibilityError(RuntimeError):
    """The upstream source no longer provides an adapter integration seam."""


@dataclass(frozen=True)
class StructuralProbe:
    path: str
    contains: str
    parameters: tuple[str, ...] | None = None


@dataclass(frozen=True)
class TrustedGoCodeIdentity:
    """A reviewed GoCode/Codex/Claude identity tuple; never learned at runtime."""

    version: str
    gocode_sha256: str
    gocode_real_path: str
    gocode_real_sha256: str
    codex_path: str
    codex_sha256: str
    shim_path_regex: str
    shim_sha256: str


@dataclass(frozen=True)
class CompatibilityManifest:
    """The M1 trust anchor for known runner and dashboard source seams."""

    version: int
    probes: tuple[StructuralProbe, ...]
    gocode_identities: tuple[TrustedGoCodeIdentity, ...] = ()

    @classmethod
    def from_dict(cls, document: dict[str, Any]) -> "CompatibilityManifest":
        if document.get("version") != 1:
            raise CompatibilityError("unsupported compatibility manifest version; expected 1")
        raw_probes = document.get("probes")
        if not isinstance(raw_probes, list) or not raw_probes:
            raise CompatibilityError("compatibility manifest requires at least one structural probe")
        probes: list[StructuralProbe] = []
        for probe in raw_probes:
            if not isinstance(probe, dict) or not isinstance(probe.get("path"), str) or not isinstance(probe.get("contains"), str):
                raise CompatibilityError("each structural probe requires string path and contains fields")
            relative = Path(probe["path"])
            if relative.is_absolute() or ".." in relative.parts or not probe["contains"]:
                raise CompatibilityError("structural probes must use a non-empty relative path and marker")
            raw_parameters = probe.get("parameters")
            if (raw_parameters is not None and
                    (not isinstance(raw_parameters, list) or
                     any(not isinstance(value, str) or not value for value in raw_parameters))):
                raise CompatibilityError("structural probe parameters must be a list of names")
            probes.append(StructuralProbe(
                probe["path"], probe["contains"],
                tuple(raw_parameters) if raw_parameters is not None else None,
            ))
        raw_identities = document.get("gocode", [])
        if not isinstance(raw_identities, list):
            raise CompatibilityError("gocode compatibility identities must be a list")
        identities: list[TrustedGoCodeIdentity] = []
        for item in raw_identities:
            if not isinstance(item, dict):
                raise CompatibilityError("each gocode compatibility identity must be an object")
            fields = ("version", "gocode_sha256", "codex_path", "codex_sha256", "shim_path_regex", "shim_sha256")
            if any(not isinstance(item.get(field), str) or not item[field] for field in fields):
                raise CompatibilityError("each gocode identity requires version, canonical Codex path, digests, and shim path rule")
            codex_path = Path(item["codex_path"])
            if not codex_path.is_absolute() or str(codex_path) != item["codex_path"]:
                raise CompatibilityError("gocode identity Codex path must be canonical and absolute")
            if any(not re.fullmatch(r"[0-9a-f]{64}", item[field]) for field in
                   ("gocode_sha256", "codex_sha256", "shim_sha256")):
                raise CompatibilityError("gocode identity digests must be lowercase SHA-256 values")
            try:
                re.compile(item["shim_path_regex"])
            except re.error as error:
                raise CompatibilityError("gocode shim path rule is not a valid regular expression") from error
            real_path = item.get("gocode_real_path", item.get("gocode_path"))
            real_sha = item.get("gocode_real_sha256", item["gocode_sha256"])
            if real_path is not None and (not isinstance(real_path, str) or not Path(real_path).is_absolute()
                                          or not re.fullmatch(r"[0-9a-f]{64}", real_sha or "")):
                raise CompatibilityError("gocode real executable identity must use an absolute path and SHA-256")
            identities.append(TrustedGoCodeIdentity(
                **{field: item[field] for field in fields},
                gocode_real_path=real_path or "", gocode_real_sha256=real_sha,
            ))
        if len(identities) > 1:
            raise CompatibilityError("compatibility manifest may not enroll multiple GoCode identities")
        return cls(version=1, probes=tuple(probes), gocode_identities=tuple(identities))

    @classmethod
    def from_file(cls, path: Path) -> "CompatibilityManifest":
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise CompatibilityError(f"cannot read compatibility manifest {path}: {error}") from error
        if not isinstance(value, dict):
            raise CompatibilityError("compatibility manifest must be a JSON object")
        return cls.from_dict(value)

    @classmethod
    def default(cls) -> "CompatibilityManifest":
        """Load the reviewed M1 seam manifest shipped by this adapter."""
        value = json.loads(
            files("autocode_provider_adapter").joinpath("compatibility-manifest-v1.json").read_text(encoding="utf-8")
        )
        return cls.from_dict(value)

    @property
    def identity(self) -> str:
        canonical = {
            "gocode": [
                {"codex_path": identity.codex_path, "codex_sha256": identity.codex_sha256, "gocode_sha256": identity.gocode_sha256,
                 "gocode_real_path": identity.gocode_real_path, "gocode_real_sha256": identity.gocode_real_sha256,
                 "shim_path_regex": identity.shim_path_regex, "shim_sha256": identity.shim_sha256,
                 "version": identity.version}
                for identity in self.gocode_identities
            ],
            "probes": [
                {**{"contains": probe.contains, "path": probe.path},
                 **({"parameters": list(probe.parameters)} if probe.parameters is not None else {})}
                for probe in self.probes
            ],
            "version": self.version,
        }
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def verify(self, checkout: Path) -> None:
        for probe in self.probes:
            candidate = checkout / probe.path
            if not candidate.is_file():
                raise CompatibilityError(
                    f"upstream is incompatible: required seam {probe.path!r} is missing "
                    f"(manifest {self.identity})"
                )
            try:
                text = candidate.read_text(encoding="utf-8")
            except UnicodeDecodeError as error:
                raise CompatibilityError(f"upstream is incompatible: cannot inspect seam {probe.path!r}") from error
            definition = re.fullmatch(r"(def|class)\s+([A-Za-z_]\w*)\(?", probe.contains)
            if definition:
                try:
                    tree = ast.parse(text, filename=str(candidate))
                except SyntaxError as error:
                    raise CompatibilityError(f"upstream is incompatible: seam {probe.path!r} is not valid Python") from error
                kind, name = definition.groups()
                expected = ast.FunctionDef if kind == "def" else ast.ClassDef
                matched = next((node for node in tree.body
                                if isinstance(node, expected) and node.name == name), None)
                if matched is None:
                    raise CompatibilityError(
                        f"upstream is incompatible: seam {probe.path!r} no longer has definition {name!r} "
                        f"(manifest {self.identity})"
                    )
                if probe.parameters is not None and isinstance(matched, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    observed = tuple(
                        argument.arg for argument in
                        (*matched.args.posonlyargs, *matched.args.args, *matched.args.kwonlyargs)
                    )
                    if observed != probe.parameters:
                        raise CompatibilityError(
                            f"upstream is incompatible: signature for {name!r} in {probe.path!r} changed "
                            f"(manifest {self.identity})"
                        )
            elif probe.contains not in text:
                raise CompatibilityError(
                    f"upstream is incompatible: seam {probe.path!r} no longer contains "
                    f"{probe.contains!r} (manifest {self.identity})"
                )

    def match_gocode_identity(
        self, *, version: str, gocode_sha256: str, gocode_real_path: str, gocode_real_sha256: str,
        codex_path: str, codex_sha256: str, shim_path: str, shim_sha256: str
    ) -> TrustedGoCodeIdentity:
        for identity in self.gocode_identities:
            if (identity.version == version and identity.gocode_sha256 == gocode_sha256
                    and identity.gocode_real_path == gocode_real_path and identity.gocode_real_sha256 == gocode_real_sha256
                    and identity.codex_path == codex_path and identity.codex_sha256 == codex_sha256 and identity.shim_sha256 == shim_sha256
                    and re.fullmatch(identity.shim_path_regex, shim_path)):
                return identity
        raise CompatibilityError(
            "untrusted GoCode transport identity or digest; install a reviewed compatibility manifest rather than enrolling it"
        )
