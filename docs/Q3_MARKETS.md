# Question 3: Native-Language Voice Bots (Philippines & Indonesia)

Two localized bots, built as different products rather than one bot translated twice: different
sectors, different call flows, different outcomes, different compliance rules.

| | 🇵🇭 Philippines | 🇮🇩 Indonesia |
|---|---|---|
| Company | Darwix Life Philippines | Darwix Finance Indonesia |
| Sector | Life insurance / bancassurance | Multifinance / consumer finance |
| Flow | Renewal + lapse reminder | Installment reminder + collections support |
| Language | Taglish (Filipino structure, English insurance terms) | Bahasa Indonesia, formal and colloquial |
| Knowledge base | 30 records | 32 records |
| ASR | Groq `whisper-large-v3-turbo`, `tl` | Groq `whisper-large-v3-turbo`, `id` |
| TTS | **No native Filipino voice available** (see Native TTS) | Browser native Indonesian voice (Damayanti) |

```bash
python -m app.ingest ph id          # rebuild both market knowledge bases
python scripts/asr_check.py         # ASR measurement
pytest tests/test_markets.py        # 32 market tests
uvicorn app.main:app --reload       # pick the market in the UI dropdown
```

## 1. Why these are not translations

The rejection condition in the brief is "literal multilingual translation without code-switching,
regional fallback, or localization". Four structural decisions avoid it.

**Different sector and flow per market.** A US health lead-qualification script translated into
Taglish would still be a lead-qualification script. These are servicing calls, and they end in
outcomes Q1 has no equivalent for: `promise_to_pay`, `already_paid`, `hardship_referred`,
`disputed`, `refused`, `wrong_person`.

**Source documents in the language each market actually publishes in.** Philippine insurers publish
policy contracts and FAQs in English while agents speak Taglish on the phone, so the PH corpus is
English and the PH *call playbook* is Taglish. Indonesian multifinance publishes in Bahasa, so the
ID corpus is entirely Bahasa. This mirrors reality and it is also what makes PH retrieval hard
(section 4).

**Each market's own compliance regime**, not a translated US one:
- PH: never state coverage has stopped while the policy is inside the 31-day grace period; never
  promise reinstatement (it is an underwriting decision).
- ID: OJK-style collections conduct — calls only 08:00–20:00, identify yourself, no threats, no
  discussing the debt with family/neighbours/employer, no seizure talk by phone, never promise that
  a keringanan will be approved.

**Spoken lines come from the knowledge base**, not from a separate hardcoded script. Every line in
`app/flows.py` is lifted from that market's approved playbook record, so the script and the
retrievable guidance cannot drift apart.

## 2. Localization evidence (≥3 per market)

### Philippines

| # | Localization | Why a translation would fail |
|---|---|---|
| PH-1 | **Payday-linked promises**: `kinsenas` (15th) and `katapusan` (end of month) are recognized payment dates | Filipinos commit to paying on payday, not on a calendar date. "On the 15th" is not what callers say, and a date parser would return nothing |
| PH-2 | **`po` / `opo` handled as grammar, not vocabulary** — stripped before intent matching | The respect particle slots anywhere: "Wala **po** akong pera" vs "Wala akong pera". Listing variants is unmaintainable; it is a politeness marker, not content |
| PH-3 | **Local payment rails** as first-class channels: GCash, Maya, Bayad Center, SM Bills Payment, Palawan Express, 7-Eleven CLiQQ | A translated US script offers "bank transfer or credit card"; most Philippine premium payments are over-the-counter or e-wallet |
| PH-4 | **Insurance terms deliberately left in English** inside Filipino sentences | Filipino clients say "premium", "policy", "rider", "lapse", "grace period". Translating them ("patakaran", "palugit") sounds wrong and clients do not use them |
| PH-5 | **Exclamations kept as politeness** | The English cleaning rule drops exclamation sentences as marketing hype; it deleted "Ay, salamat po sa pagsabi!", ordinary Filipino politeness |

### Indonesia

