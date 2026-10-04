# Legacy worker path collision

The fallback process guard searched command text for both an absolute run path
and its relative suffix. Two isolated projects using `.autocode/runs/fixture`
could therefore block each other, and `fixture-different` also matched `fixture`.
The owned-process marker was unaffected.

Match the complete run path at argument/path boundaries. Preserve conservative
blocking for bare relative legacy paths whose workspace is unknown, and retain
all owned-process, lock and process-inspection guards. This check lives in
`autocode_legacy_process`; support keeps compatible exports.

The regression fails for unrelated controller/provider paths before the repair,
then passes while same-run controller, provider and relative paths remain blocked.
The full 2,999-test canonical gate and 54 fake scenarios passed after the repair.
