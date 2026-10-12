import pandas as pd
from factures import check_invoices, normalize_invoice_columns

def test_invoice_detects_missing_fields_and_vat_mismatch():
    df = pd.DataFrame([{"numero facture":"F1","HT":100000,"TVA":17000,"TTC":117000,"taux tva":0.18}])
    report, summary = check_invoices(df, vat_rate="0.18", tolerance="1")
    assert summary["factures_controlees"] == 1
    assert summary["factures_a_verifier"] == 1
    assert "TVA déclarée différente" in report.iloc[0]["anomalies"]
    assert "IFU absent" in report.iloc[0]["anomalies"]

def test_invoice_clean_arithmetic_and_ids():
    df = pd.DataFrame([{"numero facture":"F2","IFU":"123","NIM":"NIM1","code MECEF":"XYZ","HT":100000,"TVA":18000,"TTC":118000,"taux TVA":0.18}])
    report, summary = check_invoices(df, vat_rate="0.18", tolerance="1")
    assert summary["factures_a_verifier"] == 0
    assert report.iloc[0]["statut"] == "Cohérence arithmétique OK"
