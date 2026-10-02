"""Per-market vocabularies for the ingestion pipeline (Q2 for `en`, Q3 for `ph` and `id`).

Cleaning rules are language- and sector-specific: Indonesian footers, Indonesian month names and
day-first dates, Taglish objection headings, and each market's own terminology. Keeping them here
means one pipeline serves three markets without any market's rules leaking into another's.
"""
from __future__ import annotations

import re

# --- dates -------------------------------------------------------------------------------------

EN_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
EN_MONTH_RE = (r"(Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
               r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)")

# Bahasa Indonesia month names; "Mei", "Agustus", "Oktober", "Desember" differ from English.
ID_MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "mei", "jun", "jul", "agu", "sep", "okt", "nov", "des"])}
ID_MONTH_RE = (r"(Jan(?:uari)?|Feb(?:ruari)?|Mar(?:et)?|Apr(?:il)?|Mei|Jun(?:i)?|Jul(?:i)?|"
               r"Agu(?:stus)?|Sep(?:tember)?|Okt(?:ober)?|Nov(?:ember)?|Des(?:ember)?)")


# --- shared HTML/structure patterns -------------------------------------------------------------

SKIP_CLASS_RE = re.compile(r"cookie|banner|breadcrumb|newsletter|sidebar|promo|testimonial|share", re.I)
PAGE_MARKER_RE = re.compile(r"(?:page \d+ of \d+|halaman \d+ dari \d+)", re.I)


# --- per-market configuration -------------------------------------------------------------------

