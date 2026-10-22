"""The facade jobs call: picks each job's provider and model from ``edda.toml`` and keeps the token ledger."""

from collections.abc import Sequence
from typing import Protocol

import structlog

from edda.config import EddaConfig
from edda.llm.types import CallRecord, ChatResult, Job, Provider, Tool, Turn, Usage

log = structlog.get_logger("edda.llm")


class ChatProvider(Protocol):
    """What a provider implements: one chat call."""

    def chat(
        self, *, model: str, system: str, turns: Sequence[Turn], tools: Sequence[Tool], max_tokens: int
    ) -> ChatResult:
        """Sends the conversation and returns the model's next turn."""
        ...


class LLM:
    """Model calls for jobs.

    Providers are created on first use, so a vault that never selects Anthropic never needs a key; ``providers``
    injects them (tests pass recorded ones).
    """

    def __init__(self, config: EddaConfig, providers: dict[Provider, ChatProvider] | None = None) -> None:
        """Uses the ``[llm]`` and ``[budgets]`` tables of ``config``."""
        self.config = config
        self._providers: dict[Provider, ChatProvider] = dict(providers or {})
        self.ledger: list[CallRecord] = []

    def provider_for(self, job: Job) -> tuple[Provider, str]:
        """The provider and model a job uses."""
        llm = self.config.llm
        provider: Provider = getattr(llm, job)
        model = getattr(llm.anthropic, job) if provider == "anthropic" else llm.endpoints[provider].model
        return provider, model

    def chat(
        self, job: Job, job_id: str, *, system: str, turns: Sequence[Turn], tools: Sequence[Tool] = ()
    ) -> ChatResult:
        """One model call for ``job``, recorded in the ledger and logged.

        Raises:
            ProviderNotConfigured: The job's provider cannot be used, such as Anthropic without credentials.
            LLMError: The call failed after its retries.
        """
        name, model = self.provider_for(job)
        result = self._provider(name).chat(
            model=model, system=system, turns=turns, tools=tools, max_tokens=self.config.budgets.max_output_tokens
        )
        record = CallRecord(
            job=job, job_id=job_id, provider=name, model=result.model, stop=result.stop, usage=result.usage
        )
        self.ledger.append(record)
        log.info(
            "llm_call",
            job=job,
            job_id=job_id,
            provider=name,
            model=result.model,
            stop=result.stop,
            tool_calls=len(result.turn.tool_calls),
            **result.usage.model_dump(),
        )
        return result

    def usage(self, job_id: str) -> Usage:
        """Total tokens a job used across its calls."""
        return sum((r.usage for r in self.ledger if r.job_id == job_id), Usage())

    def _provider(self, name: Provider) -> ChatProvider:
        if name not in self._providers:
            if name == "anthropic":
                from edda.llm.claude import ClaudeProvider

                self._providers[name] = ClaudeProvider(self.config.llm.anthropic)
            else:
                from edda.llm.openai_compat import OpenAICompatibleProvider

                self._providers[name] = OpenAICompatibleProvider(name, self.config.llm.endpoints[name])
        return self._providers[name]
