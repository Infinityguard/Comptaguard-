"""Gestion simple et traçable des consommables/fournitures avec valorisation CMUP."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
import sqlite3
from security_db import connect, log_event

QTY = Decimal('0.001')
MONEY = Decimal('0.01')


def D(value, label='Valeur'):
    try:
        out = Decimal(str(value).replace(' ', '').replace(',', '.'))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f'{label} invalide.')
    if not out.is_finite():
        raise ValueError(f'{label} invalide.')
    return out


def init_stock_db():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS stock_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE COLLATE NOCASE,
            designation TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Fournitures',
            unit TEXT NOT NULL DEFAULT 'unité',
            warehouse TEXT NOT NULL DEFAULT 'Magasin principal',
            location_zone TEXT NOT NULL DEFAULT '',
            location_shelf TEXT NOT NULL DEFAULT '',
            location_bin TEXT NOT NULL DEFAULT '',
            min_qty TEXT NOT NULL DEFAULT '0',
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS stock_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES stock_items(id),
            movement_date TEXT NOT NULL,
            warehouse TEXT NOT NULL DEFAULT 'Magasin principal',
            location_zone TEXT NOT NULL DEFAULT '',
            location_shelf TEXT NOT NULL DEFAULT '',
            location_bin TEXT NOT NULL DEFAULT '',
            movement_type TEXT NOT NULL CHECK(movement_type IN ('Entrée','Sortie','Ajustement +','Ajustement -')),
            quantity TEXT NOT NULL,
            unit_cost TEXT NOT NULL,
            total_cost TEXT NOT NULL,
            cmup_after TEXT NOT NULL,
            qty_after TEXT NOT NULL,
            reference TEXT NOT NULL DEFAULT '',
            beneficiary TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_stock_movements_item ON stock_movements(item_id, id);
        CREATE TRIGGER IF NOT EXISTS stock_movements_no_update BEFORE UPDATE ON stock_movements
        BEGIN SELECT RAISE(ABORT, 'Les mouvements de stock sont immuables; enregistrer une contre-opération.'); END;
        CREATE TRIGGER IF NOT EXISTS stock_movements_no_delete BEFORE DELETE ON stock_movements
        BEGIN SELECT RAISE(ABORT, 'Les mouvements de stock ne peuvent pas être supprimés.'); END;
        ''')
        # Migration non destructive des bases créées avec les versions précédentes.
        item_cols = {r['name'] for r in con.execute('PRAGMA table_info(stock_items)').fetchall()}
        for col in ('location_zone', 'location_shelf', 'location_bin'):
            if col not in item_cols:
                con.execute(f"ALTER TABLE stock_items ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
        movement_cols = {r['name'] for r in con.execute('PRAGMA table_info(stock_movements)').fetchall()}
        warehouse_added = 'warehouse' not in movement_cols
        for col, default in (('warehouse', 'Magasin principal'), ('location_zone', ''), ('location_shelf', ''), ('location_bin', '')):
            if col not in movement_cols:
                con.execute(f"ALTER TABLE stock_movements ADD COLUMN {col} TEXT NOT NULL DEFAULT '{default}'")
        # Backfill uniquement lors de la migration d'un ancien schéma sans colonne warehouse.
        # Ne jamais réécrire les mouvements à chaque démarrage : ils constituent un journal immuable.
        if warehouse_added:
            con.execute('DROP TRIGGER IF EXISTS stock_movements_no_update')
            con.execute("UPDATE stock_movements SET warehouse=(SELECT warehouse FROM stock_items WHERE stock_items.id=stock_movements.item_id)")
            con.execute("CREATE TRIGGER IF NOT EXISTS stock_movements_no_update BEFORE UPDATE ON stock_movements BEGIN SELECT RAISE(ABORT, 'Les mouvements de stock sont immuables; enregistrer une contre-opération.'); END")


def create_item(data, actor):
    code = str(data.get('code','')).strip().upper()
    designation = str(data.get('designation','')).strip()
    category = str(data.get('category','Fournitures')).strip()
    unit = str(data.get('unit','unité')).strip()
    warehouse = str(data.get('warehouse','Magasin principal')).strip()
    location_zone = str(data.get('location_zone','')).strip()
    location_shelf = str(data.get('location_shelf','')).strip()
    location_bin = str(data.get('location_bin','')).strip()
    minimum = D(data.get('min_qty', 0), 'Stock minimum')
    if not code or not designation or not unit or not warehouse:
        raise ValueError('Code, désignation, unité et magasin sont obligatoires.')
    if minimum < 0: raise ValueError('Le stock minimum ne peut pas être négatif.')
    now = datetime.now().isoformat(timespec='seconds')
    with connect() as con:
        con.execute('INSERT INTO stock_items(code,designation,category,unit,warehouse,location_zone,location_shelf,location_bin,min_qty,created_by,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (code, designation, category, unit, warehouse, location_zone, location_shelf, location_bin, str(minimum), actor, now))
    log_event(actor, 'CREATION_ARTICLE_STOCK', f'Code={code}; désignation={designation}; magasin={warehouse}')


def list_items(active_only=False):
    with connect() as con:
        sql = 'SELECT * FROM stock_items' + (' WHERE active=1' if active_only else '') + ' ORDER BY code'
        return [dict(r) for r in con.execute(sql).fetchall()]


def get_balance(item_id, warehouse=None, location_zone=None, location_shelf=None, location_bin=None):
    with connect() as con:
        sql = 'SELECT * FROM stock_movements WHERE item_id=?'; args = [int(item_id)]
        for col, val in (('warehouse', warehouse), ('location_zone', location_zone), ('location_shelf', location_shelf), ('location_bin', location_bin)):
            if val is not None:
                sql += f' AND {col}=?'; args.append(str(val))
        rows = con.execute(sql + ' ORDER BY id', args).fetchall()
    qty = Decimal('0'); value = Decimal('0'); cmup = Decimal('0')
    for r in rows:
        q, cost = D(r['quantity']), D(r['unit_cost'])
        if r['movement_type'] in ('Entrée','Ajustement +'):
            value += q * cost
            qty += q
            cmup = value / qty if qty else Decimal('0')
        else:
            qty -= q
            value -= q * cmup
            if qty == 0: value = Decimal('0'); cmup = Decimal('0')
            elif qty > 0: cmup = value / qty
    return {'quantity': qty, 'value': value.quantize(MONEY, rounding=ROUND_HALF_UP), 'cmup': cmup.quantize(MONEY, rounding=ROUND_HALF_UP)}


def stock_summary():
    items = list_items(active_only=True)
    result=[]
    with connect() as con:
        locations = con.execute("SELECT DISTINCT item_id,warehouse,location_zone,location_shelf,location_bin FROM stock_movements").fetchall()
    by_item = {}
    for loc in locations:
        by_item.setdefault(int(loc['item_id']), set()).add((loc['warehouse'],loc['location_zone'],loc['location_shelf'],loc['location_bin']))
    for item in items:
        default = (item['warehouse'], item.get('location_zone',''), item.get('location_shelf',''), item.get('location_bin',''))
        positions = by_item.get(int(item['id']), set()) | {default}
        for wh, zone, shelf, bin_code in sorted(positions):
            bal=get_balance(item['id'], wh, zone, shelf, bin_code)
            # Les emplacements sans stock ne sont affichés que s'il s'agit de l'emplacement principal de la fiche.
            if bal['quantity'] == 0 and (wh,zone,shelf,bin_code) != default:
                continue
            result.append({**item, 'warehouse': wh, 'location_zone': zone, 'location_shelf': shelf, 'location_bin': bin_code,
                'stock_disponible': str(bal['quantity']), 'cmup': str(bal['cmup']), 'valeur_stock': str(bal['value']),
                'alerte': 'Stock bas' if bal['quantity'] <= D(item['min_qty']) else 'OK'})
    return result


def register_movement(item_id, movement_type, quantity, unit_cost, actor, movement_date=None, reference='', beneficiary='', reason='', warehouse=None, location_zone=None, location_shelf=None, location_bin=None):
    q = D(quantity, 'Quantité'); cost = D(unit_cost, 'Coût unitaire')
    if q <= 0: raise ValueError('La quantité doit être strictement positive.')
    if cost < 0: raise ValueError('Le coût unitaire ne peut pas être négatif.')
    if movement_type not in ('Entrée','Sortie','Ajustement +','Ajustement -'):
        raise ValueError('Type de mouvement non reconnu.')
    if movement_type in ('Sortie','Ajustement -') and cost != 0:
        # Pour une sortie, le coût est déterminé automatiquement par le CMUP actuel.
        cost = Decimal('0')
    now = datetime.now().isoformat(timespec='seconds')
    with connect() as con:
        item = con.execute('SELECT * FROM stock_items WHERE id=? AND active=1', (int(item_id),)).fetchone()
        if item is None: raise ValueError('Article inexistant ou inactif.')
        wh = str(warehouse if warehouse is not None else item['warehouse'])
        zone = str(location_zone if location_zone is not None else item['location_zone'])
        shelf = str(location_shelf if location_shelf is not None else item['location_shelf'])
        bin_code = str(location_bin if location_bin is not None else item['location_bin'])
        # Solde calculé par article et emplacement pour empêcher les sorties négatives locales.
        rows = con.execute('SELECT * FROM stock_movements WHERE item_id=? AND warehouse=? AND location_zone=? AND location_shelf=? AND location_bin=? ORDER BY id', (int(item_id), wh, zone, shelf, bin_code)).fetchall()
        qty = Decimal('0'); value = Decimal('0'); cmup = Decimal('0')
        for r in rows:
            rq, rc = D(r['quantity']), D(r['unit_cost'])
            if r['movement_type'] in ('Entrée','Ajustement +'):
                value += rq * rc; qty += rq; cmup = value / qty if qty else Decimal('0')
            else:
                qty -= rq; value -= rq * cmup
                if qty == 0: value=Decimal('0'); cmup=Decimal('0')
                elif qty > 0: cmup=value/qty
        if movement_type in ('Sortie','Ajustement -'):
            if q > qty: raise ValueError(f'Sortie impossible : stock disponible {qty} {item["unit"]}.')
            cost = cmup
            qty_after = qty - q
            value_after = value - q * cmup
        else:
            qty_after = qty + q
            value_after = value + q * cost
        cmup_after = value_after / qty_after if qty_after else Decimal('0')
        total = q * cost
        con.execute('''INSERT INTO stock_movements(item_id,movement_date,warehouse,location_zone,location_shelf,location_bin,movement_type,quantity,unit_cost,total_cost,cmup_after,qty_after,reference,beneficiary,reason,created_by,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (int(item_id), movement_date or datetime.now().date().isoformat(), wh, zone, shelf, bin_code, movement_type,
            str(q), str(cost), str(total.quantize(MONEY, rounding=ROUND_HALF_UP)), str(cmup_after.quantize(MONEY, rounding=ROUND_HALF_UP)),
            str(qty_after), str(reference).strip()[:200], str(beneficiary).strip()[:200], str(reason).strip()[:1000], actor, now))
    log_event(actor, 'MOUVEMENT_STOCK', f'article={item["code"]}; type={movement_type}; quantité={q}; coût={cost}; référence={reference}')



def _balance_from_rows(rows):
    qty = Decimal('0'); value = Decimal('0'); cmup = Decimal('0')
    for r in rows:
        q, cost = D(r['quantity']), D(r['unit_cost'])
        if r['movement_type'] in ('Entrée', 'Ajustement +'):
            value += q * cost
            qty += q
            cmup = value / qty if qty else Decimal('0')
        else:
            qty -= q
            value -= q * cmup
            if qty == 0:
                value = Decimal('0'); cmup = Decimal('0')
            elif qty > 0:
                cmup = value / qty
    return {'quantity': qty, 'value': value, 'cmup': cmup}


def transfer_stock(item_id, quantity, actor, source, destination, movement_date=None, reference='', reason=''):
    """Transfert entre emplacements, atomique : sortie et entrée validées ensemble."""
    fields = ('warehouse', 'location_zone', 'location_shelf', 'location_bin')
    src = {k: str(source.get(k, '')).strip() for k in fields}
    dst = {k: str(destination.get(k, '')).strip() for k in fields}
    if src == dst:
        raise ValueError('La source et la destination doivent être différentes.')
    if not src['warehouse'] or not dst['warehouse']:
        raise ValueError('Le magasin source et le magasin destination sont obligatoires.')
    q = D(quantity, 'Quantité')
    if q <= 0:
        raise ValueError('La quantité doit être strictement positive.')
    transfer_reason = str(reason).strip()
    if not transfer_reason:
        raise ValueError('Le motif du transfert est obligatoire.')
    transfer_ref = str(reference).strip() or 'TRANSFERT'
    date = movement_date or datetime.now().date().isoformat()
    now = datetime.now().isoformat(timespec='seconds')
    with connect() as con:
        item = con.execute('SELECT * FROM stock_items WHERE id=? AND active=1', (int(item_id),)).fetchone()
        if item is None:
            raise ValueError('Article inexistant ou inactif.')
        def rows_for(loc):
            sql = ('SELECT * FROM stock_movements WHERE item_id=? AND warehouse=? AND location_zone=? '
                   'AND location_shelf=? AND location_bin=? ORDER BY id')
            return con.execute(sql, (int(item_id), loc['warehouse'], loc['location_zone'], loc['location_shelf'], loc['location_bin'])).fetchall()
        src_bal = _balance_from_rows(rows_for(src))
        if q > src_bal['quantity']:
            raise ValueError(f'Transfert impossible : stock disponible à la source {src_bal["quantity"]}.')
        dst_bal = _balance_from_rows(rows_for(dst))
        unit_cost = src_bal['cmup']
        src_qty_after = src_bal['quantity'] - q
        src_value_after = src_bal['value'] - q * unit_cost
        src_cmup_after = src_value_after / src_qty_after if src_qty_after else Decimal('0')
        dst_qty_after = dst_bal['quantity'] + q
        dst_value_after = dst_bal['value'] + q * unit_cost
        dst_cmup_after = dst_value_after / dst_qty_after if dst_qty_after else Decimal('0')
        total = (q * unit_cost).quantize(MONEY, rounding=ROUND_HALF_UP)
        sql = ('INSERT INTO stock_movements(item_id,movement_date,warehouse,location_zone,location_shelf,location_bin,'
               'movement_type,quantity,unit_cost,total_cost,cmup_after,qty_after,reference,beneficiary,reason,created_by,created_at) '
               'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)')
        common = (int(item_id), date)
        con.execute(sql, (*common, src['warehouse'], src['location_zone'], src['location_shelf'], src['location_bin'],
            'Sortie', str(q), str(unit_cost), str(total), str(src_cmup_after.quantize(MONEY, rounding=ROUND_HALF_UP)),
            str(src_qty_after), (transfer_ref + '-SORTIE')[:200], 'Transfert interne',
            ('Transfert vers ' + ' / '.join(dst.values()) + ' — ' + transfer_reason)[:1000], actor, now))
        con.execute(sql, (*common, dst['warehouse'], dst['location_zone'], dst['location_shelf'], dst['location_bin'],
            'Entrée', str(q), str(unit_cost), str(total), str(dst_cmup_after.quantize(MONEY, rounding=ROUND_HALF_UP)),
            str(dst_qty_after), (transfer_ref + '-ENTREE')[:200], 'Transfert interne',
            ('Transfert depuis ' + ' / '.join(src.values()) + ' — ' + transfer_reason)[:1000], actor, now))
    log_event(actor, 'TRANSFERT_STOCK', f'article={item["code"]}; quantité={q}; source={src}; destination={dst}; référence={transfer_ref}')


def list_movements(item_id=None, limit=1000):
    with connect() as con:
        sql = '''SELECT m.id,m.movement_date,m.warehouse,m.location_zone,m.location_shelf,m.location_bin,i.code,i.designation,m.movement_type,m.quantity,i.unit,m.unit_cost,m.total_cost,m.cmup_after,m.qty_after,m.reference,m.beneficiary,m.reason,m.created_by,m.created_at
                 FROM stock_movements m JOIN stock_items i ON i.id=m.item_id'''
        args=[]
        if item_id is not None: sql += ' WHERE m.item_id=?'; args.append(int(item_id))
        sql += ' ORDER BY m.id DESC LIMIT ?'; args.append(int(limit))
        return [dict(r) for r in con.execute(sql,args).fetchall()]
