# Corrections du 12 octobre 2026

## Contenu de cette archive
- Les modules métier `controles.py` (corrigé), `tolerance.py`, `factures.py`, `paie.py`, `stocks.py` : ceux du zip d'origine.
- `security_db.py` : version « renforcée » issue du PDF `Comptaguard_v12_complet.pdf`, avec le blocage de connexion en plus.
  Le zip d'origine contenait une version plus ancienne (sans lignes de comptage, soumission au contrôle ni sauvegardes).
  Si vous disposez d'un `security_db.py` plus récent, conservez le vôtre et ajoutez-y `login_lock_remaining`.
- `app.py` et `ui.py` : nouvelle interface (navigation par rôle, thème, vouvoiement), sans numéros de version.

## Corrections
1. Équilibre des pièces (`controles.py`) : calcul exact en Decimal. L'ancien `.round(2)` masquait les écarts
   inférieurs à 0,005 FCFA, même avec une tolérance de 0,001. Tests : `test_equilibre_decimal.py`.
2. Connexion : blocage temporaire après 5 échecs consécutifs (15 minutes), sans nouvelle colonne en base.
   Réglable : SYSCOHADA_MAX_FAILED_LOGINS, SYSCOHADA_LOCKOUT_MINUTES. Tests : `test_connexion_securite.py`.
3. Session : déconnexion automatique après 30 minutes d'inactivité (SYSCOHADA_SESSION_TIMEOUT_MINUTES, 0 pour désactiver).
4. Interface : totaux de paie, résultat du rapprochement, export Excel des inventaires, journal d'audit non pollué,
   exports Excel sans formules injectées et sans fuseaux horaires.

## Reste à faire (non traité ici)
- Les montants restent lus en float à l'import ; seul le contrôle d'équilibre des pièces est désormais exact.
- Base persistante hors serveur, sauvegardes externes testées, revue de sécurité indépendante.
- Plan comptable officiel, taux CNSS et barème d'impôt datés et validés.
