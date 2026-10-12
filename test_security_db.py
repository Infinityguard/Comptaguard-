import pytest
import security_db as db


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "test.sqlite3"))
    db.init_db()


def test_create_and_authenticate_user(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    assert not db.has_users()
    db.create_user("admin", "Administrateur Test", "Administrateur", "motdepasse-solide-123")
    assert db.has_users()
    user = db.authenticate("ADMIN", "motdepasse-solide-123")
    assert user["username"] == "admin"
    assert user["role"] == "Administrateur"
    assert db.authenticate("admin", "mauvais-mot-de-passe") is None


def test_password_policy_and_duplicate_user(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        db.create_user("ab", "Nom Test", "Magasinier", "court")
    db.create_user("magasinier", "Magasinier Test", "Magasinier", "un-mot-de-passe-long")
    with pytest.raises(Exception):
        db.create_user("MAGASINIER", "Autre Nom", "Magasinier", "un-mot-de-passe-long")


def test_campaign_persists_and_audit_is_written(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_campaign({
        "campaign_name": "Inventaire test", "campaign_date": "2026-10-10",
        "warehouse": "Magasin A", "counter": "Magasinier Test",
        "status": "Comptage en cours", "notes": "Écart à examiner"
    }, "magasinier")
    campaign_id = db.list_campaigns()[0]["id"]
    db.save_campaign_lines(campaign_id, sample_inventory_report(), "magasinier", "test.csv")
    db.submit_campaign_for_review(campaign_id, "magasinier")
    rows = db.list_campaigns()
    assert len(rows) == 1
    assert rows[0]["campaign_name"] == "Inventaire test"
    assert rows[0]["status"] == "Soumis pour contrôle"
    assert any(row["action"] == "CREATION_CAMPAGNE_INVENTAIRE" for row in db.list_audit())
    assert any(row["action"] == "SOUMISSION_CAMPAGNE_INVENTAIRE" for row in db.list_audit())


def test_campaign_status_update_logged(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("magasinier")
    db.update_campaign_status(campaign_id, "Approuvé", "responsable", "Contrôle effectué", "Responsable")
    assert db.list_campaigns()[0]["status"] == "Approuvé"
    assert any(row["action"] == "CHANGEMENT_STATUT_CAMPAGNE" for row in db.list_audit())


def test_cannot_disable_last_active_admin(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_user("admin1", "Admin Un", "Administrateur", "mot-de-passe-admin-123")
    user_id = db.list_users()[0]["id"]
    with pytest.raises(ValueError, match="dernier administrateur"):
        db.set_user_active(user_id, False, "admin1")
    assert db.get_active_user(user_id) is not None


def test_approval_requires_separation_of_duties(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("responsable1")
    with pytest.raises(PermissionError, match="ne peut pas approuver"):
        db.update_campaign_status(campaign_id, "Approuvé", "responsable1", "", "Responsable")
    with pytest.raises(PermissionError, match="Seul un Administrateur"):
        db.update_campaign_status(campaign_id, "Approuvé", "comptable1", "", "Comptable")
    db.update_campaign_status(campaign_id, "Approuvé", "responsable2", "Vérifié", "Responsable")
    approved = db.list_campaigns()[0]
    assert approved["status"] == "Approuvé"
    assert approved["reviewed_by"] == "responsable2"
    assert approved["review_notes"] == "Vérifié"


def test_failed_login_is_audited_without_password(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_user("admin", "Administrateur Test", "Administrateur", "mot-de-passe-solide-123")
    assert db.authenticate("admin", "secret-ne-pas-journaliser") is None
    events = db.list_audit()
    failed = next(event for event in events if event["action"] == "ECHEC_CONNEXION")
    assert "secret-ne-pas-journaliser" not in failed["details"]


def test_database_backup_is_valid(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_user("admin", "Administrateur Test", "Administrateur", "mot-de-passe-solide-123")
    data = db.backup_database_bytes()
    assert data.startswith(b"SQLite format 3\x00")
    destination = db.backup_database(tmp_path / "manual-backup.sqlite3")
    import sqlite3
    with sqlite3.connect(destination) as con:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_campaign_cannot_be_approved_before_submission(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_campaign({
        "campaign_name": "Inventaire brouillon", "campaign_date": "2026-10-10",
        "warehouse": "Magasin A", "counter": "Magasinier", "status": "Préparation"
    }, "magasinier")
    campaign_id = db.list_campaigns()[0]["id"]
    with pytest.raises(ValueError, match="soumise pour contrôle"):
        db.update_campaign_status(campaign_id, "Approuvé", "responsable", "", "Responsable")


def test_audit_log_rejects_update_and_delete(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.log_event("admin", "TEST", "événement")
    import sqlite3
    with sqlite3.connect(db.db_path()) as con:
        with pytest.raises(sqlite3.IntegrityError, match="journal d’audit"):
            con.execute("UPDATE audit_log SET details='modifié' WHERE action='TEST'")
        with pytest.raises(sqlite3.IntegrityError, match="journal d’audit"):
            con.execute("DELETE FROM audit_log WHERE action='TEST'")


def sample_inventory_report():
    import pandas as pd
    return pd.DataFrame([{
        "code_article": "ART-01", "designation": "Article test", "emplacement": "MAG-01", "lot": "",
        "stock_theorique": "10", "stock_physique": "8", "ecart_quantite": "-2", "cout_unitaire": "100",
        "ecart_valorise": "-200", "statut": "À vérifier", "anomalies": "Écart entre stock physique et théorique", "ligne_source": 2,
    }])


def make_submitted_campaign(actor="magasinier"):
    db.create_campaign({
        "campaign_name": "Inventaire avec détail", "campaign_date": "2026-10-10",
        "warehouse": "Magasin A", "counter": "Magasinier", "status": "Comptage en cours"
    }, actor)
    campaign_id = db.list_campaigns()[0]["id"]
    db.save_campaign_lines(campaign_id, sample_inventory_report(), actor, "inventaire.csv")
    db.submit_campaign_for_review(campaign_id, actor)
    return campaign_id


def test_inventory_lines_persist_and_link_to_campaign(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_campaign({
        "campaign_name": "Inventaire durable", "campaign_date": "2026-10-10",
        "warehouse": "Magasin A", "counter": "Magasinier", "status": "Comptage en cours"
    }, "magasinier")
    campaign_id = db.list_campaigns()[0]["id"]
    count = db.save_campaign_lines(campaign_id, sample_inventory_report(), "magasinier", "inventaire.csv")
    assert count == 1
    rows = db.list_campaign_lines(campaign_id)
    assert rows[0]["code_article"] == "ART-01"
    assert rows[0]["ecart_quantite"] == "-2"
    assert rows[0]["source_file"] == "inventaire.csv"
    assert rows[0]["saved_by"] == "magasinier"


def test_cannot_submit_campaign_without_count_lines(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_campaign({
        "campaign_name": "Vide", "campaign_date": "2026-10-10",
        "warehouse": "Magasin A", "counter": "Magasinier", "status": "Comptage en cours"
    }, "magasinier")
    campaign_id = db.list_campaigns()[0]["id"]
    with pytest.raises(ValueError, match="lignes de comptage"):
        db.submit_campaign_for_review(campaign_id, "magasinier")
    with pytest.raises(ValueError, match="lignes de comptage"):
        db.update_campaign_status(campaign_id, "Soumis pour contrôle", "magasinier")


def test_submitted_campaign_lines_are_locked(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign()
    with pytest.raises(ValueError, match="ne peut plus être modifié"):
        db.save_campaign_lines(campaign_id, sample_inventory_report(), "magasinier", "replacement.csv")


def test_campaign_cannot_be_created_directly_as_submitted(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="ne peut pas être créée directement"):
        db.create_campaign({
            "campaign_name": "Saut de contrôle", "campaign_date": "2026-10-10",
            "warehouse": "Magasin A", "counter": "Magasinier", "status": "Soumis pour contrôle"
        }, "magasinier")


def test_approval_workflow_requires_persisted_lines_and_independent_reviewer(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("magasinier1")
    with pytest.raises(PermissionError, match="ne peut pas approuver"):
        db.update_campaign_status(campaign_id, "Approuvé", "magasinier1", "", "Responsable")
    with pytest.raises(PermissionError, match="Seul un Administrateur"):
        db.update_campaign_status(campaign_id, "Approuvé", "comptable1", "", "Comptable")
    db.update_campaign_status(campaign_id, "Approuvé", "responsable1", "Vérifié", "Responsable")
    assert db.list_campaigns()[0]["status"] == "Approuvé"


def test_submitted_campaign_cannot_be_reopened_by_generic_status_update(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("magasinier")
    with pytest.raises(ValueError, match="Transition de statut non autorisée"):
        db.update_campaign_status(campaign_id, "Comptage en cours", "magasinier")


def test_campaign_returned_for_correction_can_be_edited_and_resubmitted(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("magasinier")
    db.update_campaign_status(campaign_id, "Écarts à justifier", "responsable", "Joindre justificatif", "Responsable")
    db.update_campaign_status(campaign_id, "À corriger", "responsable", "Corriger le comptage", "Responsable")
    assert db.list_campaigns()[0]["status"] == "À corriger"
    db.save_campaign_lines(campaign_id, sample_inventory_report(), "magasinier", "corrige.csv")
    db.submit_campaign_for_review(campaign_id, "magasinier")
    assert db.list_campaigns()[0]["status"] == "Soumis pour contrôle"


def test_campaign_is_assigned_to_specific_active_storekeeper(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_user("mag1", "Magasinier Un", "Magasinier", "mot-de-passe-mag1-123")
    db.create_user("mag2", "Magasinier Deux", "Magasinier", "mot-de-passe-mag2-123")
    db.create_campaign({
        "campaign_name": "Affectation", "campaign_date": "2026-10-10", "warehouse": "MAG-01",
        "counter": "Magasinier Un", "assigned_to": "mag1", "status": "Comptage en cours"
    }, "comptable")
    campaign_id = db.list_campaigns()[0]["id"]
    with pytest.raises(PermissionError, match="magasinier affecté"):
        db.save_campaign_lines(campaign_id, sample_inventory_report(), "mag2", "stock.csv")
    db.save_campaign_lines(campaign_id, sample_inventory_report(), "mag1", "stock.csv")
    with pytest.raises(PermissionError, match="magasinier affecté"):
        db.submit_campaign_for_review(campaign_id, "mag2")
    db.submit_campaign_for_review(campaign_id, "mag1")
    assert db.list_campaigns()[0]["assigned_to"] == "mag1"


def test_cannot_assign_campaign_to_inactive_or_non_storekeeper_user(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    db.create_user("compta", "Comptable Test", "Comptable", "mot-de-passe-compta-123")
    with pytest.raises(ValueError, match="Magasinier actif"):
        db.create_campaign({
            "campaign_name": "Mauvaise affectation", "campaign_date": "2026-10-10", "warehouse": "MAG-01",
            "counter": "Comptable Test", "assigned_to": "compta", "status": "Comptage en cours"
        }, "admin")


def test_resubmission_clears_stale_review_metadata(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    campaign_id = make_submitted_campaign("magasinier")
    db.update_campaign_status(campaign_id, "À corriger", "responsable", "Nouvelle vérification requise", "Responsable")
    assert db.list_campaigns()[0]["reviewed_by"] == "responsable"
    db.save_campaign_lines(campaign_id, sample_inventory_report(), "magasinier", "corrige.csv")
    db.submit_campaign_for_review(campaign_id, "magasinier")
    campaign = db.list_campaigns()[0]
    assert campaign["status"] == "Soumis pour contrôle"
    assert campaign["reviewed_by"] == ""
    assert campaign["review_notes"] == ""
