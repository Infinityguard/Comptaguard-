import unittest
from decimal import Decimal
import pandas as pd
from stocks import analyze_physical_stock, normalize_stock_columns, stock_template_bytes

class StockTests(unittest.TestCase):
    def test_variance_and_valuation(self):
        df = pd.DataFrame([{"code_article":"A1", "stock_theorique":10, "stock_physique":8, "cout_unitaire":5}])
        report, summary = analyze_physical_stock(df)
        self.assertEqual(report.loc[0, "ecart_quantite"], "-2")
        self.assertEqual(report.loc[0, "ecart_valorise"], "-10")
        self.assertEqual(summary["articles_a_verifier"], 1)

    def test_within_tolerance(self):
        df = pd.DataFrame([{"code_article":"A1", "stock_theorique":"10", "stock_physique":"10.001", "cout_unitaire":"2"}])
        report, _ = analyze_physical_stock(df, tolerance="0.001")
        self.assertEqual(report.loc[0, "statut"], "Concordant")

    def test_missing_required_columns(self):
        with self.assertRaises(ValueError):
            analyze_physical_stock(pd.DataFrame([{"article":"A1"}]))

    def test_same_article_different_lot_allowed(self):
        df = pd.DataFrame([
            {"code_article":"A1", "lot":"L1", "stock_theorique":2, "stock_physique":2},
            {"code_article":"A1", "lot":"L2", "stock_theorique":3, "stock_physique":3},
        ])
        report, summary = analyze_physical_stock(df)
        self.assertEqual(summary["cles_stock_dupliquees"], 0)
        self.assertTrue((report["statut"] == "À vérifier").all())  # coût absent : valorisation à compléter
        self.assertTrue(report["anomalies"].str.contains("Coût unitaire absent").all())

    def test_duplicate_stock_key_flagged(self):
        df = pd.DataFrame([
            {"code_article":"A1", "lot":"L1", "stock_theorique":2, "stock_physique":2},
            {"code_article":"A1", "lot":"L1", "stock_theorique":2, "stock_physique":1},
        ])
        report, summary = analyze_physical_stock(df)
        self.assertEqual(summary["cles_stock_dupliquees"], 1)
        self.assertTrue(report["anomalies"].str.contains("présent plusieurs fois|présent plusieurs", case=False).any() or report["anomalies"].str.contains("présent plusieurs|présent plusieurs", case=False).any() or report["anomalies"].str.contains("Même article", case=False).any())

    def test_template_created(self):
        self.assertTrue(stock_template_bytes().startswith(b"PK"))

if __name__ == "__main__":
    unittest.main()
