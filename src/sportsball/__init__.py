"""Sportsball: distributed-agent sports-market analytics and paper-trading pipeline.

The package is organized into four layers:

* ``sportsball.config``  — typed runtime configuration (env + settings.json)
* ``sportsball.db`` / ``sportsball.broker`` / ``sportsball.logging_conf`` — infrastructure
* ``sportsball.quant``   — pure, dependency-light quantitative primitives
* ``sportsball.agents``  — the long-running micro-agents that compose the pipeline

Everything below ``quant`` is import-safe without a database or broker, which is
what lets the unit suite exercise the math without any infrastructure.
"""

__version__ = "0.2.0"