| # | Localization | Why a translation would fail |
|---|---|---|
| ID-1 | **Day-first dates**: `01/02/2026` ingests as 1 February | A US-order parser reads it as 2 January — a month-wrong due date on a collections call |
| ID-2 | **Register switching**: formal (Bapak/Ibu, saya) by default, santai (Pak, udah, nih) when the caller is casual | A single formal register sounds like a government letter to a casual caller; a single casual one is disrespectful to an older customer |
| ID-3 | **Regional markers recognized**: Javanese `nggih`, `sampun`, `mboten`, `monggo`; Sundanese `punten`, `mangga`, `muhun`, `teu acan` | A Jakarta-only bot asks these callers to repeat themselves, which is the complaint the brief calls out |
| ID-4 | **`gajian` (payday)** recognized as a payment date, and local rails: virtual account, Indomaret, Alfamart, GoPay, OVO, DANA, ShopeePay | Same as PH-1/PH-3: this is how Indonesians actually pay |
| ID-5 | **Indonesian clitics stripped for retrieval**: `dendanya` → `denda` | Otherwise the most common way a caller says the word never matches the document |
| ID-6 | **Indonesian month names** (Februari, Agustus, Oktober, Desember) parsed | English month parsing silently drops these dates |

## 3. ASR configuration and results

Provider **Groq**, model **`whisper-large-v3-turbo`**, language forced per market (`tl`, `id`) and a
market-specific prompt that primes Whisper to keep finance loanwords in English rather than
transliterating them.

`python scripts/asr_check.py` synthesizes the probes in `data/eval/asr_utterances.json`, transcribes
them, and measures word error rate plus retention of the terms the agent depends on. Results in
`data/eval/asr_results.json`.

### Indonesia — measured

| Register | WER | Terms kept |
|---|---|---|
| Cooperative, formal | 0.00 | angsuran, jatuh tempo |
| Code-switching (transfer, virtual account) | 0.00 | transfer, virtual account |
| Objection (denda, tenor) | 0.00 | denda, tenor |
| Colloquial Jakarta (gajian) | 0.00 | gajian |
| **Regional Javanese** (nggih, sampun) | **0.00** | cicilan |
| **Regional Sundanese** (punten, teu acan) | **0.44** | gajian |
| Escalation | 0.00 | petugas |
| Finance terms (DP, pembiayaan) | 0.00 | DP, pembiayaan, angsuran |

**Mean WER 0.056, term retention 13/13.**

**Accent observation.** Javanese transcribes perfectly; **Sundanese is the weak point**. The observed
errors are lost word boundaries, not wrong words:

> said: `Punten Teh, abdi teu acan gajian, mangga minggu depan.`
> heard: `Puntenteh, Abdi Tuacan Gajian, Mangga Minggu Depan.`

Critically the pipeline still works: `gajian` survives, so the payment date is still extracted and
the intent is still read as a promise. This is covered by a regression test
(`test_pipeline_survives_the_observed_sundanese_asr_errors`), because term retention matters more
to this system than raw WER.

### Philippines — not measurable synthetically

macOS ships no Filipino voice, so there is nothing to synthesize Taglish probes with. The PH probes
are defined in the same fixture and are measured from the **recorded calls** instead. Observed from
those recordings: English insurance terms inside Filipino sentences transcribe reliably (the prompt
does real work here), while rapid `po`-heavy speech occasionally drops the particle — harmless,
since politeness is stripped before intent matching anyway.

**What these numbers do not measure.** Synthesized speech is cleaner and more regular than a human
on a mobile line. Treat the Indonesian figures as an upper bound; the recorded calls in `Evidence/`
are the real-speech evidence.

## 4. Cross-lingual retrieval — the main technical problem

Philippine documents are English, Philippine callers speak Taglish. "Magkano po ang babayaran ko?"
shares essentially no tokens with "Premiums may be paid annually". Baseline retrieval scored **2/5**
and both hits were the wrong record.

Three fixes, each measured:

1. **Per-market query expansion** bridges caller language to document vocabulary
   (`magkano|babayaran` → premium payment amount due; `keringanan` → restrukturisasi perpanjangan tenor).
2. **Per-market stop words.** This was the biggest single fix. Without them, Taglish and Bahasa
   function words (`po`, `ninyo`, `yang`, `aja`) dominated scoring, so *every* question matched
   whichever record happened to be written in the caller's language — the Taglish objection playbook
   answered questions about beneficiaries and reinstatement.
