"""Pytest setup: force mock mode so tests never call real APIs."""

import os

os.environ["MOCK_MODE"] = "1"
