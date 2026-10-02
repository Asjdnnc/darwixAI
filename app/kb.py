"""Knowledge-base index and retriever (Q2), used by the Q1 voice agent.

Ranking is BM25 over stemmed tokens, with the record title weighted twice, plus a small
query-expansion table that maps caller phrasing ("how much", "real person") onto knowledge-base
vocabulary ("premium", "human advisor"). It is transparent, dependency-free and deterministic,
which makes every retrieval decision explainable in the evaluation report.

Two gates decide whether a result is evidence for a customer-facing answer:
  * score    - absolute BM25 score of the best chunk;
  * coverage - share of the query's informative terms (IDF-weighted) that the chunk explains.
    Unknown words ("planet", "car") carry maximum IDF, so a generic overlap on "insurance" or
    "price" cannot ground an answer to an out-of-scope question.

Production path: keep this as the sparse half of a hybrid retriever and add dense embeddings
plus a cross-encoder reranker (see docs/KB_DESIGN.md).
"""
from __future__ import annotations

import math
import re
from collections import Counter

from app.models import Chunk, KnowledgeRecord, RetrievalResult

CHUNK_WORDS = 80  # short chunks suit spoken answers and small-model context windows
K1, B = 1.2, 0.75
TITLE_WEIGHT = 4
EXPANSION_WEIGHT = 0.5
MIN_SCORE = 2.5  # calibrated on data/eval/retrieval_queries.json (scripts/eval_retrieval.py)
MIN_COVERAGE = 0.4
# Taglish and Bahasa questions contain many ordinary words that a (partly English) corpus has never
# seen, so coverage runs structurally lower than in English. The score gate carries more of the work
# in those markets; out-of-scope rejection is verified per market in tests/test_markets.py.
MARKET_GATES = {"en": (MIN_SCORE, MIN_COVERAGE), "ph": (3.0, 0.25), "id": (3.0, 0.25)}
RELATIVE_CUTOFF = 0.4  # drop results far below the best match

EN_STOP_WORDS = set("""
a about after again all also am an and any are as at be because been before being both but by can
could did do does doing don for from get give go going got had has have having he her here hi him his
how i i'm if in into is it it's its just know let like me might more most much my myself need no not
now of off on once one only or other our out over own please really right same say she should so some
sound sounds still such tell than thank thanks that the their them then there these they this those
through to too under until up us very want was we well were what when where which while who why will
with would yes yet you your yours hello okay ok actually honestly don't isn't can't i've i'd what's
that's doesn't won't
""".split())

# Without these, Taglish and Bahasa function words ("po", "ninyo", "yang", "aja") dominate scoring
# and every question matches whichever record happens to be written in the caller's language.
PH_STOP_WORDS = EN_STOP_WORDS | set("""
ako akin aking ang anong at ay ba baka dahil dito daw raw din rin ganito ganyan hindi ikaw ito iyan iyon
ano ka kami kay kayo ko kung lamang lang may mayroon meron mga mo muna na naman namin nang naming nating
ng nga ngayon nila nilang nito niya niyang o opo pa pala para pero po pwede puwede sana sa sila siya
talaga tapos tayo yan yun yung akong nyo ninyo kayong nalang
""".split())

ID_STOP_WORDS = EN_STOP_WORDS | set("""
yang saya aku kamu anda bapak ibu pak bu mbak mas bisa boleh kalau jika di ke dari untuk dengan pada
ini itu ada aja saja sih nih dong deh ya iya nggak enggak gak tidak dan atau juga sudah udah belum
mau akan kok kan lagi minta apa apakah berapa kapan gimana bagaimana kenapa mengapa mohon tolong nya
adalah dapat harus masih sangat lebih kurang punya buat soal terus
""".split())

MARKET_STOP_WORDS = {"en": EN_STOP_WORDS, "ph": PH_STOP_WORDS, "id": ID_STOP_WORDS}

