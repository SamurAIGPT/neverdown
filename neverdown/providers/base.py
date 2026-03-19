from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class GenerationResult:
    image_url: str
    provider: str
    model: str
    latency_ms: float


class BaseProvider(ABC):
    name: str

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        model: str,
        timeout: float,
        **kwargs,
    ) -> GenerationResult:
        """Submit job and poll until complete. Raises ProviderError on failure."""
        pass
