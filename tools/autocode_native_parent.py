"""Invocation-owned observation of the existing normalized parent JSON sink.

No controller, private state reader or global observer is installed. The caller
passes its original concrete factory, writer and snapshot function. An ACK is
retired before every write; it grants nothing until the whole caller succeeds.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import uuid
import sys

try:
    from . import autocode_util as util, autocode_visual_factory as factories
except ImportError:
    import autocode_util as util, autocode_visual_factory as factories


class ParentSink:
    def __init__(self, run_dir, *, factory, ordinary_write, snapshot):
        if type(factory) is not factories.NativeVisualFactory:
            raise TypeError('Parent sink needs its original concrete native factory')
        self.path = Path(run_dir).absolute() / 'state.json'
        self.factory = factory
        self.ordinary_write = ordinary_write
        self.snapshot = snapshot
        self.pending = None
        self.observing = False
        self.ack = None
        self.candidate = None
        self.durable_candidate = None
        self.durable_publications = ()
        self.error = None
        self.blocked = False
        outcomes = self.path.parent / 'native-parent-outcomes'
        if outcomes.is_symlink() or (outcomes.exists() and (not outcomes.is_dir() or any(outcomes.iterdir()))):
            # Retained failures never become restart-time authority. Scope 3
            # has no reconciliation operation for an uncertain parent outcome.
            self.blocked = True
            self.error = 'Retained native parent outcome requires explicit reconciliation'

    def _retain(self, error, phase):
        # Diagnostic only; these outcomes never supply native authority.
        path = self.path.parent / 'native-parent-outcomes' / (uuid.uuid4().hex + '.json')
        try:
            util.atomic_json(path, {'version': 1, 'phase': phase, 'reason': str(error),
                                   'native_authority': 'HELD'})
        except OSError as retained_error:
            self.blocked = True
            print('Native parent diagnostic could not persist: ' + str(retained_error), file=sys.stderr)

    def _invalidate(self):
        self.ack = None
        try:
            self.factory.invalidate_parent_ack()
        except (util.Paused, ValueError, OSError, RuntimeError) as error:
            # The factory retires its in-memory ACK before ownership/journal I/O.
            # Preserve a valid ordinary parent write, but stop native admission.
            self.blocked = True
            self.error = error
            self._retain(error, 'ack_invalidation')

    def require_ready(self):
        if self.blocked:
            raise util.Paused('PAUSED_VISUAL_EVIDENCE', 'Native parent sink is held: ' + str(self.error))

    def write(self, path, value, **options):
        if Path(path).absolute() != self.path:
            return self.ordinary_write(path, value, **options)
        if options:
            raise TypeError('Native parent observation cannot replace its owned sink hooks')
        self._invalidate()  # Before normalization, serialization, fsync or replace.
        self.candidate = None
        self.durable_candidate = None
        self.durable_publications = ()
        durable = False
        intent = None

        def before(*, value, serialized_bytes):
            nonlocal intent
            self.candidate = deepcopy(value)  # The sink's post-normalization value.
            if self.pending is None or self.blocked:
                return
            try:
                intent = self.factory.prepare_parent_commit(value, snapshot=self.snapshot(value), **self.pending)
            except (util.Paused, ValueError, OSError, RuntimeError) as error:
                self.blocked = True
                self.error = error
                self._retain(error, 'native_preparation')

        def after(*, value, serialized_bytes, sink_identity):
            nonlocal durable
            durable = True
            self.durable_candidate = deepcopy(value)
            if intent is None:
                return
            try:
                self.ack = self.factory.capture_parent_commit(intent, committed_candidate=value,
                    serialized_bytes=serialized_bytes, sink_identity=sink_identity, snapshot=self.snapshot(value))
                self.durable_publications = self.pending.get('publications', ())
            except (util.Paused, ValueError, OSError, RuntimeError) as error:
                self.blocked = True
                self.error = error
                self._invalidate()
                self._retain(error, 'native_acknowledgement')

        try:
            return self.ordinary_write(path, value, before_commit=before, after_commit=after)
        except Exception as error:
            self._invalidate()
            if isinstance(error, util.JSONCommitUncertain):
                self.blocked = True
                self.error = error
                self._retain(error, 'replacement_uncertain')
                raise
            if durable:
                self.blocked = True
                self.error = error
                self._retain(error, 'post_persistence')
                raise util.JSONCommitUncertain(error, candidate=self.candidate,
                                              replaced=True, durable=True) from error
            raise

    @contextmanager
    def publication(self, *, planning_record=None, approval=None, publications=()):
        if sum((planning_record is not None, approval is not None, bool(publications))) != 1:
            raise ValueError('One exact original native publication intent is required')
        with self.boundary(planning_record=planning_record, approval=approval, publications=publications):
            yield

    @contextmanager
    def boundary(self, *, planning_record=None, approval=None, publications=()):
        """Observe only this original persistence operation, including nonfinal.

        An observation without a planning/approval intent grants no native
        publication and cannot borrow a durable candidate from a previous save.
        """
        self.require_ready()
        publications = tuple(publications)
        if self.observing or sum((planning_record is not None, approval is not None, bool(publications))) > 1:
            raise ValueError('One original native persistence boundary is required')
        self.observing = True
        self.pending = ({'planning_record': planning_record, 'approval': approval, 'publications': publications}
                        if planning_record is not None or approval is not None or publications else None)
        self.error = None
        self.ack = None
        self.candidate = None
        self.durable_candidate = None
        self.durable_publications = ()
        try:
            yield
        except Exception as error:
            self._invalidate()
            self.preserve_durable_failure(error, phase='publication_boundary')
            raise
        finally:
            self.pending = None
            self.observing = False

    def publish(self, state, *, planning_record=None, approval=None, publications=()):
        """Only after the complete original action/persistence boundary returned."""
        try:
            self.require_ready()
            publications = tuple(publications)
            if sum((planning_record is not None, approval is not None, bool(publications))) != 1:
                raise ValueError('One exact original native publication intent is required')
            if len(publications) != len(self.durable_publications) or any(
                    original is not current for original, current in zip(self.durable_publications, publications)):
                raise ValueError('Native evidence publication differs from the exact durable sink intent')
            if self.ack is None or self.candidate != state:
                raise util.Paused('PAUSED_VISUAL_EVIDENCE',
                                  'No matching final durable native parent ACK: ' + str(self.error or 'candidate changed'))
            ack = self.ack
            self.ack = None
            if planning_record is not None and approval is None:
                return self.factory.commit_planning_publication(ack, state, snapshot=self.snapshot(state))
            if approval is not None and planning_record is None:
                return self.factory.commit_initial_approval(approval, state,
                    snapshot=self.snapshot(state), parent_commit=ack)
            if publications:
                snapshot = self.snapshot(state)
                return tuple(self.factory.commit_evidence_publication(handle, parent_commit=ack,
                    committed_candidate=state, snapshot=snapshot) for handle in publications)
            raise ValueError('Missing exact original native publication')
        except (util.Paused, ValueError, OSError, RuntimeError) as error:
            self.preserve_durable_failure(error, phase='native_publication')
            raise

    def preserve_durable_failure(self, error, *, phase, owner_state=None):
        """Keep post-save errors outside generic pause/rejection/rollback paths.

        The exact candidate comes only from this original sink's durable hook,
        never a reread or the caller's possibly changed in-memory dictionary.
        """
        if isinstance(error, util.JSONCommitUncertain):
            if owner_state is not None and error.replaced:
                owner_state.clear(); owner_state.update(deepcopy(error.candidate))
            raise error
        self.blocked = True
        self.error = error
        self._invalidate()
        self._retain(error, phase)
        if self.durable_candidate is not None:
            candidate = deepcopy(self.durable_candidate)
            if owner_state is not None:
                owner_state.clear(); owner_state.update(deepcopy(candidate))
            raise util.JSONCommitUncertain(error, candidate=candidate, replaced=True, durable=True) from error
