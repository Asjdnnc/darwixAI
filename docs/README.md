# Documentation

Design, evaluation and limitations for each part of the assessment. Start with the assessment
report; the rest go deeper on one question each.

| Document | Read it for |
|---|---|
| [ASSESSMENT_REPORT.md](ASSESSMENT_REPORT.md) | **Start here.** What was built across all four questions, with the measured results and the known gaps |
| [ARCHITECTURE.md](ARCHITECTURE.md) | How the pieces fit: the shared system, one turn of a call, the offline knowledge build, the real-time chunk pipeline, and the design decisions with their rationale |
| [architecture.html](architecture.html) | The same diagram rendered as a page, for screen-sharing. Open it in a browser |
| [VOICE_AGENT.md](VOICE_AGENT.md) | **Q1** — call flow, qualification rules, every safety guard and what it prevents, the API, limitations |
| [KB_DESIGN.md](KB_DESIGN.md) | **Q2** — record schema, cleaning pipeline, chunking, taxonomy, versioning, how citations are produced |
| [RETRIEVAL_EVAL.md](RETRIEVAL_EVAL.md) | **Q2** — 42 labelled queries with the retrieved record, its source, why it is relevant, and a verdict |
| [Q3_MARKETS.md](Q3_MARKETS.md) | **Q3** — why these are not translations, localization evidence per market, the ASR report, the cross-lingual retrieval problem, native TTS, and the native-speaker gap |
| [Q4_REALTIME.md](Q4_REALTIME.md) | **Q4** — signal design, every nudge suppression control, the measured latency tables, the false-positive analysis, and limits at 10× scale and on noisy audio |
| [GROQ_SETUP.md](GROQ_SETUP.md) | API key and model configuration |

## Generated documents

Two files in this directory are written by scripts rather than by hand, so they are not kept in the
repository. Regenerate them when you want current numbers:

```bash
python scripts/eval_retrieval.py     # writes RETRIEVAL_EVAL.md
python scripts/run_test_calls.py     # writes Q1_TEST_CALLS.md, 12 scripted calls via the live model
```

`run_test_calls.py` also writes a per-turn record of every call to `test_logs/calls/`, which is run
output rather than evidence — the curated recordings in [`../Evidence/`](../Evidence/README.md) are
what the submission relies on.

## The short version

- **The design decision that matters:** the language model writes the words, the code owns the
  decisions. Eligibility, escalation and compliance wording are deterministic and unit-tested.
- **Retrieval:** 82% top-1 on a held-out set, 7/7 out-of-scope questions rejected. When nothing
  clears the gates the agent says it does not know rather than guessing.
- **Q3:** different sectors, flows and compliance rules per market, with ASR measured per market
  rather than assumed.
- **Q4:** real-time rather than post-hoc, P50 298 ms end to end on real audio, precision 1.00 with
  no false positives on the labelled set — and the LLM second pass switched off because it measured
  worse than the rules.
- **The biggest gap:** no native speaker has reviewed the Taglish or Bahasa content.
