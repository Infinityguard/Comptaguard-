"""Équilibre des pièces : calcul exact (Decimal), sans arrondi flottant qui masque des écarts."""
import math

import pandas as pd

from controles import analyze, prepare_dataframe


def pytest_approx(x):
    class _A:
        def __eq__(self, other): return math.isclose(other, x, abs_tol=1e-9)
    return _A()


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


# --- balance et rapprochement : sommes et seuil exacts -------------------------------------
from controles import analyze_trial_balance, reconcile_ledger_balance


def _balance(debit, credit):
    return pd.DataFrame({"compte": ["411", "701"], "debit": [debit, 0], "credit": [0, credit]})


def test_balance_ecart_egal_a_la_tolerance_n_est_pas_signale():
    issues, summary = analyze_trial_balance(_balance(100.01, 100.0), tolerance=0.01)
    assert not (issues["probleme"] == "Balance déséquilibrée").any()
    assert summary["ecart"] == pytest_approx(0.01)


def test_balance_artefact_flottant_ne_cree_pas_d_ecart():
    raw = pd.DataFrame({"compte": ["411", "412", "701"], "debit": [0.1, 0.2, 0], "credit": [0, 0, 0.3]})
    issues, _ = analyze_trial_balance(raw, tolerance=0.0)
    assert not (issues["probleme"] == "Balance déséquilibrée").any()


def test_balance_vrai_ecart_est_signale_avec_son_montant_exact():
    issues, _ = analyze_trial_balance(_balance(100.004, 100.0), tolerance=0.001)
    detail = issues.loc[issues["probleme"] == "Balance déséquilibrée", "detail"].iloc[0]
    assert "0.004" in detail


def test_rapprochement_ecart_egal_a_la_tolerance_est_concordant():
    ledger = pd.DataFrame({"compte": ["411"], "debit": [100.01], "credit": [0]})
    balance = pd.DataFrame({"compte": ["411"], "debit": [100.0], "credit": [0]})
    result, summary = reconcile_ledger_balance(ledger, balance, tolerance=0.01)
    assert result.loc[0, "statut"] == "Concordant"
    assert summary["comptes_a_verifier"] == 0


def test_rapprochement_ecart_au_dessus_de_la_tolerance_est_signale():
    ledger = pd.DataFrame({"compte": ["411"], "debit": [100.02], "credit": [0]})
    balance = pd.DataFrame({"compte": ["411"], "debit": [100.0], "credit": [0]})
    result, _ = reconcile_ledger_balance(ledger, balance, tolerance=0.01)
    assert result.loc[0, "statut"] == "À vérifier"
