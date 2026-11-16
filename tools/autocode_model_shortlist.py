"""The free shortlist of models to measure for a stage (#3630 step 1).

An account can offer hundreds of models (a gateway lists about 500), and measuring each one
would cost thousands of model calls. Before the person chooses what to measure, this module
narrows the candidates using only what discovery already read from the tool's own listing
(``autocode_route_discovery``): no model call, no network, no guess about quality.

The list it returns is a suggestion. Stage models shows it ticked, the person adds or removes
models, and only the models they keep are measured. Every candidate stays in the result with the
reason it is or is not suggested, so nothing is hidden from the person.

What it drops, each for a stated reason:

- a model the listing says cannot call tools, or cannot reason, or whose context is smaller than
  the stage's prompt needs (``MIN_CONTEXT``);
- a narrow variant (an embedding, speech, image or vision model, or a mini, nano or lite cut),
  named by the listing's own model id (``NARROW``), since no listing field states it;
- a gateway's router (``auto``, ``auto-frontier``), which answers with a model it picks per
  request, so no measurement or independence check can say which family answered
  (``autocode_model_identity.router``);
- a second route to a model already listed (one model reached two ways is one model,
  ``autocode_model_identity.identity``): the route a subscription bills is kept;
- an older version of a model line already listed (``gpt-6-sol`` when ``gpt-6.1-sol`` is there).

Of what remains it suggests the newest ``PER_VENDOR`` lines from each family
(``autocode_model_identity.FAMILIES``, else the model's vendor segment), then at most
``LIMIT`` in all, subscription routes first. A field the listing leaves unknown never drops a
model: unknown is not "no".

Pure: candidates in, rows out. Candidate dicts are the ones discovery produces (``model``,
``billing``, ``levels``, ``probe``, ``metadata`` with ``toolcall``, ``reasoning``, ``context``
and ``release_date``).
"""

from __future__ import annotations

import re

try:
    from . import autocode_model_identity as model_identity
except ImportError:
    import autocode_model_identity as model_identity

PER_VENDOR = 3
LIMIT = 15
# The Requirements prompt with a repository summary runs to tens of thousands of tokens; a model
# that cannot hold it cannot do the stage, whatever it would score.
MIN_CONTEXT = 64_000
# Model-id words that name a narrow cut of a model. Only the listing's own id says this; there is
# no listing field for it. A word must stand alone between separators, so "gemini" is not "mini".
NARROW = re.compile(
    r"(?:^|[-_.:/])(?:mini|nano|lite|tiny|embed|embedding|embeddings|tts|audio|speech|transcribe|"
    r"whisper|image|images|vision|vl|realtime|moderation|search|rerank)(?:$|[-_.:/])"
)
BILLING_ORDER = {"subscription": 0, None: 1, "pay per token": 2}
# One version token: "6.1", or the hyphenated "5-5" of "claude-opus-5-5". A trailing eight-digit
# snapshot date ("-20250929") is not part of the version; it dates the release instead.
_VERSION = re.compile(r"\d+(?:[.-]\d+)*")
_SNAPSHOT = re.compile(r"[-_@](\d{4})(\d{2})(\d{2})$")
KNOWN_FAMILIES = model_identity.FAMILIES


def shortlist(candidates, *, per_vendor=PER_VENDOR, limit=LIMIT, min_context=MIN_CONTEXT):
    """Every candidate as a row ``{"model", "vendor", "suggested", "reason", "candidate"}``.

    Suggested rows come first, best first, then the rest in model order. ``reason`` says in a
    person's words why a row is or is not suggested.
    """
    rows = {}
    kept = []
    for candidate in _valid(candidates):
        problem = _unfit(candidate, min_context)
        if problem:
            rows[candidate["model"]] = _row(candidate, False, problem)
        else:
            kept.append(candidate)
    kept = _one_route_each(kept, rows)
    newest = _newest_per_line(kept, rows)
    picked = []
    for vendor in sorted({_vendor(c["model"]) for c in newest}):
        lines = sorted((c for c in newest if _vendor(c["model"]) == vendor), key=_newest_first)
        picked.extend(lines[:per_vendor])
        for candidate in lines[per_vendor:]:
            rows[candidate["model"]] = _row(
                candidate, False, f"{vendor} already has {per_vendor} newer models on the list"
            )
    picked.sort(key=_suggestion_order)
    for candidate in picked[limit:]:
        rows[candidate["model"]] = _row(candidate, False, f"the list is capped at {limit} suggestions")
    suggested = [_row(candidate, True, _why(candidate)) for candidate in picked[:limit]]
    rest = sorted(rows.values(), key=lambda row: row["model"])
    return suggested + rest


