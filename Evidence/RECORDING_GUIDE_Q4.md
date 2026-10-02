# Q4 Recording Guide — Live Insights and Nudges

The brief asks for a **recorded live demo** showing real-time processing, useful nudges within
seconds, measurable latency, and suppression of repetitive or low-value alerts. Four terminal
segments cover all of it in about three minutes, and an optional browser segment shows the agent's
view.

Every command below was run before this guide was written; the outputs described are what actually
happens.

## Setup

```bash
cd ~/Desktop/darwix
.venv/bin/uvicorn app.main:app --reload     # only needed for the browser segment
```

Record the terminal full-screen with a readable font. Nudges print in bold, so they stand out.

---

## Segment 1 — Compliance and risk, in real time (~40 s)

```bash
.venv/bin/python scripts/replay_call.py --scenario compliance
```

**What to say while it runs:** this is a recording replayed at wall-clock speed. Each chunk is
released only when its moment arrives, so a 20-second call takes 20 seconds. Nothing is read ahead —
analysing a finished upload would not qualify.

**What appears:**

| At | Nudge |
|---|---|
| The agent quotes `$189 per month` with no disclosure | **compliance_gap** — "Give the underwriting disclosure before discussing price" |
| The agent says "you're definitely approved" | **risky_statement** — "Do not promise approval or coverage" |
| The customer asks "how do I sign up?" | **buying_signal** — move to next steps |

**Point at the footer:** `wall clock 20.0s for 20.0s of call` — that line is the proof it is real
time. Then the per-stage latency table, and `nudges fired 3`.

**Worth saying:** those are the brief's two required examples in one call — a compliance example and
a missed-opportunity/buying example. The compliance rule is *stateful*: it only fires because the
disclosure had not been given yet. Once the agent gives it, the same sentence is clean.

---

## Segment 2 — Missed opportunity, and suppression (~30 s)

```bash
.venv/bin/python scripts/replay_call.py --scenario cross_sell
```

The customer says their **wife just lost her employer plan**; the agent carries on taking a date of
birth. One **missed_cross_sell** nudge fires.

**Point at:** `nudges fired 1, suppressed 2 {'cooldown': 2}`. The opportunity is raised once, not on
every later turn that mentions the family. Suppression is the hard part of a nudge system, and every
suppression is recorded with its reason.

---

## Segment 3 — The silent call (~30 s)

```bash
.venv/bin/python scripts/replay_call.py --scenario noisy
```

A breaking line, "Hello? Hello", "never mind, nothing urgent".

**`nudges fired 0`.** Say that out loud — this is the required case where the system must stay quiet.
A nudge engine that fires on noise is worse than none, because the agent stops reading them.

---

## Segment 4 — The numbers (~45 s)

```bash
.venv/bin/python scripts/eval_nudges.py
```

Thirty labelled chunks, sixteen of them adversarial — they carry a signal's vocabulary without its
substance: *"the weather has been terrible"*, *"I was reading about your family plan"*, *"my wife
says hello"*.

**Ends with:** `recall 1.0, precision 1.0, false-positive rate 0.0`.

**The story worth telling here** (this is the strongest minute of the whole video):

> The first version scored precision 0.824 with a false-positive rate of 0.188. All three failures
> were keyword matches without context. I fixed them in the rules — and tightening the "person"
> rule then broke the brief's own second-vehicle example, so a second *asset* is an opportunity in
> itself while a *person* needs a reason. Then I measured the LLM second pass against the same set.
> It added no recall, cost precision, ran two thousand times slower, and called "I paid it already,
> last Tuesday" a payment difficulty. So it is off by default.

Optionally show it: `.venv/bin/python scripts/eval_nudges.py --llm` → precision drops to 0.875.

---

## Segment 5 — Real audio, real ASR (~60 s, optional but strong)

```bash
.venv/bin/python scripts/replay_call.py Evidence/q3/id1.wav --market id
```

A real 92-second Indonesian call replayed at wall-clock speed through live Whisper.

**Point at:** `wall clock 92.3s for 92.0s`, and the latency split — **ASR P50 298 ms** against
0.0 ms for signal extraction. ASR is the entire budget; everything after it is free.

**Also point at the transcript:** some chunks come back as *"Pertahankan istilah yang terbukukan
hari ini"* — that is Whisper returning **its own prompt** when a chunk is silence. Nudges were being
generated from words nobody said. Those chunks are now detected, discarded and counted. That failure
only shows up when you run real audio through a real streaming pipeline.

Skip this segment if you want a three-minute video; keep it if you want the strongest limitation
finding on camera.

---

## Segment 6 — The agent's view (optional, ~45 s)

With the server running, open `http://127.0.0.1:8000/`, choose **🇺🇸 US**, start a call and type:

1. `Yes, that's fine.`
2. `I need cover for me and my wife, she just lost her job.`
3. `This is ridiculous, I've been waiting weeks for a callback.`

The right-hand panel fills with nudge cards: topic, a priority colour (compliance red, relationship
amber, opportunity blue), the confidence, and the measured latency for that chunk. Both sides of the
call are watched — the agent's own replies go through the same pipeline, which is how compliance
signals are caught at all.

Press **End Demo**: the panel header shows `N fired · M suppressed · P50 … ms · P95 … ms` for the
call.

---

## Closing line for the video

> Real-time rather than post-hoc, nudges in under a second end to end with ASR as the whole budget,
> precision 1.00 with a zero false-positive rate on the labelled set, and the LLM turned off because
> I measured it making things worse. The limitations are documented: at ten times the scale the
> bottleneck is ASR, not the analysis, and on noisy audio Whisper hallucinates its own prompt.

Full write-up: `docs/Q4_REALTIME.md`. Raw per-run reports: `data/q4/*.json`.
