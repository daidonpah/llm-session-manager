"""Central definitions of every Redis key/channel used by the app.

Keeping these in one place avoids typos and makes the data model auditable.
"""

from __future__ import annotations

# Sorted set of all known job ids, scored by creation timestamp (for listing).
JOBS_INDEX = "lsm:jobs:index"


def job_key(job_id: str) -> str:
    """Hash holding the serialized :class:`~lsm.models.JobRecord`."""
    return f"lsm:job:{job_id}"


def job_tokens_key(job_id: str) -> str:
    """List of streamed text deltas, in order, used for replay on reconnect."""
    return f"lsm:job:{job_id}:tokens"


def job_events_channel(job_id: str) -> str:
    """Pub/sub channel carrying live stream events for a job."""
    return f"lsm:job:{job_id}:events"
