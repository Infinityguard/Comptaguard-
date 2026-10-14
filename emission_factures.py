"""Tickets de pesée et émission de factures (FCFA), avec numérotation continue et traçabilité.

Principes :
- le ticket de pesée est la pièce source : il reprend tous les champs du ticket papier ;
- le poids net est recalculé (|P1 - P2|) et comparé au poids net imprimé ;
- une facture émise n'est jamais modifiée ni supprimée : on l'annule (avec motif), ce qui libère les tickets ;
- le code MECeF / NIM peut être renseigné après normalisation auprès de la DGI (le logiciel ne se substitue pas
  au dispositif de facturation normalisée).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO

from security_db import connect, log_event

FCFA = Decimal("1")
DEFAULT_VAT = Decimal("0.18")

TICKET_FIELDS = [
    "ticket_no", "site", "campaign", "date_p1", "time_p1", "date_p2", "time_p2", "vehicle", "trailer",
    "movement", "product", "humidity_rate", "client", "supplier", "transporter", "origin", "destination",
    "quart", "commune", "evacuation_bd", "enlevement_no", "choice", "variety", "cvpc", "market",
    "other_market", "weight_p1", "weight_p2", "observations", "weigher", "driver", "driver_permit",
]


def _dec(value, label="Valeur") -> Decimal:
    try:
        out = Decimal(str(value).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label} invalide.")
    if not out.is_finite():
        raise ValueError(f"{label} invalide.")
    return out


def _fcfa(value: Decimal) -> Decimal:
    return value.quantize(FCFA, rounding=ROUND_HALF_UP)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
def init_invoicing_db() -> None:
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_no TEXT NOT NULL UNIQUE,
            invoice_date TEXT NOT NULL,
            due_date TEXT NOT NULL DEFAULT '',
            client_name TEXT NOT NULL,
            client_ifu TEXT NOT NULL DEFAULT '',
            client_address TEXT NOT NULL DEFAULT '',
            vat_rate TEXT NOT NULL,
            total_ht TEXT NOT NULL,
            total_vat TEXT NOT NULL,
            total_ttc TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Émise' CHECK(status IN ('Émise','Annulée')),
            mecef_code TEXT NOT NULL DEFAULT '',
            mecef_nim TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            cancelled_by TEXT NOT NULL DEFAULT '',
            cancelled_at TEXT NOT NULL DEFAULT '',
            cancel_reason TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS weighing_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_no TEXT NOT NULL,
            site TEXT NOT NULL DEFAULT '',
            campaign TEXT NOT NULL DEFAULT '',
            date_p1 TEXT NOT NULL DEFAULT '', time_p1 TEXT NOT NULL DEFAULT '',
            date_p2 TEXT NOT NULL DEFAULT '', time_p2 TEXT NOT NULL DEFAULT '',
            vehicle TEXT NOT NULL DEFAULT '', trailer TEXT NOT NULL DEFAULT '',
            movement TEXT NOT NULL DEFAULT '', product TEXT NOT NULL DEFAULT '',
            humidity_rate TEXT NOT NULL DEFAULT '',
            client TEXT NOT NULL DEFAULT '', supplier TEXT NOT NULL DEFAULT '',
            transporter TEXT NOT NULL DEFAULT '',
            origin TEXT NOT NULL DEFAULT '', destination TEXT NOT NULL DEFAULT '',
            quart TEXT NOT NULL DEFAULT '', commune TEXT NOT NULL DEFAULT '',
            evacuation_bd TEXT NOT NULL DEFAULT '', enlevement_no TEXT NOT NULL DEFAULT '',
            choice TEXT NOT NULL DEFAULT '', variety TEXT NOT NULL DEFAULT '',
            cvpc TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '',
            other_market TEXT NOT NULL DEFAULT '',
            weight_p1 INTEGER NOT NULL, weight_p2 INTEGER NOT NULL, net_weight INTEGER NOT NULL,
            observations TEXT NOT NULL DEFAULT '',
            weigher TEXT NOT NULL DEFAULT '', driver TEXT NOT NULL DEFAULT '',
            driver_permit TEXT NOT NULL DEFAULT '',
            invoice_id INTEGER REFERENCES invoices(id),
            created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            UNIQUE(site, campaign, ticket_no)
        );
        CREATE TABLE IF NOT EXISTS invoice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id),
            line_no INTEGER NOT NULL,
            ticket_id INTEGER REFERENCES weighing_tickets(id),
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit TEXT NOT NULL,
            unit_price TEXT NOT NULL,
            amount_ht TEXT NOT NULL,
            UNIQUE(invoice_id, line_no)
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_invoice ON weighing_tickets(invoice_id);

        CREATE TRIGGER IF NOT EXISTS invoices_no_delete BEFORE DELETE ON invoices
        BEGIN SELECT RAISE(ABORT, 'Une facture ne peut pas être supprimée : l''annuler avec un motif.'); END;
        CREATE TRIGGER IF NOT EXISTS invoices_frozen BEFORE UPDATE ON invoices
        WHEN OLD.status = 'Annulée'
          OR OLD.invoice_no <> NEW.invoice_no OR OLD.invoice_date <> NEW.invoice_date
          OR OLD.client_name <> NEW.client_name OR OLD.vat_rate <> NEW.vat_rate
          OR OLD.total_ht <> NEW.total_ht OR OLD.total_vat <> NEW.total_vat OR OLD.total_ttc <> NEW.total_ttc
        BEGIN SELECT RAISE(ABORT, 'Une facture émise est figée ; seule l''annulation et le code MECeF sont possibles.'); END;
        CREATE TRIGGER IF NOT EXISTS invoice_lines_frozen_u BEFORE UPDATE ON invoice_lines
        BEGIN SELECT RAISE(ABORT, 'Les lignes de facture sont immuables.'); END;
        CREATE TRIGGER IF NOT EXISTS invoice_lines_frozen_d BEFORE DELETE ON invoice_lines
        BEGIN SELECT RAISE(ABORT, 'Les lignes de facture sont immuables.'); END;
        CREATE TRIGGER IF NOT EXISTS tickets_no_delete BEFORE DELETE ON weighing_tickets
        BEGIN SELECT RAISE(ABORT, 'Un ticket de pesée ne peut pas être supprimé.'); END;
        CREATE TRIGGER IF NOT EXISTS tickets_frozen BEFORE UPDATE ON weighing_tickets
        WHEN OLD.ticket_no <> NEW.ticket_no OR OLD.weight_p1 <> NEW.weight_p1 OR OLD.weight_p2 <> NEW.weight_p2
          OR OLD.net_weight <> NEW.net_weight OR OLD.vehicle <> NEW.vehicle OR OLD.client <> NEW.client
        BEGIN SELECT RAISE(ABORT, 'Un ticket de pesée enregistré est immuable.'); END;
        """)


