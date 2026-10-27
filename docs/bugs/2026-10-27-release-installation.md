# Published distribution and install verification (#2048)

Verified 2026-10-27. This records distribution smoke checks, not current-master
provider delivery or a reliability qualification. No package was republished,
version tag moved, engine/account configured or model called.

The fresh [PyPI version API](https://pypi.org/pypi/autocode-supervisor/0.8.2/json)
returns 0.8.2, and its project API lists 0.8.0, 0.8.1 and 0.8.2. The published
release tag is `86fdb7920f46a935405840f78c8c3bff0ed3422b`, dated 2026-10-21.
Current master is newer despite retaining the same package version. A cached
index response is not evidence that the later releases are absent.

The published wheel's 885 packaged tracked `tools/` files also match the
release tag byte-for-byte. This comparison covers those files, not newer master.

Both official archives were downloaded and their SHA-256 verified against PyPI:

| Artifact | SHA-256 |
| --- | --- |
| `autocode_supervisor-0.8.2-py3-none-any.whl` | `168951a0eb029943d0c1c8589b4c3edd907ed3635954e39072bb8f7f9101f112` |
| `autocode_supervisor-0.8.2.tar.gz` | `347743fe1695b4e83bd5d3c3ab4373379dbb8b7d36d7d0395af0918d22495a53` |

## Installation checks

- **PyPI:** `pipx install --python /opt/homebrew/bin/python3.13
  autocode-supervisor==0.8.2` succeeded on macOS in a fresh, isolated pipx home,
  bin/man directories and virtual environment, with isolated HOME/cache/config
  and the public PyPI index. The resulting `autocode --version` returned
  `autocode 0.8.2 (commit unknown)`. The offline `autocode demo` exited zero.
  No account credentials or existing AutoCode virtual environment were supplied.
  This is clean application-environment evidence, not a fresh operating-system
  installation or a real-model delivery result.
- **Homebrew:** the public [0.8.2 formula](https://github.com/charlieanna/homebrew-tap/blob/65238eb50ff6e3b639c30c149722e247ea58fec9/Formula/autocode.rb)
  pins the verified source hash and psutil 7.2.2. Its Git blob is
  `5523874458a26b76b82f11dfa26e2f71f917d549`. The installed formula bytes match
  that blob, and its install receipt identifies that tap commit and 0.8.2.
  `HOMEBREW_NO_AUTO_UPDATE=1 brew test --force charlieanna/tap/autocode` passed,
  exercising the installed release's `--version`. `--force` tests an unlinked
  formula; it does not relink it or disable its test. The original unlinked
  `brew test` refusal is retained locally and is not counted as a pass. No new
  Homebrew install, dependency update or active-command/PATH change was made.

README and the install guide must therefore lead with the published package/tap,
keep Git/master installation under Advanced, and explain that a tagged release
does not include later master fixes. Installation and an offline demo do not
establish account access, kernel containment or real-model quality. Existing
macOS portable-supervision limitations and stronger supported-Linux guarantees
remain unchanged.

The next release's cadence and evidence gate remain an explicit owner decision
in #2048; the proposed two-week schedule is not an enabled automation. A future
release of newer master needs a new version/tag and its own agreed evidence,
not another upload under 0.8.2.
