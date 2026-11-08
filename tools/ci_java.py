"""Select the hosted runner's installed JDK 17 without a release-asset download."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


def configure(environment, *, run=subprocess.run):
    """Validate both tools before publishing a Java home to subsequent Actions steps."""
    home = Path(environment["JAVA_HOME_17_X64"]).resolve(strict=True)
    java, javac = home / "bin/java", home / "bin/javac"
    if not java.is_file() or not javac.is_file():
        raise ValueError("The runner's Java 17 home must contain java and javac")
    runtime = run([str(java), "-XshowSettings:properties", "-version"], capture_output=True, text=True, check=True)
    compiler = run([str(javac), "-version"], capture_output=True, text=True, check=True)
    runtime_text = runtime.stdout + runtime.stderr
    compiler_text = compiler.stdout + compiler.stderr
    if not re.search(r"^\s*java\.specification\.version\s*=\s*17\s*$", runtime_text, re.MULTILINE):
        raise ValueError("The runner's java must report Java specification version 17")
    if not re.search(r"^javac 17(?:[. +\-]|$)", compiler_text.strip()):
        raise ValueError("The runner's javac must report version 17")
    # Only validated tools enter the environment. Keep the actual patch version in the job evidence.
    with Path(environment["GITHUB_ENV"]).open("a") as stream:
        stream.write(f"JAVA_HOME={home}\n")
    with Path(environment["GITHUB_PATH"]).open("a") as stream:
        stream.write(f"{home / 'bin'}\n")
    print(f"Using installed JDK: {home}")
    print(runtime_text.strip())
    print(compiler_text.strip())
    return home


if __name__ == "__main__":
    configure(os.environ)
