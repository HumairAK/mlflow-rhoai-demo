"""Verify registry replay and recovery without deploying or calling MCP servers."""

import copy
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SETUP = Path(__file__).resolve().parents[1] / 'setup'


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SETUP / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


skills = load('mcp_test_skills', 'seed_mlflow_skills.py')
with patch.dict(sys.modules, {'seed_mlflow_skills': skills}):
    mcp = load('seed_mlflow_mcp_servers', 'seed_mlflow_mcp_servers.py')
reset = load('mcp_test_reset', 'reset-mlflow-db.py')


class FakeRegistry:
    def __init__(self):
        self.parents = {}
        self.versions = {}
        self.mutations = []
        self.fail_version = False

    def request(self, method, path='', **kwargs):
        if method != 'GET':
            self.mutations.append((method, path))
        data = copy.deepcopy(kwargs.get('data', {}))
        if not path:
            if method == 'GET':
                return {'mcp_servers': list(self.parents.values())}
            self.parents[data['name']] = dict(data, tags={}, aliases=[])
            return copy.deepcopy(self.parents[data['name']])
        if '/versions/' in path:
            name, version = path[1:].split('/versions/', 1)
            tag = version.endswith('/tags')
            version = version.removesuffix('/tags')
            if method == 'GET':
                return copy.deepcopy(self.versions.get((name, version)))
            if tag:
                self.versions[name, version].setdefault('tags', {})[data['key']] = data['value']
                return {}
            self.versions[name, version].update(data)
            return copy.deepcopy(self.versions[name, version])
        if path.endswith('/versions'):
            if self.fail_version:
                raise RuntimeError('Temporary version registration failure')
            name = path[1:].removesuffix('/versions')
            version = data['server_json']['version']
            self.versions[name, version] = dict(data, tags={})
            return copy.deepcopy(self.versions[name, version])
        if path.endswith('/tags'):
            name = path[1:].removesuffix('/tags')
            self.parents[name]['tags'][data['key']] = data['value']
            return {}
        name = path[1:]
        if method == 'GET':
            return copy.deepcopy(self.parents.get(name))
        self.parents[name].update(data)
        return copy.deepcopy(self.parents[name])


class MCPSeedingTests(unittest.TestCase):
    def test_empty_registry_restores_saved_versions_tools_and_tags(self):
        registry = FakeRegistry()
        mcp.register_servers(registry, mcp.load_servers())
        self.assertEqual(len(registry.parents), 3)
        self.assertEqual(len(registry.versions), 3)
        self.assertEqual(sum(len(v['tools']) for v in registry.versions.values()), 37)
        for parent in registry.parents.values():
            self.assertIn('catalog.source.id', parent['tags'])

    def test_replay_creates_no_duplicate_records_or_writes(self):
        registry = FakeRegistry()
        mcp.register_servers(registry, mcp.load_servers())
        registry.mutations.clear()
        mcp.register_servers(registry, mcp.load_servers())
        self.assertEqual(registry.mutations, [])

    def test_partial_parent_creation_resumes(self):
        registry = FakeRegistry()
        registry.fail_version = True
        with self.assertRaises(RuntimeError):
            mcp.register_servers(registry, mcp.load_servers())
        self.assertEqual(len(registry.parents), 1)
        registry.fail_version = False
        mcp.register_servers(registry, mcp.load_servers())
        self.assertEqual(len(registry.versions), 3)

    def test_semantic_version_conflict_does_not_replace_content(self):
        registry = FakeRegistry()
        definitions = mcp.load_servers()
        mcp.register_servers(registry, definitions)
        definitions[0]['versions'][0]['server_json']['title'] = 'Different definition'
        registry.mutations.clear()
        with self.assertRaisesRegex(RuntimeError, 'differs from the saved definition'):
            mcp.register_servers(registry, definitions)
        self.assertEqual(registry.mutations, [])

    def test_resume_mcp_only_skips_every_earlier_reset_stage(self):
        state = {'phase': 'seeding-mcp-servers', 'mlflow_uid': 'uid', 'operator': {'replicas': 1}}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.dict(sys.modules, {'seed_mlflow_mcp_servers': mcp}), \
                    patch.object(reset, 'get', return_value={'metadata': {'uid': 'uid'}}), \
                    patch.object(reset, 'restart_agent', side_effect=AssertionError('restart')), \
                    patch.object(reset, 'seed_agent_prompt', side_effect=AssertionError('prompt')), \
                    patch.object(reset, 'sql', side_effect=AssertionError('SQL')), \
                    patch.object(reset, 'migration_jobs', side_effect=AssertionError('migration')), \
                    patch.object(mcp, 'seed_mcp_servers') as seed:
                reset.recover(state, path, 10)
            seed.assert_called_once_with()
            self.assertEqual(json.loads(path.read_text())['phase'], 'complete')

    def test_failure_after_skills_saves_mcp_phase_before_registration(self):
        state = {'phase': 'seeding-skills'}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            with patch.dict(sys.modules, {'seed_mlflow_skills': skills, 'seed_mlflow_mcp_servers': mcp}), \
                    patch.object(skills, 'seed_skills') as seed_skills, \
                    patch.object(mcp, 'seed_mcp_servers', side_effect=RuntimeError('MCP failure')):
                with self.assertRaisesRegex(RuntimeError, 'MCP failure'):
                    reset.finish_reset(state, path, 10)
            seed_skills.assert_called_once_with()
            self.assertEqual(json.loads(path.read_text())['phase'], 'seeding-mcp-servers')

    def test_preview_never_contacts_cluster(self):
        with patch('sys.argv', ['seed_mcp']), \
                patch.object(mcp, 'oc_output', side_effect=AssertionError('cluster')), \
                patch('sys.stdout', io.StringIO()):
            mcp.main()


if __name__ == '__main__':
    unittest.main()
