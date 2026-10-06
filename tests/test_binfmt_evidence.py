"""Execute the hosted HGC-01 producer step with controlled Docker responses."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/validate-apko-images.yml').read_text())
REF = WORKFLOW['env']['BINFMT_IMAGE']
DIGEST = REF.rsplit('@', 1)[1]
FAKE_DOCKER = r'''import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
reference = os.environ['BINFMT_IMAGE']
with Path('docker-calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args == ['image', 'inspect', reference]:
    digest = os.environ.get('TEST_RESOLVED_DIGEST', reference.rsplit('@', 1)[1])
    digests = ['tonistiigi/binfmt@' + digest] if digest else []
    print(json.dumps([dict(RepoDigests=digests, Os='linux', Architecture='amd64')]))
elif args == ['run', '--rm', reference, '--version']:
    print('binfmt/e29e7d72 qemu/' + os.environ.get('TEST_QEMU_VERSION', 'v10.2.3') + ' go/1.25.0', file=sys.stderr)
elif args == ['run', '--rm', '--privileged', reference]:
    supported = ['linux/amd64', 'linux/arm64']
    emulators = ['qemu-aarch64'] if os.environ.get('TEST_EMULATION_MISSING') != 'yes' else []
    print(json.dumps(dict(supported=supported, emulators=emulators)))
else:
    sys.exit('unexpected Docker command: ' + repr(args))
'''


class BinfmtProducerTests(unittest.TestCase):
    def run_producer(self, **overrides):
        steps = WORKFLOW['jobs']['melange-bundle']['steps']
        step = next(s for s in steps if s.get('name') == 'Record structured binfmt evidence')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'melange').mkdir()
            (root / 'bin').mkdir()
            docker = root / 'bin/docker'
            docker.write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            docker.chmod(0o755)
            environment = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                               BINFMT_IMAGE=REF, GITHUB_REPOSITORY='alric-corp/alric-containers-image-base',
                               GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/workflow.yml@refs/heads/feat/hgc',
                               GITHUB_REF='refs/heads/feat/hgc', GITHUB_SHA='a' * 40,
                               GITHUB_RUN_ID='12000', GITHUB_RUN_ATTEMPT='2')
            environment.update(overrides)
            result = subprocess.run(['bash', '-c', step['run']], cwd=root, env=environment, capture_output=True, text=True)
            path = root / 'melange/binfmt-evidence.json'
            raw = path.read_bytes() if path.exists() else None
            calls_path = root / 'docker-calls.jsonl'
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            return result, raw, calls

    def test_qemu_action_and_receipt_use_the_same_immutable_pin(self):
        qemu = next(s for s in WORKFLOW['jobs']['melange-bundle']['steps']
                    if s.get('uses', '').startswith('docker/setup-qemu-action@'))
        self.assertEqual(qemu['with'], dict(platforms='arm64', image='${{ env.BINFMT_IMAGE }}'))
        self.assertRegex(REF, r'^docker\.io/tonistiigi/binfmt:qemu-v[0-9.]+-[0-9]+@sha256:[0-9a-f]{64}$')
        self.assertNotIn('latest', REF)

    def test_producer_records_exact_runtime_identity_in_canonical_bytes(self):
        result, raw, calls = self.run_producer()
        self.assertEqual(result.returncode, 0, result.stderr)
        document = json.loads(raw)
        self.assertEqual(set(document), {'schema_version', 'kind', 'producer', 'requested_ref', 'requested_digest',
                                         'resolved_digest', 'qemu_version', 'architecture_contract'})
        self.assertEqual(document['requested_ref'], REF)
        self.assertEqual(document['requested_digest'], DIGEST)
        self.assertEqual(document['resolved_digest'], DIGEST)
        self.assertEqual(document['qemu_version'], '10.2.3')
        self.assertEqual(document['producer'], dict(repository='alric-corp/alric-containers-image-base',
                         ref='refs/heads/feat/hgc', source_sha='a' * 40, run_id=12000, run_attempt=2,
                         release_id='r12000-a2', workflow='.github/workflows/workflow.yml'))
        self.assertEqual(document['architecture_contract'], dict(host_platform='linux/amd64',
                         emulated_platforms=['linux/arm64'], registered_emulators=['qemu-aarch64'],
                         build_architectures=['aarch64', 'x86_64']))
        self.assertEqual(raw, json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        self.assertEqual(calls, [['image', 'inspect', REF], ['run', '--rm', REF, '--version'],
                                 ['run', '--rm', '--privileged', REF]])

    def test_unpinned_latest_and_digest_mismatch_fail_without_a_receipt(self):
        cases = [dict(BINFMT_IMAGE='docker.io/tonistiigi/binfmt:latest'),
                 dict(BINFMT_IMAGE=REF.split('@', 1)[0]),
                 dict(BINFMT_IMAGE=REF.replace('qemu-v10.2.3-68', 'latest')),
                 dict(TEST_RESOLVED_DIGEST='sha256:' + '6' * 64), dict(TEST_RESOLVED_DIGEST='')]
        for overrides in cases:
            with self.subTest(overrides=overrides):
                result, raw, _ = self.run_producer(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_missing_runtime_version_or_arm64_registration_fails(self):
        for overrides in (dict(TEST_QEMU_VERSION='unknown'), dict(TEST_EMULATION_MISSING='yes')):
            with self.subTest(overrides=overrides):
                result, raw, _ = self.run_producer(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_receipt_uses_exact_attempt_and_workflow_not_an_invented_identity(self):
        result, raw, _ = self.run_producer(GITHUB_RUN_ATTEMPT='3',
            GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/catalog-certification.yml@refs/heads/feat/hgc')
        self.assertEqual(result.returncode, 0, result.stderr)
        producer = json.loads(raw)['producer']
        self.assertEqual(producer['release_id'], 'r12000-a3')
        self.assertEqual(producer['workflow'], '.github/workflows/catalog-certification.yml')
        result, raw, _ = self.run_producer(GITHUB_WORKFLOW_REF='unrelated/repo/.github/workflows/workflow.yml@main')
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(raw)

    def test_pull_request_runs_keep_their_real_identity(self):
        result, raw, _ = self.run_producer(GITHUB_REF='refs/pull/18/merge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['producer']['ref'], 'refs/pull/18/merge')

    def test_case_normalization_preserves_the_actual_caller_workflow(self):
        result, raw, _ = self.run_producer(GITHUB_REPOSITORY='Example/Caller',
                                          GITHUB_WORKFLOW_REF='Example/Caller/.github/workflows/workflow.yml@refs/heads/main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['producer']['repository'], 'example/caller')

    def test_invalid_source_or_run_metadata_fails(self):
        for overrides in (dict(GITHUB_SHA='short'), dict(GITHUB_RUN_ID='0'), dict(GITHUB_RUN_ATTEMPT='0'),
                          dict(GITHUB_REF='main')):
            with self.subTest(overrides=overrides):
                result, raw, _ = self.run_producer(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_receipt_is_preserved_with_original_candidate_artifacts(self):
        bundle_steps = WORKFLOW['jobs']['melange-bundle']['steps']
        producer = next(s for s in bundle_steps if s.get('name') == 'Record structured binfmt evidence')
        setup = next(s for s in bundle_steps if s.get('name') == 'Set up QEMU')
        build = next(s for s in bundle_steps if s.get('name') == 'Build CA package (amd64 + arm64)')
        self.assertLess(bundle_steps.index(setup), bundle_steps.index(producer))
        self.assertLess(bundle_steps.index(producer), bundle_steps.index(build))
        upload = next(s for s in bundle_steps if s.get('name') == 'Upload melange repository')
        self.assertIn('melange/binfmt-evidence.json', upload['with']['path'])
        steps = WORKFLOW['jobs']['validate']['steps']
        build = next(s for s in steps if s.get('name') == 'Build multi-architecture OCI artifact once')
        self.assertIn('cp melange-repo/binfmt-evidence.json "${FRAMEWORK}.oci/binfmt-evidence.json"', build['run'])
        replay = next(s for s in steps if s.get('name') == 'Preserve SBOMs and replay inputs')
        self.assertIn('${{ matrix.framework }}.oci/binfmt-evidence.json', replay['with']['path'])


if __name__ == '__main__':
    unittest.main()
