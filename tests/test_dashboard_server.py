"""The dashboard must not wait on reverse DNS before it can serve (#92)."""
import socket
import socketserver
import sys
import unittest
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

from dashboard import agent_console


def forbidden(*args, **kwargs):
    raise AssertionError("reverse-DNS lookup while binding a loopback server")


class Stop(Exception):
    pass


class DashboardServerBindTests(unittest.TestCase):
    def test_source_package_initializes_continuous_conversation_store(self):
        # This namespace is used by repository consumers, independently of
        # direct dashboard-script and installed autocode_cli launches.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            console = agent_console.Console([root], root / "unused-runner.py", lambda: None,
                                             conversation_root=root / "conversations",
                                             conversation_provider=lambda *_: "offline",
                                             conversation_planner=lambda *_: "offline")
            try:
                self.assertEqual([], console.conversations.list())
                self.assertIs(console.conversations.new, console.conversations.continuous)
            finally:
                console.conversations.close()
                console.pool.shutdown(wait=True)

    def test_loopback_server_binds_without_reverse_dns(self):
        with patch.object(socket, "getfqdn", forbidden):
            server = agent_console.LoopbackHTTPServer(("127.0.0.1", 0), agent_console.Handler)
        self.addCleanup(server.server_close)
        self.assertEqual("127.0.0.1", server.server_name)
        self.assertGreater(server.server_port, 0)
        self.assertEqual(server.server_address[1], server.server_port)

    def test_dashboard_main_binds_without_reverse_dns(self):
        started = []

        def stop(server):
            started.append(server)
            raise Stop

        registry = MagicMock()
        registry.default_name.return_value = "fake"
        with patch.object(socket, "getfqdn", forbidden), \
                patch.object(sys, "argv", ["agent_console", "--port", "0"]), \
                patch.object(agent_console, "provider_registry", return_value=registry), \
                patch.object(agent_console, "Console"), \
                patch.object(socketserver.BaseServer, "serve_forever", autospec=True, side_effect=stop), \
                patch("builtins.print"):
            with self.assertRaises(Stop):
                agent_console.main()
        self.assertEqual(1, len(started))
        server = started[0]
        self.addCleanup(server.server_close)
        port = server.server_port
        self.assertEqual({f"127.0.0.1:{port}", f"localhost:{port}"}, server.hosts)


if __name__ == "__main__":
    unittest.main()
