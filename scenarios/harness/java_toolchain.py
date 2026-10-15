"""Explicit, checksum-verified Maven/JDK prerequisites for the Java catalog case.

Nothing installs globally or runs a downloaded installer. CI supplies Java 17;
--install-jdk installs a pinned macOS arm64 Temurin archive under the owned root.
The Maven wrapper keeps dependencies under that root, and setup warms them once
so scenario/proof commands can use -o without network access.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
MAVEN = "3.9.9"
MAVEN_URL = f"https://archive.apache.org/dist/maven/maven-3/{MAVEN}/binaries/apache-maven-{MAVEN}-bin.tar.gz"
# Apache's published .sha512 for this exact binary archive.
MAVEN_SHA512 = (
    "a555254d6b53d267965a3404ecb14e53c3827c09c3b94b5678835887ab404556bfaf78d"
    "cfe03ba76fa2508649dca8531c74bca4d5846513522404d48e8c4ac8b"
)
JDK_URL = (
    "https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.16%2B8/"
    "OpenJDK17U-jdk_aarch64_mac_hotspot_17.0.16_8.tar.gz"
)
JDK_SHA256 = "f9845abc8403f1d489402201064e7b9f2c57605d8717b85a95a15d94f882eeb7"


def archive(root: Path, url: str, digest: str, algorithm: str) -> Path:
    target = root / url.rsplit("/", 1)[1]
    if target.is_symlink():
        raise ValueError(f"Refusing redirected archive: {target}")
    if not target.is_file():
        pending = target.with_suffix(target.suffix + ".download")
        if pending.is_symlink():
            raise ValueError(f"Refusing redirected download: {pending}")
        subprocess.run(["curl", "-fLsS", "--retry", "2", "--max-time", "300", "-o", str(pending), url], check=True)
        pending.replace(target)
    with target.open("rb") as stream:
        actual = hashlib.file_digest(stream, algorithm).hexdigest()
    if actual != digest:
        raise ValueError(f"{target.name}: {algorithm} mismatch ({actual})")
    return target


def unpack(source: Path, root: Path) -> None:
    with tarfile.open(source) as packed:
        packed.extractall(root, filter="data")


def prepare(root: Path, *, install_jdk: bool = False) -> dict:
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    maven = root / f"apache-maven-{MAVEN}"
    if not (maven / "bin/mvn").is_file():
        unpack(archive(root, MAVEN_URL, MAVEN_SHA512, "sha512"), root)
    if install_jdk:
        import platform

        if (platform.system(), platform.machine()) != ("Darwin", "arm64"):
            raise ValueError("--install-jdk is pinned for macOS arm64; supply Java 17 on other hosts")
        java = root / "jdk-17.0.16+8/Contents/Home"
        if not (java / "bin/javac").is_file():
            unpack(archive(root, JDK_URL, JDK_SHA256, "sha256"), root)
    else:
        java_home = os.environ.get("JAVA_HOME")
        executable = shutil.which("javac")
        if not java_home and not executable:
            raise ValueError("Supply a Java 17 JDK or use --install-jdk on macOS arm64")
        java = Path(java_home).resolve() if java_home else Path(executable).resolve().parent.parent
    subprocess.run([str(java / "bin/javac"), "-version"], check=True, capture_output=True, text=True, timeout=15)
    bindir = root / "bin"
    bindir.mkdir(exist_ok=True)
    for name in ("java", "javac"):
        target = bindir / name
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            raise ValueError(f"Refusing existing tool wrapper: {target}")
        target.symlink_to(java / "bin" / name)
    wrapper = bindir / "mvn"
    if wrapper.is_symlink():
        raise ValueError("Refusing redirected Maven wrapper")
    wrapper.write_text(
        "#!/bin/sh\nexport JAVA_HOME="
        + shlex.quote(str(java))
        + "\nexec "
        + shlex.quote(str(maven / "bin/mvn"))
        + " "
        + shlex.quote("-Dmaven.repo.local=" + str(root / "repository"))
        + ' "$@"\n'
    )
    wrapper.chmod(0o755)
    seed = REPO / "scenarios/catalog/feature-java-page-count/seed"
    prime = root / "prime-project"
    shutil.copytree(seed, prime, dirs_exist_ok=True)
    environment = {**os.environ, "JAVA_HOME": str(java), "PATH": str(bindir) + os.pathsep + os.environ.get("PATH", "")}
    # Execute the real seed's guard, resolving the pinned compiler, engine and
    # Surefire provider too. dependency:go-offline alone can miss that provider.
    command = [str(wrapper), "-B", "-q", "-Dtest=PageCountTest", "test"]
    result = subprocess.run(command, cwd=prime, env=environment, capture_output=True, text=True, timeout=300)
    (root / "setup.log").write_text(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError(f"Pinned Maven setup exited {result.returncode}; see {root / 'setup.log'}")
    metadata = {
        "bin": str(bindir),
        "java_home": str(java),
        "maven": MAVEN,
        "maven_sha512": MAVEN_SHA512,
        "jdk_sha256": JDK_SHA256 if install_jdk else None,
    }
    (root / "toolchain.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=REPO / ".scenario-runs/junit-java-toolchain")
    parser.add_argument("--install-jdk", action="store_true")
    args = parser.parse_args()
    print(json.dumps(prepare(args.root, install_jdk=args.install_jdk)))


if __name__ == "__main__":
    main()
