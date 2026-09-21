import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_evidence import stage_evidence, MAX_DIFF_BYTES


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / 'run'
        self.run.mkdir()
        self.diff = self.run / 'step.diff'
        self.diff.write_text('diff --git a/app.py b/app.py\n-old\n+new\n')
        self.save(str(self.diff))

    def save(self, ref):
        (self.run / 'state.json').write_text(json.dumps({'stages': [{'stage': 'terra', 'diff_ref': ref}]}))

    def test_saved_stage_list_and_diff_do_not_mutate_checkpoint(self):
        before = (self.run / 'state.json').read_bytes()
        self.assertEqual(stage_evidence(self.run)['stages'][0]['index'], 0)
        self.assertIn('+new', stage_evidence(self.run, 0)['text'])
        self.assertFalse(stage_evidence(self.run, 0)['truncated'])
        self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_arbitrary_indices_and_escape_paths_are_rejected(self):
        for index in (-1, 1, True, '0'):
            with self.assertRaises(ValueError): stage_evidence(self.run, index)
        for ref in (str(self.root / 'secret'), '../secret'):
            self.save(ref)
            with self.assertRaises(ValueError): stage_evidence(self.run, 0)

    def test_file_and_parent_symlinks_are_rejected(self):
        secret = self.root / 'secret'
        secret.write_text('not task evidence')
        link = self.run / 'link'
        link.symlink_to(secret)
        self.save(str(link))
        with self.assertRaises(ValueError): stage_evidence(self.run, 0)
        directory = self.run / 'outside'
        directory.symlink_to(self.root, target_is_directory=True)
        self.save(str(directory / 'secret'))
        with self.assertRaises(ValueError): stage_evidence(self.run, 0)

    def test_nonregular_file_rejected_without_blocking(self):
        fifo = self.run / 'fifo'
        os.mkfifo(fifo)
        self.save(str(fifo))
        with self.assertRaises(ValueError): stage_evidence(self.run, 0)

    def test_large_diff_is_bounded_and_marked(self):
        self.diff.write_bytes(b'x' * (MAX_DIFF_BYTES + 20))
        result = stage_evidence(self.run, 0)
        self.assertTrue(result['truncated'])
        self.assertEqual(len(result['text']), MAX_DIFF_BYTES)

    def test_current_file_preview_only_accepts_recorded_project_paths(self):
        (self.root / 'new.py').write_text('print("Hello")')
        state={'stages':[{'stage':'terra','diff_ref':str(self.diff),'changed_files':['new.py','../secret','.git/config']} ]}
        (self.run / 'state.json').write_text(json.dumps(state))
        result=stage_evidence(self.run,0,workspace=self.root,file_index=0)
        self.assertTrue(result['current_contents'])
        self.assertEqual(result['text'],'print("Hello")')
        for index in (1,2,3,-1):
            with self.assertRaises(ValueError):stage_evidence(self.run,0,workspace=self.root,file_index=index)

    def test_unchanged_steps_do_not_repeat_the_same_project_diff(self):
        (self.run / 'state.json').write_text(json.dumps({'stages':[
            {'stage':'terra','diff_ref':str(self.diff),'changed_files':['app.py']},
            {'stage':'sol','diff_ref':str(self.diff),'changed_files':[]}]}))
        self.assertEqual([row['stage'] for row in stage_evidence(self.run)['stages']],['terra'])
