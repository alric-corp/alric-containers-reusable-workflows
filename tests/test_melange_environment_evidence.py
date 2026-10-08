"""Execute the HGC-02 Melange environment producer steps against hosted outputs and controlled Docker answers.

Fixtures under tests/fixtures/melange-environment come from real sources:
- hosted/: melange-repo artifact of alric-containers-image-base run 37614260125 (packages and ephemeral public key);
- hosted/query-lock-x86_64.txt and source-query.txt: `melange query` output of the pinned image for the embedded
  .melange.yaml of the hosted x86_64 APK and for the source configuration;
- source/: the Melange source directory of alric-containers-image-base at 5d32e51.
"""
import ast
import base64
import copy
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/melange-environment'
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/validate-apko-images.yml').read_text())
STEPS = WORKFLOW['jobs']['melange-bundle']['steps']
CAPTURE = next(s for s in STEPS if s.get('name') == 'Capture Melange environment inputs')
FINALIZE = next(s for s in STEPS if s.get('name') == 'Record structured Melange environment evidence')
REF = WORKFLOW['env']['MELANGE_IMAGE']
DIGEST = REF.rsplit('@', 1)[1]
OTHER_DIGEST = 'sha256:' + 'c' * 64
CONFIG = 'image-base-ca-certificates.yaml'
SIGNATURE = '.SIGN.RSA256.melange.rsa.pub'
ARCHS = ('aarch64', 'x86_64')
# `melange version --json` keys observed from the pinned image; values from the hosted run's melange-version.txt.
HOSTED_VERSION = dict(gitVersion='v0.61.2', gitCommit='1fa4784b9ec0dbe1c9ebfd61be52a92056038e1c', gitTreeState='dirty',
                      buildDate='2026-09-30T19:54:36Z', goVersion='go1.27.1', compiler='gc', platform='linux/amd64')
HOSTED_WORLD = dict(image=dict(RepoTags=[], RepoDigests=['cgr.dev/chainguard/melange@' + DIGEST], Os='linux',
                               Architecture='amd64'),
                    version_stdout=json.dumps(HOSTED_VERSION, indent=2) + '\n')
HOSTED_PACKAGES = ['busybox=1.38.0-r2', 'ca-certificates-bundle=20260909-r2', 'glibc-2.44-locale-posix=2.44-r8',
                   'glibc-2.44=2.44-r8', 'ld-linux-2.44=2.44-r8', 'libcrypt1-2.44=2.44-r8', 'libgcc=16.2.0-r1',
                   'libxcrypt=4.5.2-r5', 'wolfi-baselayout=20230201-r30']
