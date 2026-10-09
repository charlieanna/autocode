"""Pure policy for bounded live-draft refreshes and their human provenance.

Two bounds limit Planner calls for one conversation's live draft:

* the answer cadence (``schedule``): automatic updates group
  ``ANSWERS_PER_UPDATE`` human turns; other turns are held until the cadence
  or an explicit chat refresh releases them;
* coalescing (``launch``/``release``): at most one Planner draft is in flight.
  Turns that arrive while one is running are held as one coalesced intent, and
  when it finishes (or fails) at most one new draft launches, bound to the
  newest requirements revision so it covers every turn that arrived meanwhile.

The store observes which drafts are in flight (a worker queued or running in
this process, or holding its delivery lease in any process); these functions
only decide.
"""
from datetime import datetime, timezone

ANSWERS_PER_UPDATE = 3
# A running draft whose dispatch record has not changed for this long no longer
# blocks an explicit update. The Planner provider call has no timeout, so a hung
# call would otherwise hold back every later update until a dashboard restart.
STALLED_AFTER_SECONDS = 600

DISPATCH, COALESCE, HOLD = 'dispatch', 'coalesce', 'hold'
# cadence_reason / freshness.reason of a turn held behind a running draft, and
# of the one draft later released for the newest revision.
COALESCED = 'coalesced_behind_draft_in_flight'
COALESCED_RELEASE = 'coalesced_update'


def _clock():
    return datetime.now(timezone.utc)


def held(dispatch):
    return isinstance(dispatch, dict) and dispatch.get('cadence_hold') is True


def coalesced(dispatch):
    return held(dispatch) and dispatch.get('cadence_reason') == COALESCED


def schedule(doc, revision):
    # Legacy intentions were already authorized for dispatch. Count them as
    # scheduled; never rewrite or relaunch a historical delivery receipt.
    prior = [row.get('requirements_revision', 0)
             for row in doc.get('_planner_dispatches', {}).values()
             if isinstance(row, dict) and not held(row)
             and type(row.get('requirements_revision')) is int]
    return not prior or revision - max(prior) >= ANSWERS_PER_UPDATE


def owed(doc):
    """True when a coalesced update is still waiting but nothing launched after it."""
    rows = [row for row in doc.get('_planner_dispatches', {}).values() if isinstance(row, dict)]
    launched = max((row['requirements_revision'] for row in rows
                    if not held(row) and type(row.get('requirements_revision')) is int), default=0)
    return any(coalesced(row) and row.get('state') == 'SAVED'
               and type(row.get('requirements_revision')) is int and row['requirements_revision'] > launched
               for row in rows)


def stalled(dispatch, now=None):
    """True when a draft's dispatch record has not changed for ``STALLED_AFTER_SECONDS``.

    Every delivery step (prepared, process started, provider identified,
    result captured) rewrites ``updated_at``, so this is the time since the
    draft last showed progress. An unreadable record never counts as stalled.
    """
    try:
        at = datetime.fromisoformat(dispatch['updated_at'])
    except (TypeError, KeyError, ValueError):
        return False
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return ((now or _clock()) - at).total_seconds() >= STALLED_AFTER_SECONDS


def blocking(doc, in_flight, now=None):
    """The in-flight turns that still block an explicit update: those not stalled."""
    records = doc.get('_planner_dispatches', {})
    return {turn for turn in in_flight if not stalled(records.get(turn), now)}


def launch(doc, revision, in_flight, *, explicit=False, now=None):
    """Decide what the Planner draft for ``revision`` does now.

    ``in_flight`` is the set of logical turns whose Planner draft is queued or
    running for this conversation right now; ``doc`` holds the earlier turns'
    dispatch records. The result is one of:

    * ``COALESCE`` while ``in_flight`` is non-empty: the draft is saved as a
      held intent and does not dispatch. ``release`` later launches at most one
      draft for the newest revision, which covers this turn and every other
      turn held meanwhile. This overrides the cadence: the running draft's
      result will be discarded as stale, so the follow-up replaces it rather
      than adding one. An explicit update coalesces too, unless every draft
      in flight has stalled (``stalled``): then it dispatches, and the stalled
      draft's result, if it ever arrives, is discarded as stale.
    * ``DISPATCH`` when nothing (that is not stalled) is in flight and the human
      explicitly asked for the update, the answer cadence schedules one, or a
      coalesced update is still owed (the draft it waited for ended just
      before this turn, so this turn's draft is that one follow-up).
    * ``HOLD`` otherwise: the answer is batched until the cadence or an
      explicit chat refresh releases it.
    """
    if in_flight and (not explicit or blocking(doc, in_flight, now)):
        return COALESCE
    return DISPATCH if explicit or owed(doc) or schedule(doc, revision) else HOLD


