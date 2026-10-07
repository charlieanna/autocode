# Verification cleanup receipts the reader rejected (#598)

A supervised `python -m unittest discover -v` records about 8,000 process identities. Indented, that receipt is about 1.4 MiB. Both readers stopped at 1 MiB, so `cleanup_complete` was false for a stopped receipt with no cleanup error, and resume paused again at `PAUSED_VERIFICATION_UNCERTAIN`.

The keeper and the readers now share a 4 MiB ceiling, enough for the 20,000-process inventory. The keeper does not replace a receipt with bytes the reader would reject. A stopped or uncertain receipt that does load, and whose owner, keeper, provider and recorded processes are all gone, is retired on resume. The check runs again. The retirement is not a passing result.
