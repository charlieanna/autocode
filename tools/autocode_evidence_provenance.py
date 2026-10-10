"""Launch-fixed disclosure; executable and model names do not attest real inference."""
from copy import deepcopy

KINDS = ("fake", "live", "mixed", "unknown")


def configured(settings, requested=None):
    saved = settings.get("evidence_provenance")
    if saved:
        if requested is not None and requested != saved.get("kind"):
            raise ValueError("Evidence provenance is fixed at launch; a resume cannot change it")
        return deepcopy(saved)
    if settings and requested not in (None, "unknown"):
        raise ValueError("Evidence provenance is fixed at launch; a legacy resume remains unknown")
    return {"kind": requested or "unknown",
            "basis": "caller_declared" if requested else "unavailable",
            "declaration": "--evidence-provenance" if requested else
                           "No declaration; provider/model names cannot distinguish a fixture from live models"}


def projection(settings):
    return deepcopy(settings.get("evidence_provenance") or configured({}))


def combined(children):
    kinds = {row.get("kind", "unknown") for row in children}
    kind = ("unknown" if not kinds or "unknown" in kinds else
            next(iter(kinds)) if len(kinds) == 1 else "mixed")
    return {"kind": kind, "basis": "child_reports", "declaration":
            "Combined from the public child reports; declarations are disclosures, not attestations"}
