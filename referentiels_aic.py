"""Référentiels annuels : fichier AIC (CVPC / marché / typologie) et transporteurs avec numéro de contrat par campagne.

Chaque campagne a ses propres référentiels : on importe les nouveaux fichiers chaque année sans toucher à l'historique.
Les noms de transporteurs figurant sur les tickets sont rapprochés des fiches (normalisation + alias à valider).
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from io import BytesIO

import pandas as pd

from security_db import connect, log_event
from emission_factures import _now

TYPO_NON = "Non Enclavée"
TYPO_ENC = "Enclavée"
TYPO_UNDEFINED = "À définir"


def norm(value) -> str:
    """Clé de comparaison : sans accents, majuscules, ponctuation et espaces uniformisés, zéros de tête ignorés (SILO 01 = SILO 1)."""
    if value is None or (isinstance(value, float) and value != value):
        return ""
    s = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", " ", s).upper().strip()
    return re.sub(r"\b0+(\d)", r"\1", re.sub(r"\s+", " ", s))


def norm_typo(value) -> str | None:
    n = norm(value)
    if not n:
        return None
    if n.startswith("NON") or n in {"NE", "N"}:
        return TYPO_NON
    if "ENCLAV" in n or n in {"E", "OUI"}:
        return TYPO_ENC
    return None


# --------------------------------------------------------------------------
# Base de données
# --------------------------------------------------------------------------
def init_referentiels_db() -> None:
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS reference_markets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign TEXT NOT NULL,
            cvpc TEXT NOT NULL DEFAULT '', cvpc_norm TEXT NOT NULL DEFAULT '',
            market TEXT NOT NULL, market_norm TEXT NOT NULL,
            commune TEXT NOT NULL DEFAULT '',
            typo TEXT NOT NULL CHECK(typo IN ('Enclavée','Non Enclavée')),
            source_file TEXT NOT NULL DEFAULT '',
            imported_by TEXT NOT NULL, imported_at TEXT NOT NULL,
            UNIQUE(campaign, cvpc_norm, market_norm)
        );
        CREATE TABLE IF NOT EXISTS transporter_contracts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign TEXT NOT NULL,
            transporter_id INTEGER NOT NULL REFERENCES transporters(id),
            contract_no TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            imported_by TEXT NOT NULL, imported_at TEXT NOT NULL,
            UNIQUE(campaign, transporter_id)
        );
        CREATE TABLE IF NOT EXISTS transporter_aliases (
            alias_norm TEXT PRIMARY KEY,
            alias_label TEXT NOT NULL,
            transporter_id INTEGER NOT NULL REFERENCES transporters(id),
            created_by TEXT NOT NULL, created_at TEXT NOT NULL
        );
        """)


# --------------------------------------------------------------------------
# Lecture tolérante des fichiers (détection de l'en-tête, synonymes de colonnes)
# --------------------------------------------------------------------------
# (champ, intitulés exacts, mots-clés) — traités dans cet ordre ; un intitulé n'est attribué qu'à un seul champ.
MARKET_COLUMNS = [
    ("typo", ["typo", "typologie", "type", "zone", "enclavement", "type de zone"], ["TYPO", "ENCLAV"]),
    ("cvpc", ["cvpc", "code cvpc", "centre cvpc"], ["CVPC"]),
    ("commune", ["commune"], ["COMMUNE"]),
    ("market", ["marche", "n marche", "numero marche", "nom marche", "code marche"], ["MARCHE"]),
]
TRANSPORTER_COLUMNS = [
    ("contract", ["n contrat", "numero contrat", "numero de contrat", "contrat", "no contrat"], ["CONTRAT"]),
    ("ifu", ["ifu"], ["IFU"]),
    ("phone", ["tel", "telephone", "contact"], ["TEL", "CONTACT"]),
    ("id_piece", ["n piece d identite", "piece d identite", "npi", "cip"], ["IDENTITE", "PIECE"]),
    ("name", ["nom prenoms", "nom et prenoms", "nom prenom", "transporteur", "nom", "raison sociale"],
     ["TRANSPORTEUR", "RAISON SOCIALE", "NOM", "PRENOM"]),
]


