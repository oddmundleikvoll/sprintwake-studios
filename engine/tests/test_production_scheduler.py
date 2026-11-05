"""Independent scheduler behavior tests.

SPDX-License-Identifier: MIT
Copyright (c) 2026 Oddmund Leikvoll
"""
import threading
import time
import unittest

from engine.server.jobs import Scheduler


class SchedulerTests(unittest.TestCase):
    def test_stop_is_scoped_and_slot_drains(self):
        scheduler = Scheduler()
        started = threading.Event(); release = threading.Event()

        def worker(job):
            started.set()
            while not job.cancel_event.is_set():
                if release.wait(.01): break
            return "done"

        first = scheduler.submit("a", "film", worker)
        self.assertTrue(started.wait(1))
        self.assertFalse(scheduler.stop("b", first.id))
        self.assertTrue(scheduler.stop("a", first.id))
        with self.assertRaises(RuntimeError): scheduler.submit("a", "film", worker)
        scheduler.wait("a", 1)
        second = scheduler.submit("a", "film", worker)
        self.assertNotEqual(first.id, second.id)
        scheduler.stop("a", second.id); scheduler.wait("a", 1)

    def test_global_single_admission_and_quiesce(self):
        scheduler = Scheduler(max_workers=8)
        started = threading.Event(); release = threading.Event()
        def worker(job):
            started.set(); release.wait(1)
        first = scheduler.submit("a", "film", worker)
        self.assertTrue(started.wait(1))
        with self.assertRaises(RuntimeError): scheduler.submit("b", "film", worker)
        self.assertTrue(scheduler.snapshot()["busy"])
        scheduler.quiesce()
        with self.assertRaises(RuntimeError): scheduler.submit("b", "film", worker)
        self.assertTrue(scheduler.quiesced)
        self.assertFalse(scheduler.stop("a", "wrong"))
        scheduler.stop("a", first.id)
        release.set(); done = scheduler.drain("a", first.id, 1)
        self.assertEqual(done.status, "cancelled")
        scheduler.resume()
        self.assertFalse(scheduler.quiesced)

    def test_before_start_guard_runs_under_admission(self):
        scheduler = Scheduler()
        with self.assertRaises(RuntimeError):
            scheduler.submit("a", "language_work", lambda job: None, before_start=lambda: False)


if __name__ == "__main__": unittest.main()