FAKE_DOCKER = r'''import json
import os
import sys
from pathlib import Path

import yaml

args = sys.argv[1:]
reference = os.environ['MELANGE_IMAGE']
world = json.loads(Path(os.environ['TEST_DOCKER_WORLD']).read_text())
with Path(os.environ['TEST_DOCKER_CALLS']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if args == ['image', 'inspect', reference]:
    print(json.dumps([world['image']]))
elif args == ['run', '--rm', reference, 'version', '--json']:
    sys.stdout.write(world['version_stdout'])
elif (len(args) == 10 and args[:3] == ['run', '--rm', '-v'] and args[3].endswith(':/query:ro')
      and args[4:7] == ['-w', '/query', reference] and args[7] == 'query'
      and all(clause in args[9] for clause in ('{{range .Environment.Contents.Packages}}package\t',
                                               '{{range .Environment.Contents.Repositories}}repository\t',
                                               '{{range .Environment.Contents.Keyring}}keyring\t',
                                               '{{range .Environment.Archs}}arch\t', 'epoch\t{{.Package.Epoch}}'))):
    # Same rendering as the pinned `melange query` for this template (see test_fake_query_matches_pinned_melange).
    document = yaml.safe_load(Path(args[3][:-len(':/query:ro')], args[8]).read_text())
    environment, package = document.get('environment') or {}, document.get('package') or {}
    contents = environment.get('contents') or {}
    for key, values in (('package', contents.get('packages')), ('repository', contents.get('repositories')),
                        ('keyring', contents.get('keyring')), ('arch', environment.get('archs'))):
        for value in values or []:
            sys.stdout.write(f'{key}\t{value}\n')
    sys.stdout.write(f"name\t{package.get('name', '')}\nversion\t{package.get('version', '')}\n"
                     f"epoch\t{package.get('epoch', 0)}\n")
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


def streams(raw):
    parts, offset = [], 0
    while offset < len(raw):
        stream = zlib.decompressobj(31)
        stream.decompress(raw[offset:])
        used = len(raw) - offset - len(stream.unused_data)
        parts.append(raw[offset:offset + used])
        offset += used
    return parts


def members(stream):
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(stream))) as archive:
        return {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers() if entry.isfile()}


def tar_gz(files):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return gzip.compress(buffer.getvalue(), mtime=0)


def rebuild(raw, signature=None, control=None, data=None, keep_datahash=False):
    """Re-emit an APK with edited sections; datahash follows the data unless the test breaks it."""
    sig_files, control_files, data_files = (members(part) for part in streams(raw))
    for files, edit in ((sig_files, signature), (control_files, control), (data_files, data)):
        if edit:
            edit(files)
    data_stream = tar_gz(data_files)
    if not keep_datahash:
        info = control_files['.PKGINFO'].decode().splitlines()
        info = [f'datahash = {hashlib.sha256(data_stream).hexdigest()}' if line.startswith('datahash = ') else line
                for line in info]
        control_files['.PKGINFO'] = ('\n'.join(info) + '\n').encode()
    return tar_gz(sig_files) + tar_gz(control_files) + data_stream


def index(apks, extra=''):
    entries = []
    for raw in apks:
        parts = streams(raw)
        info = dict(line.split(' = ', 1) for line in members(parts[1])['.PKGINFO'].decode().splitlines()
                    if ' = ' in line)
        checksum = 'Q1' + base64.b64encode(hashlib.sha1(parts[1]).digest()).decode()
        entries.append(f"C:{checksum}\nP:{info['pkgname']}\nV:{info['pkgver']}\nA:{info['arch']}\nS:{len(raw)}\n")
    return tar_gz({SIGNATURE: b'signature', 'APKINDEX': ('\n'.join(entries) + extra).encode(), 'DESCRIPTION': b''})


def edit_text(name, old, new):
    def apply(files):
        text = files[name].decode()
        if old not in text:
            raise AssertionError(f'{old!r} not in {name}')
        files[name] = text.replace(old, new).encode()
    return apply


class MelangeEnvironmentEvidenceTests(unittest.TestCase):
    def run_producer(self, docker_world=None, mutate_source=None, between=None, build=None, **overrides):
        """Capture inputs, simulate the build with hosted outputs (optionally edited), then finalize."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'melange'
            shutil.copytree(FIXTURES / 'source', source)
            shutil.copy(FIXTURES / 'hosted/melange.rsa.pub', source / 'melange.rsa.pub')
            (source / 'melange.rsa').write_text('ephemeral private key (not hashed)')
            (source / 'melange-version.txt').write_text('melange\n\nGitVersion:    v0.61.2\n')
            (source / 'binfmt-evidence.json').write_text('{"kind":"binfmt-evidence"}')
            if mutate_source:
                mutate_source(source)
            (root / 'bin').mkdir()
            (root / 'temp').mkdir()
            docker = root / 'bin/docker'
            docker.write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            docker.chmod(0o755)
            (root / 'world.json').write_text(json.dumps(HOSTED_WORLD if docker_world is None else docker_world))
            environment = dict(os.environ, PATH=str(root / 'bin') + os.pathsep + os.environ['PATH'],
                               TEST_DOCKER_WORLD=str(root / 'world.json'), TEST_DOCKER_CALLS=str(root / 'calls.jsonl'),
                               RUNNER_TEMP=str(root / 'temp'), MELANGE_IMAGE=REF, MELANGE_CONFIG=CONFIG,
                               GITHUB_REPOSITORY='alric-corp/alric-containers-image-base',
                               GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/workflow.yml@refs/heads/develop',
                               GITHUB_REF='refs/heads/develop', GITHUB_SHA='5d32e51529bfdbbcfaab58f836c8bfeafcad929f',
                               GITHUB_RUN_ID='37614260125', GITHUB_RUN_ATTEMPT='1')
            environment.update(overrides)
            capture = subprocess.run(['bash', '-c', CAPTURE['run']], cwd=root, env=environment,
                                     capture_output=True, text=True)
            finalize = None
            if capture.returncode == 0:
                shutil.copytree(FIXTURES / 'hosted/packages', source / 'packages')
                if build:
                    build(source / 'packages')
                if between:
                    between(source)
                finalize = subprocess.run(['bash', '-c', FINALIZE['run']], cwd=root, env=environment,
                                          capture_output=True, text=True)
            path = source / 'melange-environment-evidence.json'
            raw = path.read_bytes() if path.exists() else None
            return capture, finalize, raw

    def assert_produced(self, *args, **kwargs):
        capture, finalize, raw = self.run_producer(*args, **kwargs)
        self.assertEqual(capture.returncode, 0, capture.stderr)
        self.assertEqual(finalize.returncode, 0, finalize.stderr)
        return json.loads(raw), raw

    def assert_rejected(self, reason, *args, **kwargs):
        """No receipt, and the failing step names the property under test."""
        capture, finalize, raw = self.run_producer(*args, **kwargs)
        failed = capture if capture.returncode else finalize
        self.assertNotEqual(failed.returncode, 0)
        self.assertIsNone(raw)
        self.assertIn(reason, failed.stderr)

    @staticmethod
    def edit_apk(arch, rebuild_index=True, **edits):
        def apply(packages):
            directory = packages / arch
            apk = next(directory.glob('*.apk'))
            raw = rebuild(apk.read_bytes(), **edits)
            info = dict(line.split(' = ', 1) for line in members(streams(raw)[1])['.PKGINFO'].decode().splitlines()
                        if ' = ' in line)
            apk.unlink()
            target = directory / f"{info['pkgname']}-{info['pkgver']}.apk"
            target.write_bytes(raw)
            if rebuild_index:
                (directory / 'APKINDEX.tar.gz').write_bytes(index([raw]))
        return apply

    def test_steps_bracket_the_build_with_the_pinned_melange_image(self):
        self.assertRegex(REF, r'^cgr\.dev/chainguard/melange@sha256:[0-9a-f]{64}$')
        names = [s.get('name') for s in STEPS]
        order = ['Generate ephemeral melange signing key', 'Capture Melange environment inputs',
                 'Build CA package (amd64 + arm64)', 'Record structured Melange environment evidence',
                 'Upload melange repository']
        self.assertEqual([names.index(name) for name in order], sorted(names.index(name) for name in order))
        build = next(s for s in STEPS if s.get('name') == 'Build CA package (amd64 + arm64)')
        self.assertIn('for ARCH in x86_64 aarch64; do', build['run'])
        self.assertIn("TARGETS = {'aarch64': 'arm64', 'x86_64': 'amd64'}", FINALIZE['run'])

    def test_fake_query_matches_pinned_melange(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            lock = root / '.melange.yaml'
            raw = (FIXTURES / 'hosted/packages/x86_64/image-base-ca-certificates-1.0.0-r0.apk').read_bytes()
            lock.write_bytes(members(streams(raw)[1])['.melange.yaml'])
            shutil.copy(FIXTURES / f'source/{CONFIG}', root / CONFIG)
            fake = root / 'docker'
            fake.write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            fake.chmod(0o755)
            (root / 'world.json').write_text(json.dumps(HOSTED_WORLD))
            template = FINALIZE['run'].split("QUERY = (", 1)[1].split("\n\n", 1)[0]
            template = ast.literal_eval('(' + template)  # the literal template string from the workflow
            environment = dict(os.environ, MELANGE_IMAGE=REF, TEST_DOCKER_WORLD=str(root / 'world.json'),
                               TEST_DOCKER_CALLS=str(root / 'calls.jsonl'))
            for name, expected in ((lock.name, 'hosted/query-lock-x86_64.txt'), (CONFIG, 'source-query.txt')):
                with self.subTest(file=name):
                    output = subprocess.run([str(fake), 'run', '--rm', '-v', f'{root}:/query:ro', '-w', '/query', REF,
                                             'query', name, template], env=environment, capture_output=True,
                                            text=True, check=True).stdout
                    self.assertEqual(output, (FIXTURES / expected).read_text())

    def test_hosted_outputs_produce_canonical_environment_evidence(self):
        document, raw = self.assert_produced()
        self.assertEqual(raw, json.dumps(document, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())
        self.assertEqual(set(document), {'schema_version', 'kind', 'producer', 'environment', 'environment_digest',
                                         'outputs', 'signing_key', 'generated_workspace_files'})
        self.assertEqual((document['schema_version'], document['kind']), (1, 'melange-environment-evidence'))
        environment = document['environment']
        self.assertEqual(set(environment), {'tool', 'host_platform', 'configuration', 'source_files', 'repositories',
                                            'keyring', 'targets'})
        self.assertEqual(environment['tool'], dict(
            requested_ref=REF, requested_digest=DIGEST, resolved_digest=DIGEST,
            version=dict(git_version='v0.61.2', git_commit='1fa4784b9ec0dbe1c9ebfd61be52a92056038e1c',
                         git_tree_state='dirty', build_date='2026-09-30T19:54:36Z', go_version='go1.27.1',
                         compiler='gc', platform='linux/amd64')))
        self.assertEqual(environment['host_platform'], 'linux/amd64')
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        self.assertEqual(environment['configuration'], dict(
            path=f'melange/{CONFIG}', sha256=sha(FIXTURES / f'source/{CONFIG}'),
            package=dict(name='image-base-ca-certificates', version='1.0.0', epoch=0)))
        self.assertEqual(environment['source_files'], [
            dict(path='melange/' + path.relative_to(FIXTURES / 'source').as_posix(), sha256=sha(path))
            for path in sorted((FIXTURES / 'source').rglob('*')) if path.is_file()])
        self.assertEqual(environment['repositories'], ['https://packages.wolfi.dev/os'])
        self.assertEqual(environment['keyring'], [dict(path='melange/keys/wolfi-signing.rsa.pub',
                                                       sha256=sha(FIXTURES / 'source/keys/wolfi-signing.rsa.pub'))])
        expected = sorted((dict(zip(('name', 'version'), item.split('=', 1))) for item in HOSTED_PACKAGES),
                          key=lambda item: item['name'])
        self.assertEqual(environment['targets'], dict(aarch64=dict(environment_arch='arm64', packages=expected),
                                                      x86_64=dict(environment_arch='amd64', packages=expected)))
        self.assertEqual(document['environment_digest'], 'sha256:' + hashlib.sha256(json.dumps(
            environment, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest())
        for arch in ARCHS:
            apk = FIXTURES / f'hosted/packages/{arch}/image-base-ca-certificates-1.0.0-r0.apk'
            parts = streams(apk.read_bytes())
            with tarfile.open(FIXTURES / f'hosted/packages/{arch}/APKINDEX.tar.gz', 'r:gz') as archive:
                checksum = next(line[2:] for line in archive.extractfile('APKINDEX').read().decode().splitlines()
                                if line.startswith('C:'))
            self.assertEqual(document['outputs'][arch], [dict(
                path=f'melange/packages/{arch}/{apk.name}', sha256=sha(apk), size=apk.stat().st_size,
                name='image-base-ca-certificates', version='1.0.0-r0', arch=arch, origin='image-base-ca-certificates',
                datahash=hashlib.sha256(parts[2]).hexdigest(), control_checksum=checksum)])
        self.assertEqual(document['producer'], dict(repository='alric-corp/alric-containers-image-base',
                         ref='refs/heads/develop', source_sha='5d32e51529bfdbbcfaab58f836c8bfeafcad929f',
                         run_id=37614260125, run_attempt=1, release_id='r37614260125-a1',
                         workflow='.github/workflows/workflow.yml'))
        self.assertEqual(document['signing_key'], dict(path='melange/melange.rsa.pub',
                                                       sha256=sha(FIXTURES / 'hosted/melange.rsa.pub')))
        self.assertEqual(document['generated_workspace_files'], ['melange/binfmt-evidence.json',
                         'melange/melange-version.txt', 'melange/melange.rsa', 'melange/melange.rsa.pub'])

    def test_environment_digest_ignores_run_identity_and_signing_key(self):
        first, _ = self.assert_produced()
        second, _ = self.assert_produced(
            GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='3', GITHUB_REF='refs/pull/7/merge', GITHUB_SHA='b' * 40,
            mutate_source=lambda source: (source / 'melange.rsa.pub').write_text('another ephemeral key'))
        self.assertEqual(first['environment_digest'], second['environment_digest'])
        self.assertNotEqual(first['producer'], second['producer'])
        self.assertNotEqual(first['signing_key'], second['signing_key'])

    def test_environment_digest_tracks_environment_inputs(self):
        baseline, _ = self.assert_produced()
        changed, _ = self.assert_produced(mutate_source=lambda source: (source / 'certificates/manifest.json')
                                          .write_text('{"profile":"public","certificates":[],"note":"changed"}'))
        self.assertNotEqual(baseline['environment_digest'], changed['environment_digest'])

    def test_pull_request_runs_keep_their_real_identity(self):
        document, _ = self.assert_produced(GITHUB_REF='refs/pull/108/merge')
        self.assertEqual(document['producer']['ref'], 'refs/pull/108/merge')

    def test_melange_reference_must_be_pinned(self):
        for ref in ('cgr.dev/chainguard/melange:latest', 'cgr.dev/chainguard/melange:v0.61.2',
                    'cgr.dev/chainguard/melange:latest@' + DIGEST, 'ghcr.io/chainguard/melange@' + DIGEST):
            with self.subTest(ref=ref):
                self.assert_rejected('Melange must have an explicit immutable digest', MELANGE_IMAGE=ref)

    def test_resolved_melange_digest_must_equal_the_pin(self):
        for digests in (['cgr.dev/chainguard/melange@' + OTHER_DIGEST], [],
                        ['cgr.dev/chainguard/melange@' + DIGEST, 'cgr.dev/chainguard/melange@' + OTHER_DIGEST],
                        ['cgr.dev/other/melange@' + DIGEST]):
            with self.subTest(digests=digests):
                self.assert_rejected('resolved Melange digest differs from the requested digest',
                                     world(RepoDigests=digests))

    def test_melange_version_must_be_one_release(self):
        missing = {key: value for key, value in HOSTED_VERSION.items() if key != 'gitVersion'}
        cases = [('unexpected Melange version document', json.dumps(missing)),
                 ('Melange did not report one release version', json.dumps(dict(HOSTED_VERSION, gitVersion='devel'))),
                 ('Melange did not report one release version', json.dumps(dict(HOSTED_VERSION, gitCommit='unknown'))),
                 ('Extra data', json.dumps(HOSTED_VERSION) + json.dumps(HOSTED_VERSION))]
        for reason, stdout in cases:
            with self.subTest(stdout=stdout[:40]):
                self.assert_rejected(reason, world(version_stdout=stdout))

    def test_host_platform_must_be_linux_amd64(self):
        self.assert_rejected('the hosted Melange build must run on linux/amd64', world(Architecture='arm64'))
        self.assert_rejected('the Melange binary platform differs from the host platform',
                             world(version_stdout=json.dumps(dict(HOSTED_VERSION, platform='linux/arm64'))))

    def test_configuration_and_signing_key_must_exist(self):
        self.assert_rejected('the Melange configuration is missing', MELANGE_CONFIG='absent.yaml')
        self.assert_rejected('the ephemeral Melange signing key is missing',
                             mutate_source=lambda source: (source / 'melange.rsa.pub').unlink())

    def test_source_directory_must_be_regular_and_free_of_outputs(self):
        self.assert_rejected('symbolic links are not accepted in the Melange source directory',
                             mutate_source=lambda source: (source / 'link.yaml').symlink_to(source / CONFIG))
        self.assert_rejected('Melange outputs already exist before the build',
                             mutate_source=lambda source: (source / 'packages').mkdir())

    def test_source_directory_must_not_change_during_the_build(self):
        for between in (lambda source: (source / CONFIG).write_text((source / CONFIG).read_text() + '# edited\n'),
                        lambda source: (source / 'late-input.txt').write_text('appeared during the build')):
            with self.subTest(between=between):
                self.assert_rejected('the Melange source directory changed during the build', between=between)

    def test_target_architectures_must_be_exactly_built(self):
        self.assert_rejected('Melange did not build exactly the expected target architectures',
                             build=lambda packages: shutil.rmtree(packages / 'aarch64'))
        self.assert_rejected('Melange did not build exactly the expected target architectures',
                             build=lambda packages: (packages / 'riscv64').mkdir())

    def test_trust_inputs_must_be_local_pinned_files(self):
        url = 'https://packages.wolfi.dev/os/wolfi-signing.rsa.pub'
        self.assert_rejected('Melange keyring must be a local file of the source directory',
                             mutate_source=lambda source: (source / CONFIG).write_text(
                                 (source / CONFIG).read_text().replace('keys/wolfi-signing.rsa.pub', url)))
        self.assert_rejected('Melange keyring must be a local file of the source directory',
                             mutate_source=lambda source: (source / 'keys/wolfi-signing.rsa.pub').unlink())

    def test_repositories_must_be_https_locators(self):
        insecure = 'http://packages.wolfi.dev/os'
        plain = lambda text: text.replace('https://packages.wolfi.dev/os', insecure)
        both = lambda packages: [self.edit_apk(arch, control=edit_text('.melange.yaml', 'https://packages.wolfi.dev/os',
                                                                       insecure))(packages) for arch in ARCHS]
        self.assert_rejected('Melange repositories must be explicit HTTPS locators', build=both,
                             mutate_source=lambda source: (source / CONFIG).write_text(plain((source / CONFIG).read_text())))

    def test_locked_repositories_and_keyring_must_match_the_configuration(self):
        for old, new in (('https://packages.wolfi.dev/os', 'https://mirror.example/os'),
                         ('keys/wolfi-signing.rsa.pub', 'keys/other.rsa.pub')):
            with self.subTest(old=old):
                self.assert_rejected('locked repositories or keyring differ from the Melange configuration',
                                     build=self.edit_apk('x86_64', control=edit_text('.melange.yaml', old, new)))

    def test_environment_inventory_must_be_locked_and_unique(self):
        cases = [('build environment package is not locked to an exact version', 'busybox=1.38.0-r2', 'busybox'),
                 ('build environment package is not locked to an exact version', 'busybox=1.38.0-r2', 'busybox>=1.38'),
                 ('incomplete or ambiguous build environment inventory', 'libgcc=16.2.0-r1', 'busybox=1.38.0-r2')]
        for reason, old, new in cases:
            with self.subTest(new=new):
                self.assert_rejected(reason, build=self.edit_apk('aarch64', control=edit_text('.melange.yaml', old, new)))

    def test_embedded_lock_must_describe_the_target(self):
        self.assert_rejected('embedded Melange configuration differs from the request',
                             build=self.edit_apk('aarch64', control=edit_text('.melange.yaml', '- arm64', '- amd64')))
        self.assert_rejected('embedded Melange configuration differs from the request',
                             build=self.edit_apk('x86_64', control=edit_text('.melange.yaml', 'version: 1.0.0',
                                                                            'version: 9.9.9')))

    def test_outputs_must_match_their_official_metadata(self):
        sbom = 'var/lib/db/sbom/image-base-ca-certificates-1.0.0-r0.spdx.json'
        cases = [
            ('APK metadata differs from its output location',
             self.edit_apk('x86_64', control=edit_text('.PKGINFO', 'arch = x86_64', 'arch = aarch64'))),
            ('APK data differs from its declared datahash',
             self.edit_apk('x86_64', keep_datahash=True, data=edit_text(sbom, 'NOASSERTION', 'NOASSERTION '))),
            ('APKINDEX does not describe the produced APK',
             self.edit_apk('x86_64', rebuild_index=False, data=edit_text(sbom, 'NOASSERTION', 'NOASSERTION '))),
            ('APK was not produced by the inspected Melange version',
             self.edit_apk('aarch64', data=edit_text(sbom, 'melange (v0.61.2)', 'melange (v0.61.3)'))),
            ('APK is not signed by the ephemeral Melange key',
             self.edit_apk('aarch64', signature=lambda files: files.update({'.SIGN.RSA256.other.rsa.pub':
                                                                             files.pop(SIGNATURE)}))),
            ('APK lacks its Melange SBOM',
             self.edit_apk('aarch64', data=lambda files: files.pop(sbom))),
            ('the configured package was not produced',
             self.edit_apk('aarch64', control=edit_text('.PKGINFO', 'pkgver = 1.0.0-r0', 'pkgver = 1.0.0-r1'),
                           data=lambda files: files.update({sbom.replace('-r0.', '-r1.'): files.pop(sbom)}))),
            ('APKINDEX lists packages that were not produced',
             lambda packages: (packages / 'x86_64/APKINDEX.tar.gz').write_bytes(index(
                 [next((packages / 'x86_64').glob('*.apk')).read_bytes()], extra='\nC:Q1x\nP:ghost\nV:1-r0\nA:x86_64\nS:1\n'))),
            ('unexpected Melange outputs for x86_64',
             lambda packages: (packages / 'x86_64/unexpected.attest.tar.gz').write_bytes(b'')),
        ]
        for reason, build in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(reason, build=build)

    def test_producer_identity_is_validated(self):
        cases = [('invalid producer repository', dict(GITHUB_REPOSITORY='owner/repo/extra',
                  GITHUB_WORKFLOW_REF='owner/repo/extra/.github/workflows/workflow.yml@refs/heads/main')),
                 ('unexpected workflow repository',
                  dict(GITHUB_WORKFLOW_REF='mallory-co/alric-containers-image-base/.github/workflows/workflow.yml@main')),
                 ('invalid producer source SHA', dict(GITHUB_SHA='5d32e51')),
                 ('invalid producer run or attempt', dict(GITHUB_RUN_ID='0')),
                 ('invalid producer ref', dict(GITHUB_REF='refs/heads/a/../b'))]
        for reason, overrides in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(reason, **overrides)

    def test_evidence_is_preserved_with_candidate_artifacts(self):
        upload = next(s for s in STEPS if s.get('name') == 'Upload melange repository')
        self.assertIn('melange/melange-environment-evidence.json', upload['with']['path'])
        steps = WORKFLOW['jobs']['validate']['steps']
        build = next(s for s in steps if s.get('name') == 'Build multi-architecture OCI artifact once')
        self.assertIn('cp melange-repo/melange-environment-evidence.json '
                      '"${FRAMEWORK}.oci/melange-environment-evidence.json"', build['run'])
        replay = next(s for s in steps if s.get('name') == 'Preserve SBOMs and replay inputs')
        self.assertIn('${{ matrix.framework }}.oci/melange-environment-evidence.json', replay['with']['path'])


if __name__ == '__main__':
    unittest.main()
