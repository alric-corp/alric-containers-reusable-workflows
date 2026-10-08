"""Offline read-back of one HGC-03 reproduction from its downloaded artifacts (see docs/apko-contract.md).

Usage:
  python3 scripts/verify_reproducibility.py --melange-repo DIR --reference DIR --reproducibility DIR \
      --repository OWNER/REPO --run-id N --run-attempt N --source-sha SHA --ref REF

The three directories are the extracted artifacts melange-repo, melange-reproduction-reference and
melange-reproducibility of one workflow run. Repository, run, attempt, source SHA and ref come from that run's
metadata (GitHub API), never from the artifacts themselves. Needs Python 3.9+ and openssl; no build, private key,
cloud credential or package repository access. Workflows never call this file: their checkout is the caller's.
"""
import argparse
import base64
import datetime
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import zlib

SIGNATURE = '.SIGN.RSA256.melange.rsa.pub'


class Failure(Exception):
    pass


def require(condition, message):
    if not condition:
        raise Failure(message)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def q1(control):
    return 'Q1' + base64.b64encode(hashlib.sha1(control).digest()).decode()


def streams(raw):
    parts, offset = [], 0
    while offset < len(raw):
        stream = zlib.decompressobj(31)
        stream.decompress(raw[offset:])
        require(stream.eof, 'truncated gzip stream')
        used = len(raw) - offset - len(stream.unused_data)
        parts.append(raw[offset:offset + used])
        offset += used
    return parts


def members(stream):
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(stream))) as archive:
        return {entry.name: archive.extractfile(entry).read() for entry in archive.getmembers() if entry.isfile()}


def pkginfo(control):
    info = {}
    for line in members(control)['.PKGINFO'].decode().splitlines():
        if line and not line.startswith('#'):
            key, separator, value = line.partition(' = ')
            require(separator, 'malformed .PKGINFO')
            info.setdefault(key, []).append(value)
    return info


def signed_by(key_file, signature_stream, payload):
    entries = members(signature_stream)
    require(sorted(entries) == [SIGNATURE], 'unexpected signature entries')
    with tempfile.TemporaryDirectory() as temporary:
        Path(temporary, 'signature').write_bytes(entries[SIGNATURE])
        Path(temporary, 'payload').write_bytes(payload)
        result = subprocess.run(['openssl', 'dgst', '-sha256', '-verify', str(key_file), '-signature',
                                 f'{temporary}/signature', f'{temporary}/payload'], capture_output=True, text=True)
    return result.returncode == 0 and result.stdout.strip() == 'Verified OK'


def regular_files(root):
    """Every file under root, relative POSIX path -> Path; symbolic links are never followed."""
    found = {}
    for path in sorted(Path(root).rglob('*')):
        require(not path.is_symlink() and (path.is_file() or path.is_dir()), f'{path} is not a regular file')
        if path.is_file():
            found[path.relative_to(root).as_posix()] = path
    return found


def load(path, kind):
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    require(canonical(document) == raw, f'{Path(path).name} is not canonical')
    require(document.get('kind') == kind and document.get('schema_version') == 1, f'{Path(path).name} is not {kind} v1')
    return raw, document


def epoch(value):
    return int(datetime.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp())


def read_build(root, key_file, expected, targets, build_date, label):
    """Check one build's outputs against `expected` {arch: {file: (sha256, size)}} and its own key; return its streams."""
    require(sorted(path.name for path in Path(root).iterdir()) == targets, f'{label}: targets differ')
    result = {}
    for arch in targets:
        directory = Path(root, arch)
        found = regular_files(directory)
        require(sorted(found) == sorted(list(expected[arch]) + ['APKINDEX.tar.gz']), f'{label} {arch}: output set differs')
        index_raw = found['APKINDEX.tar.gz'].read_bytes()
        index_parts = streams(index_raw)
        require(len(index_parts) == 2 and signed_by(key_file, *index_parts), f'{label} {arch}: APKINDEX signature does not verify')
        entries = {}
        for block in members(index_parts[1])['APKINDEX'].decode().strip().split('\n\n'):
            entry = dict(line.split(':', 1) for line in block.splitlines())
            key = (entry.get('P'), entry.get('V'), entry.get('A'))
            require(key not in entries, f'{label} {arch}: duplicate APKINDEX entry')
            entries[key] = (entry.get('C'), entry.get('S'))
        described, result[arch] = {}, {}
        for name, (digest, size) in expected[arch].items():
            raw = found[name].read_bytes()
            require(sha256(raw) == digest and len(raw) == size, f'{label} {arch}/{name}: bytes differ from the receipt')
            parts = streams(raw)
            require(len(parts) == 3, f'{label} {arch}/{name}: not a signed APK')
            require(signed_by(key_file, parts[0], parts[1]), f'{label} {arch}/{name}: signature does not verify')
            info = pkginfo(parts[1])
            package = (info['pkgname'][0], info['pkgver'][0])
            sbom = json.loads(members(parts[2])[f'var/lib/db/sbom/{package[0]}-{package[1]}.spdx.json'])
            require(info['arch'] == [arch] and name == f'{package[0]}-{package[1]}.apk' and info['datahash'] == [sha256(parts[2])],
                    f'{label} {arch}/{name}: package metadata differs')
            require(info['builddate'] == [str(epoch(build_date))] and sbom['creationInfo']['created'] == build_date,
                    f'{label} {arch}/{name}: not built with the recorded BUILD_DATE')
            described[(*package, arch)] = (q1(parts[1]), str(len(raw)))
            result[arch][name] = dict(raw=raw, signature=parts[0], control=parts[1], data=parts[2])
        require(entries == described, f'{label} {arch}: APKINDEX does not describe exactly its own outputs')
        result[arch]['APKINDEX.tar.gz'] = index_raw
    return result


