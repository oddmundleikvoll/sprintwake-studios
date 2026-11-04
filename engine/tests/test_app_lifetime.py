import multiprocessing as mp
import tempfile
import unittest
from pathlib import Path

from server.app_lifetime import AppLifetime, UpdatePending


def hold_reader(root, ready, release):
    life = AppLifetime(root=root, bundle="/bundle", build=1).enter_engine()
    ready.set()
    release.wait(5)
    life.close()


class AppLifetimeTests(unittest.TestCase):
    def test_reader_blocks_writer_and_pending_blocks_new_engine(self):
        with tempfile.TemporaryDirectory() as root:
            ready, release = mp.Event(), mp.Event()
            proc = mp.Process(target=hold_reader, args=(root, ready, release)); proc.start(); ready.wait(3)
            life = AppLifetime(root=root, bundle="/bundle", build=1).enter_app()
            life.reserve(2)
            self.assertFalse(life.writer_ready())
            with self.assertRaises(UpdatePending):
                AppLifetime(root=root, bundle="/bundle", build=1).enter_engine()
            release.set(); proc.join(5)
            self.assertTrue(life.writer_ready())
            life.close()

    def test_updated_app_clears_matching_marker_without_touching_data(self):
        with tempfile.TemporaryDirectory() as root:
            data = Path(root) / "projects"; data.mkdir(); (data / "board.json").write_text("keep")
            old = AppLifetime(root=root, bundle="/bundle", build=1).enter_app(); old.reserve(2); old.close()
            new = AppLifetime(root=root, bundle="/bundle", build=2).enter_app()
            self.assertFalse((Path(root) / "pending-update.json").exists())
            self.assertEqual((data / "board.json").read_text(), "keep")
            new.close()

    def test_malformed_marker_fails_closed_and_can_be_recovered_explicitly(self):
        with tempfile.TemporaryDirectory() as root:
            marker = Path(root) / "pending-update.json"
            marker.write_text("{not-json")
            with self.assertRaises(UpdatePending):
                AppLifetime(root=root, bundle="/bundle", build=2).enter_engine()
            # The failed startup did not retain the reader or instance lock;
            # an operator/recovery tool can replace the marker deliberately.
            marker.unlink()
            life = AppLifetime(root=root, bundle="/bundle", build=2).enter_engine()
            life.close()

    def test_handoff_marker_survives_owner_exit_until_new_build_starts(self):
        with tempfile.TemporaryDirectory() as root:
            old = AppLifetime(root=root, bundle="/bundle", build=1).enter_app()
            old.reserve(2)
            old.close()
            self.assertTrue((Path(root) / "pending-update.json").exists())
            with self.assertRaises(UpdatePending):
                AppLifetime(root=root, bundle="/bundle", build=1).enter_engine()
            new = AppLifetime(root=root, bundle="/bundle", build=2).enter_app()
            self.assertFalse((Path(root) / "pending-update.json").exists())
            new.close()


if __name__ == "__main__":
    unittest.main()
