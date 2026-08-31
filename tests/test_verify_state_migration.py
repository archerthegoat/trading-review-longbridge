import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "skills/trading-center-review/scripts"
sys.path.insert(0, str(SCRIPTS))
import trading_review_state as state
import verify_state_migration as migration


class MigrationProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="trading-migration-proof-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.chmod(0o700)
        self.db = self.root / "review.sqlite3"
        with state.open_state_store(self.db, test_root=self.root) as store:
            self.backup = state._backup_database(store.connection, self.db)

    def test_closed_wal_backup_readback_never_needs_companion_writes(self):
        self.assertEqual(self.backup.read_bytes()[18:20], bytes((2, 2)))
        before_mode = self.backup.stat().st_mode
        with mock.patch.object(migration.sqlite3, "connect", wraps=sqlite3.connect) as connect:
            proof = migration.fingerprint(self.backup, standalone_backup=True)
            self.assertEqual(connect.call_args.args[0], self.backup.as_uri() + "?mode=ro&immutable=1")
        self.assertEqual(proof, migration.fingerprint(self.db))
        self.assertEqual(proof["quick_check"], ["ok"])
        self.assertEqual(proof["foreign_key_error_count"], 0)
        self.assertEqual(self.backup.stat().st_mode, before_mode)
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.backup) + suffix).exists())

    def test_immutable_mode_is_forbidden_for_the_live_database(self):
        with self.assertRaisesRegex(state.StateContractError, "named standalone backup"):
            migration.fingerprint(self.db, standalone_backup=True)

    def test_immutable_backup_refuses_existing_companion_state(self):
        for suffix in ("-wal", "-shm", "-journal"):
            with self.subTest(suffix=suffix):
                companion = Path(str(self.backup) + suffix)
                descriptor = os.open(companion, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                try:
                    with self.assertRaisesRegex(state.StateContractError, "companion files"):
                        migration.fingerprint(self.backup, standalone_backup=True)
                finally:
                    companion.unlink()


if __name__ == "__main__":
    unittest.main()
