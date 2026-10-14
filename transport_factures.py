"""Factures de transport (règlement des transporteurs) établies à partir des tickets de pesée.

Reproduit le modèle « FACTURE RAPPROCHEMENT COTON GRAINE » : une ligne par ticket (quantité en tonnes = poids net / 1000),
prix à la tonne selon la typologie (enclavée / non enclavée), total HT, prélèvement AIB, déduction éventuelle de gasoil,
net à payer et montant arrêté en lettres.

Les taux (prix à la tonne, AIB) sont des paramètres saisis à l'émission : ils ne sont jamais codés en dur.
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from emission_factures import _dec, _fcfa, _now
from security_db import connect, log_event
from referentiels_aic import TYPO_ENC, TYPO_NON, TYPO_UNDEFINED, init_referentiels_db, lookup_typo, same_transporter, _alias_map

TONNE = Decimal("0.01")
TYPOS = [TYPO_NON, TYPO_ENC]


# --------------------------------------------------------------------------
# Montant en lettres (français)
# --------------------------------------------------------------------------
_U = ["zéro", "un", "deux", "trois", "quatre", "cinq", "six", "sept", "huit", "neuf", "dix", "onze", "douze",
      "treize", "quatorze", "quinze", "seize"]
_T = ["", "", "vingt", "trente", "quarante", "cinquante", "soixante"]


def _lt100(n: int, final: bool) -> str:
    if n < 17:
        return _U[n]
    if n < 20:
        return "dix-" + _U[n - 10]
    if n < 70:
        t, r = _T[n // 10], n % 10
        return t if r == 0 else (t + " et un" if r == 1 else f"{t}-{_U[r]}")
    if n < 80:
        return "soixante et onze" if n == 71 else "soixante-" + _lt100(n - 60, final)
    if n == 80:
        return "quatre-vingts" if final else "quatre-vingt"
    return "quatre-vingt-" + _lt100(n - 80, final)


def _lt1000(n: int, final: bool) -> str:
    h, r = divmod(n, 100)
    if h == 0:
        return _lt100(r, final)
    head = "cent" if h == 1 else f"{_U[h]} cent"
    if r == 0:
        return head + ("s" if h > 1 and final else "")
    return f"{head} {_lt100(r, final)}"


def nombre_en_lettres(n: int) -> str:
    n = int(n)
    if n < 0:
        return "moins " + nombre_en_lettres(-n)
    if n == 0:
        return "zéro"
    parts = []
    millions, rest = divmod(n, 1_000_000)
    thousands, units = divmod(rest, 1000)
    if millions:
        parts.append("un million" if millions == 1 else _lt1000(millions, False) + " millions")
    if thousands:
        parts.append("mille" if thousands == 1 else _lt1000(thousands, False) + " mille")
    if units:
        parts.append(_lt1000(units, True))
    return " ".join(parts)


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
def init_transport_db() -> None:
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS transporters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE COLLATE NOCASE,
            phone TEXT NOT NULL DEFAULT '',
            ifu TEXT NOT NULL DEFAULT '',
            id_piece TEXT NOT NULL DEFAULT '',
            contract_no TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transport_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign TEXT NOT NULL,
            number TEXT NOT NULL,
            invoice_date TEXT NOT NULL,
            transporter_name TEXT NOT NULL,
            phone TEXT NOT NULL DEFAULT '', ifu TEXT NOT NULL DEFAULT '',
            id_piece TEXT NOT NULL DEFAULT '', contract_no TEXT NOT NULL DEFAULT '',
            object TEXT NOT NULL, debtor TEXT NOT NULL DEFAULT 'SoCoPA',
            total_weight_t TEXT NOT NULL, total_ht TEXT NOT NULL,
            aib_rate TEXT NOT NULL, aib_amount TEXT NOT NULL, net_before_fuel TEXT NOT NULL,
            fuel_liters TEXT NOT NULL DEFAULT '0', fuel_unit_price TEXT NOT NULL DEFAULT '0', fuel_amount TEXT NOT NULL DEFAULT '0',
            net_to_pay TEXT NOT NULL, amount_words TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'Émise' CHECK(status IN ('Émise','Annulée')),
            paid_at TEXT NOT NULL DEFAULT '', payment_mode TEXT NOT NULL DEFAULT '', payment_ref TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL, created_at TEXT NOT NULL,
            cancelled_by TEXT NOT NULL DEFAULT '', cancelled_at TEXT NOT NULL DEFAULT '', cancel_reason TEXT NOT NULL DEFAULT '',
            UNIQUE(campaign, number)
        );
        CREATE TABLE IF NOT EXISTS transport_invoice_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL REFERENCES transport_invoices(id),
            line_no INTEGER NOT NULL,
            ticket_id INTEGER REFERENCES weighing_tickets(id),
            line_date TEXT NOT NULL DEFAULT '', vehicle TEXT NOT NULL DEFAULT '', be_aic TEXT NOT NULL DEFAULT '',
            ticket_no TEXT NOT NULL DEFAULT '', transporter TEXT NOT NULL DEFAULT '', commune TEXT NOT NULL DEFAULT '',
            cvpc TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '', typo TEXT NOT NULL DEFAULT '',
            usine TEXT NOT NULL DEFAULT '',
            qty_t TEXT NOT NULL, unit_price TEXT NOT NULL, amount_ht TEXT NOT NULL,
            UNIQUE(invoice_id, line_no)
        );
        CREATE TRIGGER IF NOT EXISTS transport_no_delete BEFORE DELETE ON transport_invoices
        BEGIN SELECT RAISE(ABORT, 'Une facture de transport ne peut pas être supprimée : l''annuler avec un motif.'); END;
        CREATE TRIGGER IF NOT EXISTS transport_frozen BEFORE UPDATE ON transport_invoices
        WHEN OLD.status = 'Annulée'
          OR OLD.number <> NEW.number OR OLD.campaign <> NEW.campaign OR OLD.invoice_date <> NEW.invoice_date
          OR OLD.transporter_name <> NEW.transporter_name OR OLD.total_ht <> NEW.total_ht
          OR OLD.aib_amount <> NEW.aib_amount OR OLD.net_to_pay <> NEW.net_to_pay OR OLD.total_weight_t <> NEW.total_weight_t
        BEGIN SELECT RAISE(ABORT, 'Une facture de transport émise est figée ; seuls le règlement et l''annulation sont possibles.'); END;
        CREATE TRIGGER IF NOT EXISTS transport_lines_u BEFORE UPDATE ON transport_invoice_lines
        BEGIN SELECT RAISE(ABORT, 'Les lignes de facture sont immuables.'); END;
        CREATE TRIGGER IF NOT EXISTS transport_lines_d BEFORE DELETE ON transport_invoice_lines
        BEGIN SELECT RAISE(ABORT, 'Les lignes de facture sont immuables.'); END;
        """)
        # Migration additive : lien ticket -> facture de transport.
        cols = {r[1] for r in con.execute("PRAGMA table_info(weighing_tickets)")}
        if cols and "transport_invoice_id" not in cols:
            con.execute("ALTER TABLE weighing_tickets ADD COLUMN transport_invoice_id INTEGER REFERENCES transport_invoices(id)")
    init_referentiels_db()