def _locate(df: pd.DataFrame, spec: list, required: list[str]):
    """Retourne (tableau avec en-tête correct, {champ: colonne}, colonnes ignorées, décalage de lignes)."""
    def mapping(cols):
        found, used = {}, set()
        for field, aliases, keywords in spec:
            exact = {norm(a) for a in aliases}
            pick = next((c for c in cols if c not in used and norm(c) in exact), None)
            if pick is None:
                pick = next((c for c in cols if c not in used and any(k in norm(c).split() or k in norm(c) for k in keywords)), None)
            if pick is not None:
                found[field] = pick
                used.add(pick)
        return found

    found = mapping(list(df.columns))
    offset = 0
    if not all(r in found for r in required):
        for i in range(min(15, len(df))):
            cand = [str(v) for v in df.iloc[i].tolist()]
            f = mapping(cand)
            if all(r in f for r in required):
                body = df.iloc[i + 1:].copy()
                body.columns = cand
                df, found, offset = body.reset_index(drop=True), f, i + 1
                break
    missing = [r for r in required if r not in found]
    if missing:
        raise ValueError("Colonnes introuvables : " + ", ".join(missing) + ". Utilisez le modèle ou renommez les colonnes.")
    ignored = [str(c) for c in df.columns if c not in found.values()]
    return df, found, ignored, offset


def _txt(v) -> str:
    if v is None or (isinstance(v, float) and v != v):
        return ""
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s  # 12345.0 -> 12345 (Excel)


def parse_markets(df: pd.DataFrame) -> dict:
    df, cols, ignored, offset = _locate(df, MARKET_COLUMNS, ["market", "typo"])
    rows, rejected, seen = [], [], {}
    for idx, r in df.iterrows():
        line = int(idx) + offset + 2
        market = _txt(r[cols["market"]])
        raw_typo = _txt(r[cols["typo"]])
        cvpc = _txt(r[cols["cvpc"]]) if "cvpc" in cols else ""
        commune = _txt(r[cols["commune"]]) if "commune" in cols else ""
        if not market and not raw_typo and not cvpc:
            continue  # ligne vide
        if not market:
            rejected.append({"ligne": line, "raison": "Marché absent"})
            continue
        typo = norm_typo(raw_typo)
        if typo is None:
            rejected.append({"ligne": line, "raison": f"Typologie non reconnue : « {raw_typo} » (attendu : enclavée / non enclavée)"})
            continue
        key = (norm(cvpc), norm(market))
        if key in seen:
            if seen[key] != typo:
                rejected.append({"ligne": line, "raison": f"Doublon contradictoire pour {cvpc or '—'} / {market}"})
            continue
        seen[key] = typo
        rows.append({"cvpc": cvpc, "market": market, "commune": commune, "typo": typo})
    return {"rows": rows, "rejected": rejected, "ignored_columns": ignored}


def parse_transporters(df: pd.DataFrame) -> dict:
    df, cols, ignored, offset = _locate(df, TRANSPORTER_COLUMNS, ["name", "contract"])
    rows, rejected, seen = [], [], {}
    for idx, r in df.iterrows():
        line = int(idx) + offset + 2
        name = _txt(r[cols["name"]])
        contract = _txt(r[cols["contract"]])
        if not name and not contract:
            continue
        if not name or not contract:
            rejected.append({"ligne": line, "raison": "Nom ou numéro de contrat absent"})
            continue
        key = norm(name)
        if key in seen:
            if seen[key] != contract:
                rejected.append({"ligne": line, "raison": f"{name} apparaît avec deux contrats différents ({seen[key]} / {contract})"})
            continue
        seen[key] = contract
        rows.append({"name": name, "contract_no": contract, **{k: _txt(r[cols[k]]) if k in cols else ""
                                                                for k in ("phone", "ifu", "id_piece")}})
    return {"rows": rows, "rejected": rejected, "ignored_columns": ignored}


