"""Local artifact integrity and atomic publication; only synthetic bytes."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from rsi_kernel_agent.artifacts import ArtifactStore
    from rsi_kernel_agent.contracts import ArtifactRef, ValidationError
except ImportError:
    ArtifactStore = None


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(ArtifactStore, "Phase 2A artifact storage is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = ArtifactStore(self.root)

    def test_put_reuses_content_address_and_survives_reopen(self):
        first = self.store.put(b"abc")
        self.assertEqual(first.digest, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(first.size, 3)
        self.assertEqual(self.store.put(b"abc"), first)
        self.assertEqual(ArtifactStore(self.root).read(first), b"abc")
        self.store.verify(first)

    def test_same_content_writers_publish_one_complete_object(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            refs = list(pool.map(self.store.put, [b"fixture\n" * 1000] * 32))
        self.assertEqual(len(set(refs)), 1)
        self.assertEqual(self.store.read(refs[0]), b"fixture\n" * 1000)
        self.assertEqual(len([path for path in self.root.rglob("*") if path.is_file()]), 1)

    def test_existing_corrupt_object_is_never_overwritten(self):
        ref = self.store.put(b"abc")
        self.store.path_for(ref).write_bytes(b"bad")
        with self.assertRaises(ValidationError):
            self.store.read(ref)
        with self.assertRaises(ValidationError):
            self.store.put(b"abc")
        self.assertEqual(self.store.path_for(ref).read_bytes(), b"bad")

    def test_wrong_size_or_missing_artifact_fails_verification(self):
        ref = self.store.put(b"abc")
        with self.assertRaises(ValidationError):
            self.store.verify(ArtifactRef(ref.digest, 99))
        with self.assertRaises(ValidationError):
            self.store.verify(ArtifactRef("0" * 64, 0))

    def test_untrusted_reference_cannot_escape_artifact_directory(self):
        for ref in (ArtifactRef("../escape", 1), ArtifactRef("A" * 64, 1), ArtifactRef("a" * 64, -1), ArtifactRef("a" * 64, True)):
            with self.subTest(ref=ref), self.assertRaises(ValidationError):
                self.store.path_for(ref)
        with self.assertRaises(ValidationError):
            self.store.put("bytes required")

    def test_symlink_object_is_rejected_when_platform_allows_symlinks(self):
        ref = self.store.put(b"abc")
        path = self.store.path_for(ref)
        path.unlink()
        target = self.root / "outside-object"
        target.write_bytes(b"abc")
        try:
            path.symlink_to(target)
        except OSError as error:
            self.skipTest(f"Platform symlink permission unavailable: {error}")
        with self.assertRaises(ValidationError):
            self.store.read(ref)

    @unittest.skipUnless(os.name == "nt", "Windows junction behavior")
    def test_windows_junction_directory_cannot_redirect_publication(self):
        target = self.root / "redirect-target"
        target.mkdir()
        junction = self.root / "sha256"
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        try:
            with self.assertRaises(ValidationError):
                self.store.put(b"abc")
            self.assertEqual(list(target.iterdir()), [])
        finally:
            junction.rmdir()


if __name__ == "__main__":
    unittest.main()
