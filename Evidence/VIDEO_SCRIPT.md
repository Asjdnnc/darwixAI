# Video Walkthrough Script

Covers the five things the brief asks for: system overview and live demonstration · architecture and
key design decisions · knowledge-base/retrieval design and voice-agent flow · multilingual handling
and live nudge generation · error/fallback cases, limitations and production improvements.

**Target: 8–10 minutes.** Lines in quotes are what to say. Everything else is what to show.

**Before you start**

```bash
cd ~/Desktop/darwix
.venv/bin/uvicorn app.main:app --reload
```

Open two things: `http://127.0.0.1:8000/` and `docs/architecture.html` (double-click it — it opens in
your browser). Have a terminal ready.

---

## 1 · Opening (30 s) — on the architecture diagram

> "This is a voice-AI system for insurance and consumer finance. It covers all four parts of the
> assessment, and they're not four separate projects — it's one FastAPI app, one browser interface
> and one knowledge pipeline, with the market switched at the top.
>
> Question 1 is a knowledge-grounded voice agent for US health insurance. Question 2 is the
> knowledge base behind it. Question 3 is two native-language bots, Philippines and Indonesia.
> Question 4 watches a live call and nudges the agent in real time.
>
> Everything you'll see is synthetic data. And every number on this screen is produced by a script
> in the repo — I'll run some of them."

*Point at the five figures along the bottom.*

---

## 2 · Architecture and design decisions (90 s) — on the diagram

> "The top box is the browser. You pick a market, speak, and get back a reply with source citations,
> a lead panel and a live nudge panel.
>
> The middle row is the server. Speech-to-text is Groq Whisper, configured per market. The agent
> routes by market — US goes to the lead-qualification agent, Philippines and Indonesia to a
> reminder agent, because those are servicing calls, not sales calls. And retrieval sits beside
> them, serving all three."

*Point at "One turn of a call".*

> "This is the core design decision, and it's the one I'd defend hardest. **The model writes the
> language. The code owns the decisions.**
>
> Eligibility, escalation, compliance wording, which question comes next — all deterministic and
> unit-tested. The language model handles understanding, extraction and phrasing. So the system
> can't be talked out of a compliance rule, by the caller or by the model.
>
> Step 7 is where that's enforced. Every reply is checked before it's spoken: no invented numbers,
> no promises of approval, the underwriting disclosure before any price, and no drifting into
> English on a Taglish call. If the model fails or the provider rate-limits, step 3 falls back to
> rules and the call keeps going."

---

## 3 · Knowledge base and retrieval (90 s) — terminal, then diagram

> "The knowledge base is built offline from deliberately messy sources — web pages, PDF text, a
> spreadsheet, a form, and a scanned PDF with no text layer."

```bash
.venv/bin/python -m app.ingest
```

> "It strips navigation and boilerplate, flags the scanned PDF instead of silently skipping it,
> quarantines source errors — there's a negative deductible and a February the 30th in there —
> removes duplicates, redacts personal data, and standardises terminology and dates. Thirty records
> for the US market, thirty and thirty-two for the two Asian markets.
>
> Every record keeps a source reference, and that reference is what the agent cites. So any sentence
> the customer hears traces back to a specific section of a specific document."

```bash
.venv/bin/python scripts/eval_retrieval.py
```

> "Retrieval is BM25 with query expansion and a coverage check — deliberately not embeddings, so
> every score is explainable and there's no extra service to run. Forty-two labelled queries. A
> hundred percent on the set I tuned on, and **eighty-two percent on a held-out set I wrote
> afterwards** — that second number is the honest one. All seven out-of-scope questions rejected.
>
> That last part matters more than the accuracy. If retrieval returns nothing good enough, the agent
> says it doesn't know, instead of inventing an answer."

---

## 4 · Live voice call — Q1 (2 min) — on the browser

Select **🇺🇸 US**, press the call button, and speak:

1. "Yes, that's fine."
2. "I'm looking for health insurance for me and my wife."
3. "I'm 42."
4. "Texas."
5. "Just the two of us."
6. **"How much does Essential Care cost per month?"**
7. "No, neither of us smokes."
8. "No, we're both healthy."
9. "No, I lost my coverage when I left my job."
10. "Weekday evenings after six."
11. "Yes, that's fine."
12. "No, that's all, thanks."

Narrate over it:

> "It opens by saying it's a virtual advisor and that the call is recorded — that's a compliance
> requirement, and it's scripted, not left to the model.
>
> Then it asks how it can help, rather than launching into questions. The caller decides what the
> call is about."

*At turn 6, pause on the reply.*

> "Watch this one. It gives the underwriting disclosure **before** the price — that ordering is
> enforced in code — and the citations underneath link to the exact records the answer came from."

*At the end, point at the panel.*

> "Qualified, two plans matched, and a lead and callback written to the CRM. Every turn is also saved
> server-side with the citations, the guards that fired and the outcome."