# --------------------------------------------------------------------------
# Paramètres de l'émetteur
# --------------------------------------------------------------------------
SELLER_KEYS = {
    "seller_name": "SOCOPA SA",
    "seller_ifu": "",
    "seller_rccm": "",
    "seller_address": "",
    "seller_phone": "",
    "seller_bank": "",
}


def get_seller() -> dict:
    out = dict(SELLER_KEYS)
    with connect() as con:
        for r in con.execute("SELECT key,value FROM app_settings WHERE key LIKE 'seller_%'"):
            out[r["key"]] = r["value"]
    return out


def save_seller(data: dict, actor: str) -> None:
    with connect() as con:
        for key in SELLER_KEYS:
            if key in data:
                con.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(data[key]).strip()))
    log_event(actor, "PARAM_EMETTEUR_FACTURE", "Mise à jour des informations de l'émetteur")


# --------------------------------------------------------------------------
# Tickets de pesée
# --------------------------------------------------------------------------
def compute_net(p1, p2) -> int:
    a, b = _dec(p1, "Poids P1"), _dec(p2, "Poids P2")
    if a <= 0 or b <= 0:
        raise ValueError("Les poids P1 et P2 doivent être strictement positifs.")
    if a != a.to_integral_value() or b != b.to_integral_value():
        raise ValueError("Les poids sont saisis en kg entiers.")
    return int(abs(a - b))


def create_ticket(data: dict, actor: str, printed_net=None) -> int:
    """Enregistre un ticket de pesée. Le poids net est recalculé et comparé au net imprimé s'il est fourni."""
    ticket_no = str(data.get("ticket_no", "")).strip()
    if not ticket_no:
        raise ValueError("Le numéro du ticket est obligatoire.")
    net = compute_net(data.get("weight_p1"), data.get("weight_p2"))
    if net == 0:
        raise ValueError("Poids net nul : P1 et P2 sont identiques.")
    if printed_net not in (None, ""):
        if int(_dec(printed_net, "Poids net imprimé")) != net:
            raise ValueError(f"Poids net imprimé ({printed_net}) différent de |P1 - P2| = {net} kg. Vérifier la saisie.")
    values = {k: str(data.get(k, "") or "").strip() for k in TICKET_FIELDS}
    values["weight_p1"] = int(_dec(data["weight_p1"]))
    values["weight_p2"] = int(_dec(data["weight_p2"]))
    values["net_weight"] = net
    cols = list(values)
    with connect() as con:
        try:
            cur = con.execute(
                f"INSERT INTO weighing_tickets({','.join(cols)},created_by,created_at) "
                f"VALUES({','.join('?' * len(cols))},?,?)",
                [values[c] for c in cols] + [actor, _now()])
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise ValueError("Ce numéro de ticket existe déjà pour ce site et cette campagne.") from exc
            raise
        ticket_id = cur.lastrowid
    log_event(actor, "CREATION_TICKET_PESEE", f"Ticket={ticket_no}; véhicule={values['vehicle']}; net={net} kg")
    return ticket_id


