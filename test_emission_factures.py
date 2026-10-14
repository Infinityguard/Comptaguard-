from decimal import Decimal
import sqlite3
import pytest
from security_db import init_db
from emission_factures import (init_invoicing_db, create_ticket, list_tickets, lines_from_tickets, create_invoice,
                               cancel_invoice, get_invoice, list_invoices, set_mecef, invoice_pdf, compute_net,
                               compute_totals, save_seller, get_seller)

# Ticket n° 5031 de l'usine de Sérou (campagne 2025-2026) : P1 11 920 kg, P2 5 760 kg, net 6 160 kg.
TICKET = dict(ticket_no="5031", site="PONT USINE SEROU", campaign="2025-2026", date_p1="2026-04-06", time_p1="23:48:47",
              date_p2="2026-04-07", time_p2="05:57:03", vehicle="BZ9904RB", movement="CHARGEMENT", product="COTON GRAINE",
              humidity_rate="6,80", client="SOCOPA", supplier="SOCOPA", transporter="EL HADJ SALIFOU LABARAM AWALI",
              origin="USINE-SILOS", destination="ASPIRATION", quart="QUART B", commune="RAPPROCHEMENT", evacuation_bd="R545",
              choice="1ER CHOIX", variety="ANG 956", cvpc="USINE", market="SILO 1", weight_p1=11920, weight_p2=5760,
              weigher="SOCOPA _ HAKIM", driver="EL-HADJ SALIFOU AWALI", driver_permit="05440/1981DTT")


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "fact_test.sqlite3"))
    init_db()
    init_invoicing_db()


def test_net_weight_is_checked(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert compute_net(11920, 5760) == 6160
    create_ticket(TICKET, "admin", printed_net=6160)
    assert list_tickets()[0]["net_weight"] == 6160
    with pytest.raises(ValueError, match="différent"):
        create_ticket({**TICKET, "ticket_no": "5032"}, "admin", printed_net=6000)


def test_duplicate_ticket_rejected(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket(TICKET, "admin")
    with pytest.raises(ValueError, match="existe déjà"):
        create_ticket(TICKET, "admin")


def test_invoice_totals_and_numbering(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket(TICKET, "admin")
    create_ticket({**TICKET, "ticket_no": "5032", "vehicle": "AB1234", "weight_p1": 12000, "weight_p2": 6000}, "admin")
    tickets = list_tickets()
    lines = lines_from_tickets(tickets, "350")
    inv = create_invoice("Client Test", lines, "0.18", "comptable", invoice_date="2026-04-10")
    assert inv["invoice_no"] == "FAC-2026-00001"
    assert inv["total_ht"] == Decimal((6160 + 6000) * 350)
    assert inv["total_vat"] == Decimal(round((6160 + 6000) * 350 * 0.18))
    assert inv["total_ttc"] == inv["total_ht"] + inv["total_vat"]
    assert all(t["invoice_id"] for t in list_tickets())
    inv2 = create_invoice("Autre", [{"description": "Transport", "quantity": 1, "unit": "forfait", "unit_price": 1000}], "0", "comptable", invoice_date="2026-04-11")
    assert inv2["invoice_no"] == "FAC-2026-00002"


def test_ticket_cannot_be_invoiced_twice_and_cancel_releases_it(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket(TICKET, "admin")
    lines = lines_from_tickets(list_tickets(), 100)
    inv = create_invoice("Client", lines, "0.18", "c")
    with pytest.raises(ValueError, match="déjà facturé"):
        create_invoice("Client", lines, "0.18", "c")
    cancel_invoice(inv["id"], "Erreur de prix unitaire", "c")
    assert list_tickets()[0]["invoice_id"] is None
    assert list_invoices()[0]["status"] == "Annulée"
    new = create_invoice("Client", lines_from_tickets(list_tickets(), 120), "0.18", "c")
    assert new["invoice_no"] == "FAC-" + new["invoice_no"][4:8] + "-00002"  # la numérotation continue, pas de réutilisation
    with pytest.raises(ValueError):
        cancel_invoice(inv["id"], "Encore", "c")


def test_issued_invoice_is_frozen(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket(TICKET, "admin")
    inv = create_invoice("Client", lines_from_tickets(list_tickets(), 100), "0.18", "c")
    from security_db import connect
    with connect() as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE invoices SET total_ht='1' WHERE id=?", (inv["id"],))
    with connect() as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("DELETE FROM invoices WHERE id=?", (inv["id"],))
    with connect() as con:
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("UPDATE weighing_tickets SET net_weight=1")
    set_mecef(inv["id"], "ABCD-1234", "NIM001", "c")
    assert get_invoice(inv["id"])["mecef_code"] == "ABCD-1234"


def test_validation_errors():
    with pytest.raises(ValueError):
        compute_totals([{"quantity": 0, "unit_price": 10}], "0.18")
    with pytest.raises(ValueError):
        compute_totals([{"quantity": 1, "unit_price": 10}], "18")  # 18 au lieu de 0,18


def test_pdf_generation(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    save_seller({"seller_name": "SOCOPA SA", "seller_ifu": "123"}, "admin")
    assert get_seller()["seller_ifu"] == "123"
    create_ticket(TICKET, "admin")
    inv = create_invoice("Client", lines_from_tickets(list_tickets(), 100), "0.18", "c")
    pdf = invoice_pdf(get_invoice(inv["id"]))
    assert pdf.startswith(b"%PDF") and len(pdf) > 2000
