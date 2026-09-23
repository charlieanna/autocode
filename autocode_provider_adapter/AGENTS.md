# External adapter development rules

Work only in this adapter. Keep official upstream Autocode source, installed
GoCode, global authentication/configuration, remotes, and unrelated migrations
unchanged. Upstream remains authoritative for orchestration, prompts, schemas,
gates, retries, state, dashboard handlers, UI, and storage.

The user requested direct implementation; do not start the historical four-role
Autocode workflow. Develop each changed function test-first and keep coherent
functional commits atomic. Never auto-enroll an unknown executable identity or
persist a managed bearer credential.

The supported macOS launch boundary is a final path/inode/digest/authentication
recheck followed by normal canonical-path execution. Do not reintroduce `/dev/fd`,
script/native snapshots, immutable flags, or claims of atomic binding. Document
the residual same-user replacement interval honestly.
