from __future__ import annotations

from dataclasses import dataclass
import json
import time
from typing import Any

import requests


class ProviderQuotaError(RuntimeError):
    """The LLM provider rejected a request because quota or billing is unavailable."""


@dataclass(frozen=True)
class ToolCallResponse:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class CompletionResponse:
    content: str | None
    tool_calls: tuple[ToolCallResponse, ...] = ()


class LLMClient:
    """Minimal OpenAI-compatible chat client with no credential logging.

    Transient failures (read timeouts, connection errors, 5xx responses) are
    retried with exponential backoff; 4xx responses and quota rejections are
    never retried. The only configured provider is the OpenCode gateway via
    ``opencode_go_config``; StepFun support was removed entirely.
    """

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0, session: Any | None = None, max_retries: int = 2) -> None:
        if not api_key:
            raise ValueError("api_key is required")
        if not model:
            raise ValueError("model is required")
        self.api_key = api_key
        normalized_url = base_url.rstrip("/")
        for suffix in ("/chat/completions", "/models"):
            if normalized_url.endswith(suffix):
                normalized_url = normalized_url[: -len(suffix)]
                break
        self.base_url = normalized_url
        self.model = model
        self.timeout = timeout
        self.session = session or requests.Session()
        self.max_retries = max_retries

    def models(self) -> dict[str, Any]:
        response = self.session.get(
            f"{self.base_url}/models",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=self.timeout,
        )
        if response.status_code == 402:
            raise ProviderQuotaError("LLM provider quota or billing is unavailable")
        response.raise_for_status()
        return response.json()

    def complete(self, messages: list[dict[str, str]], *, temperature: float = 0.1, max_tokens: int = 6000, json_mode: bool = True) -> str:
        response = self.complete_response(messages, temperature=temperature, max_tokens=max_tokens, json_mode=json_mode)
        if not response.content:
            raise ValueError("LLM returned no text content")
        return response.content

    def _post_chat(self, request: dict[str, Any]) -> Any:
        """POST the chat completion with retry on transient failures.

        Retries read timeouts, connection errors, and 5xx responses with
        exponential backoff (2s, 4s, ...). 4xx responses and quota
        rejections surface immediately. Never logs credentials.
        """
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(min(2 ** attempt, 8))
            try:
                response = self.session.post(url, headers=headers, json=request, timeout=self.timeout)
            except (requests.Timeout, requests.ConnectionError) as error:
                last_error = error
                continue
            if response.status_code >= 500 and attempt < self.max_retries:
                last_error = requests.HTTPError(f"{response.status_code} Server Error", response=response)
                continue
            return response
        assert last_error is not None
        raise last_error

    def complete_response(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        response_format: dict[str, str] | None = None,
        temperature: float = 0.1,
        max_tokens: int = 1200,
        json_mode: bool = True,
    ) -> CompletionResponse:
        request: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is None and response_format is None and json_mode:
            response_format = {"type": "json_object"}
        if tools:
            request["tools"] = tools
        if response_format:
            request["response_format"] = response_format
        response = self._post_chat(request)
        if response.status_code == 402:
            raise ProviderQuotaError("LLM provider quota or billing is unavailable")
        response.raise_for_status()
        payload = response.json()
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as error:
            raise ValueError("LLM response did not contain a message") from error
        content = message.get("content")
        if content is not None and (not isinstance(content, str) or not content.strip()):
            content = None
        calls = []
        for raw_call in message.get("tool_calls", []) or []:
            function = raw_call.get("function", {})
            try:
                arguments = json.loads(function.get("arguments", "{}"))
            except json.JSONDecodeError as error:
                raise ValueError("LLM returned invalid tool arguments") from error
            if not isinstance(arguments, dict):
                raise ValueError("LLM tool arguments must be an object")
            calls.append(ToolCallResponse(str(raw_call.get("id", "")), str(function.get("name", "")), arguments))
        return CompletionResponse(content.strip() if isinstance(content, str) else None, tuple(calls))
