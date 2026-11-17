"""Offline checks for mock skill uploads and interrupted reset recovery."""

import importlib.util
import io
import json
import sys
import tarfile
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

SETUP = Path(__file__).resolve().parents[1] / 'setup'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SETUP / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


skills = load('seed_mlflow_skills', 'seed_mlflow_skills.py')
reset = load('reset_mlflow_db', 'reset-mlflow-db.py')


class FakeRegistry:
    def __init__(self):
        self.parents = {}
        self.versions = {}
        self.uploads = 0
        self.fail_upload = False

    def request(self, method, path='', **kwargs):
        if not path and method == 'GET':
            return {'skills': list(self.parents.values())}
        if not path and method == 'POST':
            parent = dict(kwargs['data'])
            self.parents[parent['name']] = parent
            return parent
        parts = path.strip('/').split('/')
        name = parts[0]
        if len(parts) == 1 and method == 'GET':
            return self.parents.get(name)
        if len(parts) == 1 and method == 'PATCH':
            self.parents[name].update(kwargs['data'])
            return self.parents[name]
        if len(parts) == 3 and method == 'GET':
            return self.versions[name, int(parts[2])]
        if len(parts) == 2 and method == 'POST':
            if self.fail_upload:
                raise RuntimeError('Upload unavailable')
            body = kwargs['data']
            metadata = json.loads(body.split(b'\r\n\r\n', 1)[1].split(b'\r\n', 1)[0])
            version = self.parents[name].get('latest_version', 0) + 1
            result = {'version': version, 'digest': metadata['digest'], 'status': metadata['status']}
            self.versions[name, version] = result
            self.parents[name]['latest_version'] = version
            self.uploads += 1
            return result
        raise AssertionError((method, path))


class SkillSeedingTests(unittest.TestCase):
    def setUp(self):
        mcp = types.SimpleNamespace(seed_mcp_servers=lambda: None)
        modules = patch.dict(sys.modules, {'seed_mlflow_mcp_servers': mcp})
        modules.start()
        self.addCleanup(modules.stop)

    def test_only_reviewed_manifest_uploaded(self):
        for skill in skills.skill_payloads():
            with tarfile.open(fileobj=io.BytesIO(skill['archive']), mode='r:gz') as archive:
                self.assertEqual(archive.getnames(), ['SKILL.md'])
                self.assertIn(skill['name'].encode(), archive.extractfile('SKILL.md').read())

    def test_retry_reuses_versions_and_content_change_creates_one_version(self):
        registry = FakeRegistry()
        payloads = skills.skill_payloads()
        skills.register_skills(registry, payloads)
        skills.register_skills(registry, payloads)
        self.assertEqual(registry.uploads, len(payloads))
        changed = [dict(p) for p in payloads]
        changed[0]['digest'] = 'changed-content'
        skills.register_skills(registry, changed)
        self.assertEqual(registry.uploads, len(payloads) + 1)

    def test_partial_parent_creation_can_resume(self):
        registry = FakeRegistry()
        registry.fail_upload = True
        with self.assertRaisesRegex(RuntimeError, 'Upload unavailable'):
            skills.register_skills(registry, skills.skill_payloads())
        self.assertEqual(len(registry.parents), 1)
        registry.fail_upload = False
        skills.register_skills(registry, skills.skill_payloads())
        self.assertEqual(registry.uploads, 3)

    def test_missing_api_stops_before_mutations(self):
        from unittest.mock import Mock
        registry = Mock()
        registry.request.side_effect = RuntimeError('HTTP 404')
        with self.assertRaises(RuntimeError):
            skills.register_skills(registry, skills.skill_payloads())
        registry.request.assert_called_once_with('GET')

    def test_skills_only_resume_does_not_repeat_other_reset_steps(self):
        state = {'phase': 'seeding-skills', 'mlflow_uid': 'uid', 'operator': {'replicas': 1}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.dict(sys.modules, {'seed_mlflow_skills': skills}), \
                    patch.object(reset, 'get', return_value={'metadata': {'uid': 'uid'}}), \
                    patch.object(reset, 'restart_agent', side_effect=AssertionError('restart')), \
                    patch.object(reset, 'seed_agent_prompt', side_effect=AssertionError('prompt')), \
                    patch.object(reset, 'sql', side_effect=AssertionError('SQL')), \
                    patch.object(reset, 'migration_jobs', side_effect=AssertionError('migration')), \
                    patch.object(skills, 'seed_skills') as seed:
                reset.recover(state, path, 10)
            seed.assert_called_once_with()
            self.assertEqual(json.loads(path.read_text())['phase'], 'complete')

    def test_failed_skill_seed_remains_resumable_after_prompt_seed(self):
        state = {'phase': 'seeding-prompt'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.dict(sys.modules, {'seed_mlflow_skills': skills}), \
                    patch.object(reset, 'seed_agent_prompt') as prompt, \
                    patch.object(skills, 'seed_skills', side_effect=RuntimeError('HTTP 403')):
                with self.assertRaisesRegex(RuntimeError, 'HTTP 403'):
                    reset.finish_reset(state, path, 10)
            prompt.assert_called_once_with()
            self.assertEqual(json.loads(path.read_text())['phase'], 'seeding-skills')

    def test_reset_orders_restart_then_prompt_then_skills(self):
        state = {'phase': 'restarting-agent'}
        order = []
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            def seed_skills():
                self.assertEqual(json.loads(path.read_text())['phase'], 'seeding-skills')
                order.append('skills')
            with patch.dict(sys.modules, {'seed_mlflow_skills': skills}), \
                    patch.object(reset, 'restart_agent', side_effect=lambda *a: order.append('restart')), \
                    patch.object(reset, 'seed_agent_prompt', side_effect=lambda: order.append('prompt')), \
                    patch.object(skills, 'seed_skills', side_effect=seed_skills):
                reset.finish_reset(state, path, 10)
            self.assertEqual(order, ['restart', 'prompt', 'skills'])
            self.assertEqual(state['phase'], 'complete')

    def test_prompt_failure_does_not_register_skills(self):
        state = {'phase': 'seeding-prompt'}
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(sys.modules, {'seed_mlflow_skills': skills}), \
                    patch.object(reset, 'seed_agent_prompt', side_effect=RuntimeError('prompt failure')), \
                    patch.object(skills, 'seed_skills') as seed:
                with self.assertRaisesRegex(RuntimeError, 'prompt failure'):
                    reset.finish_reset(state, Path(directory) / 'state.json', 10)
            seed.assert_not_called()
            self.assertEqual(state['phase'], 'seeding-prompt')

    def test_reset_dry_run_never_registers_skills(self):
        with patch.dict(sys.modules, {'seed_mlflow_skills': skills}), \
                patch('sys.argv', ['reset']), patch.object(reset, 'snapshot', return_value={}), \
                patch.object(reset, 'oc', side_effect=AssertionError('mutation')), \
                patch.object(skills, 'seed_skills') as seed, \
                patch('sys.stdout', io.StringIO()):
            reset.main()
        seed.assert_not_called()

    def test_preview_never_contacts_cluster(self):
        with patch('sys.argv', ['seed_mlflow_skills.py']), \
                patch.object(skills, 'oc_output', side_effect=AssertionError('cluster access')), \
                patch('sys.stdout', io.StringIO()):
            skills.main()


if __name__ == '__main__':
    unittest.main()