# --------------------------------------------------------------------------
# Transporteurs
# --------------------------------------------------------------------------
def save_transporter(data: dict, actor: str) -> None:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Le nom du transporteur est obligatoire.")
    vals = [str(data.get(k, "")).strip() for k in ("phone", "ifu", "id_piece", "contract_no")]
    with connect() as con:
        con.execute("""INSERT INTO transporters(name,phone,ifu,id_piece,contract_no,created_by,created_at) VALUES(?,?,?,?,?,?,?)
                       ON CONFLICT(name) DO UPDATE SET phone=excluded.phone, ifu=excluded.ifu,
                       id_piece=excluded.id_piece, contract_no=excluded.contract_no""", [name, *vals, actor, _now()])
    log_event(actor, "FICHE_TRANSPORTEUR", f"Transporteur={name}")


def list_transporters() -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM transporters ORDER BY name")]


def tickets_to_settle(transporter_name: str | None = None) -> list[dict]:
    """Tickets pas encore intégrés à une facture de transport.

    Le nom du transporteur est comparé après normalisation (accents, casse, ponctuation) et via les alias validés.
    """
    with connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM weighing_tickets WHERE transport_invoice_id IS NULL ORDER BY date_p2, id")]
    if transporter_name:
        amap = _alias_map()
        rows = [r for r in rows if same_transporter(r["transporter"], transporter_name, amap)]
    return rows


