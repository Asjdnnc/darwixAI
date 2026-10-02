# Q3 Recording Guide — Philippines & Indonesia

Four screen recordings, two per market, as the brief requires. Every line below was run through the
live API first, so the responses described are what actually happens.

## Before you start

```bash
uvicorn app.main:app --reload       # then open http://127.0.0.1:8000/
```

1. Pick the market in the dropdown **before** pressing the call button — it sets the knowledge base,
   flow, language, speech-to-text code and voice together.
2. Speak in short, clear sentences. Whisper handles these languages well but turns long run-on
   sentences into one blurred phrase.
3. Let the agent finish; it listens again automatically.
4. Press **End Demo** at the end — that writes the transcript.

**Voice, so nothing surprises you on camera:**
- 🇮🇩 Indonesia speaks in **Damayanti**, a native Indonesian system voice.
- 🇵🇭 Philippines uses the **browser's English voice**. No Filipino voice exists on Groq, on OpenAI,
  or in macOS. The status line says so on screen. This is the documented compromise in
  `docs/Q3_MARKETS.md §5`, not a defect — worth saying out loud in the video.

---

## PH-1 — Cooperative customer, mixed English/finance terms

**Covers:** cooperative customer · code-switching · grounded answers · local payment rail · promise to pay

| # | Say |
|---|---|
| 1 | Opo, sige po. |
| 2 | Ano po ang mangyayari kung hindi ako makabayad? |
| 3 | Pwede po ba sa GCash? |
| 4 | Sa kinsenas po. |

**What happens:** turn 2 is answered from the grace-period policy **with two citations** (31-day
grace period, then lapse). Turn 3 cites the payment-channel record and explains the GCash Bills
Payment menu. Turn 4 closes as **promise to pay**, capturing *GCash* and *kinsenas*.

**Point at:** the citations under each reply; the panel filling in *Saan magbabayad* and *Kailan
magbabayad*; the green **promise to pay** badge; `CRM: promise created`.

**Say in the video:** *kinsenas* is the 15th-of-month payday. A date parser would return nothing —
Filipinos commit to paying on payday, not on a calendar date.

---

## PH-2 — Sector objection, then hardship escalation

**Covers:** sector-specific objection · approved objection handling · hardship referral · in-language escalation

| # | Say |
|---|---|
| 1 | Opo. |
| 2 | Mahal po masyado ang premium, pwede po bang gawing quarterly? |
| 3 | Wala po akong pera ngayon. |

**What happens:** turn 2 is **answered, not escalated** — it offers the quarterly payment mode and
says the amount will be computed by Policy Administration, citing the playbook. Turn 3 is real
hardship, so it refers to a licensed advisor and ends, **staying in Taglish**.

**Point at:** the agent never states an amount — amounts come from the system, and the guard blocks
invented numbers. Note that a price objection and an inability to pay are handled differently.

---

## ID-1 — Cooperative formal, finance terms, promise to pay

**Covers:** cooperative customer · finance terminology · code-switching loanwords · promise to pay

| # | Say |
|---|---|
| 1 | Iya, boleh. |
| 2 | Dendanya berapa kalau telat? |
| 3 | Bisa lewat Indomaret? |
| 4 | Besok. |

**What happens:** turn 2 answers with the real policy — 0,1% per day, max 30 days, 3-day tolerance —
**with two citations**. Turn 3 cites the retail-channel record. Turn 4 closes as **promise to pay**,
capturing *Indomaret* and *Besok*.

**Point at:** the reply stays in Bahasa while keeping *transfer*, *virtual account* and *Indomaret*
as the loanwords customers actually use. Damayanti is speaking — a native Indonesian voice.

---

## ID-2 — Regional accent, colloquial register, human escalation

**Covers:** Indonesian regional accent · colloquial speech · sector objection · human escalation

| # | Say |
|---|---|
| 1 | Iya, boleh. |
| 2 | Nggih Mbak, dendanya kok mahal ya? |
| 3 | Udah lah, mau bicara dengan petugas aja. |

**What happens:** turn 2 opens with the Javanese **nggih** and raises the denda objection; the agent
handles it from the playbook and says the exact figure will be checked in the system. Turn 3 is
colloquial (*udah*, *aja*) and asks for a person — the agent **escalates** with the real WIB advisor
hours, citing the escalation policy, in Bahasa throughout.

**Point at:** the regional marker is understood without asking the caller to repeat. The escalation
never switches to English — that is enforced in code by the `language_switch` guard, because
unexpected English switching is a stated rejection condition.

---

## After recording

Save as `Evidence/ph1.*`, `ph2.*`, `id1.*`, `id2.*` with the exported transcripts. The server also
writes a full per-turn record to `test_logs/calls/<call_id>.json`, including citations, captured
fields, outcome, CRM actions and every guard that fired — that file is the real evidence.

## Worth mentioning in the video

- **No native speaker has reviewed this content.** Register and collections tone are exactly what a
  non-native author gets subtly wrong, and in collections that is a compliance risk rather than a
  style issue. It is documented in `docs/Q3_MARKETS.md §8` as a known gap.
- **ASR was measured, not assumed**: Indonesian mean WER 0.056 with 13/13 finance terms retained;
  Javanese transcribes perfectly while Sundanese loses word boundaries (`"Punten Teh"` →
  `"Puntenteh"`). The pipeline survives it because the terms it depends on still come through.
- **The model was chosen by measurement** — three Groq models compared on these exact flows.
