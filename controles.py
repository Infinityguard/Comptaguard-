"""Logique de contrôle comptable (sans dépendance à Streamlit, donc testable)."""
from __future__ import annotations

import io
import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from tolerance import to_decimal

REQUIRED_COLUMNS = ["date", "piece", "libelle", "compte", "debit", "credit"]
OPTIONAL_COLUMNS = ["journal"]
TEXT_COLUMNS = {"compte", "piece", "journal"}
GRAVITY_ORDER = {"Haute": 0, "Moyenne": 1, "Basse": 2}
REPORT_COLUMNS = ["ligne", "probleme", "gravite", "detail", "journal", "piece",
                  "libelle", "compte", "date", "debit", "credit"]
NO_JOURNAL = "(sans journal)"


# --------------------------------------------------------------------------
# Lecture et préparation
# --------------------------------------------------------------------------
def read_ledger(source, sheet_name=0) -> pd.DataFrame:
    """Lit une feuille Excel en forçant compte/pièce/journal en texte
    (quelle que soit la casse de l'en-tête), pour garder les zéros initiaux."""
    xls = source if isinstance(source, pd.ExcelFile) else pd.ExcelFile(source)
    header = xls.parse(sheet_name, nrows=0)
    dtype = {c: str for c in header.columns if str(c).strip().lower() in TEXT_COLUMNS}
    return xls.parse(sheet_name, dtype=dtype)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]
    if df.columns.duplicated().any():
        dupes = sorted(set(df.columns[df.columns.duplicated()]))
        raise ValueError("Colonnes en double : " + ", ".join(dupes))
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Colonnes obligatoires manquantes : " + ", ".join(missing))
    return df


def _is_missing(value) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return False
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def normalize_account(value):
    if _is_missing(value):
        return pd.NA
    value = re.sub(r"\s+", "", str(value))
    if re.fullmatch(r"\d+\.0", value):  # Excel : 411000 -> "411000.0"
        value = value[:-2]
    return value if value else pd.NA


def normalize_reference(value):
    if _is_missing(value):
        return pd.NA
    value = str(value).strip()
    if re.fullmatch(r"\d+\.0", value):
        value = value[:-2]
    return value if value else pd.NA



def read_account_reference(source) -> tuple[set[str], pd.DataFrame]:
    """Charge un référentiel de comptes fourni par l'utilisateur.

    Le fichier doit contenir une colonne `compte`. Les autres colonnes sont
    conservées pour permettre l'enrichissement futur (libellé, classe, source).
    Cette fonction ne certifie pas que le référentiel est officiellement conforme.
    """
    try:
        ref = pd.read_excel(source, dtype=str)
    except Exception as exc:
        raise ValueError(f"Impossible de lire le référentiel Excel : {exc}") from exc
    ref.columns = [str(c).strip().lower() for c in ref.columns]
    if ref.columns.duplicated().any():
        dupes = sorted(set(ref.columns[ref.columns.duplicated()]))
        raise ValueError("Colonnes en double dans le référentiel : " + ", ".join(dupes))
    if "compte" not in ref.columns:
        raise ValueError("Le référentiel doit contenir une colonne nommée 'compte'.")
    ref["compte"] = ref["compte"].map(normalize_account).astype("string")
    ref = ref[ref["compte"].notna()].copy()
    if ref.empty:
        raise ValueError("Le référentiel ne contient aucun numéro de compte exploitable.")
    duplicates = ref[ref["compte"].duplicated(keep=False)]["compte"].dropna().unique().tolist()
    if duplicates:
        preview = ", ".join(sorted(duplicates)[:10])
        raise ValueError("Comptes en double dans le référentiel : " + preview)
    accounts = set(ref["compte"].astype(str))
    return accounts, ref.reset_index(drop=True)


def make_account_reference_template() -> bytes:
    """Crée un modèle Excel vierge, sans inventer de liste officielle de comptes."""
    output = io.BytesIO()
    template = pd.DataFrame(columns=["compte", "libelle", "classe", "niveau", "source", "applicable"])
    instructions = pd.DataFrame({
        "champ": ["compte", "libelle", "classe", "niveau", "source", "applicable"],
        "description": [
            "Obligatoire : numéro exact du compte selon le référentiel choisi",
            "Libellé officiel ou libellé de référence",
            "Classe comptable si connue",
            "Niveau de détail du compte si connu",
            "Référence du document et de sa version",
            "Préciser le périmètre ou l'entité auquel le compte s'applique",
        ],
    })
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        template.to_excel(writer, sheet_name="Comptes", index=False)
        instructions.to_excel(writer, sheet_name="Instructions", index=False)
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = __import__("openpyxl").styles.Font(bold=True)
            for cells in ws.columns:
                longest = max((len(str(c.value)) if c.value is not None else 0) for c in cells)
                ws.column_dimensions[cells[0].column_letter].width = min(max(longest + 2, 14), 65)
    return output.getvalue()