# caller phrase -> knowledge-base vocabulary (applied to the raw query, weighted below 1)
#
# For `ph` this bridges languages, not just phrasing: Philippine insurers publish policy documents
# in English while clients speak Taglish, so a Filipino question shares almost no tokens with the
# source text. A multilingual embedding model would generalize this; the table is the explainable
# stand-in. See docs/Q3_MARKETS.md.
EN_EXPANSIONS = {
    r"how much|price|cost|costs|pay for|afford|budget": "premium",
    r"expensive|can'?t afford|cannot afford|too much money": "expensive budget premium",
    r"real person|human|someone|representative|supervisor|manager|speak to|talk to a": "human advisor escalation transfer",
    r"refund|money back|change(?:d)? my mind|back out": "cancel refund free-look",
    r"doctor|hospital|clinic|physician": "provider in-network",
    r"how long|wait\b": "waiting period",
    r"diabetes|asthma|hypertension|blood pressure|heart disease|illness|disease": "pre-existing condition medical",
    r"qualify|eligible|eligibility|apply": "eligibility qualification",
    r"wife|husband|spouse|partner": "spouse family",
    r"kids|children|child|son|daughter|baby|newborn": "dependent children family",
    r"scam|fraud|legit|trust you": "scam verify license",
    r"through (?:my )?(?:job|work)|employer|my company": "employer coverage",
    r"\blate\b|miss(?:ed)? (?:a )?payment|behind on": "grace period lapse payment",
    r"retired|retirement|senior|elderly|older": "senior",
    r"whole year|yearly|annual": "annually discount",
    r"recorded|recording": "recorded consent",
    r"tell me|disclose|have to say|inform me": "state disclosure",
    r"\brequired?\b|requirement|have to|mandatory": "must mandatory",
    r"\bquotes?\b": "price premium",
    r"\bagree\b|consent|okay with": "consent",
    r"medical history|medical information|health information|health details|privacy": "share medical information",
    r"pay out|never pay|paid out": "claims pay",
    r"what do you need|information do you need|details do you need": "qualification questions collect",
    r"\bfile\b|reimburse": "claim",
}

PH_EXPANSIONS = {
    r"magkano|babayaran|bayarin|halaga|premium": "premium payment amount due",
    r"hindi (?:ako )?maka ?bayad|walang pera|wala akong pera|hindi ko kaya|kapos|tight": "grace period unpaid lapse",
    r"mag-?lapse|nag-?lapse|lapse|matitigil|mawawala": "lapse coverage stops riders denied",
    r"rider|riders|dagdag na benefit": "rider accidental critical illness waiver hospital",
    r"beneficiary|benepisyaryo|palitan.*beneficiary": "beneficiary change revocable irrevocable consent",
    r"saan.*(?:bayad|magbayad)|gcash|maya|bayad center|bangko|bank|7-?eleven|palawan": "payment channels bank e-wallet over-the-counter",
    r"kailan|due|deadline|huling araw": "due date grace period premium",
    r"ibalik|balikan|reinstate|buhayin": "reinstate reinstatement unpaid premiums interest underwriting",
    r"grace period|palugit": "grace period thirty-one days",
    r"coverage|sakop|proteksyon": "coverage force policy",
    r"mode|hulugan|monthly|quarterly|buwan-buwan": "payment mode annual semi-annual quarterly monthly anniversary",
    r"claim|makaka-?claim": "claim denied lapse date",
    r"na-?post|posting|pumasok ang bayad": "posting banking days receipt portal",
    r"tao|totoong tao|advisor|agent": "licensed advisor escalation",
    r"scam|totoo ba|peke": "verify hotline policy contract official",
}

ID_EXPANSIONS = {
    r"keringanan|diringankan|dibantu|restruktur|relaksasi": "keringanan restrukturisasi perpanjangan tenor penjadwalan ulang",
    r"denda|telat|terlambat|kena ?denda": "denda keterlambatan persen angsuran tertunggak",
    r"bayar di ?mana|lewat mana|transfer|saluran": "saluran pembayaran virtual account gerai dompet digital",
    r"lunas|pelunasan|lunasi": "pelunasan dipercepat biaya administrasi",
    r"jam berapa|ditelepon|ditagih|nagih": "penagihan pukul etika petugas",
    r"jatuh tempo|tanggal bayar": "jatuh tempo angsuran tenor",
    r"terbukukan|masuk|ke ?catat|udah masuk": "terbukukan pembayaran jam hari kerja",
    r"petugas|orang|manusia|cs": "petugas berlisensi eskalasi",
    r"penipuan|scam|beneran": "call center resmi aplikasi perjanjian",
    r"toleransi|masa tenggang": "masa toleransi hari kalender",
    r"data|otp|pin": "data pribadi dilarang",
}

MARKET_EXPANSIONS = {"en": EN_EXPANSIONS, "ph": PH_EXPANSIONS, "id": ID_EXPANSIONS}


def stem(word: str) -> str:
    """Tiny suffix stripper; applied identically to documents and queries."""
    if len(word) <= 3:
        return word
    for suffix, replacement in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith(("ss", "us", "is")):
                return word
            if suffix == "es" and not word.endswith(("ses", "xes", "ches", "shes")):
                continue
            return word[: -len(suffix)] + replacement
    return word


def tokenize(text: str, market: str = "en") -> list[str]:
    stop = MARKET_STOP_WORDS.get(market, EN_STOP_WORDS)
    words = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text.lower())
    if market == "id":
        # Indonesian clitics carry no meaning for retrieval: "dendanya" -> "denda".
        words = [re.sub(r"(?<=\w{4})(?:nya|ku|mu)$", "", w) for w in words]
    return [stem(t) for t in words if t not in stop]


