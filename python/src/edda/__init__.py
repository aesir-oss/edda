"""Edda, a Knowledge OS: an LLM-maintained wiki of linked, cited Markdown pages."""

from edda._kernel import okf_version
from edda.kernel import Kernel

__all__ = ["Kernel", "okf_version"]
