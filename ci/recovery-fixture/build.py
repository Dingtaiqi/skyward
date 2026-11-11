"""Fork-only, no Gradle/downloads: create an install-only, no-code CE fixture."""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).with_name("AndroidManifest.xml")
OUT = ROOT / "fixture-apk-out"
PACKAGE = "org.skyward.recovery.fixture"
ANDROID = "{http://schemas.android.com/apk/res/android}"


def run(*args, env=None):
    return subprocess.check_output(
        [str(arg) for arg in args], text=True, encoding="utf-8",
        stderr=subprocess.STDOUT, env=env,
    )


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def version(path):
    return tuple(int(part) for part in re.findall(r"\d+", path.name))


def main():
    require(os.environ.get("GITHUB_REPOSITORY") == "Dingtaiqi/skyward",
            "Only run in the explicitly authorized fork's disposable runner")
    require(not OUT.exists(), "Refuse to mix the artifact with previous output")
    manifest = ET.parse(SOURCE).getroot()
    require(manifest.tag == "manifest" and manifest.get("package") == PACKAGE,
            "Unexpected manifest identity")
    require([node.tag for node in manifest.iter()] == ["manifest", "uses-sdk", "application"],
            "No permissions, executable components, or other manifest elements allowed")
    sdk_node, app = list(manifest)
    require(sdk_node.attrib == {ANDROID + "minSdkVersion": "30", ANDROID + "targetSdkVersion": "36"},
            "Unexpected SDK range")
    require(app.attrib == {
        ANDROID + "label": "Skyward recovery fixture", ANDROID + "hasCode": "false",
        ANDROID + "debuggable": "true", ANDROID + "allowBackup": "false",
        ANDROID + "extractNativeLibs": "false",
    }, "Unexpected application capability or code declaration")

    sdk = Path(os.environ.get("ANDROID_SDK_ROOT") or os.environ["ANDROID_HOME"])
    tools = sorted((sdk / "build-tools").glob("*"), key=version, reverse=True)
    tools = next((path for path in tools if all((path / name).is_file()
                  for name in ("aapt2", "zipalign", "apksigner"))), None)
    require(tools is not None, "No complete preinstalled Android build-tools; no download attempted")
    platforms = [path for path in (sdk / "platforms").glob("android-*")
                 if path.name.removeprefix("android-").isdigit()
                 and int(path.name.removeprefix("android-")) >= 36
                 and (path / "android.jar").is_file()]
    require(platforms, "Need a preinstalled Android platform >= 36; no download attempted")
    platform = next((path for path in platforms if path.name == "android-36"),
                    max(platforms, key=version))
    OUT.mkdir()
    apk = OUT / "skyward-recovery-fixture.apk"
    # The private key and random password are temporary and never enter OUT or logs.
    with tempfile.TemporaryDirectory(prefix="skyward-fixture-sign-") as temporary:
        tmp = Path(temporary)
        unsigned, aligned = tmp / "unsigned.apk", tmp / "aligned.apk"
        run(tools / "aapt2", "link", "--manifest", SOURCE,
            "-I", platform / "android.jar", "-o", unsigned)
        run(tools / "zipalign", "-f", "4", unsigned, aligned)
        env = os.environ.copy()
        env["FIXTURE_KEYSTORE_PASSWORD"] = secrets.token_hex(32)
        run("keytool", "-genkeypair", "-noprompt", "-keystore", tmp / "fixture.p12",
            "-storetype", "PKCS12", "-storepass:env", "FIXTURE_KEYSTORE_PASSWORD",
            "-keypass:env", "FIXTURE_KEYSTORE_PASSWORD", "-alias", "fixture",
            "-keyalg", "RSA", "-keysize", "2048", "-validity", "365",
            "-dname", "CN=Skyward recovery fixture", env=env)
        run("keytool", "-exportcert", "-keystore", tmp / "fixture.p12",
            "-storepass:env", "FIXTURE_KEYSTORE_PASSWORD", "-alias", "fixture",
            "-file", tmp / "certificate.der", env=env)
        certificate_sha = hashlib.sha256((tmp / "certificate.der").read_bytes()).hexdigest()
        run(tools / "apksigner", "sign", "--ks", tmp / "fixture.p12",
            "--ks-key-alias", "fixture", "--ks-pass", "env:FIXTURE_KEYSTORE_PASSWORD",
            "--key-pass", "env:FIXTURE_KEYSTORE_PASSWORD", "--min-sdk-version", "30",
            "--v1-signing-enabled", "false", "--v2-signing-enabled", "true",
            "--v3-signing-enabled", "true", "--v4-signing-enabled", "false",
            "--out", apk, aligned, env=env)
        del env["FIXTURE_KEYSTORE_PASSWORD"]

    verify = run(tools / "apksigner", "verify", "--verbose", "--print-certs", apk)
    print(verify)
    # New SDKs label v3 signers differently from older "Signer #1" output.
    # Compare every reported signing certificate to the actual exported DER.
    certs = re.findall(r"^.*certificate SHA-256 digest: ([0-9a-fA-F]{64})$",
                       verify, re.MULTILINE)
    require(certs and {cert.lower() for cert in certs} == {certificate_sha},
            "APK verification did not report precisely our signing certificate")
    run(tools / "zipalign", "-c", "4", apk)
    tree = run(tools / "aapt2", "dump", "xmltree", apk, "--file", "AndroidManifest.xml")
    print(tree)
    badging = run(tools / "aapt2", "dump", "badging", apk)
    elements = re.findall(r"^\s*E: ([^\s(]+)", tree, re.MULTILINE)
    require(elements == ["manifest", "uses-sdk", "application"],
            "Compiled manifest includes an unexpected permission/component/element")
    for name, expected in (("hasCode", "false"), ("debuggable", "true"),
                           ("allowBackup", "false"), ("extractNativeLibs", "false")):
        require(re.search(r"A: android:" + name + r"\([^)]*\)=" + expected + r"\b", tree),
                "Compiled manifest does not preserve " + name + "=" + expected)
    require("package: name='" + PACKAGE + "'" in badging
            and "sdkVersion:'30'" in badging and "targetSdkVersion:'36'" in badging
            and "application-label:'Skyward recovery fixture'" in badging,
            "Compiled manifest identity/SDK/label differs")
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "Duplicate APK ZIP entries")
        require("AndroidManifest.xml" in names and
                set(names) <= {"AndroidManifest.xml", "resources.arsc"},
                "APK contains something besides the manifest/resource table")
        require(archive.testzip() is None, "APK ZIP CRC failed")

    sha = hashlib.sha256(apk.read_bytes()).hexdigest()
    head = run("git", "-C", ROOT, "rev-parse", "HEAD").strip()
    proof = {
        "source_commit": head, "package": PACKAGE, "label": "Skyward recovery fixture",
        "apk": apk.name, "apk_bytes": apk.stat().st_size, "apk_sha256": sha,
        "certificate_sha256": certificate_sha, "min_sdk": 30, "target_sdk": 36,
        "compile_platform": platform.name, "build_tools": tools.name,
        "aapt2_version": run(tools / "aapt2", "version").strip(),
        "zip_entries": names, "manifest_elements": elements,
        "permissions": [], "components": [], "has_code": False, "debuggable": True,
        "private_key_uploaded": False, "device_installation_performed": False,
        "run_url": "https://github.com/Dingtaiqi/skyward/actions/runs/" + os.environ["GITHUB_RUN_ID"],
        "official_references": ["https://developer.android.com/tools/aapt2",
                                "https://developer.android.com/tools/apksigner",
                                "https://developer.android.com/guide/topics/manifest/application-element"],
    }
    (OUT / "PROOF.json").write_text(json.dumps(proof, indent=2) + "\n", encoding="utf-8")
    (OUT / "SHA256SUMS").write_text(sha + "  " + apk.name + "\n", encoding="ascii")
    (OUT / "CERTIFICATE_SHA256").write_text(certificate_sha + "\n", encoding="ascii")
    (OUT / "compiled-manifest.txt").write_text(tree, encoding="utf-8")
    (OUT / "badging.txt").write_text(badging, encoding="utf-8")
    (OUT / "signature-verification.txt").write_text(verify, encoding="utf-8")
    shutil.copyfile(SOURCE, OUT / "AndroidManifest.xml")
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(error.output)
        raise
