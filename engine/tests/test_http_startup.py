"""Local engine startup must not depend on reverse DNS being available."""
import http.client
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from server.app import build_server


class HTTPStartupTests(unittest.TestCase):
    def test_loopback_bind_and_real_desktop_request_do_not_resolve_hostnames(self):
        ctx = SimpleNamespace(ui_root=Path.cwd(), desktop_owned=True)
        with patch("socket.getfqdn", side_effect=AssertionError("reverse DNS must not block local startup")):
            server = build_server("127.0.0.1", 0, ctx)
            try:
                self.assertEqual(server.server_name, "localhost")
                self.assertEqual(server.server_port, server.server_address[1])
                worker = threading.Thread(target=server.handle_request, daemon=True)
                worker.start()
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=2)
                try:
                    connection.request("GET", "/api/desktop")
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    self.assertTrue(json.loads(response.read())["desktopOwned"])
                finally:
                    connection.close()
                worker.join(2)
                self.assertFalse(worker.is_alive())
            finally:
                server.server_close()
