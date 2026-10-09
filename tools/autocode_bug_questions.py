"""Report-bound clarification for an initial bug investigation.

Pure policy shared by the Investigator, human publication and answer routing.
The bug job owns ``investigation``; its retained output hash binds these questions
to one accepted report. Existing human request receipts bind answers to the run.
"""
try:
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_util as util
    import autocode_workflows as workflows


def has_questions(state):
    """Questions belong to initial diagnosis, before a build contract or task."""
    found = state.get('investigation') or {}
    return (workflows.kind(state) == 'bugfix' and found.get('outcome') == 'not_reproduced'
            and bool(found.get('questions')) and not state.get('goal_contract')
            and not state.get('current_task'))


def questions(state):
    """Stable ordinary questions for the exact retained diagnosis report."""
    if not has_questions(state):
        return []
    found = state['investigation']
    report = found.get('output_hash') or util.digest(found)
    return [{'id': 'investigation-' + util.digest({'report': report, 'index': index, 'question': text})[:16],
             'question': text, 'why': 'The Investigator needs this information before the reported bug can be resolved.',
             'options': [], 'proposed_default': ''}
            for index, text in enumerate(found['questions'])]


def matches_questions(state, rows):
    declared = questions(state)
    return (state.get('next_stage') == workflows.INVESTIGATE_STAGE and bool(rows)
            and len({row.get('id') for row in rows}) == len(rows)
            and all(row in declared for row in rows))


def evidence(state):
    found = state.get('investigation') or {}
    output, digest = found.get('output'), found.get('output_hash')
    return {'hashes': {output: digest}} if output and digest else {'hashes': {}}


def repeated_answers(state, rows):
    answered = {str((answer.get('question') or {}).get('question', '')).strip()
                for answer in state.get('answers', {}).values()
                if answer.get('actor') == 'user_cli' and answer in state.get('user_events', [])
                and str((answer.get('question') or {}).get('id', '')).startswith('investigation-')}
    return [text for text in rows if text.strip() in answered]


def answer_frontier(state, default_stage):
    if has_questions(state) and state.get('next_stage') == workflows.INVESTIGATE_STAGE:
        return {'phase': 'INVESTIGATING', 'next_stage': workflows.INVESTIGATE_STAGE}
    return {'phase': 'DISCOVERING', 'next_stage': default_stage}


def accepts(state, proposal):
    """Only actual questions from a successfully recorded Investigator report."""
    if proposal.get('scope') != 'clarification' or not matches_questions(state, proposal.get('questions') or []):
        return False
    found = state['investigation']
    pins = evidence(state)['hashes']
    if not pins or not found.get('source_revision') or any(proposal.get('evidence', {}).get('hashes', {}).get(path) != digest
                       for path, digest in pins.items()):
        return False
    return any((row.get('original_stage') or row.get('stage')) == workflows.INVESTIGATE_STAGE
               and row.get('output') == found.get('output') and row.get('exit_code') == 0
               and row.get('source_revision') == found.get('source_revision') and not row.get('rejected')
               for row in state.get('stages', []))
