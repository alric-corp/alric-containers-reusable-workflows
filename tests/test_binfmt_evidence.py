"""Execute the hosted HGC-01 producer step with controlled Docker responses."""
import copy
import hashlib
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
OTHER_DIGEST = 'sha256:' + 'b' * 64
# Docker answers observed in the hosted post-merge run 37614260125 of alric-containers-image-base
# (job "Compile certs with melange"); RepoDigests follows the reviewed pin.
HOSTED_WORLD = dict(
    image=dict(Id='sha256:15935d0512bf0a8e5afa1df990d971cf97a619ede08fa60f2d5966a62de6d6b7', RepoTags=[],
               RepoDigests=['tonistiigi/binfmt@' + DIGEST], Os='linux', Architecture='amd64'),
    version_stdout='', version_stderr='binfmt/e29e7d7 qemu/v10.2.3 go/1.26.4\n',
    supported=['linux/amd64', 'linux/amd64/v2', 'linux/amd64/v3', 'linux/amd64/v4', 'linux/arm64', 'linux/386'],
    emulators=['llvm-16-runtime.binfmt', 'llvm-17-runtime.binfmt', 'llvm-18-runtime.binfmt', 'python3.12',
               'qemu-aarch64'])
HOSTED_IDENTITY = dict(
    GITHUB_REPOSITORY='alric-corp/alric-containers-image-base',
    GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/workflow.yml@refs/heads/develop',
    GITHUB_REF='refs/heads/develop', GITHUB_SHA='5d32e51529bfdbbcfaab58f836c8bfeafcad929f',
    GITHUB_RUN_ID='37614260125', GITHUB_RUN_ATTEMPT='1')
# Original receipt bytes in that run's melange-repo artifact (11478737338). Changing BINFMT_IMAGE
# requires a new hosted proof; refresh this fixture from that run, never from a local rerun.
HOSTED_RECEIPT_SHA256 = '5be0e7168c642200eadfaa9f93296e79ad31e190ea020d99c70525560beb3c99'
FAKE_DOCKER = r'''import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
reference = os.environ['BINFMT_IMAGE']
world = json.loads(Path(os.environ['TEST_DOCKER_WORLD']).read_text())
with Path('docker-calls.jsonl').open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args == ['image', 'inspect', reference]:
    print(json.dumps([world['image']]))
elif args == ['run', '--rm', reference, '--version']:
    sys.stdout.write(world['version_stdout'])
    sys.stderr.write(world['version_stderr'])
elif args == ['run', '--rm', '--privileged', reference]:
    print(json.dumps(dict(supported=world['supported'], emulators=world['emulators']), indent=2))
else:
    sys.exit('unexpected Docker command: ' + repr(args))
'''


def world(**changes):
    """Hosted Docker world with independent overrides; image keys change `docker image inspect`."""
    result = copy.deepcopy(HOSTED_WORLD)
    for key, value in changes.items():
        target = result['image'] if key in result['image'] else result
        if key not in target:
            raise KeyError(key)
        target[key] = value
    return result


