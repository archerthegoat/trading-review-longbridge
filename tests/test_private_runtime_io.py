from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "trading-center-review" / "scripts" / "private_runtime_io.py"
SPEC = importlib.util.spec_from_file_location("private_runtime_io_tested", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("could not load private runtime helper")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class PrivateRuntimeIOTests(unittest.TestCase):
    def setUp(self) -> None:
        MODULE.PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        MODULE.PRIVATE_ROOT.chmod(0o700)

    def test_atomic_write_creates_owner_only_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="private-io-", dir=str(MODULE.PRIVATE_ROOT)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o700)
            output = directory_path / "report.md"
            written = MODULE.write_owner_only_text(output, "synthetic\n")
            self.assertEqual(written.read_text(encoding="utf-8"), "synthetic\n")
            self.assertEqual(stat.S_IMODE(written.stat().st_mode), 0o600)

    def test_outside_broad_and_symlink_paths_fail_closed(self) -> None:
        with self.assertRaises(MODULE.PrivateRuntimeError):
            MODULE.prepare_private_output(ROOT / "outside.md")
        with tempfile.TemporaryDirectory(prefix="private-io-", dir=str(MODULE.PRIVATE_ROOT)) as directory:
            directory_path = Path(directory)
            directory_path.chmod(0o755)
            with self.assertRaises(MODULE.PrivateRuntimeError):
                MODULE.prepare_private_output(directory_path / "broad.md")
            directory_path.chmod(0o700)
            target = directory_path / "target"
            target.mkdir(mode=0o700)
            link = directory_path / "linked"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(MODULE.PrivateRuntimeError):
                MODULE.prepare_private_output(link / "symlinked.md")


if __name__ == "__main__":
    unittest.main()
