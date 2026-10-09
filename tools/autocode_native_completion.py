"""Prepare the original Completion decision for its existing durable boundary.

This dependency never marks a milestone accepted, saves parent state, reconstructs
history, or publishes authority. The controller supplies its unchanged accepted
decision and applies its ordinary completion/functional gates using the returned
typed proof. Only that exact accepted candidate may prepare a pending publication.
"""
from copy import deepcopy

try:
    from . import autocode_util as util, autocode_native_launch as launch
    from . import autocode_visual_factory as factory, autocode_visual_history as history
except ImportError:
    import autocode_util as util
    import autocode_native_launch as launch
    import autocode_visual_factory as factory
    import autocode_visual_history as history


def _require(value, reason):
    if not value:
        raise util.Paused('PAUSED_VISUAL_EVIDENCE', 'Native completion: ' + reason)


class NativeCompletionPreparation:
    @history._technical_hold
    def __init__(self, context, state, *, snapshot, record, decision):
        _require(type(context) is launch.NativeRunContext
                 and type(context.factory) is factory.NativeVisualFactory,
                 'missing the original concrete run/factory context')
        _require(record.get('stage') == 'astra_review'
                 and decision.get('status') in ('COMPLETE', 'TASK_COMPLETE'),
                 'requires the original terminal Completion decision')
        self.factory = context.factory
        owner = self.factory.history
        _require(type(owner) is history.NativeReviewHistory, 'missing original native history owner')
        runtime_hash = util.digest(history.runtime_files(owner.runtime_root))
        self.binding = history._task_binding(state, snapshot, runtime_hash)
        matches = []
        # A pending physical sidecar is not an accepted review. Only the same
        # protection journal's actual committed publications can be selected.
        for selector in self.factory.protection.committed_selectors('reviews'):
            original = owner.verify_review(selector)
            if original['metadata']['binding'] == self.binding:
                matches.append(selector)
        _require(len(matches) == 1, 'needs one committed exact current task/source/runtime review')
        self.snapshot, self.decision = deepcopy(snapshot), deepcopy(decision)
        self.prepared = self.factory.prepare_checkpoint(state, review_selector=matches[0],
            completion_record=record, accepted_decision=decision, snapshot=snapshot)
        _require(type(self.prepared) is history.PreparedCheckpoint
                 and self.prepared.owner is owner
                 and owner._prepared.get(self.prepared.nonce) is self.prepared,
                 'missing original unreplayed Completion handle')
        self.proof = self.factory.checkpoint_proof(state, snapshot=snapshot,
            review_selector=matches[0], prepared_checkpoint=self.prepared)
        self.scope_authority = self.factory.scope_authority
        self._publication = None

    @history._technical_hold
    def publication(self, accepted_candidate):
        """After ordinary guarded accept; before task archive and parent save.

        The factory creates only a pending original handle. ParentSink consumes
        that handle after the actual whole durable persistence boundary.
        """
        _require(self._publication is None, 'Completion publication cannot be replayed')
        _require(accepted_candidate.get('status') == 'TASK_COMPLETE'
                 and accepted_candidate.get('final_decision') == self.decision,
                 'actual terminal candidate differs from the original accepted decision')
        current = history._task_binding(accepted_candidate, self.snapshot,
            util.digest(history.runtime_files(self.factory.history.runtime_root)))
        _require(current == self.binding, 'original task/source/settings/runtime changed or was archived')
        key = self.binding['contract']['hash'] + ':' + self.binding['task']['milestone_id']
        row = accepted_candidate.get('milestone_progress', {}).get(key, {})
        _require(row.get('accepted') is True
                 and row.get('contract_hash') == self.binding['contract']['hash']
                 and row.get('accepted_source_revision') == self.snapshot['revision'],
                 'ordinary guarded milestone acceptance has not occurred')
        self._publication = self.factory.prepare_checkpoint_publication(self.prepared,
            accepted_candidate, snapshot=self.snapshot)
        return self._publication
