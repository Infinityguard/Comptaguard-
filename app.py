from __future__ import annotations

import math
import os
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pandas as pd
import streamlit as st

from ui import (APP_NAME, APP_TAGLINE, APP_VERSION, brand_header, excel_bytes, inject_css,
                kpis, limits, md_escape, money, page_header, read_table, show_table)

st.set_page_config(page_title=f"{APP_NAME} — {APP_TAGLINE}",
                   page_icon=":material/verified_user:", layout="wide")
inject_css()

from controles import (analyze, analyze_trial_balance, assess_import_quality, build_account_regex,
                       make_account_reference_template, make_excel_report, prepare_dataframe,
                       rate_risks, read_account_reference, reconcile_ledger_balance, summarize)
from security_db import (ROLES, authenticate, backup_database_bytes, create_campaign, create_user,
                         get_active_user, has_users, init_db, list_audit, list_campaign_lines,
                         list_campaigns, list_users, log_event, login_lock_remaining, save_campaign_lines,
                         set_user_active, submit_campaign_for_review, update_campaign_status)
from stocks import analyze_physical_stock, stock_template_bytes
from stock_management import init_stock_db, create_item, list_items, stock_summary, register_movement, list_movements, transfer_stock
from tolerance import piece_balance_diagnostics
from emission_factures import create_ticket, init_invoicing_db, list_tickets
import transport_factures as tf
import referentiels_aic as ref

init_db()
init_stock_db()
init_invoicing_db()
tf.init_transport_db()

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
ACCEPTED = ["xlsx", "xlsm", "csv"]
EXCEL_TYPES = ["xlsx", "xlsm"]
RISKS = ["Élevé", "Modéré", "Faible"]
SEVERITIES = ["Haute", "Moyenne", "Basse"]
SESSION_TIMEOUT_MINUTES = int(os.environ.get("SYSCOHADA_SESSION_TIMEOUT_MINUTES", "30"))

current_user: dict = {}


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------
def stamp() -> str:
    return f"{datetime.now():%Y%m%d_%H%M%S}"


def csv_bytes(df: pd.DataFrame) -> bytes:
    return df.to_csv(index=False).encode("utf-8-sig")


def log_once(key: str, signature, action: str, details: str) -> None:
    """Évite de journaliser la même analyse à chaque rafraîchissement de l'écran."""
    if st.session_state.get(key) != signature:
        log_event(current_user["username"], action, details)
        st.session_state[key] = signature


def my_campaigns() -> list[dict]:
    me = current_user["username"].casefold()
    result = []
    for c in list_campaigns():
        assigned = (c.get("assigned_to") or "").casefold()
        if assigned == me or (not assigned and (c.get("created_by") or "").casefold() == me):
            result.append(c)
    return result


def campaign_label(c: dict) -> str:
    return f"N° {c['id']} — {c['campaign_name']} ({c['status']})"


# --------------------------------------------------------------------------
# Écrans d'accès
# --------------------------------------------------------------------------
def screen_setup() -> None:
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        brand_header()
        with st.container(border=True):
            st.subheader("Première configuration")
            st.write("Aucun compte n'existe encore. Créez le compte administrateur initial. "
                     "Aucun mot de passe par défaut n'est utilisé.")
            with st.form("initial_admin_setup"):
                name = st.text_input("Nom complet")
                username = st.text_input("Identifiant")
                password = st.text_input("Mot de passe (10 caractères minimum)", type="password")
                confirm = st.text_input("Confirmation du mot de passe", type="password")
                submit = st.form_submit_button("Créer l'administrateur", type="primary")
            if submit:
                if password != confirm:
                    st.error("Les deux mots de passe ne correspondent pas.")
                else:
                    try:
                        create_user(username, name, "Administrateur", password, "configuration_initiale")
                        st.success("Compte administrateur créé. Vous pouvez maintenant vous connecter.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Création impossible : {exc}")


def screen_login() -> None:
    _, middle, _ = st.columns([1, 1.4, 1])
    with middle:
        brand_header()
        with st.container(border=True):
            st.subheader("Connexion")
            notice = st.session_state.pop("session_notice", None)
            if notice:
                st.warning(notice)
            with st.form("login_form"):
                username = st.text_input("Identifiant")
                password = st.text_input("Mot de passe", type="password")
                submit = st.form_submit_button("Se connecter", type="primary")
            if submit:
                locked = login_lock_remaining(username)
                user = None if locked else authenticate(username, password)
                if user:
                    st.session_state.auth_user = user
                    st.session_state["last_activity"] = time.time()
                    log_event(user["username"], "CONNEXION", "Connexion réussie")
                    st.rerun()
                else:
                    locked = locked or login_lock_remaining(username)
                    if locked:
                        st.error("Trop de tentatives échouées. Ce compte est temporairement bloqué : "
                                 f"réessayez dans {math.ceil(locked / 60)} minute(s).")
                    else:
                        st.error("Identifiant ou mot de passe incorrect, ou compte désactivé.")
        st.caption("Chaque utilisateur doit utiliser son propre compte. "
                   "Ne communiquez jamais votre mot de passe.")


# --------------------------------------------------------------------------
# Page : contrôle des écritures
# --------------------------------------------------------------------------
def _import_tab(raw, quality_report) -> None:
    st.markdown("**Qualité de l'importation**")
    if quality_report.empty:
        st.success("Aucun problème structurel évident détecté. Cela ne garantit pas l'exactitude des données.")
    else:
        show_table(quality_report)
    st.markdown("**Aperçu des données importées**")
    show_table(raw.head(100))


