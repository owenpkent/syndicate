"""Pytest configuration.

Puts the package ``src/`` directory on the import path so the suite runs
whether or not ``sportsball`` has been ``pip install``-ed (editable). This keeps
``pytest`` working straight from a fresh checkout.
"""
import os
import sys

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "src"))
if SRC not in sys.path:
    sys.path.insert(0, SRC)
