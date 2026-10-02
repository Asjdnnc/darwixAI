import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def offline(monkeypatch, tmp_path):
    """Tests never call Groq/OpenAI or write to the real CRM or call logs."""
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "crm_webhook_url", None)
    import app.main as main
    from app.crm import MockCRM
    monkeypatch.setattr(main.agent, "crm", MockCRM(tmp_path / "crm"))
    monkeypatch.setattr(main.agent, "log_dir", tmp_path / "calls")
    main.agent.sessions.clear()
