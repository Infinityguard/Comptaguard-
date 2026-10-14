# Guide rapide — Gestion des stocks Comptaguard

## Périmètre
Le module concerne les matières consommables et fournitures. La valorisation des entrées et sorties repose sur le coût moyen unitaire pondéré (CMUP). L'inventaire physique reste une fonction de contrôle distincte.

## Première utilisation
1. Ouvrir **Stocks → Gestion des stocks**.
2. Dans **Articles**, créer chaque référence avec un code unique, sa désignation, sa catégorie, son unité, son magasin et son seuil minimum.
3. Dans **Mouvement de stock**, enregistrer une **Entrée** initiale correspondant au stock existant, avec son coût unitaire et une référence justificative. Sans mouvement d'entrée, le stock disponible est nul.
4. Enregistrer ensuite chaque réception comme une entrée et chaque consommation comme une sortie. Le motif est obligatoire ; renseigner aussi la référence du bon et le service bénéficiaire lorsque cela s'applique.
5. Consulter **Vue du stock** pour les quantités, le CMUP, la valeur et les alertes. Consulter **Historique** pour retrouver l'auteur et la justification de chaque mouvement.

## Règle CMUP
Après une entrée :

CMUP nouveau = (valeur du stock avant entrée + valeur de l'entrée) / (quantité avant entrée + quantité entrée).

Les sorties sont valorisées au CMUP en vigueur avant la sortie. Le système refuse une sortie supérieure à la quantité disponible.

## Contrôles et limites
- Les mouvements enregistrés sont immuables. En cas d'erreur, saisir une contre-opération justifiée plutôt que modifier l'historique.
- Les ajustements positifs nécessitent un coût unitaire. Les ajustements négatifs sont valorisés au CMUP courant.
- Les écarts d'inventaire physique ne mettent pas automatiquement à jour le stock : ils doivent être examinés et faire l'objet d'un ajustement autorisé.
- Cette version ne génère pas automatiquement les écritures comptables. La valorisation de stock est un registre opérationnel à rapprocher selon les procédures de l'entreprise.
- Avant utilisation en production, configurer un stockage persistant et sécurisé pour la base de données sur l'hébergement.

## Gestion multi-magasins et emplacements (mise à jour)

La fiche article peut préciser le magasin/entrepôt, la zone ou le rayon, l'étagère et le casier. Les mouvements d'entrée, de sortie et d'ajustement conservent aussi leur propre emplacement : un même article peut donc être suivi dans plusieurs magasins ou positions.

L'onglet **Transfert** permet d'indiquer l'emplacement source et l'emplacement destination, la quantité, la date, la référence et le motif. Le système enregistre une sortie à la source et une entrée à destination, au CMUP de la source, afin de conserver la valeur du stock transféré. Les deux opérations sont consultables dans l'historique.

Dans la **Vue du stock**, les quantités et valeurs sont présentées par emplacement. Les anciennes fiches et les mouvements déjà présents sont conservés ; à la migration, les mouvements historiques héritent du magasin enregistré sur leur article.

Avant utilisation en production, tester un cycle complet : réception, sortie, transfert vers un autre magasin, consultation des deux soldes et export de l'historique. Cette fonctionnalité n'émet pas automatiquement d'écritures comptables.
