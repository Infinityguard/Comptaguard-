"""Exécute chaque page de l'interface avec les vrais modules métier et une base temporaire.

Streamlit est remplacé par un double léger : ces tests détectent les erreurs de logique, de noms
ou de signatures, mais ne remplacent pas un essai visuel dans un navigateur.
"""
import datetime
import importlib
import io
import sys
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd

import factures
import security_db
import stocks

ROOT = Path(__file__).parent
SAMPLE = (ROOT / "exemple_ecritures.xlsx").read_bytes()


class Rerun(BaseException):          # comme st.rerun() : non capturé par « except Exception »
    pass


class Upload(io.BytesIO):
    def __init__(self, name, data):
        super().__init__(data)
        self.name, self.size = name, len(data)


def csv_upload(name, df):
    return Upload(name, df.to_csv(index=False).encode())


class Session(dict):
    __getattr__ = lambda self, k: self[k] if k in self else (_ for _ in ()).throw(AttributeError(k))

    def __setattr__(self, k, v):
        self[k] = v


def fake_streamlit(uploads=None, clicks=(), submits=(), radio=None, numbers=None):
    st = MagicMock(name="streamlit")
    st.session_state = Session()
    st.errors, st.metrics, st.pages, st.warnings, st.infos = [], [], [], [], []

    class Col:                                   # une colonne délègue à st
        def __getattr__(self, name):
            return getattr(st, name)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    st.columns.side_effect = lambda spec, **k: [Col() for _ in range(spec if isinstance(spec, int) else len(spec))]
    st.tabs.side_effect = lambda labels: [Col() for _ in labels]
    st.file_uploader.side_effect = lambda label, type=None, key=None, **k: (uploads or {}).get(key)
    st.button.side_effect = lambda label, key=None, **k: key in clicks or label in clicks
    st.form_submit_button.side_effect = lambda label, **k: label in submits
    st.checkbox.return_value = True
    st.radio.side_effect = lambda label, options, **k: radio or options[0]
    st.selectbox.side_effect = lambda label, options, **k: options[0] if len(options) else None
    st.multiselect.side_effect = lambda label, options, default=None, **k: default or options
    st.number_input.side_effect = lambda label, min_value=None, max_value=None, value=None, step=None, key=None, **k: (
        (numbers or {}).get(key, value if value is not None else (min_value if min_value is not None else 0.0)))
    st.text_input.side_effect = lambda label, value="", **k: value or "Test"
    st.text_area.return_value = "observation"
    st.date_input.return_value = datetime.date.today()
    st.stop.side_effect = SystemExit
    st.rerun.side_effect = Rerun()
    st.error.side_effect = lambda m, *a, **k: st.errors.append(str(m))
    st.warning.side_effect = lambda m, *a, **k: st.warnings.append(str(m))
    st.info.side_effect = lambda m, *a, **k: st.infos.append(str(m))
    st.metric.side_effect = lambda label, value, **k: st.metrics.append((label, value))
    st.Page.side_effect = lambda func, title=None, icon=None, url_path=None: (
        st.pages.append(title) or types.SimpleNamespace(title=title, url_path=url_path))
    st.dataframe.side_effect = lambda data, **k: data.to_html() if hasattr(data, "to_html") else None  # rend aussi les Styler
    return st


def load_app(tmp_path, monkeypatch, role="Administrateur", **options):
    """Charge app.py avec le faux Streamlit et une base vierge ; retourne (module, st)."""
    monkeypatch.setenv("SYSCOHADA_DB_PATH", str(tmp_path / "t.sqlite3"))
    security_db.init_db()
    if not security_db.has_users():
        security_db.create_user("admin", "Admin Test", "Administrateur", "motdepasse-solide-123", "t")
        security_db.create_user("mag", "Magasin Test", "Magasinier", "motdepasse-solide-123", "t")
        security_db.create_user("paie", "Paie Test", "Gestionnaire de paie", "motdepasse-solide-123", "t")
        security_db.create_user("comp", "Comptable Test", "Comptable", "motdepasse-solide-123", "t")
        security_db.create_user("audit", "Auditeur Test", "Auditeur", "motdepasse-solide-123", "t")
    st = fake_streamlit(**options)
    user_id = {"Administrateur": 1, "Magasinier": 2, "Gestionnaire de paie": 3,
               "Comptable": 4, "Auditeur": 5}[role]
    st.session_state.auth_user = {"id": user_id, "username": "x"}
    for name in ("app", "ui", "streamlit"):
        sys.modules.pop(name, None)
    sys.modules["streamlit"] = st
    st.column_config = MagicMock()
    return st


