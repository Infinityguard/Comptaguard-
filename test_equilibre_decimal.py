"""Équilibre des pièces : calcul exact (Decimal), sans arrondi flottant qui masque des écarts."""
import pandas as pd

from controles import analyze, prepare_dataframe


def _report(rows, **kwargs):
    df = prepare_dataframe(pd.DataFrame(rows))
    return analyze(df, **kwargs)


def _rows(debit_a, credit_b):
    return [
        {"date": "2026-01-05", "piece": "P1", "libelle": "a", "compte": "411", "debit": debit_a, "credit": 0},
        {"date": "2026-01-05", "piece": "P1", "libelle": "b", "compte": "701", "debit": 0, "credit": credit_b},
    ]


def test_ecart_inferieur_a_un_demi_centime_n_est_plus_masque():
    report = _report(_rows(100.004, 100.0), piece_tolerance=0.001)
    assert (report["probleme"] == "Pièce non équilibrée").sum() == 1


def test_ecart_dans_la_tolerance_n_est_pas_signale():
    report = _report(_rows(100.004, 100.0), piece_tolerance=0.01)
    assert (report["probleme"] == "Pièce non équilibrée").sum() == 0


def test_artefact_binaire_des_flottants_ne_cree_pas_de_faux_ecart():
    rows = [
        {"date": "2026-01-05", "piece": "P2", "libelle": "x", "compte": "411", "debit": 0.1, "credit": 0},
        {"date": "2026-01-05", "piece": "P2", "libelle": "y", "compte": "411", "debit": 0.2, "credit": 0},
        {"date": "2026-01-05", "piece": "P2", "libelle": "z", "compte": "701", "debit": 0, "credit": 0.3},
    ]
    report = _report(rows, piece_tolerance=0.0)
    assert (report["probleme"] == "Pièce non équilibrée").sum() == 0


def test_message_affiche_l_ecart_exact():
    report = _report(_rows(100.004, 100.0), piece_tolerance=0.001)
    detail = report.loc[report["probleme"] == "Pièce non équilibrée", "detail"].iloc[0]
    assert "0.004" in detail
