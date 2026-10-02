"""Q3: Philippines and Indonesia market bots. Offline and deterministic."""
import pytest

from app.flows import flow
from app.ingest import load_records
from app.kb import KnowledgeBase
from app.reminder_agent import ReminderAgent, extract_payment, switched_language
from app.crm import MockCRM


@pytest.fixture(scope="module")
def kbs():
    out = {}
    for market in ("ph", "id"):
        kb = KnowledgeBase(market=market)
        for record in load_records(market=market):
            kb.upsert(record)
        out[market] = kb
    return out


class Mute:
    """Worst case: the model contributes nothing, so only the scripted flow runs."""

    def converse_reminder(self, *a, **k):
        return {"reply": "", "citations": [], "intent": "provide_info"}


class Scripted:
    def __init__(self, replies):
        self.replies = list(replies)

    def converse_reminder(self, *a, **k):
        return {"reply": "", "citations": [], "intent": "provide_info", **self.replies.pop(0)}


def make(kbs, tmp_path, brain=None):
    return ReminderAgent(kbs, brain or Mute(), MockCRM(tmp_path / "crm"), None)


def run(agent, market, turns, call_id="c"):
    agent.start(call_id, market)
    return [agent.turn(call_id, t, market) for t in turns]


# --- flows reach their own outcomes, which have no Q1 equivalent

@pytest.mark.parametrize("market,turns,channel,date", [
    ("ph", ["Opo.", "Sa GCash po.", "Sa kinsenas po."], "GCash", "kinsenas"),
    ("id", ["Iya boleh.", "Lewat Indomaret.", "Besok."], "Indomaret", "Besok"),
])
def test_promise_to_pay_is_captured_and_recorded(kbs, tmp_path, market, turns, channel, date):
    agent = make(kbs, tmp_path)
    out = run(agent, market, turns)[-1]
    assert out["outcome"]["status"] == "promise_to_pay"
    assert out["captured"] == {"payment_channel": channel, "payment_date": date}
    assert [a["type"] for a in out["actions"]] == ["promise"]
    assert agent.crm.list("promises")[0]["channel"] == channel


@pytest.mark.parametrize("market,text", [("ph", "Nagbayad na po ako kahapon."), ("id", "Sudah saya bayar kemarin.")])
def test_already_paid_ends_the_call_politely(kbs, tmp_path, market, text):
    agent = make(kbs, tmp_path)
    out = run(agent, market, ["Opo.", text])[-1]
    assert out["outcome"]["status"] == "already_paid" and out["end_call"]
    assert [a["type"] for a in out["actions"]] == ["note"]


@pytest.mark.parametrize("market,text", [("ph", "Wala po akong pera ngayon."), ("id", "Belum ada uang, Mbak.")])
def test_hardship_is_referred_not_pressured(kbs, tmp_path, market, text):
    agent = make(kbs, tmp_path)
    out = run(agent, market, ["Opo.", text])[-1]
    assert out["outcome"]["status"] == "hardship_referred" and out["escalated"]
    assert any(a["type"] == "escalation" for a in out["actions"])


@pytest.mark.parametrize("market,text", [("ph", "Gusto ko pong makausap ang tao."), ("id", "Mau bicara dengan petugas.")])
def test_human_request_escalates_with_local_hours(kbs, tmp_path, market, text):
    out = run(make(kbs, tmp_path), market, ["Opo.", text])[-1]
    assert out["outcome"]["status"] == "escalated" and out["end_call"]
    assert out["citations"] == [flow(market).escalation_ref]


@pytest.mark.parametrize("market,text", [("ph", "Ayaw ko pong ma-record."), ("id", "Jangan direkam ya.")])
def test_declining_recording_ends_the_automated_call(kbs, tmp_path, market, text):
    out = run(make(kbs, tmp_path), market, [text])[-1]
    assert out["outcome"]["status"] == "recording_declined" and out["end_call"]
    assert [a["type"] for a in out["actions"]] == ["callback"]


@pytest.mark.parametrize("market,text", [("ph", "Mali po kayo ng tawag, hindi ako si Juan."),
                                         ("id", "Salah sambung, Pak.")])
def test_wrong_person_closes_without_discussing_the_debt(kbs, tmp_path, market, text):
    out = run(make(kbs, tmp_path), market, [text])[-1]
    assert out["outcome"]["status"] == "wrong_person" and out["end_call"]
    assert out["actions"] == []


# --- language and localization

def test_every_scripted_line_is_in_the_market_language(kbs, tmp_path):
    for market in ("ph", "id"):
        for status, line in flow(market).script.items():
            assert not switched_language(market, line), f"{market}/{status} is not in-language"
        assert not switched_language(market, flow(market).greeting)
        assert not switched_language(market, flow(market).reason)


def test_english_reply_is_dropped_rather_than_spoken(kbs, tmp_path):
    brain = Scripted([{"reply": "Sorry, I do not have that information in my records right now."}])
    out = run(make(kbs, tmp_path, brain), "ph", ["Magkano po ang babayaran ko?"])[-1]
    assert "Sorry, I do not have" not in out["text"]
    assert any(g["guard"] == "language_switch" for g in out["guard_events"])