# --------------------------------------------------------------------------
# Calculs
# --------------------------------------------------------------------------
def tonnes(net_kg) -> Decimal:
    return (_dec(net_kg, "Poids net") / Decimal(1000)).quantize(TONNE, rounding=ROUND_HALF_UP)


def lines_from_tickets(tickets: list[dict], prices: dict[str, object], usine: str, campaign: str | None = None) -> list[dict]:
    """Une ligne par ticket. La typologie vient du référentiel AIC de la campagne (CVPC + marché du ticket) ;
    si elle est introuvable, la ligne est marquée « À définir » et doit être corrigée avant l'émission."""
    out = []
    for t in tickets:
        raw = (t.get("date_p2") or t.get("date_p1") or "").replace("-", "")
        typo = lookup_typo(t.get("campaign") or campaign or "", t.get("cvpc", ""), t.get("market", "")) or TYPO_UNDEFINED
        out.append({"ticket_id": t["id"], "line_date": raw, "vehicle": t.get("vehicle", ""),
                    "be_aic": t.get("evacuation_bd", ""), "ticket_no": t["ticket_no"], "transporter": t.get("transporter", ""),
                    "commune": t.get("commune", ""), "cvpc": t.get("cvpc", ""), "market": t.get("market", ""),
                    "typo": typo, "usine": usine, "qty_t": tonnes(t["net_weight"]),
                    "unit_price": _dec(prices.get(typo, 0), "Prix à la tonne")})
    return out


def compute_transport(lines: list[dict], aib_rate, fuel_liters=0, fuel_unit_price=0) -> dict:
    rate = _dec(aib_rate, "Taux AIB")
    if not (0 <= rate <= 1):
        raise ValueError("Le taux AIB doit être compris entre 0 et 1 (ex. 0,03).")
    liters, fprice = _dec(fuel_liters, "Gasoil (litres)"), _dec(fuel_unit_price, "Prix du gasoil")
    if liters < 0 or fprice < 0:
        raise ValueError("Le gasoil ne peut pas être négatif.")
    norm, total_ht, total_t = [], Decimal(0), Decimal(0)
    for i, ln in enumerate(lines, 1):
        q, p = _dec(ln["qty_t"], "Quantité"), _dec(ln["unit_price"], "Prix à la tonne")
        if q <= 0:
            raise ValueError(f"Ligne {i} : quantité nulle ou négative.")
        if ln.get("typo") == TYPO_UNDEFINED:
            raise ValueError(f"Ligne {i} (ticket {ln.get('ticket_no', '')}) : typologie à définir — marché « {ln.get('market', '')} » "
                             "absent du fichier AIC de la campagne ; importez le fichier ou choisissez la typologie.")
        if p <= 0:
            raise ValueError(f"Ligne {i} : prix à la tonne manquant pour la typologie « {ln.get('typo', '')} ».")
        amount = _fcfa(q * p)
        norm.append({**ln, "line_no": i, "qty_t": q, "unit_price": p, "amount_ht": amount})
        total_ht += amount
        total_t += q
    aib = _fcfa(total_ht * rate)
    net_before = total_ht - aib
    fuel_amount = _fcfa(liters * fprice)
    net = net_before - fuel_amount
    if net < 0:
        raise ValueError("Le gasoil à déduire dépasse le net disponible.")
    return {"lines": norm, "total_weight_t": total_t, "total_ht": total_ht, "aib_rate": rate, "aib_amount": aib,
            "net_before_fuel": net_before, "fuel_liters": liters, "fuel_unit_price": fprice, "fuel_amount": fuel_amount,
            "net_to_pay": net, "amount_words": nombre_en_lettres(int(net))}


