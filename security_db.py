"""Authentification, rôles et persistance SQLite pour SYSCOHADA V12.

Prototype de déploiement mono-entreprise. Pour une publication cloud, configurer
SYSCOHADA_DB_PATH vers un stockage persistant et sécurisé avant toute donnée réelle.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import sqlite3
import io
import shutil
import json
from decimal import Decimal, InvalidOperation
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Blocage temporaire après échecs répétés (réglable par variables d'environnement).
MAX_FAILED_LOGINS = int(os.environ.get("SYSCOHADA_MAX_FAILED_LOGINS", "5"))
LOCKOUT_MINUTES = int(os.environ.get("SYSCOHADA_LOCKOUT_MINUTES", "15"))

ROLES = {
    "Administrateur": "Gère les comptes et accède à tous les modules.",
    "Comptable": "Accède aux contrôles comptables, factures et stocks.",
    "Magasinier": "Accède uniquement au module de stock et d'inventaire.",
    "Responsable": "Consulte les modules et suit les campagnes d'inventaire.",
    "Gestionnaire de paie": "Accède aux outils de préparation de paie.",
    "Auditeur": "Accède aux contrôles et rapports en lecture/revue.",
}


def db_path() -> Path:
    raw = os.environ.get("SYSCOHADA_DB_PATH")
    path = Path(raw).expanduser() if raw else Path(__file__).resolve().parent / "data" / "syscohada.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with connect() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            full_name TEXT NOT NULL,
            role TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            created_by TEXT
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_at TEXT NOT NULL,
            username TEXT NOT NULL,
            action TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT ''
        );
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
        BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'Le journal d’audit est append-only'); END;
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
        BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'Le journal d’audit est append-only'); END;
        CREATE TABLE IF NOT EXISTS inventory_campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_name TEXT NOT NULL,
            campaign_date TEXT NOT NULL,
            warehouse TEXT NOT NULL,
            counter TEXT NOT NULL,
            assigned_to TEXT NOT NULL DEFAULT '',
            second_counter TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            reviewed_by TEXT NOT NULL DEFAULT '',
            reviewed_at TEXT NOT NULL DEFAULT '',
            review_notes TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS inventory_campaign_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES inventory_campaigns(id) ON DELETE CASCADE,
            line_no INTEGER NOT NULL,
            code_article TEXT NOT NULL DEFAULT '',
            designation TEXT NOT NULL DEFAULT '',
            emplacement TEXT NOT NULL DEFAULT '',
            lot TEXT NOT NULL DEFAULT '',
            stock_theorique TEXT NOT NULL DEFAULT '',
            stock_physique TEXT NOT NULL DEFAULT '',
            ecart_quantite TEXT NOT NULL DEFAULT '',
            cout_unitaire TEXT NOT NULL DEFAULT '',
            ecart_valorise TEXT NOT NULL DEFAULT '',
            statut TEXT NOT NULL DEFAULT '',
            anomalies TEXT NOT NULL DEFAULT '',
            ligne_source TEXT NOT NULL DEFAULT '',
            saved_by TEXT NOT NULL,
            saved_at TEXT NOT NULL,
            source_file TEXT NOT NULL DEFAULT '',
            UNIQUE(campaign_id, line_no)
        );
        CREATE INDEX IF NOT EXISTS idx_inventory_lines_campaign ON inventory_campaign_lines(campaign_id, line_no);
        """)
        # Migration additive pour les bases créées avec une première V12.
        columns = {row[1] for row in con.execute("PRAGMA table_info(inventory_campaigns)")}
        for name, declaration in (
            ("assigned_to", "TEXT NOT NULL DEFAULT ''"),
            ("reviewed_by", "TEXT NOT NULL DEFAULT ''"),
            ("reviewed_at", "TEXT NOT NULL DEFAULT ''"),
            ("review_notes", "TEXT NOT NULL DEFAULT ''"),
        ):
            if name not in columns:
                con.execute(f"ALTER TABLE inventory_campaigns ADD COLUMN {name} {declaration}")
    # Une sauvegarde locale quotidienne est tentée sans bloquer le démarrage si le stockage est indisponible.
    try:
        automatic_daily_backup()
    except (OSError, sqlite3.Error):
        pass


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, salt_hex, expected = encoded.split("$", 2)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def has_users() -> bool:
    with connect() as con:
        return con.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None


