from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_browser_interface_loads_without_any_voice_provider():
    response = client.get("/")
    assert response.status_code == 200
    # The page copy is edited freely; assert only that the branded call UI is served.
    assert "<title>DarwixAI" in response.text and 'id="btnStartCall"' in response.text


def test_retrieval_returns_traceable_source():
    response = client.get("/retrieve", params={"q": "What disclosure is required before a quote?"})
    assert response.status_code == 200
    assert response.json()["chunks"][0]["source_ref"] == "docs/underwriting_policy_v2.txt#mandatory-disclosures"


def test_unknown_question_says_unavailable_instead_of_hallucinating():
    client.post("/agent/turn", json={"call_id": "oos", "customer_text": "yes"})
    response = client.post("/agent/turn", json={"call_id": "oos", "customer_text": "What is the exact price for planet insurance?"})
    assert response.status_code == 200
    body = response.json()
    assert "don't have verified information" in body["text"] and body["citations"] == []


def test_nudge_is_created_and_duplicate_is_suppressed():
    """Both signals fire, but they share the "relationship" theme, so only one nudge is shown;
    repeating the same chunk is then suppressed by the cooldown."""
    event = {"call_id": "test-call", "speaker": "customer", "text": "I am angry because I cannot pay this month"}
    first = client.post("/calls/transcript", json=event).json()
    second = client.post("/calls/transcript", json=event).json()["nudges"]
    assert {s["topic"] for s in first["signals"]} == {"frustration", "payment_difficulty"}
    assert [n["topic"] for n in first["nudges"]] == ["frustration"]
    assert second == []
    report = client.get("/calls/test-call/report").json()
    assert report["nudges"]["suppression_reasons"]["topic_group_occupied"] >= 1


def test_voice_transcription_requires_groq_credentials():
    response = client.post("/voice/transcribe", content=b"fake-audio")
    assert response.status_code in {502, 503}


def test_streamed_wav_with_placeholder_sizes_is_parsed():
    import struct
    from app.main import read_wav
    pcm = b"\x01\x00\x02\x00" * 10
    fmt = struct.pack("<HHIIHH", 1, 1, 24000, 48000, 2, 16)
    streamed = (b"RIFF" + b"\xff\xff\xff\xff" + b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
                + b"LIST" + struct.pack("<I", 4) + b"INFO" + b"data" + b"\xff\xff\xff\xff" + pcm)
    assert read_wav(streamed) == ((1, 24000, 2), pcm)
