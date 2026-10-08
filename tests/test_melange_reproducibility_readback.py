"""HGC-03 read-back: staging of the rebuild's public outputs, the expanded artifact at the gate, and the offline verifier.

The positive control is the real local rebuild local-a1 (pinned Melange, its own ephemeral key) against the hosted
develop reference; see test_melange_reproducibility.py for the fixtures and the job harness reused here.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import test_melange_reproducibility as harness  # noqa: E402
from test_melange_reproducibility import (APK, ARCHS, FIXTURES, GATE, RECORD, STAGE, canonical, key, members, pipeline,  # noqa: E402
                                          q1, resign, sha256, step, streams, tar_gz)
import verify_reproducibility  # noqa: E402

setUpModule = harness.setUpModule
tearDownModule = harness.tearDownModule
EVIDENCE = 'melange-reproducibility-evidence.json'
REBUILD_FILES = {'rebuild/melange.rsa.pub'} | {f'rebuild/packages/{arch}/{name}' for arch in ARCHS for name in (APK, 'APKINDEX.tar.gz')}
RUN = dict(repository='alric-corp/alric-containers-image-base', run_id=37731450779, run_attempt=1, ref='refs/heads/develop')


def rewrite_evidence(change):
    """before_stage hook: edit the approved receipt in RUNNER_TEMP and keep it canonical."""
    def apply(checkout, temp):
        path = temp / 'melange-reproducibility' / EVIDENCE
        document = json.loads(path.read_bytes())
        change(document)
        path.write_bytes(canonical(document))
    return apply


def rebind(reproducibility):
    """Make the receipt describe whatever rebuild files are present (a forger's best effort; results stay 'equal')."""
    path = reproducibility / EVIDENCE
    evidence = json.loads(path.read_bytes())
    signed = evidence['signatures']['rebuild']
    signed['public_key_sha256'] = sha256((reproducibility / 'rebuild/melange.rsa.pub').read_bytes())
    for arch in ARCHS:
        raw = (reproducibility / 'rebuild/packages' / arch / APK).read_bytes()
        signature, control, data = streams(raw)
        evidence['rebuild']['outputs'][arch] = [dict(file=APK, apk_sha256=sha256(raw), apk_size=len(raw),
                                                     control_stream_sha256=sha256(control), control_checksum=q1(control),
                                                     data_stream_sha256=sha256(data), datahash=sha256(data))]
        signed['packages'][arch][0]['signature_stream_sha256'] = sha256(signature)
        signed['apkindex'][arch]['sha256'] = sha256((reproducibility / 'rebuild/packages' / arch / 'APKINDEX.tar.gz').read_bytes())
    path.write_bytes(canonical(evidence))


def edit_rebuild(arch, control=None, data=None):
    """Edit one rebuilt APK in place, keeping its datahash coherent (streams not edited keep their bytes)."""
    def apply(packages):
        apk = packages / arch / APK
        signature, control_stream, data_stream = streams(apk.read_bytes())
        if data:
            files = members(data_stream)
            data(files)
            data_stream = tar_gz(files)
        files = members(control_stream)
        if control:
            control(files)
        if data:
            files['.PKGINFO'] = b''.join(f'datahash = {sha256(data_stream)}\n'.encode() if line.startswith(b'datahash = ') else line
                                         for line in files['.PKGINFO'].splitlines(keepends=True))
        if control or data:
            control_stream = tar_gz(files)
        apk.write_bytes(signature + control_stream + data_stream)
    return apply


class Readback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kept = tempfile.TemporaryDirectory()
        cls.default = pipeline(keep=Path(cls.kept.name))

    @classmethod
    def tearDownClass(cls):
        cls.kept.cleanup()

    def assert_staging_rejected(self, outcome, reason):
        """The approved receipt exists, staging fails, nothing is staged for upload and the gate never runs."""
        self.assertIsNotNone(outcome.evidence)
        self.assertEqual(outcome.failed, 'stage', {name: result.stderr[-1500:] for name, result in outcome.steps.items()})
        self.assertIn(reason, outcome.steps['stage'].stderr)
        self.assertIsNone(outcome.staged)
        self.assertNotIn('gate', outcome.steps)

    def gate(self, before):
        with tempfile.TemporaryDirectory() as temporary:
            validate = Path(temporary, 'inputs')
            shutil.copytree(self.default.validate, validate)
            before(validate)
            outcome = harness.SimpleNamespace(steps={}, failed=None)
            harness.run_step(outcome, 'gate', GATE, validate, {**os.environ, **self.default.github})
            return outcome.steps['gate']

    def artifacts(self, temporary, change=None):
        root = Path(temporary, 'artifacts')
        shutil.copytree(self.default.artifacts, root)
        if change:
            change(root)
        return root

    def verify(self, root, **metadata):
        identity = {**RUN, 'source_sha': self.default.sha, **metadata}
        return verify_reproducibility.verify(root / 'melange-repo', root / 'melange-reproduction-reference',
                                             root / 'melange-reproducibility', **identity)

    def assert_verifier_rejects(self, change, reason, **metadata):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.artifacts(temporary, change)
            with self.assertRaises((verify_reproducibility.Failure, KeyError)) as raised:
                self.verify(root, **metadata)
            self.assertIn(reason, str(raised.exception))

    # --- staging ------------------------------------------------------------------------------------------------

    def test_staging_preserves_exactly_the_real_rebuild_outputs(self):
        outcome = self.default
        self.assertIsNone(outcome.failed)
        self.assertEqual(outcome.steps['gate'].returncode, 0, outcome.steps['gate'].stderr)
        self.assertEqual(set(outcome.staged), {EVIDENCE} | REBUILD_FILES)
        self.assertEqual(outcome.staged[EVIDENCE], outcome.evidence)
        self.assertEqual(outcome.staged['rebuild/melange.rsa.pub'], (FIXTURES / 'local-a1/melange.rsa.pub').read_bytes())
        evidence = json.loads(outcome.evidence)
        for arch in ARCHS:
            for name in (APK, 'APKINDEX.tar.gz'):
                rebuilt = outcome.staged[f'rebuild/packages/{arch}/{name}']
                self.assertEqual(rebuilt, (FIXTURES / 'local-a1/packages' / arch / name).read_bytes())
                self.assertNotEqual(rebuilt, (FIXTURES / 'hosted-develop/packages' / arch / name).read_bytes())
            self.assertEqual(sha256(outcome.staged[f'rebuild/packages/{arch}/{APK}']), evidence['rebuild']['outputs'][arch][0]['apk_sha256'])
        # What upload-artifact packs from the staging directory: the receipt at the root, rebuild/ beside it.
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary, 'artifact.zip')
            with zipfile.ZipFile(archive, 'w') as packed:
                for name, data in outcome.staged.items():
                    packed.writestr(name, data)
            with zipfile.ZipFile(archive) as packed:
                self.assertEqual(set(packed.namelist()), {EVIDENCE} | REBUILD_FILES)
                self.assertFalse(any('melange.rsa' == Path(name).name or 'apk-cache' in name for name in packed.namelist()))
                self.assertFalse(any(b'PRIVATE KEY' in packed.read(name) for name in packed.namelist()))

    def test_staging_rejects_incomplete_outputs(self):
        cases = [(f'rebuild/packages/{arch}/{APK}', 'rebuild outputs differ from the evidence') for arch in ARCHS]
        cases += [(f'rebuild/packages/{arch}/APKINDEX.tar.gz', 'rebuild outputs differ from the evidence') for arch in ARCHS]
        cases += [('rebuild/melange.rsa.pub', 'rebuild/melange.rsa.pub is not a regular file')]
        for name, reason in cases:
            with self.subTest(missing=name):
                drop = lambda checkout, temp, name=name: (checkout / 'melange' / name[len('rebuild/'):]).unlink()
                self.assert_staging_rejected(pipeline(before_stage=drop), reason)

    def test_staging_rejects_swapped_or_altered_outputs(self):
        def replace(name, source):
            def apply(checkout, temp):
                (checkout / 'melange' / name).write_bytes(source())
            return apply
        reference = lambda arch, name: lambda: (FIXTURES / 'hosted-develop/packages' / arch / name).read_bytes()
        cases = [(f'packages/x86_64/{APK}', reference('x86_64', APK)),
                 (f'packages/aarch64/{APK}', lambda: (FIXTURES / 'local-a1/packages/aarch64' / APK).read_bytes() + b'\0'),
                 ('packages/x86_64/APKINDEX.tar.gz', reference('x86_64', 'APKINDEX.tar.gz')),
                 ('packages/aarch64/APKINDEX.tar.gz', lambda: (FIXTURES / 'local-a1/packages/x86_64/APKINDEX.tar.gz').read_bytes()),
                 ('melange.rsa.pub', lambda: (FIXTURES / 'hosted-develop/melange.rsa.pub').read_bytes())]
        for name, source in cases:
            with self.subTest(replaced=name):
                self.assert_staging_rejected(pipeline(before_stage=replace(name, source)), f'rebuild/{name} differs from the evidence')

    def test_staging_rejects_a_changed_or_foreign_receipt(self):
        def reserialize(checkout, temp):
            path = temp / 'melange-reproducibility' / EVIDENCE
            path.write_bytes(json.dumps(json.loads(path.read_bytes()), indent=1).encode())
        self.assert_staging_rejected(pipeline(before_stage=reserialize), 'only an approved canonical evidence is staged')
        self.assert_staging_rejected(pipeline(before_stage=rewrite_evidence(lambda d: d.update(status='INPUTS_DIFFER'))),
                                     'only an approved canonical evidence is staged')
        for field, value in (('runner', 'GitHub Actions 1000000001'), ('run_id', 1), ('run_attempt', 2), ('job', 'melange-bundle')):
            with self.subTest(field=field):
                change = rewrite_evidence(lambda d, field=field, value=value: d['rebuild']['producer'].update({field: value}))
                self.assert_staging_rejected(pipeline(before_stage=change), 'the evidence was not produced by this rebuild')
        swap = rewrite_evidence(lambda d: d['rebuild']['outputs']['x86_64'][0].update(
            apk_sha256=sha256((FIXTURES / 'hosted-develop/packages/x86_64' / APK).read_bytes())))
        self.assert_staging_rejected(pipeline(before_stage=swap), f'rebuild/packages/x86_64/{APK} differs from the evidence')

    def test_staging_rejects_unexpected_files_and_paths(self):
        extra = [lambda checkout, temp: (checkout / 'melange/packages/x86_64/extra.apk').write_bytes(b'extra'),
                 lambda checkout, temp: (checkout / 'melange/packages/x86_64/melange.rsa').write_bytes(
                     (checkout / 'melange/melange.rsa').read_bytes()),
                 lambda checkout, temp: (checkout / 'melange/packages/riscv64').mkdir() or (checkout / 'melange/packages/riscv64' / APK)
                 .write_bytes(b'apk')]
        for change in extra:
            with self.subTest(change=change):
                self.assert_staging_rejected(pipeline(before_stage=change), 'rebuild outputs differ from the evidence')
        for name in ('../../melange.rsa', 'melange.rsa', '../escape.apk'):
            with self.subTest(name=name):
                forged = rewrite_evidence(lambda d, name=name: d['rebuild']['outputs']['x86_64'][0].update(file=name))
                self.assert_staging_rejected(pipeline(before_stage=forged), 'unexpected output name')
        leftover = lambda checkout, temp: (temp / 'melange-reproducibility-artifact').mkdir()
        outcome = pipeline(before_stage=leftover)
        self.assertEqual(outcome.failed, 'stage')
        self.assertIn('FileExistsError', outcome.steps['stage'].stderr)

    def test_staging_rejects_symlinks(self):
        def link(name):
            def apply(checkout, temp):
                target = checkout / 'melange' / name
                moved = temp / ('moved-' + target.name)
                shutil.move(str(target), str(moved))
                target.symlink_to(moved)
            return apply
        self.assert_staging_rejected(pipeline(before_stage=link(f'packages/x86_64/{APK}')), 'rebuild outputs must be regular files')
        self.assert_staging_rejected(pipeline(before_stage=link('melange.rsa.pub')), 'rebuild/melange.rsa.pub is not a regular file')

    def test_private_key_and_cache_stay_out_of_the_artifact(self):
        # The rebuild workspace holds a private key and RUNNER_TEMP the dependency cache; only the derived set is staged.
        self.assertNotIn('melange.rsa', {Path(name).name for name in self.default.staged})
        self.assertFalse(any('apk-cache' in name or name.startswith('melange/') for name in self.default.staged))
        stage = STAGE['run']
        self.assertNotIn('melange-apk-cache', stage)
        self.assertNotIn("'melange/melange.rsa'", stage)
        for job in harness.JOBS.values():
            for definition in job['steps']:
                if definition.get('uses', '').startswith('actions/upload-artifact@'):
                    path = definition['with']['path']
                    self.assertNotIn('melange-apk-cache', path)
                    self.assertNotRegex(path, r'(^|\s)melange/(\s|$)|melange/\*\*')
        upload = step('melange-reproduce', 'Upload Melange reproducibility evidence')['with']
        self.assertEqual(upload['path'], '${{ runner.temp }}/melange-reproducibility-artifact/')

    def test_staging_refuses_private_key_material_even_when_bound(self):
        """A 'public' key file holding a private key, already hashed into the receipt, is still refused."""
        def private_as_public(checkout, temp):
            pem = key('output').read_bytes()
            (checkout / 'melange/melange.rsa.pub').write_bytes(pem)
            rewrite_evidence(lambda d: d['signatures']['rebuild'].update(public_key_sha256=sha256(pem)))(checkout, temp)
        self.assert_staging_rejected(pipeline(before_stage=private_as_public), 'a private key was staged')

    def test_staging_and_upload_are_mandatory(self):
        names = [s.get('name') for s in harness.JOBS['melange-reproduce']['steps']]
        order = ['Record Melange reproducibility evidence', 'Stage rebuild outputs for external read-back',
                 'Upload Melange reproducibility evidence', 'Preserve reproducibility diagnostics']
        self.assertEqual([names.index(name) for name in order], sorted(names.index(name) for name in order))
        upload = step('melange-reproduce', 'Upload Melange reproducibility evidence')
        for definition in (RECORD, STAGE, upload):
            self.assertNotIn('if', definition)
            self.assertNotIn('continue-on-error', definition)
        self.assertEqual((upload['with']['name'], upload['with']['if-no-files-found'], upload['with']['retention-days']),
                         ('melange-reproducibility', 'error', 30))
        diagnostics = step('melange-reproduce', 'Preserve reproducibility diagnostics')['with']
        self.assertNotEqual(diagnostics['name'], upload['with']['name'])
        self.assertNotIn('melange-reproducibility-artifact', diagnostics['path'])

    # --- gate -------------------------------------------------------------------------------------------------

    def test_gate_requires_the_complete_expanded_artifact(self):
        self.assertEqual(self.default.steps['gate'].returncode, 0)
        reason = 'rebuild outputs are not preserved with the evidence'
        cases = [lambda v: (v / 'melange-reproducibility/rebuild/packages/x86_64' / APK).unlink(),
                 lambda v: (v / 'melange-reproducibility/rebuild/melange.rsa.pub').unlink(),
                 lambda v: (v / 'melange-reproducibility/rebuild/packages/aarch64/APKINDEX.tar.gz').write_bytes(b'other'),
                 lambda v: shutil.copy(FIXTURES / 'hosted-develop/packages/x86_64' / APK, v / 'melange-reproducibility/rebuild/packages/x86_64'),
                 lambda v: (v / 'melange-reproducibility/rebuild/extra.txt').write_text('extra'),
                 lambda v: shutil.rmtree(v / 'melange-reproducibility/rebuild')]
        for change in cases:
            with self.subTest(change=change):
                result = self.gate(change)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(reason, result.stderr)

        def link(v):
            target = v / 'melange-reproducibility/rebuild/melange.rsa.pub'
            moved = v / 'outside.pub'
            shutil.move(str(target), str(moved))
            target.symlink_to(moved)
        result = self.gate(link)
        self.assertIn('reproducibility artifact holds only regular files', result.stderr)

    def test_receipts_are_preserved_byte_for_byte(self):
        artifacts = self.default.artifacts
        self.assertEqual((artifacts / 'melange-reproducibility' / EVIDENCE).read_bytes(), self.default.evidence)
        self.assertEqual((artifacts / 'melange-reproduction-reference/melange-reproduction-reference.json').read_bytes(),
                         self.default.capture)
        self.assertEqual((artifacts / 'melange-repo/binfmt-evidence.json').read_bytes(), self.default.binfmt)
        self.assertEqual((artifacts / 'melange-repo/melange-environment-evidence.json').read_bytes(), self.default.environment)
        # The OCI layout and replay artifacts still receive only the receipt, from its unchanged artifact path.
        build = step('validate', 'Build multi-architecture OCI artifact once')['run']
        self.assertIn('cp melange-reproducibility/melange-reproducibility-evidence.json \\\n', build)
        self.assertNotIn('melange-reproducibility/rebuild', build)

    def test_new_limits_text_leaves_inputs_and_digest_alone(self):
        evidence = json.loads(self.default.evidence)
        limit = next(item for item in evidence['limits'] if item.startswith('Dependency identity'))
        self.assertEqual(limit, 'Dependency identity covers the original control and data streams consumed by these two builds; a '
                                'signature stream of the served APK, when present, is not cached, and the cached streams are not '
                                'compared with the origin.')
        self.assertNotIn('no complete dependency APK is recovered', json.dumps(evidence))
        self.assertNotIn('limits', json.dumps(evidence['reproduction_inputs']))
        self.assertEqual(evidence['reproduction_input_digest'], 'sha256:' + sha256(canonical(evidence['reproduction_inputs'])))

    # --- offline verifier ----------------------------------------------------------------------------------------

    def test_verifier_reads_back_a_real_reproduction(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.artifacts(temporary)
            report = self.verify(root)
            self.assertEqual(report['result'], 'READBACK_VERIFIED')
            self.assertEqual(report['reproduction_input_digest'], json.loads(self.default.evidence)['reproduction_input_digest'])
            self.assertNotEqual(report['reference_public_key_sha256'], report['rebuild_public_key_sha256'])
            for arch in ARCHS:
                package = report['packages'][f'{arch}/{APK}']
                parts = streams((FIXTURES / 'local-a1/packages' / arch / APK).read_bytes())
                self.assertEqual((package['control_stream_sha256'], package['data_stream_sha256']), (sha256(parts[1]), sha256(parts[2])))
                self.assertNotEqual(package['reference_apk_sha256'], package['rebuild_apk_sha256'])
            arguments = ['--melange-repo', str(root / 'melange-repo'), '--reference', str(root / 'melange-reproduction-reference'),
                         '--reproducibility', str(root / 'melange-reproducibility'), '--repository', RUN['repository'],
                         '--run-id', str(RUN['run_id']), '--run-attempt', '1', '--source-sha', self.default.sha, '--ref', RUN['ref']]
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(verify_reproducibility.main(arguments), 0)
            self.assertEqual(json.loads(output.getvalue())['result'], 'READBACK_VERIFIED')
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(verify_reproducibility.main(arguments[:-1] + ['refs/heads/main']), 1)
            self.assertIn('READBACK_FAILED: binfmt receipt does not belong to this run', errors.getvalue())

    def test_verifier_compares_the_streams_themselves(self):
        """A forged receipt that binds tampered rebuild files and still claims equality is refused by the byte comparison."""
        def forge(arch, sign_with='output', **edits):
            def apply(root):
                reproducibility = root / 'melange-reproducibility'
                edit_rebuild(arch, **edits)(reproducibility / 'rebuild/packages')
                resign(reproducibility / 'rebuild/packages', key(sign_with))
                shutil.copy(key('output', public=True), reproducibility / 'rebuild/melange.rsa.pub')
                rebind(reproducibility)
            return apply
        comment = lambda files: files.update({'.PKGINFO': files['.PKGINFO'] + b'# rebuilt\n'})
        self.assert_verifier_rejects(forge('x86_64', control=comment), f'x86_64/{APK}: control streams differ')
        extra = lambda files: files.update({'etc/ssl/certs/hgc03.pem': b'tampered'})
        self.assert_verifier_rejects(forge('aarch64', data=extra), f'aarch64/{APK}: control streams differ')

        def data_only(root):
            # Same control bytes (datahash kept): only a direct data comparison or the datahash can tell.
            reproducibility = root / 'melange-reproducibility'
            apk = reproducibility / 'rebuild/packages/aarch64' / APK
            signature, control, data = streams(apk.read_bytes())
            files = members(data)
            files['etc/ssl/certs/hgc03.pem'] = b'tampered'
            apk.write_bytes(signature + control + tar_gz(files))
            resign(reproducibility / 'rebuild/packages', key('output'))
            shutil.copy(key('output', public=True), reproducibility / 'rebuild/melange.rsa.pub')
            rebind(reproducibility)
        self.assert_verifier_rejects(data_only, 'package metadata differs')

    def test_verifier_requires_valid_signatures_even_with_equal_streams(self):
        def wrong_signer(root):
            reproducibility = root / 'melange-reproducibility'
            resign(reproducibility / 'rebuild/packages', key('other'), index_key=key('output'))
            shutil.copy(key('output', public=True), reproducibility / 'rebuild/melange.rsa.pub')
            rebind(reproducibility)
        self.assert_verifier_rejects(wrong_signer, 'signature does not verify')

        def foreign_index(root):
            reproducibility = root / 'melange-reproducibility'
            shutil.copy(root / 'melange-repo/packages/x86_64/APKINDEX.tar.gz', reproducibility / 'rebuild/packages/x86_64')
            rebind(reproducibility)
        self.assert_verifier_rejects(foreign_index, 'rebuild x86_64: APKINDEX signature does not verify')

        def other_outputs(root):
            # Signed by the rebuild's own key, but describing the aarch64 outputs instead of x86_64.
            reproducibility = root / 'melange-reproducibility'
            resign(reproducibility / 'rebuild/packages', key('output'))
            shutil.copy(key('output', public=True), reproducibility / 'rebuild/melange.rsa.pub')
            shutil.copy(reproducibility / 'rebuild/packages/aarch64/APKINDEX.tar.gz', reproducibility / 'rebuild/packages/x86_64')
            rebind(reproducibility)
        self.assert_verifier_rejects(other_outputs, 'rebuild x86_64: APKINDEX does not describe exactly its own outputs')

        def reference_key(root):
            reproducibility = root / 'melange-reproducibility'
            shutil.copy(root / 'melange-repo/melange.rsa.pub', reproducibility / 'rebuild/melange.rsa.pub')
            rebind(reproducibility)
        self.assert_verifier_rejects(reference_key, 'reuses the reference key')
        self.assert_verifier_rejects(lambda root: shutil.copy(key('output', public=True), root / 'melange-reproducibility/rebuild/melange.rsa.pub'),
                                     'rebuild public key differs from the evidence')

    def test_verifier_refuses_copied_reference_outputs(self):
        """The reference outputs and key presented as a rebuild pass every check except the distinct-key rule."""
        def copy_reference(root):
            reproducibility = root / 'melange-reproducibility'
            shutil.rmtree(reproducibility / 'rebuild/packages')
            shutil.copytree(root / 'melange-repo/packages', reproducibility / 'rebuild/packages')
            shutil.copy(root / 'melange-repo/melange.rsa.pub', reproducibility / 'rebuild/melange.rsa.pub')
            rebind(reproducibility)
        self.assert_verifier_rejects(copy_reference, 'reuses the reference key')

    def test_verifier_recomputes_inputs_and_digest(self):
        def inputs(change, redigest):
            def apply(root):
                path = root / 'melange-reproducibility' / EVIDENCE
                evidence = json.loads(path.read_bytes())
                change(evidence['reproduction_inputs'])
                if redigest:
                    evidence['reproduction_input_digest'] = 'sha256:' + sha256(canonical(evidence['reproduction_inputs']))
                path.write_bytes(canonical(evidence))
            return apply
        later = lambda inputs: inputs['build'].update(build_date='2026-10-08T05:15:12Z')
        self.assert_verifier_rejects(inputs(later, True), 'reproduction inputs do not follow from the receipts')
        drop = lambda inputs: inputs['targets']['x86_64']['dependencies'].pop()
        self.assert_verifier_rejects(inputs(drop, True), 'reproduction inputs do not follow from the receipts')

        def digest_only(root):
            path = root / 'melange-reproducibility' / EVIDENCE
            evidence = json.loads(path.read_bytes())
            evidence['reproduction_input_digest'] = 'sha256:' + '0' * 64
            path.write_bytes(canonical(evidence))
        self.assert_verifier_rejects(digest_only, 'reproduction input digest mismatch')

    def test_verifier_binds_exact_bytes_even_when_signatures_hold(self):
        """Only the gzip MTIME of the signature stream changes: RSA, C: and S: still hold; the receipt hash does not."""
        def rewrap(path):
            raw = bytearray(path.read_bytes())
            raw[4:8] = (int.from_bytes(raw[4:8], 'little') + 1).to_bytes(4, 'little')
            path.write_bytes(bytes(raw))
        self.assert_verifier_rejects(lambda root: rewrap(root / 'melange-repo/packages/x86_64' / APK),
                                     f'reference x86_64/{APK}: bytes differ from the receipt')
        self.assert_verifier_rejects(lambda root: rewrap(root / 'melange-reproducibility/rebuild/packages/aarch64' / APK),
                                     f'rebuild aarch64/{APK}: bytes differ from the receipt')

    def test_verifier_checks_the_recorded_build_date(self):
        """A capture rewritten with the same instant in UTC, re-bound everywhere, no longer matches the APKs."""
        def utc(root):
            capture_path = root / 'melange-reproduction-reference/melange-reproduction-reference.json'
            capture = json.loads(capture_path.read_bytes())
            capture['temporal']['build_date'] = '2026-10-08T05:15:12Z'
            capture_path.write_bytes(canonical(capture))
            path = root / 'melange-reproducibility' / EVIDENCE
            evidence = json.loads(path.read_bytes())
            evidence['receipts']['reproduction_reference'] = sha256(capture_path.read_bytes())
            evidence['reproduction_inputs']['build']['build_date'] = '2026-10-08T05:15:12Z'
            evidence['reproduction_input_digest'] = 'sha256:' + sha256(canonical(evidence['reproduction_inputs']))
            path.write_bytes(canonical(evidence))
        self.assert_verifier_rejects(utc, 'not built with the recorded BUILD_DATE')

    def test_verifier_binds_artifacts_to_their_origin(self):
        for metadata in (dict(run_id=1), dict(source_sha='f' * 40), dict(ref='refs/pull/1/merge'), dict(repository='alric-corp/other'),
                         dict(run_attempt=0)):
            with self.subTest(metadata=metadata):
                self.assert_verifier_rejects(None, 'does not belong to this run', **metadata)
        self.assert_verifier_rejects(lambda root: (root / 'melange-repo/binfmt-evidence.json').write_bytes(
            canonical(dict(json.loads((root / 'melange-repo/binfmt-evidence.json').read_bytes()), qemu_version='9.9.9'))),
            'reference capture is bound to other receipts')
        self.assert_verifier_rejects(lambda root: shutil.copy(FIXTURES / 'local-a1/packages/x86_64' / APK, root / 'melange-repo/packages/x86_64'),
                                     f'reference x86_64/{APK}: bytes differ from the receipt')
        self.assert_verifier_rejects(lambda root: (root / 'melange-reproducibility/rebuild/packages/x86_64' / APK).write_bytes(
            (root / 'melange-reproducibility/rebuild/packages/x86_64' / APK).read_bytes() + b'\0'),
            f'rebuild x86_64/{APK}: bytes differ from the receipt')
        self.assert_verifier_rejects(lambda root: (root / 'melange-reproducibility/rebuild/notes.txt').write_text('x'),
                                     'melange-reproducibility holds other files')
        self.assert_verifier_rejects(lambda root: (root / 'melange-repo/notes.txt').write_text('x'), 'melange-repo holds other files')
        self.assert_verifier_rejects(lambda root: (root / 'melange-reproducibility' / EVIDENCE).write_bytes(
            json.dumps(json.loads((root / 'melange-reproducibility' / EVIDENCE).read_bytes()), indent=1).encode()),
            'melange-reproducibility-evidence.json is not canonical')

        def other_run(root):
            # A complete reproduction from another run, mixed with this run's reference artifacts.
            other = pipeline(env=dict(GITHUB_RUN_ID='42'), keep=root / 'other')
            shutil.rmtree(root / 'melange-reproducibility')
            shutil.copytree(other.artifacts / 'melange-reproducibility', root / 'melange-reproducibility')
        self.assert_verifier_rejects(other_run, 'evidence_reference receipt does not belong to this run')


if __name__ == '__main__':
    unittest.main()