def page_ecritures() -> None:
    page_header("Contrôle des écritures",
                "Importez des écritures, un grand livre ou une balance générale pour effectuer "
                "des contrôles de premier niveau.")
    mode = st.radio("Type de document", ["Écritures comptables", "Grand livre", "Balance générale"],
                    horizontal=True)

    with st.expander("Paramètres des contrôles", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            piece_tolerance = st.number_input(
                "Tolérance par pièce (FCFA)", min_value=0.0, value=0.001, step=0.001, format="%.3f",
                help="Seuil de signalement par pièce. Les écarts restent conservés et sont aussi cumulés séparément.")
            window_days = st.number_input("Fenêtre de détection des doublons (jours)",
                                          min_value=0, max_value=365, value=7)
            require_same_label = st.checkbox("Doublons : exiger aussi le même libellé", value=False)
        with c2:
            min_round_amount = st.number_input("Seuil des montants ronds", min_value=0,
                                               value=100000, step=10000)
            round_step = st.number_input("Pas du montant rond", min_value=1, value=10000, step=1000)
        with c3:
            account_classes = st.text_input("Classes de comptes acceptées", value="12345678")
            allow_suffix = st.checkbox("Accepter un suffixe alphanumérique (ex. 411CLIENT)", value=True)
        st.caption("Les contrôles sont des aides à la vérification ; ils ne certifient pas à eux seuls "
                   "la conformité comptable ou légale.")

    with st.container(border=True):
        left, right = st.columns(2)
        with left:
            uploaded = st.file_uploader("Fichier à contrôler (.xlsx, .xlsm, .csv)", type=ACCEPTED,
                                        key="ecr_upload")
        with right:
            reference_upload = st.file_uploader("Référentiel de comptes — facultatif (.xlsx)",
                                                type=EXCEL_TYPES, key="ecr_reference")
            st.download_button("Télécharger le modèle de référentiel",
                               data=make_account_reference_template(),
                               file_name="modele_referentiel_comptes.xlsx", mime=XLSX)
        st.caption("Le modèle est vierge. Utilisez un référentiel vérifié, adapté au périmètre "
                   "et à la version du SYSCOHADA applicable.")

    if uploaded is None:
        st.info("Sélectionnez un fichier Excel ou CSV pour commencer.")
        example = Path(__file__).with_name("exemple_ecritures.xlsx")
        if mode != "Balance générale" and example.exists():
            st.download_button("Télécharger un fichier d'exemple", data=example.read_bytes(),
                               file_name="exemple_ecritures_syscohada.xlsx", mime=XLSX)
        return

    try:
        raw, sheet = read_table(uploaded, "ecr")
        quality_report, quality_summary = assess_import_quality(raw, mode)
        if not quality_report.empty:
            st.warning(f"{quality_summary['nombre_constats']} point(s) à vérifier à l'importation — "
                       "voir l'onglet « Import et données ».")

        valid_accounts = None
        if reference_upload is not None:
            valid_accounts, _ = read_account_reference(reference_upload)
            st.caption(f"Référentiel chargé : {len(valid_accounts)} comptes uniques.")

        if mode == "Balance générale":
            issues, summary = analyze_trial_balance(raw)
            issues = rate_risks(issues)
            kpis([("Lignes de balance", summary["nombre_comptes"]),
                  ("Total débit", money(summary["total_debit"], 2)),
                  ("Total crédit", money(summary["total_credit"], 2)),
                  ("Écart débit − crédit", money(summary["ecart"], 2))])
            if abs(summary["ecart"]) <= 0.01:
                st.success("Les totaux débit et crédit sont équilibrés selon la tolérance définie.")
            else:
                st.error("La balance présente un écart arithmétique à examiner.")

            tab_alerts, tab_data = st.tabs(["Alertes", "Import et données"])
            with tab_alerts:
                if valid_accounts is not None and "compte" in raw.columns:
                    accounts = raw["compte"].map(str).str.strip()
                    missing = raw[~accounts.isin(valid_accounts)]
                    if not missing.empty:
                        st.warning(f"{len(missing)} ligne(s) contiennent un compte absent du référentiel fourni.")
                        show_table(missing)
                if issues.empty:
                    st.success("Aucune anomalie de structure ou de total détectée par les contrôles activés.")
                else:
                    kpis([(f"Risque {r.lower()}", int((issues["niveau_risque"] == r).sum())) for r in RISKS])
                    levels = st.multiselect("Niveaux de risque affichés", RISKS, default=RISKS,
                                            key="balance_risk_filter")
                    show_table(issues[issues["niveau_risque"].isin(levels)])
                st.download_button("Télécharger les alertes (CSV)", data=csv_bytes(issues),
                                   file_name=f"alertes_balance_{stamp()}.csv", mime="text/csv")
            with tab_data:
                _import_tab(raw, quality_report)
            return

        data = prepare_dataframe(raw)
        params = {"fichier": uploaded.name, "feuille": sheet, "type de document": mode,
                  "date du contrôle": f"{datetime.now():%d/%m/%Y %H:%M}",
                  "fenêtre doublons (jours)": int(window_days),
                  "doublons : même libellé exigé": require_same_label,
                  "seuil montant rond": int(min_round_amount), "pas montant rond": int(round_step),
                  "format de compte": build_account_regex(account_classes, allow_suffix),
                  "référentiel fourni": "oui" if valid_accounts is not None else "non",
                  "nombre de comptes dans le référentiel": len(valid_accounts) if valid_accounts is not None else 0,
                  "tolérance individuelle par pièce (FCFA)": piece_tolerance}
        piece_report, tol = piece_balance_diagnostics(data, tolerance=str(piece_tolerance))
        report = rate_risks(analyze(
            data, window_days=int(window_days), min_round_amount=int(min_round_amount),
            round_step=int(round_step), valid_accounts=valid_accounts,
            require_same_label=require_same_label, account_classes=account_classes,
            allow_account_suffix=allow_suffix, piece_tolerance=piece_tolerance))

        kpis([("Lignes analysées", len(data)), ("Alertes", len(report)),
              ("Risque élevé", int((report["niveau_risque"] == "Élevé").sum())),
              ("Risque modéré ou faible", int(report["niveau_risque"].isin(["Modéré", "Faible"]).sum()))])
        st.download_button("Télécharger le rapport Excel", data=make_excel_report(report, data, params),
                           file_name=f"rapport_controle_{stamp()}.xlsx", mime=XLSX, type="primary")

        tab_alerts, tab_pieces, tab_data = st.tabs(["Anomalies", "Équilibre des pièces", "Import et données"])
        with tab_alerts:
            if "journal" not in data.columns:
                st.caption("Aucune colonne journal détectée : le contrôle d'équilibre s'appuie sur la date et le numéro de pièce.")
            if report.empty:
                st.success("Aucune anomalie détectée par les contrôles activés.")
            else:
                st.markdown("**Répartition des alertes**")
                show_table(summarize(report))
                f1, f2 = st.columns(2)
                severities = f1.multiselect("Gravité initiale", SEVERITIES, default=SEVERITIES)
                levels = f2.multiselect("Niveau de risque", RISKS, default=RISKS)
                st.markdown("**Détail des anomalies**")
                show_table(report[report["gravite"].isin(severities) & report["niveau_risque"].isin(levels)])
                st.caption("Le score est un outil de priorisation, pas une probabilité d'erreur ou de fraude "
                           "ni une conclusion d'audit.")
        with tab_pieces:
            kpis([("Pièces contrôlées", tol["nombre_ecarts"]),
                  ("Pièces hors tolérance", tol["pieces_hors_tolerance"]),
                  ("Écart net signé (FCFA)", tol["ecart_net_signe"]),
                  ("Somme des écarts absolus (FCFA)", tol["somme_ecarts_absolus"])])
            st.caption("L'écart net peut masquer des écarts qui se compensent ; la somme des écarts absolus "
                       "mesure l'ensemble des déséquilibres par pièce. Une tolérance est un seuil de "
                       "signalement, jamais une correction automatique.")
            show_table(piece_report)
            st.download_button("Télécharger le suivi des tolérances (CSV)", data=csv_bytes(piece_report),
                               file_name="suivi_tolerances_par_piece.csv", mime="text/csv")
        with tab_data:
            _import_tab(raw, quality_report)
    except Exception as exc:
        st.error(f"Impossible d'analyser le fichier : {exc}")
        if mode == "Balance générale":
            st.info("La balance doit contenir une colonne compte et des colonnes débit/crédit "
                    "ou solde débiteur/solde créditeur.")
        else:
            st.info("Le fichier doit contenir date, pièce, libellé, compte, débit et crédit. "
                    "Les intitulés usuels sont reconnus automatiquement.")


# --------------------------------------------------------------------------
# Page : rapprochement grand livre / balance
# --------------------------------------------------------------------------
def page_rapprochement() -> None:
    page_header("Rapprochement grand livre et balance",
                "Compare les mouvements débit/crédit par compte lorsque les deux documents couvrent "
                "le même périmètre et la même période.")
    with st.container(border=True):
        left, right = st.columns(2)
        with left:
            gl_file = st.file_uploader("Grand livre (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="rec_gl")
        with right:
            bal_file = st.file_uploader("Balance des mouvements (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="rec_bal")
        confirm = st.checkbox("Je confirme que les colonnes débit/crédit de la balance représentent "
                              "les mouvements de la période, et non les seuls soldes.")
    signature = None
    gl_raw = bal_raw = None
    try:
        if gl_file is not None:
            gl_raw, _ = read_table(gl_file, "rec_gl", "Feuille du grand livre")
        if bal_file is not None:
            bal_raw, _ = read_table(bal_file, "rec_bal", "Feuille de la balance")
        if gl_file is not None and bal_file is not None:
            signature = (gl_file.name, gl_file.size, bal_file.name, bal_file.size)
    except Exception as exc:
        st.error(f"Lecture impossible : {exc}")
        return

    ready = gl_raw is not None and bal_raw is not None and confirm
    if st.button("Lancer le rapprochement", type="primary", disabled=not ready):
        try:
            rec, summary = reconcile_ledger_balance(gl_raw, bal_raw)
            st.session_state["rec_result"] = (signature, rec, summary)
        except Exception as exc:
            st.session_state.pop("rec_result", None)
            st.error(f"Rapprochement impossible : {exc}")

    result = st.session_state.get("rec_result")
    if result and result[0] == signature and signature is not None:
        _, rec, summary = result
        kpis([("Comptes comparés", summary["comptes_comparés"]),
              ("Concordants", summary["comptes_concordants"]),
              ("À vérifier", summary["comptes_a_verifier"])])
        if summary["comptes_a_verifier"]:
            st.warning("Des différences ont été détectées. Vérifiez la période, le périmètre et les "
                       "exports avant de conclure à une anomalie.")
        else:
            st.success("Les mouvements concordent par compte selon la tolérance définie.")
        only_issues = st.checkbox("Afficher uniquement les comptes à vérifier", value=True, key="rec_only_issues")
        show_table(rec[rec["statut"] == "À vérifier"] if only_issues else rec)
        st.download_button("Télécharger le rapprochement (CSV)", data=csv_bytes(rec),
                           file_name=f"rapprochement_{stamp()}.csv", mime="text/csv")
        limits("Le rapprochement porte sur les mouvements débit/crédit par compte.",
               "Il ne rapproche pas les soldes d'ouverture et de clôture et ne remplace pas une revue comptable.")


# --------------------------------------------------------------------------
# Page : gestion opérationnelle des stocks (consommables et fournitures, CMUP)
# --------------------------------------------------------------------------
def page_gestion_stocks() -> None:
    role = current_user["role"]
    can_manage = role in {"Administrateur", "Comptable", "Responsable", "Magasinier"}
    page_header("Gestion des stocks", "Gérez les consommables et fournitures : articles, entrées, sorties, stock disponible, valorisation CMUP et historique traçable.")
    rows = stock_summary()
    if rows:
        total_value = sum((Decimal(r["valeur_stock"]) for r in rows), Decimal("0"))
        low = sum(1 for r in rows if r["alerte"] == "Stock bas")
        kpis([("Articles actifs", len(rows)), ("Valeur du stock (FCFA)", f"{total_value:,.2f}"), ("Alertes de stock", low)])
    tabs = st.tabs(["Vue du stock", "Articles", "Mouvement de stock", "Transfert", "Historique"])
    with tabs[0]:
        if rows:
            df = pd.DataFrame(rows)
            cols = ["code", "designation", "category", "unit", "warehouse", "location_zone", "location_shelf", "location_bin", "stock_disponible", "cmup", "valeur_stock", "min_qty", "alerte"]
            df = df[cols].rename(columns={"code":"Code", "designation":"Désignation", "category":"Catégorie", "unit":"Unité", "warehouse":"Magasin", "location_zone":"Zone", "location_shelf":"Étagère", "location_bin":"Casier", "stock_disponible":"Stock disponible", "cmup":"CMUP (FCFA)", "valeur_stock":"Valeur (FCFA)", "min_qty":"Seuil minimum", "alerte":"État"})
            show_table(df)
            st.download_button("Exporter l'état du stock (Excel)", data=excel_bytes({"Etat du stock": df}), file_name=f"etat_stock_{stamp()}.xlsx", mime=XLSX)
        else:
            st.info("Aucun article actif. Commencez par créer les articles dans l’onglet Articles.")
        st.caption("Le CMUP est recalculé à chaque entrée. Les sorties sont valorisées au CMUP en vigueur. Les mouvements validés sont conservés dans l’historique.")
    with tabs[1]:
        st.subheader("Créer un article consommable ou une fourniture")
        if not can_manage:
            st.info("Votre profil est en consultation seule.")
        else:
            with st.form("stock_create_item", clear_on_submit=True):
                c1,c2,c3 = st.columns(3)
                code = c1.text_input("Code article *", placeholder="EX. FOUR-001")
                designation = c2.text_input("Désignation *", placeholder="Huile moteur, papier A4…")
                category = c3.selectbox("Catégorie", ["Fournitures", "Pièces de rechange", "Carburants et lubrifiants", "Produits d’entretien", "Consommables de production", "Autres"])
                c4,c5,c6 = st.columns(3)
                unit = c4.selectbox("Unité de mesure", ["unité", "pièce", "litre", "kg", "sac", "boîte", "carton", "ramette", "mètre", "paquet"])
                warehouse = c5.text_input("Magasin / entrepôt *", value="Magasin principal")
                minimum = c6.number_input("Seuil minimum", min_value=0.0, value=0.0, step=1.0)
                loc1,loc2,loc3 = st.columns(3)
                location_zone = loc1.text_input("Zone / rayon", placeholder="Ex. Zone A")
                location_shelf = loc2.text_input("Étagère", placeholder="Ex. Étagère 03")
                location_bin = loc3.text_input("Casier / emplacement", placeholder="Ex. Casier B")
                save_item = st.form_submit_button("Créer l’article", type="primary")
            if save_item:
                try:
                    create_item({"code":code,"designation":designation,"category":category,"unit":unit,"warehouse":warehouse,"location_zone":location_zone,"location_shelf":location_shelf,"location_bin":location_bin,"min_qty":minimum}, current_user["username"])
                    st.success("Article créé. Enregistrez ensuite une entrée initiale pour alimenter le stock.")
                    st.rerun()
                except Exception as exc: st.error(f"Création impossible : {exc}")
    with tabs[2]:
        st.subheader("Enregistrer un mouvement")
        active_items = list_items(active_only=True)
        if not active_items:
            st.warning("Créez d’abord au moins un article.")
        elif not can_manage:
            st.info("Votre profil est en consultation seule.")
        else:
            with st.form("stock_movement_form"):
                item = st.selectbox("Article", active_items, format_func=lambda x: f"{x['code']} — {x['designation']} ({x['warehouse']} / {x.get('location_zone','')} / {x.get('location_shelf','')} / {x.get('location_bin','')})")
                c1,c2,c3 = st.columns(3)
                mtype = c1.selectbox("Type de mouvement", ["Entrée", "Sortie", "Ajustement +", "Ajustement -"])
                mdate = c2.date_input("Date du mouvement", value=datetime.now().date())
                qty = c3.number_input("Quantité", min_value=0.001, value=1.0, step=1.0, format="%.3f")
                loc1,loc2,loc3,loc4 = st.columns(4)
                move_warehouse = loc1.text_input("Magasin du mouvement", value=item['warehouse'])
                move_zone = loc2.text_input("Zone / rayon", value=item.get('location_zone',''))
                move_shelf = loc3.text_input("Étagère", value=item.get('location_shelf',''))
                move_bin = loc4.text_input("Casier", value=item.get('location_bin',''))
                current = next((x for x in rows if int(x["id"]) == int(item["id"]) and x['warehouse']==move_warehouse and x.get('location_zone','')==move_zone and x.get('location_shelf','')==move_shelf and x.get('location_bin','')==move_bin), None)
                if current: st.caption(f"Stock actuel : {current['stock_disponible']} {item['unit']} · CMUP actuel : {current['cmup']} FCFA")
                cost = st.number_input("Coût unitaire d’entrée (FCFA)", min_value=0.0, value=0.0, step=100.0, disabled=mtype in ("Sortie", "Ajustement -"), help="Pour une entrée, saisir le coût d'acquisition unitaire. Pour une sortie, le CMUP est appliqué automatiquement.")
                c4,c5 = st.columns(2)
                reference = c4.text_input("Référence justificative", placeholder="N° bon de réception / bon de sortie")
                beneficiary = c5.text_input("Service bénéficiaire / fournisseur")
                reason = st.text_area("Motif / observations *", placeholder="Expliquez la nature du mouvement et sa justification.")
                submit_move = st.form_submit_button("Enregistrer le mouvement", type="primary")
            if submit_move:
                if not reason.strip(): st.error("Le motif est obligatoire pour assurer la traçabilité.")
                elif mtype == "Entrée" and cost <= 0: st.error("Le coût unitaire d’une entrée doit être supérieur à zéro.")
                else:
                    try:
                        register_movement(item["id"], mtype, qty, cost, current_user["username"], str(mdate), reference, beneficiary, reason, move_warehouse, move_zone, move_shelf, move_bin)
                        st.success("Mouvement enregistré. Le stock et le CMUP ont été recalculés.")
                        st.rerun()
                    except Exception as exc: st.error(f"Mouvement refusé : {exc}")
    with tabs[3]:
        st.subheader("Transférer un article entre deux emplacements")
        active_items = list_items(active_only=True)
        if not can_manage:
            st.info("Votre profil est en consultation seule.")
        elif not active_items:
            st.info("Créez d’abord un article et enregistrez une entrée de stock.")
        else:
            with st.form("stock_transfer_form", clear_on_submit=True):
                item_t = st.selectbox("Article à transférer", active_items, format_func=lambda x: f"{x['code']} — {x['designation']}")
                st.markdown("**Emplacement de départ**")
                a,b,c,d = st.columns(4)
                src_wh = a.text_input("Magasin source *", value=item_t['warehouse'])
                src_zone = b.text_input("Zone source", value=item_t.get('location_zone',''))
                src_shelf = c.text_input("Étagère source", value=item_t.get('location_shelf',''))
                src_bin = d.text_input("Casier source", value=item_t.get('location_bin',''))
                st.markdown("**Emplacement d’arrivée**")
                e,f,g,h = st.columns(4)
                dst_wh = e.text_input("Magasin destination *", value="Magasin secondaire")
                dst_zone = f.text_input("Zone destination")
                dst_shelf = g.text_input("Étagère destination")
                dst_bin = h.text_input("Casier destination")
                q_t = st.number_input("Quantité à transférer", min_value=0.001, value=1.0, step=1.0, format="%.3f")
                date_t = st.date_input("Date du transfert", value=datetime.now().date())
                ref_t = st.text_input("Référence du bon de transfert")
                reason_t = st.text_area("Motif du transfert *", placeholder="Ex. Réorganisation du magasin")
                submit_transfer = st.form_submit_button("Valider le transfert", type="primary")
            if submit_transfer:
                try:
                    transfer_stock(item_t['id'], q_t, current_user['username'],
                        {'warehouse':src_wh,'location_zone':src_zone,'location_shelf':src_shelf,'location_bin':src_bin},
                        {'warehouse':dst_wh,'location_zone':dst_zone,'location_shelf':dst_shelf,'location_bin':dst_bin},
                        str(date_t), ref_t, reason_t)
                    st.success("Transfert enregistré. Les deux emplacements sont tracés dans l’historique.")
                    st.rerun()
                except Exception as exc: st.error(f"Transfert refusé : {exc}")
    with tabs[4]:
        history = list_movements(limit=1000)
        if history:
            hdf = pd.DataFrame(history).rename(columns={"id":"N°", "movement_date":"Date", "warehouse":"Magasin", "location_zone":"Zone", "location_shelf":"Étagère", "location_bin":"Casier", "code":"Code article", "designation":"Désignation", "movement_type":"Mouvement", "quantity":"Quantité", "unit":"Unité", "unit_cost":"Coût unitaire (FCFA)", "total_cost":"Valeur (FCFA)", "cmup_after":"CMUP après (FCFA)", "qty_after":"Stock après", "reference":"Référence", "beneficiary":"Bénéficiaire / fournisseur", "reason":"Motif", "created_by":"Enregistré par", "created_at":"Horodatage"})
            show_table(hdf)
            st.download_button("Exporter l’historique (Excel)", data=excel_bytes({"Mouvements": hdf}), file_name=f"historique_mouvements_stock_{stamp()}.xlsx", mime=XLSX)
        else: st.info("Aucun mouvement enregistré.")
        limits("Les mouvements enregistrés ne sont ni modifiables ni supprimables. En cas d’erreur, utilisez une contre-opération justifiée.", "Cette première version calcule la valorisation interne au CMUP ; elle ne génère pas automatiquement d’écritures comptables.")


# --------------------------------------------------------------------------
# Page : stocks physiques
# --------------------------------------------------------------------------
def page_stocks() -> None:
    page_header("Stocks physiques",
                "Rapprochez le comptage physique du stock théorique, identifiez les écarts de quantité "
                "et estimez leur incidence financière.")
    with st.container(border=True):
        left, right = st.columns([2, 1])
        with left:
            upload = st.file_uploader("Comptage physique (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="stock_upload")
        with right:
            tolerance = st.number_input(
                "Tolérance de quantité par ligne", min_value=0.0, value=0.0, step=0.001, format="%.3f",
                help="Seuil de signalement en unité de stock. Il ne corrige pas les quantités et ne remplace "
                     "pas les règles de l'entreprise.")
        st.download_button("Télécharger le modèle d'inventaire", data=stock_template_bytes(),
                           file_name="modele_inventaire_physique.xlsx", mime=XLSX)
        st.caption("Colonnes requises : code article, stock théorique, stock physique. Désignation, "
                   "emplacement, lot et coût unitaire sont facultatifs mais recommandés. Remplacez les "
                   "données d'exemple du modèle.")
    if upload is None:
        st.info("Importez un fichier de comptage pour lancer l'analyse.")
        return
    try:
        raw, _ = read_table(upload, "stock", "Feuille de l'inventaire")
        report, summary = analyze_physical_stock(raw, tolerance=str(tolerance))
        log_once("_logged_stock", (upload.name, upload.size, str(tolerance)), "ANALYSE_INVENTAIRE",
                 f"Fichier={upload.name}; lignes={len(report)}")
        kpis([("Lignes comptées", summary["articles_controles"]),
              ("Lignes à vérifier", summary["articles_a_verifier"]),
              ("Écart net de quantité", summary["ecart_quantite_net"]),
              ("Somme des écarts absolus", summary["somme_ecarts_quantite_absolus"])])
        kpis([("Écart valorisé net (FCFA)", summary["ecart_valorise_net"]),
              ("Somme des écarts valorisés absolus (FCFA)", summary["somme_ecarts_valorises_absolus"])])
        only_issues = st.checkbox("Afficher uniquement les lignes à vérifier", value=True, key="stock_only_issues")
        show_table(report[report["statut"] == "À vérifier"] if only_issues else report)
        st.download_button("Télécharger le rapport d'inventaire (Excel)",
                           data=excel_bytes({"Inventaire": report}),
                           file_name=f"rapport_inventaire_{stamp()}.xlsx", mime=XLSX)
        limits("Un écart positif signifie que le comptage dépasse le stock théorique ; un écart négatif "
               "signifie l'inverse.",
               "La valorisation est indicative et dépend du coût unitaire fourni.",
               "Les écarts ne sont jamais ajustés automatiquement.")
    except Exception as exc:
        st.error(f"Analyse de l'inventaire impossible : {exc}")
        st.info("Vérifiez que le fichier contient au minimum le code article, le stock théorique et le "
                "stock physique. Les intitulés usuels sont reconnus automatiquement.")


# --------------------------------------------------------------------------
# Page : paie
# --------------------------------------------------------------------------
PAY_PROFILES = ["Mensualisé", "Journalier", "Horaire", "Cadre/dirigeant salarié",
                "Commercial à commissions", "Temporaire"]


def page_paie() -> None:
    from paie import calculate_payroll, payslip_pdf

    page_header("Paie",
                "Préparez les calculs pour les mensualisés, journaliers, horaires, cadres, commerciaux "
                "à commissions et temporaires. Les paramètres sont modifiables et doivent être validés "
                "avant toute paie officielle.")
    with st.expander("Paramètres de paie — à valider pour la période", expanded=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            pension_sal = st.number_input("Pension salarié (%)", 0.0, 100.0, 3.6, 0.1, key="p_pension_sal") / 100
            pension_emp = st.number_input("Pension employeur (%)", 0.0, 100.0, 6.4, 0.1, key="p_pension_emp") / 100
        with c2:
            family = st.number_input("Prestations familiales employeur (%)", 0.0, 100.0, 9.0, 0.5, key="p_family") / 100
            risk = st.number_input("Risques professionnels employeur (%)", 0.0, 100.0, 2.0, 0.5, key="p_risk") / 100
        with c3:
            tax = st.number_input("Retenue fiscale forfaitaire (%)", 0.0, 100.0, 0.0, 0.5, key="p_tax") / 100
            other = st.number_input("Autres retenues proportionnelles (%)", 0.0, 100.0, 0.0, 0.5, key="p_other") / 100
        progressive = st.checkbox("Utiliser le barème progressif indicatif du CGI 2026 "
                                  "(à valider avec la base imposable réelle)", value=False)
        st.caption("Les taux CNSS préremplis sont des paramètres de départ : prestations familiales 9 %, "
                   "risques professionnels 1 à 4 % selon l'activité, pension 10 % répartie entre 6,4 % "
                   "employeur et 3,6 % salarié. Le calcul fiscal réel peut dépendre d'abattements, "
                   "d'avantages, de plafonds et de règles particulières : vérifiez les paramètres officiels.")
    pay_params = {"pension_salarie_taux": pension_sal, "pension_employeur_taux": pension_emp,
                  "prestations_familiales_employeur_taux": family, "risques_professionnels_employeur_taux": risk,
                  "impot_salarial_taux": tax, "autres_retenues_taux": other, "activer_impot_progressif": progressive}

    if "payroll_records" not in st.session_state:
        st.session_state.payroll_records = []

    with st.form("payroll_form", clear_on_submit=False):
        st.subheader("Saisie d'une fiche de paie")
        p1, p2, p3 = st.columns(3)
        with p1:
            matricule = st.text_input("Matricule", key="pay_matricule")
            nom = st.text_input("Nom", key="pay_nom")
            prenom = st.text_input("Prénom", key="pay_prenom")
            poste = st.text_input("Poste / classification", key="pay_poste")
        with p2:
            profile = st.selectbox("Profil", PAY_PROFILES, key="pay_type")
            period = st.text_input("Période (AAAA-MM)", value=datetime.now().strftime("%Y-%m"), key="pay_period")
            base = st.number_input("Salaire mensuel ou taux unitaire (FCFA)", min_value=0.0, value=0.0,
                                   step=1000.0, key="pay_base")
            units = st.number_input("Jours ou heures rémunérés (journalier / horaire)", min_value=0.0,
                                    value=1.0, step=1.0, key="pay_units")
        with p3:
            overtime = st.number_input("Heures supplémentaires (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_overtime")
            commissions = st.number_input("Commissions (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_comm")
            bonus = st.number_input("Primes (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_bonus")
            allowances = st.number_input("Indemnités (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_allow")
        p4, p5, p6 = st.columns(3)
        benefits = p4.number_input("Avantages en nature (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_benefits")
        recall = p5.number_input("Rappel / régularisation (+ ou −, FCFA)", value=0.0, step=500.0, key="pay_recall")
        deductions = p6.number_input("Autres retenues saisies (FCFA)", min_value=0.0, value=0.0, step=500.0, key="pay_ded")
        submitted = st.form_submit_button("Calculer et ajouter au registre", type="primary")
    if submitted:
        if not nom.strip() or not matricule.strip():
            st.error("Renseignez au minimum le nom et le matricule du salarié.")
        else:
            employee = {"matricule": matricule, "nom": nom, "prenom": prenom, "poste": poste,
                        "type_salarie": profile, "periode": period, "salaire_base": base, "taux_base": base,
                        "unites": units, "heures_supp_montant": overtime, "commissions": commissions,
                        "primes": bonus, "indemnites": allowances, "avantages_nature": benefits,
                        "rappel_regularisation": recall, "autres_retenues": deductions}
            st.session_state.payroll_records.append(calculate_payroll(employee, pay_params))
            st.success(f"Calcul ajouté pour {prenom} {nom}. Vérifiez les bases et les retenues avant validation.")

    records = st.session_state.payroll_records
    if not records:
        st.info("Le registre de paie est vide. Saisissez une première fiche ci-dessus.")
        return

    payroll_df = pd.DataFrame(records)
    total_net = sum((Decimal(str(r.get("net_a_payer", 0))) for r in records), Decimal(0))
    total_cost = sum((Decimal(str(r.get("cout_total_employeur", 0))) for r in records), Decimal(0))
    st.subheader("Registre de paie")
    kpis([("Salariés calculés", len(records)), ("Total net à payer (FCFA)", money(total_net)),
          ("Coût employeur estimé (FCFA)", money(total_cost))])
    show_table(payroll_df)

    summary_cols = ["matricule", "nom", "prenom", "periode", "brut_cash", "total_retenues",
                    "net_a_payer", "cout_total_employeur"]
    st.download_button("Exporter le registre de paie (Excel)",
                       data=excel_bytes({"Paie": payroll_df, "Synthèse": payroll_df[summary_cols]}),
                       file_name=f"registre_paie_{stamp()}.xlsx", mime=XLSX)

    st.subheader("Bulletin de paie (PDF)")
    index = st.selectbox("Salarié", list(range(len(payroll_df))),
                         format_func=lambda i: f"{payroll_df.iloc[i]['matricule']} — "
                                               f"{payroll_df.iloc[i]['prenom']} {payroll_df.iloc[i]['nom']}")
    e1, e2, e3 = st.columns(3)
    employer_name = e1.text_input("Raison sociale de l'employeur", key="pay_employer_name")
    employer_ifu = e2.text_input("IFU de l'employeur", key="pay_employer_ifu")
    employer_cnss = e3.text_input("N° employeur CNSS", key="pay_employer_cnss")
    try:
        selected = payroll_df.iloc[index].to_dict()
        pdf = payslip_pdf(selected, {"nom": employer_name or "À renseigner",
                                     "ifu": employer_ifu or "À renseigner",
                                     "cnss": employer_cnss or "À renseigner"})
        st.download_button("Télécharger le bulletin de paie", data=pdf, mime="application/pdf",
                           file_name=f"bulletin_{selected.get('matricule', 'salarie')}_{selected.get('periode', 'periode')}.pdf")
    except Exception as exc:
        st.warning(f"Génération du PDF indisponible : {exc}")
    if st.button("Effacer le registre de cette session"):
        st.session_state.payroll_records = []
        st.rerun()
    limits("Le registre reste en mémoire de session : il n'est pas sauvegardé dans la base de données.",
           "Exportez les résultats avant de fermer la session.")


# --------------------------------------------------------------------------
# Page : factures et TVA
# --------------------------------------------------------------------------
def page_factures() -> None:
    from factures import check_invoices, invoice_template_bytes

    page_header("Factures et TVA",
                "Contrôle de cohérence arithmétique HT / TVA / TTC et de la présence des champs d'identification.")
    with st.container(border=True):
        c1, c2 = st.columns(2)
        vat_rate = c1.number_input("Taux de TVA de contrôle (%)", 0.0, 100.0, 18.0, 1.0, key="invoice_vat_rate") / 100
        vat_tol = c2.number_input("Tolérance d'arrondi TVA / TTC (FCFA)", min_value=0.0, value=1.0, step=1.0, key="invoice_vat_tol")
        upload = st.file_uploader("Registre de factures (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="invoice_upload")
        st.download_button("Télécharger le modèle de registre", data=invoice_template_bytes(),
                           file_name="modele_controle_factures_tva.xlsx", mime=XLSX)
    limits("La présence d'un IFU, d'un NIM ou d'un code n'authentifie pas la facture.",
           "La vérification officielle doit être faite auprès des services compétents.")
    if upload is None:
        st.info("Importez un registre de factures pour lancer le contrôle.")
        return
    try:
        raw, _ = read_table(upload, "invoice", "Feuille des factures")
        report, summary = check_invoices(raw, vat_rate=str(vat_rate), tolerance=str(vat_tol))
        kpis([("Factures contrôlées", summary["factures_controlees"]),
              ("À vérifier", summary["factures_a_verifier"]),
              ("Sans anomalie détectée", summary["factures_sans_anomalie_detectee"])])
        show_table(report)
        parameters = pd.DataFrame([
            {"paramètre": "Taux de TVA saisi", "valeur": vat_rate},
            {"paramètre": "Tolérance (FCFA)", "valeur": vat_tol},
            {"paramètre": "Limite", "valeur": "Contrôle arithmétique seulement ; authenticité non vérifiée."}])
        st.download_button("Télécharger le rapport (Excel)",
                           data=excel_bytes({"Contrôles": report, "Données importées": raw, "Paramètres": parameters}),
                           file_name=f"rapport_factures_tva_{stamp()}.xlsx", mime=XLSX)
    except Exception as exc:
        st.error(f"Contrôle des factures impossible : {exc}")


# --------------------------------------------------------------------------
# Page : campagnes d'inventaire (suivi et validation)
# --------------------------------------------------------------------------
def page_campagnes() -> None:
    role = current_user["role"]
    can_create = role in {"Administrateur", "Comptable", "Responsable"}
    can_review = role in {"Administrateur", "Responsable"}
    page_header("Campagnes d'inventaire",
                "Les campagnes sont enregistrées dans la base de données et restent disponibles après "
                "déconnexion. Le magasinier soumet ; le responsable habilité examine et approuve.")
    labels = ["Historique"] + (["Nouvelle campagne"] if can_create else []) + (["Examen et validation"] if can_review else [])
    tabs = dict(zip(labels, st.tabs(labels)))
    campaigns = list_campaigns()

    with tabs["Historique"]:
        if campaigns:
            show_table(pd.DataFrame(campaigns))
            st.download_button("Exporter l'historique (Excel)",
                               data=excel_bytes({"Historique": pd.DataFrame(campaigns)}),
                               file_name="historique_campagnes_inventaire.xlsx", mime=XLSX)
        else:
            st.info("Aucune campagne enregistrée pour le moment.")
        if not can_create:
            st.caption("Mode consultation : la création de campagne est réservée aux rôles habilités.")

    if can_create:
        with tabs["Nouvelle campagne"]:
            storekeepers = [u for u in list_users() if u["role"] == "Magasinier" and u["active"]]
            if not storekeepers:
                st.warning("Créez d'abord un compte Magasinier actif dans l'administration pour pouvoir lui affecter une campagne.")
            else:
                with st.form("inventory_campaign_form"):
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        name = st.text_input("Nom de la campagne", value=f"Inventaire {datetime.now():%Y-%m-%d}")
                        day = st.date_input("Date de comptage", value=datetime.now().date())
                    with c2:
                        warehouse = st.text_input("Magasin / entrepôt")
                        assignee = st.selectbox("Magasinier affecté", storekeepers,
                                                format_func=lambda u: f"{u['full_name']} ({u['username']})")
                    with c3:
                        second = st.text_input("Second compteur (facultatif)")
                        status = st.selectbox("État initial", ["Préparation", "Comptage en cours", "Comptage terminé"])
                    notes = st.text_area("Consignes de comptage / observations")
                    submitted = st.form_submit_button("Créer et affecter la campagne", type="primary")
                if submitted:
                    if not name.strip() or not warehouse.strip():
                        st.error("Renseignez au minimum le nom de la campagne et le magasin.")
                    else:
                        try:
                            create_campaign({"campaign_name": name.strip(), "campaign_date": str(day),
                                             "warehouse": warehouse.strip(), "counter": assignee["full_name"],
                                             "assigned_to": assignee["username"], "second_counter": second.strip(),
                                             "status": status, "notes": notes.strip()}, current_user["username"])
                            st.success(f"Campagne affectée à {assignee['full_name']}.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Enregistrement impossible : {exc}")

    if can_review:
        with tabs["Examen et validation"]:
            choices = [c for c in campaigns if c["status"] in {"Soumis pour contrôle", "Écarts à justifier", "Approuvé"}]
            if not choices:
                st.info("Aucune campagne en attente de décision.")
            else:
                selected = st.selectbox("Campagne à examiner", choices, format_func=campaign_label,
                                        key="approve_campaign_select")
                lines = list_campaign_lines(selected["id"])
                st.markdown("**Détail du comptage enregistré**")
                if lines:
                    lines_df = pd.DataFrame(lines)
                    show_table(lines_df)
                    st.download_button("Exporter les lignes (CSV)", data=csv_bytes(lines_df), mime="text/csv",
                                       file_name=f"inventaire_campagne_{selected['id']}.csv",
                                       key=f"export_campaign_{selected['id']}")
                else:
                    st.warning("Aucune ligne de comptage n'est enregistrée pour cette campagne. Elle ne devrait pas être approuvée.")
                if selected["status"] == "Approuvé":
                    options = ["Clôturé"]
                elif selected["status"] == "Écarts à justifier":
                    options = ["À corriger"]
                else:
                    options = ["À corriger", "Écarts à justifier", "Approuvé", "Rejeté"]
                decision = st.selectbox("Décision", options, key="review_campaign_status")
                review_notes = st.text_area("Commentaire de contrôle / motif de la décision", key="review_campaign_notes")
                if st.button("Enregistrer la décision", type="primary", key="save_campaign_review"):
                    try:
                        update_campaign_status(selected["id"], decision, current_user["username"],
                                               review_notes, current_user["role"])
                        st.success("Décision enregistrée et ajoutée au journal d'audit.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Mise à jour impossible : {exc}")


# --------------------------------------------------------------------------
# Page : comptage d'inventaire (magasinier)
# --------------------------------------------------------------------------
def page_magasinier() -> None:
    page_header("Comptage d'inventaire",
                "Créez une campagne, importez le comptage, enregistrez les lignes puis transmettez la "
                "campagne au contrôle. Aucun stock n'est modifié automatiquement.")
    st.download_button("Télécharger le modèle d'inventaire", data=stock_template_bytes(),
                       file_name="modele_inventaire_physique.xlsx", mime=XLSX)

    st.subheader("1. Nouvelle campagne")
    with st.form("magasinier_campaign_form"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input("Nom de la campagne", value=f"Inventaire {datetime.now():%Y-%m-%d}")
            day = st.date_input("Date du comptage", value=datetime.now().date())
        with c2:
            warehouse = st.text_input("Magasin / entrepôt")
            second = st.text_input("Second compteur (facultatif)")
        notes = st.text_area("Observations générales")
        submitted = st.form_submit_button("Créer la campagne", type="primary")
    if submitted:
        if not name.strip() or not warehouse.strip():
            st.error("Renseignez le nom de la campagne et le magasin.")
        else:
            try:
                create_campaign({"campaign_name": name.strip(), "campaign_date": str(day),
                                 "warehouse": warehouse.strip(), "counter": current_user["full_name"],
                                 "assigned_to": current_user["username"], "second_counter": second.strip(),
                                 "status": "Comptage en cours", "notes": notes.strip()}, current_user["username"])
                st.success("Campagne créée. Vous pouvez maintenant y enregistrer le fichier de comptage.")
                st.rerun()
            except Exception as exc:
                st.error(f"Création impossible : {exc}")

    editable = [c for c in my_campaigns()
                if c["status"] in {"Préparation", "Comptage en cours", "Comptage terminé", "À corriger"}]

    st.subheader("2. Import et enregistrement du comptage")
    upload = st.file_uploader("Comptage physique (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="magasinier_stock_upload")
    tolerance = st.number_input("Tolérance de quantité par ligne", min_value=0.0, value=0.0, step=0.001,
                                format="%.3f", key="magasinier_stock_tolerance")
    if upload is not None:
        try:
            raw, _ = read_table(upload, "magasinier", "Feuille de l'inventaire")
            report, summary = analyze_physical_stock(raw, tolerance=str(tolerance))
            kpis([("Lignes contrôlées", summary["articles_controles"]),
                  ("Lignes à vérifier", summary["articles_a_verifier"]),
                  ("Écart net de quantité", summary["ecart_quantite_net"])])
            show_table(report)
            st.download_button("Télécharger le rapport de comptage (Excel)",
                               data=excel_bytes({"Comptage": report}),
                               file_name="rapport_comptage_magasinier.xlsx", mime=XLSX)
            st.caption("L'analyse seule n'enregistre pas les lignes. Rattachez-les à une campagne ci-dessous.")
            if not editable:
                st.warning("Aucune campagne modifiable. Créez une campagne avant d'enregistrer le comptage.")
            else:
                selected = st.selectbox("Campagne de rattachement", editable, key="magasinier_save_campaign",
                                        format_func=lambda c: f"N° {c['id']} — {c['campaign_name']} | {c['warehouse']} | {c['status']}")
                if st.button("Enregistrer les lignes dans la campagne", type="primary", key="save_inventory_lines"):
                    try:
                        saved = save_campaign_lines(selected["id"], report, current_user["username"], upload.name)
                        log_event(current_user["username"], "ANALYSE_INVENTAIRE",
                                  f"Fichier={upload.name}; lignes={saved}; campagne={selected['id']}")
                        st.success(f"{saved} lignes enregistrées dans la campagne n° {selected['id']}.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Enregistrement du comptage impossible : {exc}")
                saved_lines = list_campaign_lines(selected["id"])
                if saved_lines:
                    st.info(f"Cette campagne contient {len(saved_lines)} ligne(s) enregistrée(s). Un nouvel "
                            "enregistrement remplacera le détail précédent tant que la campagne n'est pas soumise.")
                    if st.button("Soumettre la campagne pour contrôle", key="submit_inventory_campaign"):
                        try:
                            submit_campaign_for_review(selected["id"], current_user["username"])
                            st.success("Campagne soumise pour contrôle. Le comptage est verrouillé contre les modifications ordinaires.")
                            st.rerun()
                        except Exception as exc:
                            st.error(f"Soumission impossible : {exc}")
        except Exception as exc:
            st.error(f"Analyse impossible : {exc}")

    st.subheader("3. Historique de vos campagnes")
    mine = my_campaigns()
    if not mine:
        st.info("Aucune campagne n'est affectée à votre compte.")
    else:
        show_table(pd.DataFrame(mine))
        viewed = st.selectbox("Consulter les lignes enregistrées", mine, key="magasinier_view_campaign",
                              format_func=campaign_label)
        lines = list_campaign_lines(viewed["id"])
        if lines:
            show_table(pd.DataFrame(lines))
        else:
            st.caption("Aucune ligne de comptage enregistrée pour cette campagne.")
    limits("Les campagnes et lignes de comptage sont conservées dans la base de données.",
           "Prévoyez des sauvegardes régulières et une copie externe.")


# --------------------------------------------------------------------------
# Page : administration
# --------------------------------------------------------------------------
def page_admin() -> None:
    page_header("Administration", "Comptes utilisateurs, journal d'audit et sauvegarde de la base de données.")
    tab_users, tab_audit, tab_backup = st.tabs(["Utilisateurs", "Journal d'audit", "Sauvegarde et exploitation"])

    with tab_users:
        with st.form("create_user_form"):
            st.markdown("**Créer un compte**")
            c1, c2 = st.columns(2)
            with c1:
                name = st.text_input("Nom complet", key="new_user_name")
                username = st.text_input("Identifiant", key="new_user_username")
            with c2:
                role = st.selectbox("Rôle", list(ROLES.keys()), key="new_user_role",
                                    help="\n\n".join(f"{k} : {v}" for k, v in ROLES.items()))
                password = st.text_input("Mot de passe initial (10 caractères minimum)", type="password",
                                         key="new_user_password")
            st.caption("Attribuez uniquement les droits nécessaires à chaque personne.")
            submitted = st.form_submit_button("Créer le compte", type="primary")
        if submitted:
            try:
                create_user(username, name, role, password, current_user["username"])
                st.success(f"Compte créé pour {name}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Création impossible : {exc}")
        rows = list_users()
        if rows:
            users_df = pd.DataFrame(rows)
            users_df["active"] = users_df["active"].map({1: "Oui", 0: "Non", True: "Oui", False: "Non"})
            show_table(users_df)
            others = [u for u in rows if u["username"].lower() != current_user["username"].lower()]
            if others:
                target = st.selectbox("Compte à activer ou désactiver", others, key="user_status_select",
                                      format_func=lambda u: f"{u['username']} — {u['role']}")
                if target["active"]:
                    if st.button("Désactiver ce compte", key="disable_user"):
                        set_user_active(target["id"], False, current_user["username"])
                        st.rerun()
                elif st.button("Réactiver ce compte", key="enable_user"):
                    set_user_active(target["id"], True, current_user["username"])
                    st.rerun()

    with tab_audit:
        audit = list_audit()
        if audit:
            show_table(pd.DataFrame(audit), height=520)
        else:
            st.info("Le journal d'audit est vide.")

    with tab_backup:
        st.warning("La sauvegarde contient les comptes utilisateurs, les campagnes et le journal d'audit. "
                   "Conservez-la dans un emplacement sécurisé, distinct du serveur.")
        try:
            st.download_button("Télécharger une sauvegarde de la base", data=backup_database_bytes(),
                               file_name=f"comptaguard_sauvegarde_{stamp()}.sqlite3",
                               mime="application/vnd.sqlite3", key="download_database_backup")
            st.caption("Une sauvegarde locale quotidienne est également tentée sur le serveur (dossier backups, "
                       "rétention de 14 jours). Elle ne remplace ni une copie externe ni un test de restauration.")
        except Exception as exc:
            st.error(f"Sauvegarde indisponible : {exc}")
        with st.expander("Avant toute mise en production", expanded=False):
            st.markdown(
                "- Tester la restauration d'une sauvegarde et configurer une copie hors serveur.\n"
                "- Activer HTTPS et un stockage persistant ; contrôler les accès réseau.\n"
                "- Définir une politique de confidentialité et de conservation des données.\n"
                "- Réaliser des tests d'intégration.\n"
                "- Les fichiers importés et les données de paie restent temporaires (mémoire de session).")


# --------------------------------------------------------------------------
# Page : tickets de pesée
# --------------------------------------------------------------------------
def page_tickets_pesee() -> None:
    can_ticket = current_user["role"] in {"Administrateur", "Comptable", "Responsable", "Magasinier"}
    page_header("Tickets de pesée",
                "Saisissez les tickets du pont-bascule. Ils servent ensuite de base à la facture de transport "
                "(règlement des transporteurs). Un ticket enregistré ne peut plus être modifié.")
    tickets = list_tickets()
    kpis([("Tickets enregistrés", len(tickets)),
          ("Tickets à régler (transport)", len(tf.tickets_to_settle()))])
    if not can_ticket:
        st.info("Votre profil est en consultation seule.")
    else:
        with st.form("ticket_form", clear_on_submit=True):
            st.markdown("**Identification**")
            a, b, c = st.columns(3)
            ticket_no = a.text_input("N° du ticket *")
            site = b.text_input("Site", value="PONT USINE SEROU")
            campaign = c.text_input("Campagne", value="2025-2026")
            st.markdown("**Pesées**")
            d1, d2, d3, d4 = st.columns(4)
            date_p1 = d1.date_input("Date P1", value=datetime.now().date(), key="t_dp1")
            time_p1 = d2.text_input("Heure P1", placeholder="23:48:47")
            date_p2 = d3.date_input("Date P2", value=datetime.now().date(), key="t_dp2")
            time_p2 = d4.text_input("Heure P2", placeholder="05:57:03")
            w1, w2, w3, w4 = st.columns(4)
            p1 = w1.number_input("Poids P1 (kg) *", min_value=0, step=10)
            p2 = w2.number_input("Poids P2 (kg) *", min_value=0, step=10)
            printed = w3.number_input("Poids net imprimé (kg)", min_value=0, step=10,
                                      help="Facultatif : s'il est renseigné, il doit être égal à |P1 − P2|.")
            humidity = w4.text_input("Taux H. (%)", placeholder="6,80")
            st.markdown("**Mouvement et produit**")
            m1, m2, m3, m4 = st.columns(4)
            vehicle = m1.text_input("Véhicule", placeholder="BZ9904RB")
            trailer = m2.text_input("Remorque")
            movement = m3.selectbox("Mouvement", ["CHARGEMENT", "DÉCHARGEMENT", "TRANSFERT", "AUTRE"])
            product = m4.text_input("Produit", value="COTON GRAINE")
            n1, n2, n3, n4 = st.columns(4)
            variety = n1.text_input("Variété", placeholder="ANG 956")
            choice = n2.text_input("Choix", placeholder="1ER CHOIX")
            cvpc = n3.text_input("CVPC", placeholder="USINE")
            market = n4.text_input("Marché", placeholder="SILO 1")
            other_market = st.text_input("Autre marché")
            st.markdown("**Parties et trajet**")
            q1, q2, q3 = st.columns(3)
            client = q1.text_input("Client", value="SOCOPA")
            supplier = q2.text_input("Fournisseur", value="SOCOPA")
            transporter = q3.text_input("Transporteur")
            r1, r2, r3, r4 = st.columns(4)
            origin = r1.text_input("Provenance", placeholder="USINE-SILOS")
            destination = r2.text_input("Destination", placeholder="ASPIRATION")
            quart = r3.text_input("Quart", placeholder="QUART B")
            commune = r4.text_input("Commune")
            s1, s2 = st.columns(2)
            evacuation = s1.text_input("BD. évacuation", placeholder="R545")
            enlevement = s2.text_input("N° d'enlèvement")
            st.markdown("**Intervenants**")
            u1, u2, u3 = st.columns(3)
            weigher = u1.text_input("Peseur")
            driver = u2.text_input("Chauffeur")
            permit = u3.text_input("N° permis chauffeur")
            observations = st.text_area("Observations", height=68)
            submit = st.form_submit_button("Enregistrer le ticket", type="primary")
        if submit:
            try:
                create_ticket({"ticket_no": ticket_no, "site": site, "campaign": campaign,
                               "date_p1": date_p1.isoformat(), "time_p1": time_p1,
                               "date_p2": date_p2.isoformat(), "time_p2": time_p2, "vehicle": vehicle,
                               "trailer": trailer, "movement": movement, "product": product,
                               "humidity_rate": humidity, "client": client, "supplier": supplier,
                               "transporter": transporter, "origin": origin, "destination": destination,
                               "quart": quart, "commune": commune, "evacuation_bd": evacuation,
                               "enlevement_no": enlevement, "choice": choice, "variety": variety, "cvpc": cvpc,
                               "market": market, "other_market": other_market, "weight_p1": p1, "weight_p2": p2,
                               "observations": observations, "weigher": weigher, "driver": driver,
                               "driver_permit": permit},
                              current_user["username"], printed_net=printed or None)
                st.success("Ticket enregistré. Le poids net a été recalculé et contrôlé.")
                st.rerun()
            except Exception as exc:
                st.error(f"Enregistrement impossible : {exc}")
    if tickets:
        df = pd.DataFrame(tickets).drop(columns=["created_by", "created_at", "invoice_id"])
        df["facture de transport"] = df.pop("transport_invoice_id").map(lambda v: "Oui" if v == v and v else "Non")
        show_table(df, height=360)
        st.download_button("Exporter les tickets (Excel)", data=excel_bytes({"Tickets": df}),
                           file_name=f"tickets_pesee_{stamp()}.xlsx", mime=XLSX)
    else:
        st.info("Aucun ticket enregistré.")


# --------------------------------------------------------------------------
# Page : règlement des transporteurs (factures de transport)
# --------------------------------------------------------------------------
def page_reglement_transporteurs() -> None:
    role = current_user.get("role", "")
    # Les factures de transport relèvent de l'administrateur principal.
    # Comptable et Auditeur disposent uniquement d'un accès en lecture.
    # Le Magasinier et les autres profils ne doivent même pas voir ce module.
    if role not in {"Administrateur", "Comptable", "Auditeur"}:
        st.warning("Accès interdit : ce module est réservé à l'administrateur principal, avec consultation seule pour le comptable et l'auditeur.")
        return
    can_manage = role == "Administrateur"
    page_header("Règlement des transporteurs",
                "Établissez la facture de transport (rapprochement coton graine) directement à partir des tickets de "
                "pesée : quantité en tonnes, prix à la tonne, AIB, gasoil, net à payer et montant en lettres.")
    invoices = tf.list_transport_invoices()
    to_settle = tf.tickets_to_settle()
    kpis([("Tickets à régler", len(to_settle)),
          ("Factures de transport", sum(1 for i in invoices if i["status"] == "Émise")),
          ("Reste à payer (FCFA)", money(sum(Decimal(i["net_to_pay"]) for i in invoices if i["status"] == "Émise" and not i["paid_at"])))])
    if can_manage:
        tabs = st.tabs(["Émettre une facture de transport", "Registre et règlement", "Transporteurs", "Référentiels AIC"])
    else:
        tabs = st.tabs(["Registre des factures (lecture seule)"])

    if can_manage:
        with tabs[0]:
            transporters = tf.list_transporters()
            if not can_manage:
                st.info("L'émission des factures de transport est réservée à l'administrateur principal.")
            elif not transporters:
                st.warning("Créez d'abord la fiche du transporteur (onglet Transporteurs).")
            else:
                with st.container(border=True):
                    tr = st.selectbox("Transporteur", transporters, format_func=lambda t: t["name"], key="tf_tr")
                    avail = tf.tickets_to_settle(tr["name"])
                    if not avail:
                        st.info("Aucun ticket non réglé pour ce transporteur. Le nom du transporteur du ticket doit correspondre à sa fiche.")
                    chosen = st.multiselect("Tickets à régler", avail, default=avail, key=f"tf_tickets_{tr['id']}",
                                            format_func=lambda t: f"N° {t['ticket_no']} · {t['date_p2']} · {t['vehicle'] or '—'} · {t['net_weight']} kg")
                    c1, c2, c3 = st.columns(3)
                    campaign = c1.text_input("Campagne", value=(avail[0]["campaign"] if avail else "2025-2026"), key="tf_campaign")
                    usine = c2.text_input("Usine", value="USINE DE DJOUGOU", key="tf_usine")
                    inv_date = c3.date_input("Date de la facture", value=datetime.now().date(), key="tf_date")
                    p1, p2, p3 = st.columns(3)
                    price_ne = p1.number_input("Prix / tonne — Non enclavée (FCFA)", min_value=0.0, value=2500.0, step=50.0, key="tf_p_ne")
                    price_e = p2.number_input("Prix / tonne — Enclavée (FCFA)", min_value=0.0, value=0.0, step=50.0, key="tf_p_e",
                                              help="À renseigner si certaines lignes sont en zone enclavée.")
                    aib = p3.number_input("Prélèvement AIB (%)", min_value=0.0, max_value=100.0, value=3.0, step=0.5, key="tf_aib",
                                          help="3 % sur le modèle fourni. À valider selon la situation fiscale du transporteur.")
                    g1, g2, g3 = st.columns(3)
                    fuel_l = g1.number_input("Gasoil à déduire (litres)", min_value=0.0, value=0.0, step=1.0, key="tf_fuel_l")
                    fuel_p = g2.number_input("Prix du litre (FCFA)", min_value=0.0, value=0.0, step=5.0, key="tf_fuel_p")
                    objet = g3.text_input("Objet", value="FACTURE RAPPROCHEMENT COTON GRAINE", key="tf_obj")
                    prices = {"Non Enclavée": price_ne, "Enclavée": price_e}
                    tr_inv = ref.transporter_for_invoice(tr["id"], campaign)
                    if tr_inv["contract_for_campaign"]:
                        st.caption(f"Contrat n° {tr_inv['contract_no']} (campagne {campaign})")
                    else:
                        st.warning(f"Aucun contrat importé pour la campagne {campaign} : "
                                   f"dernier contrat connu n° {tr_inv['contract_no'] or '—'}. Importez le fichier des contrats (onglet Référentiels AIC).")
                    lines = tf.lines_from_tickets(chosen, prices, usine, campaign) if chosen else []
                    undefined = [l["ticket_no"] for l in lines if l["typo"] == ref.TYPO_UNDEFINED]
                    if undefined:
                        st.warning("Typologie introuvable dans le fichier AIC de la campagne pour les tickets : "
                                   + ", ".join(undefined) + ". Importez le fichier AIC ou choisissez la typologie dans le tableau.")
                    if lines:
                        st.caption("La typologie vient du fichier AIC de la campagne (CVPC + marché) ; vous pouvez la corriger ligne par ligne.")
                        grid = pd.DataFrame([{"Ticket": l["ticket_no"], "Date": l["line_date"], "Véhicule": l["vehicle"],
                                              "BE AIC": l["be_aic"], "Marché": l["market"], "Qté (T)": float(l["qty_t"]),
                                              "Typo": l["typo"]} for l in lines])
                        edited = st.data_editor(grid, hide_index=True, use_container_width=True,
                                                disabled=["Ticket", "Date", "Véhicule", "BE AIC", "Marché", "Qté (T)"],
                                                column_config={"Typo": st.column_config.SelectboxColumn("Typo", options=tf.TYPOS + [ref.TYPO_UNDEFINED], required=True)},
                                                key="tf_grid_" + "_".join(str(l["ticket_id"]) for l in lines))
                        for l, typo in zip(lines, edited["Typo"]):
                            l["typo"], l["unit_price"] = typo, Decimal(str(prices.get(typo, 0)))
                        try:
                            calc = tf.compute_transport(lines, aib / 100, fuel_l, fuel_p)
                            kpis([("Poids total (T)", f"{calc['total_weight_t']}"), ("Total HT (FCFA)", money(calc["total_ht"])),
                                  (f"AIB {aib:g} % (FCFA)", money(calc["aib_amount"])), ("Net à payer (FCFA)", money(calc["net_to_pay"]))])
                            st.caption(f"Arrêté à : {calc['amount_words']}")
                        except Exception as exc:
                            calc = None
                            st.error(str(exc))
                        if st.button("Émettre la facture de transport", type="primary", key="tf_issue", disabled=calc is None):
                            try:
                                res = tf.create_transport_invoice(tr_inv, lines, aib / 100, current_user["username"], campaign,
                                                                  invoice_date=inv_date.isoformat(), objet=objet,
                                                                  fuel_liters=fuel_l, fuel_unit_price=fuel_p)
                                st.session_state["tf_last"] = res["id"]
                                st.success(f"Facture {res['number']} émise — net à payer {money(res['net_to_pay'])} FCFA.")
                            except Exception as exc:
                                st.error(f"Émission impossible : {exc}")
                last = st.session_state.get("tf_last")
                if last:
                    full = tf.get_transport_invoice(last)
                    st.download_button(f"Télécharger {full['number']} (PDF)", data=tf.transport_pdf(full),
                                       file_name=f"facture_transport_{full['number']}.pdf", mime="application/pdf", key="tf_dl_last")
                limits("Les prix à la tonne et le taux d'AIB sont des paramètres saisis ici ; ils ne sont pas déduits du ticket.",
                       "Un ticket ne peut être intégré qu'à une seule facture de transport ; l'annulation le libère.",
                       "Quantité en tonnes = poids net ÷ 1 000, arrondie à 0,01 T.")

    with (tabs[1] if can_manage else tabs[0]):
        if not invoices:
            st.info("Aucune facture de transport émise.")
        else:
            reg = pd.DataFrame(invoices)[["campaign", "number", "invoice_date", "transporter_name", "total_weight_t", "total_ht",
                                          "aib_amount", "net_to_pay", "status", "paid_at"]]
            reg.columns = ["Campagne", "N°", "Date", "Transporteur", "Poids (T)", "HT", "AIB", "Net à payer", "Statut", "Réglée le"]
            show_table(reg, height=300)
            st.download_button("Exporter le registre (Excel)", data=excel_bytes({"Factures transport": reg}),
                               file_name=f"registre_transport_{stamp()}.xlsx", mime=XLSX)
            sel = st.selectbox("Facture", invoices, key="tf_reg_sel",
                               format_func=lambda i: f"{i['campaign']} · {i['number']} — {i['transporter_name']} ({i['status']})")
            full = tf.get_transport_invoice(sel["id"])
            st.download_button("Télécharger le PDF", data=tf.transport_pdf(full), file_name=f"facture_transport_{full['number']}.pdf",
                               mime="application/pdf", key="tf_dl_reg")
            if can_manage and full["status"] == "Émise":
                if not full["paid_at"]:
                    with st.expander("Enregistrer le règlement"):
                        r1, r2, r3 = st.columns(3)
                        pay_date = r1.date_input("Date de règlement", value=datetime.now().date(), key="tf_pay_date")
                        pay_mode = r2.selectbox("Mode", ["Virement", "Espèces", "Chèque", "Mobile money"], key="tf_pay_mode")
                        pay_ref = r3.text_input("Référence", key="tf_pay_ref")
                        if st.button("Marquer comme réglée", key="tf_pay_btn"):
                            try:
                                tf.record_payment(full["id"], pay_date.isoformat(), pay_mode, pay_ref, current_user["username"])
                                st.success("Règlement enregistré.")
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))
                    with st.expander("Annuler cette facture"):
                        reason = st.text_input("Motif d'annulation *", key="tf_cancel_reason")
                        if st.button("Annuler la facture", key="tf_cancel_btn"):
                            try:
                                tf.cancel_transport_invoice(full["id"], reason, current_user["username"])
                                st.success("Facture annulée ; les tickets sont à nouveau disponibles.")
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))
                else:
                    st.success(f"Réglée le {full['paid_at']} ({full['payment_mode']} {full['payment_ref']}).")

    if can_manage:
        with tabs[2]:
            if not can_manage:
                st.info("La gestion des transporteurs est réservée à l'administrateur principal.")
            else:
                with st.form("tf_transporter_form", clear_on_submit=True):
                    a, b = st.columns(2)
                    name = a.text_input("Nom & prénoms *", help="Doit correspondre au nom du transporteur sur les tickets.")
                    phone = b.text_input("Téléphone")
                    c, d, e = st.columns(3)
                    ifu = c.text_input("IFU")
                    contract = d.text_input("N° de contrat")
                    id_piece = e.text_input("N° pièce d'identité")
                    if st.form_submit_button("Enregistrer le transporteur", type="primary"):
                        try:
                            tf.save_transporter({"name": name, "phone": phone, "ifu": ifu, "contract_no": contract, "id_piece": id_piece},
                                                current_user["username"])
                            st.success("Fiche enregistrée.")
                            st.rerun()
                        except Exception as exc:
                            st.error(str(exc))
                rows = tf.list_transporters()
                if rows:
                    show_table(pd.DataFrame(rows).drop(columns=["id", "created_by", "created_at"]))

    if can_manage:
        with tabs[3]:
            if not can_manage:
                st.info("La modification des référentiels AIC est réservée à l'administrateur principal.")
            else:
                st.caption("Chaque année, importez les fichiers fournis par l'AIC : l'historique des campagnes précédentes est conservé.")
                ref_campaign = st.text_input("Campagne concernée", value="2025-2026", key="ref_campaign")

                st.subheader("1. Typologie, CVPC et marché (fichier AIC)")
                st.download_button("Télécharger le modèle", data=ref.market_template_bytes(), file_name="modele_marches_aic.xlsx",
                                   mime=XLSX, key="ref_mk_tpl")
                up_mk = st.file_uploader("Fichier AIC (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="ref_mk_upload")
                if up_mk is not None:
                    try:
                        raw_mk, _ = read_table(up_mk, "ref_mk", "Feuille du fichier AIC")
                        parsed = ref.parse_markets(raw_mk)
                        kpis([("Lignes valides", len(parsed["rows"])), ("Lignes rejetées", len(parsed["rejected"]))])
                        if parsed["ignored_columns"]:
                            st.caption("Colonnes non utilisées : " + ", ".join(parsed["ignored_columns"]))
                        if parsed["rejected"]:
                            show_table(pd.DataFrame(parsed["rejected"]))
                        if parsed["rows"]:
                            show_table(pd.DataFrame(parsed["rows"]).head(50))
                            replace = st.checkbox("Remplacer tout le référentiel de cette campagne (sinon : mise à jour des lignes)",
                                                  key="ref_mk_replace")
                            if st.button("Importer le fichier AIC", type="primary", key="ref_mk_go"):
                                res = ref.save_markets(parsed["rows"], ref_campaign, current_user["username"], replace=replace,
                                                       source_file=up_mk.name)
                                st.success(f"{res['imported']} lignes importées — {res['total_campaign']} marchés pour la campagne {ref_campaign}.")
                    except Exception as exc:
                        st.error(f"Import impossible : {exc}")
                current_mk = ref.list_markets(ref_campaign)
                with st.expander(f"Marchés enregistrés pour {ref_campaign} ({len(current_mk)})"):
                    show_table(pd.DataFrame(current_mk), height=300)

                st.subheader("2. Transporteurs et numéros de contrat")
                st.download_button("Télécharger le modèle", data=ref.transporter_template_bytes(), file_name="modele_transporteurs_contrats.xlsx",
                                   mime=XLSX, key="ref_tr_tpl")
                up_tr = st.file_uploader("Fichier des transporteurs et contrats (.xlsx, .xlsm, .csv)", type=ACCEPTED, key="ref_tr_upload")
                if up_tr is not None:
                    try:
                        raw_tr, _ = read_table(up_tr, "ref_tr", "Feuille des transporteurs")
                        parsed_tr = ref.parse_transporters(raw_tr)
                        kpis([("Lignes valides", len(parsed_tr["rows"])), ("Lignes rejetées", len(parsed_tr["rejected"]))])
                        if parsed_tr["rejected"]:
                            show_table(pd.DataFrame(parsed_tr["rejected"]))
                        if parsed_tr["rows"]:
                            show_table(pd.DataFrame(parsed_tr["rows"]).head(50))
                            if st.button("Importer les transporteurs et contrats", type="primary", key="ref_tr_go"):
                                res = ref.save_transporters(parsed_tr["rows"], ref_campaign, current_user["username"], source_file=up_tr.name)
                                st.success(f"{res['imported']} contrats importés pour {ref_campaign} dont {res['created']} nouveaux transporteurs.")
                    except Exception as exc:
                        st.error(f"Import impossible : {exc}")
                current_ct = ref.list_contracts(ref_campaign)
                with st.expander(f"Contrats enregistrés pour {ref_campaign} ({len(current_ct)})"):
                    show_table(pd.DataFrame(current_ct), height=300)

                st.subheader("3. Noms de transporteurs non reconnus sur les tickets")
                unmatched = ref.unmatched_ticket_transporters()
                fiches = tf.list_transporters()
                if not unmatched:
                    st.caption("Tous les noms de transporteurs des tickets correspondent à une fiche.")
                elif not fiches:
                    st.info("Importez d'abord les transporteurs.")
                else:
                    st.caption("Rattachez chaque nom du ticket à la bonne fiche : la correspondance est mémorisée pour les prochains tickets.")
                    for i, u in enumerate(unmatched):
                        c1, c2, c3 = st.columns([3, 3, 1])
                        c1.markdown(f"**{md_escape(u['name'])}** — {u['tickets']} ticket(s)")
                        default = next((k for k, f in enumerate(fiches) if ref.norm(f["name"]) == u["suggestion_norm"]), 0)
                        target = c2.selectbox("Fiche", fiches, index=default, format_func=lambda f: f["name"],
                                              key=f"ref_alias_sel_{i}", label_visibility="collapsed")
                        if c3.button("Rattacher", key=f"ref_alias_btn_{i}"):
                            ref.link_alias(u["name"], target["id"], current_user["username"])
                            st.rerun()


# --------------------------------------------------------------------------
# Navigation par rôle
# --------------------------------------------------------------------------
PAGES = [
    # (section, titre, icône, chemin, fonction, rôles autorisés)
    ("Contrôles", "Contrôle des écritures", ":material/fact_check:", "ecritures", page_ecritures,
     {"Administrateur", "Comptable", "Auditeur"}),
    ("Contrôles", "Rapprochement", ":material/compare_arrows:", "rapprochement", page_rapprochement,
     {"Administrateur", "Comptable", "Responsable", "Auditeur"}),
    ("Contrôles", "Factures et TVA", ":material/receipt_long:", "factures", page_factures,
     {"Administrateur", "Comptable"}),
    ("Transport", "Tickets de pesée", ":material/scale:", "tickets-pesee", page_tickets_pesee,
     {"Administrateur", "Comptable", "Responsable", "Auditeur"}),
    ("Transport", "Factures de transport", ":material/local_shipping:", "reglement-transporteurs", page_reglement_transporteurs,
     {"Administrateur", "Comptable", "Auditeur"}),
    ("Stocks", "Gestion des stocks", ":material/warehouse:", "gestion-stocks", page_gestion_stocks,
     {"Administrateur", "Comptable", "Responsable", "Auditeur", "Magasinier"}),
    ("Stocks", "Stocks physiques", ":material/inventory_2:", "stocks", page_stocks,
     {"Administrateur", "Comptable", "Responsable", "Auditeur"}),
    ("Stocks", "Campagnes d'inventaire", ":material/assignment:", "campagnes", page_campagnes,
     {"Administrateur", "Comptable", "Responsable", "Auditeur"}),
    ("Stocks", "Comptage d'inventaire", ":material/barcode_scanner:", "comptage", page_magasinier, {"Magasinier"}),
    ("Paie", "Paie", ":material/payments:", "paie", page_paie, {"Administrateur", "Gestionnaire de paie"}),
    ("Administration", "Administration", ":material/admin_panel_settings:", "administration", page_admin,
     {"Administrateur"}),
]


def build_navigation(role: str) -> dict:
    sections: dict[str, list] = {}
    for section, title, icon, path, func, roles in PAGES:
        if role in roles:
            sections.setdefault(section, []).append(st.Page(func, title=title, icon=icon, url_path=path))
    return sections


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------
if not has_users():
    screen_setup()
    st.stop()

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None
if st.session_state.auth_user is None:
    screen_login()
    st.stop()

# Déconnexion automatique après une période d'inactivité (0 pour désactiver).
_now_ts = time.time()
_last_seen = st.session_state.get("last_activity")
if SESSION_TIMEOUT_MINUTES > 0 and _last_seen is not None and _now_ts - _last_seen > SESSION_TIMEOUT_MINUTES * 60:
    log_event(st.session_state.auth_user.get("username", "inconnu"), "SESSION_EXPIREE",
              "Déconnexion automatique après inactivité")
    st.session_state.auth_user = None
    st.session_state["last_activity"] = None
    st.session_state["session_notice"] = (f"Votre session a expiré après {SESSION_TIMEOUT_MINUTES} minutes "
                                          "d'inactivité. Veuillez vous reconnecter.")
    st.rerun()
st.session_state["last_activity"] = _now_ts

_active = get_active_user(st.session_state.auth_user.get("id"))
if _active is None:
    st.session_state.auth_user = None
    st.session_state["session_notice"] = "Votre session a expiré ou votre compte a été désactivé. Veuillez vous reconnecter."
    st.rerun()
current_user = _active

navigation = build_navigation(current_user["role"])
if not navigation:
    st.error("Aucun module n'est associé à votre rôle. Contactez votre administrateur.")
    st.stop()

pg = st.navigation(navigation)
with st.sidebar:
    st.divider()
    st.markdown(f"**{md_escape(current_user['full_name'])}**")
    st.caption(current_user["role"])
    if st.button("Se déconnecter", key="logout_button"):
        log_event(current_user["username"], "DECONNEXION", "Déconnexion volontaire")
        st.session_state.auth_user = None
        st.rerun()
    st.caption(f"{APP_NAME} · version {APP_VERSION}")
pg.run()
