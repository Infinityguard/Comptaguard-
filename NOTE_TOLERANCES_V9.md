# V10 — Gestion des tolérances et de leur accumulation

## Principes
- Les montants sont comparés avec `Decimal` dans le nouveau module `tolerance.py`.
- Le seuil par pièce est configurable dans l'interface (valeur initiale : 0,001 FCFA).
- Les écarts sources ne sont jamais modifiés ou supprimés par application de la tolérance.
- Le rapport montre l'écart signé net et la somme des écarts absolus : les compensations ne masquent donc pas l'existence d'écarts.
- Un détail par pièce peut être consulté et exporté en CSV.
- La tolérance de 0,001 est un seuil technique initial, pas une règle fiscale ou comptable universelle. Elle doit être adaptée à la devise, au format des données et aux règles validées du contrôle concerné.

## Périmètre actuel
Cette version applique le suivi détaillé à l'équilibre des pièces comptables et fournit des fonctions réutilisables pour comparer et cumuler des écarts monétaires. Les contrôles spécifiques de TVA et d'AIB restent à développer séparément, avec leurs propres règles de base taxable, de taux et d'arrondi validées pour le Bénin.

## Tests
Lancer depuis ce dossier :

```bash
python -m pytest -q
```

Les tests couvrent les contrôles existants ainsi que la comparaison décimale, l'accumulation des écarts et les pièces comptables.
