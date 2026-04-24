"""Abstract base class for all discoverers."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import MediaTarget, ResearchBrief
    from ..cache import EnrichmentCache
    from ..config import Config

logger = logging.getLogger(__name__)


class BaseDiscoverer(ABC):
    """All discoverers share the same interface."""

    SOURCE_NAME: str = "unknown"

    def __init__(self, config: "Config", cache: "EnrichmentCache") -> None:
        self.config = config
        self.cache = cache

    @abstractmethod
    async def discover(self, brief: "ResearchBrief") -> list["MediaTarget"]:
        """Return a list of candidate MediaTarget objects (unenriched)."""
        ...

    def _log_degraded(self, reason: str) -> None:
        logger.warning("[%s] degraded: %s", self.SOURCE_NAME, reason)