def list_tickets(only_uninvoiced=False, limit=1000) -> list[dict]:
    sql = "SELECT * FROM weighing_tickets"
    if only_uninvoiced:
        sql += " WHERE invoice_id IS NULL"
    sql += " ORDER BY id DESC LIMIT ?"
    with connect() as con:
        return [dict(r) for r in con.execute(sql, (int(limit),)).fetchall()]


# --------------------------------------------------------------------------
# Factures
# --------------------------------------------------------------------------
def ticket_description(t: dict) -> str:
    parts = [t.get("product") or "Marchandise", t.get("variety"), t.get("choice")]
    head = " — ".join(p for p in parts if p)
    return f"{head} — ticket n° {t['ticket_no']} — véhicule {t.get('vehicle') or '—'} — {t.get('date_p2') or t.get('date_p1') or ''}".strip(" —")


def lines_from_tickets(tickets: list[dict], unit_price) -> list[dict]:
    price = _dec(unit_price, "Prix unitaire")
    return [{"ticket_id": t["id"], "description": ticket_description(t), "quantity": t["net_weight"],
             "unit": "kg", "unit_price": price} for t in tickets]


def compute_totals(lines: list[dict], vat_rate) -> dict:
    rate = _dec(vat_rate, "Taux de TVA")
    if rate < 0 or rate > 1:
        raise ValueError("Le taux de TVA doit être compris entre 0 et 1 (ex. 0,18).")
    norm, total_ht = [], Decimal("0")
    for i, ln in enumerate(lines, 1):
        q, p = _dec(ln["quantity"], "Quantité"), _dec(ln["unit_price"], "Prix unitaire")
        if q <= 0:
            raise ValueError(f"Ligne {i} : quantité strictement positive requise.")
        if p < 0:
            raise ValueError(f"Ligne {i} : prix unitaire négatif impossible.")
        amount = _fcfa(q * p)
        total_ht += amount
        norm.append({**ln, "line_no": i, "quantity": q, "unit_price": p, "amount_ht": amount})
    vat = _fcfa(total_ht * rate)
    return {"lines": norm, "rate": rate, "total_ht": total_ht, "total_vat": vat, "total_ttc": total_ht + vat}


