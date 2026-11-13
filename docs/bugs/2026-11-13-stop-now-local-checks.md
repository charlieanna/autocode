# Stop now waited through local verification

The TODO qualification on source `4342ccc44c92029ca44bfbfc6ed471a823bc24a3`
accepted its fresh public Stop now action at 19:47:35 UTC on 2026-11-13.
The controller stayed running with no active model while regression proof 03
continued admitting mutation commands. Retained public observations show mutant
11 at 19:50:19, mutant 33 at 19:56:53, and mutant 39 at 19:58:43. The fixed
19:58:18 outer deadline was missed; `bound_honored` is false.

The controller eventually paused at 20:02:25.753590, after proof 03 finished at
20:01:19. No subsequent model attempt was advertised. This demonstrates delayed
cancellation through runner checks, not a demonstrated post-stop model launch.
The keeper collected the paused run and exited at 20:03:16, retaining its volume.
The Core bundle manifest is
`674d78c83d0722000f14fbe0c1207e04ee3bf121b7babc5af8884c76a425c8fc`;
the native final handoff SHA-256 is
`55e39f2473cb45c5167a4145d11d4a349c1d9063115539cf3bc1fb5ea043bd47`.
These are observations of the old source, not qualification of the fix.

Issue #3431 already described the same gap. Provider supervision polled the
Stop now inbox, but ordinary local commands waited for their entire timeout,
and mutation checks admitted their next command without observing that request.
The fix captures a run-scoped cancellation callback at command entry, preserves
the original absolute deadline, and unwinds proof fallbacks through the existing
process owner's cleanup. Only authenticated cleanup permits application of the
exact observed intervention. Interrupted output cannot authorize proof; uncertain
cleanup retains ownership and leaves the request unacknowledged.

The focused public CLI regression holds a real original-code baseline and its
descendant on a FIFO, takes the view's named Stop now action, and requires cleanup
before ordinary completion, unchanged approval and model attempts, and a coherent
pause. Separate controls cover mutation unwind, stale and unrelated requests,
boundary pauses, uncertainty, normal completion and timeouts, and the original
held native command without an activity monitor. These offline controls establish
the changed runtime behavior without launching models or changing the old run.