def test_payday_and_local_channels_are_understood(kbs, tmp_path):
    assert extract_payment("ph", "Sa kinsenas po, sa Bayad Center") == {
        "payment_channel": "Bayad Center", "payment_date": "kinsenas"}
    assert extract_payment("ph", "sa katapusan po")["payment_date"] == "katapusan"
    assert extract_payment("id", "pas gajian aja Pak")["payment_date"] == "gajian"
    assert extract_payment("id", "tanggal 25 lewat BCA") == {
        "payment_channel": "virtual account", "payment_date": "tanggal 25"}
    # "Sa GCash" must not be read as a date just because it starts with "sa".
    assert "payment_date" not in extract_payment("ph", "Sa GCash po.")


def test_regional_and_colloquial_forms_are_recognized(kbs, tmp_path):
    intents = flow("id").intents
    assert intents["promise"].search("nggih, besok saya bayar")      # Javanese
    assert intents["agree"].search("mangga, silakan")                 # Sundanese
    assert intents["hardship"].search("lagi susah Pak, belum gajian")  # colloquial
    assert intents["already_paid"].search("udah bayar kemarin kok")


# --- retrieval in the caller's language

@pytest.mark.parametrize("market,query,expect_grounded", [
    ("ph", "Ano po mangyayari kung hindi ako makabayad?", True),
    ("ph", "Pwede po ba akong magbayad sa GCash?", True),
    ("ph", "Pwede ko po bang ibalik ang na-lapse na policy?", True),
    ("ph", "May car insurance po ba kayo?", False),
    ("ph", "Ano po ang lagay ng panahon bukas?", False),
    ("id", "Dendanya berapa kalau telat?", True),
    ("id", "Bisa nggak saya minta keringanan?", True),
    ("id", "Bisa lewat Indomaret?", True),
    ("id", "Ada asuransi mobil nggak?", False),
    ("id", "Cuaca besok gimana ya?", False),
])
def test_grounding_gates_hold_in_both_languages(kbs, market, query, expect_grounded):
    chunks = kbs[market].retrieve(query, grounded_only=True).chunks
    assert bool(chunks) is expect_grounded


def test_amounts_are_never_invented(kbs, tmp_path):
    brain = Scripted([{"reply": "Ang babayaran po ninyo ay 12,345 pesos."}])
    out = run(make(kbs, tmp_path, brain), "ph", ["Magkano po?"])[-1]
    assert "12,345" not in out["text"]
    assert any(g["guard"] == "ungrounded_number" for g in out["guard_events"])


@pytest.mark.parametrize("market,reply,banned", [
    ("ph", "Cancelled na po ang policy ninyo.", "Cancelled na"),
    ("ph", "Siguradong ma-reinstate po agad ang policy ninyo.", "Siguradong ma-reinstate"),
    ("id", "Kalau tidak bayar, unitnya akan disita.", "disita"),
    ("id", "Keringanannya pasti disetujui kok, Pak.", "pasti disetujui"),
])
def test_market_compliance_rewrites(kbs, tmp_path, market, reply, banned):
    out = run(make(kbs, tmp_path, Scripted([{"reply": reply}])), market, ["Opo."])[-1]
    assert banned.lower() not in out["text"].lower()
    assert any(g["guard"] == "prohibited_statement" for g in out["guard_events"])


def test_pipeline_survives_the_observed_sundanese_asr_errors(kbs, tmp_path):
    """Whisper merges Sundanese word boundaries ("Punten Teh" -> "Puntenteh"); the terms the flow
    depends on still survive, so the call continues. Measured in data/eval/asr_results.json."""
    garbled = "Puntenteh, Abdi Tuacan Gajian, Mangga Minggu Depan."
    assert extract_payment("id", garbled)["payment_date"].lower() == "gajian"
    from app.reminder_agent import detect_intent
    assert detect_intent(flow("id"), garbled) == "promise"


def test_playbook_placeholders_are_never_spoken(kbs, tmp_path):
    """Both gpt-oss models echoed the playbook slot "[grace end date]" verbatim."""
    brain = Scripted([{"reply": "Naiintindihan ko po. Hanggang [grace end date] pa po ang grace period ninyo."}])
    agent = make(kbs, tmp_path, brain)
    out = run(agent, "ph", ["Ano po ang grace period?"])[-1]   # a plain turn, so the guard runs
    assert "[" not in out["text"] and "grace end date" not in out["text"]
    assert any(g["guard"] == "unfilled_placeholder" for g in agent.sessions["c"].guard_events)


@pytest.mark.parametrize("text,expected", [
    ("Ano po ang mangyayari kung hindi ako makabayad?", None),   # a question, not a dispute
    ("Hindi po ako makabayad ngayong buwan.", None),
    ("Mali po ang record ninyo, hindi ko po utang 'yan.", "dispute"),
    ("Hindi po ako si Juan, mali po kayo ng tawag.", "wrong_person"),
])
def test_dispute_is_not_triggered_by_ordinary_negation(text, expected):
    from app.reminder_agent import detect_intent
    assert detect_intent(flow("ph"), text) == expected


def test_price_objection_is_answered_not_escalated(kbs, tmp_path):
    """"Mahal masyado" asks for a cheaper payment mode; only real hardship is referred."""
    from app.reminder_agent import detect_intent
    assert detect_intent(flow("ph"), "Mahal po masyado ang premium, pwede po bang gawing quarterly?") is None
    assert detect_intent(flow("ph"), "Wala po akong pera ngayon.") == "hardship"
    assert detect_intent(flow("id"), "Dendanya kok mahal ya?") is None
    assert detect_intent(flow("id"), "Belum ada uang nih Mbak.") == "hardship"
