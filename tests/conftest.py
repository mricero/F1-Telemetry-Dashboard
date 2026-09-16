"""Shared test fixtures.

The live ingest adapter is a process-wide singleton (LIVE-09), so without a
reset between tests one test's primed buffers would leak into the next.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolated_live_service():
    from data.live_service import reset_live_service

    reset_live_service()
    yield
    reset_live_service()
