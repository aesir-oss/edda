"""Errors from the model layer."""


class LLMError(RuntimeError):
    """A model call failed after its retries: unreachable endpoint, bad response, or an error from the provider."""


class ProviderNotConfigured(LLMError):
    """A job is set to a provider that cannot be used here, such as Anthropic without the user's own credentials."""
