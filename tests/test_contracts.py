import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_contracts import check, steps_errors, workflow_errors


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.validation = yaml.safe_load(
            (ROOT / '.github/workflows/validate-apko-images.yml').read_text())

    def test_repository_contracts(self):
        self.assertEqual(check(), [])

    def test_pr_validation_cannot_gain_oidc_or_write_permissions(self):
        for permissions in ({'contents': 'write'}, {'contents': 'read', 'id-token': 'write'}):
            document = copy.deepcopy(self.validation)
            document['permissions'] = permissions
            self.assertTrue(workflow_errors(document, reusable=True))

    def test_shared_workflows_cannot_add_triggers(self):
        self.validation[True]['workflow_dispatch'] = {}
        self.assertTrue(workflow_errors(self.validation, reusable=True))

    def test_checkout_cannot_switch_to_shared_repository_or_persist_token(self):
        for options in ({'persist-credentials': True},
                        {'persist-credentials': False, 'repository': 'owner/shared'},
                        {'persist-credentials': False, 'ref': 'main'}):
            self.assertTrue(steps_errors([{'uses': 'actions/checkout@' + 'a' * 40,
                                          'with': options}]))

    def test_scan_failure_cannot_produce_validated_artifact(self):
        steps = self.validation['jobs']['validate']['steps']
        upload = copy.deepcopy(steps[-1])
        upload['if'] = 'always()'
        self.assertTrue(steps_errors([upload]))
        scan = next(s for s in steps if s.get('name') == 'Scan both architectures')
        self.assertFalse(scan.get('continue-on-error'))
        self.assertLess(steps.index(scan), len(steps) - 1)

    def test_shell_and_input_injection_rules(self):
        self.assertTrue(steps_errors([{'run': 'echo ok'}], composite=True))
        self.assertTrue(steps_errors([{'run': 'echo "${{ inputs.command }}"', 'shell': 'bash'}]))
        self.assertFalse(steps_errors([{'run': 'echo "$FRAMEWORK"', 'shell': 'bash',
                                      'env': {'FRAMEWORK': '${{ inputs.framework }}'}}]))

    def test_floating_dependency_is_rejected(self):
        self.assertTrue(steps_errors([{'uses': 'owner/action@main'}]))

    def test_retention_and_oci_protocol(self):
        expected = {'melange-repo': 1, 'build-scans-': 30, 'validated-oci-': 3, 'runtime-': 30}
        found = {}
        for name in ('validate-apko-images.yml', 'test-runtime-images.yml'):
            document = yaml.safe_load((ROOT / '.github/workflows' / name).read_text())
            for job in document['jobs'].values():
                for step in job['steps']:
                    if step.get('uses', '').startswith('actions/upload-artifact@'):
                        options = step['with']
                        found[options['name'].split('${{')[0]] = options['retention-days']
        self.assertEqual(found, expected)

    def test_trivy_version_has_one_governed_definition(self):
        action = yaml.safe_load((ROOT / 'actions/setup-trivy/action.yml').read_text())
        installer, verify = action['runs']['steps']
        self.assertEqual(installer['with']['version'], '${{ env.TRIVY_VERSION }}')
        self.assertEqual(verify['shell'], 'bash')
        self.assertIn('trivy --version', verify['run'])
        self.assertNotIn('continue-on-error', installer)
        self.assertNotIn('continue-on-error', verify)
        self.assertNotIn('TRIVY_VERSION', self.validation.get('env', {}))

    def run_runtime_guard(self, module, framework='nodejs22', run_id='123'):
        document = yaml.safe_load((ROOT / '.github/workflows/test-runtime-images.yml').read_text())
        step = next(s for s in document['jobs']['runtime']['steps'] if s.get('id') == 'contract')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            scripts = directory / '.github/scripts'
            scripts.mkdir(parents=True)
            (scripts / 'runtime_images.py').write_text(module)
            output = directory / 'output.txt'
            result = subprocess.run(['bash', '-c', step['run']], cwd=directory,
                                    env=dict(os.environ, FRAMEWORK=framework, ARTIFACT_RUN_ID=run_id,
                                             GITHUB_OUTPUT=str(output)), text=True, capture_output=True)
            return result, output.read_text() if output.exists() else ''

    def test_runtime_guard_accepts_original_and_compiled_consumer_apis(self):
        result, output = self.run_runtime_guard('def runtime(f): return ("node", "probe.cjs")\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dev=\n', output)
        module = ('def supported(f): return "compiled"\n'
                  'def project(f): return ("project", "go1-26-dev", "go")\n')
        result, output = self.run_runtime_guard(module, framework='go1-26')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('dev=go1-26-dev\n', output)

    def test_runtime_guard_rejects_bad_catalog_input_and_cross_run_injection(self):
        rejected = 'def runtime(f): raise ValueError("framework outside catalog")\n'
        result, output = self.run_runtime_guard(rejected, framework='../bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')
        result, output = self.run_runtime_guard('def runtime(f): return "node"\n', run_id='123\ndev=bad')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(output, '')


if __name__ == '__main__':
    unittest.main()
