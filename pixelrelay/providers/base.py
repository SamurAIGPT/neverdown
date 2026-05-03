from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Optional


@dataclass
class GenerationResult:
    image_url: str
    provider: str
    model: str
    latency_ms: float


@dataclass
class SubmitResult:
    """Returned by submit_async — the provider has accepted the job and will POST to our webhook."""
    provider_job_id: str
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CallbackPayload:
    """Normalized form of a provider's webhook POST body."""
    provider_job_id: str
    status: Literal["succeeded", "failed"]
    image_url: Optional[str] = None
    error: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)


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
        """Submit job and poll until complete. Used by SDK library mode."""
        pass

    async def submit_async(
        self,
        prompt: str,
        model: str,
        webhook_url: str,
        **kwargs,
    ) -> "SubmitResult":
        """Submit a job that will POST to webhook_url when complete. Used by the gateway.

        Default implementation raises — providers without webhook support must override
        with a polling shim or be marked as polling-only in the registry.
        """
        raise NotImplementedError(
            f"Provider '{self.name}' does not support async webhook submission"
        )

    @staticmethod
    def parse_callback(headers: Dict[str, str], body: bytes) -> "CallbackPayload":
        """Parse a provider webhook POST into a normalized CallbackPayload.

        Subclasses override. The default raises so missing implementations are loud.
        """
        raise NotImplementedError("Provider must implement parse_callback")
