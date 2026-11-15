import base64
import json
from pathlib import Path
import tempfile
import unittest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from update_catalog import catalog, public_bytes


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.images = Path(self.temp.name)
        self.key = Ed25519PrivateKey.generate()
        self.trusted = public_bytes(self.key)
        self.image = self.images / "skyward-v0.2.1-myron-OS4.0.6.0.zip"
        self.image.write_bytes(b"test image bytes")

    def make(self):
        return catalog(self.images, "v0.2.1", self.key, self.trusted)

    def test_signed_bytes_and_digest_cover_the_actual_zip(self):
        import hashlib
        signed = self.make()
        payload = base64.b64decode(signed["payload"])
        self.key.public_key().verify(base64.b64decode(signed["signature"]), payload)
        asset = json.loads(payload)["assets"][0]
        self.assertEqual(asset["digest"], "sha256:" + hashlib.sha256(self.image.read_bytes()).hexdigest())
        self.assertEqual(asset["size"], len(self.image.read_bytes()))
        self.assertIn("/Dingtaiqi/skyward/", asset["browser_download_url"])

    def test_wrong_signing_key_is_rejected(self):
        self.trusted = public_bytes(Ed25519PrivateKey.generate())
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.make()

    def test_multiple_variants_of_a_phone_are_not_selected_arbitrarily(self):
        (self.images / "skyward-v0.2.1-myron-OTHER.zip").write_bytes(b"another variant")
        with self.assertRaisesRegex(ValueError, "multiple builds"):
            self.make()

    def test_diagnostics_and_wrong_tags_are_not_published(self):
        self.image.rename(self.image.with_name("skyward-v0.2.1-myron-diagnostic.zip"))
        with self.assertRaisesRegex(ValueError, "not a release"):
            self.make()

    def test_empty_or_missing_images_are_not_published(self):
        self.image.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.make()
        self.image.unlink()
        with self.assertRaisesRegex(ValueError, "no recovery"):
            self.make()


if __name__ == "__main__":
    unittest.main()