# --------------------------------------------------------------------------
# Import des formats usuels : écritures, balance générale et grand livre
# --------------------------------------------------------------------------
COLUMN_ALIASES = {
    "date": {"date", "date ecriture", "date écriture", "date operation", "date opération"},
    "piece": {"piece", "pièce", "numero piece", "numéro pièce", "n piece", "n° piece", "n° pièce", "numero de piece", "numéro de pièce", "reference", "référence"},
    "libelle": {"libelle", "libellé", "intitule", "intitulé", "description"},
    "compte": {"compte", "numero compte", "numéro compte", "n compte", "n° compte", "compte general", "compte général"},
    "debit": {"debit", "débit", "mouvement debit", "mouvement débit"},
    "credit": {"credit", "crédit", "mouvement credit", "mouvement crédit"},
    "journal": {"journal", "code journal", "journal code"},
    "solde debiteur": {"solde debiteur", "solde débiteur", "debit solde", "débit solde"},
    "solde crediteur": {"solde crediteur", "solde créditeur", "credit solde", "crédit solde"},
}

def _canonical_header(value: object) -> str:
    return re.sub(r"\s+", " ", str(value).strip().lower().replace("_", " "))

def map_common_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Mappe les intitulés usuels d'exports comptables vers des noms communs."""
    out = df.copy()
    mapping = {}
    for col in out.columns:
        key = _canonical_header(col)
        for canonical, aliases in COLUMN_ALIASES.items():
            if key in {_canonical_header(a) for a in aliases} or key == canonical:
                mapping[col] = canonical
                break
    out = out.rename(columns=mapping)
    if out.columns.duplicated().any():
        dupes = sorted(set(out.columns[out.columns.duplicated()]))
        raise ValueError("Plusieurs colonnes correspondent au même champ : " + ", ".join(dupes))
    return out

def read_uploaded_table(source, sheet_name=0, filename: str | None = None) -> pd.DataFrame:
    """Lit un export Excel ou CSV et harmonise les noms de colonnes.

    Pour les CSV, le séparateur est détecté automatiquement (virgule, point-virgule,
    tabulation ou barre verticale). Les identifiants restent du texte afin de
    préserver les zéros initiaux. Les encodages UTF-8 avec BOM et Windows-1252 sont
    pris en charge. Le type du fichier peut être fourni via ``filename``.
    """
    name = filename or getattr(source, "name", "") or ""
    is_csv = str(name).lower().endswith(".csv")
    if is_csv:
        # Streamlit UploadedFile est seekable ; on revient au début entre essais.
        last_error = None
        for encoding in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                if hasattr(source, "seek"):
                    source.seek(0)
                raw = pd.read_csv(source, sep=None, engine="python", dtype=object,
                                  encoding=encoding, keep_default_na=False)
                break
            except (UnicodeDecodeError, pd.errors.ParserError, ValueError) as exc:
                last_error = exc
        else:
            raise ValueError(f"Impossible de lire le CSV : {last_error}")
        return map_common_columns(raw)

    xls = source if isinstance(source, pd.ExcelFile) else pd.ExcelFile(source)
    raw = xls.parse(sheet_name, dtype=object)
    return map_common_columns(raw)



