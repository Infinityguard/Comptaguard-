# Journal des modifications — V12

## Objectif
Préparer une première version mono-entreprise avec plusieurs comptes individuels, notamment un accès dédié au magasinier.

## Fonctionnalités ajoutées
- Initialisation du premier compte Administrateur lors du premier lancement, sans mot de passe par défaut.
- Connexion individuelle et déconnexion.
- Stockage des mots de passe sous forme de hachage PBKDF2-HMAC-SHA256 avec sel aléatoire.
- Rôles d'accès : Administrateur, Comptable, Magasinier, Responsable, Gestionnaire de paie et Auditeur.
- Espace magasinier limité au stock/inventaire; les modules comptables, factures et paie ne sont pas affichés à ce rôle.
- Création, activation et désactivation de comptes par l'administrateur; protection contre la désactivation du dernier administrateur actif.
- Base SQLite pour les comptes, campagnes d'inventaire et journal d'audit.
- Campagnes d'inventaire persistantes, même après déconnexion; décision d'examen réservée à l'Administrateur ou au Responsable.
- Journalisation des connexions, déconnexions, créations/modifications de comptes, analyses d'inventaire et décisions de campagne.
- Vérification du statut actif de l'utilisateur à chaque rechargement de l'application.
- Tests unitaires dédiés à l'authentification, aux campagnes persistantes et à l'audit.

## Limites connues
- Cette version vise une seule entreprise par installation. La séparation multi-entreprises est réservée à une version ultérieure.
- SQLite est le stockage de départ. Sur un hébergement cloud, il faut un disque persistant et des sauvegardes testées; un système de fichiers éphémère peut perdre la base.
- Les fichiers importés, les rapports temporaires et les données de paie restent temporaires/en mémoire de session.
- Il n'y a pas encore de procédure intégrée de réinitialisation de mot de passe, d'authentification multifacteur, de chiffrement applicatif des données métier, ni de sauvegarde/restauration automatisée.
- Les rôles contrôlent les écrans disponibles dans l'application. Une revue de sécurité indépendante et des tests d'intégration de l'interface sont nécessaires avant production.
- Streamlit n'était pas installé dans l'environnement de construction; l'interface n'a pas pu être démarrée ici. Le code compile et les tests automatisés passent.
- Les calculs de paie et contrôles comptables restent des aides de travail, pas une certification de conformité.

## Extension — Gestion opérationnelle des stocks (CMUP)
- Ajout d'un module distinct « Gestion des stocks » pour les consommables et fournitures.
- Création du référentiel articles : code unique, désignation, catégorie, unité, magasin et seuil minimum.
- Enregistrement des entrées, sorties et ajustements avec référence, bénéficiaire et motif obligatoire.
- Calcul du coût moyen unitaire pondéré (CMUP), de la valeur du stock et du coût des sorties.
- Prévention des sorties supérieures au stock disponible.
- Historique append-only : les mouvements enregistrés ne peuvent être modifiés ou supprimés ; les erreurs doivent être corrigées par une contre-opération documentée.
- Export Excel de l'état du stock et de l'historique.
- Le module d'inventaire physique reste séparé et n'ajuste pas automatiquement le stock.
- Tests de valorisation CMUP, refus de stock négatif et validation des quantités : 90 tests réussis, 1 ignoré.

## Ajout — Tickets de pesée (base du règlement des transporteurs)
- Nouveau module `emission_factures.py` et nouvelle page « Émission de factures » (section Ventes).
- Tickets de pesée : tous les champs du ticket papier (N°, site, campagne, P1/P2 avec dates et heures, véhicule, remorque, mouvement, produit, taux H., client, fournisseur, transporteur, provenance, destination, quart, commune, BD évacuation, variété, choix, CVPC, marché, peseur, chauffeur, permis). Poids net recalculé (|P1 − P2|) et comparé au net imprimé.
- Factures : numérotation continue FAC-AAAA-NNNNN, lignes issues des tickets (quantité = poids net en kg) ou lignes libres, TVA paramétrable, PDF avec annexe des pesées.
- Intégrité : factures et tickets immuables (déclencheurs SQL), annulation avec motif qui libère les tickets, journal d'audit, code MECeF/NIM renseignable après normalisation.
- Tests : `test_emission_factures.py`.

## Ajout — Règlement des transporteurs (facture de transport)
- Nouveau module `transport_factures.py` et page « Règlement des transporteurs » : facture « RAPPROCHEMENT COTON GRAINE » établie à partir des tickets de pesée (une ligne par ticket, quantité en tonnes, prix à la tonne selon la typologie enclavée / non enclavée).
- Totaux : poids, total HT, prélèvement AIB (taux saisi), déduction gasoil, net à payer, montant arrêté en lettres. Numérotation R0001… continue par campagne.
- Fiche transporteur (IFU, contrat, pièce d'identité), enregistrement du règlement, annulation avec motif (libère les tickets), factures figées, PDF paysage conforme au modèle.
- Tests : `test_transport_factures.py` (chiffres de la facture R0025).

- Précision : la facturation client n'est pas exposée dans l'interface (hors périmètre) ; seule la saisie des tickets de pesée est conservée (page « Tickets de pesée », section Transport).

## Ajout — Référentiels annuels AIC et contrats transporteurs
- Nouveau module `referentiels_aic.py` et onglet « Référentiels AIC » : import chaque année du fichier AIC (CVPC, marché, typologie) et du fichier des transporteurs avec numéro de contrat, par campagne (historique conservé).
- Lecture tolérante : détection de l'en-tête même sous des lignes de titre, synonymes de colonnes, lignes rejetées listées avec la raison, aperçu avant import, option « remplacer la campagne ».
- La typologie de chaque ligne de facture vient du couple (CVPC, marché) du ticket ; si le marché est absent du fichier, la ligne est bloquée (« À définir ») jusqu'à correction.
- Le numéro de contrat est celui de la campagne de la facture.
- Noms de transporteurs : comparaison normalisée (accents, casse, ponctuation) + alias à valider pour les noms qui diffèrent sur les tickets.
- Tests : `test_referentiels_aic.py`.


## Fusion AIC + gestion avancée des stocks
- Fusion des modules de référentiels AIC, tickets de pesée, factures de transport et règlements transporteurs avec la gestion des stocks multi-magasins.
- Conservation des zones, étagères, casiers et transferts entre emplacements.
- Correction des droits de navigation du profil Magasinier : accès limité aux fonctions de stock/inventaire prévues.
- Contrôles automatisés exécutés avant livraison du ZIP fusionné.

## Correctif des droits — Factures de transport
- L'émission, le règlement, l'annulation des factures, la gestion des fiches transporteurs et la modification des référentiels AIC sont réservés au rôle Administrateur (administrateur principal).
- Les rôles Comptable et Auditeur peuvent consulter le registre des factures et télécharger les PDF/exports en lecture seule.
- Le rôle Magasinier ne voit pas le module Factures de transport et son accès direct est refusé.
- Le rôle Responsable n'a plus accès au module Factures de transport.
