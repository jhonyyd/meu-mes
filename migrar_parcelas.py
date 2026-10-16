from datetime import datetime
import json
from pathlib import Path

COLUMNS = {
    'tipo': "VARCHAR(16) NOT NULL DEFAULT 'unica'",
    'data_pagamento': 'DATE NULL',
    'parcelamento_id': 'CHAR(36) NULL',
    'numero_parcela': 'TINYINT UNSIGNED NOT NULL DEFAULT 1',
    'total_parcelas': 'TINYINT UNSIGNED NOT NULL DEFAULT 1',
    'versao': 'INT UNSIGNED NOT NULL DEFAULT 1',
}


def prepare_installments(connect, root):
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT GET_LOCK('controle_despesas_parcelas_v1', 10) AS obtido")
        if cur.fetchone()['obtido'] != 1:
            raise RuntimeError('Outra atualização de parcelas está em andamento.')
        try:
            cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='despesas'")
            existing = {row['COLUMN_NAME'] for row in cur.fetchall()}
            missing = {name: definition for name, definition in COLUMNS.items() if name not in existing}
            if missing:
                cur.execute('SELECT * FROM despesas')
                previous = cur.fetchall()
                folder = Path(root) / 'backups'
                folder.mkdir(exist_ok=True)
                path = folder / f'antes-parcelas-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
                with path.open('x', encoding='utf-8') as file:
                    json.dump({'despesas': previous}, file, default=str, ensure_ascii=False, indent=2)
                print(f'Cópia anterior ao controle de parcelas salva em: {path}')
                additions = ', '.join(f'ADD COLUMN {name} {definition}' for name, definition in missing.items())
                cur.execute('ALTER TABLE despesas ' + additions)
                print('Despesas antigas preservadas como em aberto; confirme o pagamento de cada uma pela edição.')
            cur.execute("SELECT INDEX_NAME FROM information_schema.STATISTICS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='despesas' AND INDEX_NAME='ix_despesa_aberta'")
            if not cur.fetchone():
                cur.execute('CREATE INDEX ix_despesa_aberta ON despesas (usuario_id, data_pagamento, data_despesa)')
            con.commit()
        finally:
            cur.execute("SELECT RELEASE_LOCK('controle_despesas_parcelas_v1')")
