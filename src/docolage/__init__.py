"""docolage — self-hosted document parsing on Databricks GPU compute.

Public interface: two entry points over one shared implementation
(ADR-0004).
"""

from docolage.config import DocolageConfig
from docolage.parser import parse

__all__ = ["parse", "DocolageConfig"]
