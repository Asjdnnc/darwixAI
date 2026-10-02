# Question 4: Live Insights and Nudges From Call Audio

Agent assist: it watches a **human** agent on a call in progress and raises short, actionable
nudges while there is still call left to act on.

```bash
python scripts/replay_call.py --scenario compliance        # scripted, real time
python scripts/replay_call.py Evidence/q3/id1.wav --market id   # real audio, real time
python scripts/eval_nudges.py                              # false-positive analysis
pytest tests/test_realtime.py                              # 14 tests
uvicorn app.main:app --reload                              # live nudge panel in the browser UI
```

## 1. Streaming, not post-hoc

The brief rules out analysing a finished upload, so the unit of work is one chunk of a call that is
still running. `scripts/replay_call.py` cuts a recording into 4-second chunks and releases each one
**only when its moment arrives in wall-clock time**: a 92-second call takes 92 seconds. Nothing is
read ahead.

Four inputs reach the same pipeline:

| Input | Path |
|---|---|
| Live browser microphone | `POST /calls/{id}/audio` |
| Already-transcribed chunk, or the UI's typed fallback | `POST /calls/transcript` |
| Replayed recording at real-time speed | `scripts/replay_call.py` |
| Scripted scenario | `scripts/replay_call.py --scenario` |

Delivery is over a **WebSocket** (`/ws/nudges/{id}`) for the dashboard, with a **polling** endpoint
(`/calls/{id}/nudges`) and a per-call **report** (`/calls/{id}/report`) alongside, so the transport
is not what makes it real time.

Agent and customer are separated by the `speaker` field, which matters: the same words mean
different things from each side. A price from the agent is a compliance event; a price from the
customer is not.

## 2. Signal design

Two stages (`app/signals.py`):

**Deterministic rules** run on every chunk in well under a millisecond. Several are **stateful**,
because a missing disclosure can only be judged against what has already been said in this call.

| Signal | Fires on | Group | Priority |
|---|---|---|---|
| `compliance_gap` | Agent states a price before the underwriting disclosure | compliance | 1 |
| `risky_statement` | Agent promises approval or coverage | compliance | 1 |
| `payment_difficulty` | Customer cannot pay, lost a job, is behind | relationship | 2 |
| `frustration` | Explicit anger, or a repetition marker that repeats | relationship | 2 |
| `buying_signal` | Customer asks how to sign up or proceed | opportunity | 2 |
| `missed_cross_sell` | A second asset, or a person who needs cover | opportunity | 3 |
| `callback` | Customer asks to be called back | logistics | 3 |
| `topic_shift` | Intent changes mid-call | logistics | 4 |

**An optional LLM pass** adds nuance, is clamped to the same taxonomy, and is **off by default** —
section 5 explains why, with numbers.

## 3. Nudge control

Generating nudges is easy; *not* generating them is the problem. Every control the brief lists is in
`app/nudges.py`, and every suppression is recorded with its reason, which is what makes section 5
possible.

| Control | Behaviour |
|---|---|
| Confidence threshold | Below `NUDGE_MIN_CONFIDENCE` (0.75) the signal is dropped |
| Higher bar for inferred signals | An LLM-sourced signal needs +0.10, since it has no explicit evidence |
| Cooldown | Per topic: 45 s for compliance, 90–150 s for the rest |
| Duplicate suppression | A near-identical message is dropped even across topics |
| Topic grouping | One nudge per coaching theme at a time — **except compliance**, where each finding is a distinct regulatory event |
| Priority | Compliance outranks relationship outranks opportunity; the active list is sorted and capped at 3 |
| Expiry | 60 s for compliance and buying signals, 90 s otherwise, so the panel shows only what is still actionable |
| Repetition limit | At most 2 per topic per call |

## 4. Measured latency

Timed from the moment the bytes arrive, so queueing counts against us rather than being hidden.
Reported live during the call and written to `data/q4/<name>.json`.

### Text chunks (UI, or an upstream ASR already running)

| Stage | P50 | P95 |
|---|---|---|
| Signal extraction | 0.4 ms | 5.6 ms |
| Nudge generation | 0.0 ms | 0.8 ms |
| Delivery | 0.1 ms | 1.3 ms |
| **End to end** | **0.6 ms** | **6.9 ms** |

### Real call audio, 4-second chunks (`Evidence/q3/id1.wav`, 92 s, 24 chunks)

| Stage | P50 | P95 |
|---|---|---|
| **ASR (Groq Whisper)** | **297.8 ms** | **371.6 ms** |
| Signal extraction | 0.0 ms | 1.7 ms |
| Nudge generation | 0.0 ms | 0.0 ms |
| Delivery | 0.1 ms | 0.3 ms |
| **End to end** | **298.0 ms** | **371.7 ms** |

