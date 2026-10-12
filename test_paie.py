from decimal import Decimal
from paie import calculate_payroll, progressive_tax

def test_monthly_payroll_totals():
    r = calculate_payroll({"matricule":"M1","nom":"Test","prenom":"A","type_salarie":"Mensualisé","salaire_base":200000}, {
        "pension_salarie_taux":"0.036", "pension_employeur_taux":"0.064", "prestations_familiales_employeur_taux":"0.09", "risques_professionnels_employeur_taux":"0.02", "impot_salarial_taux":"0"})
    assert r["salaire_base"] == Decimal("200000")
    assert r["pension_salarie"] == Decimal("7200")
    assert r["net_a_payer"] == Decimal("192800")
    assert r["cout_total_employeur"] == Decimal("234800")

def test_hourly_worker_and_recall():
    r = calculate_payroll({"matricule":"H1","nom":"Test","type_salarie":"Horaire","taux_base":1500,"unites":20,"rappel_regularisation":-1000,"autres_retenues":500}, {"impot_salarial_taux":0})
    assert r["salaire_base"] == Decimal("30000")
    assert r["brut_cash"] == Decimal("29000")
    assert r["net_a_payer"] < r["brut_cash"]

def test_progressive_tax():
    assert progressive_tax(Decimal("100000"), [(60000, "0"), (150000, "0.10"), (None, "0.2")]) == Decimal("4000")
