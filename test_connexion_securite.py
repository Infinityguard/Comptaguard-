"""Blocage temporaire après échecs répétés de connexion."""
import pytest

import security_db as db


@pytest.fixture
def base(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "t.sqlite3"))
    db.init_db()
    db.create_user("admin", "Admin", "Administrateur", "motdepasse-solide-123")
    db.create_user("autre", "Autre", "Comptable", "motdepasse-solide-456")
    return db


def _echecs(n, user="admin"):
    for _ in range(n):
        assert db.authenticate(user, "mauvais") is None


def test_sous_le_seuil_pas_de_blocage(base):
    _echecs(db.MAX_FAILED_LOGINS - 1)
    assert db.login_lock_remaining("admin") == 0
    assert db.authenticate("admin", "motdepasse-solide-123")["username"] == "admin"


def test_blocage_apres_echecs_repetes_meme_avec_bon_mot_de_passe(base):
    _echecs(db.MAX_FAILED_LOGINS)
    assert db.login_lock_remaining("admin") > 0
    assert db.authenticate("admin", "motdepasse-solide-123") is None


def test_blocage_insensible_a_la_casse_et_limite_a_l_identifiant(base):
    _echecs(db.MAX_FAILED_LOGINS)
    assert db.login_lock_remaining("ADMIN") > 0
    assert db.login_lock_remaining("autre") == 0
    assert db.authenticate("autre", "motdepasse-solide-456")["username"] == "autre"


def test_connexion_reussie_remet_le_compteur_a_zero(base):
    _echecs(db.MAX_FAILED_LOGINS - 1)
    db.log_event("admin", "CONNEXION", "Connexion réussie")
    _echecs(db.MAX_FAILED_LOGINS - 1)
    assert db.login_lock_remaining("admin") == 0


def test_le_blocage_expire(base, monkeypatch):
    _echecs(db.MAX_FAILED_LOGINS)
    monkeypatch.setattr(db, "LOCKOUT_MINUTES", 0)
    assert db.login_lock_remaining("admin") == 0


def test_les_tentatives_bloquees_ne_prolongent_pas_le_delai(base):
    _echecs(db.MAX_FAILED_LOGINS)
    avant = db.login_lock_remaining("admin")
    for _ in range(3):
        db.authenticate("admin", "mauvais")
    assert db.login_lock_remaining("admin") <= avant
