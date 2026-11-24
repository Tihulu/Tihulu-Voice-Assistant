import os
import subprocess
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from tools.conflict_check import report, merge_tree

class GateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        prev = os.getcwd()
        os.chdir(tmp.name)
        self.addCleanup(os.chdir, prev)
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.commit('shared.txt', 'base')
        self.git('switch', '-c', 'feature')
        self.commit('feature.txt', 'feature')
        self.git('switch', 'main')
        self.commit('main.txt', 'main')
    def git(self, *args):
        return subprocess.run(['git', *args], check=True, capture_output=True, text=True).stdout.strip()
    def commit(self, name, data):
        Path(name).write_text(data)
        self.git('add', name)
        self.git('commit', '-m', name)
    def run_report(self, plan):
        out, err = StringIO(), StringIO()
        before = self.git('rev-parse', 'HEAD')
        rc = report(plan, out=out, err=err)
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')
        return rc, out.getvalue(), err.getvalue()
    def entry(self, head='feature', base='main'):
        return {'number': 1, 'head': head, 'base': base}
    def test_missing_head(self):
        rc, out, err = self.run_report([self.entry(head='absent')])
        self.assertEqual(rc, 1)
        self.assertIn('head missing locally', err)
        self.assertIn('0 branches checked, 1 problems', out)
    def test_missing_base(self):
        rc, out, err = self.run_report([self.entry(base='absent')])
        self.assertEqual(rc, 1)
        self.assertIn('base missing locally', err)
    def test_empty_plan(self):
        rc, out, err = self.run_report([])
        self.assertEqual(rc, 1)
        self.assertIn('no branches to rehearse', err)
    def test_clean(self):
        rc, out, err = self.run_report([self.entry()])
        self.assertEqual(rc, 0)
        self.assertIn('1 branches checked, 0 problems', out)
        self.assertEqual(err, '')
    def test_mixed_missing_and_clean(self):
        rc, out, err = self.run_report([self.entry(), self.entry(head='absent')])
        self.assertEqual(rc, 1)
        self.assertIn('1 branches checked, 1 problems', out)
    def test_conflict(self):
        self.commit('shared.txt', 'main')
        self.git('switch', 'feature')
        self.commit('shared.txt', 'feature')
        self.git('switch', 'main')
        rc, out, err = self.run_report([self.entry()])
        self.assertEqual(rc, 1)
        self.assertIn('CONFLICT in shared.txt', err)
    def test_invalid_entry(self):
        rc, out, err = self.run_report([self.entry(head='')])
        self.assertEqual(rc, 1)
        self.assertIn('no head or base', err)