MARKETS: dict[str, dict] = {
    # ---------------------------------------------------------------- United States, health insurance
    "en": {
        "language": "English",
        "drop_exclamations": True,
        "months": EN_MONTHS,
        "month_re": EN_MONTH_RE,
        "day_first": False,  # US sources write MM/DD/YYYY
        "proper_nouns": ["Essential Care", "Family Shield", "Senior Secure"],
        "glossary": {
            "pre-existing condition": ["pre existing disease", "pre-existing disease", "pre-existing illness",
                                       "pre existing illness", "preexisting condition", "prior medical condition"],
            "premium": ["monthly fee", "premium amount", "monthly cost"],
            "deductible": ["deductable"],
            "out-of-pocket maximum": ["out of pocket limit", "out-of-pocket limit", "oop max", "max out-of-pocket"],
            "in-network provider": ["network hospital", "network provider", "empanelled hospital"],
            "waiting period": ["waiting time"],
            "medical underwriting": ["health underwriting", "med underwriting"],
            "dependent": ["dependant"],
            "Family Shield": ["FamilyShield", "Family-Shield"],
            "license": ["licence"],
        },
        "metadata_re": re.compile(
            r"^(?:(?P<date_key>last updated|updated|effective date|effective)\s*:?\s*(?P<date>.+)|"
            r"document version\s+(?P<version>[\d.]+)|owner\s*:.+)$", re.I),
        "boilerplate_re": re.compile(
            r"all rights reserved|privacy policy|terms of use|we use cookies|skip to (main )?content|"
            r"share on |back to top|subscribe|call us today|do not distribute", re.I),
        "irrelevant_heading_re": re.compile(
            r"share this|subscribe|what our members say|testimonial|still have questions|open roles|"
            r"how to apply|join our team|internal notes|^purpose$", re.I),
        "domain_re": re.compile(
            r"\b(plans?|coverage|covered|covers|premiums?|deductible|polic(y|ies)|claims?|underwriting|eligib\w*|"
            r"enrol\w*|quotes?|copay|dependents?|advisors?|disclosures?|waiting periods?|out-of-pocket|"
            r"applications?|in-network|callback|qualif\w*|insur\w*|caller|lead)\b", re.I),
        "category_rules": [
            ("objection", r"^objection"),
            ("qualification", r"eligib|qualif|intake|quote form"),
            ("policy", r"disclosure|prohibited|escalation|compliance|errata"),
            ("process", r"script|opening"),
        ],
    },

    # ------------------------------------------------- Philippines, life insurance / bancassurance
    "ph": {
        "language": "Taglish (Filipino with English insurance terms)",
        # Filipino and Indonesian use exclamations for ordinary politeness, not marketing.
        "drop_exclamations": False,
        "months": EN_MONTHS,
        "month_re": EN_MONTH_RE,
        "day_first": False,  # Philippine business documents follow the US MM/DD/YYYY convention
        "proper_nouns": ["Darwix Life Philippines", "Bayad Center", "GCash", "Maya"],
        "glossary": {
            # Clients and agents say these in English; keep one canonical spelling for each.
            "grace period": ["grace-period", "grace priod"],
            "lapse": ["lapsed status"],
            "reinstate": ["re-instate", "reinstatement of policy"],
            "premium": ["premium payment amount"],
            "rider": ["riders attached", "attached rider"],
            "payment mode": ["mode of payment", "payment frequency"],
            "beneficiary": ["benificiary"],
            "policy contract": ["policy document", "contract of insurance"],
        },
        "metadata_re": re.compile(
            r"^(?:(?P<date_key>last updated|updated|effective date|effective)\s*:?\s*(?P<date>.+)|"
            r"document version\s+(?P<version>[\d.]+)|owner\s*:.+|issued by\s*:.+)$", re.I),
        "boilerplate_re": re.compile(
            r"all rights reserved|privacy policy|terms of use|we use cookies|skip to (main )?content|"
            r"share on |back to top|subscribe|call our hotline|synthetic company", re.I),
        "irrelevant_heading_re": re.compile(
            r"share this|subscribe|still have questions|careers|internal notes", re.I),
        "domain_re": re.compile(
            r"\b(polic(y|ies)|premiums?|coverage|covered|riders?|lapse[ds]?|grace period|reinstat\w*|"
            r"beneficiar(y|ies)|due date|payment|bayad|gcash|maya|bank|branch|advisors?|claims?|"
            r"underwriting|insurance|anniversary|mode|portal|receipts?)\b", re.I),
        "category_rules": [
            ("objection", r"^objection"),
            ("policy", r"grace|lapse|reinstat|may not do|beneficiary|due date|rider"),
            ("process", r"opening|closing|escalation|stating the reason|bank referral"),
            ("product", r"payment|channel|posting|auto-debit"),
        ],
    },

    # ------------------------------------------------ Indonesia, multifinance / consumer finance
    "id": {
        "language": "Bahasa Indonesia",
        # Filipino and Indonesian use exclamations for ordinary politeness, not marketing.
        "drop_exclamations": False,
        "months": ID_MONTHS,
        "month_re": ID_MONTH_RE,
        "day_first": True,  # Indonesian documents write DD/MM/YYYY: 01/02/2026 is 1 February
        "proper_nouns": ["Darwix Finance Indonesia", "Indomaret", "Alfamart", "GoPay", "OVO", "DANA",
                         "ShopeePay", "OJK"],
        "glossary": {
            # Nasabah use these words; normalize spelling variants without translating them.
            "angsuran": ["angsuran bulanan"],
            "cicilan": ["cicilan bulanan"],
            "jatuh tempo": ["tanggal jatuh tempo pembayaran"],
            "denda": ["denda keterlambatan pembayaran"],
            "tenor": ["jangka waktu pembiayaan"],
            "pembiayaan": ["fasilitas pembiayaan"],
            "autodebet": ["auto debet", "auto-debet", "autodebit"],
            "virtual account": ["virtual akun", "VA"],
            "keringanan": ["relaksasi"],
        },
        "metadata_re": re.compile(
            r"^(?:(?P<date_key>terakhir diperbarui|diperbarui|tanggal berlaku|berlaku)\s*:?\s*(?P<date>.+)|"
            r"versi dokumen\s+(?P<version>[\d.]+)|pemilik dokumen\s*:.+|diterbitkan oleh\s*:.+)$", re.I),
        "boilerplate_re": re.compile(
            r"hak cipta dilindungi|kebijakan privasi|syarat dan ketentuan|kami menggunakan cookie|"
            r"terima semua|hubungi call center kami sekarang|perusahaan fiktif", re.I),
        "irrelevant_heading_re": re.compile(
            r"masih ada pertanyaan|bagikan|berlangganan|karier|catatan internal", re.I),
        "domain_re": re.compile(
            r"\b(angsuran|cicilan|denda|tenor|jatuh tempo|pembiayaan|pembayaran|bayar|nasabah|keringanan|"
            r"restrukturisasi|pelunasan|virtual account|autodebet|petugas|penagihan|kontrak|perjanjian|"
            r"toleransi|rekening|dompet digital|gerai)\b", re.I),
        "category_rules": [
            ("objection", r"^keberatan"),
            ("policy", r"etika|denda|jatuh tempo|data pribadi|eskalasi|keringanan|pelunasan|toleransi"),
            ("process", r"pembukaan|penutup|menyampaikan|logat|panduan"),
            ("product", r"saluran|pembayaran|virtual account|gerai|dompet|autodebet|konfirmasi"),
        ],
    },
}


def vocab(market: str) -> dict:
    if market not in MARKETS:
        raise KeyError(f"unknown market {market!r}; known: {sorted(MARKETS)}")
    return MARKETS[market]