class BinfmtProducerTests(unittest.TestCase):
    def run_producer(self, docker_world=None, **overrides):
        steps = WORKFLOW['jobs']['melange-bundle']['steps']
        step = next(s for s in steps if s.get('name') == 'Record structured binfmt evidence')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'melange').mkdir()
            (root / 'bin').mkdir()
            docker = root / 'bin/docker'
            docker.write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            docker.chmod(0o755)
            (root / 'world.json').write_text(json.dumps(HOSTED_WORLD if docker_world is None else docker_world))
            environment = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                               TEST_DOCKER_WORLD=str(root / 'world.json'),
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

    def assert_rejected(self, reason, docker_world=None, **overrides):
        """No receipt, and the failure names the property under test rather than an earlier check."""
        result, raw, _ = self.run_producer(docker_world, **overrides)
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(raw)
        self.assertIn(reason, result.stderr)

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

    def test_hosted_post_merge_receipt_is_reproduced_byte_for_byte(self):
        result, raw, _ = self.run_producer(**HOSTED_IDENTITY)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['producer'], dict(repository='alric-corp/alric-containers-image-base',
                         ref='refs/heads/develop', source_sha='5d32e51529bfdbbcfaab58f836c8bfeafcad929f',
                         run_id=37614260125, run_attempt=1, release_id='r37614260125-a1',
                         workflow='.github/workflows/workflow.yml'))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), HOSTED_RECEIPT_SHA256)

    def test_unpinned_latest_and_digest_mismatch_fail_without_a_receipt(self):
        cases = [(None, dict(BINFMT_IMAGE='docker.io/tonistiigi/binfmt:latest')),
                 (None, dict(BINFMT_IMAGE=REF.split('@', 1)[0])),
                 (None, dict(BINFMT_IMAGE=REF.replace('qemu-v10.2.3-68', 'latest'))),
                 (world(RepoDigests=['tonistiigi/binfmt@sha256:' + '6' * 64]), {}), (world(RepoDigests=[]), {})]
        for docker_world, overrides in cases:
            with self.subTest(overrides=overrides, docker_world=docker_world and docker_world['image']['RepoDigests']):
                result, raw, _ = self.run_producer(docker_world, **overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_resolved_digests_must_equal_exactly_the_requested_pin(self):
        for digests in (['tonistiigi/binfmt@' + DIGEST, 'tonistiigi/binfmt@' + OTHER_DIGEST],
                        ['docker.io/tonistiigi/binfmt@' + DIGEST, 'tonistiigi/binfmt@' + OTHER_DIGEST]):
            with self.subTest(digests=digests):
                self.assert_rejected('resolved binfmt digest differs from the requested digest',
                                     world(RepoDigests=digests))
        # Both registry spellings of the same pinned digest are one resolution, not ambiguity.
        result, raw, _ = self.run_producer(world(RepoDigests=['docker.io/tonistiigi/binfmt@' + DIGEST,
                                                              'tonistiigi/binfmt@' + DIGEST]))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['resolved_digest'], DIGEST)

    def test_pin_resolved_only_by_another_repository_is_rejected(self):
        for digests in (['other/repository@' + DIGEST], ['docker.io/library/binfmt@' + DIGEST],
                        ['ghcr.io/tonistiigi/binfmt@' + DIGEST]):
            with self.subTest(digests=digests):
                self.assert_rejected('resolved binfmt digest differs from the requested digest',
                                     world(RepoDigests=digests))

    def test_exactly_one_qemu_version_must_be_observed(self):
        hosted = HOSTED_WORLD['version_stderr']
        for changes in (dict(version_stderr=hosted + 'binfmt/e29e7d7 qemu/v9.2.4 go/1.26.4\n'),
                        dict(version_stdout='qemu/v9.2.4\n')):
            with self.subTest(changes=changes):
                self.assert_rejected('binfmt did not report one QEMU version', world(**changes))

    def test_missing_runtime_version_or_arm64_registration_fails(self):
        for docker_world in (world(version_stderr='binfmt/e29e7d7 qemu/unknown go/1.26.4\n'), world(emulators=[])):
            with self.subTest(docker_world=docker_world):
                result, raw, _ = self.run_producer(docker_world)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_linux_arm64_must_be_a_supported_platform(self):
        for supported in (['linux/amd64'], [p for p in HOSTED_WORLD['supported'] if p != 'linux/arm64']):
            with self.subTest(supported=supported):
                self.assert_rejected('required arm64 emulation is unavailable', world(supported=supported))

    def test_qemu_aarch64_must_be_a_registered_emulator(self):
        for emulators in ([], [e for e in HOSTED_WORLD['emulators'] if e != 'qemu-aarch64']):
            with self.subTest(emulators=emulators):
                self.assert_rejected('required arm64 emulation is unavailable', world(emulators=emulators))

    def test_binfmt_image_must_be_a_linux_variant(self):
        self.assert_rejected('the hosted binfmt producer must run on linux/amd64', world(Os='windows'))

    def test_binfmt_image_must_be_the_amd64_host_variant(self):
        self.assert_rejected('the hosted binfmt producer must run on linux/amd64', world(Architecture='arm64'))

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

    def test_workflow_identity_must_belong_to_the_caller_repository(self):
        caller = 'alric-corp/alric-containers-image-base'
        # Same length as the caller, so the path parsing alone cannot reject them.
        for foreign in ('mallory-co/alric-containers-image-base', 'alric-corp/alric-containers-image-basf'):
            with self.subTest(foreign=foreign):
                self.assertEqual(len(foreign), len(caller))
                self.assert_rejected('unexpected workflow repository', GITHUB_REPOSITORY=caller,
                                     GITHUB_WORKFLOW_REF=foreign + '/.github/workflows/workflow.yml@refs/heads/main')

    def test_pull_request_runs_keep_their_real_identity(self):
        result, raw, _ = self.run_producer(GITHUB_REF='refs/pull/18/merge')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['producer']['ref'], 'refs/pull/18/merge')

    def test_case_normalization_preserves_the_actual_caller_workflow(self):
        result, raw, _ = self.run_producer(GITHUB_REPOSITORY='Example/Caller',
                                          GITHUB_WORKFLOW_REF='Example/Caller/.github/workflows/workflow.yml@refs/heads/main')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(raw)['producer']['repository'], 'example/caller')

    def test_producer_repository_must_match_the_contract(self):
        for repository in ('alric corp/caller', 'owner/repo/extra', '/caller'):
            with self.subTest(repository=repository):
                self.assert_rejected('invalid producer repository', GITHUB_REPOSITORY=repository,
                                     GITHUB_WORKFLOW_REF=repository + '/.github/workflows/workflow.yml@refs/heads/main')

    def test_invalid_source_or_run_metadata_fails(self):
        for overrides in (dict(GITHUB_SHA='short'), dict(GITHUB_RUN_ID='0'), dict(GITHUB_RUN_ATTEMPT='0'),
                          dict(GITHUB_REF='main')):
            with self.subTest(overrides=overrides):
                result, raw, _ = self.run_producer(**overrides)
                self.assertNotEqual(result.returncode, 0)
                self.assertIsNone(raw)

    def test_producer_ref_cannot_contain_parent_segments(self):
        for ref in ('refs/heads/a/../b', 'refs/tags/../heads/develop'):
            with self.subTest(ref=ref):
                self.assert_rejected('invalid producer ref', GITHUB_REF=ref)

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
