"""Cloud-only packaging of exact existing socrates images; never builds or flashes."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CHUNK = 4 * 1024 * 1024
LIMIT = 2 * 1024 ** 3
PINS = {
    'arch': {
        'repo': 'Dingtaiqi/socrates-Arch', 'release': 439398957,
        'manifest_id': 728303125,
        'manifest_sha256': 'f17bc4a17be99b70b510a78df1299d0820159caa3a7d4e608ff6ee5220a732f8',
        'source_name': 'arch-kde-v4.img', 'image': 'images/system.img',
        'bytes': 3221225472,
        'sha256': '4e044e6358f8a76000fa3ab2325de96c412dfee87907d4db57bca62ca4494d19',
        'scope': 'Exact historically tested 3 GiB KDE seed; later Wi-Fi/menu runtime fixes are not baked into this image.',
    },
    'recovery': {
        'repo': 'Dingtaiqi/skyward', 'release': 441904971, 'asset': 736052223,
        'asset_name': 'skyward-bb88bc7-socrates-OS3.0.304.0.WMKCNXM.zip',
        'asset_bytes': 43464663,
        'asset_sha256': '6653c8a5fe0a5ca10a6ced2056a9ac87c28068aad79f82fc2718cd77fd6348d0',
        'source_name': 'skyward-bb88bc7-socrates-OS3.0.304.0.WMKCNXM.img',
        'image': 'images/recovery.img', 'bytes': 104857600,
        'sha256': 'bd1c80633e266071f90299f61d2361185c8337e016ce787a0576cd9f6e5e5e57',
        'compiled_commit': 'bb88bc785175590c07d57b89e1d98d573a70b8c0',
        'validation': 'https://github.com/Dingtaiqi/skyward/blob/a6112bd8bd39fb27489664d38bab836465aaf80c/devices/socrates-validation.json',
        'scope': 'Scoped native recovery_a tests; not a kernel-bearing fastboot boot image, full-data restore or slot-b validation.',
    },
}


def api(path, data=None):
    command = ['gh', 'api', '--method', 'GET' if data is None else 'POST', path]
    if data is not None:
        command += ['--input', '-']
    return json.loads(subprocess.check_output(command,
        input=None if data is None else json.dumps(data).encode()))


def download(repo, asset, target):
    with target.open('xb') as output:
        subprocess.run(['gh', 'api', f'repos/{repo}/releases/assets/{asset}',
            '-H', 'Accept: application/octet-stream'], stdout=output, check=True)


def digest(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            result.update(block)
    return result.hexdigest()


def checked_asset(repo, asset, target, size, sha256):
    assert asset['size'] == size and asset['digest'] == 'sha256:' + sha256
    assert asset['state'] == 'uploaded'
    download(repo, asset['id'], target)
    assert target.stat().st_size == size and digest(target) == sha256


def upload(release, package):
    url = release['upload_url'].split('{', 1)[0]
    assert urllib.parse.urlsplit(url).netloc == 'uploads.github.com'
    assert urllib.parse.urlsplit(url).scheme == 'https'
    # File body + explicit Content-Length streams the ZIP without loading GiB into RAM.
    with package.open('rb') as stream:
        request = urllib.request.Request(url + '?' + urllib.parse.urlencode({'name': package.name}),
            data=stream, method='POST', headers={
                'Authorization': 'Bearer ' + os.environ['GH_TOKEN'],
                'Content-Type': 'application/octet-stream',
                'Content-Length': str(package.stat().st_size),
                'Accept': 'application/vnd.github+json'})
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.load(response)


def fetch_image(kind, pin, work):
    release = api(f'repos/{pin["repo"]}/releases/{pin["release"]}')
    assert release['id'] == pin['release'] and release['draft']
    assets = {a['name']: a for a in release['assets']}
    image = work / 'exact-input.img'
    if kind == 'arch':
        manifest_asset = next(a for a in assets.values() if a['id'] == pin['manifest_id'])
        manifest_path = work / 'seed-manifest.json'
        checked_asset(pin['repo'], manifest_asset, manifest_path, manifest_asset['size'], pin['manifest_sha256'])
        manifest = json.loads(manifest_path.read_text())
        assert manifest['schema'] == 1 and manifest['compression'] == 'gzip'
        assert all(manifest[k] == pin[v] for k, v in [('name', 'source_name'), ('bytes', 'bytes'), ('sha256', 'sha256')])
        assert manifest['parts'] and len({p['name'] for p in manifest['parts']}) == len(manifest['parts'])
        with tempfile.TemporaryFile(dir=work) as joined:
            for part in manifest['parts']:
                assert re.fullmatch(r'arch-kde-v4\.img\.gz\.part[0-9]{3}', part['name'])
                path = work / part['name']
                checked_asset(pin['repo'], assets[part['name']], path, part['bytes'], part['sha256'])
                with path.open('rb') as stream:
                    shutil.copyfileobj(stream, joined, CHUNK)
                path.unlink()
            joined.seek(0)
            with gzip.GzipFile(fileobj=joined) as stream, image.open('xb') as output:
                bounded_copy(stream, output, pin['bytes'])
    else:
        asset = assets[pin['asset_name']]
        assert asset['id'] == pin['asset']
        original = work / 'original-recovery.zip'
        checked_asset(pin['repo'], asset, original, pin['asset_bytes'], pin['asset_sha256'])
        with zipfile.ZipFile(original) as archive:
            assert archive.namelist() == [pin['source_name']] and archive.testzip() is None
            with archive.open(pin['source_name']) as stream, image.open('xb') as output:
                bounded_copy(stream, output, pin['bytes'])
    assert image.stat().st_size == pin['bytes'] and digest(image) == pin['sha256']
    return image


def bounded_copy(source, target, limit):
    size = 0
    for block in iter(lambda: source.read(CHUNK), b''):
        size += len(block)
        assert size <= limit, 'Image exceeds its pinned byte length'
        target.write(block)
    assert size == limit


def documentation(kind):
    files = {}
    for category in ('common', kind):
        source = ROOT / 'delivery' / 'socrates' / category
        assert source.is_dir(), 'Missing reviewed Chinese docs: ' + str(source)
        for path in sorted(source.rglob('*')):
            assert not path.is_symlink(), 'No documentation symlink inputs'
            if not path.is_file():
                continue
            assert path.suffix.lower() in {'.md', '.png', '.sh', '.ps1', '.bat', '.py', '.json', '.txt'}
            assert '.private' not in path.name and path.stat().st_size < 25 * 1024 * 1024
            relative = path.relative_to(source).as_posix()
            files[f'docs/{category}/{relative}'] = path
    assert any(n.endswith('.md') for n in files)
    pngs = [p for n, p in files.items() if n.startswith(f'docs/{kind}/') and n.endswith('.png')]
    assert pngs, 'At least one documented screenshot or clearly marked illustration is required'
    assert all(p.open('rb').read(8) == b'\x89PNG\r\n\x1a\n' for p in pngs)
    if kind == 'recovery':
        assert len(pngs) == 1 and pngs[0].name == 'Recovery-K60Pro-离线渲染.png', 'Only the attributed K60 Pro render belongs in this bundle'
    return files


def verify_zip(path, pin, hashes):
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == len(set(archive.namelist()))
        assert set(archive.namelist()) == set(hashes) | {'SHA256SUMS'}
        for name, expected in hashes.items():
            result = hashlib.sha256()
            with archive.open(name) as stream:
                for block in iter(lambda: stream.read(CHUNK), b''):
                    result.update(block)
            assert result.hexdigest() == expected, 'Packaged bytes differ: ' + name
        assert archive.getinfo(pin['image']).file_size == pin['bytes']
        assert hashes[pin['image']] == pin['sha256']
        expected_sums = ''.join(f'{value}  {name}\n' for name, value in sorted(hashes.items()))
        assert archive.read('SHA256SUMS').decode() == expected_sums


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kind', choices=['arch', 'recovery', 'evox'])
    kind = parser.parse_args().kind
    assert kind in PINS, 'Exact tested patched EvoX 648b759 input is not archived yet; refuse any replacement or rebuild'
    pin = PINS[kind]
    assert os.environ['GITHUB_REPOSITORY'] == pin['repo'], 'Use the source repository token; no cross-repository PAT required'
    docs = documentation(kind)
    run, attempt = os.environ['GITHUB_RUN_ID'], os.environ['GITHUB_RUN_ATTEMPT']
    tag = f'socrates-{kind}-delivery-{run}-{attempt}'
    with tempfile.TemporaryDirectory(prefix='socrates-delivery-') as temporary:
        work = Path(temporary)
        image = fetch_image(kind, pin, work)
        provenance = {'device': 'REDMI K60 Pro', 'codename': 'socrates', 'kind': kind,
            'packaging_commit': os.environ['GITHUB_SHA'],
            'packaging_workflow': f'https://github.com/{pin["repo"]}/actions/runs/{run}',
            'image_input': pin, 'image_rebuilt_or_modified': False,
            'original_userdata_included': False, 'release_must_remain_draft': True,
            'documentation_sha256': {n: digest(p) for n, p in docs.items()}}
        render_files = {}
        if kind == 'recovery':
            evidence = Path(os.environ['RUNNER_TEMP']) / 'recovery-preview-proof'
            record = json.loads((evidence / 'RENDER_PROVENANCE.json').read_text())
            assert record['source_commit'] == pin['compiled_commit']
            assert record['recovery_image_rebuilt_or_modified'] is False
            png = ROOT / 'delivery/socrates/recovery/screenshots/Recovery-K60Pro-离线渲染.png'
            assert digest(png) == record['image_sha256']
            provenance['screenshot_rendering'] = record
            for name in ('RENDER_PROVENANCE.json', 'preview.patch', 'original-main.rs', 'rendered-main.rs', 'Cargo.lock', 'socrates.toml'):
                render_files['packaging/recovery-preview/' + name] = (evidence / name).read_bytes()
            render_files['packaging/recovery-preview/render-socrates-recovery.py'] = (ROOT / 'ci/render-socrates-recovery.py').read_bytes()
        payloads = {'PROVENANCE.json': (json.dumps(provenance, indent=2, ensure_ascii=False) + '\n').encode(),
            'README_先阅读.txt': ('仅适用于 REDMI K60 Pro（socrates），不适用于 K60 / myron。\n'
                '请先阅读 docs/common 和 docs/' + kind + ' 中的中文说明及截图来源。\n'
                '本包只原样打包固定已验证镜像；不包含个人 userdata，不自动刷机、不切换槽位。\n'
                'DSU 启用条件、当前系统依赖和已知限制以对应说明为准。\n').encode(),
            'packaging/package-socrates-delivery.py': Path(__file__).read_bytes()}
        payloads.update(render_files)
        workflow = ROOT / '.github' / 'workflows' / 'package-socrates-delivery.yml'
        payloads['packaging/package-socrates-delivery.yml'] = workflow.read_bytes()
        hashes = {**{n: digest(p) for n, p in docs.items()}, pin['image']: pin['sha256'],
            **{n: hashlib.sha256(data).hexdigest() for n, data in payloads.items()}}
        package = work / f'K60Pro-socrates-{kind}-verified-{pin["sha256"][:12]}.zip'
        with zipfile.ZipFile(package, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=1, allowZip64=True) as archive:
            with image.open('rb') as source, archive.open(pin['image'], 'w', force_zip64=True) as target:
                bounded_copy(source, target, pin['bytes'])
            for name, path in docs.items():
                archive.write(path, name)
            for name, data in payloads.items():
                archive.writestr(name, data)
            archive.writestr('SHA256SUMS', ''.join(f'{value}  {name}\n' for name, value in sorted(hashes.items())))
        verify_zip(package, pin, hashes)
        size, expected = package.stat().st_size, digest(package)
        print(json.dumps({'package': package.name, 'bytes': size, 'sha256': expected}), flush=True)
        assert size < LIMIT, f'Compressed bundle is {size} bytes (>= 2 GiB); not uploaded. Use reviewed 7z volumes for this one logical bundle.'
        body = ('Private draft delivery for REDMI K60 Pro / socrates only.\n\n'
            f'Exact input image SHA-256: `{pin["sha256"]}` ({pin["bytes"]} bytes).\n\n'
            'Chinese usage notes, screenshot attribution, guarded deployment tools where supplied, checksums and provenance are inside the ZIP. '
            'No new image was built and no phone was connected. Historical debugging configuration may remain; do not publish this draft.\n\n'
            f'Packaging run: {provenance["packaging_workflow"]}\n')
        release = api(f'repos/{pin["repo"]}/releases', {'tag_name': tag,
            'target_commitish': os.environ['GITHUB_SHA'], 'name': f'K60 Pro / {kind} verified delivery / {run}',
            'body': body, 'draft': True, 'prerelease': True})
        assert release['draft'] and not release['assets']
        upload(release, package)
        saved = api(f'repos/{pin["repo"]}/releases/{release["id"]}')
        assert saved['draft'] and len(saved['assets']) == 1
        asset = saved['assets'][0]
        assert asset['name'] == package.name and asset['size'] == size and asset['digest'] == 'sha256:' + expected
        readback = work / 'release-readback.zip'
        checked_asset(pin['repo'], asset, readback, size, expected)
        verify_zip(readback, pin, hashes)
        result = {'release_url': saved['html_url'], 'release_id': saved['id'], 'draft': True,
            'kind': kind, 'asset': {k: asset[k] for k in ('id', 'name', 'size', 'digest')},
            'image': pin, 'cloud_release_readback_and_zip_crc_and_all_member_sha256_verified': True,
            'packaging_commit': os.environ['GITHUB_SHA'], 'run': run}
        print('DELIVERY_RESULT_JSON=' + json.dumps(result), flush=True)
        Path(os.environ['GITHUB_STEP_SUMMARY']).write_text(json.dumps(result, indent=2) + '\n')


if __name__ == '__main__':
    main()