def create_invoice(client_name: str, lines: list[dict], vat_rate, actor: str, invoice_date: str | None = None,
                   client_ifu="", client_address="", due_date="", notes="") -> dict:
    client_name = str(client_name).strip()
    if not client_name:
        raise ValueError("Le nom du client est obligatoire.")
    if not lines:
        raise ValueError("Ajoutez au moins une ligne à la facture.")
    calc = compute_totals(lines, vat_rate)
    date = invoice_date or datetime.now().date().isoformat()
    year = date[:4]
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")  # verrou : numérotation continue sans doublon
        ticket_ids = [ln["ticket_id"] for ln in calc["lines"] if ln.get("ticket_id")]
        if len(ticket_ids) != len(set(ticket_ids)):
            raise ValueError("Un même ticket ne peut figurer qu'une fois sur la facture.")
        for tid in ticket_ids:
            row = con.execute("SELECT ticket_no, invoice_id FROM weighing_tickets WHERE id=?", (int(tid),)).fetchone()
            if row is None:
                raise ValueError("Ticket de pesée introuvable.")
            if row["invoice_id"] is not None:
                raise ValueError(f"Le ticket n° {row['ticket_no']} est déjà facturé.")
        last = con.execute("SELECT invoice_no FROM invoices WHERE invoice_no LIKE ? ORDER BY id DESC LIMIT 1",
                           (f"FAC-{year}-%",)).fetchone()
        seq = int(last["invoice_no"].rsplit("-", 1)[1]) + 1 if last else 1
        number = f"FAC-{year}-{seq:05d}"
        cur = con.execute(
            """INSERT INTO invoices(invoice_no,invoice_date,due_date,client_name,client_ifu,client_address,vat_rate,
               total_ht,total_vat,total_ttc,notes,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (number, date, due_date, client_name, str(client_ifu).strip(), str(client_address).strip(),
             str(calc["rate"]), str(calc["total_ht"]), str(calc["total_vat"]), str(calc["total_ttc"]),
             str(notes).strip()[:1000], actor, _now()))
        inv_id = cur.lastrowid
        for ln in calc["lines"]:
            con.execute("""INSERT INTO invoice_lines(invoice_id,line_no,ticket_id,description,quantity,unit,unit_price,amount_ht)
                           VALUES(?,?,?,?,?,?,?,?)""",
                        (inv_id, ln["line_no"], ln.get("ticket_id"), str(ln["description"]).strip(),
                         str(ln["quantity"]), str(ln.get("unit") or "unité"), str(ln["unit_price"]), str(ln["amount_ht"])))
            if ln.get("ticket_id"):
                con.execute("UPDATE weighing_tickets SET invoice_id=? WHERE id=?", (inv_id, int(ln["ticket_id"])))
    log_event(actor, "EMISSION_FACTURE", f"{number}; client={client_name}; HT={calc['total_ht']}; TTC={calc['total_ttc']}")
    return {"id": inv_id, "invoice_no": number, **{k: calc[k] for k in ("total_ht", "total_vat", "total_ttc")}}


def cancel_invoice(invoice_id: int, reason: str, actor: str) -> None:
    reason = str(reason).strip()
    if len(reason) < 5:
        raise ValueError("Un motif d'annulation d'au moins 5 caractères est obligatoire.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        inv = con.execute("SELECT invoice_no,status,mecef_code FROM invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None:
            raise ValueError("Facture introuvable.")
        if inv["status"] == "Annulée":
            raise ValueError("Cette facture est déjà annulée.")
        con.execute("UPDATE invoices SET status='Annulée', cancelled_by=?, cancelled_at=?, cancel_reason=? WHERE id=?",
                    (actor, _now(), reason[:500], int(invoice_id)))
        con.execute("UPDATE weighing_tickets SET invoice_id=NULL WHERE invoice_id=?", (int(invoice_id),))
    extra = " ; code MECeF déjà renseigné : prévoir un avoir normalisé" if inv["mecef_code"] else ""
    log_event(actor, "ANNULATION_FACTURE", f"{inv['invoice_no']}; motif={reason}{extra}")


def set_mecef(invoice_id: int, code: str, nim: str, actor: str) -> None:
    with connect() as con:
        inv = con.execute("SELECT invoice_no,status FROM invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None or inv["status"] != "Émise":
            raise ValueError("Facture introuvable ou annulée.")
        con.execute("UPDATE invoices SET mecef_code=?, mecef_nim=? WHERE id=?",
                    (str(code).strip()[:200], str(nim).strip()[:100], int(invoice_id)))
    log_event(actor, "CODE_MECEF_FACTURE", f"{inv['invoice_no']}; code renseigné")


def list_invoices(limit=1000) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT ?", (int(limit),))]


def get_invoice(invoice_id: int) -> dict:
    with connect() as con:
        inv = con.execute("SELECT * FROM invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None:
            raise ValueError("Facture introuvable.")
        lines = [dict(r) for r in con.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY line_no", (int(invoice_id),))]
        tickets = [dict(r) for r in con.execute(
            "SELECT t.* FROM weighing_tickets t JOIN invoice_lines l ON l.ticket_id=t.id WHERE l.invoice_id=? ORDER BY l.line_no",
            (int(invoice_id),))]
    return {**dict(inv), "lines": lines, "tickets": tickets}


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------
def _fmt(value, decimals=0) -> str:
    return f"{_dec(value):,.{decimals}f}".replace(",", "\u00a0")


def invoice_pdf(invoice: dict, seller: dict | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    seller = seller or get_seller()
    esc = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=10.5)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
                            title=f"Facture {invoice['invoice_no']}")
    dark = colors.HexColor("#1f3a5f")
    story = [Paragraph(f"<b>{esc(seller.get('seller_name'))}</b>", styles["Title"])]
    ident = " · ".join(x for x in [f"IFU : {esc(seller.get('seller_ifu')) or 'à renseigner'}",
                                   f"RCCM : {esc(seller.get('seller_rccm'))}" if seller.get("seller_rccm") else "",
                                   esc(seller.get("seller_address")), esc(seller.get("seller_phone"))] if x)
    story += [Paragraph(ident, small), Spacer(1, 6 * mm)]
    status = " — ANNULÉE" if invoice["status"] == "Annulée" else ""
    story.append(Paragraph(f"<b>FACTURE N° {esc(invoice['invoice_no'])}</b>{status}", styles["Heading2"]))
    head = [[Paragraph(f"<b>Date :</b> {esc(invoice['invoice_date'])}<br/><b>Échéance :</b> {esc(invoice.get('due_date')) or '—'}", cell),
             Paragraph(f"<b>Client :</b> {esc(invoice['client_name'])}<br/><b>IFU client :</b> {esc(invoice.get('client_ifu')) or '—'}<br/>"
                       f"{esc(invoice.get('client_address'))}", cell)]]
    t = Table(head, colWidths=[85 * mm, 95 * mm])
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [t, Spacer(1, 5 * mm)]

    rows = [["N°", "Désignation", "Qté", "Unité", "P.U. (FCFA)", "Montant HT"]]
    for ln in invoice["lines"]:
        rows.append([ln["line_no"], Paragraph(esc(ln["description"]), cell), _fmt(ln["quantity"], 0 if Decimal(ln["quantity"]) == Decimal(ln["quantity"]).to_integral_value() else 3),
                     esc(ln["unit"]), _fmt(ln["unit_price"], 2 if Decimal(ln["unit_price"]) != Decimal(ln["unit_price"]).to_integral_value() else 0), _fmt(ln["amount_ht"])])
    tbl = Table(rows, colWidths=[9 * mm, 80 * mm, 20 * mm, 12 * mm, 27 * mm, 32 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), dark), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                             ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                             ("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [tbl, Spacer(1, 4 * mm)]
    rate_pct = Decimal(invoice["vat_rate"]) * 100
    tot = Table([["Total HT", _fmt(invoice["total_ht"]) + " FCFA"],
                 [f"TVA ({rate_pct.normalize():f} %)", _fmt(invoice["total_vat"]) + " FCFA"],
                 ["TOTAL TTC", _fmt(invoice["total_ttc"]) + " FCFA"]], colWidths=[40 * mm, 45 * mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
                             ("LINEABOVE", (0, 2), (-1, 2), 0.8, dark), ("FONTSIZE", (0, 0), (-1, -1), 9.5)]))
    story += [tot, Spacer(1, 5 * mm)]
    if invoice.get("notes"):
        story.append(Paragraph(f"<b>Observations :</b> {esc(invoice['notes'])}", small))
    if seller.get("seller_bank"):
        story.append(Paragraph(f"<b>Coordonnées bancaires :</b> {esc(seller['seller_bank'])}", small))
    if invoice.get("mecef_code"):
        story.append(Paragraph(f"<b>Code MECeF :</b> {esc(invoice['mecef_code'])} &nbsp; <b>NIM :</b> {esc(invoice.get('mecef_nim'))}", small))
    else:
        story.append(Paragraph("<i>Document non normalisé : le code MECeF / NIM n'a pas encore été renseigné.</i>", small))
    if invoice["status"] == "Annulée":
        story.append(Paragraph(f"<b>Annulée le {esc(invoice['cancelled_at'][:10])} par {esc(invoice['cancelled_by'])} — "
                               f"motif : {esc(invoice['cancel_reason'])}</b>", small))

    if invoice.get("tickets"):
        story += [PageBreak(), Paragraph(f"Annexe — tickets de pesée de la facture {esc(invoice['invoice_no'])}", styles["Heading3"])]
        arows = [["Ticket", "Date P2", "Véhicule", "Transporteur", "Origine → Destination", "P1 (kg)", "P2 (kg)", "Net (kg)"]]
        for tk in invoice["tickets"]:
            arows.append([esc(tk["ticket_no"]), esc(tk["date_p2"] or tk["date_p1"]), esc(tk["vehicle"]),
                          Paragraph(esc(tk["transporter"]), cell), Paragraph(f"{esc(tk['origin'])} → {esc(tk['destination'])}", cell),
                          _fmt(tk["weight_p1"]), _fmt(tk["weight_p2"]), _fmt(tk["net_weight"])])
        arows.append(["", "", "", "", "Total net", "", "", _fmt(sum(int(x["net_weight"]) for x in invoice["tickets"]))])
        at = Table(arows, colWidths=[15 * mm, 20 * mm, 22 * mm, 34 * mm, 40 * mm, 17 * mm, 17 * mm, 17 * mm], repeatRows=1)
        at.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), dark), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                                ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                                ("ALIGN", (5, 0), (-1, -1), "RIGHT"), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
        story.append(at)
    doc.build(story)
    return buf.getvalue()