# --------------------------------------------------------------------------
# Émission, règlement, annulation
# --------------------------------------------------------------------------
def create_transport_invoice(transporter: dict, lines: list[dict], aib_rate, actor: str, campaign: str,
                             invoice_date: str | None = None, objet: str = "FACTURE RAPPROCHEMENT COTON GRAINE",
                             fuel_liters=0, fuel_unit_price=0, prefix: str = "R", debtor: str = "SoCoPA") -> dict:
    name = str(transporter.get("name", "")).strip()
    campaign = str(campaign).strip()
    if not name:
        raise ValueError("Le transporteur est obligatoire.")
    if not campaign:
        raise ValueError("La campagne est obligatoire.")
    if not lines:
        raise ValueError("Sélectionnez au moins un ticket.")
    calc = compute_transport(lines, aib_rate, fuel_liters, fuel_unit_price)
    date = invoice_date or datetime.now().date().isoformat()
    prefix = str(prefix).strip().upper() or "R"
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")  # numérotation continue sans doublon
        ids = [ln["ticket_id"] for ln in calc["lines"] if ln.get("ticket_id")]
        if len(ids) != len(set(ids)):
            raise ValueError("Un même ticket ne peut figurer qu'une fois.")
        for tid in ids:
            row = con.execute("SELECT ticket_no, transport_invoice_id FROM weighing_tickets WHERE id=?", (int(tid),)).fetchone()
            if row is None:
                raise ValueError("Ticket de pesée introuvable.")
            if row["transport_invoice_id"] is not None:
                raise ValueError(f"Le ticket n° {row['ticket_no']} est déjà intégré à une facture de transport.")
        seqs = [int(m.group(1)) for (n,) in con.execute("SELECT number FROM transport_invoices WHERE campaign=?", (campaign,))
                if (m := re.fullmatch(rf"{re.escape(prefix)}(\d+)", n))]
        number = f"{prefix}{(max(seqs) + 1 if seqs else 1):04d}"
        cur = con.execute("""INSERT INTO transport_invoices(campaign,number,invoice_date,transporter_name,phone,ifu,id_piece,contract_no,
            object,debtor,total_weight_t,total_ht,aib_rate,aib_amount,net_before_fuel,fuel_liters,fuel_unit_price,fuel_amount,
            net_to_pay,amount_words,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (campaign, number, date, name, transporter.get("phone", ""), transporter.get("ifu", ""),
                           transporter.get("id_piece", ""), transporter.get("contract_no", ""), objet, debtor,
                           str(calc["total_weight_t"]), str(calc["total_ht"]), str(calc["aib_rate"]), str(calc["aib_amount"]),
                           str(calc["net_before_fuel"]), str(calc["fuel_liters"]), str(calc["fuel_unit_price"]),
                           str(calc["fuel_amount"]), str(calc["net_to_pay"]), calc["amount_words"], actor, _now()))
        inv_id = cur.lastrowid
        for ln in calc["lines"]:
            con.execute("""INSERT INTO transport_invoice_lines(invoice_id,line_no,ticket_id,line_date,vehicle,be_aic,ticket_no,
                transporter,commune,cvpc,market,typo,usine,qty_t,unit_price,amount_ht) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (inv_id, ln["line_no"], ln.get("ticket_id"), ln.get("line_date", ""), ln.get("vehicle", ""),
                         ln.get("be_aic", ""), ln.get("ticket_no", ""), ln.get("transporter", ""), ln.get("commune", ""),
                         ln.get("cvpc", ""), ln.get("market", ""), ln.get("typo", ""), ln.get("usine", ""),
                         str(ln["qty_t"]), str(ln["unit_price"]), str(ln["amount_ht"])))
            if ln.get("ticket_id"):
                con.execute("UPDATE weighing_tickets SET transport_invoice_id=? WHERE id=?", (inv_id, int(ln["ticket_id"])))
    log_event(actor, "EMISSION_FACTURE_TRANSPORT", f"{number}; campagne={campaign}; transporteur={name}; "
              f"poids={calc['total_weight_t']} T; HT={calc['total_ht']}; net à payer={calc['net_to_pay']}")
    return {"id": inv_id, "number": number, "net_to_pay": calc["net_to_pay"], "total_ht": calc["total_ht"]}


