"""Gestion exacte et traçable des tolérances monétaires.

Les tolérances sont des seuils de signalement, pas une autorisation de modifier
ou d'effacer les montants sources. Utiliser Decimal évite les artefacts binaires
classiques des nombres flottants pour les montants décimaux.
"""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from collections.abc import Iterable
import pandas as pd


def to_decimal(value) -> Decimal:
    """Convertit une valeur en Decimal à partir de sa représentation textuelle."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return Decimal("0")
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if not text:
        return Decimal("0")
    # Support simple des montants français (1 234,56) et format international.
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Montant non numérique : {value!r}") from exc


def compare_amounts(expected, actual, tolerance="0.001") -> dict:
    """Compare deux montants et renvoie l'écart exact et son statut."""
    tol = to_decimal(tolerance)
    if tol < 0:
        raise ValueError("La tolérance ne peut pas être négative.")
    expected_d, actual_d = to_decimal(expected), to_decimal(actual)
    difference = actual_d - expected_d
    return {
        "montant_attendu": str(expected_d),
        "montant_observe": str(actual_d),
        "ecart_signe": str(difference),
        "ecart_absolu": str(abs(difference)),
        "tolerance": str(tol),
        "dans_tolerance": abs(difference) <= tol,
    }


def summarize_differences(values: Iterable, tolerance="0.001") -> dict:
    """Résume des écarts en gardant l'écart net et la somme des valeurs absolues."""
    tol = to_decimal(tolerance)
    diffs = [to_decimal(v) for v in values]
    net = sum(diffs, Decimal("0"))
    abs_sum = sum((abs(v) for v in diffs), Decimal("0"))
    outside = [v for v in diffs if abs(v) > tol]
    return {
        "nombre_ecarts": len(diffs),
        "nombre_ecarts_hors_tolerance": len(outside),
        "ecart_net_signe": str(net),
        "somme_ecarts_absolus": str(abs_sum),
        "ecart_absolu_maximal": str(max((abs(v) for v in diffs), default=Decimal("0"))),
        "tolerance_individuelle": str(tol),
        "alerte_accumulation": abs_sum > tol,
        "compensation_entre_ecarts": net == 0 and abs_sum > 0,
    }


def piece_balance_diagnostics(df: pd.DataFrame, tolerance="0.001") -> tuple[pd.DataFrame, dict]:
    """Calcule l'écart exact par pièce, puis mesure son accumulation.

    Attend des colonnes debit et credit, et utilise date/journal/piece lorsqu'elles
    existent. Les valeurs d'origine ne sont jamais arrondies ni modifiées.
    """
    required = {"debit", "credit", "piece"}
    if not required.issubset(df.columns):
        raise ValueError("Colonnes requises : piece, debit et credit.")
    keys = [c for c in ("date", "journal", "piece") if c in df.columns]
    work = df.copy()
    for col in ("debit", "credit"):
        work[col] = work[col].map(to_decimal)
    rows = []
    for key, group in work.groupby(keys, dropna=False, sort=False):
        vals = key if isinstance(key, tuple) else (key,)
        record = dict(zip(keys, vals))
        debit = sum(group["debit"], Decimal("0"))
        credit = sum(group["credit"], Decimal("0"))
        gap = debit - credit
        record.update({"total_debit": str(debit), "total_credit": str(credit),
                       "ecart_signe": str(gap), "ecart_absolu": str(abs(gap)),
                       "dans_tolerance": abs(gap) <= to_decimal(tolerance),
                       "nombre_lignes": len(group)})
        rows.append(record)
    report = pd.DataFrame(rows)
    summary = summarize_differences(report["ecart_signe"].tolist() if not report.empty else [], tolerance)
    summary["pieces_hors_tolerance"] = int((~report["dans_tolerance"]).sum()) if not report.empty else 0
    return report, summary
