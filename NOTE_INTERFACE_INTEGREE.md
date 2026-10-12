# ComptaGuard V12 — version fusionnée (12 octobre 2026)

Cette archive réunit la refonte de l'interface et les corrections, avec le thème vert.

## Origine des fichiers
- `controles.py` (corrigé), `tolerance.py`, `factures.py`, `paie.py`, `stocks.py` : modules métier du zip d'origine.
- `security_db.py` : version « renforcée » du PDF `Comptaguard_v12_complet.pdf` (lignes de comptage, soumission au
  contrôle, sauvegardes), plus le blocage de connexion. Les bases créées avec l'ancien `security_db.py` s'ouvrent sans perte.
  Si vous possédez un `security_db.py` plus récent, conservez-le et ajoutez-y `login_lock_remaining`.
- `app.py` / `ui.py` : interface par pages et par rôle, vouvoiement, sans numéros de version dans les titres.
- `.streamlit/config.toml` : thème vert (contrastes vérifiés, au moins 4,5:1) et `headless = true`.

## Corrections
1. Équilibre des pièces, balance et rapprochement : sommes et seuils en Decimal. L'ancien `.round(2)` masquait les
   écarts inférieurs à 0,005 FCFA, et un écart égal à la tolérance pouvait être signalé à tort (artefact des flottants).
2. Connexion : blocage de 15 minutes après 5 échecs consécutifs (SYSCOHADA_MAX_FAILED_LOGINS, SYSCOHADA_LOCKOUT_MINUTES).
3. Session : déconnexion après 30 minutes d'inactivité (SYSCOHADA_SESSION_TIMEOUT_MINUTES, 0 pour désactiver).
4. Accès : le rapprochement est réservé à Administrateur, Comptable, Responsable et Auditeur (le rôle
   « Gestionnaire de paie » n'accède qu'à la paie, conformément à sa description).
5. Paie : totaux corrects (les montants Decimal étaient comptés pour 0).
6. Rapprochement : le résultat reste affiché quand on change un filtre.
7. Inventaire : le bouton « Excel » fournit un vrai fichier Excel ; le journal d'audit n'enregistre plus
   « ANALYSE_INVENTAIRE » à chaque rafraîchissement.
8. Exports Excel : en-têtes mis en forme, sans formules injectées (cellule commençant par « = »), sans fuseaux horaires.

## Vérification
Lancer `pytest -q` depuis ce dossier : 90 tests, dont `test_pages_integration.py` qui exécute chaque page avec les vrais
modules et une base temporaire (Streamlit y est simulé). Cela ne remplace pas un essai dans un navigateur.

## Reste à faire
- Les montants sont toujours lus en float à l'import ; le calcul des écarts est exact à partir de leur valeur affichée.
- Le registre de paie reste en mémoire de session : exportez-le avant de fermer.
- Base hors serveur avec sauvegardes externes testées, revue de sécurité indépendante,
  plan comptable officiel, taux CNSS et barème d'impôt datés et validés.
