# Déploiement — Contrôle comptable SYSCOHADA V12

## Nouveautés V12 : comptes utilisateurs et magasinier
La V12 ajoute une connexion individuelle, des rôles, une base SQLite pour les utilisateurs et les campagnes d’inventaire, ainsi qu’un journal d’audit. Consultez `GUIDE_UTILISATION_V12.md` et `CHANGELOG_V12.md` pour les détails et les limites.

### Démarrage local
```bash
python -m pip install -r requirements.txt
streamlit run app.py
```
Au premier lancement, créez un compte Administrateur depuis l'écran de configuration. Aucun identifiant ni mot de passe par défaut n'est fourni.

### Base de données
Par défaut, SQLite est enregistré dans `data/syscohada.sqlite3`. Définissez `SYSCOHADA_DB_PATH` avant le démarrage pour choisir un chemin protégé et persistant. Sur un hébergement cloud, vérifiez explicitement que le stockage survit aux redémarrages; les systèmes de fichiers éphémères peuvent perdre la base. Sauvegardez-la et testez sa restauration.

### Publication / données réelles
Ne publiez pas l'application sur Internet sans HTTPS, stockage persistant, sauvegardes, contrôle des accès réseau et revue de sécurité. Les fichiers importés et la paie restent temporaires/en mémoire de session. La V12 est mono-entreprise et n'est pas encore un système de production certifié.

### Tests V12
```bash
python -m pytest -q
streamlit run app.py
```
Les tests automatisés de construction : 46 réussites. Streamlit n'était pas installé dans l'environnement de construction, donc le démarrage visuel de l'interface doit être vérifié dans l'environnement cible.

---

# Déployer l'application Contrôle comptable SYSCOHADA

## Ce que vous obtenez
Application web Python/Streamlit utilisable depuis un navigateur sur ordinateur, Android et iPhone. Le téléphone n'a pas besoin d'installer une application native, mais une connexion Internet est nécessaire une fois l'application hébergée.

## Avant publication
- Les contrôles sont des alertes indicatives et ne certifient pas à eux seuls la conformité SYSCOHADA.
- Ne téléversez pas de véritables données comptables confidentielles avant d'avoir choisi un hébergement et des protections adaptés.
- Pour une utilisation par plusieurs personnes, il faudra ajouter une authentification et définir les règles de conservation/suppression des fichiers.

## Déploiement simple via Streamlit Community Cloud
1. Décompressez ce ZIP sur votre ordinateur.
2. Créez un dépôt GitHub et importez les fichiers contenus dans ce dossier à la racine du dépôt : `app.py`, `controles.py`, `requirements.txt`, `.streamlit/`, etc.
3. Connectez-vous à Streamlit Community Cloud avec votre compte GitHub.
4. Choisissez **Create app** puis le dépôt, la branche et le fichier principal `app.py`.
5. Lancez le déploiement. Une fois terminé, copiez le lien HTTPS fourni et ouvrez-le sur ordinateur, Android ou iPhone.
6. Testez avec `exemple_ecritures.xlsx` avant de charger des données réelles.

## Tests locaux
Installez Python 3.10 ou plus récent, puis exécutez dans ce dossier :

```bash
python -m pip install -r requirements.txt
python -m pytest -q test_controles.py
streamlit run app.py
```

## Référentiel de comptes
L'application accepte un référentiel Excel contenant une colonne obligatoire `compte`. Les colonnes `libelle`, `classe`, `niveau`, `source` et `applicable` peuvent servir à documenter le référentiel. L'interface permet de télécharger un modèle vierge.

Le modèle ne contient volontairement aucune liste de comptes présentée comme officielle. Importez uniquement une liste vérifiée, applicable à votre périmètre, et renseignez sa source/version. La comparaison indique qu'un compte est absent du fichier importé ; elle ne suffit pas, à elle seule, à certifier la conformité réglementaire.

## Format attendu
Le fichier Excel doit contenir les colonnes `date`, `piece`, `libelle`, `compte`, `debit`, `credit`. La colonne `journal` est facultative. Formats pris en charge : `.xlsx`, `.xlsm` et `.csv`. Pour les CSV, le séparateur courant (point-virgule, virgule, tabulation ou barre verticale) est détecté automatiquement.

## Formats comptables pris en charge dans cette version
- **Écritures comptables** : colonnes date, pièce, libellé, compte, débit, crédit ; le journal est facultatif. Plusieurs intitulés usuels français sont reconnus automatiquement.
- **Grand livre** : peut être contrôlé avec le même moteur si l'export contient les colonnes de détail d'écriture (date, pièce, libellé, compte, débit, crédit).
- **Balance générale** : compte + débit/crédit ou compte + solde débiteur/solde créditeur. Le programme contrôle notamment l'équilibre arithmétique des totaux, les montants non numériques, les valeurs négatives et les comptes répétés.

Ces contrôles restent des contrôles de premier niveau. Ils ne valident pas automatiquement les règles métier propres à chaque logiciel comptable, les soldes d'ouverture/clôture ou la conformité de tous les états financiers. Testez chaque format avec un export anonymisé avant usage professionnel.

## Version 4 — priorisation des risques

Le rapport de contrôle ajoute désormais les champs `score_risque` (0 à 100), `niveau_risque` (Élevé, Modéré ou Faible) et `justification_risque`. Le score sert uniquement à trier les alertes selon des règles explicables, fondées sur la nature du contrôle, la gravité initiale et un facteur de montant plafonné. Il ne représente ni une probabilité d'erreur/fraude, ni une conclusion d'audit, ni un seuil de matérialité réglementaire. Toute alerte doit être examinée au regard des pièces justificatives et du contexte.

