# Feuille de route — Contrôle comptable SYSCOHADA

## Vision produit
Créer une application web professionnelle, utilisable sur ordinateur et téléphone, qui aide à contrôler les exports comptables SYSCOHADA. Les alertes assistent le professionnel : elles ne remplacent ni son jugement, ni les pièces justificatives, ni une mission d'audit réglementée.

## Fonctionnalités déjà présentes dans la V4
- Import Excel `.xlsx` et `.xlsm`.
- Reconnaissance de plusieurs intitulés de colonnes en français.
- Contrôles de premier niveau des écritures et grands livres : champs manquants, dates, comptes, montants, pièces déséquilibrées, doublons potentiels et montants ronds.
- Contrôles arithmétiques de base de la balance : totaux débit/crédit, valeurs non numériques ou négatives, comptes répétés.
- Import facultatif d'un référentiel de comptes fourni par l'utilisateur et modèle Excel vierge.
- Score de priorisation explicable des alertes et export de rapport Excel/CSV.

## Ajout de la V5
- Rapprochement des mouvements du grand livre et d'une balance de mouvements, compte par compte.
- Affichage des écarts débit, crédit et net, des comptes concordants et des comptes à vérifier.
- Export CSV du rapprochement.
- Garde-fou : refuse de comparer directement des mouvements du grand livre à une balance qui ne contient que des soldes débiteurs/créditeurs, car les à-nouveaux et la période pourraient fausser la comparaison.


## Ajout de la V6
- Import des fichiers CSV pour les écritures et les rapprochements.
- Détection du séparateur courant et prise en charge des encodages UTF-8 avec BOM, Windows-1252 et Latin-1.
- Reconnaissance des alias de colonnes après import CSV.
- Référentiel de comptes maintenu en Excel pour éviter d'étendre le périmètre sans tests supplémentaires.

## Ajout de la V7 (correctif de fiabilité)
- Correction du rapprochement inter-documents pour accepter réellement les fichiers CSV, comme annoncé dans l'interface.
- Lecture distincte CSV/Excel pour le grand livre et la balance, y compris lorsque les deux formats sont mélangés.

## Catalogue des fonctionnalités possibles

### A. Importation et qualité des données
- Excel `.xlsx`/`.xlsm` (actuel), CSV (priorité suivante), puis formats exportés par logiciels comptables selon échantillons réels anonymisés.
- Détection de la feuille, de la ligne d'en-tête et du type de document.
- Assistant de correspondance des colonnes si les intitulés ne sont pas reconnus.
- Contrôle des dates, devises, séparateurs décimaux, comptes conservés comme texte et lignes vides.
- Aperçu avant lancement, rapport d'importation et traçabilité du fichier source.
- Gestion des gros fichiers par traitement en lots et messages d'erreur compréhensibles.

### B. Contrôle des écritures et journaux
- Équilibre par pièce/journal/date avec règles configurables et limites documentées.
- Champs obligatoires, dates hors période, numéros de pièces manquants ou répétés.
- Débits/crédits invalides, ligne avec débit et crédit simultanés, écriture nulle.
- Doublons exacts et quasi-doublons, avec explication des critères et réduction des faux positifs.
- Séquences de pièces, écritures le week-end ou hors horaires si les règles de l'entreprise le demandent.
- Montants inhabituels, seuils de matérialité paramétrables et recherche de contrepassations.
- Analyse des libellés et des écritures manuelles, uniquement comme indicateurs à examiner.

### C. Plan de comptes SYSCOHADA
- Référentiel versionné, source officielle ou vérifiable, date d'effet et périmètre.
- Vérification de l'existence, de la classe, du niveau et du caractère imputable des comptes.
- Distinction entre compte inconnu, compte non imputable et compte simplement absent du référentiel importé.
- Gestion des sous-comptes internes sans prétendre qu'ils sont tous des comptes officiels.
- Aucun plan comptable officiel ne doit être préchargé sans validation de sa provenance et de ses droits d'utilisation.

### D. Balance et grand livre
- Balance des mouvements, balance des soldes et balance comparative N/N-1.
- Vérification des totaux, soldes débiteurs/créditeurs et cohérence des colonnes.
- Calcul du solde d'ouverture, mouvements période et solde de clôture quand les données existent.
- Rapprochement balance ↔ grand livre par compte et période (V5 : premier rapprochement des mouvements).
- Rapprochement du total des journaux avec les mouvements de balance.
- Recherche de comptes sans mouvement, comptes inattendus et variations significatives.
- Contrôle de cohérence entre sous-comptes et comptes collectifs lorsque les niveaux sont identifiables.

### E. Contrôles par cycle comptable (développement ultérieur)
- Trésorerie et banques : rapprochement bancaire si relevé importé, chèques/virements en suspens.
- Clients : ancienneté des créances, soldes créditeurs à examiner, lettrage si données disponibles.
- Fournisseurs : ancienneté des dettes, soldes débiteurs à examiner, factures et paiements si données disponibles.
- Stocks : variations, comptes de stock et écritures d'inventaire, sous réserve des données fournies.
- Immobilisations : mouvements, amortissements et cohérence du fichier immobilisations.
- Paie/fiscalité : contrôles de cohérence seulement avec fichiers et règles explicitement fournis.
- Ces modules nécessitent des sources spécifiques et ne doivent pas tirer de conclusions à partir du seul grand livre.

