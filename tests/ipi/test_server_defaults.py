import inspect
import unittest

from q_ai.ipi.cli import listen
from q_ai.ipi.server import start_server


class TestServerDefaults(unittest.TestCase):
    def test_start_server_defaults(self):
        """Verify that start_server defaults to 127.0.0.1."""
        sig = inspect.signature(start_server)
        params = sig.parameters
        self.assertEqual(params["host"].default, "127.0.0.1")
        self.assertEqual(params["port"].default, 8080)

    def test_cli_listen_defaults(self):
        """Verify that CLI listen command defaults to 127.0.0.1."""
        sig = inspect.signature(listen)
        params = sig.parameters
        self.assertEqual(params["host"].default, "127.0.0.1")
        self.assertEqual(params["port"].default, 8080)


if __name__ == "__main__":
    unittest.main()
