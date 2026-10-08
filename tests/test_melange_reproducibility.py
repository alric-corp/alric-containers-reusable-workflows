"""Execute the HGC-03 reproduction steps (reference capture, rebuild job, validate gate) against real outputs.

Fixtures under tests/fixtures/melange-reproducibility hold no private key:
- hosted-develop/: melange-repo artifact of alric-containers-image-base run 37731450779 (commit 0510c94, BUILD_DATE
  2026-10-08T02:15:12-03:00): packages, ephemeral public key and the original binfmt and Melange environment receipts;
- hosted-pr/: packages and public key of run 37729369156 (BUILD_DATE 2026-10-08T01:49:56-03:00);
- local-a1/, local-b/, local-b2/: builds with the pinned Melange and another ephemeral key (private key deleted) for
  the develop string, the PR string and the develop instant written in UTC (2026-10-08T05:15:12Z);
- source/: melange/ of the consumer at 0510c94;
- wolfi/: original control and data streams of wolfi-baselayout from the pinned tool's cache, and the entries of the
  real Wolfi APKINDEX (verified with the declared keyring) for the locked packages.
Each test writes caches in the observed go-apk layout. Their indexes are signed by a key generated at runtime, which
replaces the declared keyring file in the test source tree; the real Wolfi index is exercised by the local
real-tool probe documented in docs/apko-contract.md, not here.
"""
import ast
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import urllib.parse

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_melange_environment_evidence import HOSTED_PACKAGES, HOSTED_VERSION, edit_text, members, streams, tar_gz  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/melange-reproducibility'
WORKFLOW = yaml.safe_load((ROOT / '.github/workflows/validate-apko-images.yml').read_text())
JOBS = WORKFLOW['jobs']


def step(job, name):
    return next(s for s in JOBS[job]['steps'] if s.get('name') == name)


DATE_STEP = step('melange-bundle', 'Validate package configuration and trust inputs')
ENV_CAPTURE = step('melange-bundle', 'Capture Melange environment inputs')
ENV_RECORD = step('melange-bundle', 'Record structured Melange environment evidence')
BUNDLE_CHOWN = step('melange-bundle', 'Hand the Melange cache to the runner user')
REFERENCE = step('melange-bundle', 'Capture Melange reproduction reference')
PREPARE = step('melange-reproduce', 'Prepare reproduction inputs')
REBUILD_CHOWN = step('melange-reproduce', 'Hand the Melange cache to the runner user')
RECORD = step('melange-reproduce', 'Record Melange reproducibility evidence')
STAGE = step('melange-reproduce', 'Stage rebuild outputs for external read-back')
GATE = step('validate', 'Require reproduced Melange package')
MELANGE = WORKFLOW['env']['MELANGE_IMAGE']
BINFMT = WORKFLOW['env']['BINFMT_IMAGE']
CONFIG = 'image-base-ca-certificates.yaml'
APK = 'image-base-ca-certificates-1.0.0-r0.apk'
DEVELOP_DATE, PR_DATE, UTC_DATE = '2026-10-08T02:15:12-03:00', '2026-10-08T01:49:56-03:00', '2026-10-08T05:15:12Z'
DEVELOP_SHA = '0510c94e9292f8c4e2030cdbb8dbe16343ad4d1e'
REPOSITORY = 'https://packages.wolfi.dev/os'
QUOTED = urllib.parse.quote(REPOSITORY, safe='')
ARCHS = ('aarch64', 'x86_64')
LOCK = sorted(tuple(item.split('=', 1)) for item in HOSTED_PACKAGES)
WOLFI = json.loads((FIXTURES / 'wolfi/index-entries.json').read_text())
MARKERS = ('# --- HGC-03 shared helpers: byte-identical in both jobs (tests enforce it) ---',
           '# --- end of HGC-03 shared helpers ---')
# Observed for the pinned images with `docker image inspect` (Melange Config.Env carries no SOURCE_DATE_EPOCH).
WORLD = dict(melange_ref=MELANGE, version_stdout=json.dumps(HOSTED_VERSION, indent=2) + '\n', images={
    MELANGE: dict(RepoTags=[], RepoDigests=['cgr.dev/chainguard/melange@' + MELANGE.rsplit('@', 1)[1]], Os='linux',
                  Architecture='amd64', Config=dict(Env=['PATH=/usr/local/sbin:/usr/local/bin:/usr/bin:/usr/sbin:/sbin:/bin',
                                                         'SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt'])),
    BINFMT: dict(RepoTags=[], RepoDigests=['tonistiigi/binfmt@' + BINFMT.rsplit('@', 1)[1]], Os='linux',
                 Architecture='amd64')})