**If something goes wrong on camera, don't cut it — narrate it.** Speech-to-text mishearing you is a
real condition this system is built for, and saying so is stronger than a clean take.

---

## 5 · Multilingual — Q3 (2 min) — browser, then terminal

Switch to **🇵🇭 Philippines**:

1. "Opo, sige lang po."
2. "Ano po ang mangyayari kung hindi ako makabayad?"
3. "Pwede po ba sa GCash?"
4. "Magbabayad po ako sa sahod ko."

> "This is a different product, not the same bot translated. Different sector — life insurance.
> Different flow — a renewal and lapse reminder. And outcomes the US agent doesn't have: promise to
> pay, already paid, hardship referral.
>
> It's speaking Taglish — Filipino sentences with the English insurance terms kept in English,
> because that's how Filipinos actually talk about insurance. And that's a native Tagalog voice
> running locally; no cloud provider here offers one.
>
> It just captured 'sahod' — payday — as the payment date. People in both these markets commit to
> paying on payday, not on a calendar date."

*Then the honest part:*

```bash
.venv/bin/python scripts/asr_check.py
```

> "I measured speech recognition per market instead of assuming it. Indonesian is strong — word
> error rate of five percent, every finance term retained. Javanese accents transcribe perfectly;
> **Sundanese loses word boundaries.**
>
> Philippines is much worse — twenty-nine percent, and only four of twelve key terms retained. The
> failure is specific: Filipino words transcribe fine, the **English** loanwords don't. 'Coverage'
> comes back as 'kubirage'.
>
> The system still works, because it's built on term retention rather than perfect transcription.
> That's the design point."

---

## 6 · Live nudges — Q4 (2 min) — terminal

```bash
.venv/bin/python scripts/replay_call.py --scenario compliance
```

> "This is a call being analysed while it happens. The recording is released chunk by chunk at
> wall-clock speed — a twenty-second call takes twenty seconds. Nothing is read ahead, because
> analysing a finished recording wouldn't qualify.
>
> There — the agent quoted a price without the disclosure, and the compliance nudge fired. And
> there — 'you're definitely approved', which an agent may not say.
>
> Look at that line: twenty seconds of wall clock for twenty seconds of call. That's the proof it's
> real time."

```bash
.venv/bin/python scripts/replay_call.py --scenario noisy
```

> "A noisy call — a breaking line, 'hello, hello', 'never mind'. **Zero nudges.** A nudge engine that
> fires on noise is worse than none, because the agent stops reading them."

```bash
.venv/bin/python scripts/eval_nudges.py
```

> "Thirty labelled chunks, sixteen of them adversarial — they contain a signal's vocabulary without
> its meaning. 'The weather has been terrible.' 'My wife says hello.'
>
> Precision one point zero, zero false positives. But it didn't start there — it started at point
> eight two. All three failures were keyword matches without context, so I fixed the rules.
>
> Then I measured the language model as a second pass. **It added no recall, cost precision, ran two
> thousand times slower, and labelled 'I paid it already last Tuesday' as a payment difficulty.** So
> it's off by default. That's in the config with the measurement next to it."

---

## 7 · Limitations and production (60 s) — on the diagram

> "What I'd want fixed before this went near a real customer.
>
> **The biggest gap: no native speaker has reviewed the Taglish or Bahasa content.** I wrote it to be
> idiomatic rather than translated, but register and collections tone are exactly what a non-native
> author gets subtly wrong — and in collections that's a compliance risk, not a style issue. That
> needs sign-off before a single real call.
>
> Retrieval is keyword-based. It's explainable and fast, but embeddings would handle paraphrases
> better — the two held-out failures are both that.
>
> On the real-time side, at ten times the scale the bottleneck is speech-to-text, not the analysis —
> the analysis is a tenth of a millisecond. That's a streaming connection per call and a queue, not
> a smarter algorithm.
>
> And on noisy audio I found something I wouldn't have predicted: **Whisper returns its own prompt
> as the transcript** when a chunk is silence. Nudges were being generated from words nobody said.
> Those chunks are now detected and counted. You only find that by running real audio through a real
> streaming pipeline.
>
> A hundred and twenty-six tests, all offline — clone it and run pytest with no API keys."

---

## Checklist

- [ ] Architecture diagram on screen, legible
- [ ] Live Q1 call reaching **qualified** with citations visible
- [ ] Live Q3 call in Taglish or Bahasa with the native voice
- [ ] Q4 compliance nudge firing in real time
- [ ] Q4 noisy scenario firing nothing
- [ ] One measurement run live (`eval_retrieval.py`, `asr_check.py` or `eval_nudges.py`)
- [ ] Limitations said out loud, including the native-speaker gap

## If you only have four minutes

Sections 1, 4 and 6, then the first and last paragraph of section 7.
