"""Tests for server startup configuration."""

import os
import unittest
from unittest.mock import patch

from server import app


class ServerStartupTests(unittest.TestCase):
    @patch("uvicorn.run")
    @patch("copilot.auth.load_auth")
    def test_uses_default_address(self, _load_auth, run):
        with patch.dict(os.environ, {}, clear=True):
            app()

        self.assertEqual(run.call_args.kwargs["host"], "127.0.0.1")
        self.assertEqual(run.call_args.kwargs["port"], 8000)

    @patch("uvicorn.run")
    @patch("copilot.auth.load_auth")
    def test_uses_address_from_environment(self, _load_auth, run):
        with patch.dict(os.environ, {"HOST": "0.0.0.0", "PORT": "8080"}, clear=True):
            app()

        self.assertEqual(run.call_args.kwargs["host"], "0.0.0.0")
        self.assertEqual(run.call_args.kwargs["port"], 8080)

    @patch("uvicorn.run")
    @patch("copilot.auth.load_auth")
    def test_explicit_address_takes_precedence(self, _load_auth, run):
        with patch.dict(os.environ, {"HOST": "0.0.0.0", "PORT": "8080"}, clear=True):
            app(host="localhost", port=0)

        self.assertEqual(run.call_args.kwargs["host"], "localhost")
        self.assertEqual(run.call_args.kwargs["port"], 0)


if __name__ == "__main__":
    unittest.main()
