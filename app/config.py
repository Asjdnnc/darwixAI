from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed settings. Never add secrets to source control."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    groq_api_key: str | None = None
    groq_model: str = "qwen/qwen3.8-27b"  # see docs/Q3_MARKETS.md for the model comparison
    groq_stt_model: str = "whisper-large-v3-turbo"
    groq_tts_model: str = "canopylabs/orpheus-v1-english"
    groq_tts_voice: str = "austin"  # orpheus: autumn, diana, hannah (female); austin, daniel, troy (male)
    openai_api_key: str | None = None
    openai_q3_q4_model: str = "gpt-4o-mini"
    openai_transcribe_model: str = "gpt-transcribe"
    openai_tts_model: str = "gpt-4o-mini-tts"
    openai_tts_voice: str = "ash"  # English fallback when the Groq TTS daily quota is exhausted
    nudge_min_confidence: float = 0.75
    # Measured on data/eval/q4_chunks.json: the LLM second pass adds no recall and costs precision
    # (1.00/1.00 rules only vs 1.00/0.875 with it), while adding ~215 ms per chunk. Off by default;
    # see docs/Q4_REALTIME.md.
    nudge_use_llm: bool = False
    crm_webhook_url: str | None = None  # optional: POST leads/callbacks/escalations here


settings = Settings()