Wall clock was 92.3 s for 92.0 s of audio, confirming real-time pacing rather than a fast replay.
**ASR is essentially the entire budget**; everything after it is free by comparison. A nudge lands
roughly a third of a second after the words are spoken, and at worst one chunk boundary (4 s) after.

With the LLM pass enabled, add ~215 ms P50 to each figure.

## 5. False-positive analysis

`data/eval/q4_chunks.json` labels 30 chunks: 14 positives and 16 **adversarial** negatives that
carry a signal's vocabulary without its substance. `python scripts/eval_nudges.py` scores the real
pipeline against them.

| Configuration | Recall | Precision | False-positive rate |
|---|---|---|---|
| Baseline rules | 1.000 | 0.824 | 0.188 |
| **Tightened rules (default)** | **1.000** | **1.000** | **0.000** |
| Tightened rules + LLM pass | 1.000 | 0.875 | 0.125 |

**The three baseline false positives were all keyword matches without context**, and were fixed in
the rules rather than papered over:

| Chunk | Was flagged | Fix |
|---|---|---|
| "The weather has been terrible this week" | frustration | "terrible"/"awful" only count alongside a service complaint |
| "I was reading about your family plan" | missed_cross_sell | Product names are stripped before matching |
| "My wife says hello" | missed_cross_sell | A *person* needs a sign they need cover |

Tightening the person rule then regressed the brief's own example — "we just bought a second
vehicle" — so a second **asset** is treated as an opportunity in itself, while a **person** needs
context. That distinction is the substance of the fix.

**Why the LLM pass is off by default.** Measured against the same set it added **no recall and cost
precision**, flagging "I was reading about your family plan" as a buying signal and "I paid it
already, last Tuesday" as payment difficulty — the literal opposite of what was said. It also costs
~215 ms per chunk against ~0.1 ms for the rules. It remains available (`NUDGE_USE_LLM=true`, or
`--llm`) because on a larger and noisier corpus the trade may invert; the point is that the choice
was measured rather than assumed.

Note this measures detection in isolation — each case runs in its own call, so cooldowns and
grouping cannot mask a miss. Suppression is exercised separately by the replay scenarios and by
`tests/test_realtime.py`.

## 6. Required scenario coverage

Run with `scripts/replay_call.py --scenario <name>`; all four are also regression tests.

| Scenario | Result |
|---|---|
| **Compliance** — price before disclosure, then an unlawful promise | 2 compliance nudges, 176 ms and 245 ms |
| **Missed opportunity** — customer mentions a spouse who lost cover | `missed_cross_sell` nudge |
| **Rising frustration** — a repetition marker, then explicit anger | `frustration`, confidence rising on repetition |
| **Noisy / ambiguous** — breaking line, "Hello? Hello", "never mind" | **0 nudges** |

## 7. Known limitations

**At 10× scale.** The bottleneck is ASR, not the analysis: rules cost ~0.1 ms per chunk, so a single
process could sustain thousands of concurrent calls on the text path alone. Ten times the traffic
means ten times the Whisper calls, which is where cost, rate limits and tail latency land. The fix
is architectural rather than algorithmic — a streaming ASR connection per call instead of
chunk-at-a-time HTTP, a queue between ASR and analysis so a slow transcription cannot block the
nudge path, and per-tenant rate limiting. State is currently in memory, so horizontal scaling needs
the call state in Redis, keyed by call, with sticky routing or a shared store.

**With noisy audio.** Two failure modes were observed on real recordings rather than predicted:
- **Whisper returns its own prompt as the transcript** on silent or unintelligible chunks. On the
  Indonesian replay, chunks came back as "Pertahankan istilah yang terbukukan hari ini", lifted
  straight from the ASR prompt. Nudges were being generated from words nobody said. Those chunks are
  now detected, discarded and counted in the report as `asr_prompt_echoes_discarded`.
- **Fixed 4-second chunking cuts words mid-utterance**, so a phrase spanning a boundary can be lost
  to both chunks. Voice-activity-based segmentation would fix this; a 4-second window is the simple
  choice that also bounds worst-case latency.

**Other limits.**
- Speaker separation relies on the caller labelling the turn. Real telephony needs channel
  separation or diarization; the replay harness alternates speakers, which is a simplification.
- Signals are English-only. The Q3 markets have their own vocabulary, so Taglish and Bahasa calls
  would need their own rule sets — the structure supports it, the rules are not written.
- The evaluation set is 30 chunks written by one author. It is enough to catch gross regressions and
  it did catch three, but it is not a substitute for labelled production calls.
- Nudges are advisory and unaudited. Anything compliance-related should be reviewed by a human
  before it drives a real conversation.
