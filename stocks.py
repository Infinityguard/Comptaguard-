"""Contrôles de stocks physiques et rapprochement avec le stock théorique."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
from io import BytesIO
import pandas as pd

ALIASES = {
    "code_article": ["code_article", "code article", "référence", "reference", "sku", "article", "code"],
    "designation": ["designation", "désignation", "libelle", "libellé", "nom article", "produit"],
    "stock_theorique": ["stock_theorique", "stock théorique", "quantite_theorique", "quantité théorique", "qté théorique", "quantité système", "stock systeme", "stock système"],
    "stock_physique": ["stock_physique", "stock physique", "quantite_physique", "quantité physique", "qté comptée", "quantite comptee", "comptage"],
    "cout_unitaire": ["cout_unitaire", "coût unitaire", "prix unitaire", "valeur unitaire", "cout moyen", "coût moyen", "cmup"],
    "emplacement": ["emplacement", "magasin", "entrepôt", "entrepot", "rayon", "site"],
    "lot": ["lot", "numéro de lot", "numero lot", "batch"],
}

def _norm(s: object) -> str:
    return " ".join(str(s).strip().lower().replace("_", " ").split())

def _decimal(value: object) -> Decimal | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    s = str(value).strip().replace("\u00a0", "").replace(" ", "")
    if "," in s and "." in s:
        # Le séparateur décimal est le dernier séparateur rencontré.
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None

def normalize_stock_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    norm_map = {_norm(c): c for c in out.columns}
    rename = {}
    for target, aliases in ALIASES.items():
        for alias in aliases:
            if _norm(alias) in norm_map:
                rename[norm_map[_norm(alias)]] = target
                break
    out = out.rename(columns=rename)
    return out

def analyze_physical_stock(df: pd.DataFrame, tolerance: str = "0") -> tuple[pd.DataFrame, dict]:
    data = normalize_stock_columns(df)
    required = ["code_article", "stock_theorique", "stock_physique"]
    missing = [c for c in required if c not in data.columns]
    if missing:
        raise ValueError("Colonnes obligatoires manquantes : " + ", ".join(missing))
    if "designation" not in data.columns: data["designation"] = ""
    if "cout_unitaire" not in data.columns: data["cout_unitaire"] = None
    if "emplacement" not in data.columns: data["emplacement"] = ""
    if "lot" not in data.columns: data["lot"] = ""
    data["code_article"] = data["code_article"].fillna("").astype(str).str.strip()
    data["stock_theorique_decimal"] = data["stock_theorique"].map(_decimal)
    data["stock_physique_decimal"] = data["stock_physique"].map(_decimal)
    data["cout_unitaire_decimal"] = data["cout_unitaire"].map(_decimal)
    tol = Decimal(str(tolerance))
    results = []
    # Une référence peut exister dans plusieurs emplacements ou lots : la clé de doublon utilise ces dimensions lorsqu’elles sont renseignées.
    key_cols = ["code_article"]
    if data["emplacement"].astype(str).str.strip().ne("").any(): key_cols.append("emplacement")
    if data["lot"].astype(str).str.strip().ne("").any(): key_cols.append("lot")
    keys = data[key_cols].fillna("").astype(str).apply(lambda r: "|".join(v.strip() for v in r), axis=1)
    duplicate_keys = set(keys[(data["code_article"].ne("")) & keys.duplicated(keep=False)])
    for idx, row in data.iterrows():
        code = row["code_article"]
        theo, phys, cost = row["stock_theorique_decimal"], row["stock_physique_decimal"], row["cout_unitaire_decimal"]
        issues = []
        if not code: issues.append("Code article manquant")
        if theo is None: issues.append("Stock théorique absent ou illisible")
        if phys is None: issues.append("Stock physique absent ou illisible")
        if keys.iloc[list(data.index).index(idx)] in duplicate_keys: issues.append("Même article/emplacement/lot présent plusieurs fois")
        if theo is not None and theo < 0: issues.append("Stock théorique négatif à vérifier")
        if phys is not None and phys < 0: issues.append("Stock physique négatif à vérifier")
        variance = phys - theo if phys is not None and theo is not None else None
        value_variance = variance * cost if variance is not None and cost is not None else None
        if variance is not None and abs(variance) > tol: issues.append("Écart entre stock physique et théorique")
        if variance is not None and cost is None: issues.append("Coût unitaire absent : valorisation impossible")
        if not issues: status = "Concordant"
        elif variance is not None and abs(variance) <= tol and len(issues) == 0: status = "Concordant dans la tolérance"
        else: status = "À vérifier"
        results.append({
            "code_article": code, "designation": row["designation"], "emplacement": row["emplacement"], "lot": row["lot"],
            "stock_theorique": str(theo) if theo is not None else "", "stock_physique": str(phys) if phys is not None else "",
            "ecart_quantite": str(variance) if variance is not None else "", "cout_unitaire": str(cost) if cost is not None else "",
            "ecart_valorise": str(value_variance) if value_variance is not None else "", "statut": status,
            "anomalies": "; ".join(issues), "ligne_source": int(idx) + 2,
        })
    report = pd.DataFrame(results)
    valid_var = [r for r in results if r["ecart_quantite"] != ""]
    net = sum((Decimal(r["ecart_quantite"]) for r in valid_var), Decimal("0"))
    absolute = sum((abs(Decimal(r["ecart_quantite"])) for r in valid_var), Decimal("0"))
    valued = [r for r in results if r["ecart_valorise"] != ""]
    summary = {
        "articles_controles": len(report),
        "articles_a_verifier": int((report["statut"] == "À vérifier").sum()) if not report.empty else 0,
        "articles_concordants": int((report["statut"] == "Concordant").sum()) if not report.empty else 0,
        "cles_stock_dupliquees": len(duplicate_keys),
        "ecart_quantite_net": str(net),
        "somme_ecarts_quantite_absolus": str(absolute),
        "ecart_valorise_net": str(sum((Decimal(r["ecart_valorise"]) for r in valued), Decimal("0"))),
        "somme_ecarts_valorises_absolus": str(sum((abs(Decimal(r["ecart_valorise"])) for r in valued), Decimal("0"))),
    }
    return report, summary

def stock_template_bytes() -> bytes:
    df = pd.DataFrame([
        {"code_article": "ART-001", "designation": "Exemple article A", "emplacement": "MAG-01", "lot": "LOT-A", "stock_theorique": 100, "stock_physique": 98, "cout_unitaire": 1500},
        {"code_article": "ART-002", "designation": "Exemple article B", "emplacement": "MAG-01", "lot": "LOT-B", "stock_theorique": 50, "stock_physique": 50, "cout_unitaire": 2200},
    ])
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Inventaire")
        pd.DataFrame([{"Note": "Remplacez les lignes d'exemple. Les quantités doivent être dans la même unité de mesure."}]).to_excel(writer, index=False, sheet_name="Instructions")
    return buffer.getvalue()
