# Bounded terminal run overview (#1882)

The background attention inventory already used the bounded status-read pool,
but explicit `/runs` called the project and global listings synchronously,
without a timeout. An uncached `/open` could take the same path. Holding either
listing therefore stopped keyboard handling even after the earlier status fix.

Both full-screen discovery and an opened chat now request those public listings
through the shared pool: at most two children, five-second read deadlines,
round-robin admission and cancellation/reaping on close. The command returns
immediately. A completion notice invites an explicit `/runs` inspection, which
freezes the numbered snapshot used by `/open N`. Subsequent arrivals only update
the cache, preserving the choice being typed. Failed refreshes label the retained
snapshot unavailable. Listing does not subscribe to individual run statuses.

## Focused evidence

The new pure public-command regression fails against original client
`229a85d2d91f1f42974f482ca9fc204b109cf7ad` at the synchronous `subprocess.run`.
The corrected reader passes. The focused client/context/polling/attention,
architecture and CI-selection batch passed 122 checks in 13.966 seconds on
macOS. No supervised provider was launched locally.

`tests.test_chat_inventory_cli` drives real curses input and public CLI argument
arrays against a model-free command fixture. It renders 25 inventory entries,
explicitly opens two stable identities, switches a Unicode draft and cursor,
then holds both project and global listing children. In the local 120-column
terminal, the inserted key rendered in 14.1 ms and `/help` in 21.6 ms, while both
children remained held. `/quit` reaped every recorded read process. The whole
case passed in 1.160 seconds. These are individual input samples, below the
proposed 250 ms controlled-host target, not a latency percentile or delivery
benchmark. The CI result must identify its own exact head and Linux host.

The companion `tests.test_chat_inventory` first proves that the 25 listed rows
produce only the selected run's status reads. It then explicitly opens all 25
through `/open`, and uses a fake clock with hanging, malformed and removed-run
responses. Every subscribed identity receives admission with no more than two
active reads; healthy outcomes arrive, faults remain unavailable, and close
reaps all children. Inventory shares that pool. Fairness depends on the current
number of subscribed/read identities, two slots, five-second deadlines and the
keyboard pump; the ten-second background interval is not a freshness deadline.

## Limits and retained failures

The terminal case supplies 25 public command-fixture entries, not 25 actual
persistent core workers. The all-25 subscription case uses controlled time;
neither establishes a wall-clock 25-run freshness SLA or a CPU utilization
percentile. Existing native-host tests remain the evidence for real controllers,
detach, services and SSH. This slice does not close every #1882 or #1886 proof
obligation and does not require a paid model run.

The first local terminal attempt could not capture the sandbox's private tmux
socket. With normal private IPC access, two fixture corrections were needed:
the completed view initially omitted its follow-up input offer, and completed
`/quit` exits zero rather than the paused viewer's two. Their failed outputs
remain in the session; no product gate was weakened.
