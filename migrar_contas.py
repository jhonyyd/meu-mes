import json
from datetime import datetime
from getpass import getpass
from pathlib import Path
from autenticacao import MySQLAuth, validate_account


def prepare_accounts(connect, root):
    with connect() as lock_con, lock_con.cursor() as lock_cursor:
        lock_cursor.execute("SELECT GET_LOCK('controle_despesas_contas_v1', 10) AS obtido")
        if lock_cursor.fetchone()['obtido'] != 1:
            raise RuntimeError('Outra atualização está em andamento. Feche o outro servidor e tente novamente.')
        try:
            _prepare(connect, root)
        finally:
            lock_cursor.execute("SELECT RELEASE_LOCK('controle_despesas_contas_v1')")


def _prepare(connect, root):
    with connect() as con, con.cursor() as cur:
        cur.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='despesas' AND COLUMN_NAME='usuario_id'")
        migrated = cur.fetchone() is not None
        if not migrated:
            backup = {}
            for table in ('despesas', 'categorias', 'formas_pagamento', 'orcamentos_mensais'):
                cur.execute(f'SELECT * FROM {table}')
                backup[table] = cur.fetchall()
            destination = Path(root) / 'backups'
            destination.mkdir(exist_ok=True)
            path = destination / f'antes-contas-{datetime.now():%Y%m%d-%H%M%S-%f}.json'
            with path.open('x', encoding='utf-8') as file:
                json.dump(backup, file, default=str, ensure_ascii=False, indent=2)
            print(f'Cópia dos dados antigos salva em: {path}')
        cur.execute('''CREATE TABLE IF NOT EXISTS usuarios (
            id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
            nome VARCHAR(80) NOT NULL,
            email VARCHAR(254) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL UNIQUE,
            senha_hash VARCHAR(255) NOT NULL,
            criado_em TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB''')
        cur.execute('''CREATE TABLE IF NOT EXISTS sessoes (
            token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL PRIMARY KEY,
            usuario_id BIGINT UNSIGNED NOT NULL,
            expira_em DATETIME NOT NULL,
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            INDEX ix_sessao_expiracao (expira_em)
        ) ENGINE=InnoDB''')
        if not migrated:
            cur.execute('ALTER TABLE despesas ADD COLUMN usuario_id BIGINT UNSIGNED NULL, ADD INDEX ix_despesa_usuario_data (usuario_id, data_despesa), ADD CONSTRAINT fk_despesa_usuario FOREIGN KEY (usuario_id) REFERENCES usuarios(id)')
        cur.execute('''CREATE TABLE IF NOT EXISTS orcamentos_usuarios (
            usuario_id BIGINT UNSIGNED NOT NULL,
            ano SMALLINT UNSIGNED NOT NULL,
            mes TINYINT UNSIGNED NOT NULL,
            valor DECIMAL(12,2) NOT NULL,
            PRIMARY KEY (usuario_id, ano, mes),
            FOREIGN KEY (usuario_id) REFERENCES usuarios(id),
            CHECK (mes BETWEEN 1 AND 12), CHECK (valor >= 0)
        ) ENGINE=InnoDB''')
        con.commit()
    auth = MySQLAuth(connect)
    if auth.count_users() == 0:
        print('\nCrie SUA conta inicial. Os lançamentos antigos serão vinculados a ela.')
        print('As demais pessoas poderão criar suas contas pelo navegador, começando sem despesas.')
        while True:
            name = input('Seu nome: ').strip()
            email = input('Seu e-mail para login: ').strip()
            password = getpass('Crie sua senha do aplicativo (mínimo 12 caracteres): ')
            confirmation = getpass('Repita a senha do aplicativo: ')
            try:
                if password != confirmation:
                    raise ValueError('As senhas não coincidem.')
                validate_account(name, email, password)
                auth.register(name, email, password, claim_legacy=True)
                print('Sua conta foi criada. Dados antigos preservados e vinculados a ela.\n')
                break
            except ValueError as error:
                print(error)