def create_user(username: str, full_name: str, role: str, password: str, created_by: str = "system") -> None:
    username, full_name = username.strip(), full_name.strip()
    if len(username) < 3:
        raise ValueError("L'identifiant doit contenir au moins 3 caractères.")
    if len(full_name) < 2:
        raise ValueError("Renseigne le nom complet de l'utilisateur.")
    if role not in ROLES:
        raise ValueError("Rôle non reconnu.")
    if len(password) < 10:
        raise ValueError("Le mot de passe doit contenir au moins 10 caractères.")
    with connect() as con:
        con.execute("INSERT INTO users(username,full_name,role,password_hash,created_at,created_by) VALUES(?,?,?,?,?,?)",
                    (username, full_name, role, _password_hash(password), _now(), created_by))
    log_event(created_by, "CREATION_UTILISATEUR", f"Identifiant={username}; rôle={role}")


def get_active_user(user_id: int) -> dict[str, Any] | None:
    with connect() as con:
        row = con.execute("SELECT id,username,full_name,role,active FROM users WHERE id=?", (int(user_id),)).fetchone()
    if not row or not row["active"]:
        return None
    return {"id": row["id"], "username": row["username"], "full_name": row["full_name"], "role": row["role"]}


def login_lock_remaining(username: str) -> int:
    """Secondes de blocage restantes pour cet identifiant (0 si la connexion est autorisée).

    Le compteur repose sur le journal d'audit : MAX_FAILED_LOGINS échecs consécutifs depuis la
    dernière connexion réussie bloquent l'identifiant pendant LOCKOUT_MINUTES à partir du
    dernier échec. Aucune colonne supplémentaire n'est nécessaire.
    """
    username = (username or "").strip()
    if not username or MAX_FAILED_LOGINS <= 0:
        return 0
    with connect() as con:
        rows = con.execute(
            "SELECT event_at,action FROM audit_log WHERE username=? COLLATE NOCASE "
            "AND action IN ('ECHEC_CONNEXION','CONNEXION') ORDER BY id DESC LIMIT ?",
            (username, MAX_FAILED_LOGINS)).fetchall()
    failures = []
    for row in rows:
        if row["action"] == "CONNEXION":
            break
        failures.append(datetime.fromisoformat(row["event_at"]))
    if len(failures) < MAX_FAILED_LOGINS:
        return 0
    unlock_at = failures[0] + timedelta(minutes=LOCKOUT_MINUTES)
    remaining = (unlock_at - datetime.now(timezone.utc).astimezone()).total_seconds()
    return max(0, int(remaining))


def authenticate(username: str, password: str) -> dict[str, Any] | None:
    username = (username or "").strip()
    if login_lock_remaining(username) > 0:
        # Pas de vérification du mot de passe pendant le blocage ; cette tentative n'allonge pas le délai.
        log_event(username, "CONNEXION_BLOQUEE", "Tentative pendant le blocage temporaire")
        return None
    with connect() as con:
        row = con.execute("SELECT id,username,full_name,role,password_hash,active FROM users WHERE username=? COLLATE NOCASE", (username,)).fetchone()
    if not row or not row["active"] or not _verify_password(password or "", row["password_hash"]):
        # Ne jamais journaliser le mot de passe ni le stocker dans les détails.
        log_event(username or "inconnu", "ECHEC_CONNEXION", "Identifiants invalides ou compte inactif")
        return None
    return {"id": row["id"], "username": row["username"], "full_name": row["full_name"], "role": row["role"]}


def list_users() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT id,username,full_name,role,active,created_at,created_by FROM users ORDER BY username").fetchall()
    return [dict(r) for r in rows]


def set_user_active(user_id: int, active: bool, actor: str) -> None:
    with connect() as con:
        target = con.execute("SELECT role, active FROM users WHERE id=?", (int(user_id),)).fetchone()
        if target is None:
            raise ValueError("Utilisateur introuvable.")
        if not active and target["role"] == "Administrateur" and target["active"]:
            remaining = con.execute("SELECT COUNT(*) FROM users WHERE role='Administrateur' AND active=1 AND id<>?", (int(user_id),)).fetchone()[0]
            if remaining == 0:
                raise ValueError("Impossible de désactiver le dernier administrateur actif.")
        con.execute("UPDATE users SET active=? WHERE id=?", (1 if active else 0, int(user_id)))
    log_event(actor, "MODIFICATION_UTILISATEUR", f"id={user_id}; actif={active}")


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def log_event(username: str, action: str, details: str = "") -> None:
    with connect() as con:
        con.execute("INSERT INTO audit_log(event_at,username,action,details) VALUES(?,?,?,?)", (_now(), username, action, details[:2000]))


