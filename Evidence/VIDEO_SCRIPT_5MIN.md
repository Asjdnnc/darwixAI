# 5-Minute Video Script — browser only, no terminal

**Open two tabs before you start:**

1. `docs/architecture-simple.html` — double-click it, it opens in your browser
2. `http://127.0.0.1:8000/` — the app

Start the server first (one command, before recording):

```bash
.venv/bin/uvicorn app.main:app --reload
```

Lines in **quotes** are what to say. Everything else is what to do.

---

## 0:00 – 0:50 · What it is — on the diagram

> **"This is a voice AI system for insurance and consumer finance. It covers all four parts of the
> assessment, and they're not four separate projects — it's one application. You pick the market at
> the top, and everything below follows."**

*Point at the four coloured boxes.*

> **"A voice agent that qualifies leads for US health insurance. The knowledge base behind it, built
> from messy documents. Two local-language bots — Philippines and Indonesia. And a live layer that
> watches a call as it happens and prompts the agent.**
>
> **All the data is synthetic — the company, the policies, the prices."**

---

## 0:50 – 1:30 · How it works — on the diagram

*Point at the six steps.*

> **"When someone speaks, six things happen. We turn speech into text. We look the answer up in the
> knowledge base. The language model writes the reply. The rules decide the outcome. Safety checks
> run on what it's about to say. Then it speaks."**

*Point at the line underneath.*

> **"This is the decision I'd defend hardest. The language model writes the words — the code owns
> the decisions. Who qualifies, when to escalate, what has to be disclosed before a price: all fixed
> in code. So neither the caller nor the model can talk the system out of a compliance rule.**
>
> **If the model fails or hits a rate limit, the call carries on using the rules."**

---

## 1:30 – 3:10 · Live call — switch to the app

Select **🇺🇸 US**, press the call button. Speak each line, waiting for the reply:

| # | Say |
|---|---|
| 1 | Yes, that's fine. |
| 2 | **I'm looking for health insurance for me and my wife.** |
| 3 | I'm 42. |
| 4 | Texas. |
| 5 | Just the two of us. |
| 6 | **How much does Essential Care cost per month?** |
| 7 | No, neither of us smokes. |
| 8 | No, we're both healthy. |
| 9 | No, I don't have cover right now. |
| 10 | Weekday evenings after six. |
| 11 | Yes, that's fine. |
| 12 | No, that's all, thanks. |

**Say at the start:**
> **"It opens by saying it's a virtual advisor and that the call is recorded. That's a compliance
> requirement, so it's scripted — not left to the model. Then it asks how it can help, rather than
> interrogating the caller."**

**After line 2** — a nudge appears in the right panel:
> **"That's the live layer. The customer mentioned a spouse, so it's prompting the agent to ask
> whether she needs covering too. That appeared in under a millisecond."**

**After line 6** — pause on the reply:
> **"Watch the order here. It gives the underwriting disclosure *before* the price. That ordering is
> enforced in code. And underneath, the sources — every factual answer links back to the document it
> came from, so nothing is invented."**

*Move briskly through 7–11.*

**At the end, point at the panel:**
> **"Qualified. Two plans matched, the details captured, and a lead and callback written to the CRM.
> Every turn is saved with its sources and outcome."**

> *If speech recognition mishears you, don't stop — say: "and that's exactly the kind of error this
> is built to absorb." It's more convincing than a clean take.*

---

## 3:10 – 4:20 · Local language — same app

Switch the dropdown to **🇵🇭 Philippines**, start a new call:

| # | Say |
|---|---|
| 1 | Opo, sige lang po. |
| 2 | Ano po ang mangyayari kung hindi ako makabayad? |
| 3 | Pwede po ba sa GCash? |
| 4 | Magbabayad po ako sa sahod ko. |

> **"Same application, different market — and this is a different product, not the same bot
> translated. Different sector: life insurance instead of health. Different call: a renewal reminder
> instead of a sales call. And outcomes the US agent doesn't have, like a promise to pay.**
>
> **It's speaking Taglish — Filipino sentences with the English insurance terms kept in English,
> because that's how Filipinos actually talk about insurance. The voice is a Tagalog model running
> locally, because no cloud provider I had access to offers one.**
>
> **And it just understood 'sahod' — payday — as the payment date. People here commit to paying on
> payday, not on a calendar date. You only get that by building for the market rather than
> translating into it."**

---

## 4:20 – 5:00 · Limits and close — back to the diagram

> **"What I'd fix before this went near a real customer.**
>
> **The biggest gap: no native speaker has reviewed the Filipino or Indonesian wording. I wrote it
> to be natural rather than translated, but tone in a collections call is exactly what a non-native
> writer gets subtly wrong — and that's a compliance risk, not a style one. That needs sign-off.**
>
> **I measured speech recognition per market instead of assuming it. Indonesian is strong.
> Philippines is weaker, and specifically on the English words inside Filipino sentences.**
>
> **And on the live layer, I tested using a language model to detect the prompts. It was slower and
> less accurate than simple rules, so it's switched off — with the measurement recorded next to the
> setting."**

*Point at the numbers along the bottom.*

> **"82% retrieval accuracy on questions it had never seen. A nudge on screen about a third of a
> second after the words are spoken. No false alarms on the labelled set. And 126 automated tests
> that run with no API keys — clone it and they pass."**

---

## Checklist

- [ ] Diagram visible and readable
- [ ] US call reaches **qualified**, citations visible
- [ ] At least one nudge appears in the right panel
- [ ] Philippines call in Taglish with the local voice
- [ ] Native-speaker gap said out loud
