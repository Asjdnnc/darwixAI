# Q3 call evidence — Philippines & Indonesia

Four calls, two per market, as the brief requires. **The caller's voice is synthesized** (Google
TTS) because the submitting engineer does not speak Filipino or Bahasa Indonesia. Everything else is
the live pipeline, with nothing shortcut:

    caller text -> Google TTS -> audio -> Groq Whisper (real ASR) -> agent -> Meta MMS-TTS -> audio

Each `.wav` is the complete two-sided call. Each `.json` holds the transcript with, per turn, what
was spoken, what ASR actually heard, whether ASR changed it, the citations used and the outcome.
Reproduce any of them with `python scripts/simulate_call.py ph1`.

| Call | Market | Scenario | Outcome | ASR altered |
|---|---|---|---|---|
| `ph1` | 🇵🇭 | Cooperative; asks what happens if unpaid; pays by GCash on payday | **promise to pay** (GCash / sahod) | 2 of 4 turns |
| `ph2` | 🇵🇭 | Price objection, then genuine inability to pay | **hardship referred** | 1 of 3 turns |
| `id1` | 🇮🇩 | Formal; asks the late-payment penalty; pays at Indomaret | **promise to pay** (Indomaret / besok) | 1 of 4 turns |
| `id2` | 🇮🇩 | Javanese marker, colloquial register, asks for a human | **escalated** | 1 of 3 turns |

Between them these cover the brief's required test coverage: cooperative customer, sector-specific
objection, mixed English/finance terms, colloquial speech, human escalation, and an Indonesian
regional accent.

## Real ASR errors are visible in these calls, and the system absorbs them

Nothing was cleaned up. `asr_changed: true` marks every turn ASR altered:

| Spoken | Heard | Still worked because |
|---|---|---|
| Pwede po ba sa GCash? | "Pwede Pulu Basa GCash." | GCash survived, so the channel was captured |
| Magbabayad po ako sa sahod ko. | "Magbabayan po ako sa sahod ko." | *sahod* (payday) survived |
| Nggih Mbak, dendanya kok mahal ya? | "Enggak mbak, dendanya kok mahal ya?" | The Javanese marker was lost but the objection was still handled |

This is the point of the design: the system depends on **term retention**, not on perfect
transcription. Measured ASR figures are in `docs/Q3_MARKETS.md` section 3.

## Honest limitations

- **Synthesized caller, not a human.** A native speaker would transcribe better still, so these
  understate real-world quality rather than flatter it. The one earlier human-recorded Philippine
  call produced *closer* transcriptions than the synthetic caller does.
- **Google TTS is used for the caller only**, because Whisper transcribes it far more reliably than
  the local MMS voice (MMS pronounces English loanwords with local phonology: "GCash" came back as
  "sag-asi"). The **agent** speaks with the local MMS voice that the product actually ships.
- **No native-speaker review** of the Taglish or Bahasa content — see `docs/Q3_MARKETS.md` section 8.
