"""Pytest configuration.

Puts the Analytics Engine source directory on the import path so the unit
tests can import the math/arbitrage modules directly without a running
container or a configured PYTHONPATH.
"""
import os
import sys

ENGINE_DIR = os.path.join(os.path.dirname(__file__), os.pardir, "src", "analytics_engine")
sys.path.insert(0, os.path.abspath(ENGINE_DIR))
