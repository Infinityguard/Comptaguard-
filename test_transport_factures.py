from decimal import Decimal
import sqlite3
import pytest
from security_db import init_db, connect
from emission_factures import init_invoicing_db, create_ticket, list_tickets
from transport_factures import (init_transport_db, nombre_en_lettres, tonnes, compute_transport, lines_from_tickets,
                                create_transport_invoice, record_payment, cancel_transport_invoice, tickets_to_settle,
                                save_transporter, list_transporters, get_transport_invoice, transport_pdf)
from test_emission_factures import TICKET
from referentiels_aic import save_markets

TRANSPORTER = {"name": "ISSA SIDI", "phone": "0196522480", "ifu": "1201300029804", "id_piece": "5210283828", "contract_no": "009"}


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "transport_test.sqlite3"))
    init_db()
    init_invoicing_db()
    init_transport_db()
    for camp in ("2025-2026", "2026-2027"):  # fichier AIC de la campagne : le marché SILO 1 de l'usine est non enclavé
        save_markets([{"cvpc": "USINE", "market": "SILO 1", "commune": "", "typo": "Non Enclavée"}], camp, "admin")


def test_amount_in_words():
    assert nombre_en_lettres(525401) == "cinq cent vingt-cinq mille quatre cent un"
    assert nombre_en_lettres(80) == "quatre-vingts"
    assert nombre_en_lettres(80000) == "quatre-vingt mille"
    assert nombre_en_lettres(200) == "deux cents"
    assert nombre_en_lettres(200000) == "deux cent mille"
    assert nombre_en_lettres(71) == "soixante et onze"
    assert nombre_en_lettres(1000) == "mille"
    assert nombre_en_lettres(1_250_000) == "un million deux cent cinquante mille"
    assert nombre_en_lettres(0) == "zéro"


def test_totals_match_scanned_invoice():
    # Facture R0025 : 216,66 T à 2 500 F/T -> HT 541 650 ; AIB 3 % = 16 250 ; net avant gasoil 525 400.
    line = {"qty_t": Decimal("216.66"), "unit_price": 2500, "typo": "Non Enclavée"}
    calc = compute_transport([line], "0.03")
    assert calc["total_ht"] == Decimal("541650")
    assert calc["aib_amount"] == Decimal("16250")
    assert calc["net_before_fuel"] == Decimal("525400")
    assert calc["net_to_pay"] == Decimal("525400")
    # lignes du scan : 10,50 T -> 26 250 ; 9,56 T -> 23 900 ; 2,26 T -> 5 650
    for q, amount in (("10.50", 26250), ("9.56", 23900), ("2.26", 5650), ("12.34", 30850)):
        assert compute_transport([{"qty_t": Decimal(q), "unit_price": 2500}], "0")["total_ht"] == Decimal(amount)


def test_fuel_deduction_and_validation():
    calc = compute_transport([{"qty_t": Decimal("10"), "unit_price": 2500}], "0.03", fuel_liters=10, fuel_unit_price=600)
    assert calc["fuel_amount"] == Decimal("6000")
    assert calc["net_to_pay"] == Decimal(25000 - 750 - 6000)
    with pytest.raises(ValueError, match="dépasse"):
        compute_transport([{"qty_t": Decimal("1"), "unit_price": 2500}], "0.03", fuel_liters=100, fuel_unit_price=600)
    with pytest.raises(ValueError, match="prix"):
        compute_transport([{"qty_t": Decimal("1"), "unit_price": 0, "typo": "Enclavée"}], "0.03")
    with pytest.raises(ValueError, match="AIB"):
        compute_transport([{"qty_t": Decimal("1"), "unit_price": 10}], "3")