def verify(melange_repo, reference, reproducibility, repository, run_id, run_attempt, source_sha, ref):
    melange_repo, reference, reproducibility = Path(melange_repo), Path(reference), Path(reproducibility)
    binfmt_raw, binfmt = load(melange_repo / 'binfmt-evidence.json', 'binfmt-evidence')
    environment_raw, environment = load(melange_repo / 'melange-environment-evidence.json', 'melange-environment-evidence')
    capture_raw, capture = load(reference / 'melange-reproduction-reference.json', 'melange-reproduction-reference')
    evidence_raw, evidence = load(reproducibility / 'melange-reproducibility-evidence.json', 'melange-reproducibility-evidence')

    # A. origin and receipt bindings.
    origin = dict(repository=repository.lower(), ref=ref, source_sha=source_sha, run_id=run_id)
    producers = dict(binfmt=binfmt['producer'], environment=environment['producer'], reference=capture['producer'],
                     evidence_reference=evidence['reference']['producer'], evidence_rebuild=evidence['rebuild']['producer'])
    for name, producer in producers.items():
        require({key: producer.get(key) for key in origin} == origin and 0 < producer['run_attempt'] <= run_attempt,
                f'{name} receipt does not belong to this run')
    require({key: value for key, value in capture['producer'].items() if key not in ('job', 'runner')} == environment['producer'],
            'reference capture has another producer')
    require(capture['receipts'] == dict(binfmt_evidence=sha256(binfmt_raw), melange_environment_evidence=sha256(environment_raw)),
            'reference capture is bound to other receipts')
    require(evidence['receipts'] == dict(binfmt_evidence=sha256(binfmt_raw), melange_environment_evidence=sha256(environment_raw),
                                         environment_digest=environment['environment_digest'],
                                         reproduction_reference=sha256(capture_raw)), 'evidence is bound to other receipts')
    require(evidence['reference']['producer'] == capture['producer'], 'evidence names another reference build')
    rebuild_producer = evidence['rebuild']['producer']
    require(rebuild_producer['job'] != capture['producer']['job'] and rebuild_producer['runner'] != capture['producer']['runner']
            and (evidence['status'], evidence['independence']) == ('REPRODUCED', 'CROSS_JOB_SAME_RUN'),
            'evidence does not approve an independent rebuild')

    # B. digests recomputed from the receipts.
    facts, temporal = environment['environment'], capture['temporal']
    require(environment['environment_digest'] == 'sha256:' + sha256(canonical(facts)), 'environment digest mismatch')
    targets = sorted(evidence['scope']['targets'])
    require(targets == sorted(facts['targets']) == sorted(capture['dependencies']), 'targets differ between receipts')
    identity = lambda item: {key: value for key, value in item.items() if key not in ('resolution_index_sha256', 'checks')}
    for arch in targets:
        lock = sorted((item['name'], item['version']) for item in facts['targets'][arch]['packages'])
        require([(item['name'], item['version']) for item in capture['dependencies'][arch]] == lock,
                f'{arch}: recorded materials differ from the lock')
        require([identity(item) for item in evidence['rebuild']['dependencies'][arch]]
                == [identity(item) for item in capture['dependencies'][arch]], f'{arch}: rebuild materials differ')
    inputs = dict(tool=facts['tool'], host_platform=facts['host_platform'],
                  binfmt=dict(requested_ref=binfmt['requested_ref'], resolved_digest=binfmt['resolved_digest'],
                              qemu_version=binfmt['qemu_version']),
                  configuration=dict(path=facts['configuration']['path'], sha256=facts['configuration']['sha256']),
                  source_files=facts['source_files'], repositories=facts['repositories'], keyring=facts['keyring'],
                  build=dict(build_date=temporal['build_date'], parameter='--build-date',
                             melange_process_source_date_epoch=temporal['melange_process_source_date_epoch']),
                  targets={arch: dict(environment_arch=facts['targets'][arch]['environment_arch'],
                                      dependencies=[identity(item) for item in capture['dependencies'][arch]]) for arch in targets})
    require(evidence['reproduction_inputs'] == inputs, 'reproduction inputs do not follow from the receipts')
    digest = 'sha256:' + sha256(canonical(inputs))
    require(evidence['reproduction_input_digest'] == digest, 'reproduction input digest mismatch')

    # C. reference outputs = the HGC-02 outputs, under the HGC-02 key.
    reference_key = melange_repo / 'melange.rsa.pub'
    require(sha256(reference_key.read_bytes()) == environment['signing_key']['sha256']
            == evidence['signatures']['reference']['public_key_sha256'], 'reference public key differs')
    require(sorted(regular_files(melange_repo)) == sorted(
        ['binfmt-evidence.json', 'melange-environment-evidence.json', 'melange-version.txt', 'melange.rsa.pub']
        + [f"packages/{arch}/{name}" for arch in targets for name in
           [record['path'].rsplit('/', 1)[1] for record in environment['outputs'][arch]] + ['APKINDEX.tar.gz']]),
        'melange-repo holds other files')
    build_date = temporal['build_date']
    built = read_build(melange_repo / 'packages', reference_key,
                       {arch: {record['path'].rsplit('/', 1)[1]: (record['sha256'], record['size'])
                               for record in environment['outputs'][arch]} for arch in targets}, targets, build_date, 'reference')

    # D/E. rebuild outputs, public key and APKINDEX = the files the evidence describes, and nothing else.
    signatures = evidence['signatures']['rebuild']
    expected = {arch: {item['file']: (item['apk_sha256'], item['apk_size']) for item in evidence['rebuild']['outputs'][arch]}
                for arch in targets}
    require(sorted(regular_files(reproducibility)) == sorted(
        ['melange-reproducibility-evidence.json', 'rebuild/melange.rsa.pub']
        + [f'rebuild/packages/{arch}/{name}' for arch in targets for name in list(expected[arch]) + ['APKINDEX.tar.gz']]),
        'melange-reproducibility holds other files')
    rebuild_key = reproducibility / 'rebuild/melange.rsa.pub'
    require(sha256(rebuild_key.read_bytes()) == signatures['public_key_sha256'] != sha256(reference_key.read_bytes()),
            'rebuild public key differs from the evidence or reuses the reference key')
    rebuilt = read_build(reproducibility / 'rebuild/packages', rebuild_key, expected, targets, build_date, 'rebuild')
    for arch in targets:
        require(sha256(rebuilt[arch]['APKINDEX.tar.gz']) == signatures['apkindex'][arch]['sha256'],
                f'rebuild {arch}: APKINDEX differs from the evidence')

    # H/I. direct comparison of the original stream bytes (signature streams and signed APKs are not compared).
    report = {}
    for arch in targets:
        names = sorted(name for name in built[arch] if name != 'APKINDEX.tar.gz')
        require(names == sorted(name for name in rebuilt[arch] if name != 'APKINDEX.tar.gz'), f'{arch}: package sets differ')
        for name in names:
            before, after = built[arch][name], rebuilt[arch][name]
            require(before['control'] == after['control'], f'{arch}/{name}: control streams differ')
            require(before['data'] == after['data'], f'{arch}/{name}: data streams differ')
            report[f'{arch}/{name}'] = dict(control_stream_sha256=sha256(after['control']), data_stream_sha256=sha256(after['data']),
                                            reference_apk_sha256=sha256(before['raw']), rebuild_apk_sha256=sha256(after['raw']))
    return dict(result='READBACK_VERIFIED', reproduction_input_digest=digest, build_date=build_date, packages=report,
                reference_public_key_sha256=sha256(reference_key.read_bytes()),
                rebuild_public_key_sha256=sha256(rebuild_key.read_bytes()),
                evidence_sha256=sha256(evidence_raw), reference_capture_sha256=sha256(capture_raw))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    for name in ('melange-repo', 'reference', 'reproducibility', 'repository', 'source-sha', 'ref'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--run-id', type=int, required=True)
    parser.add_argument('--run-attempt', type=int, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify(args.melange_repo, args.reference, args.reproducibility, args.repository, args.run_id, args.run_attempt,
                        args.source_sha, args.ref)
    except (Failure, KeyError, ValueError, OSError) as error:
        print(f'READBACK_FAILED: {error}', file=sys.stderr)
        return 1
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
