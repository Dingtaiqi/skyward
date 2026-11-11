"""Fork-only cloud archive of one pinned CI build; never publishes a release."""
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
SOURCE = 'f64d5c7f00907a944ec3069d76b6d473f9ee6b47'
RUN = 42985917414
ARTIFACT = 13839408282
ARTIFACT_SHA = 'b32c871699834bb0eeedc853b2942f14f120202f29e4c409abae428289360bbe'
PACKAGE = 'skyward-f64d5c7-socrates-OS3.0.304.0.WMKCNXM.zip'
TAG = 'skyward-socrates-f64d5c7-20261111'
TITLE = 'socrates f64d5c7 — build archive, not boot-tested'
STOCK_SHA = 'a1ed92db62fb0915b20309c5fe80b9181c6cf3ecef7d76e68503a2e1e9ff8c88'
NAMES = {PACKAGE, 'SOURCE_INPUTS.zip', 'SOURCE_BUILD_LOGS.zip', 'BUILD_PROOF.json', 'SHA256SUMS'}
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
    assert expected[PACKAGE]['sha256'] == ARTIFACT_SHA and expected[PACKAGE]['bytes'] == 43453910
    expected[sums.name] = metadata(sums)
    for name, item in expected.items():
        verify_asset(saved[name], item, work / ('checked-' + name))
    proof = json.loads((work / 'checked-BUILD_PROOF.json').read_text())
    assert proof['source_commit'] == SOURCE and proof['source_run']['id'] == RUN
    assert proof['source_artifact']['id'] == ARTIFACT and proof['source_artifact']['digest'] == 'sha256:' + ARTIFACT_SHA
    assert proof['source_run']['conclusion'] == 'success'
    with zipfile.ZipFile(work / ('checked-' + PACKAGE)) as archive:
        with archive.open(PACKAGE[:-4] + '.img') as stream:
            header = stream.read(4096)
    boot = {'kernel_bytes': struct.unpack_from('<I', header, 8)[0],
        'ramdisk_bytes': struct.unpack_from('<I', header, 12)[0],
        'cmdline': header[44:1580].split(b'\0', 1)[0].decode()}
    result = {'release_url': release['html_url'], 'tag': release['tag_name'], 'requested_tag': TAG, 'draft': True,
        'source_commit': SOURCE, 'archive_run': os.environ['GITHUB_RUN_ID'],
        'all_release_assets_downloaded_and_hashed': True,
        'image': proof['image'], 'boot': boot, 'assets': list(expected.values())}
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
    assert artifact['size_in_bytes'] == 43453910 and artifact['digest'] == 'sha256:' + ARTIFACT_SHA
    assert artifact['workflow_run']['id'] == RUN and artifact['workflow_run']['head_sha'] == SOURCE
    package = work / PACKAGE
    download(f'repos/{REPO}/actions/artifacts/{ARTIFACT}/zip', package)
    assert sha(package) == ARTIFACT_SHA and package.stat().st_size == artifact['size_in_bytes']

    # upload-artifact archive:false retained the original recovery ZIP, with
    # one image inside it. Read it fully to check both CRC and the image hash.
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
        image = {'name': archive.namelist()[0], 'bytes': count, 'sha256': digest.hexdigest(),
            'boot_header_version': struct.unpack_from('<I', header, 40)[0], 'avb_footer': True, 'zip_crc_verified': True}
        assert image['boot_header_version'] in (3, 4)
    print('IMAGE_VERIFIED=' + json.dumps(image), flush=True)

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
    source_inputs = work / 'SOURCE_INPUTS.zip'
    with zipfile.ZipFile(source_inputs, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in {**contents, 'source-run.json': json.dumps(run, indent=2).encode(),
                'source-jobs.json': json.dumps(jobs, indent=2).encode()}.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 11, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
    logs = work / 'SOURCE_BUILD_LOGS.zip'
    download(f'repos/{REPO}/actions/runs/{RUN}/logs', logs)
    with zipfile.ZipFile(logs) as archive:
        assert archive.testzip() is None

    body = ('Build archive for REDMI K60 Pro (socrates), source `' + SOURCE + '`.\n\n'
        '**Draft: this is not a device boot acceptance. Data decryption is not configured.** '
        'Do not treat compilation or archive checks as a successful recovery boot.\n\n'
        'Source CI: https://github.com/' + REPO + '/actions/runs/' + str(RUN) + '\n\n'
        'Official stock: ' + config['stock']['version'] + '; recovery SHA-256 `' + STOCK_SHA + '`.\n\n'
        'The original recovery ZIP, original source inputs, source build logs, and checksum/build evidence are retained here.\n')
    if release is None:
        release = api(f'repos/{REPO}/releases', {'tag_name': TAG, 'target_commitish': SOURCE,
            'name': TITLE, 'body': body, 'draft': True, 'prerelease': True})
    assert release['draft'] and release['target_commitish'] == SOURCE
    core = []
    for path in (package, source_inputs, logs):
        asset = upload(release, path)
        expected = metadata(path)
        verify_asset(asset, expected, work / ('roundtrip-' + path.name))
        core.append({**expected, 'release_asset_id': asset['id']})
    proof = {'source_commit': SOURCE, 'source_run': {k: run[k] for k in ('id', 'head_sha', 'conclusion', 'html_url')},
        'source_artifact': artifact, 'image': image, 'stock': config['stock'],
        'source_files_sha256': {name: hashlib.sha256(data).hexdigest() for name, data in contents.items()},
        'source_ndk': 'r29', 'source_cargo_ndk': '4.1.2',
        'device_boot_tested': False, 'data_decryption_configured': False,
        'core_assets_verified_by_release_download': core,
        'archive_recipe_commit': os.environ['GITHUB_SHA'], 'archive_run_id': os.environ['GITHUB_RUN_ID']}
    proof_path = work / 'BUILD_PROOF.json'
    proof_path.write_text(json.dumps(proof, indent=2) + '\n')
    upload(release, proof_path)
    sums = work / 'SHA256SUMS'
    sums.write_text(''.join(sha(path) + '  ' + path.name + '\n' for path in (package, source_inputs, logs, proof_path)))
    upload(release, sums)
    complete(release, work)
    body += ('\nAll five Release assets were downloaded again on the cloud runner and their sizes/SHA-256 checked. '
        'Image ZIP CRC and 100 MiB image SHA-256 were also checked.\n\n'
        'Archive validation: https://github.com/' + REPO + '/actions/runs/' + os.environ['GITHUB_RUN_ID'] + '\n')
    # Never publish, including on replay.
    subprocess.run(['gh', 'api', '--method', 'PATCH', f'repos/{REPO}/releases/{release["id"]}', '--input', '-'],
        input=json.dumps({'body': body, 'draft': True}).encode(), stdout=subprocess.DEVNULL, check=True)