def record_payment(invoice_id: int, paid_at: str, mode: str, reference: str, actor: str) -> None:
    if not str(paid_at).strip():
        raise ValueError("La date de règlement est obligatoire.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        inv = con.execute("SELECT number,status,paid_at FROM transport_invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None or inv["status"] != "Émise":
            raise ValueError("Facture introuvable ou annulée.")
        if inv["paid_at"]:
            raise ValueError("Cette facture est déjà marquée comme réglée.")
        con.execute("UPDATE transport_invoices SET paid_at=?, payment_mode=?, payment_ref=? WHERE id=?",
                    (str(paid_at).strip(), str(mode).strip()[:50], str(reference).strip()[:100], int(invoice_id)))
    log_event(actor, "REGLEMENT_FACTURE_TRANSPORT", f"{inv['number']}; date={paid_at}; mode={mode}; réf={reference}")


def cancel_transport_invoice(invoice_id: int, reason: str, actor: str) -> None:
    if len(str(reason).strip()) < 5:
        raise ValueError("Un motif d'annulation d'au moins 5 caractères est obligatoire.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        inv = con.execute("SELECT number,status,paid_at FROM transport_invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None:
            raise ValueError("Facture introuvable.")
        if inv["status"] == "Annulée":
            raise ValueError("Cette facture est déjà annulée.")
        if inv["paid_at"]:
            raise ValueError("Facture déjà réglée : l'annulation est impossible, régularisez par une écriture contraire.")
        con.execute("UPDATE transport_invoices SET status='Annulée', cancelled_by=?, cancelled_at=?, cancel_reason=? WHERE id=?",
                    (actor, _now(), str(reason).strip()[:500], int(invoice_id)))
        con.execute("UPDATE weighing_tickets SET transport_invoice_id=NULL WHERE transport_invoice_id=?", (int(invoice_id),))
    log_event(actor, "ANNULATION_FACTURE_TRANSPORT", f"{inv['number']}; motif={reason}")


def list_transport_invoices(limit=1000) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("SELECT * FROM transport_invoices ORDER BY id DESC LIMIT ?", (int(limit),))]


def get_transport_invoice(invoice_id: int) -> dict:
    with connect() as con:
        inv = con.execute("SELECT * FROM transport_invoices WHERE id=?", (int(invoice_id),)).fetchone()
        if inv is None:
            raise ValueError("Facture introuvable.")
        lines = [dict(r) for r in con.execute("SELECT * FROM transport_invoice_lines WHERE invoice_id=? ORDER BY line_no", (int(invoice_id),))]
    return {**dict(inv), "lines": lines}


# --------------------------------------------------------------------------
# PDF (paysage, modèle SOCOPA)
# --------------------------------------------------------------------------
def _n(value, decimals=0) -> str:
    return f"{_dec(value):,.{decimals}f}".replace(",", "\u00a0").replace(".", ",")


def transport_pdf(inv: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    esc = lambda s: str(s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    base = getSampleStyleSheet()["Normal"]
    txt = ParagraphStyle("t", parent=base, fontSize=9, leading=12)
    cell = ParagraphStyle("c", parent=base, fontSize=7.5, leading=9)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=10 * mm,
                            bottomMargin=12 * mm, title=f"Facture transport {inv['number']}")
    d = inv["invoice_date"]
    date_fr = f"{d[8:10]}/{d[5:7]}/{d[:4]}" if len(d) >= 10 else d
    left = [["Nom & Prénoms :", inv["transporter_name"]], ["Tél :", inv["phone"]], ["IFU :", inv["ifu"]],
            ["N° FACT TRANSP :", inv["number"]], ["N° CONTRAT :", inv["contract_no"]], ["N° Pièce d'identité :", inv["id_piece"]]]
    head = Table([[Paragraph(f"<b>{esc(a)}</b>", txt), Paragraph(esc(b), txt)] for a, b in left], colWidths=[42 * mm, 90 * mm])
    right = Paragraph(f"<b>Date :</b> {date_fr}<br/><b>Doit :</b> {esc(inv['debtor'])}", txt)
    top = Table([[head, right]], colWidths=[190 * mm, 80 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story = [top, Spacer(1, 3 * mm), Paragraph(f"<u><b>Objet :</b></u> <b>{esc(inv['object'])}</b>", txt)]
    if inv["status"] == "Annulée":
        story.append(Paragraph(f"<b>FACTURE ANNULÉE le {esc(inv['cancelled_at'][:10])} — {esc(inv['cancel_reason'])}</b>", txt))
    story.append(Spacer(1, 3 * mm))

    cols = ["Date", "N° Véhicule", "N° BE AIC", "N° Ticket", "Transporteur", "Commune", "CVPC", "Marché", "Typo", "Usine",
            "QTE CG (T)", "Prix unit. CG/T", "Montant HT"]
    rows = [cols]
    for ln in inv["lines"]:
        rows.append([ln["line_date"], ln["vehicle"], ln["be_aic"], ln["ticket_no"], Paragraph(esc(ln["transporter"]), cell),
                     Paragraph(esc(ln["commune"]), cell), ln["cvpc"], Paragraph(esc(ln["market"]), cell),
                     Paragraph(esc(ln["typo"]), cell), Paragraph(esc(ln["usine"]), cell), _n(ln["qty_t"], 2),
                     _n(ln["unit_price"], 2), _n(ln["amount_ht"])])
    widths = [17, 21, 19, 17, 27, 33, 14, 17, 17, 22, 18, 22, 22]
    tbl = Table(rows, colWidths=[w * mm for w in widths], repeatRows=1)
    tbl.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                             ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e9edf2")), ("GRID", (0, 0), (-1, -1), 0.4, colors.black),
                             ("ALIGN", (10, 1), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [tbl, Spacer(1, 4 * mm)]

    fuel_label = f"GASOIL {_n(inv['fuel_liters'], 2)} L :"
    tot = Table([["POIDS TOTAL :", _n(inv["total_weight_t"], 2)], ["TOTAL HT :", _n(inv["total_ht"])],
                 [f"PRÉLÈVEMENT AIB {_n(Decimal(inv['aib_rate']) * 100, 0)}% :", _n(inv["aib_amount"])],
                 ["NET AVANT GASOIL :", _n(inv["net_before_fuel"])], [fuel_label, _n(inv["fuel_amount"])],
                 ["NET À PAYER :", _n(inv["net_to_pay"])]], colWidths=[55 * mm, 35 * mm], hAlign="RIGHT")
    tot.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9), ("ALIGN", (1, 0), (1, -1), "RIGHT"), ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
                             ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    sign = Table([[Paragraph("<b>Pour SoCoPA SA</b>", txt), Paragraph("<b>Le Transporteur</b>", txt)]], colWidths=[130 * mm, 130 * mm])
    sign.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "RIGHT")]))
    paid = (f"Réglée le {esc(inv['paid_at'])} — {esc(inv['payment_mode'])} {esc(inv['payment_ref'])}" if inv["paid_at"] else "")
    story.append(KeepTogether([tot, Spacer(1, 3 * mm),
                               Paragraph(f"Arrêté la présente facture à la somme de FRANCS CFA : <b>{esc(inv['amount_words'])}</b>", txt),
                               Spacer(1, 3 * mm), sign, Spacer(1, 14 * mm),
                               Paragraph(f"<b>CAMPAGNE {esc(inv['campaign'])}</b> / {esc(inv['created_at'][8:10])}/{esc(inv['created_at'][5:7])}/{esc(inv['created_at'][:4])} {esc(inv['created_at'][11:])}"
                                         + (f"<br/>{paid}" if paid else ""), cell)]))
    doc.build(story)
    return buf.getvalue()
