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
