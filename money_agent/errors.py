"""Turn errors from the AI service into plain advice, for both the terminal and the web app."""
from __future__ import annotations

import openai

from . import config


def _detail(body) -> str:
    """Pull the human-readable message out of an error body (Gemini wraps it in a list)."""
    if isinstance(body, list) and body:
        body = body[0]
    if isinstance(body, dict):
        err = body.get("error", body)
        if isinstance(err, dict):
            return str(err.get("message", ""))
    return ""


def friendly_error(exc: Exception) -> str | None:
    """A short explanation of what went wrong and what to do, or None if it isn't an AI-service error."""
    key_name = config.KEY_ENV or "MONEY_AGENT_API_KEY"
    if isinstance(exc, openai.APITimeoutError):
        return "The AI service took too long to answer. Please try again."
    if isinstance(exc, openai.AuthenticationError):
        return f"The API key was rejected. Check {key_name} in the .env file."
    if isinstance(exc, openai.PermissionDeniedError):
        return "This API key isn't allowed to use that model."
    if isinstance(exc, openai.NotFoundError):
        hint = f" Run: ollama pull {config.MODEL}" if config.PROVIDER == "ollama" else ""
        return f"Model '{config.MODEL}' wasn't found. Set MONEY_AGENT_MODEL to a model you can use.{hint}"
    if isinstance(exc, openai.RateLimitError):
        return "The free usage limit was reached for now. Wait a minute (or until tomorrow) and try again."
    if isinstance(exc, openai.BadRequestError):
        detail = _detail(exc.body) or exc.message
        if "api key" in detail.lower():  # Gemini reports a bad key as 400, not 401
            return f"The API key was rejected. Check {key_name} in the .env file."
        return f"The AI service didn't accept the request: {detail}"
    if isinstance(exc, openai.APIStatusError):
        return f"The AI service returned an error ({exc.status_code}). Please try again."
    if isinstance(exc, openai.APIConnectionError):
        if config.PROVIDER == "ollama":
            return "Couldn't reach Ollama. Make sure the Ollama app is running."
        return "Couldn't reach the AI service. Check your internet connection."
    return None


def missing_key_message() -> str:
    return (f"No API key found. Get a free Gemini key at https://aistudio.google.com/apikey, then paste it "
            f"into the file {config.PROJECT_ROOT / '.env'} after {config.KEY_ENV}= and save.")
