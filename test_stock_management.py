from decimal import Decimal
import pytest
from security_db import init_db
from stock_management import init_stock_db, create_item, list_items, register_movement, get_balance, list_movements, transfer_stock, stock_summary


def setup_db(tmp_path, monkeypatch):
    monkeypatch.setenv('SYSCOHADA_DB_PATH', str(tmp_path / 'stock_test.sqlite3'))
    init_db(); init_stock_db()


def test_cmup_and_issue_valuation(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'HUILE-01','designation':'Huile moteur','category':'Carburants et lubrifiants','unit':'litre','warehouse':'Magasin','min_qty':2}, 'admin')
    item = list_items()[0]
    register_movement(item['id'], 'Entrée', 10, 100, 'admin', reference='BR-001', reason='Réception fournisseur')
    register_movement(item['id'], 'Entrée', 10, 200, 'admin', reference='BR-002', reason='Deuxième réception')
    before = get_balance(item['id'])
    assert before['quantity'] == Decimal('20')
    assert before['cmup'] == Decimal('150.00')
    register_movement(item['id'], 'Sortie', 5, 0, 'magasinier', reference='BS-001', beneficiary='Atelier', reason='Consommation atelier')
    after = get_balance(item['id'])
    assert after['quantity'] == Decimal('15')
    assert after['cmup'] == Decimal('150.00')
    assert after['value'] == Decimal('2250.00')
    movements = list_movements(item['id'])
    assert movements[0]['unit_cost'] == '150'
    assert movements[0]['total_cost'] == '750.00'


def test_prevent_negative_stock(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'PAP-01','designation':'Papier A4','unit':'ramette','warehouse':'Magasin','min_qty':0}, 'admin')
    item = list_items()[0]
    with pytest.raises(ValueError, match='Sortie impossible'):
        register_movement(item['id'], 'Sortie', 1, 0, 'magasinier', reason='Test')


def test_movement_requires_positive_quantity(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'PAP-01','designation':'Papier A4','unit':'ramette','warehouse':'Magasin','min_qty':0}, 'admin')
    item = list_items()[0]
    with pytest.raises(ValueError, match='strictement positive'):
        register_movement(item['id'], 'Entrée', 0, 100, 'admin', reason='Test')


def test_transfer_tracks_both_locations_and_preserves_total_stock(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'HUILE-02','designation':'Huile','unit':'litre','warehouse':'Magasin A','location_zone':'A','location_shelf':'E1','location_bin':'B1'}, 'admin')
    item = list_items()[0]
    register_movement(item['id'], 'Entrée', 20, 100, 'admin', reason='Réception initiale')
    transfer_stock(item['id'], 7, 'admin',
        {'warehouse':'Magasin A','location_zone':'A','location_shelf':'E1','location_bin':'B1'},
        {'warehouse':'Magasin B','location_zone':'B','location_shelf':'E2','location_bin':'B3'},
        reference='TR-001', reason='Réorganisation')
    assert get_balance(item['id'], 'Magasin A', 'A', 'E1', 'B1')['quantity'] == Decimal('13')
    assert get_balance(item['id'], 'Magasin B', 'B', 'E2', 'B3')['quantity'] == Decimal('7')
    assert get_balance(item['id'])['quantity'] == Decimal('20')
    assert len(list_movements(item['id'])) == 3
    summary = stock_summary()
    assert {r['warehouse'] for r in summary} == {'Magasin A', 'Magasin B'}


def test_transfer_rejects_insufficient_source_stock(tmp_path, monkeypatch):
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'PAP-02','designation':'Papier','unit':'ramette','warehouse':'A'}, 'admin')
    item = list_items()[0]
    with pytest.raises(ValueError, match='Transfert impossible'):
        transfer_stock(item['id'], 1, 'admin', {'warehouse':'A','location_zone':'','location_shelf':'','location_bin':''},
            {'warehouse':'B','location_zone':'','location_shelf':'','location_bin':''}, reason='Test')


def test_transfer_is_atomic_when_destination_insert_fails(tmp_path, monkeypatch):
    from security_db import connect
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'ATOMIC-01','designation':'Article test','unit':'unité','warehouse':'Magasin A'}, 'admin')
    item = list_items()[0]
    register_movement(item['id'], 'Entrée', 10, 100, 'admin', reason='Stock initial',
                      warehouse='Magasin A', location_zone='', location_shelf='', location_bin='')
    with connect() as con:
        con.execute("""CREATE TRIGGER force_transfer_entry_failure BEFORE INSERT ON stock_movements
            WHEN NEW.reference='TR-FAIL-ENTREE'
            BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(Exception, match='test failure'):
        transfer_stock(item['id'], 4, 'admin',
            {'warehouse':'Magasin A','location_zone':'','location_shelf':'','location_bin':''},
            {'warehouse':'Magasin B','location_zone':'','location_shelf':'','location_bin':''},
            reference='TR-FAIL', reason='Test atomicité')
    # La sortie ne doit pas être conservée si l'entrée correspondante échoue.
    assert get_balance(item['id'], 'Magasin A', '', '', '')['quantity'] == Decimal('10')
    assert get_balance(item['id'], 'Magasin B', '', '', '')['quantity'] == Decimal('0')
    assert len(list_movements(item['id'])) == 1


def test_reinitialisation_ne_rewrite_existing_movement_history(tmp_path, monkeypatch):
    from security_db import connect
    setup_db(tmp_path, monkeypatch)
    create_item({'code':'HIST-01','designation':'Article historique','unit':'unité','warehouse':'Magasin A'}, 'admin')
    item = list_items()[0]
    register_movement(item['id'], 'Entrée', 3, 25, 'admin', reason='Réception',
                      warehouse='Magasin principal', location_zone='', location_shelf='', location_bin='')
    with connect() as con:
        before = dict(con.execute('SELECT warehouse, quantity FROM stock_movements').fetchone())
    init_stock_db()
    with connect() as con:
        after = dict(con.execute('SELECT warehouse, quantity FROM stock_movements').fetchone())
    assert after == before
