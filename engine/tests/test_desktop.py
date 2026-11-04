"""Native lifetime tests using pipes and inert HTTP children, never models."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from server.desktop import DesktopLifetime, stop_owned_work
from server.film import GuardedLLM, FilmController
from server.orchestrator import _gpu_start

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_launcher", ROOT / "local/launch.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)

# A small real server exercises pipe inheritance, readiness and process reaping.
CHILD = '''
import json, os, signal, sys, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from server.desktop import DesktopLifetime
from types import SimpleNamespace
root, port, mode = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
(root / 'child.pid').write_text(str(os.getpid()))
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        info = {'installationRoot': str(root), 'pid': os.getpid(), 'desktopOwned': True}
        if mode == 'wrong-owner': info['pid'] += 1
        if mode == 'not-ready': self.send_error(503); return
        self.send_response(200); self.end_headers()
        self.wfile.write(json.dumps(info).encode())
httpd = ThreadingHTTPServer(('127.0.0.1', port), Handler)
orch = SimpleNamespace(_start_lock=threading.RLock(), _cancel=threading.Event())
lifetime = DesktopLifetime(SimpleNamespace(orch=orch), httpd)
lifetime.watch(sys.stdin.buffer)
signal.signal(signal.SIGTERM, lambda *_: lifetime.request_stop())
httpd.serve_forever(poll_interval=.02)
httpd.server_close()
(root / 'child.stopped').write_text('clean')
'''

HARNESS = '''
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('launcher', sys.argv[1])
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
module.ROOT = Path(sys.argv[2]); module.URL = 'http://127.0.0.1:' + sys.argv[3]
module.run_app(sys.stdin.buffer, command=[sys.executable, '-u', '-c', sys.argv[4],
               sys.argv[2], sys.argv[3], sys.argv[5]], startup_timeout=.8, stop_timeout=3)
'''


class DesktopTests(unittest.TestCase):
    def context(self):
        orch = SimpleNamespace(_start_lock=threading.RLock(), _cancel=threading.Event(),
                               _thread=None, _stills_thread=None, stop=Mock())
        return SimpleNamespace(orch=orch, film=SimpleNamespace(stop=Mock()))

    def test_eof_stops_server_once_and_closes_admission(self):
        ctx = self.context()
        server = SimpleNamespace(shutdown=Mock())
        lifecycle = DesktopLifetime(ctx, server)
        lifecycle.watch(io.BytesIO(b""))
        self.assertTrue(lifecycle.stopping.wait(1))
        for _ in range(100):
            if server.shutdown.called: break
            time.sleep(.001)
        lifecycle.request_stop()
        server.shutdown.assert_called_once()
        self.assertTrue(ctx.orch._closing)
        self.assertTrue(ctx.orch._cancel.is_set())

    def test_shutdown_cancels_and_joins_both_workers(self):
        ctx = self.context()
        finished = []
        def worker():
            ctx.orch._cancel.wait(2)
            finished.append(True)
        workers = [threading.Thread(target=worker) for _ in range(2)]
        ctx.orch._thread, ctx.orch._stills_thread = workers
        for t in workers: t.start()
        self.assertTrue(stop_owned_work(ctx, timeout=1))
        self.assertEqual(finished, [True, True])
        ctx.film.stop.assert_called_once()
        ctx.orch.stop.assert_called_once()
        self.assertFalse(any(t.is_alive() for t in workers))

    def test_shutdown_timeout_is_bounded_without_killing_worker(self):
        ctx, release = self.context(), threading.Event()
        worker = threading.Thread(target=release.wait)
        ctx.orch._thread = worker
        worker.start()
        try:
            start = time.monotonic()
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(stop_owned_work(ctx, timeout=.02))
            self.assertLess(time.monotonic() - start, .5)
            self.assertTrue(worker.is_alive())
        finally:
            release.set(); worker.join()

    def test_shutdown_timeout_includes_blocked_cancel(self):
        ctx, release = self.context(), threading.Event()
        ctx.orch.stop.side_effect = lambda: release.wait(2)
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                start = time.monotonic()
                self.assertFalse(stop_owned_work(ctx, timeout=.02))
            self.assertLess(time.monotonic() - start, .5)
        finally:
            release.set()

    def test_closing_blocks_language_gpu_and_film_starts(self):
        ctx = self.context()
        ctx.orch._closing = True
        inner = Mock()
        with self.assertRaisesRegex(RuntimeError, 'avsluttes'):
            GuardedLLM(inner, ctx.orch).complete('', '')
        inner.complete.assert_not_called()
        class GPU:
            _start_lock = ctx.orch._start_lock
            _closing = True
            @_gpu_start
            def start(self):
                raise AssertionError('must not start')
        with self.assertRaisesRegex(RuntimeError, 'avsluttes'):
            GPU().start()
        with tempfile.TemporaryDirectory() as temp:
            ctx.data_dir = Path(temp)
            ctx.ui_root = ROOT
            film = FilmController(ctx)
            with self.assertRaisesRegex(RuntimeError, 'avsluttes'):
                film.start({'description': 'Inert film idea', 'seconds': 6})

    def test_cli_launch_reuses_existing_server_without_ownership(self):
        with patch.object(launcher, 'ready', return_value=True), \
             patch.object(launcher.subprocess, 'Popen') as popen, \
             patch.object(sys, 'argv', ['launch.py']), contextlib.redirect_stdout(io.StringIO()):
            launcher.main()
        popen.assert_not_called()

    def test_app_refuses_existing_listener_without_touching_it(self):
        with tempfile.TemporaryDirectory() as temp, socket.socket() as listener:
            root = Path(temp); (root / 'local').mkdir()
            listener.bind(('127.0.0.1', 0)); listener.listen()
            url = f'http://127.0.0.1:{listener.getsockname()[1]}'
            with patch.object(launcher, 'ROOT', root), patch.object(launcher, 'URL', url), \
                 patch.object(launcher.subprocess, 'Popen') as popen:
                with self.assertRaisesRegex(RuntimeError, 'allerede i bruk'):
                    launcher.run_app(io.BytesIO(b''))
            popen.assert_not_called()
            self.assertGreaterEqual(listener.fileno(), 0)

    @contextlib.contextmanager
    def app_process(self, mode='normal'):
        with tempfile.TemporaryDirectory() as temp, socket.socket() as probe:
            root = Path(temp); (root / 'local').mkdir()
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]; probe.close()
            process = subprocess.Popen([sys.executable, '-u', '-c', HARNESS,
                str(ROOT / 'local/launch.py'), str(root), str(port), CHILD, mode],
                cwd=ROOT, env={**os.environ, 'PYTHONPATH': str(ROOT)},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                yield process, root, port
            finally:
                if process.stdin and not process.stdin.closed: process.stdin.close()
                if process.poll() is None:
                    process.wait(timeout=5)
                process.stdout.close(); process.stderr.close()

    def wait_ready(self, process):
        readable, _, _ = select.select([process.stdout], [], [], 5)
        self.assertTrue(readable, 'launcher did not return readiness')
        self.assertTrue(process.stdout.readline().startswith(b'http://127.0.0.1:'))

    def wait_stopped(self, root):
        deadline = time.monotonic() + 5
        while not (root / 'child.stopped').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((root / 'child.stopped').exists(), 'owned child did not cleanly stop')

    def test_app_pipe_close_reaps_owned_child_and_pidfile(self):
        with self.app_process() as (process, root, port):
            self.wait_ready(process)
            self.assertTrue((root / 'server.pid').exists())
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0, process.stderr.read().decode())
            self.wait_stopped(root)
            self.assertFalse((root / 'server.pid').exists())
            with socket.socket() as probe:
                self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)

    def test_killed_launcher_closes_child_lifeline(self):
        with self.app_process() as (process, root, _):
            self.wait_ready(process)
            process.kill(); process.wait(timeout=5)
            self.wait_stopped(root)

    def test_early_app_close_does_not_leave_server(self):
        with self.app_process('not-ready') as (process, root, _):
            deadline = time.monotonic() + 3
            while not (root / 'child.pid').exists() and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertTrue((root / 'child.pid').exists())
            process.stdin.close()
            self.assertEqual(process.wait(timeout=5), 0)
            self.wait_stopped(root)

    def test_wrong_readiness_owner_is_rejected_and_own_child_reaped(self):
        with self.app_process('wrong-owner') as (process, root, _):
            self.assertNotEqual(process.wait(timeout=5), 0)
            self.assertIn('annen server', process.stderr.read().decode())
            self.wait_stopped(root)

    def test_startup_timeout_cleans_up_child(self):
        with self.app_process('not-ready') as (process, root, _):
            self.assertNotEqual(process.wait(timeout=5), 0)
            self.assertIn('ikke klar', process.stderr.read().decode())
            self.wait_stopped(root)
