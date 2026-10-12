# Guide d'utilisation — Contrôle comptable SYSCOHADA V12

## 1. Périmètre
La V12 est une application web pour une seule entreprise par installation. Elle ajoute des comptes utilisateurs, des rôles et une conservation SQLite des campagnes d'inventaire et des actions sensibles. Elle fonctionne dans un navigateur, sous réserve d'installer les dépendances et d'héberger l'application.

## 2. Premier démarrage
1. Installer Python 3.10 ou plus récent.
2. Ouvrir un terminal dans le dossier de l'application.
3. Installer les dépendances avec `python -m pip install -r requirements.txt`.
4. Lancer `streamlit run app.py`.
5. Au premier accès, créer le compte Administrateur initial. Il n'existe aucun mot de passe par défaut.
6. Se connecter, puis ouvrir dans la barre latérale « Administration des utilisateurs » pour créer les comptes du personnel.

Le mot de passe doit comporter au moins 10 caractères. Chaque utilisateur doit avoir son propre compte. Ne partagez pas les identifiants.

## 3. Rôles
- **Administrateur** : tous les modules, gestion des comptes et journal d'audit.
- **Comptable** : contrôles comptables, contrôle des factures/TVA, stocks et campagnes.
- **Magasinier** : espace dédié au comptage physique et aux campagnes. Il peut soumettre les résultats, mais pas approuver les ajustements.
- **Responsable** : suivi des stocks/campagnes et décisions d'approbation des campagnes.
- **Gestionnaire de paie** : module de préparation de paie.
- **Auditeur** : contrôles comptables et examen des stocks/campagnes.

Attribuez uniquement les droits nécessaires. Les comptes peuvent être désactivés depuis l'administration. Le dernier administrateur actif ne peut pas être désactivé.

## 4. Parcours du magasinier
1. L'administrateur crée un compte avec le rôle « Magasinier ».
2. Le magasinier se connecte avec ses propres identifiants.
3. Il crée une campagne et indique le magasin, la date et les observations.
4. Il importe le fichier de comptage Excel/CSV et examine les écarts.
5. Il télécharge le rapport de comptage et le transmet au comptable selon la procédure interne.
6. La campagne est enregistrée dans la base et apparaît dans l'historique.
7. L'administrateur ou le responsable examine la campagne et consigne une décision.

L'application ne corrige jamais automatiquement le stock ou les écritures comptables. Un écart doit être justifié et contrôlé.

## 5. Données conservées
Par défaut, la base se trouve dans `data/syscohada.sqlite3` sous le dossier de l'application. Pour changer son emplacement, définir la variable d'environnement `SYSCOHADA_DB_PATH` vers un chemin protégé et persistant avant le lancement.

La base contient les comptes, les campagnes et le journal d'audit. Elle ne contient pas encore tous les fichiers importés, les résultats temporaires ni le registre de paie. Prévoir une sauvegarde régulière de la base et tester sa restauration.

## 6. Déploiement et sécurité
- Ne publiez pas l'application sur Internet sans HTTPS, stockage persistant, sauvegardes, règles d'accès réseau et revue de sécurité.
- Un système de fichiers cloud éphémère peut effacer la base SQLite au redémarrage. Configurez un volume persistant ou une base de données gérée avant usage réel.
- Ne chargez pas de données confidentielles en production avant d'avoir validé la confidentialité, les sauvegardes, la restauration et les accès.
- Les fichiers Excel/CSV téléversés sont traités temporairement et ne constituent pas une archive pérenne.
- La présence d'une connexion et de rôles ne remplace pas une revue indépendante de sécurité.

## 7. Tests
Depuis le dossier du projet :

```bash
python -m pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

Au moment de la préparation de cette version, les tests automatisés donnent **46 réussites**. L'interface n'a pas pu être démarrée dans l'environnement de construction, car Streamlit n'y était pas installé; il faut donc réaliser un test de lancement et un test fonctionnel dans l'environnement de déploiement.

## 8. Limites fonctionnelles
La V12 n'est pas encore multi-entreprises. Le stock est contrôlé par import de comptage; il n'y a pas encore de mouvements de stock complets, de CMUP/FIFO automatisé, ni de génération automatique d'écritures d'ajustement. La paie reste un outil de préparation qui doit être validé selon les règles applicables au Bénin et à la période concernée.
