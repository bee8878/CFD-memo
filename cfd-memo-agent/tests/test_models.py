import json

import pytest

from cfd_memo_agent import models
from cfd_memo_agent.models import (
    DeepSeekResponsesClient, FakeModelClient, ModelConfigurationError,
    ModelOutputError, ModelRequest, ModelSettings, OpenAIResponsesClient,
    create_model_client,
)

SCHEMA = {
    "type": "object",
    "properties": {"decision": {"type": "string", "enum": ["continue", "stop"]}},
    "required": ["decision"],
    "additionalProperties": False,
}


def request():
    return ModelRequest(role="planner", instructions="Return JSON.", input_text="cylinder flow",
                        output_schema=SCHEMA, schema_name="test_decision",
                        prompt_version="d1-test-v1")


def test_settings_default_to_rules_and_never_expose_key():
    settings = ModelSettings.from_env({"OPENAI_API_KEY": "secret-value"})
    status = settings.public_status({"OPENAI_API_KEY": "secret-value"})
    assert status["provider"] == "rules" and status["ready"]
    assert status["api_key_configured"] is True
    assert "secret-value" not in json.dumps(status)
    with pytest.raises(ModelConfigurationError, match="规则模式"):
        create_model_client(settings, environ={"OPENAI_API_KEY": "secret-value"})


def test_settings_load_fixed_project_dotenv_without_overriding_process(monkeypatch, tmp_path):
    calls = []

    def fake_load(path, *, override, encoding):
        calls.append((path, override, encoding))
        monkeypatch.setenv("CFD_MEMO_MODEL_PROVIDER", "deepseek")
        monkeypatch.setenv("CFD_MEMO_MODEL", "deepseek-flash")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "local-secret")
        return True

    monkeypatch.setattr(models, "load_dotenv", fake_load)
    settings = ModelSettings.from_env()

    assert calls == [(models.PROJECT_ENV_PATH, False, "utf-8")]
    assert settings.provider == "deepseek"
    assert settings.public_status()["ready"] is True
    assert "local-secret" not in json.dumps(settings.public_status())

    calls.clear()
    ModelSettings.from_env({})
    assert calls == []


def test_fake_model_validates_every_output():
    client = FakeModelClient([{"decision": "continue"}, {"decision": "invalid"}])
    assert client.complete_json(request()).output == {"decision": "continue"}
    assert client.requests[0].role == "planner"
    with pytest.raises(ModelOutputError, match="不符合 schema"):
        client.complete_json(request())


def test_openai_adapter_uses_responses_structured_output_without_storing_key():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "resp_test", "status": "completed", "model": "test-model",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"decision":"continue"}'}]}],
                "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
            }).encode()

    def opener(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["authorization"] = api_request.get_header("Authorization")
        captured["payload"] = json.loads(api_request.data)
        captured["timeout"] = timeout
        return Response()

    settings = ModelSettings(provider="openai", model="test-model", timeout=12)
    client = OpenAIResponsesClient(
        settings, environ={"OPENAI_API_KEY": "test-secret"}, opener=opener)
    result = client.complete_json(request())

    assert result.output == {"decision": "continue"}
    assert result.trace["response_id"] == "resp_test" and result.trace["usage"]["total_tokens"] == 13
    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["authorization"] == "Bearer test-secret" and captured["timeout"] == 12
    assert captured["payload"]["store"] is False
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert "test-secret" not in json.dumps(result.trace)


def test_openai_requires_model_and_key_without_network_call():
    with pytest.raises(ModelConfigurationError, match="CFD_MEMO_MODEL"):
        ModelSettings.from_env({"CFD_MEMO_MODEL_PROVIDER": "openai"})
    settings = ModelSettings(provider="openai", model="test-model")
    with pytest.raises(ModelConfigurationError, match="OPENAI_API_KEY"):
        OpenAIResponsesClient(settings, environ={})


def test_deepseek_adapter_uses_official_responses_api_and_non_thinking_json_schema():
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "id": "resp_deepseek", "status": "completed",
                "model": "deepseek-flash",
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": '{"decision":"continue"}'}]}],
                "usage": {
                    "input_tokens": 1200, "output_tokens": 260, "total_tokens": 1460,
                    "input_tokens_details": {"cached_tokens": 900},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            }).encode()

    def opener(api_request, timeout):
        captured["url"] = api_request.full_url
        captured["authorization"] = api_request.get_header("Authorization")
        captured["payload"] = json.loads(api_request.data)
        return Response()

    settings = ModelSettings(provider="deepseek", model="deepseek-flash")
    client = DeepSeekResponsesClient(
        settings, environ={"DEEPSEEK_API_KEY": "deepseek-secret"}, opener=opener)
    result = client.complete_json(request())

    assert result.output == {"decision": "continue"}
    assert result.trace["provider"] == "deepseek"
    assert result.trace["usage"]["cached_input_tokens"] == 900
    assert result.trace["usage"]["reasoning_tokens"] == 0
    assert captured["url"] == "https://api.deepseek.com/responses"
    assert captured["authorization"] == "Bearer deepseek-secret"
    assert captured["payload"]["reasoning"] == {"effort": "none"}
    assert captured["payload"]["text"]["format"]["type"] == "json_schema"
    assert "strict" not in captured["payload"]["text"]["format"]
    assert "store" not in captured["payload"]
    assert "deepseek-secret" not in json.dumps(result.trace)


def test_deepseek_settings_select_key_factory_and_reject_nonofficial_url():
    environment = {
        "CFD_MEMO_MODEL_PROVIDER": "deepseek",
        "CFD_MEMO_MODEL": "deepseek-flash",
        "DEEPSEEK_API_KEY": "deepseek-secret",
    }
    settings = ModelSettings.from_env(environment)
    status = settings.public_status(environment)

    assert status["api_key_env"] == "DEEPSEEK_API_KEY"
    assert status["api_key_configured"] is True and status["ready"] is True
    assert isinstance(create_model_client(settings, environ=environment),
                      DeepSeekResponsesClient)
    assert "deepseek-secret" not in json.dumps(status)

    with pytest.raises(ModelConfigurationError, match="官方 DeepSeek"):
        ModelSettings.from_env({
            **environment,
            "CFD_MEMO_DEEPSEEK_BASE_URL": "https://example.invalid/responses",
        })
    with pytest.raises(ModelConfigurationError, match="DEEPSEEK_API_KEY"):
        DeepSeekResponsesClient(settings, environ={})