FAKE_DOCKER = r'''import json
import os
import sys
from pathlib import Path

import yaml

args = sys.argv[1:]
world = json.loads(Path(os.environ['TEST_DOCKER_WORLD']).read_text())
melange = world['melange_ref']
with Path(os.environ['TEST_DOCKER_CALLS']).open('a') as stream:
    stream.write(json.dumps(args) + '\n')
if len(args) == 3 and args[:2] == ['image', 'inspect'] and args[2] in world['images']:
    print(json.dumps([world['images'][args[2]]]))
elif args == ['run', '--rm', melange, 'version', '--json']:
    sys.stdout.write(world['version_stdout'])
elif (len(args) == 10 and args[:3] == ['run', '--rm', '-v'] and args[3].endswith(':/query:ro')
      and args[4:7] == ['-w', '/query', melange] and args[7] == 'query'):
    # Same rendering as the pinned `melange query` (test_fake_query_matches_pinned_melange).
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
FAKE_SUDO = '#!/bin/sh\necho "$@" >> "$TEST_SUDO_CALLS"\n[ "$1" = chown ] && [ "$2" = -hR ] || exit 97\nexec "$@"\n'
KEYS = None


def setUpModule():
    global KEYS
    KEYS = tempfile.TemporaryDirectory()
    for name in ('index', 'other', 'output'):
        key = Path(KEYS.name, name + '.key')
        subprocess.run(['openssl', 'genrsa', '-out', str(key), '2048'], check=True, capture_output=True)
        subprocess.run(['openssl', 'rsa', '-in', str(key), '-pubout', '-out', str(key.with_suffix('.pub'))],
                       check=True, capture_output=True)


def tearDownModule():
    KEYS.cleanup()


def key(name, public=False):
    return Path(KEYS.name, name + ('.pub' if public else '.key'))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def q1(control):
    return 'Q1' + base64.b64encode(hashlib.sha1(control).digest()).decode()


def sign(private, payload):
    return subprocess.run(['openssl', 'dgst', '-sha256', '-sign', str(private)], input=payload, capture_output=True,
                          check=True).stdout


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def pkginfo(control):
    return dict(line.split(' = ', 1) for line in members(control)['.PKGINFO'].decode().splitlines() if ' = ' in line)


def materials(arch):
    """Locked dependencies: wolfi-baselayout keeps its original streams, the others get small real-format streams."""
    result = {}
    for name, version in LOCK:
        if name == 'wolfi-baselayout':
            control = (FIXTURES / f'wolfi/{arch}/{name}-{version}.control.tar.gz').read_bytes()
            data = (FIXTURES / f'wolfi/{arch}/{name}-{version}.data.tar.gz').read_bytes()
        else:
            data = tar_gz({f'usr/share/hgc03/{name}': f'{name}-{version} {arch}'.encode()})
            control = tar_gz({'.PKGINFO': f'pkgname = {name}\npkgver = {version}\narch = {arch}\n'
                                          f'datahash = {sha256(data)}\n'.encode()})
        result[(name, version)] = (control, data)
    return result


def synthetic(name, version, arch, marker=''):
    data = tar_gz({f'usr/share/hgc03/{name}': f'{name}-{version} {arch}{marker}'.encode()})
    control = tar_gz({'.PKGINFO': f'pkgname = {name}\npkgver = {version}\narch = {arch}\ndatahash = {sha256(data)}\n'.encode()})
    return control, data


def entry(name, version, arch, control, data):
    real = WOLFI[arch]['entries'].get(name, '')
    if f'C:{q1(control)}' in real.splitlines():
        return real
    return f'C:{q1(control)}\nP:{name}\nV:{version}\nA:{arch}\nS:{len(control) + len(data)}'


def write_cache(root, edit=None, entries=None, signer=None, after=None):
    """go-apk cache layout observed with the pinned Melange (apko v1.4.6)."""
    spec = {arch: materials(arch) for arch in ARCHS}
    if edit:
        edit(spec)
    for arch, items in spec.items():
        base = root / QUOTED / arch
        lines = [entry(name, version, arch, *pair) for (name, version), pair in sorted(items.items())]
        lines.append(f'C:Q1{"A" * 26}=\nP:unrelated-package\nV:1.0-r0\nA:{arch}\nS:1')  # indexes list far more packages
        if entries:
            lines = entries(arch, lines)
        index = tar_gz({'DESCRIPTION': b'', 'APKINDEX': ('\n\n'.join(lines) + '\n').encode()})
        private, name, payload = signer or (key('index'), 'wolfi-signing.rsa.pub', None)
        raw = tar_gz({'.SIGN.RSA256.' + name: sign(private, payload or index)}) + index
        (base / 'APKINDEX').mkdir(parents=True)
        (base / 'APKINDEX/1617709418.tmp').write_bytes(raw)
        (base / 'APKINDEX/MI3DOYJUMEYWKZTFMZSTAMZUHE4DSMBYGM4DSMLDGFTGGODDGZSQ====.tar.gz').symlink_to('1617709418.tmp')
        for (package, version), (control, data) in items.items():
            directory = base / f'{package}-{version}'
            expanded = directory / 'expand-apk2502242938'
            expanded.mkdir(parents=True)
            for stream, content in (('stream-0.tar.gz', control), ('stream-1.tar.gz', data), ('stream-1.tar', gzip.decompress(data))):
                (expanded / stream).write_bytes(content)
            (directory / f'{hashlib.sha1(control).hexdigest()}.ctl.tar.gz').symlink_to(f'{expanded.name}/stream-0.tar.gz')
            (directory / f'{sha256(data)}.dat.tar.gz').symlink_to(f'{expanded.name}/stream-1.tar.gz')
            (directory / f'{sha256(data)}.dat.tar').symlink_to(f'{expanded.name}/stream-1.tar')
    if after:
        after(root / QUOTED)


def resign(packages, private, index_key=None):
    """Sign every APK with `private` and regenerate APKINDEX, as Melange does with its ephemeral key."""
    for directory in sorted(path for path in packages.iterdir() if path.is_dir()):
        lines = []
        for apk in sorted(directory.glob('*.apk')):
            parts = streams(apk.read_bytes())
            raw = tar_gz({'.SIGN.RSA256.melange.rsa.pub': sign(private, parts[1])}) + parts[1] + parts[2]
            apk.write_bytes(raw)
            info = pkginfo(parts[1])
            lines.append(f"C:{q1(parts[1])}\nP:{info['pkgname']}\nV:{info['pkgver']}\nA:{info['arch']}\nS:{len(raw)}")
        index = tar_gz({'APKINDEX': ('\n\n'.join(lines) + '\n').encode(), 'DESCRIPTION': b''})
        signature = tar_gz({'.SIGN.RSA256.melange.rsa.pub': sign(index_key or private, index)})
        (directory / 'APKINDEX.tar.gz').write_bytes(signature + index)


def tamper(arch, control=None, data=None, keep_datahash=False, sign_with='output'):
    """Edit one APK of the rebuild; streams that are not edited keep their original bytes.

    Unless sign_with is None, the whole output is then re-signed with a runtime key (a new ephemeral key)."""
    def apply(packages):
        apk = packages / arch / APK
        signature, control_stream, data_stream = streams(apk.read_bytes())
        if data:
            files = members(data_stream)
            data(files)
            data_stream = tar_gz(files)
        if control or (data and not keep_datahash):
            files = members(control_stream)
            if control:
                control(files)
            if data and not keep_datahash:
                files = dict(files, **{'.PKGINFO': b''.join(
                    f'datahash = {sha256(data_stream)}\n'.encode() if line.startswith(b'datahash = ') else line
                    for line in files['.PKGINFO'].splitlines(keepends=True))})
            control_stream = tar_gz(files)
        apk.write_bytes(signature + control_stream + data_stream)
        if sign_with:
            resign(packages, key(sign_with))
    return apply


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            name, _, value = line.partition('=')
            values[name] = value
    return values


def git(cwd, *arguments, date=None):
    environment = dict(os.environ, GIT_AUTHOR_NAME='hgc03', GIT_AUTHOR_EMAIL='hgc03@example.invalid',
                       GIT_COMMITTER_NAME='hgc03', GIT_COMMITTER_EMAIL='hgc03@example.invalid')
    if date:
        environment.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    return subprocess.run(['git', *arguments], cwd=cwd, env=environment, check=True, capture_output=True,
                          text=True).stdout.strip()


def run_step(outcome, name, definition, cwd, environment):
    result = subprocess.run(['bash', '-c', definition['run']], cwd=cwd / definition.get('working-directory', '.'),
                            env=environment, capture_output=True, text=True)
    outcome.steps[name] = result
    if result.returncode and outcome.failed is None:
        outcome.failed = name
    return result.returncode == 0


def pipeline(reference='hosted-develop', rebuild='local-a1', date=DEVELOP_DATE, world=None, rebuild_world=None, env=None,
             bundle_env=None, reproduce_env=None, validate_env=None, reference_cache=None, rebuild_cache=None,
             reference_outputs=None, rebuild_outputs=None, rebuild_key=None, before_reference=None, transit=None,
             workspace=None, after_prepare=None, before_stage=None, before_gate=None, keep=None):
    """melange-bundle, then melange-reproduce in another workspace and RUNNER_TEMP, then the validate gate."""
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        (root / 'bin').mkdir()
        for name, content in (('docker', '#!' + sys.executable + '\n' + FAKE_DOCKER), ('sudo', FAKE_SUDO)):
            (root / 'bin' / name).write_text(content)
            (root / 'bin' / name).chmod(0o755)
        (root / 'world.json').write_text(json.dumps(world or WORLD))
        (root / 'rebuild-world.json').write_text(json.dumps(rebuild_world or WORLD))
        origin = root / 'origin'
        shutil.copytree(FIXTURES / 'source', origin / 'melange')
        shutil.copy(key('index', public=True), origin / 'melange/keys/wolfi-signing.rsa.pub')  # runtime keyring stand-in
        git(origin, 'init', '-q')
        git(origin, 'add', '-A')
        git(origin, 'commit', '-q', '-m', 'consumer', date=date)
        outcome = SimpleNamespace(steps={}, failed=None, sha=git(origin, 'rev-parse', 'HEAD'), github_env={}, capture=None, staged=None,
                                  evidence=None, diagnostics=None, environment=None, sudo='', docker='')
        base = dict(os.environ, PATH=f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", TEST_DOCKER_CALLS=str(root / 'calls.jsonl'),
                    TEST_SUDO_CALLS=str(root / 'sudo.txt'), MELANGE_IMAGE=MELANGE, BINFMT_IMAGE=BINFMT, MELANGE_CONFIG=CONFIG,
                    LOCKED_BUILD='false', GITHUB_REPOSITORY='alric-corp/alric-containers-image-base',
                    GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/workflow.yml@refs/heads/develop',
                    GITHUB_REF='refs/heads/develop', GITHUB_SHA=outcome.sha, GITHUB_RUN_ID='37731450779', GITHUB_RUN_ATTEMPT='1')
        base.update(env or {})

        # melange-bundle: the real date step, HGC-02 capture and record around the simulated build, then HGC-03.
        bundle, bundle_temp = root / 'bundle', root / 'temp-bundle'
        git(root, 'clone', '-q', str(origin), str(bundle))
        bundle_temp.mkdir()
        generated = bundle / 'melange'
        shutil.copy(FIXTURES / reference / 'melange.rsa.pub', generated / 'melange.rsa.pub')
        (generated / 'melange.rsa').write_text('ephemeral private key of the reference job (never leaves it)')
        (generated / 'melange-version.txt').write_text('melange\n\nGitVersion:    v0.61.2\n')
        # The hosted HGC-01 receipt with this pipeline's producer, as the bundle job would write it.
        binfmt = json.loads((FIXTURES / 'hosted-develop/binfmt-evidence.json').read_bytes())
        run, attempt = int(base['GITHUB_RUN_ID']), int(base['GITHUB_RUN_ATTEMPT'])
        binfmt['producer'] = dict(repository=base['GITHUB_REPOSITORY'].lower(), ref=base['GITHUB_REF'], source_sha=base['GITHUB_SHA'],
                                  run_id=run, run_attempt=attempt, release_id=f'r{run}-a{attempt}', workflow='.github/workflows/workflow.yml')
        outcome.binfmt = canonical(binfmt)
        (generated / 'binfmt-evidence.json').write_bytes(outcome.binfmt)
        env_a = dict(base, RUNNER_TEMP=str(bundle_temp), GITHUB_ENV=str(bundle_temp / 'github_env'), GITHUB_JOB='melange-bundle',
                     RUNNER_NAME='GitHub Actions 1000000001', TEST_DOCKER_WORLD=str(root / 'world.json'))
        ok = run_step(outcome, 'date', DATE_STEP, bundle, env_a)
        env_a.update(read_env(bundle_temp / 'github_env'), **(bundle_env or {}))
        ok = ok and run_step(outcome, 'environment-capture', ENV_CAPTURE, bundle, env_a)
        if ok:
            shutil.copytree(FIXTURES / reference / 'packages', generated / 'packages')
            if reference_outputs:
                reference_outputs(generated / 'packages')
            (bundle_temp / 'melange-apk-cache').mkdir()
            write_cache(bundle_temp / 'melange-apk-cache', **(reference_cache or {}))
        ok = ok and run_step(outcome, 'environment-record', ENV_RECORD, bundle, env_a)
        if ok and before_reference:
            before_reference(bundle)
        ok = ok and run_step(outcome, 'bundle-chown', BUNDLE_CHOWN, bundle, env_a)
        ok = ok and run_step(outcome, 'reference', REFERENCE, bundle, env_a)
        capture = bundle_temp / 'melange-reproduction/melange-reproduction-reference.json'
        outcome.capture = capture.read_bytes() if capture.exists() else None
        if (generated / 'melange-environment-evidence.json').exists():
            outcome.environment = (generated / 'melange-environment-evidence.json').read_bytes()
        if not ok:
            return outcome

        # Artifacts between jobs: melange-repo (as uploaded) and the reproduction reference.
        artifacts = root / 'artifacts'
        repo = artifacts / 'melange-repo'
        repo.mkdir(parents=True)
        shutil.copytree(generated / 'packages', repo / 'packages')
        for name in ('melange.rsa.pub', 'melange-version.txt', 'binfmt-evidence.json', 'melange-environment-evidence.json'):
            shutil.copy(generated / name, repo / name)
        (artifacts / 'capture').mkdir()
        shutil.copy(capture, artifacts / 'capture')
        if transit:
            transit(repo, artifacts / 'capture')

        # melange-reproduce: clean checkout, reference material only in RUNNER_TEMP.
        rebuild_root, rebuild_temp = root / 'reproduce', root / 'temp-reproduce'
        git(root, 'clone', '-q', str(origin), str(rebuild_root))
        if workspace:
            workspace(rebuild_root)
        (rebuild_temp / 'reproduction-reference').mkdir(parents=True)
        shutil.copytree(repo, rebuild_temp / 'reproduction-reference/melange-repo')
        shutil.copytree(artifacts / 'capture', rebuild_temp / 'reproduction-reference/capture')
        env_b = dict(base, RUNNER_TEMP=str(rebuild_temp), GITHUB_ENV=str(rebuild_temp / 'github_env'),
                     GITHUB_JOB='melange-reproduce', RUNNER_NAME='GitHub Actions 1000000002',
                     TEST_DOCKER_WORLD=str(root / 'rebuild-world.json'))
        env_b.update(reproduce_env or {})
        ok = run_step(outcome, 'prepare', PREPARE, rebuild_root, env_b)
        if ok:
            outcome.github_env = read_env(rebuild_temp / 'github_env')
            env_b.update(outcome.github_env, **(after_prepare or {}))
            # keygen stand-in: the public half of the rebuild's own ephemeral key.
            shutil.copy(rebuild_key or FIXTURES / rebuild / 'melange.rsa.pub', rebuild_root / 'melange/melange.rsa.pub')
            (rebuild_root / 'melange/melange.rsa').write_text('ephemeral private key of the rebuild job')
            shutil.copytree(FIXTURES / rebuild / 'packages', rebuild_root / 'melange/packages')
            if rebuild_outputs:
                rebuild_outputs(rebuild_root / 'melange/packages')
            (rebuild_temp / 'melange-apk-cache').mkdir()
            write_cache(rebuild_temp / 'melange-apk-cache', **(rebuild_cache or {}))
        ok = ok and run_step(outcome, 'rebuild-chown', REBUILD_CHOWN, rebuild_root, env_b)
        ok = ok and run_step(outcome, 'record', RECORD, rebuild_root, env_b)
        evidence = rebuild_temp / 'melange-reproducibility/melange-reproducibility-evidence.json'
        diagnostics = rebuild_temp / 'melange-reproducibility-diagnostics/melange-reproducibility-diagnostics.json'
        outcome.evidence = evidence.read_bytes() if evidence.exists() else None
        outcome.diagnostics = diagnostics.read_bytes() if diagnostics.exists() else None
        outcome.sudo = Path(base['TEST_SUDO_CALLS']).read_text() if Path(base['TEST_SUDO_CALLS']).exists() else ''
        outcome.docker = Path(base['TEST_DOCKER_CALLS']).read_text()
        # Read-back staging; the upload sends exactly this directory as melange-reproducibility.
        staging = rebuild_temp / 'melange-reproducibility-artifact'
        if ok and before_stage:
            before_stage(rebuild_root, rebuild_temp)
        ok = ok and run_step(outcome, 'stage', STAGE, rebuild_root, env_b)
        if ok:
            outcome.staged = {path.relative_to(staging).as_posix(): path.read_bytes()
                              for path in sorted(staging.rglob('*')) if path.is_file()}

        # validate: the gate before any candidate is built.
        if ok:
            validate = root / 'validate'
            shutil.copytree(repo, validate / 'melange-repo')
            shutil.copytree(staging, validate / 'melange-reproducibility')
            if before_gate:
                before_gate(validate)
            run_step(outcome, 'gate', GATE, validate, dict(base, GITHUB_JOB='validate', **(validate_env or {})))
            if keep:
                outcome.validate = keep / 'validate'
                shutil.copytree(validate, outcome.validate)
                outcome.github = {name: value for name, value in base.items() if name.startswith('GITHUB_')}
                # The three artifacts as an external auditor downloads them.
                outcome.artifacts = keep / 'artifacts'
                shutil.copytree(repo, outcome.artifacts / 'melange-repo')
                shutil.copytree(artifacts / 'capture', outcome.artifacts / 'melange-reproduction-reference')
                shutil.copytree(staging, outcome.artifacts / 'melange-reproducibility')
        return outcome


class Reproduction(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kept = tempfile.TemporaryDirectory()
        cls.default = pipeline(keep=Path(cls.kept.name))

    @classmethod
    def tearDownClass(cls):
        cls.kept.cleanup()

    def assert_reproduced(self, outcome):
        self.assertIsNone(outcome.failed, {name: result.stderr[-2000:] for name, result in outcome.steps.items()})
        self.assertIsNotNone(outcome.evidence)
        self.assertEqual(outcome.steps['gate'].returncode, 0, outcome.steps['gate'].stderr)
        return json.loads(outcome.evidence)

    def assert_rejected(self, outcome, failed, reason):
        """The named step fails for the property under test and no final evidence exists."""
        self.assertEqual(outcome.failed, failed, {name: result.stderr[-1500:] for name, result in outcome.steps.items()})
        self.assertIn(reason, outcome.steps[failed].stderr)
        self.assertIsNone(outcome.evidence)
        self.assertNotIn('gate', outcome.steps)

    def assert_classified(self, outcome, status):
        self.assert_rejected(outcome, 'record', f'Melange CA package was not reproduced: {status}')
        diagnostics = json.loads(outcome.diagnostics)
        self.assertEqual((diagnostics['kind'], diagnostics['status']), ('melange-reproducibility-diagnostics', status))
        return diagnostics

    # --- workflow structure -------------------------------------------------------------------------------------

    def test_helpers_are_byte_identical_in_both_jobs(self):
        blocks = []
        for definition in (REFERENCE, RECORD):
            text = definition['run']
            start, end = text.index(MARKERS[0]), text.index(MARKERS[1])
            blocks.append(text[start:end])
        self.assertEqual(blocks[0], blocks[1])
        for name in ('def confined(', 'def package_record(', 'def index_record(', 'def cache_materials(', 'def melange_image('):
            self.assertIn(name, blocks[0])

    def test_build_step_is_shared_and_keeps_the_cache_outside_the_sources(self):
        bundle = step('melange-bundle', 'Build CA package (amd64 + arm64)')
        self.assertEqual(bundle, step('melange-reproduce', 'Build CA package (amd64 + arm64)'))
        self.assertEqual(bundle['working-directory'], 'melange')
        run = bundle['run']
        for fragment in ('mkdir "$RUNNER_TEMP/melange-apk-cache"', '-v "$RUNNER_TEMP/melange-apk-cache":/apk-cache',
                         '--apk-cache-dir /apk-cache', '--signing-key melange.rsa --build-date "$BUILD_DATE"',
                         'for ARCH in x86_64 aarch64; do'):
            self.assertIn(fragment, run)
        # Nothing but the image's own Config.Env reaches the Melange process: no -e, --env or --env-file.
        self.assertNotRegex(run, r'(^|\s)(-e|--env|--env-file)(\s|=)')
        # BUILD_DATE keeps its original source in the reference job.
        self.assertIn('echo "BUILD_DATE=$(git show -s --format=%cI HEAD)" >> "$GITHUB_ENV"', DATE_STEP['run'])
        self.assertIn('echo "SOURCE_DATE_EPOCH=$(git show -s --format=%ct HEAD)" >> "$GITHUB_ENV"', DATE_STEP['run'])
        self.assertEqual(BUNDLE_CHOWN, REBUILD_CHOWN)
        self.assertEqual(BUNDLE_CHOWN['run'], 'sudo chown -hR "$(id -u):$(id -g)" "$RUNNER_TEMP/melange-apk-cache"')

    def test_job_graph_requires_a_reproduction_before_candidates(self):
        reproduce, validate = JOBS['melange-reproduce'], JOBS['validate']
        self.assertEqual(reproduce['needs'], 'melange-bundle')
        self.assertEqual(validate['needs'], ['melange-bundle', 'melange-reproduce'])
        self.assertEqual(reproduce['runs-on'], 'ubuntu-latest')
        for definition in (reproduce, validate):
            self.assertNotIn('if', definition)
            self.assertNotIn('continue-on-error', definition)
        names = [s.get('name') for s in reproduce['steps']]
        order = ['Download reference melange repository', 'Download Melange reproduction reference',
                 'Prepare reproduction inputs', 'Generate ephemeral melange signing key', 'Build CA package (amd64 + arm64)',
                 'Hand the Melange cache to the runner user', 'Record Melange reproducibility evidence',
                 'Stage rebuild outputs for external read-back', 'Upload Melange reproducibility evidence',
                 'Preserve reproducibility diagnostics']
        self.assertEqual([names.index(name) for name in order], sorted(names.index(name) for name in order))
        for name in order[:2]:
            self.assertTrue(step('melange-reproduce', name)['with']['path'].startswith('${{ runner.temp }}/reproduction-reference/'))
        bundle_names = [s.get('name') for s in JOBS['melange-bundle']['steps']]
        order = ['Record structured Melange environment evidence', 'Hand the Melange cache to the runner user',
                 'Capture Melange reproduction reference', 'Upload melange repository', 'Upload Melange reproduction reference']
        self.assertEqual([bundle_names.index(name) for name in order], sorted(bundle_names.index(name) for name in order))
        validate_names = [s.get('name') for s in validate['steps']]
        self.assertLess(validate_names.index('Require reproduced Melange package'),
                        validate_names.index('Build multi-architecture OCI artifact once'))
        downloads = [s['with']['name'] for s in validate['steps'] if s.get('uses', '').startswith('actions/download-artifact@')]
        self.assertEqual(downloads, ['melange-repo', 'melange-reproducibility'])
        for definition in (GATE, RECORD, STAGE, step('melange-reproduce', 'Upload Melange reproducibility evidence')):
            self.assertNotIn('if', definition)
            self.assertNotIn('continue-on-error', definition)
        upload = step('melange-reproduce', 'Upload Melange reproducibility evidence')['with']
        self.assertEqual((upload['name'], upload['if-no-files-found']), ('melange-reproducibility', 'error'))
        diagnostics = step('melange-reproduce', 'Preserve reproducibility diagnostics')
        self.assertEqual(diagnostics['if'], '${{ failure() }}')
        self.assertTrue(diagnostics['with']['name'].startswith('melange-reproducibility-diagnostics-'))
        # melange-repo keeps its HGC-02 content; the HGC-03 results travel in their own artifacts.
        self.assertEqual(step('melange-bundle', 'Upload melange repository')['with']['path'].split(),
                         ['melange/packages/', 'melange/melange.rsa.pub', 'melange/melange-version.txt',
                          'melange/binfmt-evidence.json', 'melange/melange-environment-evidence.json'])
        for job in JOBS.values():
            for definition in job['steps']:
                if definition.get('uses', '').startswith('actions/upload-artifact@'):
                    self.assertNotRegex(definition['with']['path'], r'melange\.rsa(\s|$)')

    def test_fake_query_matches_pinned_melange(self):
        environment_fixtures = ROOT / 'tests/fixtures/melange-environment'
        template = 'QUERY = (' + RECORD['run'].split('QUERY = (', 1)[1].split(')\n', 1)[0] + ')\n'
        self.assertIn(template, ENV_RECORD['run'])  # the HGC-02 template, unchanged
        template = ast.literal_eval(template.split('=', 1)[1].strip())  # the literal template string from the workflow
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = (environment_fixtures / 'hosted/packages/x86_64' / APK).read_bytes()
            (root / '.melange.yaml').write_bytes(members(streams(raw)[1])['.melange.yaml'])
            shutil.copy(environment_fixtures / f'source/{CONFIG}', root / CONFIG)
            (root / 'docker').write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            (root / 'docker').chmod(0o755)
            (root / 'world.json').write_text(json.dumps(WORLD))
            environment = dict(os.environ, TEST_DOCKER_WORLD=str(root / 'world.json'), TEST_DOCKER_CALLS=str(root / 'calls'))
            for name, expected in (('.melange.yaml', 'hosted/query-lock-x86_64.txt'), (CONFIG, 'source-query.txt')):
                output = subprocess.run([str(root / 'docker'), 'run', '--rm', '-v', f'{root}:/query:ro', '-w', '/query', MELANGE,
                                         'query', name, template], env=environment, capture_output=True, text=True, check=True)
                self.assertEqual(output.stdout, (environment_fixtures / expected).read_text())

    # --- real fixtures ------------------------------------------------------------------------------------------

    def test_real_wolfi_streams_match_the_signed_index_entries(self):
        environment = json.loads((FIXTURES / 'hosted-develop/melange-environment-evidence.json').read_bytes())
        for arch in ARCHS:
            self.assertEqual(WOLFI[arch]['signature'], '.SIGN.RSA256.wolfi-signing.rsa.pub')
            entries = {name: dict(line.split(':', 1) for line in text.splitlines()) for name, text in WOLFI[arch]['entries'].items()}
            self.assertEqual({(name, item['V']) for name, item in entries.items()},
                             {(item['name'], item['version']) for item in environment['environment']['targets'][arch]['packages']})
            control, data = materials(arch)[('wolfi-baselayout', '20230201-r30')]
            info = pkginfo(control)
            self.assertEqual((info['pkgname'], info['pkgver'], info['arch'], info['datahash']),
                             ('wolfi-baselayout', '20230201-r30', arch, sha256(data)))
            self.assertEqual(entries['wolfi-baselayout']['C'], q1(control))
            self.assertEqual(int(entries['wolfi-baselayout']['S']), len(control) + len(data))

    def test_real_builds_show_which_inputs_are_material(self):
        def parts(name, arch):
            return streams((FIXTURES / name / 'packages' / arch / APK).read_bytes())
        for arch in ARCHS:
            develop, a1, b2 = parts('hosted-develop', arch), parts('local-a1', arch), parts('local-b2', arch)
            # Same string, other key: control and data equal, signature and full APK differ.
            self.assertEqual(develop[1:], a1[1:])
            self.assertNotEqual(develop[0], a1[0])
            self.assertEqual(parts('hosted-pr', arch)[1:], parts('local-b', arch)[1:])
            # Same instant written with another offset: same builddate, other SBOM string, other streams.
            self.assertEqual(pkginfo(develop[1])['builddate'], pkginfo(b2[1])['builddate'])
            sbom = 'var/lib/db/sbom/image-base-ca-certificates-1.0.0-r0.spdx.json'
            self.assertEqual(json.loads(members(develop[2])[sbom])['creationInfo']['created'], DEVELOP_DATE)
            self.assertEqual(json.loads(members(b2[2])[sbom])['creationInfo']['created'], UTC_DATE)
            self.assertNotEqual(develop[1], b2[1])
            self.assertNotEqual(develop[2], b2[2])

    def test_hgc02_receipt_stays_byte_identical(self):
        """The unchanged HGC-02 producer still emits the hosted develop receipt from the hosted inputs."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(FIXTURES / 'source', root / 'melange')
            for name in ('melange.rsa.pub', 'binfmt-evidence.json'):
                shutil.copy(FIXTURES / 'hosted-develop' / name, root / 'melange' / name)
            (root / 'melange/melange.rsa').write_text('private key')
            (root / 'melange/melange-version.txt').write_text('version')
            (root / 'bin').mkdir()
            (root / 'bin/docker').write_text('#!' + sys.executable + '\n' + FAKE_DOCKER)
            (root / 'bin/docker').chmod(0o755)
            (root / 'temp').mkdir()
            (root / 'world.json').write_text(json.dumps(WORLD))
            environment = dict(os.environ, PATH=f"{root / 'bin'}{os.pathsep}{os.environ['PATH']}", RUNNER_TEMP=str(root / 'temp'),
                               TEST_DOCKER_WORLD=str(root / 'world.json'), TEST_DOCKER_CALLS=str(root / 'calls'),
                               MELANGE_IMAGE=MELANGE, MELANGE_CONFIG=CONFIG, GITHUB_REPOSITORY='alric-corp/alric-containers-image-base',
                               GITHUB_WORKFLOW_REF='alric-corp/alric-containers-image-base/.github/workflows/workflow.yml@refs/heads/develop',
                               GITHUB_REF='refs/heads/develop', GITHUB_SHA=DEVELOP_SHA, GITHUB_RUN_ID='37731450779',
                               GITHUB_RUN_ATTEMPT='1')
            for definition in (ENV_CAPTURE, ENV_RECORD):
                if definition is ENV_RECORD:
                    shutil.copytree(FIXTURES / 'hosted-develop/packages', root / 'melange/packages')
                result = subprocess.run(['bash', '-c', definition['run']], cwd=root, env=environment, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((root / 'melange/melange-environment-evidence.json').read_bytes(),
                             (FIXTURES / 'hosted-develop/melange-environment-evidence.json').read_bytes())

    # --- reproduction ------------------------------------------------------------------------------------------

    def test_same_inputs_with_a_new_key_reproduce_control_and_data(self):
        outcome = self.default
        evidence = self.assert_reproduced(outcome)
        self.assertEqual(outcome.evidence, canonical(evidence))
        self.assertEqual(set(evidence), {'schema_version', 'kind', 'status', 'independence', 'scope', 'receipts', 'reference',
                                         'rebuild', 'reproduction_inputs', 'reproduction_input_digest', 'results',
                                         'signatures', 'limits'})
        self.assertEqual((evidence['schema_version'], evidence['kind'], evidence['status'], evidence['independence']),
                         (1, 'melange-reproducibility-evidence', 'REPRODUCED', 'CROSS_JOB_SAME_RUN'))
        self.assertEqual(evidence['scope'], dict(configuration=f'melange/{CONFIG}', targets=list(ARCHS),
                                                 compared=['control_stream', 'data_stream'],
                                                 excluded_from_equality=['signature_stream', 'apkindex', 'signed_apk']))
        for arch in ARCHS:
            self.assertEqual(evidence['results'][arch], dict(dependencies_equal=True, packages=[
                dict(file=APK, control_stream_equal=True, data_stream_equal=True)]))
            reference, rebuilt = evidence['reference']['outputs'][arch][0], evidence['rebuild']['outputs'][arch][0]
            develop = (FIXTURES / 'hosted-develop/packages' / arch / APK).read_bytes()
            local = (FIXTURES / 'local-a1/packages' / arch / APK).read_bytes()
            self.assertEqual((reference['apk_sha256'], rebuilt['apk_sha256']), (sha256(develop), sha256(local)))
            self.assertNotEqual(reference['apk_sha256'], rebuilt['apk_sha256'])  # not a copy of the original outputs
            for field in ('control_stream_sha256', 'control_checksum', 'data_stream_sha256', 'datahash'):
                self.assertEqual(reference[field], rebuilt[field])
            self.assertEqual(reference['control_stream_sha256'], sha256(streams(develop)[1]))
            for side, raw in (('reference', develop), ('rebuild', local)):
                signed = evidence['signatures'][side]
                self.assertEqual(signed['packages'][arch], [dict(file=APK, signature='.SIGN.RSA256.melange.rsa.pub',
                                                                 signature_stream_sha256=sha256(streams(raw)[0]), verified=True)])
                self.assertTrue(signed['apkindex'][arch]['verified'] and signed['apkindex'][arch]['describes_outputs'])
        self.assertEqual(evidence['signatures']['reference']['public_key_sha256'],
                         sha256((FIXTURES / 'hosted-develop/melange.rsa.pub').read_bytes()))
        self.assertEqual(evidence['signatures']['rebuild']['public_key_sha256'],
                         sha256((FIXTURES / 'local-a1/melange.rsa.pub').read_bytes()))
        self.assertNotIn('PRIVATE KEY', outcome.evidence.decode() + outcome.capture.decode())
        self.assertIn(f'chown -hR {os.getuid()}:{os.getgid()} ', outcome.sudo)

    def test_temporal_parameters_are_recorded_exactly(self):
        evidence = json.loads(self.default.evidence)
        self.assertEqual(self.default.github_env, dict(BUILD_DATE=DEVELOP_DATE, SOURCE_DATE_EPOCH='1791436512'))
        self.assertEqual(evidence['reference']['temporal'], dict(
            build_date=DEVELOP_DATE, build_date_source='git show -s --format=%cI HEAD', build_date_commit=self.default.sha,
            host_source_date_epoch=1791436512, melange_process_source_date_epoch='absent', applied='--build-date',
            observed_in_outputs=dict(pkginfo_builddate=1791436512, sbom_created=DEVELOP_DATE)))
        self.assertEqual(evidence['rebuild']['temporal'], dict(
            build_date=DEVELOP_DATE, build_date_source='melange-reproduction-reference', melange_process_source_date_epoch='absent',
            applied='--build-date', observed_in_outputs=dict(pkginfo_builddate=1791436512, sbom_created=DEVELOP_DATE)))
        self.assertEqual(evidence['reproduction_inputs']['build'], dict(
            build_date=DEVELOP_DATE, parameter='--build-date', melange_process_source_date_epoch='absent'))

    def test_dependency_materials_are_recorded_from_the_cached_streams(self):
        evidence = json.loads(self.default.evidence)
        for arch in ARCHS:
            index = evidence['reference']['resolution_indexes'][arch]
            self.assertEqual(len(index), 1)
            self.assertEqual((index[0]['role'], index[0]['signature_verified'], index[0]['signature_key']['path']),
                             ('RESOLUTION_INDEX', True, 'melange/keys/wolfi-signing.rsa.pub'))
            self.assertEqual(index[0]['signature_key']['sha256'], sha256(key('index', public=True).read_bytes()))
            expected = []
            for (name, version), (control, data) in sorted(materials(arch).items()):
                expected.append(dict(name=name, version=version, arch=arch, repository=REPOSITORY,
                                     control_stream_sha256=sha256(control), control_stream_size=len(control),
                                     control_checksum=q1(control), datahash=pkginfo(control)['datahash'],
                                     data_stream_sha256=sha256(data), data_stream_size=len(data)))
            self.assertEqual(evidence['reproduction_inputs']['targets'][arch],
                             dict(environment_arch=dict(aarch64='arm64', x86_64='amd64')[arch], dependencies=expected))
            for side in ('reference', 'rebuild'):
                records = evidence[side]['dependencies'][arch]
                self.assertEqual([{k: v for k, v in item.items() if k not in ('resolution_index_sha256', 'checks')}
                                  for item in records], expected)
                self.assertTrue(all(item['resolution_index_sha256'] == evidence[side]['resolution_indexes'][arch][0]['sha256']
                                    and all(item['checks'].values()) for item in records))

    def test_reproduction_input_digest_covers_inputs_not_execution(self):
        develop = json.loads(self.default.evidence)
        self.assertEqual(develop['reproduction_input_digest'],
                         'sha256:' + sha256(canonical(develop['reproduction_inputs'])))
        rerun = self.assert_reproduced(pipeline(env=dict(GITHUB_RUN_ID='42', GITHUB_RUN_ATTEMPT='2'),
                                                reproduce_env=dict(RUNNER_NAME='GitHub Actions 1000000009')))
        self.assertEqual(rerun['reproduction_input_digest'], develop['reproduction_input_digest'])
        self.assertNotEqual(rerun['rebuild']['producer'], develop['rebuild']['producer'])
        pr = self.assert_reproduced(pipeline(reference='hosted-pr', rebuild='local-b', date=PR_DATE,
                                             env=dict(GITHUB_REF='refs/pull/110/merge', GITHUB_RUN_ID='37729369156')))
        self.assertNotEqual(pr['reproduction_input_digest'], develop['reproduction_input_digest'])
        changed = {key for key in develop['reproduction_inputs'] if develop['reproduction_inputs'][key] != pr['reproduction_inputs'][key]}
        self.assertEqual(changed, {'build'})
        self.assertEqual(pr['reproduction_inputs']['build']['build_date'], PR_DATE)

    def test_isolated_build_date_changes_are_rejected(self):
        self.assert_rejected(pipeline(rebuild='local-b'), 'record', 'the rebuild did not apply the recorded --build-date string')
        # The UTC spelling of the same instant passes the epoch check but is not the recorded string.
        self.assert_rejected(pipeline(bundle_env=dict(BUILD_DATE=UTC_DATE)), 'reference', 'BUILD_DATE does not come from the built commit')
        self.assert_rejected(pipeline(bundle_env=dict(BUILD_DATE=PR_DATE)), 'reference',
                             'BUILD_DATE and SOURCE_DATE_EPOCH describe different instants')
        amend = lambda checkout: git(checkout, 'commit', '-q', '--amend', '--no-edit', '--date', PR_DATE, date=PR_DATE)
        self.assert_rejected(pipeline(workspace=amend), 'prepare', 'the recorded BUILD_DATE does not belong to this commit')

    def test_same_instant_with_another_offset_is_another_input(self):
        self.assert_rejected(pipeline(rebuild='local-b2'), 'record', 'the rebuild did not apply the recorded --build-date string')
        self.assert_rejected(pipeline(reference='local-b2'), 'reference', 'Melange did not apply the exact --build-date string')

    def test_source_or_configuration_changes_are_rejected(self):
        cases = [lambda checkout: (checkout / 'melange' / CONFIG).write_text((checkout / 'melange' / CONFIG).read_text() + '# x\n'),
                 lambda checkout: (checkout / 'melange/certificates/extra.pem').write_text('extra'),
                 lambda checkout: (checkout / 'melange/keys/wolfi-signing-key.json').unlink()]
        for change in cases:
            with self.subTest(change=change):
                self.assert_rejected(pipeline(workspace=change), 'prepare', 'the rebuild source directory differs from the reference sources')
        self.assert_rejected(pipeline(workspace=lambda checkout: (checkout / 'melange/link.yaml').symlink_to(CONFIG)), 'prepare',
                             'symbolic links are not accepted in the Melange source directory')
        self.assert_rejected(pipeline(reproduce_env=dict(MELANGE_CONFIG='other.yaml')), 'prepare',
                             'the rebuild request differs from the reference')
        other = MELANGE.rsplit('@', 1)[0] + '@sha256:' + 'c' * 64
        self.assert_rejected(pipeline(reproduce_env=dict(MELANGE_IMAGE=other)), 'prepare', 'the rebuild request differs from the reference')

    def test_missing_or_extra_dependencies_are_rejected(self):
        drop = lambda spec: spec['x86_64'].pop(('busybox', '1.38.0-r2'))
        extra = lambda spec: spec['x86_64'].update({('zlib', '1.3.1-r0'): synthetic('zlib', '1.3.1-r0', 'x86_64')})
        for edit in (drop, extra):
            with self.subTest(edit=edit):
                self.assert_rejected(pipeline(rebuild_cache=dict(edit=edit)), 'record', 'dependency materials differ from the x86_64 lock')
                self.assert_rejected(pipeline(reference_cache=dict(edit=edit)), 'reference',
                                     'dependency materials differ from the x86_64 lock')
        self.assert_rejected(pipeline(rebuild_cache=dict(after=lambda repo: shutil.rmtree(repo / 'aarch64'))), 'record',
                             'dependency materials differ from the aarch64 lock')

    def test_materially_different_dependency_is_inputs_differ(self):
        swap = lambda spec: spec['x86_64'].update({('busybox', '1.38.0-r2'): synthetic('busybox', '1.38.0-r2', 'x86_64', ' rebuilt')})
        diagnostics = self.assert_classified(pipeline(rebuild_cache=dict(edit=swap)), 'INPUTS_DIFFER')
        self.assertEqual(diagnostics['results']['x86_64']['dependencies_equal'], False)
        self.assertEqual(diagnostics['results']['aarch64']['dependencies_equal'], True)
        # Outputs were equal: the status names the input change, not a determinism failure.
        self.assertTrue(all(item['control_stream_equal'] and item['data_stream_equal']
                            for result in diagnostics['results'].values() for item in result['packages']))
        # Another resolved version: the rebuild lock and its cache move together.
        newer = lambda spec: spec['x86_64'].update({('busybox', '1.38.0-r3'): synthetic('busybox', '1.38.0-r3', 'x86_64')}) \
            or spec['x86_64'].pop(('busybox', '1.38.0-r2'))
        relock = tamper('x86_64', control=edit_text('.melange.yaml', 'busybox=1.38.0-r2', 'busybox=1.38.0-r3'))
        diagnostics = self.assert_classified(pipeline(rebuild_cache=dict(edit=newer), rebuild_outputs=relock,
                                                      rebuild_key=key('output', public=True)), 'INPUTS_DIFFER')
        self.assertFalse(diagnostics['results']['x86_64']['packages'][0]['control_stream_equal'])

    def test_tampered_control_or_data_is_classified_with_equal_inputs(self):
        comment = lambda files: files.update({'.PKGINFO': files['.PKGINFO'] + b'# rebuilt\n'})
        diagnostics = self.assert_classified(pipeline(rebuild_outputs=tamper('x86_64', control=comment),
                                                      rebuild_key=key('output', public=True)),
                                             'OUTPUTS_DIFFER_WITH_EQUAL_RECORDED_INPUTS')
        self.assertEqual(diagnostics['results']['x86_64'], dict(dependencies_equal=True, packages=[
            dict(file=APK, control_stream_equal=False, data_stream_equal=True)]))
        self.assertEqual(diagnostics['results']['aarch64']['packages'][0]['control_stream_equal'], True)
        ca = lambda files: files.update({'etc/ssl/certs/hgc03.pem': b'tampered'})
        diagnostics = self.assert_classified(pipeline(rebuild_outputs=tamper('aarch64', data=ca),
                                                      rebuild_key=key('output', public=True)),
                                             'OUTPUTS_DIFFER_WITH_EQUAL_RECORDED_INPUTS')
        self.assertEqual(diagnostics['results']['aarch64']['packages'][0],
                         dict(file=APK, control_stream_equal=False, data_stream_equal=False))
        self.assert_rejected(pipeline(rebuild_outputs=tamper('aarch64', keep_datahash=True, data=ca),
                                      rebuild_key=key('output', public=True)), 'record', 'APK data differs from its datahash')
        self.assert_rejected(pipeline(rebuild_outputs=tamper('x86_64', sign_with=None, control=comment)), 'record',
                             'signature by melange.rsa.pub does not verify')

    def test_cache_names_must_match_the_bytes(self):
        def overwrite(stream):
            def apply(repo):
                target = next((repo / 'x86_64/busybox-1.38.0-r2').glob('expand-apk*')) / stream
                donor = next((repo / 'x86_64/libgcc-16.2.0-r1').glob('expand-apk*')) / stream
                target.write_bytes(donor.read_bytes())
            return apply
        self.assert_rejected(pipeline(rebuild_cache=dict(after=overwrite('stream-0.tar.gz'))), 'record',
                             'cache entry names do not match their bytes')
        self.assert_rejected(pipeline(reference_cache=dict(after=overwrite('stream-1.tar.gz'))), 'reference',
                             'cache entry names do not match their bytes')
        self.assert_rejected(pipeline(rebuild_cache=dict(after=overwrite('stream-1.tar'))), 'record',
                             'expanded data differs from the cached data stream')

        def foreign(repo):
            # Another package's streams under consistent names: only .PKGINFO can tell.
            target, donor = repo / 'x86_64/busybox-1.38.0-r2', repo / 'x86_64/libgcc-16.2.0-r1'
            shutil.rmtree(target)
            shutil.copytree(donor, target, symlinks=True)
        self.assert_rejected(pipeline(rebuild_cache=dict(after=foreign)), 'record', 'cached control stream belongs to another package')

    def test_cache_symlinks_stay_inside_their_directory(self):
        def relink(target):
            def apply(repo):
                directory = repo / 'x86_64/busybox-1.38.0-r2'
                link = next(directory.glob('*.ctl.tar.gz'))
                stream = next(directory.glob('expand-apk*')) / 'stream-0.tar.gz'
                link.unlink()
                link.symlink_to(target(directory, stream))
            return apply
        outside = lambda directory, stream: shutil.copy(stream, directory.parents[3] / 'copy.tar.gz') and '../../../../copy.tar.gz'
        cases = [('cache entries must be relative symlinks', lambda directory, stream: str(stream)),
                 ('cache symlink leaves its cache directory', outside),
                 ('cache symlink leaves its cache directory', lambda directory, stream: os.path.relpath(
                     next((directory.parent / 'libgcc-16.2.0-r1').glob('expand-apk*')) / 'stream-0.tar.gz', directory)),
                 ('cache symlink does not point to its stream', lambda directory, stream: f'{stream.parent.name}/stream-1.tar.gz')]
        for reason, target in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(pipeline(rebuild_cache=dict(after=relink(target))), 'record', reason)

        def linked_expansion(repo):
            directory = repo / 'aarch64/glibc-2.44-2.44-r8'
            expanded = next(directory.glob('expand-apk*'))
            shutil.move(str(expanded), str(repo.parents[1] / 'moved'))
            expanded.symlink_to(repo.parents[1] / 'moved')
        self.assert_rejected(pipeline(rebuild_cache=dict(after=linked_expansion)), 'record', 'unrecognized package cache layout')

        def linked_repository(repo):
            shutil.move(str(repo), str(repo.parent.parent / 'elsewhere'))
            repo.symlink_to(repo.parent.parent / 'elsewhere')
        self.assert_rejected(pipeline(reference_cache=dict(after=linked_repository)), 'reference', 'unrecognized entry in the Melange cache')

    def test_resolution_index_must_be_signed_by_the_declared_keyring(self):
        cases = [('signature by wolfi-signing.rsa.pub does not verify', dict(signer=(key('other'), 'wolfi-signing.rsa.pub', None))),
                 ('signature by wolfi-signing.rsa.pub does not verify', dict(signer=(key('index'), 'wolfi-signing.rsa.pub', b'other'))),
                 ('resolution index is not signed by a declared keyring file', dict(signer=(key('index'), 'other.rsa.pub', None))),
                 ('truncated gzip stream', dict(after=lambda repo: (repo / 'x86_64/APKINDEX/1617709418.tmp').write_bytes(
                     (repo / 'x86_64/APKINDEX/1617709418.tmp').read_bytes()[:-40]))),
                 ('unrecognized APKINDEX cache layout', dict(after=lambda repo: (repo / 'x86_64/APKINDEX/ON2HE2LOMVSA====.tar.gz')
                                                             .symlink_to('1617709418.tmp'))),
                 ('the resolution index is missing from the Melange cache',
                  dict(after=lambda repo: shutil.rmtree(repo / 'aarch64/APKINDEX')))]
        for reason, options in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(pipeline(rebuild_cache=options), 'record', reason)
        keyring = lambda checkout: (checkout / 'melange/keys/wolfi-signing.rsa.pub').write_bytes(key('other', public=True).read_bytes())
        self.assert_rejected(pipeline(before_reference=keyring), 'reference', 'declared keyring file changed')

    def test_index_entry_must_describe_the_consumed_control_stream(self):
        def edit(change):
            def apply(arch, lines):
                return [change(line) if arch == 'x86_64' and '\nP:busybox\n' in line else line for line in lines]
            return apply
        reason = 'the resolution index does not describe the consumed control stream'
        cases = [edit(lambda line: 'C:Q1' + 'B' * 26 + '=' + line[line.index('\n'):]),
                 lambda arch, lines: [line for line in lines if '\nP:busybox\n' not in line],
                 lambda arch, lines: lines + [line for line in lines if '\nP:busybox\n' in line]]
        for entries in cases:
            with self.subTest(entries=entries):
                self.assert_rejected(pipeline(rebuild_cache=dict(entries=entries)), 'record', reason)
        self.assert_rejected(pipeline(reference_cache=dict(entries=cases[0])), 'reference', reason)

    def test_targets_must_be_exact(self):
        self.assert_rejected(pipeline(rebuild_outputs=lambda packages: shutil.rmtree(packages / 'aarch64')), 'record',
                             'outputs must cover exactly the reproducibility targets')
        self.assert_rejected(pipeline(rebuild_outputs=lambda packages: (packages / 'riscv64').mkdir()), 'record',
                             'outputs must cover exactly the reproducibility targets')
        self.assert_rejected(pipeline(rebuild_cache=dict(after=lambda repo: shutil.copytree(repo / 'x86_64', repo / 'riscv64',
                                                                                            symlinks=True))),
                             'record', 'unrecognized architecture in the Melange cache')

        def swap(packages):
            for arch in ARCHS:
                (packages / arch / APK).rename(packages / f'{arch}.apk')
            for arch, other in zip(ARCHS, reversed(ARCHS)):
                (packages / f'{other}.apk').rename(packages / arch / APK)
        self.assert_rejected(pipeline(rebuild_outputs=swap), 'record', 'APK metadata differs from its output location')
        relock = tamper('aarch64', control=edit_text('.melange.yaml', '- arm64', '- amd64'))
        self.assert_rejected(pipeline(rebuild_outputs=relock, rebuild_key=key('output', public=True)), 'record',
                             'rebuild lock for aarch64 is inconsistent')

    def test_output_signatures_must_verify_even_with_equal_streams(self):
        # The original outputs cannot pass as a rebuild: with another key they do not verify ...
        self.assert_rejected(pipeline(rebuild='hosted-develop', rebuild_key=FIXTURES / 'local-a1/melange.rsa.pub'), 'record',
                             'signature by melange.rsa.pub does not verify')
        # ... and with the reference key they are not a new build.
        self.assert_rejected(pipeline(rebuild='hosted-develop'), 'record', 'the rebuild must use its own ephemeral key')
        bad_index = lambda packages: resign(packages, key('output'), index_key=key('other'))
        self.assert_rejected(pipeline(rebuild_outputs=bad_index, rebuild_key=key('output', public=True)), 'record',
                             'signature by melange.rsa.pub does not verify')

        def foreign_index(packages):
            resign(packages, key('output'))
            (packages / 'x86_64/APKINDEX.tar.gz').write_bytes((packages / 'aarch64/APKINDEX.tar.gz').read_bytes())
        self.assert_rejected(pipeline(rebuild_outputs=foreign_index, rebuild_key=key('output', public=True)), 'record',
                             'APKINDEX does not describe the APK of its own build')

        def reference_index(repo, capture):
            (repo / 'packages/x86_64/APKINDEX.tar.gz').write_bytes(
                (FIXTURES / 'local-a1/packages/x86_64/APKINDEX.tar.gz').read_bytes())
        self.assert_rejected(pipeline(transit=reference_index), 'record', 'signature by melange.rsa.pub does not verify')

    def test_reference_outputs_must_be_the_hgc02_outputs(self):
        def rewrap(repo, capture):
            # Only the gzip MTIME of the signature stream changes: the RSA signature, C: and S: still match.
            apk = repo / 'packages/x86_64' / APK
            raw = bytearray(apk.read_bytes())
            raw[4:8] = (int.from_bytes(raw[4:8], 'little') + 1).to_bytes(4, 'little')
            apk.write_bytes(bytes(raw))
        self.assert_rejected(pipeline(transit=rewrap), 'record', 'downloaded reference APKs differ from the HGC-02 outputs')

    def test_receipts_must_bind_this_run_and_build(self):
        def rewrite(name, change, raw=False):
            def apply(repo, capture):
                path = (capture if name.startswith('melange-reproduction') else repo) / name
                document = json.loads(path.read_bytes())
                result = change(document)
                path.write_bytes(result if raw else canonical(document))
            return apply
        cases = [
            ('the reference capture is bound to other receipts', rewrite('binfmt-evidence.json', lambda d: d.update(qemu_version='9.9.9'))),
            ('reference receipts are not canonical',
             rewrite('melange-environment-evidence.json', lambda d: json.dumps(d, indent=1).encode(), raw=True)),
            ('reference receipts are not canonical',
             rewrite('melange-reproduction-reference.json', lambda d: json.dumps(d, sort_keys=True).encode(), raw=True)),
            ('the reference capture has another producer',
             rewrite('melange-reproduction-reference.json', lambda d: d['producer'].update(run_attempt=2))),
            ('the rebuild must run in another job',
             rewrite('melange-reproduction-reference.json', lambda d: d['producer'].update(job='melange-reproduce'))),
        ]
        for reason, transit in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(pipeline(transit=transit), 'prepare', reason)
        for overrides in (dict(GITHUB_RUN_ID='37729369156'), dict(GITHUB_SHA='f' * 40), dict(GITHUB_REF='refs/pull/1/merge'),
                          dict(GITHUB_REPOSITORY='alric-corp/other')):
            with self.subTest(overrides=overrides):
                self.assert_rejected(pipeline(reproduce_env=overrides), 'prepare', 'reference receipts belong to another run or commit')
        # A reference from a later attempt cannot feed an earlier one.
        self.assert_rejected(pipeline(env=dict(GITHUB_RUN_ATTEMPT='2'), reproduce_env=dict(GITHUB_RUN_ATTEMPT='1')), 'prepare',
                             'reference receipts belong to another run or commit')
        self.assert_reproduced(pipeline(env=dict(GITHUB_RUN_ATTEMPT='1'), reproduce_env=dict(GITHUB_RUN_ATTEMPT='2'),
                                        validate_env=dict(GITHUB_RUN_ATTEMPT='2')))

    def test_record_requires_the_environment_the_build_step_used(self):
        other = MELANGE.rsplit('@', 1)[0] + '@sha256:' + 'c' * 64
        for overrides in (dict(MELANGE_IMAGE=other), dict(BUILD_DATE=UTC_DATE)):
            with self.subTest(overrides=overrides):
                self.assert_rejected(pipeline(after_prepare=overrides), 'record',
                                     'the rebuild did not use the recorded tool and BUILD_DATE')

    def test_rebuild_must_be_independent(self):
        diagnostics = self.assert_classified(pipeline(reproduce_env=dict(RUNNER_NAME='GitHub Actions 1000000001')),
                                             'NOT_INDEPENDENT')
        self.assertEqual(diagnostics['independence'], 'CROSS_JOB_SAME_RUN')

    def test_tools_must_match_their_receipts(self):
        def changed(image, **fields):
            world = json.loads(json.dumps(WORLD))
            world['images'][image].update(fields)
            return world
        melange = WORLD['images'][MELANGE]
        cases = [('resolved Melange digest differs from the recorded digest',
                  dict(rebuild_world=changed(MELANGE, RepoDigests=['cgr.dev/chainguard/melange@sha256:' + 'c' * 64]))),
                 ('Melange host platform differs', dict(rebuild_world=changed(MELANGE, Architecture='arm64'))),
                 ('the rebuild Melange differs', dict(rebuild_world=dict(WORLD, version_stdout=json.dumps(
                     dict(HOSTED_VERSION, gitVersion='v0.61.3'))))),
                 ('the rebuild binfmt digest differs',
                  dict(rebuild_world=changed(BINFMT, RepoDigests=['tonistiigi/binfmt@sha256:' + 'c' * 64]))),
                 ('the rebuild binfmt pin differs', dict(reproduce_env=dict(BINFMT_IMAGE='docker.io/tonistiigi/binfmt@sha256:' + 'c' * 64))),
                 ('SOURCE_DATE_EPOCH reaches the Melange process', dict(rebuild_world=changed(
                     MELANGE, Config=dict(Env=melange['Config']['Env'] + ['SOURCE_DATE_EPOCH=1791436512']))))]
        for reason, options in cases:
            with self.subTest(reason=reason):
                self.assert_rejected(pipeline(**options), 'record', reason)
        self.assert_rejected(pipeline(world=changed(MELANGE, Config=dict(Env=['SOURCE_DATE_EPOCH=1']))), 'reference',
                             'SOURCE_DATE_EPOCH reaches the Melange process')
        self.assert_rejected(pipeline(bundle_env=dict(BINFMT_IMAGE=BINFMT.replace('68@', '69@'))), 'reference',
                             'pinned tools differ from their receipts')

    # --- validate gate ----------------------------------------------------------------------------------------

    def gate(self, change=None, before=None, raw=None, **environment):
        """Run only the gate on a copy of the default run's melange-repo and evidence."""
        with tempfile.TemporaryDirectory() as temporary:
            validate = Path(temporary)
            shutil.copytree(self.default.validate, validate / 'inputs')
            validate = validate / 'inputs'
            path = validate / 'melange-reproducibility/melange-reproducibility-evidence.json'
            if change:
                document = json.loads(path.read_bytes())
                change(document)
                path.write_bytes(canonical(document))
            if raw:
                path.write_bytes(raw(path.read_bytes()))
            if before:
                before(validate)
            outcome = SimpleNamespace(steps={}, failed=None)
            run_step(outcome, 'gate', GATE, validate, {**os.environ, **self.default.github, **environment})
            return outcome

    def test_gate_rejects_unapproved_or_unbound_evidence(self):
        def other(document, side, **fields):
            document[side]['producer'].update(fields)
        cases = [
            ('reproducibility evidence is not canonical', dict(raw=lambda raw: json.dumps(json.loads(raw), indent=1).encode())),
            ('the Melange CA package was not reproduced', dict(change=lambda d: d.update(status='INPUTS_DIFFER'))),
            ('the Melange CA package was not reproduced', dict(change=lambda d: d.update(independence='CROSS_RUN'))),
            ('the Melange CA package was not reproduced', dict(change=lambda d: d.update(kind='melange-reproducibility-diagnostics'))),
            ('reproducibility evidence is bound to other receipts', dict(before=lambda v: (v / 'melange-repo/binfmt-evidence.json')
                                                                         .write_bytes(b'{}'))),
            ('reproducibility evidence is bound to other receipts',
             dict(change=lambda d: d['receipts'].update(environment_digest='sha256:' + '0' * 64))),
            ('reproducibility evidence belongs to another run', dict(GITHUB_RUN_ID='1')),
            ('reproducibility evidence belongs to another run', dict(GITHUB_SHA='f' * 40)),
            ('reproducibility evidence belongs to another run', dict(change=lambda d: other(d, 'rebuild', run_attempt=2))),
            ('the rebuild was not independent', dict(change=lambda d: other(d, 'rebuild', runner='GitHub Actions 1000000001'))),
            ('reproduction input digest mismatch',
             dict(change=lambda d: d['reproduction_inputs']['build'].update(build_date=UTC_DATE))),
            ('candidate packages differ from the reproduced reference',
             dict(before=lambda v: shutil.copy(FIXTURES / 'local-a1/packages/x86_64' / APK, v / 'melange-repo/packages/x86_64'))),
            ('output signatures were not verified',
             dict(change=lambda d: d['signatures']['rebuild']['packages']['aarch64'][0].update(verified=False))),
            ('output signatures were not verified',
             dict(change=lambda d: d['signatures']['rebuild'].update(public_key_sha256=d['signatures']['reference']['public_key_sha256']))),
            ('reproducibility results are not all equal',
             dict(change=lambda d: d['results']['x86_64']['packages'][0].update(data_stream_equal=False))),
            ('reproducibility results are not all equal', dict(change=lambda d: d['results'].pop('aarch64'))),
        ]
        for reason, options in cases:
            with self.subTest(reason=reason):
                outcome = self.gate(**options)
                self.assertNotEqual(outcome.steps['gate'].returncode, 0)
                self.assertIn(reason, outcome.steps['gate'].stderr)

    def test_missing_evidence_or_failed_rebuild_blocks_candidates(self):
        outcome = self.gate(before=lambda v: (v / 'melange-reproducibility/melange-reproducibility-evidence.json').unlink())
        self.assertNotEqual(outcome.steps['gate'].returncode, 0)
        self.assertIn('No such file or directory', outcome.steps['gate'].stderr)
        # A failed rebuild leaves diagnostics only; presenting them as evidence is still refused.
        swap = lambda spec: spec['aarch64'].update({('libgcc', '16.2.0-r1'): synthetic('libgcc', '16.2.0-r1', 'aarch64', ' other')})
        failed = pipeline(rebuild_cache=dict(edit=swap))
        self.assert_classified(failed, 'INPUTS_DIFFER')
        outcome = self.gate(raw=lambda _: failed.diagnostics)
        self.assertNotEqual(outcome.steps['gate'].returncode, 0)
        self.assertIn('the Melange CA package was not reproduced', outcome.steps['gate'].stderr)

    def test_evidence_propagates_without_byte_changes(self):
        upload = step('melange-reproduce', 'Upload Melange reproducibility evidence')['with']
        self.assertEqual(upload['path'], '${{ runner.temp }}/melange-reproducibility-artifact/')
        download = step('validate', 'Download Melange reproducibility evidence')['with']
        self.assertEqual((download['name'], download['path']), (upload['name'], 'melange-reproducibility'))
        build = step('validate', 'Build multi-architecture OCI artifact once')['run']
        copy = build[build.index('cp melange-reproducibility/'):].split('\n\n')[0].split('\n', 2)
        command = '\n'.join(copy[:2])
        self.assertEqual(command, 'cp melange-reproducibility/melange-reproducibility-evidence.json \\\n'
                                  '  "${FRAMEWORK}.oci/melange-reproducibility-evidence.json"')
        replay = step('validate', 'Preserve SBOMs and replay inputs')['with']['path']
        self.assertIn('${{ matrix.framework }}.oci/melange-reproducibility-evidence.json', replay)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'melange-reproducibility').mkdir()
            (root / 'go1-26.oci').mkdir()
            (root / 'melange-reproducibility/melange-reproducibility-evidence.json').write_bytes(self.default.evidence)
            subprocess.run(['bash', '-c', command], cwd=root, env=dict(os.environ, FRAMEWORK='go1-26'), check=True)
            self.assertEqual((root / 'go1-26.oci/melange-reproducibility-evidence.json').read_bytes(), self.default.evidence)
        # The receipts in the evidence are the hashes of the original HGC-01/HGC-02 bytes that travel unchanged.
        evidence = json.loads(self.default.evidence)
        self.assertEqual(evidence['receipts']['binfmt_evidence'], sha256(self.default.binfmt))
        self.assertEqual(evidence['receipts']['melange_environment_evidence'], sha256(self.default.environment))
        self.assertEqual(evidence['receipts']['reproduction_reference'], sha256(self.default.capture))


if __name__ == '__main__':
    unittest.main()
