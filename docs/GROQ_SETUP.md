# Groq Setup Guide

## 1 Create an API key

Create a Groq API key in the Groq Console, then copy `.env.example` to `.env` and set `GROQ_API_KEY`. Do not expose the key in browser JavaScript or commit it to Git.

## 2 Choose models

- Chat and grounded answers: `llama-3.3-70b-versatile`
- Multilingual transcription: `whisper-large-v3-turbo`
- English TTS: `canopylabs/orpheus-v1-english`

Groq Whisper supports multilingual transcription. Groq's current first-party TTS models support English and Saudi Arabic, not Filipino or Indonesian. Therefore the web interface presents localized text for the Philippines and Indonesia prototypes, and the report must state this limitation.

## 3 Run the application

```bash
cp .env.example .env
python3 -m pip install -e '.[dev]'
python3 -m uvicorn app.main:app --reload --port 8000
```

Use `http://127.0.0.1:8000/docs` to test text, transcription, TTS, retrieval, and nudge endpoints. A public URL is unnecessary unless you later deploy it for external testers.

## 4 Browser flow

The browser records a short WebM/WAV chunk and POSTs it to `/voice/transcribe`. Send the returned transcript to `/agent/turn`, then call `/voice/speak` only for the English market. Send every transcript chunk concurrently to `/calls/transcript` to produce real-time nudge events.
