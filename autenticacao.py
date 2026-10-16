from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets
import threading
import time

import pymysql

ITERATIONS = 600_000
SESSION_DAYS = 7


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def password_hash(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return f'pbkdf2_sha256${ITERATIONS}${salt}${digest}'


def password_matches(password, encoded):
    try:
        algorithm, iterations, salt, expected = encoded.split('$')
        if algorithm != 'pbkdf2_sha256' or not 100_000 <= int(iterations) <= 2_000_000:
            return False
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def normalize_email(email):
    value = str(email).strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[^@\s]{1,64}@[^@\s.]+(?:\.[^@\s.]+)+', value):
        raise ValueError('Informe um e-mail válido.')
    return value


def validate_account(name, email, password):
    if not isinstance(name, str) or not isinstance(email, str):
        raise ValueError('Informe nome e e-mail válidos.')
    name = str(name).strip()
    if not 1 <= len(name) <= 80:
        raise ValueError('Informe um nome com até 80 caracteres.')
    email = normalize_email(email)
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError('Use uma senha com 12 a 128 caracteres. Pode ser uma frase.')
    return name, email, password


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class RateLimiter:

    def __init__(self):
        self.entries = {}
        self.lock = threading.Lock()

    def check(self, ip, email, action):
        current = time.monotonic()
        with self.lock:
            self.entries = {key: value for key, value in self.entries.items() if value[0] > current - 900}
            keys = [(action, 'ip', ip), (action, 'email', token_hash(email))]
            limits = [30 if action == 'login' else 10, 8 if action == 'login' else 5]
            if any(self.entries.get(key, (current, 0))[1] >= limit for key, limit in zip(keys, limits)):
                return False
            for key in keys:
                since, count = self.entries.get(key, (current, 0))
                self.entries[key] = (since, count + 1)
            return True


class MySQLAuth:
    def __init__(self, connect):
        self.connect = connect
        self.dummy_hash = password_hash('conta-inexistente-' + secrets.token_hex(16))

    def count_users(self):
        with self.connect() as con, con.cursor() as cur:
            cur.execute('SELECT COUNT(*) AS quantidade FROM usuarios')
            return cur.fetchone()['quantidade']

    def register(self, name, email, password, claim_legacy=False):
        name, email, password = validate_account(name, email, password)
        encoded = password_hash(password)
        with self.connect() as con:
            try:
                with con.cursor() as cur:
                    if claim_legacy:

                        cur.execute('SELECT COUNT(*) AS quantidade FROM usuarios')
                        if cur.fetchone()['quantidade']:
                            raise ValueError('A conta inicial já existe. Entre pelo navegador.')
                    cur.execute('INSERT INTO usuarios (nome, email, senha_hash) VALUES (%s,%s,%s)', (name, email, encoded))
                    user_id = cur.lastrowid
                    if claim_legacy:
                        cur.execute('UPDATE despesas SET usuario_id=%s WHERE usuario_id IS NULL', (user_id,))
                        cur.execute('INSERT INTO orcamentos_usuarios (usuario_id, ano, mes, valor) SELECT %s, ano, mes, valor FROM orcamentos_mensais', (user_id,))
                con.commit()
            except pymysql.IntegrityError as error:
                con.rollback()
                if error.args[0] == 1062:
                    raise ValueError('Não foi possível criar esta conta. Tente outro e-mail ou entre na conta existente.') from error
                raise
            except Exception:
                con.rollback()
                raise
        return {'id': user_id, 'nome': name, 'email': email}

    def login(self, email, password):
        try:
            email = normalize_email(email)
        except ValueError:
            email = ''
        with self.connect() as con, con.cursor() as cur:
            cur.execute('SELECT id, nome, email, senha_hash FROM usuarios WHERE email=%s', (email,))
            user = cur.fetchone()
        valid = password_matches(password, user['senha_hash'] if user else self.dummy_hash)
        if not user or not valid:
            return None
        return {key: user[key] for key in ('id', 'nome', 'email')}

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute('DELETE FROM sessoes WHERE expira_em <= %s', (now_utc(),))
                cur.execute('INSERT INTO sessoes (token_hash, usuario_id, expira_em) VALUES (%s,%s,%s)',
                            (token_hash(token), user_id, now_utc() + timedelta(days=SESSION_DAYS)))
            con.commit()
        return token

    def session_user(self, token):
        if not token or len(token) > 200:
            return None
        with self.connect() as con, con.cursor() as cur:
            cur.execute('SELECT u.id, u.nome, u.email FROM sessoes s JOIN usuarios u ON u.id=s.usuario_id WHERE s.token_hash=%s AND s.expira_em>%s', (token_hash(token), now_utc()))
            return cur.fetchone()

    def logout(self, token):
        with self.connect() as con:
            with con.cursor() as cur:
                cur.execute('DELETE FROM sessoes WHERE token_hash=%s', (token_hash(token),))
            con.commit()