def test_ticket_to_tonnes_and_line_mapping(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert tonnes(6160) == Decimal("6.16")
    assert tonnes(10555) == Decimal("10.56")
    create_ticket({**TICKET, "transporter": "ISSA SIDI", "commune": "RAPPROCHEMENT"}, "admin")
    lines = lines_from_tickets(tickets_to_settle("issa sidi"), {"Non Enclavée": 2500}, "USINE DE DJOUGOU")
    assert len(lines) == 1
    ln = lines[0]
    assert (ln["line_date"], ln["vehicle"], ln["be_aic"], ln["ticket_no"], ln["market"], ln["cvpc"]) == \
           ("20260407", "BZ9904RB", "R545", "5031", "SILO 1", "USINE")
    assert ln["qty_t"] == Decimal("6.16")


def test_issue_pay_cancel_flow(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    save_transporter(TRANSPORTER, "admin")
    assert list_transporters()[0]["contract_no"] == "009"
    create_ticket({**TICKET, "transporter": "ISSA SIDI"}, "admin")
    create_ticket({**TICKET, "ticket_no": "5032", "transporter": "ISSA SIDI", "weight_p1": 16000, "weight_p2": 5500}, "admin")
    create_ticket({**TICKET, "ticket_no": "5033", "transporter": "AUTRE"}, "admin")
    assert len(tickets_to_settle("ISSA SIDI")) == 2
    lines = lines_from_tickets(tickets_to_settle("ISSA SIDI"), {"Non Enclavée": 2500}, "USINE DE DJOUGOU")
    res = create_transport_invoice(TRANSPORTER, lines, "0.03", "comptable", "2025-2026", invoice_date="2026-04-21")
    assert res["number"] == "R0001"
    inv = get_transport_invoice(res["id"])
    assert inv["total_weight_t"] == "16.66"  # 6,16 + 10,50
    assert inv["total_ht"] == "41650"
    assert inv["aib_amount"] == "1250"  # 3 % de 41 650 = 1 249,5 arrondi à 1 250
    assert inv["net_to_pay"] == "40400"
    assert len(tickets_to_settle("ISSA SIDI")) == 0
    with pytest.raises(ValueError, match="déjà intégré"):
        create_transport_invoice(TRANSPORTER, lines, "0.03", "comptable", "2025-2026")
    second = create_transport_invoice(TRANSPORTER, lines_from_tickets(tickets_to_settle("AUTRE"), {"Non Enclavée": 2500}, "U"),
                                      "0.03", "comptable", "2025-2026")
    assert second["number"] == "R0002"
    # nouvelle campagne : la numérotation repart
    create_ticket({**TICKET, "ticket_no": "1", "campaign": "2026-2027", "transporter": "ISSA SIDI"}, "admin")
    third = create_transport_invoice(TRANSPORTER, lines_from_tickets(tickets_to_settle("ISSA SIDI"), {"Non Enclavée": 2500}, "U"),
                                     "0.03", "comptable", "2026-2027")
    assert third["number"] == "R0001"
    record_payment(res["id"], "2026-04-25", "Virement", "VIR-123", "comptable")
    with pytest.raises(ValueError, match="déjà marquée"):
        record_payment(res["id"], "2026-04-26", "Espèces", "", "comptable")
    with pytest.raises(ValueError, match="réglée"):
        cancel_transport_invoice(res["id"], "Erreur de prix", "comptable")
    cancel_transport_invoice(second["id"], "Erreur de transporteur", "comptable")
    assert len(tickets_to_settle("AUTRE")) == 1  # le ticket est libéré
    with connect() as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE transport_invoices SET total_ht='1' WHERE id=?", (res["id"],))
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM transport_invoices WHERE id=?", (res["id"],))


def test_pdf(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket({**TICKET, "transporter": "ISSA SIDI"}, "admin")
    lines = lines_from_tickets(tickets_to_settle(), {"Non Enclavée": 2500}, "USINE DE DJOUGOU")
    res = create_transport_invoice(TRANSPORTER, lines, "0.03", "c", "2025-2026", invoice_date="2026-04-21")
    pdf = transport_pdf(get_transport_invoice(res["id"]))
    assert pdf.startswith(b"%PDF") and len(pdf) > 2500
