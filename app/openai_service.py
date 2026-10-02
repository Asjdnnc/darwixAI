"""OpenAI provider for Q3 localization and Q4 multilingual call insights."""
import json
from openai import OpenAI
from app.config import settings
from app.models import Signal


class OpenAIQ3Q4Service:
    def transcribe(self, audio: bytes, market: str) -> str:
        client = OpenAI(api_key=settings.openai_api_key)
        languages = ["tl", "en"] if market == "ph" else ["id", "en"]
        result = client.audio.transcriptions.create(
            file=("recording.webm", audio, "audio/webm"), model=settings.openai_transcribe_model,
            languages=languages, prompt="Financial customer-service conversation. Preserve Taglish or Bahasa Indonesia and finance terms exactly.",
        )
        return result.text

    def speak(self, text: str, market: str, output_path: str) -> None:
        client = OpenAI(api_key=settings.openai_api_key)
        language = "Tagalog" if market == "ph" else "Indonesian"
        response = client.audio.speech.create(
            model=settings.openai_tts_model, voice="marin", input=text[:4096], response_format="wav",
            instructions=f"Speak naturally in {language}. Preserve English finance loanwords without changing the language.",
        )
        response.stream_to_file(output_path)

    def detect_signals(self, event_text: str, market: str) -> list[Signal]:
        if not settings.openai_api_key:
            return []
        schema = {"name": "call_signals", "schema": {"type": "object", "properties": {"signals": {"type": "array", "items": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["compliance_gap", "cross_sell", "frustration", "payment_difficulty", "callback"]}, "confidence": {"type": "number"}, "evidence": {"type": "string"}}, "required": ["topic", "confidence", "evidence"], "additionalProperties": False}}}, "required": ["signals"], "additionalProperties": False}}
        try:
            client = OpenAI(api_key=settings.openai_api_key)
            result = client.chat.completions.create(model=settings.openai_q3_q4_model, temperature=0, response_format={"type": "json_schema", "json_schema": {**schema, "strict": True}}, messages=[{"role": "system", "content": "Detect only actionable financial-call signals. Do not infer signals without direct evidence. Preserve the customer language."}, {"role": "user", "content": f"Market: {market}\nTranscript chunk: {event_text}"}])
            return [Signal(**item) for item in json.loads(result.choices[0].message.content or "{}").get("signals", [])]
        except Exception:
            return []
