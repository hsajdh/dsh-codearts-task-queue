import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from urllib.parse import unquote

import yaml

ROOT = Path(__file__).resolve().parent.parent


class PublicContractTests(unittest.TestCase):
    def test_local_document_links_exist(self):
        documents = [*ROOT.glob('*.md'), *ROOT.joinpath('docs').glob('*.md')]
        for document in documents:
            for destination in re.findall(r'\]\(([^)]+)\)', document.read_text(encoding='utf-8')):
                if destination.startswith(('https://', 'http://', '#')):
                    continue
                target = unquote(destination.split('#', 1)[0])
                self.assertTrue((document.parent / target).is_file(), (document.name, destination))

    def test_examples_share_a_database_and_expose_expected_roles(self):
        connector = json.loads((ROOT / 'examples/codearts.connector.json').read_text(encoding='utf-8'))['dsh-task-queue']
        entries = yaml.safe_load((ROOT / 'examples/dsh.patch.yml').read_text(encoding='utf-8'))[0]['insert']
        dispatcher = entries[0]['config']
        relay = entries[1]['config']
        self.assertIn('executor', connector['command'])
        self.assertIn('dispatcher', dispatcher['args'])
        self.assertEqual(connector['command'][-1], dispatcher['args'][-1])
        self.assertEqual(dispatcher['args'][-1], relay['databasePath'])
        self.assertEqual(dispatcher['serverName'], 'codearts_queue')

    def test_queue_rejects_relative_database_argument(self):
        result = subprocess.run([sys.executable, str(ROOT / 'codearts_queue.py'), '--role', 'dispatcher',
                                 '--database', 'relative.sqlite'], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 2)
        self.assertIn('absolute path', result.stderr)

    def test_generator_handles_custom_paths_and_writes_only_import_artifacts(self):
        parent = ROOT / 'tests/test-temp'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            directory = Path(temporary).resolve() / '中文 space'
            output = directory / 'generated'
            data = directory / 'private data'
            result = subprocess.run([sys.executable, str(ROOT / 'scripts/configure.py'), '--profiles', 'desktop', 'web',
                '--dsh-home', str(directory / 'dsh'), '--codearts-home', str(directory / 'codearts'),
                '--data-dir', str(data), '--output-dir', str(output)], capture_output=True, text=True,
                encoding='utf-8', timeout=15, env={**os.environ, 'PYTHONUTF8': '1'})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((directory / 'dsh').exists())
            self.assertFalse((directory / 'codearts').exists())
            connector = json.loads((output / 'codearts.connector.json').read_text(encoding='utf-8'))['dsh-task-queue']
            self.assertEqual(connector['command'][0], str(Path(sys.executable).absolute()))
            self.assertEqual(connector['command'][-1], str(data / 'queue.sqlite'))
            self.assertTrue((output / 'desktop.patch.yml').is_file())
            self.assertTrue((output / 'web.patch.yml').is_file())


if __name__ == '__main__':
    unittest.main(verbosity=2)
