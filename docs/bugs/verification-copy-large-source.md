# Verification copies with large source inventories

A read-only judging stage creates a standalone source copy before launching
its provider. Staging a large source inventory as individual loose Git objects
can exhaust the existing Git preparation timeout before any model starts.

The copy now primes its own object database with packed, authenticated source
blobs. Regular files are streamed with byte-count, SHA256, mode and physical
identity checks; symlinks contribute their literal copied targets. The pack
imports no commits or refs and never borrows the project's Git object database.

The ordinary `git add -f --all` still applies Git attributes and records modes.
The copy retains its single baseline commit, existing Git preparation timeouts,
original source checks, run-owned manifest and independent clean replay.
Deleted inputs stay absent. Dependency links are still added after Git setup.

This changes preparation of fresh verification copies. Previously stopped
Arena attempts and their failed checks retain their original outcomes.