def suggested_models(candidates, **options):
    """The model ids ``shortlist`` ticks, in its order."""
    return [row["model"] for row in shortlist(candidates, **options) if row["suggested"]]


def _valid(candidates):
    seen = set()
    for candidate in candidates or ():
        model = candidate.get("model") if isinstance(candidate, dict) else None
        if isinstance(model, str) and model and model not in seen:
            seen.add(model)
            yield candidate


def _metadata(candidate):
    metadata = candidate.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _unfit(candidate, min_context):
    """Why the listing itself rules this model out for the stage, or None."""
    metadata = _metadata(candidate)
    if metadata.get("toolcall") is False:
        return "the listing says it cannot call tools"
    levels = candidate.get("levels")
    if metadata.get("reasoning") is False or (isinstance(levels, (list, tuple)) and not levels):
        return "the listing says it has no reasoning levels"
    context = metadata.get("context")
    if type(context) is int and context < min_context:
        return f"its context ({context:,} tokens) is smaller than the stage needs ({min_context:,})"
    if model_identity.router(candidate["model"]):
        return "it is a router that picks a different model per request, so nothing can show which family answered"
    if NARROW.search(_name(candidate["model"])):
        return "its name marks a narrow variant (mini, lite, vision, speech or similar)"
    return None


def _one_route_each(candidates, rows):
    """One candidate per model identity: the best-billed route, the rest recorded as duplicates."""
    best = {}
    for candidate in sorted(candidates, key=_route_order):
        identity = model_identity.identity(candidate["model"])
        if identity in best:
            rows[candidate["model"]] = _row(candidate, False, f"the same model as {best[identity]['model']}")
        else:
            best[identity] = candidate
    return list(best.values())


def _newest_per_line(candidates, rows):
    """The newest version of each model line; older versions recorded as replaced."""
    newest = {}
    for candidate in sorted(candidates, key=_newest_first):
        line = (_vendor(candidate["model"]), _line(candidate["model"]))
        if line in newest:
            rows[candidate["model"]] = _row(candidate, False, f"a newer version is listed: {newest[line]['model']}")
        else:
            newest[line] = candidate
    return list(newest.values())


def _name(model):
    """The model's own name, without its gateway, vendor or snapshot date."""
    return _SNAPSHOT.sub("", _full_name(model))


def _full_name(model):
    return model_identity.identity(model).rsplit("/", 1)[-1]


def _vendor(model):
    """The family the verifier policy knows, else the vendor segment of the model's identity."""
    family = model_identity.family(model)
    if family in KNOWN_FAMILIES:
        return family
    identity = model_identity.identity(model)
    return identity.split("/", 1)[0] if "/" in identity else identity


def _line(model):
    """The model's name with its version numbers taken out: ``gpt-#-sol`` for every Sol."""
    return _VERSION.sub("#", _name(model))


def _version(model):
    """The version numbers in the model's name, padded so ``6`` sorts as ``6.0`` below ``6.1``."""
    parts = [int(part) for match in _VERSION.findall(_name(model)) for part in re.split(r"[.-]", match)]
    return tuple((parts + [0] * 6)[:6])


def _release(candidate):
    """The listing's release date, else the snapshot date in the model's id, else ""."""
    value = _metadata(candidate).get("release_date")
    if isinstance(value, str) and value:
        return value
    snapshot = _SNAPSHOT.search(_full_name(candidate["model"]))
    return "-".join(snapshot.groups()) if snapshot else ""


def _newest_first(candidate):
    """The highest version first, then the newest release (listed, or the id's snapshot date), then the id.

    Version leads because a release date is often missing or only inferred from a snapshot suffix,
    and a dated snapshot of an older version must not outrank the next version.
    """
    release = _release(candidate)
    return (tuple(-part for part in _version(candidate["model"])), release == "", _negate(release), candidate["model"])


def _negate(text):
    # Sorting descending by an ISO date inside an ascending key; padded so a longer date is not newer.
    return tuple(-ord(char) for char in text.ljust(32))


def _route_order(candidate):
    billing = candidate.get("billing") if candidate.get("billing") in BILLING_ORDER else None
    return (BILLING_ORDER[billing], 0 if candidate.get("probe") == "known" else 1, candidate["model"])


def _suggestion_order(candidate):
    return (_route_order(candidate)[0], *_newest_first(candidate))


def _why(candidate):
    billing = candidate.get("billing")
    paid = {"subscription": "billed by a subscription", "pay per token": "billed per token"}.get(
        billing, "billing unknown"
    )
    return f"newest {_vendor(candidate['model'])} model on the list, {paid}"


def _row(candidate, suggested, reason):
    return {
        "model": candidate["model"],
        "vendor": _vendor(candidate["model"]),
        "suggested": suggested,
        "reason": reason,
        "candidate": candidate,
    }