## Version 5 — rapprochement grand livre / balance
La V5 ajoute un module séparé de rapprochement des mouvements débit/crédit par compte. Importez le grand livre et une balance qui présente les mouvements de la même période et du même périmètre, puis confirmez ce point dans l'interface. Les balances qui ne contiennent que les soldes débiteurs/créditeurs sont refusées pour ce contrôle, car elles ne peuvent pas être comparées directement aux mouvements sans tenir compte des à-nouveaux.

Consultez `ROADMAP_FONCTIONNALITES.md` pour le catalogue des fonctionnalités possibles et l'ordre recommandé de réalisation.


## Version 6 — import CSV
La V6 ajoute la lecture des fichiers CSV pour les écritures et les rapprochements grand livre/balance. Le lecteur tente de détecter le séparateur et accepte les encodages UTF-8 avec BOM et Windows-1252/Latin-1. Les intitulés courants sont ensuite harmonisés. Vérifiez toujours l'aperçu et les totaux après import : un fichier CSV mal formé ou des colonnes ambiguës peuvent nécessiter une préparation manuelle. Le référentiel de comptes reste au format Excel dans cette version.


## Version 7 — correction du rapprochement CSV
Le module de rapprochement grand livre/balance lit désormais correctement chaque fichier selon son extension : CSV ou Excel. Les deux fichiers peuvent être de formats différents. Pour un CSV, aucun choix de feuille Excel n'est demandé.


### Nouveautés V8
La V8 affiche un diagnostic de qualité des données à l’importation et un aperçu des lignes brutes avant les contrôles. Les constats sont indicatifs et ne remplacent pas la revue du professionnel.


## V10 — Gestion des stocks physiques

Le module d’inventaire physique permet d’importer un comptage au format Excel/CSV, de comparer les quantités théoriques et physiques, de repérer les écarts, les stocks négatifs, les codes incomplets et les clés article/emplacement/lot dupliquées. Il estime l’écart valorisé si un coût unitaire est fourni. Aucun ajustement de stock n’est comptabilisé automatiquement.

Colonnes obligatoires : `code_article`, `stock_theorique`, `stock_physique`. Colonnes recommandées : `designation`, `emplacement`, `lot`, `cout_unitaire`. Un modèle Excel est téléchargeable depuis l’interface.

Limites : cette version ne remplace pas une gestion de mouvements de stock en temps réel, ne calcule pas automatiquement le CMUP/FIFO et ne génère pas d’écriture comptable d’ajustement. Ces fonctions devront être développées et validées séparément.

## Version 11 — Paie, factures/TVA et suivi des inventaires

La V11 conserve les contrôles comptables V10 et ajoute :

- **Paie multi-profils** : mensualisés, journaliers, horaires, cadres/dirigeants salariés, commerciaux à commissions et temporaires. Prise en compte des primes, indemnités, heures supplémentaires saisies en montant, avantages en nature, rappels/régularisations positifs ou négatifs et retenues.
- **Bulletins PDF** individuels et registre de paie exportable en Excel.
- **Paramètres de paie ajustables** : pension salarié/employeur, prestations familiales, risques professionnels et retenue fiscale. Les taux préremplis de pension/prestations/risques sont des valeurs de départ issues de la documentation CNSS; le taux risques dépend de l'activité. Les paramètres doivent être vérifiés pour l'entreprise et la période.
- **Contrôle de factures/TVA** : modèle Excel, présence de champs IFU/NIM/code électronique, cohérence HT + TVA = TTC, contrôle indicatif de la TVA au taux saisi et détection des numéros répétés.
- **Suivi de campagnes d'inventaire** : nom/date/entrepôt/responsables, deuxième comptage, état, justifications et historique exportable en Excel. Le rapprochement quantitatif V10 reste disponible.

### Limites importantes de la V11

1. Le calcul de paie est un **outil de préparation** et non un moteur légal certifié : les plafonds, bases exactes, exonérations, avantages en nature, abattements, barèmes fiscaux et conventions collectives doivent être configurés/vérifiés. Le barème progressif affiché est indicatif; la retenue forfaitaire est à zéro par défaut.
2. Le bulletin PDF est un modèle de travail et doit être vérifié au regard des mentions et formats réglementaires applicables avant remise aux salariés.
3. La présence d'un IFU, d'un NIM ou d'un code électronique dans un fichier ne confirme pas l'authenticité d'une facture. Aucune vérification directe de la plateforme fiscale n'est réalisée.
4. Les registres de paie et l'historique des campagnes sont conservés en mémoire de session et non dans une base de données durable. Exportez les fichiers avant de fermer la session.
5. Cette version n'a pas encore d'authentification, de rôles appliqués côté serveur, de sauvegarde sécurisée, de chiffrement des données métier ni de journal d'audit persistant. Ne l'utilisez pas encore comme système de paie/stock de production avec données confidentielles.

### Tests

Depuis ce dossier :

```bash
python -m pip install -r requirements.txt
python -m pytest -q
streamlit run app.py
```

Pour la paie béninoise, consultez les sources officielles :
- CNSS Bénin, procédure et taux de cotisation : https://cnss.bj/procedure-de-recouvrement-des-cotisations/
- CNSS Bénin, déclaration et périodicité : https://cnss.bj/declaration-et-paiement/
- Code général des impôts 2026 : https://api.impots.bj/media/6984ebbbb7bc0_B%C3%A9nin-Code%20G%C3%A9n%C3%A9ral%20des%20Imp%C3%B4ts%202026.pdf
- Code du travail du Bénin, notamment la remise du bulletin de paie : https://www.travail.gouv.bj/storage/docs/2301191453-451pdf.pdf
