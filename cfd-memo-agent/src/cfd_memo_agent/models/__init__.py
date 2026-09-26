"""Provider-neutral structured model calls for Stage D agents."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import Draft202012Validator
from dotenv import load_dotenv

PROJECT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEEPSEEK_RESPONSES_URL = "https://api.deepseek.com/responses"
PROVIDERS = ("rules", "openai", "deepseek")
PROVIDER_NAMES = {"openai": "OpenAI", "deepseek": "DeepSeek"}
PROVIDER_URLS = {
    "openai": OPENAI_RESPONSES_URL,
    "deepseek": DEEPSEEK_RESPONSES_URL,
}
PROVIDER_KEY_ENVS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}
PROVIDER_URL_ENVS = {
    "openai": "CFD_MEMO_OPENAI_BASE_URL",
    "deepseek": "CFD_MEMO_DEEPSEEK_BASE_URL",
}


class ModelError(RuntimeError):
    """Base error for model configuration, transport, and output failures."""


class ModelConfigurationError(ModelError):
    pass


class ModelInvocationError(ModelError):
    pass


class ModelOutputError(ModelError):
    pass


@dataclass(frozen=True)
class ModelSettings:
    provider: str = "rules"
    model: str | None = None
    timeout: float = 60.0
    max_output_tokens: int = 2000
    api_key_env: str | None = None
    base_url: str | None = None

    @property
    def resolved_api_key_env(self) -> str:
        return self.api_key_env or PROVIDER_KEY_ENVS.get(self.provider, "OPENAI_API_KEY")

    @property
    def resolved_base_url(self) -> str:
        return self.base_url or PROVIDER_URLS.get(self.provider, OPENAI_RESPONSES_URL)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "ModelSettings":
        if environ is None:
            load_dotenv(PROJECT_ENV_PATH, override=False, encoding="utf-8")
            values = os.environ
        else:
            values = environ
        provider = values.get("CFD_MEMO_MODEL_PROVIDER", "rules").strip().lower()
        model = values.get("CFD_MEMO_MODEL", "").strip() or None
        key_env = PROVIDER_KEY_ENVS.get(provider, "OPENAI_API_KEY")
        default_url = PROVIDER_URLS.get(provider, OPENAI_RESPONSES_URL)
        url_env = PROVIDER_URL_ENVS.get(provider, "CFD_MEMO_OPENAI_BASE_URL")
        base_url = values.get(url_env, default_url).strip()
        try:
            timeout = float(values.get("CFD_MEMO_MODEL_TIMEOUT", "60"))
            max_tokens = int(values.get("CFD_MEMO_MODEL_MAX_OUTPUT_TOKENS", "2000"))
        except ValueError as exc:
            raise ModelConfigurationError("模型超时和输出 token 上限必须是数字") from exc
        settings = cls(provider=provider, model=model, timeout=timeout,
                       max_output_tokens=max_tokens, api_key_env=key_env, base_url=base_url)
        settings.validate(require_credentials=False, environ=values)
        return settings

    def validate(self, *, require_credentials: bool,
                 environ: Mapping[str, str] | None = None) -> None:
        values = os.environ if environ is None else environ
        if self.provider not in PROVIDERS:
            raise ModelConfigurationError(f"不支持的模型 provider：{self.provider}")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ModelConfigurationError("模型超时必须为有限正数")
        if isinstance(self.max_output_tokens, bool) or self.max_output_tokens <= 0:
            raise ModelConfigurationError("模型输出 token 上限必须为正整数")
        if self.provider in PROVIDER_NAMES and not self.model:
            name = PROVIDER_NAMES[self.provider]
            raise ModelConfigurationError(f"{name} 模式需要设置 CFD_MEMO_MODEL")
        if self.provider in PROVIDER_NAMES:
            expected_url = PROVIDER_URLS[self.provider]
            if self.resolved_base_url != expected_url:
                name = PROVIDER_NAMES[self.provider]
                raise ModelConfigurationError(f"仅允许官方 {name} Responses API 地址")
        key_env = self.resolved_api_key_env
        if require_credentials and self.provider in PROVIDER_NAMES and not values.get(key_env):
            name = PROVIDER_NAMES[self.provider]
            raise ModelConfigurationError(f"{name} 模式需要环境变量 {key_env}")

    def public_status(self, environ: Mapping[str, str] | None = None) -> dict[str, Any]:
        values = os.environ if environ is None else environ
        key_env = self.resolved_api_key_env
        key_configured = bool(values.get(key_env))
        ready = self.provider == "rules" or (self.model is not None and key_configured)
        return {
            "provider": self.provider,
            "model": self.model,
            "mode": "rules" if self.provider == "rules" else "llm",
            "timeout_seconds": self.timeout,
            "max_output_tokens": self.max_output_tokens,
            "api_key_env": key_env,
            "api_key_configured": key_configured,
            "ready": ready,
        }


@dataclass(frozen=True)
class ModelRequest:
    role: str
    instructions: str
    input_text: str
    output_schema: dict[str, Any]
    schema_name: str
    prompt_version: str


@dataclass(frozen=True)
class ModelResult:
    output: dict[str, Any]
    trace: dict[str, Any]


class ModelClient(Protocol):
    def complete_json(self, request: ModelRequest) -> ModelResult: ...


def _validate_output(value: Any, schema: dict[str, Any]) -> dict[str, Any]:
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(value),
                    key=lambda item: str(list(item.absolute_path)))
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path) or "$"
        raise ModelOutputError(f"模型输出不符合 schema：{location}: {first.message}")
    if not isinstance(value, dict):
        raise ModelOutputError("模型结构化输出必须是 JSON object")
    return value


class FakeModelClient:
    """Deterministic queued outputs for tests; never performs network I/O."""

    def __init__(self, outputs: list[Any]):
        self._outputs = deque(outputs)
        self.requests: list[ModelRequest] = []

    def complete_json(self, request: ModelRequest) -> ModelResult:
        self.requests.append(request)
        if not self._outputs:
            raise ModelInvocationError("假模型没有剩余输出")
        value = self._outputs.popleft()
        if isinstance(value, Exception):
            raise value
        output = _validate_output(value, request.output_schema)
        return ModelResult(output=output, trace={
            "provider": "fake", "model": "deterministic-test-double",
            "role": request.role, "prompt_version": request.prompt_version,
            "response_id": None, "status": "completed", "elapsed_seconds": 0.0,
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        })


class OpenAIResponsesClient:
    """Responses API adapter shared by allowlisted compatible providers."""

    def __init__(self, settings: ModelSettings, *, environ: Mapping[str, str] | None = None,
                 opener: Callable[..., Any] = urlopen):
        settings.validate(require_credentials=True, environ=environ)
        self.settings = settings
        self._environ = os.environ if environ is None else environ
        self._opener = opener

    def complete_json(self, request: ModelRequest) -> ModelResult:
        output_format = {
            "type": "json_schema", "name": request.schema_name,
            "schema": request.output_schema,
        }
        payload = {
            "model": self.settings.model,
            "instructions": request.instructions,
            "input": request.input_text,
            "max_output_tokens": self.settings.max_output_tokens,
            "text": {"format": output_format},
        }
        if self.settings.provider == "openai":
            payload["store"] = False
            output_format["strict"] = True
        elif self.settings.provider == "deepseek":
            payload["reasoning"] = {"effort": "none"}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        key_env = self.settings.resolved_api_key_env
        api_request = Request(
            self.settings.resolved_base_url, data=encoded, method="POST",
            headers={"Authorization": f"Bearer {self._environ[key_env]}",
                     "Content-Type": "application/json"},
        )
        provider_name = PROVIDER_NAMES[self.settings.provider]
        started = time.monotonic()
        try:
            with self._opener(api_request, timeout=self.settings.timeout) as response:
                body = response.read()
            document = json.loads(body.decode("utf-8"))
        except HTTPError as exc:
            raise ModelInvocationError(f"{provider_name} API 返回 HTTP {exc.code}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ModelInvocationError(f"{provider_name} API 调用失败：{exc}") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ModelInvocationError(f"{provider_name} API 返回了无法解析的响应") from exc
        elapsed = time.monotonic() - started
        if document.get("status") != "completed":
            raise ModelInvocationError(f"模型响应未完成：{document.get('status', 'unknown')}")
        text = None
        for item in document.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "refusal":
                    raise ModelOutputError("模型拒绝生成结构化输出")
                if content.get("type") == "output_text":
                    text = content.get("text")
                    break
        if not isinstance(text, str):
            raise ModelOutputError("模型响应缺少 output_text")
        try:
            output = _validate_output(json.loads(text), request.output_schema)
        except json.JSONDecodeError as exc:
            raise ModelOutputError("模型 output_text 不是有效 JSON") from exc
        usage = document.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        output_details = usage.get("output_tokens_details") or {}
        return ModelResult(output=output, trace={
            "provider": self.settings.provider,
            "model": document.get("model", self.settings.model),
            "role": request.role, "prompt_version": request.prompt_version,
            "response_id": document.get("id"), "status": document.get("status"),
            "elapsed_seconds": elapsed,
            "usage": {
                **{key: usage.get(key) for key in
                   ("input_tokens", "output_tokens", "total_tokens")},
                "cached_input_tokens": input_details.get("cached_tokens"),
                "reasoning_tokens": output_details.get("reasoning_tokens"),
            },
        })


class DeepSeekResponsesClient(OpenAIResponsesClient):
    """DeepSeek's native OpenAI-compatible Responses API adapter."""


def create_model_client(settings: ModelSettings | None = None,
                        *, environ: Mapping[str, str] | None = None) -> ModelClient:
    selected = settings or ModelSettings.from_env(environ)
    if selected.provider == "rules":
        raise ModelConfigurationError("规则模式不调用 LLM；请继续使用现有确定性 planner")
    if selected.provider == "deepseek":
        return DeepSeekResponsesClient(selected, environ=environ)
    return OpenAIResponsesClient(selected, environ=environ)


__all__ = [
    "FakeModelClient", "ModelClient", "ModelConfigurationError", "ModelError",
    "ModelInvocationError", "ModelOutputError", "ModelRequest", "ModelResult",
    "ModelSettings", "OpenAIResponsesClient", "DeepSeekResponsesClient",
    "create_model_client",
]
