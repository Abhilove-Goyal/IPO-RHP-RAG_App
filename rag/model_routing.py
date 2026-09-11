"""Deterministic model routing for generation requests."""

from dataclasses import dataclass

from langchain_openai import ChatOpenAI

from core.settings import settings
import time


@dataclass
class ModelCallResult:
    text: str | None
    status: str
    model: str | None
    error: Exception | None = None


def _status_for_error(error: Exception) -> str:
    status_code = getattr(error, "status_code", None)
    message = str(error).lower()
    if status_code == 413 or "413" in message or "rate limit" in message or "tpm" in message:
        return "RATE_LIMIT"
    if isinstance(error, TimeoutError) or "timeout" in message or "timed out" in message:
        return "TIMEOUT"
    return "MODEL_ERROR"


def invoke_with_fallback(prompt: str) -> ModelCallResult:
    """Invoke primary model with retries, then fallback model with retries.
    Settings used:
      - llm_max_retries: number of attempts per model
      - llm_initial_backoff: base seconds for exponential back‑off
      - rag_fallback_model: optional secondary model (e.g., "gpt-3.5-turbo")
    """
    # Build model list: primary Groq model, optional fallback, then generic llm_model if distinct
    models = [settings.groq_model]
    if settings.rag_fallback_model and settings.rag_fallback_model != settings.groq_model:
        models.append(settings.rag_fallback_model)
    if settings.llm_model != settings.groq_model and settings.llm_model not in models:
        models.append(settings.llm_model)

    last_error = None
    last_status = "MODEL_ERROR"
    for model in models:
        for attempt in range(1, settings.llm_max_retries + 1):
            try:
                llm = ChatOpenAI(
                    model=model,
                    api_key=settings.groq_api_key,
                    base_url="https://api.groq.com/openai/v1",
                    temperature=0,
                )
                content = llm.invoke(prompt).content
                if not isinstance(content, str) or not content.strip():
                    raise ValueError("Model returned an empty answer")
                return ModelCallResult(content.strip(), "SUCCESS", model)
            except Exception as error:
                last_error = error
                last_status = _status_for_error(error)
                print(f"[MODEL ROUTING] {model} attempt {attempt}/{settings.llm_max_retries} failed ({last_status}): {error}")
                if attempt < settings.llm_max_retries:
                    backoff = settings.llm_initial_backoff * (2 ** (attempt - 1))
                    time.sleep(backoff)
        # Exhausted retries for this model, move to next one
    return ModelCallResult(None, last_status, models[-1] if models else None, last_error)