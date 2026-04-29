import streamlit as st
import json
import hashlib
import os
from io import BytesIO

import pandas as pd
from pypdf import PdfReader
from openai import OpenAI

st.set_page_config(page_title="Document AI Spike", layout="wide")

st.markdown("""
<style>
.block-container {
    padding-top: 2rem;
    padding-bottom: 2rem;
}

.hero {
    padding: 1.6rem 1.9rem;
    border-radius: 18px;
    background: linear-gradient(135deg, #e0f2fe 0%, #eef2ff 100%);
    border: 1px solid #cbd5e1;
    margin-bottom: 1.5rem;
    color: #0f172a !important;
}

.hero h1 {
    margin-bottom: 0.4rem;
    color: #0f172a !important;
    font-weight: 800;
}

.hero p {
    color: #334155 !important;
    font-size: 1.05rem;
}

.status-good {
    padding: 0.9rem 1rem;
    border-radius: 12px;
    background: #ecfdf5;
    color: #065f46;
    border: 1px solid #a7f3d0;
    margin-bottom: 1rem;
}

.status-bad {
    padding: 0.9rem 1rem;
    border-radius: 12px;
    background: #fef2f2;
    color: #991b1b;
    border: 1px solid #fecaca;
    margin-bottom: 1rem;
}

.small-muted {
    color: #64748b;
    font-size: 0.9rem;
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
    <h1>📄 Risk-aware Document Processing AI</h1>
    <p>PDF hochladen, Text extrahieren, ERP-taugliches JSON erzeugen und automatisch prüfen, ob ein Human Review nötig ist.</p>
</div>
""", unsafe_allow_html=True)

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    st.error("OPENAI_API_KEY ist nicht gesetzt. Bitte im Terminal setzen: export OPENAI_API_KEY='dein_key'")
    st.stop()

REVIEW_AMOUNT_THRESHOLD = 10000
RISK_CUSTOMERS = ["Müller Maschinenbau GmbH"]


def file_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def extract_pdf_text(uploaded_file):
    reader = PdfReader(uploaded_file)
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def parse_document_with_llm(text):
    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "document_type": {
                "type": "string",
                "enum": ["customer_order", "purchase_order", "delivery_note", "unknown"]
            },
            "document_id": {"type": "string"},
            "document_date": {"type": "string"},
            "business_partner": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "role": {"type": "string"},
                    "name": {"type": "string"},
                    "number": {"type": "string"}
                },
                "required": ["role", "name", "number"]
            },
            "positions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "position": {"type": "integer"},
                        "item_number": {"type": "string"},
                        "description": {"type": "string"},
                        "quantity": {"type": "number"},
                        "unit": {"type": "string"},
                        "unit_price": {"type": ["number", "null"]},
                        "line_total": {"type": ["number", "null"]}
                    },
                    "required": [
                        "position",
                        "item_number",
                        "description",
                        "quantity",
                        "unit",
                        "unit_price",
                        "line_total"
                    ]
                }
            },
            "amounts": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "subtotal": {"type": ["number", "null"]},
                    "vat_amount": {"type": ["number", "null"]},
                    "total": {"type": ["number", "null"]},
                    "currency": {"type": "string"}
                },
                "required": ["subtotal", "vat_amount", "total", "currency"]
            },
            "logistics": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "delivery_date": {"type": "string"},
                    "delivery_address": {"type": "string"},
                    "delivery_status": {"type": "string"}
                },
                "required": ["delivery_date", "delivery_address", "delivery_status"]
            },
            "payment": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "terms": {"type": "string"}
                },
                "required": ["terms"]
            },
            "notes": {"type": "string"}
        },
        "required": [
            "document_type",
            "document_id",
            "document_date",
            "business_partner",
            "positions",
            "amounts",
            "logistics",
            "payment",
            "notes"
        ]
    }

    response = client.responses.create(
        model="gpt-4.1-mini",
        input=f"""
Extrahiere aus folgendem deutschen Geschäftsdokument ein ERP-taugliches JSON.

Regeln:
- Keine Werte erfinden.
- Fehlende Textwerte als "" setzen.
- Fehlende Zahlenwerte als null setzen.
- Datum im Format YYYY-MM-DD.
- Deutsche Dezimalzahlen in Punkt-Schreibweise umwandeln.
- Lieferscheine haben normalerweise keine Preise.
- Gib nur die Daten zurück, die im Dokument stehen.

Dokumenttext:
{text}
""",
        text={
            "format": {
                "type": "json_schema",
                "name": "erp_document",
                "schema": schema,
                "strict": True
            }
        }
    )

    return json.loads(response.output_text)