def run_pages(tmp_path, monkeypatch, names, role="Administrateur", user="admin", **options):
    saved = {n: sys.modules.get(n) for n in ("streamlit", "ui", "app")}
    try:
        st = load_app(tmp_path, monkeypatch, role, **options)
        app = importlib.import_module("app")
        for name in names:
            app.current_user = {"id": 1, "username": user, "full_name": "T", "role": role}
            try:
                getattr(app, name)()
            except Rerun:
                pass
        return app, st
    finally:
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod


ALL_PAGES = ["page_ecritures", "page_rapprochement", "page_factures", "page_stocks", "page_campagnes",
             "page_paie", "page_admin"]


def test_toutes_les_pages_sans_fichier(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ALL_PAGES)
    assert st.errors == []


def test_page_magasinier_sans_fichier(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ["page_magasinier"], role="Magasinier", user="mag")
    assert st.errors == []


def test_ecritures_fichier_d_exemple(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ["page_ecritures"], uploads={"ecr_upload": Upload("e.xlsx", SAMPLE)})
    assert st.errors == []


def test_ecritures_grand_livre_et_balance(tmp_path, monkeypatch):
    balance = pd.DataFrame({"compte": ["411", "512", "701"], "debit": [100.0, 50.0, 0.0], "credit": [0.0, 0.0, 150.0]})
    _, st = run_pages(tmp_path, monkeypatch, ["page_ecritures"], radio="Balance générale",
                      uploads={"ecr_upload": csv_upload("b.csv", balance)})
    assert st.errors == []
    _, st = run_pages(tmp_path, monkeypatch, ["page_ecritures"], radio="Grand livre",
                      uploads={"ecr_upload": Upload("e.xlsx", SAMPLE)})
    assert st.errors == []


def test_rapprochement_donnees_propres(tmp_path, monkeypatch):
    ledger = pd.DataFrame({"date": ["2026-01-05"] * 2, "piece": ["P1", "P1"], "libelle": ["a", "b"],
                           "compte": ["411", "701"], "debit": [100.0, 0], "credit": [0, 100.0]})
    balance = pd.DataFrame({"compte": ["411", "701"], "debit": [100.0, 0.0], "credit": [0.0, 100.0]})
    _, st = run_pages(tmp_path, monkeypatch, ["page_rapprochement"],
                      uploads={"rec_gl": csv_upload("g.csv", ledger), "rec_bal": csv_upload("b.csv", balance)},
                      clicks=["Lancer le rapprochement"])
    assert st.errors == []
    assert ("Concordants", "2") in st.metrics and ("À vérifier", "0") in st.metrics


def test_stocks_et_factures_avec_les_modeles(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ["page_stocks"],
                      uploads={"stock_upload": Upload("s.xlsx", stocks.stock_template_bytes())})
    assert st.errors == []
    _, st = run_pages(tmp_path, monkeypatch, ["page_factures"],
                      uploads={"invoice_upload": Upload("f.xlsx", factures.invoice_template_bytes())})
    assert st.errors == []


def test_paie_les_totaux_ne_sont_pas_a_zero(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ["page_paie", "page_paie"], role="Gestionnaire de paie", user="paie",
                      submits=["Calculer et ajouter au registre"], numbers={"pay_base": 150000.0})
    assert st.errors == []
    totals = [v for label, v in st.metrics if label == "Total net à payer (FCFA)"]
    assert totals and totals[-1] != "0"