# --------------------------------------------------------------------------
# Enregistrement
# --------------------------------------------------------------------------
def save_markets(rows: list[dict], campaign: str, actor: str, replace=False, source_file="") -> dict:
    campaign = str(campaign).strip()
    if not campaign:
        raise ValueError("La campagne est obligatoire.")
    if not rows:
        raise ValueError("Aucune ligne valide à importer.")
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        if replace:
            con.execute("DELETE FROM reference_markets WHERE campaign=?", (campaign,))
        for r in rows:
            con.execute("""INSERT INTO reference_markets(campaign,cvpc,cvpc_norm,market,market_norm,commune,typo,source_file,imported_by,imported_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(campaign,cvpc_norm,market_norm) DO UPDATE SET typo=excluded.typo, commune=excluded.commune,
                  cvpc=excluded.cvpc, market=excluded.market, source_file=excluded.source_file,
                  imported_by=excluded.imported_by, imported_at=excluded.imported_at
                WHERE typo<>excluded.typo OR commune<>excluded.commune""",
                              (campaign, r["cvpc"], norm(r["cvpc"]), r["market"], norm(r["market"]), r["commune"], r["typo"],
                               source_file, actor, _now()))
        total = con.execute("SELECT COUNT(*) FROM reference_markets WHERE campaign=?", (campaign,)).fetchone()[0]
    log_event(actor, "IMPORT_REFERENTIEL_AIC", f"campagne={campaign}; lignes={len(rows)}; remplacement={replace}; fichier={source_file}")
    return {"imported": len(rows), "total_campaign": total}


def list_markets(campaign: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute(
            "SELECT cvpc,market,commune,typo,source_file,imported_at FROM reference_markets WHERE campaign=? ORDER BY cvpc,market", (campaign,))]


def lookup_typo(campaign: str, cvpc: str, market: str) -> str | None:
    """Typologie du couple (CVPC, marché) pour la campagne ; None si absent ou ambigu."""
    m = norm(market)
    if not m:
        return None
    with connect() as con:
        row = con.execute("SELECT typo FROM reference_markets WHERE campaign=? AND cvpc_norm=? AND market_norm=?",
                          (campaign, norm(cvpc), m)).fetchone()
        if row:
            return row["typo"]
        found = {r["typo"] for r in con.execute("SELECT typo FROM reference_markets WHERE campaign=? AND market_norm=?", (campaign, m))}
    return found.pop() if len(found) == 1 else None


def save_transporters(rows: list[dict], campaign: str, actor: str, source_file="") -> dict:
    campaign = str(campaign).strip()
    if not campaign:
        raise ValueError("La campagne est obligatoire.")
    if not rows:
        raise ValueError("Aucune ligne valide à importer.")
    created = 0
    with connect() as con:
        con.execute("BEGIN IMMEDIATE")
        existing = {norm(r["name"]): r["id"] for r in con.execute("SELECT id,name FROM transporters")}
        for r in rows:
            tid = existing.get(norm(r["name"]))
            if tid is None:
                tid = con.execute("INSERT INTO transporters(name,phone,ifu,id_piece,contract_no,created_by,created_at) VALUES(?,?,?,?,?,?,?)",
                                  (r["name"], r["phone"], r["ifu"], r["id_piece"], r["contract_no"], actor, _now())).lastrowid
                existing[norm(r["name"])] = tid
                created += 1
            else:  # ne remplace les coordonnées que si le fichier en fournit
                con.execute("""UPDATE transporters SET phone=CASE WHEN ?<>'' THEN ? ELSE phone END,
                    ifu=CASE WHEN ?<>'' THEN ? ELSE ifu END, id_piece=CASE WHEN ?<>'' THEN ? ELSE id_piece END,
                    contract_no=? WHERE id=?""", (r["phone"], r["phone"], r["ifu"], r["ifu"], r["id_piece"], r["id_piece"],
                                                  r["contract_no"], tid))
            con.execute("""INSERT INTO transporter_contracts(campaign,transporter_id,contract_no,source_file,imported_by,imported_at)
                VALUES(?,?,?,?,?,?) ON CONFLICT(campaign,transporter_id) DO UPDATE SET contract_no=excluded.contract_no,
                source_file=excluded.source_file, imported_by=excluded.imported_by, imported_at=excluded.imported_at""",
                        (campaign, tid, r["contract_no"], source_file, actor, _now()))
    log_event(actor, "IMPORT_TRANSPORTEURS_CONTRATS", f"campagne={campaign}; lignes={len(rows)}; nouveaux={created}; fichier={source_file}")
    return {"imported": len(rows), "created": created}


def list_contracts(campaign: str) -> list[dict]:
    with connect() as con:
        return [dict(r) for r in con.execute("""SELECT t.name, c.contract_no, t.phone, t.ifu, t.id_piece
            FROM transporter_contracts c JOIN transporters t ON t.id=c.transporter_id WHERE c.campaign=? ORDER BY t.name""", (campaign,))]