### F. États financiers et conformité
- Import des états financiers et contrôles croisés entre états, balance et grand livre.
- Contrôles de cohérence des totaux et des renvois, avec règles versionnées par exercice et cadre applicable.
- Génération assistée d'une liste d'écarts à investiguer.
- Ne déclarer la conformité SYSCOHADA complète qu'après validation documentée des règles et jeux de tests.

### G. Risques, anomalies et rapport d'audit
- Score explicable par règle, gravité, montant et contexte, avec paramètres transparents.
- Filtre par risque, compte, journal, période et type d'anomalie.
- Justification, donnée source, numéro de ligne Excel et piste de résolution pour chaque alerte.
- Commentaires du réviseur, statut « à examiner / confirmé / écart justifié / corrigé », responsable et date.
- Rapport Excel puis PDF, synthèse exécutive, limites, périmètre et méthodologie.
- Historique de revue et journal d'activité dans une version multi-utilisateurs sécurisée.
- Le score n'est pas une probabilité de fraude et une alerte n'est pas une preuve.

### H. Produit professionnel et sécurité
- Comptes utilisateurs, rôles (administrateur, préparateur, réviseur, lecture seule) et authentification.
- Cloisonnement strict des dossiers clients et organisations.
- Chiffrement en transit et au repos selon l'hébergement, politique de conservation et suppression des fichiers.
- Pas de données comptables confidentielles sur une instance publique non sécurisée.
- Sauvegardes, journal d'activité, gestion des erreurs, surveillance et procédure de restauration.
- Conditions d'utilisation, politique de confidentialité, support et gestion des incidents.
- Tests automatisés, tests d'intégration, jeux d'essai anonymisés et validation par un comptable SYSCOHADA.

### I. Commercialisation et plateformes
- Application web responsive en premier, avec URL HTTPS.
- Modèle freemium envisagé : limites explicites pour l'offre gratuite et fonctions professionnelles payantes.
- Paiements, facturation, limites d'usage et gestion d'abonnement à développer après validation du produit.
- PWA éventuelle pour installation depuis le navigateur.
- Android/iOS natifs ou enveloppe web uniquement après vérification des règles des boutiques et des besoins hors ligne.
- Version Windows/Mac à envisager si une demande claire existe ; une application web responsive peut couvrir le besoin initial.
- Publication sur les boutiques après tests, comptes développeur, politique de confidentialité et conformité à leurs exigences.

## Ordre de réalisation recommandé
1. **Fiabilité du noyau** : tests sur fichiers anonymisés, cas limites, format des nombres, doublons et équilibre.
2. **Rapprochements** : grand livre/balance, puis journaux/balance, périodes et à-nouveaux.
3. **Importation robuste** : import CSV de base ajouté en V6 ; prochaine amélioration : assistant de colonnes et rapport d'erreurs d'import.
4. **Référentiel SYSCOHADA vérifié** : version, source, dates d'effet et règles de validation.
5. **Rapport professionnel** : piste d'audit, commentaires, statut de revue, export Excel/PDF.
6. **Contrôles par cycle et états financiers** : module par module avec tests métier validés.
7. **Sécurité et multi-utilisateurs** : avant toute utilisation de données clients réelles.
8. **Déploiement web**, puis bêta privée et modèle commercial.
9. **Applications et boutiques** après stabilisation du produit web.

## Conditions avant une commercialisation sérieuse
- Validation des règles par un professionnel compétent en SYSCOHADA.
- Jeux de tests représentatifs et anonymisés, incluant des faux positifs et faux négatifs connus.
- Documentation claire des limites de chaque contrôle.
- Protection des données, gestion des accès et conditions de service en place.
- Pas de promesse de détection exhaustive des erreurs ou de fraude.


## V8 — Qualité des données à l’importation
- Diagnostic préalable des colonnes nécessaires selon le type de document.
- Détection indicative des lignes entièrement vides, montants illisibles, dates non reconnues et comptes manquants.
- Aperçu des données brutes avant les résultats des contrôles.
- Les diagnostics n’altèrent pas les données sources et ne constituent pas une certification comptable.


## V10 — Stocks physiques (premier niveau)
- [x] Import du comptage physique Excel/CSV
- [x] Comparaison stock théorique / stock physique
- [x] Écarts de quantité nets et absolus
- [x] Valorisation indicative avec coût unitaire fourni
- [x] Détection des quantités négatives, données manquantes et clés de stock dupliquées
- [x] Modèle d’inventaire Excel téléchargeable
- [ ] Mouvements de stock en temps réel et fiches de stock
- [ ] Méthodes de valorisation CMUP/FIFO selon le référentiel applicable
- [ ] Gestion des unités, conversions, péremptions et numéros de série
- [ ] Workflow de validation des écarts et génération contrôlée des écritures d’ajustement
