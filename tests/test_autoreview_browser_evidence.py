"""Offline canonical browser receipts; no browser or model is dispatched."""
import base64
import copy
import json
import shlex
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

import autoreview_product_probe as probe

from . import test_autoreview_products as audit


def png(width=375, height=812):
    """A sanitized valid RGB image; provider execution, not pixels, is provenance."""
    def chunk(kind, data):
        return (len(data).to_bytes(4, 'big') + kind + data
                + zlib.crc32(kind + data).to_bytes(4, 'big'))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', width.to_bytes(4, 'big') + height.to_bytes(4, 'big') + b'\x08\x02\0\0\0')
            + chunk(b'IDAT', zlib.compress((b'\0' + b'\xff' * (width * 3)) * height))
            + chunk(b'IEND', b''))


class BrowserEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve()
        (self.project / 'index.html').write_text('frozen fixture')
        self.identity = probe.candidate_identity(self.project, files=('index.html',))
        self.command = [sys.executable, str(Path(probe.__file__).resolve()), '--project', str(self.project), '--browser']
        self.uri = (self.project / 'index.html').as_uri()
        def element(text, y, height, overflow='visible'):
            return dict(text=text, rect=dict(x=8, y=y, width=359, height=height,
                        left=8, top=y, right=367, bottom=y + height),
                        style=dict(overflow=overflow, display='block', visibility='visible', opacity='1'))
        # Geometry retains the real render's structure and observations, not its paths.
        self.observation = dict(url=self.uri, viewport=dict(width=375, height=812, scrollX=0, scrollY=0),
            elements=dict(panel=element('Task runningUpdated just now', 8, 80, 'hidden'),
                          space=element('', 87.875, 150),
                          freshness=element('Updated just now', 253.875, 18.5)),
            screenshot_base64=base64.b64encode(png()).decode('ascii'))
        self.row = dict(candidate=self.identity, execution=dict(
            command=[sys.executable, '-c', probe.BROWSER_PROBE, self.uri], cwd=str(self.project),
            scope='probe', returncode=0, stdout=json.dumps(self.observation), stderr='',
            timed_out=False, interrupted=False, error=None))
        self.event = dict(id='item_17', type='command_execution', status='completed', exit_code=0,
                          command=shlex.join(self.command), aggregated_output=json.dumps(self.row))

    def accepted(self, event=None, row=None, observation=None, checks=None):
        event = copy.deepcopy(self.event if event is None else event)
        if row is not None or observation is not None:
            row = copy.deepcopy(self.row if row is None else row)
            if observation is not None:
                row['execution']['stdout'] = json.dumps(observation)
            event['aggregated_output'] = json.dumps(row)
        if checks is None:
            checks = [dict(command=event['command'], exit_code=0, evidence_ref='event:item_17')]
        return audit.browser_evidence([event], checks, self.command, self.project, self.identity)

    def test_canonical_direct_and_shell_commands(self):
        self.assertTrue(self.accepted())
        for shell in ('sh', 'bash', 'zsh'):
            event = dict(self.event, command=shlex.join([shell, '-lc', self.event['command']]))
            self.assertTrue(self.accepted(event))
            bare_report = [dict(command=self.event['command'], exit_code=0, evidence_ref='event:item_17')]
            self.assertTrue(self.accepted(event, checks=bare_report))

    def test_not_a_report_summary_or_arbitrary_command(self):
        for command in ('printf screenshot', 'echo playwright', 'cat index.html',
                        'fake-wrapper ' + self.event['command'], self.event['command'] + ' --unknown'):
            with self.subTest(command=command):
                self.assertFalse(self.accepted(dict(self.event, command=command)))
        for output in ('375x812 freshness clipped', json.dumps(self.observation),
                       self.event['aggregated_output'] + '\n' + self.event['aggregated_output']):
            self.assertFalse(self.accepted(dict(self.event, aggregated_output=output)))

    def test_provider_event_and_report_link_are_required(self):
        self.assertFalse(audit.browser_evidence([], [], self.command, self.project, self.identity))
        for change in (dict(type='agent_message'), dict(type='mcp_tool_call'),
                       dict(status='failed'), dict(status='in_progress'), dict(exit_code=1), dict(exit_code=None),
                       dict(error='blocked'), dict(timed_out=True), dict(interrupted=True)):
            self.assertFalse(self.accepted(dict(self.event, **change)))
        for checks in ([], [dict(command=self.event['command'], exit_code=0, evidence_ref='event:other')],
                       [dict(command='echo fake', exit_code=0, evidence_ref='event:item_17')],
                       [dict(command=self.event['command'], exit_code=1, evidence_ref='event:item_17')]):
            self.assertFalse(self.accepted(checks=checks))

    def test_execution_failure_unknown_argv_and_candidate_mismatch(self):
        for change in (dict(returncode=1), dict(timed_out=True), dict(interrupted=True), dict(error='blocked'),
                       dict(stderr='browser error'), dict(scope='host-only'), dict(cwd='/other'),
                       dict(command=[sys.executable, '-c', 'print("fake screenshot")', self.uri]),
                       dict(stdout='source inspection only')):
            with self.subTest(change=change):
                row = copy.deepcopy(self.row)
                row['execution'].update(change)
                self.assertFalse(self.accepted(row=row))
        self.assertFalse(self.accepted(row=dict(self.row, candidate={'index.html': 'stale'})))

    def test_wrong_page_viewport_scroll_and_missing_geometry(self):
        for change in (dict(url='http://127.0.0.1:8765/'), dict(url='file:///other/index.html'),
                       dict(viewport=dict(width=812, height=375, scrollX=0, scrollY=0)),
                       dict(viewport=dict(width=375, height=812, scrollX=0, scrollY=1)),
                       dict(viewport=dict(width=375, height=812, scrollX=1, scrollY=0)), dict(elements={})):
            self.assertFalse(self.accepted(observation=dict(self.observation, **change)))

    def test_visible_or_inconsistent_geometry_does_not_prove_clipping(self):
        changes = [('freshness', 'text', 'Old fixture'), ('panel', 'text', 'Unrelated page'),
                   ('panel', 'style', dict(overflow='visible')),
                   ('freshness', 'style', dict(display='none', visibility='visible', opacity='1')),
                   ('freshness', 'style', dict(display='block', visibility='hidden', opacity='1')),
                   ('freshness', 'rect', dict(x=8, y=20, left=8, top=20, right=367, bottom=38.5, width=359, height=18.5)),
                   ('freshness', 'rect', dict(x=8, y=253.875, left=8, top=253.875, right=367, bottom=0, width=359, height=18.5))]
        for element, field, value in changes:
            with self.subTest(element=element, field=field, value=value):
                obs = copy.deepcopy(self.observation)
                obs['elements'][element][field] = value
                self.assertFalse(self.accepted(observation=obs))
        obs = copy.deepcopy(self.observation)
        obs['elements']['freshness']['rect']['x'] = float('nan')
        self.assertFalse(self.accepted(observation=obs))

    def test_missing_fake_truncated_corrupt_and_wrong_sized_screenshots(self):
        images = [b'', b'fake PNG screenshot', png()[:24], png()[:-1], png(374, 812), png(375, 811)]
        corrupt = bytearray(png()); corrupt[-5] ^= 1
        images.append(bytes(corrupt))
        for image in images:
            self.assertFalse(self.accepted(observation=dict(self.observation,
                screenshot_base64=base64.b64encode(image).decode('ascii'))))
        obs = copy.deepcopy(self.observation)
        del obs['screenshot_base64']
        self.assertFalse(self.accepted(observation=obs))
        self.assertFalse(self.accepted(observation=dict(self.observation, screenshot_base64='not base64!')))

    def test_live_oracle_uses_latest_invocation_or_its_archived_original_only(self):
        case = audit.ReviewProducts('test_04_mobile_initial_visibility_requires_rendered_evidence')
        case.project = self.project
        checks = [dict(command=self.event['command'], exit_code=0, evidence_ref='event:item_17')]
        state = dict(validation=dict(verdict='FAIL', findings=[dict(finding='clipped freshness')], checks=checks),
                     stages=[dict(stage='sol', events='older.jsonl'), dict(stage='sol', events='latest.jsonl')])
        def events(path):
            return [] if path == 'latest.jsonl' else [dict(type='item.completed', item=self.event)]
        with patch.object(case, 'require_capability'), patch.object(case, 'prepare') as prepare, \
                patch.object(case, 'review', return_value=state), patch.object(audit.support, 'events', side_effect=events) as reader:
            with self.assertRaisesRegex(AssertionError, 'No successful canonical'):
                case.test_04_mobile_initial_visibility_requires_rendered_evidence()
            reader.assert_called_once_with('latest.jsonl')
            state['stages'].append(dict(stage='sol_report_repair', events='repair.jsonl',
                                        applied_original_events='archived/original.jsonl'))
            reader.reset_mock()
            case.test_04_mobile_initial_visibility_requires_rendered_evidence()
            reader.assert_called_once_with('archived/original.jsonl')
            method = prepare.call_args.args[0]['contract']['acceptance_criteria'][0]['verification_method']
            self.assertIn(shlex.join(self.command), method)
            self.assertNotIn('253.875', method)
            self.assertNotIn('overflow', method)


if __name__ == '__main__':
    unittest.main()