def in_flight_candidates(doc):
    """Logical turns whose Planner draft may still have a running worker.

    Held intents never launched; committed, stale-discarded and rejected
    drafts are settled. The store checks each candidate's worker lease.
    """
    return [turn for turn, row in doc.get('_planner_dispatches', {}).items()
            if isinstance(row, dict) and not held(row) and row.get('state') != 'REPLY_COMMITTED'
            and not row.get('stale_result_discarded') and not row.get('structured_rejected')]


def release(doc, in_flight):
    """Return the logical turn whose coalesced draft should launch now, or None.

    Called after a Planner draft finishes or fails and after restart recovery.
    Only the newest requirements revision's turn can be released, so a single
    draft covers every coalesced turn; older coalesced intents stay held and
    superseded. Nothing launches while another draft is in flight, after the
    conversation is attached or archived, or when the newest turn was batched
    by the cadence rather than coalesced (its explicit refresh stays available).
    """
    revisions = doc.get('requirements', {}).get('revisions') or []
    if in_flight or not revisions or doc.get('attachment') or doc.get('archived_at'):
        return None
    turn = (revisions[-1].get('source') or {}).get('logical_turn_id')
    dispatch = doc.get('_planner_dispatches', {}).get(turn)
    if (coalesced(dispatch) and dispatch.get('state') == 'SAVED'
            and dispatch.get('requirements_revision') == revisions[-1].get('revision')):
        return turn
    return None


def sources(doc, after=0):
    messages = {row['id']: row for row in doc.get('messages', []) if row.get('role') == 'user'}
    result = []
    for revision in doc.get('requirements', {}).get('revisions', []):
        if revision.get('revision', 0) <= after:
            continue
        source = revision.get('source') or {}
        message = messages.get(source.get('message_id'))
        if message:
            result.append({'message_id': message['id'], 'logical_turn_id': message.get('logical_turn_id'),
                           'requirements_revision': revision['revision'],
                           'excerpt': ' '.join(message.get('text', '').split())[:240]})
    return result


def refreshable(doc):
    """True when the newest turn's draft is a held intent a human may launch now.

    Whether it may launch immediately or joins the draft in flight is
    ``launch(..., explicit=True)``'s decision.
    """
    revisions = doc.get('requirements', {}).get('revisions') or []
    turn = (revisions[-1].get('source') or {}).get('logical_turn_id') if revisions else None
    dispatch = doc.get('_planner_dispatches', {}).get(turn)
    return (held(dispatch) and dispatch.get('state') == 'SAVED' and doc.get('status') == 'ready'
            and not doc.get('attachment') and not doc.get('archived_at'))


def public(doc, now=None):
    revisions = doc.get('requirements', {}).get('revisions') or []
    if not revisions:
        return None
    latest = revisions[-1]
    turn = (latest.get('source') or {}).get('logical_turn_id')
    dispatch = doc.get('_planner_dispatches', {}).get(turn) or {}
    waiting = held(dispatch)
    behind = coalesced(dispatch)
    scheduled = [row.get('requirements_revision', 0)
                 for row in doc.get('_planner_dispatches', {}).values()
                 if isinstance(row, dict) and not held(row)
                 and type(row.get('requirements_revision')) is int]
    count = latest['revision'] - max(scheduled, default=0) if waiting else 0
    # A coalesced update starts by itself when the draft ahead of it ends. Only
    # when that draft has stalled, or is no longer recorded as running, may the
    # human launch it explicitly.
    stuck = behind and not blocking(doc, in_flight_candidates(doc), now)
    return {'held': waiting, 'coalesced': behind, 'stalled': stuck,
            'requirements_revision': latest['revision'], 'logical_turn_id': turn,
            'answers_since_update': count, 'answers_per_update': ANSWERS_PER_UPDATE,
            'can_refresh': refreshable(doc) and (not behind or stuck)}