def assess_import_quality(raw: pd.DataFrame, mode: str = "Écritures comptables") -> tuple[pd.DataFrame, dict]:
    """Évalue la qualité structurelle des données avant analyse comptable.

    Les constats sont indicatifs : ils ne modifient pas les données sources et
    ne concluent pas à une anomalie comptable.
    """
    df = map_common_columns(raw)
    findings: list[dict] = []
    if df.empty:
        findings.append({"niveau": "Élevé", "constat": "Fichier sans données",
                         "nombre_lignes": 0, "detail": "La feuille importée ne contient aucune ligne de données."})
    required = (["compte", "debit", "credit"] if mode == "Balance générale"
                else ["date", "piece", "libelle", "compte", "debit", "credit"])
    missing = [c for c in required if c not in df.columns]
    if missing:
        findings.append({"niveau": "Élevé", "constat": "Colonnes nécessaires absentes",
                         "nombre_lignes": len(df), "detail": "Colonnes non reconnues : " + ", ".join(missing)})
    if df.columns.duplicated().any():
        findings.append({"niveau": "Élevé", "constat": "Colonnes dupliquées",
                         "nombre_lignes": len(df), "detail": "Plusieurs colonnes correspondent au même champ normalisé."})
    empty_mask = df.apply(lambda col: col.map(lambda value: _is_missing(value) or (isinstance(value, str) and value.strip() == "")))
    empty_rows = int(empty_mask.all(axis=1).sum()) if len(df.columns) else len(df)
    if empty_rows:
        findings.append({"niveau": "Faible", "constat": "Lignes entièrement vides",
                         "nombre_lignes": empty_rows, "detail": "Ces lignes seront ignorées ou doivent être vérifiées."})
    amount_cols = [c for c in ("debit", "credit", "solde debiteur", "solde crediteur") if c in df.columns]
    for col in amount_cols:
        bad = sum(not parse_amount(v)[1] for v in df[col])
        if bad:
            findings.append({"niveau": "Élevé", "constat": f"Montants illisibles : {col}",
                             "nombre_lignes": int(bad), "detail": "Des valeurs ne peuvent pas être interprétées comme des montants."})
    if "date" in df.columns and mode != "Balance générale":
        nonempty = df["date"].notna() & df["date"].astype(str).str.strip().ne("")
        parsed_dates = parse_dates(df.loc[nonempty, "date"])
        bad_dates = int(parsed_dates.isna().sum())
        if bad_dates:
            findings.append({"niveau": "Moyen", "constat": "Dates à vérifier",
                             "nombre_lignes": bad_dates, "detail": "Dates renseignées mais non reconnues ; vérifier le format source."})
    if "compte" in df.columns:
        empty_accounts = int((df["compte"].isna() | df["compte"].astype(str).str.strip().eq("")).sum())
        if empty_accounts:
            findings.append({"niveau": "Moyen", "constat": "Numéros de compte manquants",
                             "nombre_lignes": empty_accounts, "detail": "Des lignes ne contiennent pas de numéro de compte."})
    report = pd.DataFrame(findings, columns=["niveau", "constat", "nombre_lignes", "detail"])
    summary = {"nombre_lignes": int(len(df)), "nombre_colonnes": int(len(df.columns)),
               "colonnes_manquantes": missing, "nombre_constats": int(len(report)),
               "lignes_entierement_vides": empty_rows}
    return report, summary


