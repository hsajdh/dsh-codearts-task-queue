import argparse
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('configure_queue', ROOT / 'scripts/configure.py')
configure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(configure)


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT / 'tests/test-temp'
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temporary.name)
        self.args = argparse.Namespace(dsh_home=self.root / 'dsh', codearts_home=self.root / 'codearts',
                                      data_dir=self.root / 'private data', profiles=['desktop', 'web'], uninstall=False)
        self.args.database = self.args.data_dir / 'queue.sqlite'
        self.patches = []
        for profile in self.args.profiles:
            directory = self.args.dsh_home / 'profiles' / profile
            directory.mkdir(parents=True)
            (directory / 'package.json').write_text('{}', encoding='utf-8')
            target = directory / 'cordis.patch.yml'
            target.write_text('- insert:\n  - id: unrelated\n    name: example\n    config: {value: preserved}\n'
                              '  - id: codearts-desktop-queue\n    name: old-queue\n', encoding='utf-8')
            self.patches.append(target)
        target = self.args.codearts_home / 'mcp/mcp_settings.json'
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps({'mcp': {'other-connector': {'enabled': True}}}), encoding='utf-8')
        (self.args.dsh_home / 'AGENTS.md').write_text('Existing project instructions.\n', encoding='utf-8')

    def tearDown(self):
        self.assertTrue(self.root.resolve().is_relative_to((ROOT / 'tests/test-temp').resolve()))
        self.temporary.cleanup()

    def test_install_is_idempotent_and_preserves_other_configuration(self):
        result = configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.assertTrue(Path(result['backup']).is_dir())
        self.assertEqual(configure.make_plan(self.args), [])
        self.assertTrue(configure.doctor(self.args)['configuration_ready'])
        for target in self.patches:
            entries = yaml.safe_load(target.read_text(encoding='utf-8'))
            self.assertEqual(entries[0]['insert'][0]['id'], 'unrelated')
            self.assertEqual(entries[0]['insert'][0]['config'], {'value': 'preserved'})
            relay = next(child for entry in entries for child in entry.get('insert', [])
                         if child.get('id') == 'codearts-review-notifications')
            self.assertEqual(relay['name'], (ROOT / 'relay/index.js').as_uri() +
                             '?version=' + configure.PACKAGE_VERSION)
        settings = json.loads((self.args.codearts_home / 'mcp/mcp_settings.json').read_text(encoding='utf-8'))
        self.assertTrue(settings['mcp']['other-connector']['enabled'])
        self.assertTrue((self.args.dsh_home / 'AGENTS.md').read_text(encoding='utf-8').startswith('Existing project instructions.'))

    def test_uninstall_removes_owned_entries_and_keeps_other_entries(self):
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.args.uninstall = True
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        for target in self.patches:
            self.assertEqual([child['id'] for item in yaml.safe_load(target.read_text(encoding='utf-8'))
                              for child in item.get('insert', [])], ['unrelated'])
        settings = json.loads((self.args.codearts_home / 'mcp/mcp_settings.json').read_text(encoding='utf-8'))
        self.assertEqual(list(settings['mcp']), ['other-connector'])
        self.assertNotIn(configure.START, (self.args.dsh_home / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_partial_failure_rolls_back_already_written_files(self):
        plan = configure.make_plan(self.args)
        original_operation = configure.replace_file
        count = 0

        def fail_second(path, payload, expected):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError('Injected write failure')
            return original_operation(path, payload, expected)

        with patch.object(configure, 'replace_file', side_effect=fail_second):
            with self.assertRaises(OSError):
                configure.apply_plan(plan, self.args.data_dir)
        for path, original, _ in plan:
            self.assertEqual(configure.read_bytes(path), original)

    def test_concurrent_edit_is_detected_before_replacement(self):
        target = self.patches[0]
        original = target.read_bytes()
        target.write_bytes(b'Concurrent user edit\n')
        with self.assertRaises(RuntimeError):
            configure.replace_file(target, b'Our update\n', original)
        self.assertEqual(target.read_bytes(), b'Concurrent user edit\n')

    def test_uninstall_one_profile_keeps_shared_connector_for_the_other(self):
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.args.uninstall = True
        self.args.profiles = ['desktop']
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.args.profiles = ['web']
        self.assertTrue(configure.doctor(self.args)['configuration_ready'])
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        settings = json.loads((self.args.codearts_home / 'mcp/mcp_settings.json').read_text(encoding='utf-8'))
        self.assertNotIn('dsh-task-queue', settings['mcp'])

    def test_uninstall_works_after_profile_package_was_removed(self):
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        for profile in self.args.profiles:
            (self.args.dsh_home / 'profiles' / profile / 'package.json').unlink()
        self.args.uninstall = True
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.assertNotIn(configure.START, (self.args.dsh_home / 'AGENTS.md').read_text(encoding='utf-8'))

    def test_uninstall_missing_clients_is_a_noop(self):
        self.args.dsh_home = self.root / 'removed-dsh'
        self.args.codearts_home = self.root / 'removed-codearts'
        self.args.uninstall = True
        self.assertEqual(configure.make_plan(self.args), [])
        self.assertFalse(self.args.dsh_home.exists())
        self.assertFalse(self.args.codearts_home.exists())

    def test_uninstall_preserves_unrelated_configuration_text(self):
        for target in self.patches:
            target.write_text('# User comment\n- id: unrelated\n  config: {custom: true}\n', encoding='utf-8')
        settings = self.args.codearts_home / 'mcp/mcp_settings.json'
        settings.write_text('{"other-setting": true}\n', encoding='utf-8')
        self.args.uninstall = True
        self.assertEqual(configure.make_plan(self.args), [])

    def test_install_and_uninstall_preserve_yaml_12_scalar_meanings(self):
        target = self.patches[0]
        target.write_text('- id: existing-values\n  config:\n    mode: off\n'
                          '    labels: [on, off, yes, no]\n    enabled: true\n'
                          '    leadingZero: 012\n    scientific: 1e3\n'
                          '    reasoningEfforts: {off: null, high: high}\n', encoding='utf-8')
        expected = {'mode': 'off', 'labels': ['on', 'off', 'yes', 'no'], 'enabled': True,
                    'leadingZero': 12, 'scientific': 1000.0,
                    'reasoningEfforts': {'off': None, 'high': 'high'}}
        self.assertEqual(configure.load_patch(target)[0]['config'], expected)
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.assertEqual(configure.load_patch(target)[0]['config'], expected)
        self.args.uninstall = True
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        self.assertEqual(configure.load_patch(target)[0]['config'], expected)

    def test_doctor_detects_outdated_marked_instructions(self):
        configure.apply_plan(configure.make_plan(self.args), self.args.data_dir)
        target = self.args.dsh_home / 'AGENTS.md'
        text = target.read_text(encoding='utf-8')
        target.write_text(text.replace('用户授权将当前项目任务', 'Legacy queue instructions'), encoding='utf-8')
        self.assertFalse(configure.doctor(self.args)['checks']['dsh_instructions'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
