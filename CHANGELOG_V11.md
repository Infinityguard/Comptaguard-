# Contrôle comptable SYSCOHADA — V11

## Base conservée depuis V10
- Contrôle des écritures, grand livre et balance générale.
- Contrôles de qualité à l'import, priorisation des risques et suivi des tolérances.
- Rapprochement grand livre / mouvements de balance.
- Contrôle des écarts d'inventaire physique et estimation de leur valeur.

## Ajouts V11
- `paie.py` : calcul de paie paramétrable pour plusieurs profils, régularisations positives/négatives, cotisations employeur/salarié paramétrables et génération de bulletin PDF.
- Interface de paie : registre de session, export Excel et bulletins PDF individuels.
- `factures.py` : contrôle de présence de champs de facture, doublons de numéros, TVA attendue au taux saisi et cohérence HT + TVA = TTC.
- Interface factures/TVA : modèle Excel et rapport de contrôle.
- Interface de suivi des campagnes d'inventaire : responsable, second comptage, entrepôt, statut, justification et historique exportable.
- Tests unitaires additionnels pour paie et factures.

## Vérification effectuée
- Compilation Python : réussie.
- Tests : 41 réussis.
- Génération d'un bulletin PDF de test : réussie.
- Test de démarrage Streamlit : non exécuté dans l'environnement de construction, car le paquet Streamlit n'y était pas installé. Il figure dans `requirements.txt`.

## Limites restantes
- Pas encore de base de données persistante ni d'authentification robuste.
- Pas encore de synchronisation multi-utilisateurs ni d'application native App Store/Play Store/Microsoft Store.
- Les calculs de paie et les contrôles de factures sont des aides de préparation et ne certifient pas la conformité fiscale/sociale ni l'authenticité des factures.
