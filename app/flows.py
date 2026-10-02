"""Q3 market flows: Philippines renewal reminder and Indonesia installment reminder.

These are servicing calls, not the US lead-qualification flow. The outcomes a reminder call can
reach (promise to pay, already paid, hardship, dispute) have no counterpart in Q1, so each market
gets its own flow definition rather than a translated copy of the Q1 rules.

Every spoken line here is taken from that market's approved call playbook in the knowledge base
(`data/raw/<market>/docs/*playbook*|*panduan*`), so the script and the retrievable guidance cannot
drift apart. Intent patterns are written in the language the caller actually speaks, including
colloquial and regional forms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Flow:
    market: str
    language: str          # how the model is told to speak
    sector: str
    name: str
    greeting: str
    reason: str            # why we are calling, said after consent
    questions: dict        # field -> spoken question
    collect_order: list
    script: dict           # outcome -> closing line
    intents: dict          # intent -> compiled pattern over the caller's words
    prohibited: list       # (compiled pattern, replacement) compliance rewrites
    escalation_ref: str
    policy_refs: dict


# --------------------------------------------------------------------------- Philippines

PH = Flow(
    market="ph",
    language=("natural Taglish: Filipino sentence structure with English kept for insurance and "
              "banking terms (premium, policy, coverage, rider, lapse, grace period, due date, "
              "beneficiary, reinstate). Always use po and opo. Never translate the insurance terms."),
    sector="life insurance / bancassurance",
    name="renewal_lapse_reminder",
    greeting=("Magandang araw po, ako po si Maya mula sa Darwix Life Philippines. Recorded po ang tawag "
              "na ito para sa quality at compliance. Okay lang po ba na mag-usap tayo sandali?"),
    reason=("Tungkol po sana ito sa premium ng policy ninyo na may due na. Nasa loob pa po tayo ng 31-day "
            "grace period, kaya active pa rin po ang coverage ninyo. Nabayaran na po ba ninyo ito, o gusto "
            "po ninyong tulungan ko kayo sa payment?"),
    questions={
        "payment_channel": ("Saan po ninyo balak magbayad — sa GCash, Maya, bank branch, o sa Bayad Center?"),
        "payment_date": "Kailan po ninyo balak magbayad?",
        "callback_number": "Anong number po ang pwede naming tawagan para sa confirmation?",
    },
    collect_order=["payment_channel", "payment_date", "callback_number"],
    script={
        "already_paid": ("Ay, salamat po sa pagsabi! Minsan po kasi hanggang two banking days bago mag-post "
                         "ang bayad. Hindi ko na po kayo aabalahin — mag-re-reflect din po 'yan sa system. "
                         "Salamat po sa oras ninyo at ingat po kayo."),
        "hardship_referred": ("Naiintindihan ko po, mahirap talaga ngayon. Para po matulungan kayo nang maayos, "
                              "ikokonekta ko po kayo sa licensed advisor namin na pwedeng mag-check ng options "
                              "tulad ng pagpapalit ng payment mode. Hindi po ako pwedeng mangako ng approval."),
        "disputed": ("Pasensya na po sa abala. Ipapasa ko po ito sa licensed advisor namin para ma-review nang "
                     "maayos ang record ninyo."),
        "refused": ("Naiintindihan ko po ang desisyon ninyo. Paalala lang po, kapag nag-lapse ang policy ay "
                    "titigil din po ang lahat ng riders. Salamat po sa oras ninyo at ingat po kayo."),
        "escalated": ("Sige po, ikokonekta ko po kayo sa licensed advisor namin. Available po sila Lunes hanggang "
                      "Biyernes, 8 AM hanggang 6 PM, at Sabado, 9 AM hanggang 12 NN. Kung wala pong available "
                      "ngayon, tatawagan po kayo within one business day."),
        "recording_declined": ("Naiintindihan ko po. Itatapos ko na po ang automated call na ito, at ipapa-schedule "
                               "ko na lang po na tawagan kayo ng licensed advisor namin. Salamat po."),
        "promise_to_pay": ("Salamat po! Para po sa record ko, magbabayad po kayo sa {payment_channel} sa "
                           "{payment_date}, tama po ba? Maraming salamat po at ingat po kayo."),
        "wrong_person": ("Pasensya na po sa abala. Itatama po namin ang record namin. Salamat po."),
    },
    intents={
        "human_request": re.compile(r"\b(tao|totoong tao|real person|human|agent|advisor|supervisor|manager|"
                                    r"makausap|kausapin ko|ibigay mo sa)\b", re.I),
        "already_paid": re.compile(r"\b(nagbayad na|bayad na|nabayaran ko na|na-pay ko na|binayaran ko na|"
                                   r"paid na|na-settle ko na)\b", re.I),
        # "Mahal masyado" is a price objection, not hardship: the playbook answers it by offering a
        # payment-mode change. Only an actual inability to pay is referred to an advisor.
        # ASR sometimes drops the leading W of "wala", so "ala akong pera" is accepted too.
        "hardship": re.compile(r"\b(w?ala\s+(?:akong|ako|kaming|po akong)?\s*pera|walang pera|tight ang budget|"
                               r"nahihirapan|hindi ko kaya|kapos|kulang ang budget|walang pambayad)\b", re.I),
        # "hindi ako" alone is far too broad: "hindi ako makabayad" ("I can't pay") is a question
        # about the grace period, not a dispute.
        "dispute": re.compile(r"\b(mali ang (?:record|bill|singil)|hindi ko utang|hindi ko policy|reklamo|"
                              r"complaint|hindi ako ang|hindi ako si|nagkamali kayo|bakit ako sinisingil)\b", re.I),
        "refuse": re.compile(r"\b(ayoko na|ayaw ko na|cancel ko na|huwag na|wag na|hindi na ako interesado|"
                             r"itigil n?[iy]o)\b", re.I),
        "promise": re.compile(r"\b(magbabayad|babayaran ko|babayaran na|sige|oo nga|magbabayad na ako|"
                              r"this week|bukas|sa|next week)\b", re.I),
        "decline_recording": re.compile(r"\b(?:ayaw|ayoko|huwag|wag|hindi)\b[^.!?]{0,25}"
                                        r"\b(?:i-?record|ma-?record|irecord|nire-?record|record)\b", re.I),
        "wrong_person": re.compile(r"\b(mali po kayo ng tawag|wala dito|hindi ko kilala|wrong number|"
                                   r"hindi ako si)\b", re.I),
        "agree": re.compile(r"^\W*(opo|oo|sige|okay|ok|yes|pwede|go ahead|tuloy)\b", re.I),
    },
    prohibited=[
        # Never say coverage has already stopped while the policy is still inside the grace period.
        (re.compile(r"[^.!?]*\b(cancelled na|kanselado na|wala na pong coverage|expired na ang policy)\b[^.!?]*[.!?]?", re.I),
         " Nasa loob pa po tayo ng grace period, kaya active pa rin po ang coverage ninyo."),
        # Reinstatement is an underwriting decision, never a promise on a call.
        (re.compile(r"[^.!?]*\b(siguradong ma-?reinstate|guaranteed na ma-?reinstate|basta magbayad kayo ay "
                    r"babalik agad)\b[^.!?]*[.!?]?", re.I),
         " Ang reinstatement po ay subject sa underwriting approval, hindi po automatic."),
    ],
    escalation_ref="docs/renewal_lapse_policy.txt#human-escalation",
    policy_refs={
        "grace": "docs/renewal_lapse_policy.txt#grace-period",
        "lapse": "docs/renewal_lapse_policy.txt#what-lapse-means-for-the-client",
        "conduct": "docs/renewal_lapse_policy.txt#what-agents-and-automated-calls-may-not-do",
    },
)


# --------------------------------------------------------------------------- Indonesia

ID = Flow(
    market="id",
    language=("Bahasa Indonesia. Match the caller's register: formal (Bapak/Ibu, saya) by default, "
              "santai (Pak/Bu, aku/saya, 'udah', 'nih') if the caller speaks casually. Keep finance "
              "loanwords as nasabah say them: cicilan, angsuran, tenor, denda, DP, jatuh tempo, "
              "pembiayaan, transfer, virtual account, autodebet."),
    sector="multifinance / consumer finance",
    name="installment_reminder",
    greeting=("Selamat siang, Bapak/Ibu. Saya Rani dari Darwix Finance. Panggilan ini direkam untuk "
              "keperluan kualitas dan kepatuhan. Apakah sekarang waktu yang tepat untuk berbicara sebentar?"),
    reason=("Saya ingin menginformasikan mengenai angsuran pembiayaan Bapak/Ibu yang sudah jatuh tempo dan "
            "tercatat belum terbayar. Masih ada masa toleransi 3 hari sebelum denda mulai dihitung. Apakah "
            "angsurannya sudah dibayar, atau boleh saya bantu untuk pembayarannya?"),
    questions={
        "payment_channel": ("Rencananya mau bayar lewat mana — virtual account, Indomaret, Alfamart, atau "
                            "dompet digital seperti GoPay atau OVO?"),
        "payment_date": "Kira-kira kapan rencana pembayarannya?",
        "callback_number": "Nomor telepon yang aktif untuk konfirmasi, nomor ini ya?",
    },
    collect_order=["payment_channel", "payment_date", "callback_number"],
    script={
        "already_paid": ("Oh, terima kasih infonya. Pembayaran memang bisa perlu waktu sampai terbukukan di "
                         "sistem. Kalau begitu tidak perlu saya ganggu lagi, nanti otomatis ter-update. "
                         "Terima kasih waktunya."),
        "hardship_referred": ("Saya mengerti, kondisi bisa berubah. Supaya bisa dibantu dengan benar, saya "
                              "hubungkan ke petugas kami yang bisa memproses pengajuan keringanan seperti "
                              "perpanjangan tenor atau penjadwalan ulang angsuran. Saya tidak bisa menjanjikan "
                              "disetujui ya, Pak/Bu."),
        "disputed": ("Mohon maaf atas ketidaknyamanannya. Keberatan Bapak/Ibu saya catat dan saya teruskan ke "
                     "petugas kami supaya ditindaklanjuti dengan benar."),
        "refused": ("Baik, saya catat keberatan Bapak/Ibu dan tidak akan saya lanjutkan sekarang. Terima kasih "
                    "atas waktunya."),
        "escalated": ("Baik, saya hubungkan dengan petugas kami ya. Petugas tersedia Senin sampai Jumat pukul "
                      "08.00 sampai 17.00 WIB dan Sabtu pukul 09.00 sampai 13.00 WIB. Kalau sekarang tidak ada "
                      "yang tersedia, petugas kami akan menghubungi dalam 1 hari kerja."),
        "recording_declined": ("Baik, saya mengerti. Panggilan otomatis ini saya akhiri dan akan saya jadwalkan "
                               "agar petugas kami yang menghubungi Bapak/Ibu. Terima kasih."),
        "promise_to_pay": ("Baik, saya catat ya: pembayaran melalui {payment_channel} pada {payment_date}. "
                           "Benar begitu, Pak/Bu? Terima kasih banyak, selamat beraktivitas kembali."),
        "wrong_person": ("Mohon maaf atas gangguannya, data kami akan kami perbaiki. Terima kasih."),
    },
    intents={
        # Colloquial and regional forms matter here: "udah" (sudah), "nggih" (Javanese yes),
        # "muhun" (Sundanese yes), "teu acan" (Sundanese not yet).
        "human_request": re.compile(r"\b(petugas|orang|manusia|customer service|cs|atasan|supervisor|"
                                    r"bicara dengan|ngomong sama orang)\b", re.I),
        "already_paid": re.compile(r"\b(sudah (saya )?(bayar|transfer|lunas)|udah (bayar|transfer|lunas)|"
                                   r"sampun (bayar|mbayar)|tadi sudah bayar|kemarin sudah bayar)\b", re.I),
        "hardship": re.compile(r"\b(belum ada uang|gak ada uang|nggak ada uang|lagi susah|lagi berat|"
                               r"kesulitan|belum gajian|lagi seret|dereng wonten|teu acan aya)\b", re.I),
        "dispute": re.compile(r"\b(keberatan|salah|komplain|protes|bukan saya|saya tidak (pernah )?(pinjam|ambil)|"
                              r"kok bisa)\b", re.I),
        "refuse": re.compile(r"\b(jangan telepon|jangan hubungi|tidak mau|gak mau|nggak mau|stop|berhenti "
                             r"menelepon|mboten purun)\b", re.I),
        "promise": re.compile(r"\b(nanti (saya )?bayar|akan (saya )?bayar|besok bayar|minggu depan|iya bayar|"
                              r"saya bayar|bayar nanti|insya ?allah|nggih|muhun|siap)\b", re.I),
        "decline_recording": re.compile(r"\b(jangan direkam|tidak mau direkam|gak mau direkam|"
                                        r"keberatan direkam)\b", re.I),
        "wrong_person": re.compile(r"\b(salah sambung|salah nomor|bukan nomor|tidak kenal|saya bukan)\b", re.I),
        "agree": re.compile(r"^\W*(iya|ya|baik|boleh|silakan|silahkan|oke|ok|monggo|mangga|nggih|muhun)\b", re.I),
    },
    prohibited=[
        # Collections conduct (OJK-style): no threats, no third parties, no promised approvals.
        (re.compile(r"[^.!?]*\b(disita|kami sita|akan kami ambil|datang ke rumah|lapor polisi|"
                    r"hubungi (atasan|keluarga|tetangga) anda)\b[^.!?]*[.!?]?", re.I),
         " Mohon maaf, kami hanya mengingatkan pembayaran dan tidak melakukan hal tersebut melalui telepon."),
        (re.compile(r"[^.!?]*\b(pasti disetujui|dijamin disetujui|keringanannya pasti)\b[^.!?]*[.!?]?", re.I),
         " Pengajuan keringanan diproses petugas kami dan tidak otomatis disetujui."),
    ],
    escalation_ref="docs/kebijakan_angsuran.txt#eskalasi-ke-petugas",
    policy_refs={
        "denda": "docs/kebijakan_angsuran.txt#denda-keterlambatan",
        "toleransi": "docs/kebijakan_angsuran.txt#masa-toleransi",
        "conduct": "docs/kebijakan_angsuran.txt#etika-penagihan-mengacu-pada-ketentuan-ojk",
    },
)


FLOWS = {"ph": PH, "id": ID}


def flow(market: str) -> Flow:
    if market not in FLOWS:
        raise KeyError(f"no Q3 flow for market {market!r}; known: {sorted(FLOWS)}")
    return FLOWS[market]