3. **Per-market gates** (score ≥ 3.0, coverage ≥ 0.25 vs 2.5/0.4 for English). Coverage runs
   structurally lower when ordinary local words ("mangyayari", "minta") are absent from a partly
   English corpus, so the score gate carries more of the work.

**Result: 7/7 per market** — correct records for in-scope questions, and every out-of-scope question
(car insurance, weather, company directors) rejected in both languages. Asserted in
`tests/test_markets.py`.

Embeddings would generalize this; the expansion table is the explainable, dependency-free stand-in.

## 5. Native TTS and the compromises made

| Market | Outcome |
|---|---|
| Indonesia | **Native voice available.** Browser speech synthesis uses Damayanti (`id_ID`) |
| Philippines | **No Filipino voice exists** on Groq (Orpheus is English-only), on OpenAI with this account, or in macOS system voices |

Sending Taglish to an English voice model returns English-accented nonsense, so `/voice/speak`
returns **501** for both markets with the reason, and the browser synthesizes instead. The UI shows
which engine spoke each line, so a demo never silently misrepresents this.

**Production fix:** Azure Speech and ElevenLabs both ship Filipino (`fil-PH`) voices; that is a
provider swap in `/voice/speak`, not a redesign.

## 6. Fallback and escalation stay in language

"Unexpected English switching" is a stated rejection condition, so it is enforced in code, not left
to the prompt. A reply containing none of that market's language markers is **dropped** and replaced
with the in-language fallback (`language_switch` guard).

This caught two real bugs during end-to-end testing: the reminder agent was initially using the Q1
system prompt, so Taglish callers received whole English sentences ("I'm sorry, I don't have the
exact amount due") and Indonesian callers were offered **asuransi kesehatan** — US health insurance.

Escalation, hardship referral and the unavailable-information fallback are all scripted in-language,
with each market's real advisor hours (PH: Mon–Fri 8 AM–6 PM, Sat 9 AM–12 NN; ID: Senin–Jumat
08.00–17.00 WIB, Sabtu 09.00–13.00 WIB).

## 7. Market comparison

| | Philippines | Indonesia |
|---|---|---|
| Hardest technical problem | Cross-lingual retrieval (English corpus, Taglish speech) | Register switching and regional variation |
| Code-switching | Constant and expected; English insurance terms inside Filipino grammar | Loanwords only (transfer, virtual account); sentences stay Bahasa |
| Politeness | Grammatical particle `po`/`opo`, must appear in every line | Lexical: Bapak/Ibu, mohon, silakan; varies by register |
| Compliance pressure | Moderate — grace-period wording, no reinstatement promises | High — OJK collections conduct, no threats or third-party contact |
| ASR quality | Not synthetically measurable; terms transcribe well in recordings | Mean WER 0.056; Sundanese is the weak spot |
| Native TTS | Not available | Available |

## 8. Known gaps

- **No native-speaker review.** This is the most important gap. The Taglish and Bahasa content was
  written to be idiomatic rather than translated, and reviewed against the terminology each brief
  specifies, but it has not been validated by a native speaker. Register, politeness level and
  collections tone are exactly the things a non-native author gets subtly wrong, and in a
  collections context that is a compliance risk, not a style issue. **Sign-off by a Filipino and an
  Indonesian speaker is required before any real call.**
- **No compliance sign-off.** The OJK-style conduct rules and Insurance Commission-flavoured wording
  are written from public norms, not from a reviewed legal source.
- **Tagalog morphology is not handled.** The stemmer is English. Filipino affixes (`mag-`, `nag-`,
  `-in`, `-an`) are not reduced, so `magbayad` and `bayad` are separate tokens; the expansion table
  compensates where it matters.
- **Synthetic ASR only for Indonesia**, and none for Filipino (no voice to synthesize with).
- **One regional accent per market tested.** Indonesia has far more variation (Batak, Minang,
  Bugis); Philippine regional languages (Cebuano, Ilocano) are not covered at all.
- All company names, policies, amounts and customers are synthetic.
