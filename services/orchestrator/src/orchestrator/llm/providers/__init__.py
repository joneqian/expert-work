"""Concrete :class:`~orchestrator.llm.router.LLMProvider` adapters — Stream E.11."""

from orchestrator.llm.providers.anthropic import (
    DEFAULT_MAX_TOKENS as DEFAULT_MAX_TOKENS,
)
from orchestrator.llm.providers.anthropic import (
    AnthropicClient as AnthropicClient,
)
from orchestrator.llm.providers.anthropic import (
    AnthropicProvider as AnthropicProvider,
)
from orchestrator.llm.providers.anthropic import (
    HTTPAnthropicClient as HTTPAnthropicClient,
)
from orchestrator.llm.providers.anthropic import (
    RecordingAnthropicClient as RecordingAnthropicClient,
)
from orchestrator.llm.providers.openai import (
    DEFAULT_CHAT_COMPLETIONS_PATH as DEFAULT_CHAT_COMPLETIONS_PATH,
)
from orchestrator.llm.providers.openai import (
    HTTPOpenAIClient as HTTPOpenAIClient,
)
from orchestrator.llm.providers.openai import (
    OpenAIClient as OpenAIClient,
)
from orchestrator.llm.providers.openai import (
    OpenAIProvider as OpenAIProvider,
)
from orchestrator.llm.providers.openai import (
    RecordingOpenAIClient as RecordingOpenAIClient,
)
from orchestrator.llm.providers.openai_compatible import (
    DEEPSEEK_BASE_URL as DEEPSEEK_BASE_URL,
)
from orchestrator.llm.providers.openai_compatible import (
    DOUBAO_BASE_URL as DOUBAO_BASE_URL,
)
from orchestrator.llm.providers.openai_compatible import (
    DOUBAO_CHAT_COMPLETIONS_PATH as DOUBAO_CHAT_COMPLETIONS_PATH,
)
from orchestrator.llm.providers.openai_compatible import (
    GLM_BASE_URL as GLM_BASE_URL,
)
from orchestrator.llm.providers.openai_compatible import (
    GLM_CHAT_COMPLETIONS_PATH as GLM_CHAT_COMPLETIONS_PATH,
)
from orchestrator.llm.providers.openai_compatible import (
    KIMI_BASE_URL as KIMI_BASE_URL,
)
from orchestrator.llm.providers.openai_compatible import (
    QWEN_BASE_URL as QWEN_BASE_URL,
)
from orchestrator.llm.providers.openai_compatible import (
    OpenAICompatibleProvider as OpenAICompatibleProvider,
)
from orchestrator.llm.providers.openai_compatible import (
    make_azure_client as make_azure_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_deepseek_client as make_deepseek_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_doubao_client as make_doubao_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_glm_client as make_glm_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_kimi_client as make_kimi_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_qwen_client as make_qwen_client,
)
from orchestrator.llm.providers.openai_compatible import (
    make_self_hosted_client as make_self_hosted_client,
)

__all__ = [
    "DEEPSEEK_BASE_URL",
    "DEFAULT_CHAT_COMPLETIONS_PATH",
    "DEFAULT_MAX_TOKENS",
    "DOUBAO_BASE_URL",
    "DOUBAO_CHAT_COMPLETIONS_PATH",
    "GLM_BASE_URL",
    "GLM_CHAT_COMPLETIONS_PATH",
    "KIMI_BASE_URL",
    "QWEN_BASE_URL",
    "AnthropicClient",
    "AnthropicProvider",
    "HTTPAnthropicClient",
    "HTTPOpenAIClient",
    "OpenAIClient",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "RecordingAnthropicClient",
    "RecordingOpenAIClient",
    "make_azure_client",
    "make_deepseek_client",
    "make_doubao_client",
    "make_glm_client",
    "make_kimi_client",
    "make_qwen_client",
    "make_self_hosted_client",
]
