"""Fork-only archive of one pinned test candidate and its entire source tree."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import tomllib
import urllib.parse
import urllib.request
import zipfile

REPO = 'Dingtaiqi/skyward'
SOURCE = 'bb88bc785175590c07d57b89e1d98d573a70b8c0'
SOURCE_TREE = '3ec44eb4ecea74362ac7ec16bd99d058b1baab3f'
RUN = 43046320050
ARTIFACT = 13866988417
ARTIFACT_SHA = '6653c8a5fe0a5ca10a6ced2056a9ac87c28068aad79f82fc2718cd77fd6348d0'
ARTIFACT_SIZE = 43464663
TOOLS = 13866899285
TOOLS_SHA = 'c976c8eaaf862a4478a3d2ec69ad5e8ba0586d69e2ffc39fd293559f11c70c65'
TOOLS_SIZE = 7048363
IMAGE_SHA = 'bd1c80633e266071f90299f61d2361185c8337e016ce787a0576cd9f6e5e5e57'
EXTRAS = {
    'ANDROID_RW_PROBE': {
        'source': 'f34b381bc47c011821e01bf3615cb67cf28d89d0',
        'tree': '74ce66020eb355cf602c50e355998726ef3e8798',
        'run': 43048917992, 'workflow': '.github/workflows/socrates-rw-probe.yml',
        'artifact': 13869165590, 'artifact_name': 'socrates-rw-fixture-probe',
        'bytes': 309557, 'sha256': '7c19262ed255e2b1bb2200bbb5ba308b31e1c30e3350d142ad120d371f238d73',
        'binary_sha256': '82dc8ba14efaf8596e17f982a42467d6a0f36fe3c65f500e38e8b91c72b0b81d',
        'files': ['ci/socrates-rw-probe.rs', '.github/workflows/socrates-rw-probe.yml',
            'Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', '.github/actions/setup/action.yml'],
    },
    'RECOVERY_FIXTURE_APK': {
        'source': 'a726b36a11dd713fb0dcb3384e6c883bccd925e1',
        'tree': '2fb0e5faee38357128847e39cb75f12c1998980c',
        'run': 43049397576, 'workflow': '.github/workflows/recovery-fixture-apk.yml',
        'artifact': 13868637512, 'artifact_name': 'skyward-recovery-fixture-apk',
        'bytes': 5377, 'sha256': '4b8d7ef6c4e137c635c14985fd4ee882ecc1e8bd092ea5ae6705b75d75033ce5',
        'files': ['ci/recovery-fixture/AndroidManifest.xml', 'ci/recovery-fixture/build.py',
            '.github/workflows/recovery-fixture-apk.yml'],
    },
}
PRODUCTION_FILES = ['crates/skyward-backup/src/archive.rs', 'crates/skyward-backup/src/store.rs',
    'crates/skyward-files/src/lib.rs', 'crates/skyward-mtp/src/responder.rs',
    'crates/skyward-mtp/src/codes.rs', 'crates/skyward-block/src/erase.rs',
    'crates/skyward-install/src/image.rs']
IO_RUN = 43023589389
IO_SOURCE = '589f1eb0e734ad6ab2d4d12d0f712bb0b803d587'
IO_TESTED = 'ca2cf871a36ea6f4494ef56b7c93da4d62303168'
IO_ARTIFACT = 13856546476
IO_SHA = '14389404e0e0c5cb8c3651f7d9bb0bb7f999d78dcb173a272ae824e254022c53'
IO_SIZE = 2787
IO_PACKAGE = 'DISPOSABLE_IO_MTP_PROOF.zip'
IO_FILES = {
    'PRODUCTION_SOURCE_SHA256SUMS': '1320e7e7d3e456b302df427af084ba16c7964d2a',
    'SOURCE_COMMIT': '23fe8ed644dabeb27bceb74b01084aab58e475dc',
    'VALIDATION.json': 'a384523c9dac50d76e589ce9bb118ca517e1cf78',
    'disposable-io.log': '430972703c93652023182ceabcb2a2cf5e546bec',
    'outside-jail-refusal.log': '2265ee9da94c1c4b6033187d28e399d8a892780a',
}
PACKAGE = f'skyward-{SOURCE[:7]}-socrates-OS3.0.304.0.WMKCNXM.zip'
TAG = f'skyward-socrates-{SOURCE[:7]}-candidate-20261111'
TITLE = f'socrates {SOURCE[:7]} — test candidate and complete source archive'
STOCK_SHA = 'a1ed92db62fb0915b20309c5fe80b9181c6cf3ecef7d76e68503a2e1e9ff8c88'
NAMES = {PACKAGE, 'SOURCE_CODE.zip', 'SOURCE_TREE.json', 'SOURCE_INPUTS.zip',
    'SOURCE_BUILD_LOGS.zip', 'BUILD_TOOLS.zip', IO_PACKAGE, 'BUILD_PROOF.json', 'SHA256SUMS'}
NAMES |= {name + suffix for name in EXTRAS for suffix in ('.zip', '_SOURCE_INPUTS.zip', '_BUILD_LOGS.zip')}
assert os.environ['GITHUB_REPOSITORY'] == REPO


def api(path, data=None):
    command = ['gh', 'api', '--method', 'GET' if data is None else 'POST', path]
    if data is not None:
        command += ['--input', '-']
    return json.loads(subprocess.check_output(command, input=None if data is None else json.dumps(data).encode()))


def download(path, target, binary=False):
    command = ['gh', 'api', path]
    if binary:
        command += ['-H', 'Accept: application/octet-stream']
    with target.open('wb') as output:
        subprocess.run(command, stdout=output, check=True)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def metadata(path):
    return {'name': path.name, 'bytes': path.stat().st_size, 'sha256': sha(path)}


def verify_source(source, manifest):
    """Check every archived tracked file against the pinned Git tree."""
    assert manifest['source_commit'] == SOURCE
    tree = manifest['tree']
    assert tree['sha'] == SOURCE_TREE and tree['truncated'] is False
    assert all(e['type'] in ('tree', 'blob') for e in tree['tree']), 'A gitlink needs a separate source archive'
    files = {e['path']: e for e in tree['tree'] if e['type'] == 'blob'}
    with zipfile.ZipFile(source) as archive:
        assert archive.testzip() is None
        records = [e for e in archive.infolist() if not e.is_dir()]
        prefix = records[0].filename.split('/', 1)[0] + '/'
        assert prefix.startswith('Dingtaiqi-skyward-') and SOURCE[:7] in prefix
        seen, total = set(), 0
        for entry in records:
            assert entry.filename.startswith(prefix)
            name = entry.filename[len(prefix):]
            assert name in files and name not in seen, 'Unexpected or duplicate source path: ' + name
            expected = files[name]
            assert entry.file_size == expected['size']
            digest = hashlib.sha1(b'blob ' + str(entry.file_size).encode() + b'\0')
            with archive.open(entry) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(block)
            assert digest.hexdigest() == expected['sha'], 'Source blob differs: ' + name
            total += entry.file_size
            seen.add(name)
        assert seen == set(files), 'The archive is missing tracked source files'
    return {'source_commit': SOURCE, 'git_tree_sha': SOURCE_TREE,
        'tracked_files': len(files), 'uncompressed_bytes': total,
        'every_git_blob_verified': True, 'zip_crc_verified': True}


def image_details(package):
    with zipfile.ZipFile(package) as archive:
        assert archive.namelist() == [PACKAGE[:-4] + '.img']
        digest = hashlib.sha256()
        count, header, tail = 0, b'', b''
        with archive.open(archive.namelist()[0]) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                if not header:
                    header = block[:4096]
                tail = block[-64:]
                count += len(block)
                digest.update(block)
        assert count == 104857600 and header[:8] == b'ANDROID!' and tail[:4] == b'AVBf'
        version = struct.unpack_from('<I', header, 40)[0]
        kernel = struct.unpack_from('<I', header, 8)[0]
        ramdisk = struct.unpack_from('<I', header, 12)[0]
        assert version == 4 and kernel == 0 and 0 < ramdisk < count
        image = {'name': archive.namelist()[0], 'bytes': count, 'sha256': digest.hexdigest(),
            'boot_header_version': version, 'avb_footer': True, 'zip_crc_verified': True}
        assert image['sha256'] == IMAGE_SHA
        boot = {'kernel_bytes': kernel, 'ramdisk_bytes': ramdisk,
            'cmdline': header[44:1580].split(b'\0', 1)[0].decode()}
    return image, boot


def verify_disposable(proof_zip, source_inputs, source_archive):
    """Bind the sanitized isolated-test evidence to the archived source."""
    assert sha(proof_zip) == IO_SHA and proof_zip.stat().st_size == IO_SIZE
    with zipfile.ZipFile(source_inputs) as inputs:
        public = {name: inputs.read('disposable-io-proof/' + name) for name in IO_FILES}
        tested_lock = inputs.read('disposable-io-proof/TESTED_Cargo.lock')
    for name, data in public.items():
        blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        assert blob == IO_FILES[name], 'Public evidence blob differs: ' + name
    validation = json.loads(public['VALIDATION.json'])
    assert validation['tested_commit'] == IO_TESTED and public['SOURCE_COMMIT'].decode().strip() == IO_TESTED
    assert validation['workflow_url'] == f'https://github.com/{REPO}/actions/runs/{IO_RUN}'
    assert validation['conclusion'] == 'success' and validation['outside_jail_refusal_passed'] is True
    assert validation['artifact_id'] == IO_ARTIFACT and validation['artifact_zip_sha256'] == IO_SHA
    mtp = validation['mtp']
    assert mtp['statvfs_readonly_and_kernel_EROFS_verified'] is True
    assert mtp['storage_info_access_capability'] == 1
    assert mtp['fixture_content_and_empty_destination_preserved'] is True
    assert mtp['whole_ext4_image_bytes'] == 67108864
    assert mtp['whole_ext4_image_sha256_before_and_after'] == '72c810678115c08730cf52110301511a596dd8d5bb89b8ddf5b871d62ab96463'
    assert validation['explicit_isolated_tests_passed'] == {'destructive_io': 1, 'real_readonly_mtp': 1}
    with zipfile.ZipFile(proof_zip) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == set(IO_FILES) - {'VALIDATION.json'}
        for name in archive.namelist():
            assert archive.read(name) == public[name], 'CI proof differs from public evidence: ' + name
    matched, changed, checked_lock = [], [], None
    with zipfile.ZipFile(source_archive) as archive:
        prefix = archive.namelist()[0].split('/', 1)[0] + '/'
        for line in public['PRODUCTION_SOURCE_SHA256SUMS'].decode().splitlines():
            expected, name = line.split('  ', 1)
            assert len(expected) == 64 and name not in matched
            candidate = archive.read(prefix + name)
            if name == 'Cargo.lock':
                assert hashlib.sha256(tested_lock).hexdigest() == expected
                tested, production = tomllib.loads(tested_lock.decode()), tomllib.loads(candidate.decode())
                install = [p for p in tested['package'] if p['name'] == 'skyward-install']
                assert len(install) == 1
                dependencies = install[0]['dependencies']
                extras = ['skyward-backup', 'skyward-files']
                assert all(dependencies.count(name) == 1 for name in extras)
                install[0]['dependencies'] = [name for name in dependencies if name not in extras]
                assert tested == production, 'Lock changes beyond the two isolated-test dev dependencies'
                checked_lock = {'exact_byte_match': False, 'tested_sha256': expected,
                    'candidate_sha256': hashlib.sha256(candidate).hexdigest(),
                    'isolated_test_only_path_dependencies': extras,
                    'all_other_lock_entries_identical': True}
            else:
                if hashlib.sha256(candidate).hexdigest() == expected:
                    matched.append(name)
                else:
                    changed.append(name)
    assert len(matched) + len(changed) == 7 and checked_lock is not None
    return {'workflow_url': validation['workflow_url'], 'tested_commit': IO_TESTED,
        'public_evidence_source_commit': IO_SOURCE, 'original_artifact_sha256': IO_SHA,
        'production_files_matching_candidate': matched,
        'production_files_changed_since_historical_test': changed,
        'historical_evidence_not_validation_of_changed_candidate_files': True,
        'cargo_lock_comparison': checked_lock,
        'mtp_storage_access_capability': mtp['storage_info_access_capability'],
        'whole_ext4_image_sha256_before_and_after': mtp['whole_ext4_image_sha256_before_and_after'],
        'limits': validation['limits']}


def assets(release):
    return {a['name']: a for a in api(f'repos/{REPO}/releases/{release["id"]}/assets?per_page=100')}


def zip_write(target, files):
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)


def extra_inputs(work, name, spec):
    run = api(f'repos/{REPO}/actions/runs/{spec["run"]}')
    artifact = api(f'repos/{REPO}/actions/artifacts/{spec["artifact"]}')
    assert run['id'] == spec['run'] and run['head_sha'] == spec['source']
    assert run['status'] == 'completed' and run['conclusion'] == 'success'
    assert run['repository']['full_name'] == REPO and run['path'] == spec['workflow']
    assert artifact['id'] == spec['artifact'] and artifact['name'] == spec['artifact_name']
    assert not artifact['expired'] and artifact['digest'] == 'sha256:' + spec['sha256']
    assert artifact['size_in_bytes'] == spec['bytes']
    assert artifact['workflow_run']['id'] == spec['run'] and artifact['workflow_run']['head_sha'] == spec['source']
    commit = api(f'repos/{REPO}/git/commits/{spec["source"]}')
    assert commit['sha'] == spec['source'] and commit['tree']['sha'] == spec['tree']
    tree = api(f'repos/{REPO}/git/trees/{spec["tree"]}?recursive=1')
    assert tree['sha'] == spec['tree'] and tree['truncated'] is False
    contents = {'source-commit.json': json.dumps(commit, indent=2).encode(),
        'source-tree.json': json.dumps(tree, indent=2).encode(),
        'source-run.json': json.dumps(run, indent=2).encode(),
        'source-artifact.json': json.dumps(artifact, indent=2).encode()}
    for path in spec['files']:
        entry = api(f'repos/{REPO}/contents/{path}?ref={spec["source"]}')
        contents[path] = base64.b64decode(entry['content'])
    zip_write(work / (name + '_SOURCE_INPUTS.zip'), contents)
    download(f'repos/{REPO}/actions/artifacts/{spec["artifact"]}/zip', work / (name + '.zip'))
    download(f'repos/{REPO}/actions/runs/{spec["run"]}/logs', work / (name + '_BUILD_LOGS.zip'))


def verify_extras(work, runtime_manifest, prefix=''):
    """Only saved assets are used, so replay does not depend on expiring artifacts."""
    result = {}
    runtime_files = {e['path']: e for e in runtime_manifest['tree']['tree'] if e['type'] == 'blob'}
    for name, spec in EXTRAS.items():
        artifact_path = work / (prefix + name + '.zip')
        assert artifact_path.stat().st_size == spec['bytes'] and sha(artifact_path) == spec['sha256']
        with zipfile.ZipFile(work / (prefix + name + '_SOURCE_INPUTS.zip')) as inputs:
            assert inputs.testzip() is None
            assert set(inputs.namelist()) == set(spec['files']) | {'source-commit.json', 'source-tree.json', 'source-run.json', 'source-artifact.json'}
            commit = json.loads(inputs.read('source-commit.json'))
            tree = json.loads(inputs.read('source-tree.json'))
            run = json.loads(inputs.read('source-run.json'))
            artifact = json.loads(inputs.read('source-artifact.json'))
            assert commit['sha'] == spec['source'] and commit['tree']['sha'] == spec['tree']
            assert tree['sha'] == spec['tree'] and tree['truncated'] is False
            assert run['id'] == spec['run'] and run['head_sha'] == spec['source'] and run['conclusion'] == 'success'
            assert run['repository']['full_name'] == REPO and run['path'] == spec['workflow']
            assert artifact['id'] == spec['artifact'] and artifact['name'] == spec['artifact_name']
            assert artifact['size_in_bytes'] == spec['bytes'] and artifact['digest'] == 'sha256:' + spec['sha256']
            assert artifact['workflow_run']['id'] == spec['run'] and artifact['workflow_run']['head_sha'] == spec['source']
            entries = {e['path']: e for e in tree['tree'] if e['type'] == 'blob'}
            source_hashes = {}
            for path in spec['files']:
                data = inputs.read(path)
                assert entries[path]['sha'] == hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
                source_hashes[path] = hashlib.sha256(data).hexdigest()
            evidence = {'source_commit': spec['source'], 'git_tree_sha': spec['tree'],
                'workflow_url': f'https://github.com/{REPO}/actions/runs/{spec["run"]}',
                'artifact_id': spec['artifact'], 'artifact_zip_sha256': spec['sha256'],
                'source_files_sha256': source_hashes, 'saved_source_git_blobs_verified': True}
            with zipfile.ZipFile(artifact_path) as archive:
                assert archive.testzip() is None
                if name == 'ANDROID_RW_PROBE':
                    assert set(archive.namelist()) == {'socrates_rw_probe', 'SOURCE_COMMIT', 'SHA256SUMS'}
                    assert archive.read('SOURCE_COMMIT').decode().strip() == spec['source']
                    assert hashlib.sha256(archive.read('socrates_rw_probe')).hexdigest() == spec['binary_sha256']
                    assert archive.read('SHA256SUMS').decode().strip() == spec['binary_sha256'] + '  probe-out/socrates_rw_probe'
                    changed = sorted(p for p in set(entries) | set(runtime_files)
                        if entries.get(p, {}).get('sha') != runtime_files.get(p, {}).get('sha'))
                    assert changed == ['ci/socrates-rw-probe.rs'], 'Probe source differs beyond its fork-only helper'
                    same = {p: runtime_files[p]['sha'] for p in PRODUCTION_FILES}
                    assert all(entries[p]['sha'] == blob for p, blob in same.items())
                    evidence.update({'binary_sha256': spec['binary_sha256'],
                        'runtime_compiled_from_different_commit': True,
                        'only_source_path_changed_since_runtime': changed,
                        'production_git_blobs_matching_runtime': same,
                        'reconstruction': 'Use SOURCE_CODE.zip at bb88bc7 and replace only ci/socrates-rw-probe.rs with this exact f34b381 input; runtime image remains compiled from bb88bc7.'})
                else:
                    assert set(archive.namelist()) == {'skyward-recovery-fixture.apk', 'PROOF.json', 'SHA256SUMS',
                        'CERTIFICATE_SHA256', 'compiled-manifest.txt', 'badging.txt', 'signature-verification.txt', 'AndroidManifest.xml'}
                    proof = json.loads(archive.read('PROOF.json'))
                    apk = archive.read('skyward-recovery-fixture.apk')
                    assert proof['source_commit'] == spec['source'] and proof['run_url'] == evidence['workflow_url']
                    assert proof['package'] == 'org.skyward.recovery.fixture' and proof['has_code'] is False
                    assert proof['permissions'] == [] and proof['components'] == [] and proof['private_key_uploaded'] is False
                    assert hashlib.sha256(apk).hexdigest() == proof['apk_sha256'] and len(apk) == proof['apk_bytes']
                    assert archive.read('AndroidManifest.xml') == inputs.read('ci/recovery-fixture/AndroidManifest.xml')
                    with zipfile.ZipFile(io.BytesIO(apk)) as nested:
                        assert nested.testzip() is None
                        assert set(nested.namelist()) <= {'AndroidManifest.xml', 'resources.arsc'}
                    evidence.update(apk_sha256=proof['apk_sha256'], apk_bytes=proof['apk_bytes'],
                        certificate_sha256=proof['certificate_sha256'], no_code_permissions_or_components=True,
                        signing_private_key_not_archived=True,
                        reconstruction='Two exact source files and workflow are retained; build uses temporary random signing key, so recompilation is not byte-for-byte identical.')
        with zipfile.ZipFile(work / (prefix + name + '_BUILD_LOGS.zip')) as logs:
            assert logs.testzip() is None
        result[name] = evidence
    return result


def verify_asset(asset, expected, target):
    assert asset['size'] == expected['bytes'] and asset['state'] == 'uploaded'
    assert asset['digest'] == 'sha256:' + expected['sha256']
    download(f'repos/{REPO}/releases/assets/{asset["id"]}', target, binary=True)
    assert target.stat().st_size == expected['bytes'] and sha(target) == expected['sha256'], 'Published bytes differ: ' + expected['name']
    if target.suffix == '.zip':
        with zipfile.ZipFile(target) as archive:
            assert archive.testzip() is None


def upload(release, path):
    expected = metadata(path)
    existing = assets(release).get(path.name)
    if existing is not None:
        assert existing['size'] == expected['bytes'] and existing['digest'] == 'sha256:' + expected['sha256'], 'Refusing to replace an existing asset'
        return existing
    url = release['upload_url'].split('{', 1)[0]
    assert urllib.parse.urlsplit(url).scheme == 'https' and urllib.parse.urlsplit(url).netloc == 'uploads.github.com'
    request = urllib.request.Request(url + '?' + urllib.parse.urlencode({'name': path.name}),
        data=path.read_bytes(), method='POST', headers={
            'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
            'Content-Type': 'application/octet-stream',
            'Accept': 'application/vnd.github+json'})
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.load(response)


def complete(release, work):
    """Saved results can be checked again even after the source artifact expires."""
    saved = assets(release)
    assert set(saved) == NAMES, 'Archive assets are incomplete or unexpected'
    sums = work / 'SHA256SUMS'
    download(f'repos/{REPO}/releases/assets/{saved[sums.name]["id"]}', sums, binary=True)
    expected = {}
    for line in sums.read_text().splitlines():
        digest, name = line.split('  ', 1)
        assert len(digest) == 64 and name in NAMES and name != sums.name
        assert name not in expected
        expected[name] = {'name': name, 'bytes': saved[name]['size'], 'sha256': digest}
    assert set(expected) == NAMES - {sums.name}
    assert expected[PACKAGE]['sha256'] == ARTIFACT_SHA and expected[PACKAGE]['bytes'] == ARTIFACT_SIZE
    assert expected['BUILD_TOOLS.zip']['sha256'] == TOOLS_SHA and expected['BUILD_TOOLS.zip']['bytes'] == TOOLS_SIZE
    assert expected[IO_PACKAGE]['sha256'] == IO_SHA and expected[IO_PACKAGE]['bytes'] == IO_SIZE
    for name, spec in EXTRAS.items():
        assert expected[name + '.zip']['sha256'] == spec['sha256']
        assert expected[name + '.zip']['bytes'] == spec['bytes']
    expected[sums.name] = metadata(sums)
    for name, item in expected.items():
        verify_asset(saved[name], item, work / ('checked-' + name))
    proof = json.loads((work / 'checked-BUILD_PROOF.json').read_text())
    assert proof['source_commit'] == SOURCE and proof['source_run']['id'] == RUN
    assert proof['source_artifact']['id'] == ARTIFACT and proof['source_artifact']['digest'] == 'sha256:' + ARTIFACT_SHA
    assert proof['source_run']['conclusion'] == 'success'
    image, boot = image_details(work / ('checked-' + PACKAGE))
    assert image == proof['image']
    manifest = json.loads((work / 'checked-SOURCE_TREE.json').read_text())
    source = verify_source(work / 'checked-SOURCE_CODE.zip', manifest)
    assert source == proof['complete_source']
    io_proof = verify_disposable(work / ('checked-' + IO_PACKAGE), work / 'checked-SOURCE_INPUTS.zip', work / 'checked-SOURCE_CODE.zip')
    assert io_proof == proof['isolated_io_mtp_evidence']
    extra_proof = verify_extras(work, manifest, prefix='checked-')
    assert extra_proof == proof['android_fixture_builds']
    assert proof['build_tools_artifact']['id'] == TOOLS
    assert proof['build_tools_artifact']['digest'] == 'sha256:' + TOOLS_SHA
    assert proof['device_boot_tested_by_archive_workflow'] is False and proof['data_decryption_configured'] is True
    assert proof['data_decryption_read_only'] is False
    result = {'release_url': release['html_url'], 'tag': release['tag_name'], 'requested_tag': TAG, 'draft': True,
        'source_commit': SOURCE, 'archive_run': os.environ['GITHUB_RUN_ID'],
        'all_release_assets_downloaded_and_hashed': True,
        'image': image, 'boot': boot, 'complete_source': source, 'isolated_io_mtp_evidence': io_proof,
        'android_fixture_builds': extra_proof,
        'device_hardware_acceptance': 'partial device testing reported; final public validation pending, never inferred from archive checks',
        'assets': list(expected.values())}
    print('ARCHIVE_RESULT_JSON=' + json.dumps(result), flush=True)
    Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(
        'Draft archive verified: ' + release['html_url'] + '\n\n```json\n' + json.dumps(result, indent=2) + '\n```\n')
    return result


with tempfile.TemporaryDirectory(prefix='socrates-archive-') as directory:
    work = Path(directory)
    releases = api(f'repos/{REPO}/releases?per_page=100')
    # GitHub may retain an unpublished draft under an automatic untagged-* name.
    matches = [r for r in releases if r['tag_name'] == TAG or
        (r['draft'] and r['target_commitish'] == SOURCE and r['name'] == TITLE)]
    assert len(matches) <= 1, 'Ambiguous archive release'
    release = matches[0] if matches else None
    if release is not None:
        assert release['draft'] and release['target_commitish'] == SOURCE
        if set(assets(release)) == NAMES:
            complete(release, work)
            raise SystemExit(0)

    run = api(f'repos/{REPO}/actions/runs/{RUN}')
    assert run['id'] == RUN and run['head_sha'] == SOURCE
    assert run['status'] == 'completed' and run['conclusion'] == 'success'
    assert run['repository']['full_name'] == REPO and run['head_branch'] == 'device/socrates-rw'
    assert run['path'] == '.github/workflows/build.yml'
    jobs = api(f'repos/{REPO}/actions/runs/{RUN}/jobs?per_page=100')
    successful = {j['name'] for j in jobs['jobs'] if j['conclusion'] == 'success'}
    assert successful == {'Lint and test', 'List the devices', 'Build the recovery service and the packer', 'Recovery for socrates'}
    artifact = api(f'repos/{REPO}/actions/artifacts/{ARTIFACT}')
    assert artifact['id'] == ARTIFACT and artifact['name'] == PACKAGE and not artifact['expired']
    assert artifact['size_in_bytes'] == ARTIFACT_SIZE and artifact['digest'] == 'sha256:' + ARTIFACT_SHA
    assert artifact['workflow_run']['id'] == RUN and artifact['workflow_run']['head_sha'] == SOURCE
    package = work / PACKAGE
    download(f'repos/{REPO}/actions/artifacts/{ARTIFACT}/zip', package)
    assert sha(package) == ARTIFACT_SHA and package.stat().st_size == artifact['size_in_bytes']

    # The artifact retains the original ZIP. Streaming its sole image
    # checks the ZIP CRC and the image SHA, not just the compressed bytes.
    image, _ = image_details(package)
    print('IMAGE_VERIFIED=' + json.dumps(image), flush=True)

    tools_artifact = api(f'repos/{REPO}/actions/artifacts/{TOOLS}')
    assert tools_artifact['id'] == TOOLS and tools_artifact['name'] == 'tools' and not tools_artifact['expired']
    assert tools_artifact['size_in_bytes'] == TOOLS_SIZE and tools_artifact['digest'] == 'sha256:' + TOOLS_SHA
    assert tools_artifact['workflow_run']['id'] == RUN and tools_artifact['workflow_run']['head_sha'] == SOURCE
    tools = work / 'BUILD_TOOLS.zip'
    download(f'repos/{REPO}/actions/artifacts/{TOOLS}/zip', tools)
    assert sha(tools) == TOOLS_SHA and tools.stat().st_size == TOOLS_SIZE
    with zipfile.ZipFile(tools) as archive:
        assert archive.testzip() is None
        assert set(archive.namelist()) == {'skyward', 'skyward-pack', 'system-init'}
        tools_files = {name: hashlib.sha256(archive.read(name)).hexdigest() for name in archive.namelist()}

    commit = api(f'repos/{REPO}/git/commits/{SOURCE}')
    assert commit['sha'] == SOURCE and commit['tree']['sha'] == SOURCE_TREE
    tree = api(f'repos/{REPO}/git/trees/{SOURCE_TREE}?recursive=1')
    source_manifest = {'repository': REPO, 'source_commit': SOURCE, 'commit': commit, 'tree': tree}
    tree_path = work / 'SOURCE_TREE.json'
    tree_path.write_text(json.dumps(source_manifest, indent=2) + '\n')
    source_archive = work / 'SOURCE_CODE.zip'
    download(f'repos/{REPO}/zipball/{SOURCE}', source_archive)
    source_check = verify_source(source_archive, source_manifest)
    print('FULL_SOURCE_VERIFIED=' + json.dumps(source_check), flush=True)

    io_artifact = api(f'repos/{REPO}/actions/artifacts/{IO_ARTIFACT}')
    assert io_artifact['id'] == IO_ARTIFACT and not io_artifact['expired']
    assert io_artifact['workflow_run']['id'] == IO_RUN and io_artifact['workflow_run']['head_sha'] == IO_TESTED
    assert io_artifact['size_in_bytes'] == IO_SIZE and io_artifact['digest'] == 'sha256:' + IO_SHA
    io_package = work / IO_PACKAGE
    download(f'repos/{REPO}/actions/artifacts/{IO_ARTIFACT}/zip', io_package)
    assert sha(io_package) == IO_SHA and io_package.stat().st_size == IO_SIZE
    io_public = {}
    for name, blob in IO_FILES.items():
        item = api(f'repos/{REPO}/contents/ci/proofs/disposable-io-{IO_RUN}/{name}?ref={IO_SOURCE}')
        data = base64.b64decode(item['content'])
        assert item['sha'] == blob and hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == blob
        io_public['disposable-io-proof/' + name] = data
    item = api(f'repos/{REPO}/contents/Cargo.lock?ref={IO_TESTED}')
    io_public['disposable-io-proof/TESTED_Cargo.lock'] = base64.b64decode(item['content'])

    files = ['Cargo.toml', 'Cargo.lock', 'rust-toolchain.toml', 'devices/socrates.toml',
        '.github/workflows/build.yml', '.github/actions/setup/action.yml']
    contents = {}
    for name in files:
        item = api(f'repos/{REPO}/contents/{name}?ref={SOURCE}')
        data = base64.b64decode(item['content'])
        assert hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == item['sha']
        contents[name] = data
    config = tomllib.loads(contents['devices/socrates.toml'].decode())
    assert config['device']['codename'] == 'socrates' and config['stock']['recovery'] == STOCK_SHA
    assert config['decrypt']['read_only'] is False
    assert config['decrypt']['auto_repair'] is False and config['decrypt']['key_upgrades'] is False
    cnss = [s for s in config['service'] if s['path'] == '/vendor/bin/cnss-daemon']
    assert len(cnss) == 1 and cnss[0]['memory_data'] is True
    source_inputs = work / 'SOURCE_INPUTS.zip'
    with zipfile.ZipFile(source_inputs, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**contents, **io_public, 'source-run.json': json.dumps(run, indent=2).encode(),
                'source-jobs.json': json.dumps(jobs, indent=2).encode()}.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    io_check = verify_disposable(io_package, source_inputs, source_archive)
    print('ISOLATED_IO_MTP_EVIDENCE_VERIFIED=' + json.dumps(io_check), flush=True)
    logs = work / 'SOURCE_BUILD_LOGS.zip'
    download(f'repos/{REPO}/actions/runs/{RUN}/logs', logs)
    with zipfile.ZipFile(logs) as archive:
        assert archive.testzip() is None

    for name, spec in EXTRAS.items():
        extra_inputs(work, name, spec)
    extra_check = verify_extras(work, source_manifest)
    print('ANDROID_FIXTURE_BUILDS_VERIFIED=' + json.dumps(extra_check), flush=True)

    body = ('Build archive for REDMI K60 Pro (socrates), source `' + SOURCE + '`.\n\n'
        '**Draft RW test candidate: scoped device tests are in progress; final public acceptance record is pending.** '
        'Successful compilation and archive checks do not establish successful device operation.\n\n'
        'This candidate configures writable decrypted data with automatic repair and KeyMint blob upgrades disabled, '
        'isolated RAM calibration data, touch reload preparation, BootControl, filesystem-aware MTP storage reporting, '
        'haptics and archive/MTP path containment fixes. Only dedicated disposable fixture operations are in scope. '
        'This archive does not claim full personal-data restore, cross-format/fscrypt key-root reconstruction, '
        'OTA/VAB/AVB installation, userdata erase or slot switching.\n\n'
        'Source CI: https://github.com/' + REPO + '/actions/runs/' + str(RUN) + '\n\n'
        'Official stock: ' + config['stock']['version'] + '; recovery SHA-256 `' + STOCK_SHA + '`.\n\n'
        'The original recovery ZIP, complete pinned source snapshot (every tracked Git blob checked), '
        'Git tree/commit evidence, build tools, source inputs, source build logs, and checksums are retained here. '
        'The historical isolated IO/MTP proof ZIP is retained with its exact tested commit; BUILD_PROOF records '
        'which recorded production files changed since that older test, so it is not presented as validation of changed code. '
        'Six additional assets retain the original Android probe and inert fixture APK ZIPs, their exact source inputs '
        'and build logs. The probe was compiled from f34b381, not the runtime image commit bb88bc7: the only changed source '
        'path is the fork-only probe helper, and seven production Git blobs are independently compared. '
        'The inert no-code/no-permission APK was compiled from a726b36; its ephemeral signing private key is not archived. '
        'The recovery image is ramdisk-only (boot header v4, no kernel); it is not a standalone fastboot-boot image.\n')
    if release is None:
        release = api(f'repos/{REPO}/releases', {'tag_name': TAG, 'target_commitish': SOURCE,
            'name': TITLE, 'body': body, 'draft': True, 'prerelease': True})
    assert release['draft'] and release['target_commitish'] == SOURCE
    core = []
    extra_paths = tuple(work / (name + suffix) for name in EXTRAS
        for suffix in ('.zip', '_SOURCE_INPUTS.zip', '_BUILD_LOGS.zip'))
    core_paths = (package, source_archive, tree_path, source_inputs, logs, tools, io_package, *extra_paths)
    for path in core_paths:
        asset = upload(release, path)
        expected = metadata(path)
        verify_asset(asset, expected, work / ('roundtrip-' + path.name))
        core.append({**expected, 'release_asset_id': asset['id']})
    proof = {'source_commit': SOURCE, 'source_run': {k: run[k] for k in ('id', 'head_sha', 'conclusion', 'html_url')},
        'source_artifact': artifact, 'image': image, 'stock': config['stock'],
        'source_files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        'source_ndk': 'r29', 'source_cargo_ndk': '4.1.2',
        'device_boot_tested_by_archive_workflow': False, 'data_decryption_configured': True,
        'data_decryption_read_only': False, 'complete_source': source_check,
        'build_tools_artifact': tools_artifact, 'build_tools_files_sha256': tools_files,
        'isolated_io_mtp_evidence': io_check,
        'android_fixture_builds': extra_check,
        'hardware_acceptance': 'Partial device testing reported; final public validation pending. Never inferred from CI/archive.',
        'core_assets_verified_by_release_download': core,
        'archive_recipe_commit': os.environ['GITHUB_SHA'], 'archive_run_id': os.environ['GITHUB_RUN_ID']}
    proof_path = work / 'BUILD_PROOF.json'
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    upload(release, proof_path)
    sums = work / 'SHA256SUMS'
    sums.write_text(''.join(sha(path) + '  ' + path.name + '\n' for path in (*core_paths, proof_path)))
    upload(release, sums)
    complete(release, work)
    body += ('\nAll fifteen Release assets were downloaded again on the cloud runner and their sizes/SHA-256 checked. '
        'Image ZIP CRC, 100 MiB image SHA-256, and every file in the full source snapshot were also checked.\n\n'
        'Archive validation: https://github.com/' + REPO + '/actions/runs/' + os.environ['GITHUB_RUN_ID'] + '\n')
    # Never publish, including on replay.
    subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{REPO}/releases/{release["id"]}', '--input', '-'],
        input=json.dumps({'body': body, 'draft': True}).encode(), stdout=subprocess.DEVNULL, check=True)