def test_workflow_magasinier_et_validation(tmp_path, monkeypatch):
    options = dict(uploads={"magasinier_stock_upload": Upload("s.xlsx", stocks.stock_template_bytes())})
    _, st = run_pages(tmp_path, monkeypatch, ["page_magasinier"], role="Magasinier", user="mag",
                      submits=["Créer la campagne"], **options)
    assert st.errors == []
    _, st = run_pages(tmp_path, monkeypatch, ["page_magasinier"], role="Magasinier", user="mag",
                      clicks=["save_inventory_lines", "submit_inventory_campaign"], **options)
    assert st.errors == []
    assert security_db.list_campaign_lines(1)
    _, st = run_pages(tmp_path, monkeypatch, ["page_campagnes"], clicks=["save_campaign_review"])
    assert st.errors == []


def test_navigation_selon_le_role(tmp_path, monkeypatch):
    app, st = run_pages(tmp_path, monkeypatch, [])
    def titles(role):
        st.pages.clear()
        app.build_navigation(role)
        return set(st.pages)
    assert titles("Magasinier") == {"Comptage d'inventaire", "Gestion des stocks"}
    paie = titles("Gestionnaire de paie")
    assert paie == {"Paie"}                       # pas d'accès au rapprochement ni aux écritures
    assert "Administration" in titles("Administrateur") and "Administration" not in titles("Comptable")
    assert "Rapprochement" in titles("Auditeur")
    assert "Factures de transport" in titles("Administrateur")
    assert "Factures de transport" in titles("Comptable")
    assert "Factures de transport" in titles("Auditeur")
    assert "Factures de transport" not in titles("Magasinier")
    assert "Factures de transport" not in titles("Responsable")


def test_session_expiree_apres_inactivite(tmp_path, monkeypatch):
    saved = {n: sys.modules.get(n) for n in ("streamlit", "ui", "app")}
    try:
        st = load_app(tmp_path, monkeypatch)
        st.session_state.auth_user = {"id": 1, "username": "admin"}
        st.session_state["last_activity"] = time.time() - 3600
        try:
            importlib.import_module("app")
            raise AssertionError("la session aurait dû expirer")
        except Rerun:
            pass
        assert st.session_state.auth_user is None
        assert "expiré" in st.session_state["session_notice"]
    finally:
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod


def test_blocage_de_connexion_affiche_un_message(tmp_path, monkeypatch):
    saved = {n: sys.modules.get(n) for n in ("streamlit", "ui", "app")}
    try:
        st = load_app(tmp_path, monkeypatch, submits=["Se connecter"])
        st.text_input.side_effect = lambda label, value="", **k: "admin" if "Identifiant" in label else "mauvais"
        st.session_state.auth_user = None
        for _ in range(security_db.MAX_FAILED_LOGINS):
            sys.modules.pop("app", None)
            try:
                importlib.import_module("app")
            except SystemExit:
                pass
        assert "temporairement bloqué" in st.errors[-1]
    finally:
        for n, mod in saved.items():
            sys.modules.pop(n, None)
            if mod is not None:
                sys.modules[n] = mod


def test_factures_transport_interdites_au_magasinier(tmp_path, monkeypatch):
    _, st = run_pages(tmp_path, monkeypatch, ["page_reglement_transporteurs"], role="Magasinier", user="mag")
    assert any("Accès interdit" in msg for msg in st.warnings)
    assert not any("Factures de transport" in msg for msg in st.metrics)


def test_factures_transport_lecture_seule_comptable_auditeur(tmp_path, monkeypatch):
    for role, user in (("Comptable", "comp"), ("Auditeur", "audit")):
        _, st = run_pages(tmp_path, monkeypatch, ["page_reglement_transporteurs"], role=role, user=user)
        assert st.errors == []
        tab_labels = st.tabs.call_args.args[0]
        assert tab_labels == ["Registre des factures (lecture seule)"]