def transporter_for_invoice(transporter_id: int, campaign: str) -> dict:
    """Fiche du transporteur avec le contrat de la campagne (à défaut, le dernier contrat connu)."""
    with connect() as con:
        t = con.execute("SELECT * FROM transporters WHERE id=?", (int(transporter_id),)).fetchone()
        if t is None:
            raise ValueError("Transporteur introuvable.")
        c = con.execute("SELECT contract_no FROM transporter_contracts WHERE campaign=? AND transporter_id=?",
                        (campaign, int(transporter_id))).fetchone()
    out = dict(t)
    out["contract_for_campaign"] = bool(c)
    if c:
        out["contract_no"] = c["contract_no"]
    return out


# --------------------------------------------------------------------------
# Rapprochement des noms de transporteurs
# --------------------------------------------------------------------------
def _alias_map() -> dict[str, int]:
    with connect() as con:
        m = {norm(r["name"]): r["id"] for r in con.execute("SELECT id,name FROM transporters")}
        m.update({r["alias_norm"]: r["transporter_id"] for r in con.execute("SELECT alias_norm,transporter_id FROM transporter_aliases")})
    return m


def resolve_transporter(name: str, amap: dict | None = None) -> int | None:
    return (amap if amap is not None else _alias_map()).get(norm(name))


def same_transporter(ticket_name: str, fiche_name: str, amap: dict | None = None) -> bool:
    amap = amap if amap is not None else _alias_map()
    a, b = norm(ticket_name), norm(fiche_name)
    if a and a == b:
        return True
    ia, ib = amap.get(a), amap.get(b)
    return ia is not None and ia == ib


def unmatched_ticket_transporters() -> list[dict]:
    """Noms de transporteurs présents sur les tickets sans fiche ni alias correspondant."""
    amap = _alias_map()
    with connect() as con:
        rows = con.execute("SELECT transporter AS name, COUNT(*) AS n FROM weighing_tickets WHERE trim(transporter)<>'' GROUP BY transporter").fetchall()
    names = list(amap)
    out = []
    for r in rows:
        if norm(r["name"]) not in amap:
            close = difflib.get_close_matches(norm(r["name"]), names, n=1, cutoff=0.75)
            out.append({"name": r["name"], "tickets": r["n"], "suggestion_norm": close[0] if close else ""})
    return sorted(out, key=lambda x: -x["tickets"])


def link_alias(ticket_name: str, transporter_id: int, actor: str) -> None:
    key = norm(ticket_name)
    if not key:
        raise ValueError("Nom vide.")
    with connect() as con:
        if con.execute("SELECT 1 FROM transporters WHERE id=?", (int(transporter_id),)).fetchone() is None:
            raise ValueError("Transporteur introuvable.")
        con.execute("""INSERT INTO transporter_aliases(alias_norm,alias_label,transporter_id,created_by,created_at) VALUES(?,?,?,?,?)
                       ON CONFLICT(alias_norm) DO UPDATE SET transporter_id=excluded.transporter_id""",
                    (key, str(ticket_name).strip(), int(transporter_id), actor, _now()))
    log_event(actor, "ALIAS_TRANSPORTEUR", f"« {ticket_name} » rattaché à la fiche n° {transporter_id}")


# --------------------------------------------------------------------------
# Modèles de fichiers
# --------------------------------------------------------------------------
def _xlsx(sheet: str, df: pd.DataFrame, note: str) -> bytes:
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        df.to_excel(w, index=False, sheet_name=sheet)
        pd.DataFrame([{"Note": note}]).to_excel(w, index=False, sheet_name="Instructions")
    return buf.getvalue()


def market_template_bytes() -> bytes:
    df = pd.DataFrame([{"CVPC": "EXEMPLE-CVPC", "Marché": "EXEMPLE-MARCHÉ", "Commune": "EXEMPLE", "Typologie": "Non Enclavée"}])
    return _xlsx("Marchés", df, "Exemple fictif à remplacer. Typologie : Enclavée ou Non Enclavée. "
                 "Le fichier AIC peut être importé tel quel si ses colonnes portent ces noms (ou des synonymes courants).")


def transporter_template_bytes() -> bytes:
    df = pd.DataFrame([{"Nom & Prénoms": "EXEMPLE TRANSPORTEUR", "N° Contrat": "000", "Téléphone": "", "IFU": "", "N° Pièce d'identité": ""}])
    return _xlsx("Transporteurs", df, "Exemple fictif à remplacer. Nom et N° de contrat obligatoires ; les autres colonnes sont facultatives.")