def evaluate_risk(result):
    reasons = []

    total = result.get("amounts", {}).get("total")
    partner_name = result.get("business_partner", {}).get("name", "")

    if total and total > REVIEW_AMOUNT_THRESHOLD:
        reasons.append("Hoher Betrag")

    if partner_name in RISK_CUSTOMERS:
        reasons.append("Geschäftspartner auf Risikoliste")

    if not result.get("positions"):
        reasons.append("Keine Positionen erkannt")

    if result.get("document_type") == "unknown":
        reasons.append("Dokumenttyp unsicher")

    return {
        "requires_human_review": len(reasons) > 0,
        "reasons": reasons
    }


def create_excel(result):
    output = BytesIO()

    header_data = {
        "document_type": result.get("document_type"),
        "document_id": result.get("document_id"),
        "document_date": result.get("document_date"),
        "partner_role": result.get("business_partner", {}).get("role"),
        "partner_name": result.get("business_partner", {}).get("name"),
        "partner_number": result.get("business_partner", {}).get("number"),
        "subtotal": result.get("amounts", {}).get("subtotal"),
        "vat_amount": result.get("amounts", {}).get("vat_amount"),
        "total": result.get("amounts", {}).get("total"),
        "currency": result.get("amounts", {}).get("currency"),
        "delivery_date": result.get("logistics", {}).get("delivery_date"),
        "delivery_address": result.get("logistics", {}).get("delivery_address"),
        "delivery_status": result.get("logistics", {}).get("delivery_status"),
        "payment_terms": result.get("payment", {}).get("terms"),
        "notes": result.get("notes"),
    }

    df_header = pd.DataFrame([header_data])
    df_positions = pd.DataFrame(result.get("positions", []))

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df_header.to_excel(writer, index=False, sheet_name="Dokument")
        df_positions.to_excel(writer, index=False, sheet_name="Positionen")

    output.seek(0)
    return output


if "last_file_hash" not in st.session_state:
    st.session_state.last_file_hash = None

if "text" not in st.session_state:
    st.session_state.text = ""

if "result" not in st.session_state:
    st.session_state.result = None

if "risk" not in st.session_state:
    st.session_state.risk = None


left_top, right_top = st.columns([2, 1])

with left_top:
    uploaded = st.file_uploader("PDF hochladen", type=["pdf"])
    st.markdown(
        '<p class="small-muted">Unterstützt Text-PDFs. Scan-PDFs benötigen später OCR.</p>',
        unsafe_allow_html=True
    )

with right_top:
    st.metric("Review-Grenze", f"{REVIEW_AMOUNT_THRESHOLD:,.0f} €".replace(",", "."))
    st.caption("Dokumente oberhalb dieser Grenze werden markiert.")


if uploaded:
    file_bytes = uploaded.getvalue()
    current_hash = file_hash(file_bytes)

    if current_hash != st.session_state.last_file_hash:
        st.session_state.last_file_hash = current_hash
        st.session_state.result = None
        st.session_state.risk = None

        text = extract_pdf_text(uploaded)
        st.session_state.text = text

        if text.strip():
            st.success("PDF wurde ausgelesen.")

            with st.spinner("KI verarbeitet Dokument..."):
                result = parse_document_with_llm(text)

            st.session_state.result = result
            st.session_state.risk = evaluate_risk(result)

        else:
            st.error("PDF konnte nicht gelesen werden. Eventuell ist es ein Scan.")

    else:
        st.info("Dokument wurde bereits verarbeitet. Kein neuer API-Call.")


if st.session_state.text.strip() and st.session_state.result:
    result = st.session_state.result
    risk = st.session_state.risk

    st.divider()

    kpi1, kpi2, kpi3, kpi4 = st.columns(4)

    kpi1.metric("Dokumenttyp", result.get("document_type", "unknown"))
    kpi2.metric("Dokument-ID", result.get("document_id", "n/a"))
    kpi3.metric("Positionen", len(result.get("positions", [])))

    total = result.get("amounts", {}).get("total")
    kpi4.metric(
        "Gesamtbetrag",
        f"{total:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".") if total else "n/a"
    )

    if risk["requires_human_review"]:
        st.markdown(
            '<div class="status-bad"><b>Human Review erforderlich</b></div>',
            unsafe_allow_html=True
        )
        st.write(risk["reasons"])
    else:
        st.markdown(
            '<div class="status-good"><b>Automatische Verarbeitung möglich</b></div>',
            unsafe_allow_html=True
        )

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Ausgelesener Dokumenttext")
        st.text_area(
            "text",
            st.session_state.text,
            height=520,
            label_visibility="collapsed"
        )

    with col2:
        st.subheader("ERP-JSON")
        st.json(result)

        json_data = json.dumps(result, ensure_ascii=False, indent=2)
        excel_data = create_excel(result)

        d1, d2 = st.columns(2)

        with d1:
            st.download_button(
                "JSON herunterladen",
                data=json_data,
                file_name=f"{result.get('document_id', 'document')}.json",
                mime="application/json",
                use_container_width=True
            )

        with d2:
            st.download_button(
                "Excel herunterladen",
                data=excel_data,
                file_name=f"{result.get('document_id', 'document')}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )