from decimal import Decimal
import pandas as pd
from tolerance import compare_amounts, summarize_differences, piece_balance_diagnostics


def test_decimal_compare_without_float_artifact():
    result = compare_amounts("0.3", "0.300", "0.001")
    assert result["ecart_signe"] == "0.000"
    assert result["dans_tolerance"] is True


def test_accumulation_not_hidden_by_individual_tolerance():
    summary = summarize_differences(["0.001", "0.001", "-0.002"], "0.001")
    assert Decimal(summary["ecart_net_signe"]) == Decimal("0.000")
    assert Decimal(summary["somme_ecarts_absolus"]) == Decimal("0.004")
    assert summary["compensation_entre_ecarts"] is True
    assert summary["alerte_accumulation"] is True


def test_piece_balance_uses_decimal_and_groups_by_piece():
    df = pd.DataFrame({"date": ["2026-01-01"] * 4,
                       "journal": ["AC"] * 4,
                       "piece": ["P1", "P1", "P2", "P2"],
                       "debit": ["10.001", "0", "5", "0"],
                       "credit": ["10", "0", "0", "5.002"]})
    report, summary = piece_balance_diagnostics(df, "0.001")
    assert len(report) == 2
    assert summary["pieces_hors_tolerance"] == 1
    assert Decimal(summary["ecart_net_signe"]) == Decimal("-0.001")
    assert Decimal(summary["somme_ecarts_absolus"]) == Decimal("0.003")
