import pytest

from baton.opencode import OpenCode


def fake_client(models: list[dict]) -> OpenCode:
    client = object.__new__(OpenCode)
    client.list_models = lambda: models  # type: ignore[method-assign]
    return client


MODELS = [
    {"providerID": "acme", "id": "acme/llm-large", "modelID": "acme/llm-large"},
    {"providerID": "gpustack", "id": "glm-5.3", "modelID": "glm-5.3"},
]


def test_resolve_exact_model_id():
    client = fake_client(MODELS)
    assert client.resolve_model("gpustack/glm-5.3") == {"providerID": "gpustack", "id": "glm-5.3"}


def test_resolve_model_id_with_slash():
    client = fake_client(MODELS)
    assert client.resolve_model("acme/llm-large")["id"] == "acme/llm-large"


def test_unknown_provider_raises():
    client = fake_client(MODELS)
    with pytest.raises(ValueError, match="not available"):
        client.resolve_model("nope/model")


def test_unknown_model_lists_candidates():
    client = fake_client(MODELS)
    with pytest.raises(ValueError, match="cannot resolve"):
        client.resolve_model("gpustack/unknown")