class KnowledgeBase:
    def __init__(self, market: str = "en") -> None:
        self.market = market
        self.expansions = MARKET_EXPANSIONS.get(market, EN_EXPANSIONS)
        self.records: dict[str, KnowledgeRecord] = {}
        self.chunks: list[Chunk] = []
        self._tf: list[Counter] = []
        self._df: Counter = Counter()
        self._avgdl = 0.0

    def upsert(self, record: KnowledgeRecord) -> None:
        """Store a record and replace chunks from any earlier version of it.

        Records are already section-sized, so chunks follow sentence boundaries and
        overlap by one sentence only when a record exceeds CHUNK_WORDS.
        """
        self.records[record.record_id] = record
        self.chunks = [chunk for chunk in self.chunks if chunk.record_id != record.record_id]
        sentences = re.split(r"(?<=[.!?])\s+", record.content.strip())
        groups: list[list[str]] = [[]]
        for sentence in sentences:
            if groups[-1] and len(" ".join(groups[-1] + [sentence]).split()) > CHUNK_WORDS:
                groups.append([groups[-1][-1]])
            groups[-1].append(sentence)
        for index, group in enumerate(groups):
            self.chunks.append(Chunk(
                chunk_id=f"{record.record_id}#c{index}", record_id=record.record_id, title=record.title,
                content=" ".join(group), source_ref=record.source_ref, category=record.category,
                version=record.version,
            ))
        self._reindex()

    def _reindex(self) -> None:
        self._tf = [Counter(tokenize(c.title, self.market) * TITLE_WEIGHT + tokenize(c.content, self.market))
                    for c in self.chunks]
        self._df = Counter(term for tf in self._tf for term in tf)
        self._avgdl = sum(sum(tf.values()) for tf in self._tf) / max(1, len(self._tf))

    def _idf(self, term: str) -> float:
        n = len(self.chunks)
        return math.log(1 + (n - self._df.get(term, 0) + 0.5) / (self._df.get(term, 0) + 0.5))

    def _query_terms(self, query: str) -> tuple[dict[str, float], dict[str, set[str]]]:
        """Return weighted query terms and, for each original term, the expansion terms that may cover it."""
        weights: dict[str, float] = {t: 1.0 for t in tokenize(query, self.market)}
        covers: dict[str, set[str]] = {t: {t} for t in weights}
        lowered = query.lower()
        for pattern, expansion in self.expansions.items():
            for match in re.finditer(pattern, lowered):
                source = tokenize(match.group(0), self.market)
                expanded = tokenize(expansion, self.market)
                # When the caller's own words never occur in the knowledge base, the expansion
                # is the only possible evidence, so it gets full weight.
                weight = 1.0 if all(t not in self._df for t in source) else EXPANSION_WEIGHT
                for term in expanded:
                    weights[term] = max(weights.get(term, 0.0), weight)
                for term in source:
                    covers.setdefault(term, {term}).update(expanded)
        return weights, covers

    def score(self, query: str) -> list[dict]:
        """Every chunk with BM25 score, coverage and matched terms, best first (one per record)."""
        weights, covers = self._query_terms(query)
        originals = list(covers)
        best: dict[str, dict] = {}
        for chunk, tf in zip(self.chunks, self._tf):
            dl = sum(tf.values())
            matched = [t for t in weights if t in tf]
            if not matched:
                continue
            bm25 = sum(weights[t] * self._idf(t) * tf[t] * (K1 + 1) / (tf[t] + K1 * (1 - B + B * dl / self._avgdl))
                       for t in matched)
            total = sum(self._idf(t) for t in originals) or 1.0
            covered = sum(self._idf(t) for t in originals if covers[t] & set(matched))
            coverage = covered / total
            # Coordination factor: prefer chunks that explain the whole question over chunks
            # that repeat one of its words many times.
            item = {"chunk": chunk, "score": round(bm25 * (0.5 + 0.5 * coverage), 3),
                    "coverage": round(coverage, 3), "matched": matched}
            if chunk.record_id not in best or item["score"] > best[chunk.record_id]["score"]:
                best[chunk.record_id] = item
        return sorted(best.values(), key=lambda i: i["score"], reverse=True)

    def retrieve(self, query: str, limit: int = 3, grounded_only: bool = False,
                 category: str | None = None) -> RetrievalResult:
        """Ranked chunks for a query.

        grounded_only=True applies the score/coverage gates required before a chunk may be used
        as evidence in a customer-facing answer; operators can search without them.
        """
        ranked = [i for i in self.score(query) if category is None or i["chunk"].category == category]
        if ranked:
            top = ranked[0]["score"]
            ranked = [i for i in ranked if i["score"] >= top * RELATIVE_CUTOFF]
        if grounded_only:
            min_score, min_coverage = MARKET_GATES.get(self.market, (MIN_SCORE, MIN_COVERAGE))
            ranked = [i for i in ranked if i["score"] >= min_score and i["coverage"] >= min_coverage]
        chunks = [i["chunk"].model_copy(update={"score": i["score"], "coverage": i["coverage"]})
                  for i in ranked[:limit]]
        return RetrievalResult(query=query, chunks=chunks)
