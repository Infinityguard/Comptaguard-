import pandas as pd
import pytest
from security_db import init_db
from emission_factures import init_invoicing_db, create_ticket
from transport_factures import (init_transport_db, tickets_to_settle, lines_from_tickets, compute_transport,
                                create_transport_invoice)
from referentiels_aic import (norm, norm_typo, parse_markets, parse_transporters, save_markets, save_transporters,
                              lookup_typo, list_markets, list_contracts, transporter_for_invoice, unmatched_ticket_transporters,
                              link_alias, market_template_bytes, transporter_template_bytes)
from test_emission_factures import TICKET


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "ref_test.sqlite3"))
    init_db(); init_invoicing_db(); init_transport_db()


def test_normalisation():
    assert norm("  Silo 01 ") == "SILO 1"
    assert norm("EL-HADJ  Salifou") == "EL HADJ SALIFOU"
    assert norm("Rapprochement") == norm("RAPPROCHÉMENT")
    assert norm_typo("Non enclavée") == "Non Enclavée" and norm_typo("ENCLAVEE") == "Enclavée" and norm_typo("NE") == "Non Enclavée"
    assert norm_typo("peut-être") is None


def test_parse_markets_with_synonyms_and_title_rows():
    raw = pd.DataFrame([["LISTE DES MARCHÉS 2026-2027", None, None], [None, None, None],
                        ["Code CVPC", "Marché", "Typologie"],
                        ["USINE", "SILO 1", "non enclavée"], ["KOUANDE", "Marché Kouandé", "Enclavée"],
                        ["KOUANDE", "Marché Kouandé", "Non enclavée"],   # doublon contradictoire
                        ["X", "Y", "bizarre"], [None, None, None]])
    raw.columns = ["c0", "c1", "c2"]
    res = parse_markets(raw)
    assert len(res["rows"]) == 2
    reasons = " | ".join(r["raison"] for r in res["rejected"])
    assert "contradictoire" in reasons and "non reconnue" in reasons
    with pytest.raises(ValueError, match="Colonnes introuvables"):
        parse_markets(pd.DataFrame({"a": [1], "b": [2]}))


def test_markets_per_campaign_and_lookup(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    save_markets([{"cvpc": "USINE", "market": "SILO 1", "commune": "", "typo": "Non Enclavée"},
                  {"cvpc": "A", "market": "VILLAGE X", "commune": "", "typo": "Enclavée"}], "2025-2026", "admin")
    save_markets([{"cvpc": "A", "market": "VILLAGE X", "commune": "", "typo": "Non Enclavée"}], "2026-2027", "admin")
    assert lookup_typo("2025-2026", "usine", "silo 01") == "Non Enclavée"
    assert lookup_typo("2025-2026", "A", "Village X") == "Enclavée"
    assert lookup_typo("2026-2027", "A", "Village X") == "Non Enclavée"      # la typologie peut changer d'une année à l'autre
    assert lookup_typo("2025-2026", "", "village x") == "Enclavée"            # CVPC absent du ticket : marché unique
    assert lookup_typo("2025-2026", "A", "INCONNU") is None
    # réimport sans « remplacer » : mise à jour ; avec « remplacer » : le référentiel de la campagne est refait
    save_markets([{"cvpc": "A", "market": "VILLAGE X", "commune": "", "typo": "Non Enclavée"}], "2025-2026", "admin")
    assert lookup_typo("2025-2026", "A", "VILLAGE X") == "Non Enclavée" and len(list_markets("2025-2026")) == 2
    save_markets([{"cvpc": "B", "market": "Z", "commune": "", "typo": "Enclavée"}], "2025-2026", "admin", replace=True)
    assert len(list_markets("2025-2026")) == 1 and len(list_markets("2026-2027")) == 1


def test_contracts_per_campaign(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    raw = pd.DataFrame({"Nom & Prénoms": ["ISSA SIDI", "ISSA SIDI", "AUTRE T"], "N° Contrat": [9, 10, "A-1"],
                        "IFU": ["1201300029804", "", ""]})
    res = parse_transporters(raw)
    assert [r["contract_no"] for r in res["rows"]] == ["9", "A-1"] and len(res["rejected"]) == 1
    save_transporters(res["rows"], "2025-2026", "admin")
    save_transporters([{"name": "issa  sidi", "contract_no": "014", "phone": "", "ifu": "", "id_piece": ""}], "2026-2027", "admin")
    assert [c["contract_no"] for c in list_contracts("2025-2026")] == ["A-1", "9"]  # triés par nom : AUTRE T, ISSA SIDI
    from transport_factures import list_transporters
    tr = [t for t in list_transporters() if t["name"] == "ISSA SIDI"][0]   # pas de doublon de fiche (même nom normalisé)
    assert len(list_transporters()) == 2 and tr["ifu"] == "1201300029804"  # l'IFU n'est pas écrasé par une cellule vide
    assert transporter_for_invoice(tr["id"], "2025-2026")["contract_no"] == "9"
    assert transporter_for_invoice(tr["id"], "2026-2027")["contract_no"] == "014"


def test_name_matching_and_aliases(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    save_transporters([{"name": "EL HADJ SALIFOU LABARAM AWALI", "contract_no": "7", "phone": "", "ifu": "", "id_piece": ""}], "2025-2026", "admin")
    create_ticket(TICKET, "admin")                                                       # nom exact
    create_ticket({**TICKET, "ticket_no": "6000", "transporter": "El-Hadj Salifou  Labaram Awali"}, "admin")  # casse / ponctuation
    create_ticket({**TICKET, "ticket_no": "6001", "transporter": "SALIFOU LABARAM"}, "admin")                 # nom différent
    assert len(tickets_to_settle("EL HADJ SALIFOU LABARAM AWALI")) == 2
    unmatched = unmatched_ticket_transporters()
    assert [u["name"] for u in unmatched] == ["SALIFOU LABARAM"]
    from transport_factures import list_transporters
    link_alias("SALIFOU LABARAM", list_transporters()[0]["id"], "admin")
    assert len(tickets_to_settle("EL HADJ SALIFOU LABARAM AWALI")) == 3 and unmatched_ticket_transporters() == []


def test_unknown_market_blocks_invoice(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_ticket({**TICKET, "market": "SILO 99"}, "admin")
    lines = lines_from_tickets(tickets_to_settle(), {"Non Enclavée": 2500}, "U")
    assert lines[0]["typo"] == "À définir"
    with pytest.raises(ValueError, match="typologie à définir"):
        compute_transport(lines, "0.03")
    save_markets([{"cvpc": "USINE", "market": "SILO 99", "commune": "", "typo": "Enclavée"}], "2025-2026", "admin")
    lines = lines_from_tickets(tickets_to_settle(), {"Non Enclavée": 2500, "Enclavée": 3000}, "U")
    assert lines[0]["typo"] == "Enclavée" and lines[0]["unit_price"] == 3000


def test_templates_are_readable():
    assert market_template_bytes()[:2] == b"PK" and transporter_template_bytes()[:2] == b"PK"
