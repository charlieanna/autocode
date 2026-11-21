"""Transport a source-read refusal through an authenticated, fully collected worker.

This negative result never supplies a project check, accepts a report, or settles
unknown command custody. The existing source-recovery owner decides continuation.
"""

import re
from contextlib import contextmanager
from pathlib import Path

try:
    from . import autocode_process as processes
    from . import autocode_util as util
    from . import autocode_verification_recovery as recovery
except ImportError:
    import autocode_process as processes
    import autocode_util as util
    import autocode_verification_recovery as recovery

PUBLICATION = "autocode-source-refusal-sha256="


@contextmanager
def capture(answer):
    try:
        yield
    except util.Paused as error:
        if not util.source_unavailable(error) or answer["operation"] not in ("prepare", "execute"):
            raise
        answer["source_refusal"] = {"status": error.status, "reason": util.redact(str(error))[:4000]}
        if isinstance(error, util.PinnedSourceLinkRefusal):
            answer["source_refusal"]["link_policy"] = error.policy


def rethrow(
    answer, receipt, *, result_sha, out, workspace, admission, reference, request, request_sha, command, published
):
    """Raise the original domain pause only after authenticating every owned check."""
    try:
        refused = answer["source_refusal"]
        if (
            set(answer) != {"operation", "nonce", "request_sha256", "source_refusal"}
            or answer["operation"] not in ("prepare", "execute")
            or not isinstance(refused, dict)
            or set(refused) not in ({"status", "reason"}, {"status", "reason", "link_policy"})
            or ("link_policy" in refused and refused["link_policy"] not in ("strict", "protected"))
            or refused["status"] != "PAUSED_SOURCE_UNAVAILABLE"
            or not isinstance(refused["reason"], str)
            or not 0 < len(refused["reason"]) <= 4000
            or not recovery.collected_command(receipt, out)
            or receipt.get("command") != command
            or receipt.get("supervision", {}).get("owner") != admission["owner"]
            or util.file_hash(admission["worker"]) != admission["worker_sha256"]
            or util.file_hash(request) != request_sha
            or util.file_hash(reference["path"]) != reference["sha256"]
            or not published(receipt, result_sha, out, workspace, prefix=PUBLICATION)
        ):
            recovery.hold("source refusal is not bound to its closed worker and exact inputs")
        outer = recovery.receipts.load(receipt["supervision"], root=out, pin=receipt["supervision_sha256"])
        if not outer:
            recovery.hold("source refusal has no collected outer command")
        parent = Path(receipt["supervision"]["receipt"]).parent
        store = recovery.owned_path(parent.parent, out, directory=True)
        for child in store.iterdir():
            if child == parent:
                continue
            if not re.fullmatch("[0-9a-f]{32}", child.name):
                recovery.hold("source refusal retains an unknown command inventory")
            recovery.owned_path(child, out, directory=True)
            inner, _ = recovery.read_regular(child / "result.json", out)
            if Path(inner["supervision"]["receipt"]).parent != child or not (
                recovery.collected_command(inner, out) or recovery.settled_timeout(inner, out)
            ):
                recovery.hold("source refusal retains uncollected inner command custody")
            bound = recovery.bind_command(out, {**inner, "processes": []})
            if processes.identity(bound["metadata"]["owner"]) not in [
                processes.identity(row) for row in outer["processes"]
            ]:
                recovery.hold("source refusal inner owner is outside the original worker")
    except (OSError, ValueError, KeyError, TypeError) as error:
        recovery.hold("source refusal evidence is unavailable: " + str(error))
    if "link_policy" in refused:
        raise util.PinnedSourceLinkRefusal(refused["status"], refused["reason"], policy=refused["link_policy"])
    raise util.Paused(refused["status"], refused["reason"])
