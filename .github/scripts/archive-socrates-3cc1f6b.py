"""Fork-only archive of one pinned test candidate and its entire source tree."""
import base64
import hashlib
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
SOURCE = '3cc1f6b79b40a71c0cb9bbf604d949bace6fe0e9'
SOURCE_TREE = 'bc38bbf7b2fb5d6ce0331da4cc93c6b15767b04a'
RUN = 43028737299
ARTIFACT = 13860156415
ARTIFACT_SHA = '1d1730bb7572348b3674cd2d567199da46fecee404f2b57cf84e56f5bdaab311'
ARTIFACT_SIZE = 43462069
TOOLS = 13860135988
TOOLS_SHA = '8f2e99ca24f9a33ddd765f71a6492ff51ea4d56d66ecd4ed296e9f4d1a534ca9'
TOOLS_SIZE = 7044739
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
    matched, checked_lock = [], None
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
                assert hashlib.sha256(candidate).hexdigest() == expected, 'Tested production source differs: ' + name
                matched.append(name)
    assert len(matched) == 7 and checked_lock is not None
    return {'workflow_url': validation['workflow_url'], 'tested_commit': IO_TESTED,
        'public_evidence_source_commit': IO_SOURCE, 'original_artifact_sha256': IO_SHA,
        'production_files_matching_candidate': matched,
        'cargo_lock_comparison': checked_lock,
        'mtp_storage_access_capability': mtp['storage_info_access_capability'],
        'whole_ext4_image_sha256_before_and_after': mtp['whole_ext4_image_sha256_before_and_after'],
        'limits': validation['limits']}


def assets(release):
    return {a['name']: a for a in api(f'repos/{REPO}/releases/{release["id"]}/assets?per_page=100')}


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
    assert proof['build_tools_artifact']['id'] == TOOLS
    assert proof['build_tools_artifact']['digest'] == 'sha256:' + TOOLS_SHA
    assert proof['device_boot_tested'] is False and proof['data_decryption_configured'] is True
    assert proof['data_decryption_read_only'] is True
    result = {'release_url': release['html_url'], 'tag': release['tag_name'], 'requested_tag': TAG, 'draft': True,
        'source_commit': SOURCE, 'archive_run': os.environ['GITHUB_RUN_ID'],
        'all_release_assets_downloaded_and_hashed': True,
        'image': image, 'boot': boot, 'complete_source': source, 'isolated_io_mtp_evidence': io_proof,
        'device_hardware_acceptance': 'pending, not established by this archive',
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
    assert run['repository']['full_name'] == REPO and run['head_branch'] == 'device/socrates'
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
    assert config['decrypt']['read_only'] is True
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

    body = ('Build archive for REDMI K60 Pro (socrates), source `' + SOURCE + '`.\n\n'
        '**Draft test candidate: final image hardware acceptance is still being verified.** '
        'Successful compilation and archive checks do not establish successful device operation.\n\n'
        'This candidate configures read-only data decryption, isolated RAM calibration data, '
        'touch reload preparation, BootControl service bring-up, read-only MTP reporting and a haptic gain change. '
        'The haptic change and complete new image remain under device validation; this archive does not claim final hardware acceptance.\n\n'
        'Source CI: https://github.com/' + REPO + '/actions/runs/' + str(RUN) + '\n\n'
        'Official stock: ' + config['stock']['version'] + '; recovery SHA-256 `' + STOCK_SHA + '`.\n\n'
        'The original recovery ZIP, complete pinned source snapshot (every tracked Git blob checked), '
        'Git tree/commit evidence, build tools, source inputs, source build logs, and checksums are retained here. '
        'The ninth asset retains the original isolated IO/MTP proof ZIP; seven recorded production source files '
        'match this source snapshot exactly. The tested Cargo.lock differs solely by two isolated-test path dev-dependencies '
        'on skyward-backup and skyward-files; all other lock entries match. That protocol/filesystem test does not test '
        'USB transport or real-phone destructive operations. '
        'The recovery image is ramdisk-only (boot header v4, no kernel); it is not a standalone fastboot-boot image.\n')
    if release is None:
        release = api(f'repos/{REPO}/releases', {'tag_name': TAG, 'target_commitish': SOURCE,
            'name': TITLE, 'body': body, 'draft': True, 'prerelease': True})
    assert release['draft'] and release['target_commitish'] == SOURCE
    core = []
    core_paths = (package, source_archive, tree_path, source_inputs, logs, tools, io_package)
    for path in core_paths:
        asset = upload(release, path)
        expected = metadata(path)
        verify_asset(asset, expected, work / ('roundtrip-' + path.name))
        core.append({**expected, 'release_asset_id': asset['id']})
    proof = {'source_commit': SOURCE, 'source_run': {k: run[k] for k in ('id', 'head_sha', 'conclusion', 'html_url')},
        'source_artifact': artifact, 'image': image, 'stock': config['stock'],
        'source_files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        'source_ndk': 'r29', 'source_cargo_ndk': '4.1.2',
        'device_boot_tested': False, 'data_decryption_configured': True,
        'data_decryption_read_only': True, 'complete_source': source_check,
        'build_tools_artifact': tools_artifact, 'build_tools_files_sha256': tools_files,
        'isolated_io_mtp_evidence': io_check,
        'hardware_acceptance': 'Pending external real-device validation; never inferred from CI/archive.',
        'core_assets_verified_by_release_download': core,
        'archive_recipe_commit': os.environ['GITHUB_SHA'], 'archive_run_id': os.environ['GITHUB_RUN_ID']}
    proof_path = work / 'BUILD_PROOF.json'
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    upload(release, proof_path)
    sums = work / 'SHA256SUMS'
    sums.write_text(''.join(sha(path) + '  ' + path.name + '\n' for path in (*core_paths, proof_path)))
    upload(release, sums)
    complete(release, work)
    body += ('\nAll nine Release assets were downloaded again on the cloud runner and their sizes/SHA-256 checked. '
        'Image ZIP CRC, 100 MiB image SHA-256, and every file in the full source snapshot were also checked.\n\n'
        'Archive validation: https://github.com/' + REPO + '/actions/runs/' + os.environ['GITHUB_RUN_ID'] + '\n')
    # Never publish, including on replay.
    subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{REPO}/releases/{release["id"]}', '--input', '-'],
        input=json.dumps({'body': body, 'draft': True}).encode(), stdout=subprocess.DEVNULL, check=True)
