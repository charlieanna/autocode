# Changelog

AutoCode Task Chat is published from every commit to AutoCode's `master` branch that changes
the extension. Its version is `MAJOR.MINOR` from `package.json` with the number of commits in
master's history as the patch, so each published version is newer than the last.

The extension is a thin client of the `autocode` CLI, and most of what it shows comes from the
CLI's status view. What changed in each AutoCode release, the extension included, is in
[AutoCode's changelog](https://github.com/charlieanna/autocode/blob/master/CHANGELOG.md).
Install a current CLI (`pipx upgrade autocode-supervisor`) alongside a new extension.