def create_campaign(data: dict[str, str], actor: str) -> None:
    initial_statuses = {"Préparation", "Comptage en cours", "Comptage terminé"}
    if data.get("status") not in initial_statuses:
        raise ValueError("Une campagne ne peut pas être créée directement avec une décision de contrôle.")
    for field, label, limit in (("campaign_name", "Nom de campagne", 200), ("warehouse", "Magasin", 200), ("counter", "Compteur", 200)):
        value = str(data.get(field, "")).strip()
        if not value:
            raise ValueError(f"{label} obligatoire.")
        if len(value) > limit:
            raise ValueError(f"{label} trop long (maximum {limit} caractères).")
    assigned_to = str(data.get("assigned_to", "")).strip()
    with connect() as con:
        if assigned_to:
            assignee = con.execute("SELECT role,active FROM users WHERE username=? COLLATE NOCASE", (assigned_to,)).fetchone()
            if assignee is None or not assignee["active"] or assignee["role"] != "Magasinier":
                raise ValueError("Le responsable affecté doit être un compte Magasinier actif.")
        con.execute("""INSERT INTO inventory_campaigns(campaign_name,campaign_date,warehouse,counter,assigned_to,second_counter,status,notes,created_by,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,?)""", (data["campaign_name"], data["campaign_date"], data["warehouse"], data["counter"], assigned_to,
        data.get("second_counter", ""), data["status"], data.get("notes", ""), actor, _now()))
    log_event(actor, "CREATION_CAMPAGNE_INVENTAIRE", f"Campagne={data['campaign_name']}; magasin={data['warehouse']}; statut={data['status']}")



