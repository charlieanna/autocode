"""Folder identity and explicit legacy scope confirmation, without model dispatch."""
import hashlib
import json
from pathlib import Path


def creation_scope(workspace):
    if not workspace:
        return {}
    path = Path(workspace).resolve(strict=True)
    info = path.stat()
    if not path.is_dir() or not (path / '.git').exists():
        raise ValueError('Select an existing Git project folder.')
    return {'project_workspace': str(path), '_project_identity': [info.st_dev, info.st_ino]}


def scope_problem(doc):
    if not doc.get('project_workspace') or (doc.get('attachment') or {}).get('status') == 'linked':
        return {}
    try:
        info = Path(doc['project_workspace']).resolve(strict=True).stat()
        if doc.get('_project_identity') is not None and doc['_project_identity'] != [info.st_dev, info.st_ino]:
            return {'project_scope_error': 'The saved project folder was replaced. Restore the original folder before continuing; all saved work is preserved.'}
        current = creation_scope(doc['project_workspace'])
    except (OSError, ValueError, RuntimeError):
        return {'project_scope_error': 'The saved project folder is unavailable. Restore it before continuing; your conversation is preserved.'}
    saved = doc.get('_project_identity')
    if saved is not None:
        if saved == current['_project_identity']:
            return {}
        return {'project_scope_error': 'The saved project folder was replaced. Restore the original folder before continuing; all saved work is preserved.'}
    token = hashlib.sha256(json.dumps([doc['id'], current], sort_keys=True).encode()).hexdigest()
    return {'project_scope_error': 'This older conversation has no saved folder identity. Confirm the current project folder below before continuing. Your history is preserved.',
            'project_scope_confirmation': {'workspace': current['project_workspace'], 'token': token}}


class ScopeConfirmationMixin:
    def confirm_project_scope(self, ident, token):
        with self._guard():
            doc = self._load(ident)
            self.require_visible(doc)
            if (doc.get('attachment') and doc['attachment'].get('status') != 'failed') or doc.get('status') in ('thinking', 'uncertain'):
                raise ValueError('Wait for active work or reconcile uncertain delivery before confirming a folder.')
            proposed = scope_problem(doc).get('project_scope_confirmation')
            if not proposed or not isinstance(token, str) or token != proposed['token']:
                raise ValueError('The project folder changed or was already confirmed. Refresh chat and inspect it again.')
            current = creation_scope(proposed['workspace'])
            # Recheck the exact identity after resolving the proposed path.
            check = hashlib.sha256(json.dumps([doc['id'], current], sort_keys=True).encode()).hexdigest()
            if check != token:
                raise ValueError('The project folder changed. Refresh chat before confirming.')
            doc['_project_identity'] = current['_project_identity']
            doc['_project_scope_confirmation'] = {'token': token, 'actor': 'user_dashboard'}
            self._save(doc)
            return self._public(doc)
