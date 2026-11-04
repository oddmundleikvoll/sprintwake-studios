import threading
import unittest
import os
from types import SimpleNamespace

from server.update_lifecycle import UpdateBusy, UpdateLifecycle


class Backend:
    id = "configured"

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = 0

    def admission_ready(self):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class Film:
    def __init__(self, busy=False, malformed=False):
        self.busy = busy
        self.malformed = malformed

    def status(self):
        if self.malformed:
            return {"busy": "unknown"}
        return {"busy": self.busy}


def context(*, backend=None, film=None, **fields):
    orch = SimpleNamespace(
        _start_lock=threading.RLock(), _closing=False, busy=False,
        stills_busy=False, image_busy=False, _project_batch_active=False,
        _llm_active=0,
    )
    for key, value in fields.items():
        setattr(orch, key, value)
    return SimpleNamespace(orch=orch, backend=backend or Backend(), film=film or Film())


class UpdateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._old_token = os.environ.get("SPRINTWAKE_UPDATE_TOKEN")
        os.environ["SPRINTWAKE_UPDATE_TOKEN"] = "test-capability"

    @classmethod
    def tearDownClass(cls):
        if cls._old_token is None:
            os.environ.pop("SPRINTWAKE_UPDATE_TOKEN", None)
        else:
            os.environ["SPRINTWAKE_UPDATE_TOKEN"] = cls._old_token

    def gate(self):
        return UpdateLifecycle(context())

    def test_idle_quiesce_returns_lease_and_resume_reopens(self):
        gate = self.gate()
        before = gate.status()
        self.assertTrue(before["ready"])
        result = gate.quiesce("test-capability")
        self.assertTrue(result["quiesced"])
        self.assertTrue(result["leaseToken"])
        with self.assertRaises(UpdateBusy):
            gate.assert_open("render")
        resumed = gate.resume(result["leaseToken"], "test-capability")
        self.assertFalse(resumed["quiesced"])
        self.assertTrue(resumed["ready"])

    def test_missing_or_wrong_capability_cannot_reserve(self):
        gate = self.gate()
        with self.assertRaises(UpdateBusy):
            gate.quiesce(None)
        with self.assertRaises(UpdateBusy):
            gate.quiesce("wrong")

    def test_each_active_dimension_blocks_without_cancellation(self):
        for field, reason in (
            ("busy", "render-active"), ("stills_busy", "stills-active"),
            ("image_busy", "images-active"),
            ("_project_batch_active", "project-batch-active"),
            ("_llm_active", "language-active"),
        ):
            ctx = context(**{field: (1 if field == "_llm_active" else True)})
            ctx.orch._cancel = threading.Event()
            gate = UpdateLifecycle(ctx)
            status = gate.status()
            self.assertFalse(status["ready"], field)
            self.assertIn(reason, status["blockedReasons"])
            self.assertFalse(ctx.orch._cancel.is_set())
            with self.assertRaises(UpdateBusy):
                gate.quiesce("test-capability")

    def test_film_busy_and_malformed_state_fail_closed(self):
        for film, reason in ((Film(busy=True), "film-active"), (Film(malformed=True), "film-status-uncertain")):
            gate = UpdateLifecycle(context(film=film))
            status = gate.status()
            self.assertFalse(status["ready"])
            self.assertIn(reason, status["blockedReasons"])

    def test_comfy_unreachable_or_rejected_fails_closed(self):
        for backend in (Backend(error=OSError("offline")), Backend(result=False)):
            status = UpdateLifecycle(context(backend=backend)).status()
            self.assertFalse(status["ready"])
            self.assertTrue(any("ComfyUI-admission" in value for value in status["blockedReasons"]))

    def test_missing_comfy_admission_is_unknown(self):
        backend = SimpleNamespace(id="comfy-workflow")
        status = UpdateLifecycle(context(backend=backend)).status()
        self.assertFalse(status["ready"])
        self.assertIn("backend-status-uncertain", status["blockedReasons"])

    def test_lease_is_serialized_with_start_lock(self):
        gate = self.gate()
        token = gate.quiesce("test-capability")["leaseToken"]
        with self.assertRaises(UpdateBusy):
            gate.resume("wrong-token", "test-capability")
        self.assertTrue(gate.status()["quiesced"])
        gate.resume(token, "test-capability")

    def test_inflight_http_mutation_blocks_quiesce(self):
        gate = self.gate()
        gate.enter_http_mutation("/api/boards/demo")
        try:
            status = gate.status()
            self.assertFalse(status["ready"])
            self.assertIn("http-mutation-active", status["blockedReasons"])
            with self.assertRaises(UpdateBusy):
                gate.quiesce("test-capability")
        finally:
            gate.exit_http_mutation()
        lease = gate.quiesce("test-capability")
        self.assertTrue(lease["quiesced"])


if __name__ == "__main__":
    unittest.main()