def normalize_trial_balance(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalise une balance avec compte et mouvements/soldes débit-crédit.

    Champs acceptés : compte + (debit, credit) ou (solde debiteur, solde crediteur).
    Les totaux de mouvements et de soldes sont contrôlés séparément.
    """
    df = map_common_columns(raw)
    if "compte" not in df.columns:
        raise ValueError("La balance doit contenir une colonne 'compte'.")
    df["compte"] = df["compte"].map(normalize_account).astype("string")
    debit_col = "debit" if "debit" in df.columns else "solde debiteur"
    credit_col = "credit" if "credit" in df.columns else "solde crediteur"
    if debit_col not in df.columns or credit_col not in df.columns:
        raise ValueError("La balance doit contenir débit/crédit ou solde débiteur/solde créditeur.")
    for col in (debit_col, credit_col):
        parsed = [parse_amount(v) for v in df[col]]
        df[col] = [x[0] for x in parsed]
        df[f"{col}_invalide"] = [not x[1] for x in parsed]
    df = df[df["compte"].notna()].copy()
    df.attrs["debit_source"] = debit_col
    df.attrs["credit_source"] = credit_col
    return df

def analyze_trial_balance(raw: pd.DataFrame, tolerance: float = 0.01) -> tuple[pd.DataFrame, dict]:
    """Contrôles arithmétiques de base sur une balance, sans présumer de sa conformité légale."""
    df = normalize_trial_balance(raw)
    debit_col, credit_col = df.attrs["debit_source"], df.attrs["credit_source"]
    issues = []
    for col in (debit_col, credit_col):
        bad = df[df[f"{col}_invalide"]]
        for _, row in bad.iterrows():
            issues.append({"compte": row["compte"], "probleme": f"Valeur non numérique : {col}",
                           "gravite": "Moyenne", "detail": "Vérifier la valeur source."})
    for _, row in df[(df[debit_col] < 0) | (df[credit_col] < 0)].iterrows():
        issues.append({"compte": row["compte"], "probleme": "Montant négatif dans la balance",
                       "gravite": "Moyenne", "detail": "Vérifier le sens et la présentation du solde."})
    duplicates = df[df["compte"].duplicated(keep=False)]
    for account in duplicates["compte"].dropna().unique():
        issues.append({"compte": account, "probleme": "Compte présent plusieurs fois",
                       "gravite": "Moyenne", "detail": "Vérifier si les lignes doivent être regroupées."})
    total_debit = float(df[debit_col].sum())
    total_credit = float(df[credit_col].sum())
    gap = total_debit - total_credit
    if abs(gap) > tolerance:
        issues.append({"compte": "(total)", "probleme": "Balance déséquilibrée", "gravite": "Haute",
                       "detail": f"Écart débit − crédit : {gap:,.2f}".replace(",", " ")})
    summary = {"nombre_comptes": int(len(df)), "total_debit": total_debit,
               "total_credit": total_credit, "ecart": gap, "debit_colonne": debit_col,
               "credit_colonne": credit_col}
    return pd.DataFrame(issues, columns=["compte", "probleme", "gravite", "detail"]), summary



def reconcile_ledger_balance(ledger_raw: pd.DataFrame, balance_raw: pd.DataFrame, tolerance: float = 0.01) -> tuple[pd.DataFrame, dict]:
    """Compare les mouvements débit/crédit du grand livre et d'une balance de mouvements.

    Refuse les balances en soldes débiteurs/créditeurs : sans à-nouveaux et période
    identique, on ne peut pas les comparer directement aux mouvements du grand livre.
    """
    ledger = map_common_columns(ledger_raw)
    if not {"compte", "debit", "credit"}.issubset(ledger.columns):
        raise ValueError("Le grand livre doit contenir les colonnes compte, débit et crédit.")
    balance = map_common_columns(balance_raw)
    if not {"compte", "debit", "credit"}.issubset(balance.columns):
        raise ValueError("Pour ce rapprochement, la balance doit présenter les mouvements débit et crédit par compte, et non uniquement les soldes.")

    def clean(frame, label):
        out = frame[["compte", "debit", "credit"]].copy()
        out["compte"] = out["compte"].map(normalize_account)
        if out["compte"].isna().any():
            raise ValueError(f"Le document {label} contient des comptes vides ou invalides.")
        for col in ("debit", "credit"):
            parsed = [parse_amount(v) for v in out[col]]
            if any(not valid for _, valid in parsed):
                raise ValueError(f"Le document {label} contient des montants non numériques dans la colonne {col}.")
            out[col] = [value for value, _ in parsed]
        return out.groupby("compte", as_index=False)[["debit", "credit"]].sum()

    led = clean(ledger, "grand livre").rename(columns={"debit": "debit_grand_livre", "credit": "credit_grand_livre"})
    bal = clean(balance, "balance").rename(columns={"debit": "debit_balance", "credit": "credit_balance"})
    result = led.merge(bal, on="compte", how="outer", indicator=True)
    for col in ("debit_grand_livre", "credit_grand_livre", "debit_balance", "credit_balance"):
        result[col] = result[col].fillna(0)
    result["ecart_debit"] = result["debit_grand_livre"] - result["debit_balance"]
    result["ecart_credit"] = result["credit_grand_livre"] - result["credit_balance"]
    result["ecart_net"] = (result["debit_grand_livre"] - result["credit_grand_livre"]) - (result["debit_balance"] - result["credit_balance"])
    result["statut"] = result.apply(lambda r: "À vérifier" if r["_merge"] != "both" or abs(r["ecart_debit"]) > tolerance or abs(r["ecart_credit"]) > tolerance else "Concordant", axis=1)
    result["motif"] = result.apply(lambda r: "Compte absent d'un des documents" if r["_merge"] != "both" else ("Écart de mouvements" if r["statut"] == "À vérifier" else "Mouvements concordants"), axis=1)
    result = result.drop(columns=["_merge"]).sort_values(["statut", "compte"], ascending=[True, True]).reset_index(drop=True)
    summary = {
        "comptes_comparés": int(len(result)),
        "comptes_concordants": int((result["statut"] == "Concordant").sum()),
        "comptes_a_verifier": int((result["statut"] == "À vérifier").sum()),
        "ecart_total_debit": float(result["ecart_debit"].sum()),
        "ecart_total_credit": float(result["ecart_credit"].sum()),
        "tolerance": tolerance,
    }
    return result, summary


def parse_amount(value):
    """Retourne (montant, valide).

    Cellule vide (ou « - ») : (0.0, True), c'est normal en comptabilité.
    Texte non convertible : (0.0, False).
    Accepte « 15 000,50 », « 15.000 », « 15,000 », « (500) », « 85 000 FCFA ».
    """
    if _is_missing(value):
        return 0.0, True
    if isinstance(value, (bool, np.bool_)):
        return 0.0, False
    if isinstance(value, (int, float, np.integer, np.floating)):
        f = float(value)
        return (f, True) if np.isfinite(f) else (0.0, False)
    if not isinstance(value, str):
        return 0.0, False

    s = value.strip()
    if s in {"", "-", "–", "—"}:
        return 0.0, True
    s = re.sub(r"(?i)\s*(fcfa|xof|cfa|f)\s*$", "", s)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    if s.startswith("-"):
        negative, s = True, s[1:]
    s = re.sub(r"[\s\u00a0\u202f']", "", s)
    if not re.fullmatch(r"[0-9.,]+", s):
        return 0.0, False
    if "," in s and "." in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        thou = "." if dec == "," else ","
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        s = s.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", s) else s.replace(",", ".")
    elif "." in s and re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        number = float(s)
    except ValueError:
        return 0.0, False
    return (-number if negative else number), True


def parse_dates(series: pd.Series) -> pd.Series:
    """Dates ISO (AAAA-MM-JJ) lues strictement ; le reste en jour/mois/année.
    (dayfirst=True seul inverserait jour et mois sur « 2026-03-05 ».)"""
    out = pd.to_datetime(series, errors="coerce", format="ISO8601")
    rest = out.isna() & series.notna()
    if rest.any():
        out = out.copy()
        out[rest] = pd.to_datetime(series[rest], errors="coerce", dayfirst=True, format="mixed")
    return out


def prepare_dataframe(raw: pd.DataFrame) -> pd.DataFrame:
    df = normalize_columns(raw)
    # Numéro de ligne Excel réel (en-têtes en ligne 1), calculé avant tout filtrage.
    df["ligne"] = np.arange(2, len(df) + 2)

    check_cols = [c for c in REQUIRED_COLUMNS + OPTIONAL_COLUMNS if c in df.columns]
    blank = pd.concat(
        [df[c].isna() | df[c].astype(str).str.strip().eq("") for c in check_cols], axis=1
    ).all(axis=1)
    n_blank = int(blank.sum())
    df = df.loc[~blank].copy()

    df["compte"] = df["compte"].map(normalize_account).astype("string")
    df["piece"] = df["piece"].map(normalize_reference).astype("string")
    if "journal" in df.columns:
        df["journal"] = df["journal"].map(normalize_reference).astype("string")
    df["date"] = parse_dates(df["date"])

    for col in ("debit", "credit"):
        parsed = [parse_amount(v) for v in df[col]]
        df[col] = pd.Series([p[0] for p in parsed], index=df.index, dtype=float)
        df[f"{col}_invalide"] = pd.Series([not p[1] for p in parsed], index=df.index, dtype=bool)

    df.attrs["lignes_vides"] = n_blank
    return df


# --------------------------------------------------------------------------
# Contrôles
# --------------------------------------------------------------------------
def build_account_regex(classes: str = "12345678", allow_suffix: bool = True) -> str:
    digits = "".join(sorted({ch for ch in str(classes) if ch.isdigit()})) or "12345678"
    suffix = r"[A-Za-z0-9]{0,12}" if allow_suffix else ""
    return rf"[{digits}][0-9]{{1,7}}{suffix}"


def _issue(rec: dict, problem: str, severity: str, detail: str = "") -> dict:
    return {
        "ligne": rec.get("ligne"), "probleme": problem, "gravite": severity, "detail": detail,
        "journal": rec.get("journal", pd.NA), "piece": rec.get("piece", pd.NA),
        "libelle": rec.get("libelle", pd.NA), "compte": rec.get("compte", pd.NA),
        "date": rec.get("date", pd.NaT), "debit": rec.get("debit", 0.0),
        "credit": rec.get("credit", 0.0),
    }


def _fmt_lines(lines, limit=6) -> str:
    lines = sorted(int(x) for x in lines)
    text = ", ".join(str(x) for x in lines[:limit])
    return text + (f" … (+{len(lines) - limit})" if len(lines) > limit else "")


def analyze(df: pd.DataFrame, window_days: int = 7, min_round_amount: int = 100000,
            round_step: int = 10000, valid_accounts: set[str] | None = None,
            require_same_label: bool = False, account_classes: str = "12345678",
            allow_account_suffix: bool = True, piece_tolerance: float = 0.001) -> pd.DataFrame:
    has_journal = "journal" in df.columns
    issues: list[dict] = []

    def signal(rows, problem, severity, detail=""):
        for rec in rows.to_dict("records"):
            issues.append(_issue(rec, problem, severity, detail))

    if not df.empty:
        # 0. Montants renseignés mais non numériques (une cellule vide est normale).
        signal(df[df["debit_invalide"]], "Débit manquant ou non numérique", "Moyenne",
               "Valeur non convertible en nombre ; traitée comme 0 dans les calculs.")
        signal(df[df["credit_invalide"]], "Crédit manquant ou non numérique", "Moyenne",
               "Valeur non convertible en nombre ; traitée comme 0 dans les calculs.")

        # 1. Doublons possibles (même pièce, compte, débit, crédit ; dates proches).
        keys = (["journal"] if has_journal else []) + ["piece", "compte", "debit", "credit"]
        cand = df[df["date"].notna() & df["piece"].notna() & df["compte"].notna()
                  & ((df["debit"] != 0) | (df["credit"] != 0))].copy()
        if has_journal:
            cand["journal"] = cand["journal"].fillna(NO_JOURNAL).astype(str)
        if require_same_label:
            cand["_lib"] = cand["libelle"].fillna("").astype(str).str.strip().str.lower()
            keys.append("_lib")
        partners: dict[int, set[int]] = defaultdict(set)
        for _, group in cand.groupby(keys, dropna=False):
            if len(group) < 2:
                continue
            recs = group.sort_values(["date", "ligne"], kind="stable").to_dict("records")
            left = 0
            for right, b in enumerate(recs):
                while (b["date"] - recs[left]["date"]).days > window_days:
                    left += 1
                for k in range(left, right):
                    a = recs[k]
                    partners[int(a["ligne"])].add(int(b["ligne"]))
                    partners[int(b["ligne"])].add(int(a["ligne"]))
        if partners:
            by_line = {int(r["ligne"]): r for r in df.to_dict("records")}
            extra = " et même libellé" if require_same_label else ""
            for line in sorted(partners):
                issues.append(_issue(
                    by_line[line], f"Doublon possible ≤ {window_days} jours", "Haute",
                    f"Même pièce, compte et montants{extra} que la/les ligne(s) "
                    f"{_fmt_lines(partners[line])} ; vérifier la pièce justificative."))

        # 2. Débit et crédit simultanément positifs
        signal(df[(df["debit"] > 0) & (df["credit"] > 0)],
               "Débit et crédit sur la même ligne", "Haute")
        # 3. Aucun montant
        signal(df[(df["debit"] == 0) & (df["credit"] == 0)], "Aucun montant", "Moyenne")
        # 3 bis. Montant négatif
        signal(df[(df["debit"] < 0) | (df["credit"] < 0)], "Montant négatif", "Moyenne",
               "Montant négatif inhabituel au débit ou au crédit : vérifier le sens de l'écriture.")
        # 4. Date invalide ou manquante
        signal(df[df["date"].isna()], "Date invalide ou manquante", "Moyenne")
        # 5. Pièce manquante
        signal(df[df["piece"].isna()], "Numéro de pièce manquant", "Moyenne")
        # 6. Montants ronds élevés
        round_mask = (
            ((df["debit"] >= min_round_amount) & (df["debit"] % round_step == 0))
            | ((df["credit"] >= min_round_amount) & (df["credit"] % round_step == 0))
        )
        signal(df[round_mask], "Montant rond élevé (à vérifier)", "Basse")

        # 7. Équilibre par pièce, date et (si disponible) journal.
        # La date évite qu'un numéro de pièce réutilisé plus tard masque un écart.
        # Sans journal, des pièces distinctes le même jour et portant le même numéro
        # restent impossibles à distinguer : l'interface doit signaler cette limite.
        wp = df[df["piece"].notna()].copy()
        wp["piece"] = wp["piece"].astype(str)
        wp["_date_key"] = wp["date"].dt.strftime("%Y-%m-%d").fillna("(date invalide)")
        keys_bal = ["_date_key", "piece"]
        if has_journal:
            wp["journal"] = wp["journal"].fillna(NO_JOURNAL).astype(str)
            keys_bal = ["_date_key", "journal", "piece"]
        if not wp.empty:
            # Sommes exactes en Decimal : un arrondi flottant à 2 décimales masquerait
            # les écarts inférieurs à 0,005 alors que la tolérance affichée est plus fine.
            wp["_debit_exact"] = [to_decimal(v) for v in wp["debit"]]
            wp["_credit_exact"] = [to_decimal(v) for v in wp["credit"]]
            grouped = wp.groupby(keys_bal, dropna=False)
            totals = grouped.agg(debit=("_debit_exact", lambda s: sum(s, Decimal(0))),
                                 credit=("_credit_exact", lambda s: sum(s, Decimal(0))))
            lines_by_key = grouped["ligne"].agg(list)
            tolerance_exact = to_decimal(piece_tolerance)
            bad = totals[(totals["debit"] - totals["credit"]).abs() > tolerance_exact]
            for idx, row in bad.iterrows():
                values = idx if isinstance(idx, tuple) else (idx,)
                key_values = dict(zip(keys_bal, values))
                journal = key_values.get("journal", pd.NA)
                piece = key_values["piece"]
                date_key = key_values["_date_key"]
                date_piece = pd.NaT if date_key == "(date invalide)" else pd.Timestamp(date_key)
                gap = row["debit"] - row["credit"]
                gap_text = f"{gap:,.2f}" if gap == gap.quantize(Decimal("0.01")) else f"{gap:,f}"
                issues.append(_issue(
                    {"ligne": None, "date": date_piece, "journal": journal, "piece": piece,
                     "debit": float(row["debit"]), "credit": float(row["credit"])},
                    "Pièce non équilibrée", "Haute",
                    f"Écart débit − crédit : {gap_text}".replace(",", " ")
                    + f". Lignes : {_fmt_lines(lines_by_key[idx])}."))

        # 8. Format de compte (règle de forme, pas une validation du plan comptable).
        signal(df[df["compte"].isna()], "Numéro de compte manquant", "Moyenne")
        pattern = build_account_regex(account_classes, allow_account_suffix)
        bad_format = df["compte"].notna() & ~df["compte"].str.fullmatch(pattern, na=False)
        signal(df[bad_format], "Format de numéro de compte invalide", "Moyenne",
               "Format attendu (règle indicative, voir paramètres) : "
               f"expression {pattern}")

        # 9. Optionnel : compte absent du référentiel fourni.
        if valid_accounts is not None:
            ref_invalid = df["compte"].notna() & ~df["compte"].isin(valid_accounts)
            signal(df[ref_invalid], "Compte absent du référentiel fourni", "Moyenne",
                   "Comparer au référentiel applicable et vérifier le niveau de détail.")

    report = pd.DataFrame(issues, columns=REPORT_COLUMNS)
    report["ligne"] = pd.array(report["ligne"], dtype="Int64")
    report["date"] = pd.to_datetime(report["date"], errors="coerce")
    for col in ("debit", "credit"):
        report[col] = pd.to_numeric(report[col], errors="coerce").fillna(0.0)
    for col in ("journal", "piece", "libelle", "compte"):
        report[col] = report[col].astype("string")
    if not has_journal:
        report = report.drop(columns="journal")
    if not report.empty:
        report["_o"] = report["gravite"].map(GRAVITY_ORDER).fillna(9)
        report = (report.sort_values(["_o", "ligne", "probleme"], kind="stable",
                                     na_position="last")
                  .drop(columns="_o").reset_index(drop=True))
    return report


def rate_risks(report: pd.DataFrame) -> pd.DataFrame:
    """Ajoute une priorisation explicable des alertes, sans conclure à une fraude.

    Le score est un indice de tri (0–100), pas une probabilité d'erreur ni une
    mesure de matérialité d'audit. Il combine la nature de l'alerte, sa gravité
    initiale et, lorsque disponible, un facteur de montant plafonné.
    """
    result = report.copy()
    if result.empty:
        for col, dtype in (("score_risque", "int64"), ("niveau_risque", "object"),
                           ("justification_risque", "object")):
            result[col] = pd.Series(dtype=dtype)
        return result

    high_keywords = ("déséquilibrée", "non équilibrée", "débit et crédit", "doublon")
    medium_keywords = ("invalide", "manquant", "non numérique", "négatif", "absent du référentiel",
                       "plusieurs fois", "aucun montant")
    scores = []
    reasons = []
    for _, row in result.iterrows():
        problem = str(row.get("probleme", "")).lower()
        severity = str(row.get("gravite", "")).lower()
        if any(k in problem for k in high_keywords):
            score, reason = 75, "Contrôle prioritaire selon la nature de l'alerte."
        elif any(k in problem for k in medium_keywords):
            score, reason = 50, "Incohérence à examiner et à rapprocher des justificatifs."
        elif "montant rond" in problem:
            score, reason = 25, "Signal faible : le montant rond seul ne prouve pas une anomalie."
        else:
            score, reason = {"haute": 70, "moyenne": 45, "basse": 25}.get(severity, 20),                             "Priorisation indicative fondée sur la gravité initiale."

        amount = 0.0
        for col in ("debit", "credit", "montant", "ecart"):
            if col in result.columns:
                try:
                    value = pd.to_numeric(pd.Series([row.get(col)]), errors="coerce").iloc[0]
                    if pd.notna(value):
                        amount = max(amount, abs(float(value)))
                except (TypeError, ValueError):
                    pass
        # Le montant ne peut augmenter le score que de 0 à 15 points.
        if amount >= 10_000_000:
            score += 15
        elif amount >= 1_000_000:
            score += 10
        elif amount >= 100_000:
            score += 5
        score = min(100, int(score))
        if amount:
            reason += f" Facteur montant appliqué (montant détecté : {amount:,.2f}).".replace(",", " ")
        scores.append(score)
        reasons.append(reason)

    result["score_risque"] = scores
    result["niveau_risque"] = ["Élevé" if x >= 70 else "Modéré" if x >= 40 else "Faible" for x in scores]
    result["justification_risque"] = reasons
    return result


def summarize(report: pd.DataFrame) -> pd.DataFrame:
    if report.empty:
        return pd.DataFrame(columns=["gravite", "probleme", "nombre"])
    s = report.groupby(["gravite", "probleme"], dropna=False).size().reset_index(name="nombre")
    s["_o"] = s["gravite"].map(GRAVITY_ORDER).fillna(9)
    return s.sort_values(["_o", "probleme"]).drop(columns="_o").reset_index(drop=True)


# --------------------------------------------------------------------------
# Export Excel
# --------------------------------------------------------------------------
def make_excel_report(report: pd.DataFrame, source_df: pd.DataFrame,
                      params: dict | None = None) -> bytes:
    source = source_df.drop(columns=["debit_invalide", "credit_invalide"], errors="ignore")
    source = source[["ligne"] + [c for c in source.columns if c != "ligne"]]
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        if report.empty:
            pd.DataFrame([{"résultat": "Aucune anomalie détectée par les contrôles activés"}]
                         ).to_excel(writer, sheet_name="Synthèse", index=False)
        else:
            summarize(report).to_excel(writer, sheet_name="Synthèse", index=False)
            report.to_excel(writer, sheet_name="Anomalies", index=False)
        source.to_excel(writer, sheet_name="Données contrôlées", index=False)
        if params:
            rows = [{"paramètre": k, "valeur": str(v)} for k, v in params.items()]
            rows.append({"paramètre": "Avertissement",
                         "valeur": "Les alertes sont des pistes de vérification, "
                                   "pas des preuves d'erreur ou de fraude."})
            pd.DataFrame(rows).to_excel(writer, sheet_name="Paramètres", index=False)

        from openpyxl.styles import Font
        for ws in writer.book.worksheets:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for row in ws.iter_rows():
                for cell in row:
                    if cell.data_type == "f":      # un libellé « =... » reste du texte
                        cell.data_type = "s"
                    elif isinstance(cell.value, datetime):
                        cell.number_format = "dd/mm/yyyy"
            for cells in ws.columns:
                longest = max((len(str(c.value)) if c.value is not None else 0) for c in cells)
                ws.column_dimensions[cells[0].column_letter].width = min(max(longest + 2, 12), 60)
    return output.getvalue()