def save_campaign_lines(campaign_id: int, report, actor: str, source_file: str = "") -> int:
    """Persiste le détail d'un comptage analysé, sans modifier les stocks réels.

    Le rapport est remplacé atomiquement uniquement tant que la campagne n'a pas
    été soumise à validation. Les quantités sont conservées comme texte décimal
    afin d'éviter les conversions flottantes et de préserver les unités saisies.
    """
    required = {
        "code_article", "designation", "emplacement", "lot", "stock_theorique",
        "stock_physique", "ecart_quantite", "cout_unitaire", "ecart_valorise",
        "statut", "anomalies", "ligne_source",
    }
    columns = set(getattr(report, "columns", []))
    missing = sorted(required - columns)
    if missing:
        raise ValueError("Rapport incomplet; colonnes manquantes : " + ", ".join(missing))
    records = report[list(required)].to_dict(orient="records")
    if not records:
        raise ValueError("Le rapport ne contient aucune ligne à enregistrer.")
    if len(records) > 100_000:
        raise ValueError("Le rapport dépasse la limite de 100 000 lignes par campagne.")
    now = _now()
    fields = ["code_article", "designation", "emplacement", "lot", "stock_theorique", "stock_physique",
              "ecart_quantite", "cout_unitaire", "ecart_valorise", "statut", "anomalies", "ligne_source"]
    with connect() as con:
        campaign = con.execute("SELECT status,assigned_to,created_by FROM inventory_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if campaign is None:
            raise ValueError("Campagne introuvable.")
        allowed_worker = campaign["assigned_to"] or campaign["created_by"]
        if allowed_worker.casefold() != actor.casefold():
            raise PermissionError("Seul le magasinier affecté (ou le créateur si aucun magasinier n'est affecté) peut modifier ce comptage.")
        if campaign["status"] not in {"Préparation", "Comptage en cours", "Comptage terminé", "À corriger"}:
            raise ValueError("Le détail ne peut plus être modifié après soumission au contrôle.")
        con.execute("DELETE FROM inventory_campaign_lines WHERE campaign_id=?", (int(campaign_id),))
        values = []
        for line_no, record in enumerate(records, start=1):
            vals = []
            for field in fields:
                value = record.get(field, "")
                if value is None:
                    value = ""
                # pandas/numpy scalars become ordinary strings for stable SQLite storage.
                vals.append(str(value).strip())
            values.append((int(campaign_id), line_no, *vals, actor, now, str(source_file)[:500]))
        con.executemany("""INSERT INTO inventory_campaign_lines(
            campaign_id,line_no,code_article,designation,emplacement,lot,stock_theorique,stock_physique,
            ecart_quantite,cout_unitaire,ecart_valorise,statut,anomalies,ligne_source,saved_by,saved_at,source_file
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", values)
        con.execute("UPDATE inventory_campaigns SET updated_at=? WHERE id=?", (now, int(campaign_id)))
    log_event(actor, "ENREGISTREMENT_LIGNES_INVENTAIRE", f"campagne={campaign_id}; lignes={len(records)}; fichier={str(source_file)[:300]}")
    return len(records)


def list_campaign_lines(campaign_id: int) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("""SELECT line_no,code_article,designation,emplacement,lot,stock_theorique,stock_physique,
            ecart_quantite,cout_unitaire,ecart_valorise,statut,anomalies,ligne_source,saved_by,saved_at,source_file
            FROM inventory_campaign_lines WHERE campaign_id=? ORDER BY line_no""", (int(campaign_id),)).fetchall()
    return [dict(row) for row in rows]


def submit_campaign_for_review(campaign_id: int, actor: str) -> None:
    """Soumet une campagne seulement si son détail de comptage a été enregistré."""
    now = _now()
    with connect() as con:
        row = con.execute("SELECT campaign_name,status,assigned_to,created_by FROM inventory_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if row is None:
            raise ValueError("Campagne introuvable.")
        allowed_worker = row["assigned_to"] or row["created_by"]
        if allowed_worker.casefold() != actor.casefold():
            raise PermissionError("Seul le magasinier affecté (ou le créateur si aucun magasinier n'est affecté) peut soumettre ce comptage.")
        if row["status"] not in {"Préparation", "Comptage en cours", "Comptage terminé", "À corriger"}:
            raise ValueError("Cette campagne n'est pas dans un état permettant la soumission.")
        count = con.execute("SELECT COUNT(*) FROM inventory_campaign_lines WHERE campaign_id=?", (int(campaign_id),)).fetchone()[0]
        if count == 0:
            raise ValueError("Impossible de soumettre : enregistre d'abord les lignes de comptage.")
        con.execute("UPDATE inventory_campaigns SET status='Soumis pour contrôle', reviewed_by='', reviewed_at='', review_notes='', updated_at=? WHERE id=?", (now, int(campaign_id)))
    log_event(actor, "SOUMISSION_CAMPAGNE_INVENTAIRE", f"id={campaign_id}; campagne={row['campaign_name']}; lignes={count}")


def update_campaign_status(campaign_id: int, status: str, actor: str, notes: str = "", actor_role: str | None = None) -> None:
    allowed = {"Préparation", "Comptage en cours", "Comptage terminé", "Soumis pour contrôle", "Écarts à justifier", "À corriger", "Approuvé", "Clôturé", "Rejeté"}
    decisions = {"Écarts à justifier", "À corriger", "Approuvé", "Clôturé", "Rejeté"}
    if status not in allowed:
        raise ValueError("Statut de campagne non reconnu.")
    if status in decisions and actor_role not in {"Administrateur", "Responsable"}:
        raise PermissionError("Seul un Administrateur ou un Responsable peut enregistrer cette décision.")
    with connect() as con:
        row = con.execute("SELECT campaign_name, created_by, assigned_to, status FROM inventory_campaigns WHERE id=?", (int(campaign_id),)).fetchone()
        if row is None:
            raise ValueError("Campagne introuvable.")
        current = row["status"]
        line_count = con.execute("SELECT COUNT(*) FROM inventory_campaign_lines WHERE campaign_id=?", (int(campaign_id),)).fetchone()[0]
        if status in {"Approuvé", "Rejeté", "À corriger", "Écarts à justifier"} and current != "Soumis pour contrôle" and not (status == "À corriger" and current == "Écarts à justifier"):
            raise ValueError("La campagne doit être soumise pour contrôle avant qu'une décision soit enregistrée.")
        if status == "Soumis pour contrôle" and current != "Soumis pour contrôle" and line_count == 0:
            raise ValueError("Impossible de soumettre : enregistre d'abord les lignes de comptage.")
        if status == "Approuvé" and line_count == 0:
            raise ValueError("Impossible d'approuver une campagne sans lignes de comptage enregistrées.")
        transitions = {
            "Préparation": {"Préparation", "Comptage en cours", "Comptage terminé"},
            "Comptage en cours": {"Comptage en cours", "Comptage terminé"},
            "Comptage terminé": {"Comptage terminé", "Comptage en cours"},
            "À corriger": {"À corriger", "Comptage en cours", "Comptage terminé"},
            "Soumis pour contrôle": {"Soumis pour contrôle", "Écarts à justifier", "À corriger", "Approuvé", "Rejeté"},
            "Écarts à justifier": {"Écarts à justifier", "À corriger"},
            "Approuvé": {"Approuvé", "Clôturé"},
            "Rejeté": {"Rejeté"},
            "Clôturé": {"Clôturé"},
        }
        if status not in transitions.get(current, set()):
            raise ValueError(f"Transition de statut non autorisée : {current} → {status}.")
        if status in {"Approuvé", "Clôturé"} and actor.casefold() in {row["created_by"].casefold(), row["assigned_to"].casefold()}:
            raise PermissionError("Séparation des tâches : le créateur ou le magasinier affecté ne peut pas approuver ou clôturer cette campagne.")
        if status == "Clôturé" and current != "Approuvé":
            raise ValueError("Seule une campagne approuvée peut être clôturée.")
        now = _now()
        if status in decisions:
            con.execute("UPDATE inventory_campaigns SET status=?, notes=?, review_notes=?, reviewed_by=?, reviewed_at=?, updated_at=? WHERE id=?",
                        (status, notes[:4000], notes[:4000], actor, now, now, int(campaign_id)))
        elif notes:
            con.execute("UPDATE inventory_campaigns SET status=?, notes=?, updated_at=? WHERE id=?", (status, notes[:4000], now, int(campaign_id)))
        else:
            con.execute("UPDATE inventory_campaigns SET status=?, updated_at=? WHERE id=?", (status, now, int(campaign_id)))
    log_event(actor, "CHANGEMENT_STATUT_CAMPAGNE", f"id={campaign_id}; campagne={row['campaign_name']}; statut={status}; note={notes[:300]}")


def list_campaigns() -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT id,campaign_name,campaign_date,warehouse,counter,assigned_to,second_counter,status,notes,created_by,updated_at,reviewed_by,reviewed_at,review_notes FROM inventory_campaigns ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def list_audit(limit: int = 500) -> list[dict[str, Any]]:
    with connect() as con:
        rows = con.execute("SELECT event_at,username,action,details FROM audit_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rows]


# Sauvegarde SQLite cohérente : l'API backup tient compte des transactions/WAL.
def backup_database(destination: str | Path) -> Path:
    destination = Path(destination).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_path = db_path()
    if not source_path.exists():
        raise FileNotFoundError("La base de données n'existe pas encore.")
    target = sqlite3.connect(destination)
    try:
        with sqlite3.connect(source_path, timeout=15) as source:
            source.backup(target)
        result = target.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise sqlite3.DatabaseError(f"Vérification de sauvegarde échouée : {result}")
    finally:
        target.close()
    return destination


def backup_database_bytes() -> bytes:
    """Retourne un instantané SQLite cohérent, utilisable pour export manuel."""
    source_path = db_path()
    if not source_path.exists():
        raise FileNotFoundError("La base de données n'existe pas encore.")
    out = io.BytesIO()
    memory = sqlite3.connect(":memory:")
    try:
        with sqlite3.connect(source_path, timeout=15) as source:
            source.backup(memory)
        memory.commit()
        # serialize produit un fichier SQLite complet, incluant les transactions validées.
        return memory.serialize()
    finally:
        memory.close()


def automatic_daily_backup(retention_days: int = 14) -> Path | None:
    """Sauvegarde locale quotidienne; ne remplace pas une copie hors serveur."""
    source = db_path()
    if not source.exists() or source.stat().st_size == 0:
        return None
    # Évite de conserver comme sauvegarde quotidienne une base initiale encore vide.
    try:
        with sqlite3.connect(source, timeout=10) as con:
            has_user_table = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
            if not has_user_table or con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
                return None
    except sqlite3.Error:
        return None
    folder = source.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    today = datetime.now().astimezone().strftime("%Y-%m-%d")
    destination = folder / f"syscohada-{today}.sqlite3"
    if not destination.exists():
        backup_database(destination)
    cutoff = datetime.now().astimezone().timestamp() - max(1, retention_days) * 86400
    for candidate in folder.glob("syscohada-*.sqlite3"):
        try:
            if candidate.stat().st_mtime < cutoff:
                candidate.unlink()
        except OSError:
            pass
    return destination
