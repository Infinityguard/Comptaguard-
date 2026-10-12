"""Une ligne à date invalide ne doit pas scinder sa pièce en deux groupes."""
import pandas as pd
from controles import analyze, prepare_dataframe

COLS = ["Date", "Piece", "Libelle", "Compte", "Debit", "Credit", "Journal"]


def _desequilibres(rows):
    df = prepare_dataframe(pd.DataFrame(rows, columns=COLS))
    rep = analyze(df)
    return rep[rep["probleme"] == "Pièce non équilibrée"]


def test_date_invalide_rejoint_sa_piece():
    rows = [["31/02/2026", "0004", "Honoraires", "622000", 30000, None, "AC"],
            ["2026-03-10", "0004", "Fournisseur", "401000", None, 30000, "AC"]]
    assert _desequilibres(rows).empty


def test_ecart_reel_reste_detecte():
    rows = [["2026-03-01", "8", "a", "601000", 100, None, "AC"],
            ["2026-03-01", "8", "b", "401000", None, 90, "AC"],
            ["xx", "8", "c", "445200", 5, None, "AC"]]
    assert len(_desequilibres(rows)) == 1


def test_numero_reutilise_a_plusieurs_dates_ligne_isolee():
    rows = [["2026-03-01", "9", "a", "601000", 100, None, "AC"],
            ["2026-03-01", "9", "b", "401000", None, 100, "AC"],
            ["2026-04-01", "9", "a", "601000", 50, None, "AC"],
            ["2026-04-01", "9", "b", "401000", None, 50, "AC"],
            ["xx", "9", "c", "601000", 10, None, "AC"]]
    assert len(_desequilibres(rows)) == 1
