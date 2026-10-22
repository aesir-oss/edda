"""Jobs: loops of model calls whose tools are the kernel's system calls, so all they do arrives as proposals."""

from edda.jobs.ingest import ingest
from edda.jobs.runner import JobResult, ToolSpec, run_job

__all__ = ["JobResult", "ToolSpec", "ingest", "run_job"]
