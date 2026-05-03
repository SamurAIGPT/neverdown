class PixelrelayError(Exception):
    """Base exception for pixelrelay."""
    pass


class ProviderError(PixelrelayError):
    """Error from a specific provider."""
    def __init__(self, message: str, provider: str, status_code: int = None):
        self.provider = provider
        self.status_code = status_code
        super().__init__(f"[{provider}] {message}")


class ProviderUnavailableError(ProviderError):
    """Provider is down or unreachable."""
    pass


class JobFailedError(ProviderError):
    """Job was submitted but failed on provider side."""
    pass


class JobTimeoutError(ProviderError):
    """Job exceeded timeout while polling."""
    pass


class AllProvidersFailedError(PixelrelayError):
    """All providers in the list failed."""
    def __init__(self, errors: dict):
        self.errors = errors  # {provider: exception}
        summary = ", ".join(f"{p}: {e}" for p, e in errors.items())
        super().__init__(f"All providers failed — {summary}")
