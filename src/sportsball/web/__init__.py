"""Web dashboard for the sportsball pipeline.

A small FastAPI app (``app.create_app``) serving a self-contained HTML page plus a
JSON ``/api/snapshot`` endpoint. The data behind it comes from a pluggable
:class:`~sportsball.web.providers.DataProvider` so the same UI renders against the
live Postgres store, a DuckDB research file, or fully in-memory demo data (the
default when no infrastructure is reachable).
"""
