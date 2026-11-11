"""Render two preview pages from pinned source; never modifies the recovery image."""
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

SOURCE = 'bb88bc785175590c07d57b89e1d98d573a70b8c0'
MAIN = 'crates/skyward-preview/src/main.rs'


def change_once(text, old, new):
    assert text.count(old) == 1, 'Pinned preview source changed: ' + old
    return text.replace(old, new)


def patch(text):
    text = change_once(text, 'root.join("devices/nezha.toml")', 'root.join("devices/socrates.toml")')
    start = text.index('fn nezha(cfg: &DeviceConfig) -> DeviceInfo {')
    end = text.index('\n}\n', start) + 2
    info = text[start:end]
    info = change_once(info, 'model: "Xiaomi 17 Ultra by Leica".into(),', 'model: cfg.device.model.clone(),')
    info = change_once(info, 'os_version: Some("OS4.0.4.0.XPACNXM".into()),', 'os_version: Some("OS3.0.304.0.WMKCNXM".into()),')
    info = change_once(info, 'slot: Some(Slot::B),', 'slot: Some(Slot::A),')
    text = text[:start] + info + text[end:]
    start = text.index('for (name, screen, dark, info, data, pattern) in shots {')
    end = text.index('\n    }', start)
    loop = text[start:end]
    loop = change_once(loop, 'println!("wrote {}", path.display());',
        'println!("wrote {}", path.display());\n        if name == "02-home-light" { return Ok(()); }')
    return text[:start] + loop + text[end:]


def main():
    assert os.environ['GITHUB_REPOSITORY'] == 'Dingtaiqi/skyward'
    assert os.environ['GITHUB_ACTIONS'] == 'true', 'Build the renderer on the disposable cloud runner only'
    root = Path(__file__).resolve().parents[1]
    temporary = Path(os.environ['RUNNER_TEMP']).resolve()
    source = temporary / 'socrates-preview-bb88bc7'
    evidence = temporary / 'recovery-preview-proof'
    output = temporary / 'socrates-preview-output'
    for path in (source, evidence, output):
        assert path.parent == temporary
        path.mkdir()
    subprocess.run(['git', 'fetch', '--no-tags', '--depth=1', 'origin', SOURCE], cwd=root, check=True)
    assert subprocess.check_output(['git', 'rev-parse', SOURCE], cwd=root, text=True).strip() == SOURCE
    archive = temporary / 'socrates-preview-source.tar'
    with archive.open('xb') as stream:
        subprocess.run(['git', 'archive', '--format=tar', SOURCE], cwd=root, stdout=stream, check=True)
    with tarfile.open(archive) as stream:
        for entry in stream:
            assert entry.isfile() or entry.isdir(), 'Unexpected linked archive member'
            assert not Path(entry.name).is_absolute() and '..' not in Path(entry.name).parts
        stream.extractall(source, filter='data')
    original = (source / MAIN).read_text()
    modified = patch(original)
    (source / MAIN).write_text(modified)
    changes = ''.join(difflib.unified_diff(original.splitlines(keepends=True), modified.splitlines(keepends=True),
        fromfile='a/' + MAIN, tofile='b/' + MAIN))
    (evidence / 'preview.patch').write_text(changes)
    (evidence / 'original-main.rs').write_text(original)
    (evidence / 'rendered-main.rs').write_text(modified)
    shutil.copyfile(source / 'Cargo.lock', evidence / 'Cargo.lock')
    shutil.copyfile(source / 'devices/socrates.toml', evidence / 'socrates.toml')
    # Explicit override uses the installed native stable toolchain without selecting
    # rust-toolchain.toml's Android target; this is a host-only UI renderer.
    env = {**os.environ, 'RUSTUP_TOOLCHAIN': 'stable'}
    command = ['cargo', 'run', '--locked', '--release', '-p', 'skyward-preview', '--', str(output), '--lang', 'zh-CN']
    subprocess.run(command, cwd=source, env=env, check=True)
    assert {p.name for p in output.glob('*.png')} == {'01-decrypt-dark.png', '02-home-light.png'}
    png = output / '02-home-light.png'
    target = root / 'delivery/socrates/recovery/screenshots/Recovery-K60Pro-离线渲染.png'
    target.parent.mkdir(parents=True, exist_ok=True)
    assert not target.exists(), 'Do not replace a supplied screenshot'
    shutil.copyfile(png, target)
    sha = lambda data: hashlib.sha256(data).hexdigest()
    record = {'kind': 'offline UI rendering; not a real-device screenshot',
        'source_commit': SOURCE, 'source_main_git_blob': subprocess.check_output(
            ['git', 'rev-parse', SOURCE + ':' + MAIN], cwd=root, text=True).strip(),
        'source_main_sha256': sha(original.encode()), 'rendered_main_sha256': sha(modified.encode()),
        'patch_sha256': sha(changes.encode()), 'image_sha256': sha(png.read_bytes()),
        'config': 'devices/socrates.toml', 'model': 'REDMI K60 Pro',
        'os_version': 'OS3.0.304.0.WMKCNXM', 'slot': 'a', 'locale': 'zh-CN',
        'sample_telemetry_is_not_measured_device_evidence': True,
        'recovery_image_rebuilt_or_modified': False,
        'command': command, 'rustc': subprocess.check_output(['rustc', '--version'], env=env, text=True).strip()}
    (evidence / 'RENDER_PROVENANCE.json').write_text(json.dumps(record, indent=2) + '\n')
    print('RECOVERY_OFFLINE_RENDER=' + json.dumps(record), flush=True)


if __name__ == '__main__':
    main()
