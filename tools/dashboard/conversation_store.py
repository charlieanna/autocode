"""Route old intake and continuous planning documents without rewriting either."""
try:
    from .. import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol


try:
    from .dashboard_conversations import ConversationStore as IntakeStore
    from .dashboard_continuous import ContinuousConversationStore
except ImportError:
    from dashboard_conversations import ConversationStore as IntakeStore
    from dashboard_continuous import ContinuousConversationStore


class ConversationStore:
    def __init__(self, root=None, provider=None, planner=None):
        self.intake = IntakeStore(root=root, provider=provider)
        self.root = self.intake.root
        self.continuous = ContinuousConversationStore(
            root=self.root / 'continuous', provider=provider, planner=planner)
        # Existing integrations injecting only an intake provider keep that
        # boundary; full pipeline tests inject both independent providers.
        self.new = self.intake if provider is not None and planner is None else self.continuous

    def _owner(self, ident):
        path = self.continuous._path(ident)
        return self.continuous if path.exists() else self.intake

    @property
    def provider(self):
        return self.new.provider

    @provider.setter
    def provider(self, value):
        self.new.provider = value

    def create(self, *args, **kwargs):
        return self.new.create(*args, **kwargs)

    def create_empty(self, *args, **kwargs):
        return self.new.create_empty(*args, **kwargs)

    def list(self, include_archived=False):
        rows = self.intake.list(include_archived) + self.continuous.list(include_archived)
        return sorted(rows, key=lambda row: row['updated_at'], reverse=True)

    def get(self, ident):
        return self._owner(ident).get(ident)

    def send(self, ident, *args, **kwargs):
        return self._owner(ident).send(ident, *args, **kwargs)

    def retry(self, ident):
        return self._owner(ident).retry(ident)

    def update(self, ident, **kwargs):
        return self._owner(ident).update(ident, **kwargs)

    def archive(self, ident, action):
        return self._owner(ident).archive(ident, action)

    def claim_attachment(self, ident, attachment, expected_attachment=None):
        store = self._owner(ident)
        with store._guard():
            if store is self.continuous:
                current = self.handoff(ident)
                if attachment.get('handoff_digest') != current['digest']:
                    raise ValueError('The conversation changed while attaching. Review the latest draft and retry.')
            return store.claim_attachment(ident, attachment, expected_attachment)

    def is_continuous(self, ident):
        return self._owner(ident) is self.continuous

    def handoff(self, ident):
        store = self._owner(ident)
        with store._guard():
            doc = store._load(ident)
            if store is self.continuous:
                latest = (doc.get('requirements', {}).get('revisions') or [{}])[-1].get('revision')
                if not any(row.get('status') == 'current'
                           and row.get('requirements_revision') == latest
                           and row.get('freshness', {}).get('state') == 'fresh'
                           for row in doc.get('plan_drafts', [])):
                    raise ValueError('Wait for the current structured draft before attaching a project. Retry a failed delivery from this chat.')
            return protocol.handoff_from_document(doc)

    require_visible = staticmethod(IntakeStore.require_visible)

    def close(self, wait=True):
        self.continuous.close(wait)
        self.intake.close(wait)
