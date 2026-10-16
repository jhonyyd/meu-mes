import argparse
from calendar import monthrange
from configparser import ConfigParser
from datetime import date
from decimal import Decimal
from getpass import getpass
from pathlib import Path
import socket
import sys
from uuid import uuid4

import pymysql
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from despesas import converter_data, converter_valor, periodo_mes
from autenticacao import MySQLAuth, RateLimiter, SESSION_DAYS
from migrar_contas import prepare_accounts
from migrar_parcelas import prepare_installments
from parcelas import expense_fields, installment_plan, monthly_date

ROOT = Path(__file__).resolve().parent
DETAILS = ('SELECT d.id, d.data_despesa, d.descricao, d.valor, d.categoria_id, d.forma_pagamento_id, '
           'd.data_pagamento, d.tipo, d.parcelamento_id, d.numero_parcela, d.total_parcelas, d.versao, '
           'c.nome AS categoria, f.nome AS forma_pagamento FROM despesas d '
           'JOIN categorias c ON c.id=d.categoria_id JOIN formas_pagamento f ON f.id=d.forma_pagamento_id ')


def serialize_expense(row):
    return {**row, 'valor': str(row['valor']), 'data_despesa': row['data_despesa'].isoformat(),
            'data_pagamento': row.get('data_pagamento').isoformat() if row.get('data_pagamento') else None,
            'atrasada': row['data_despesa'] < date.today() and not row.get('data_pagamento')}


def create_app(connect, auth=None, secure_cookie=False):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    auth = auth if auth is not None else MySQLAuth(connect)
    limiter = RateLimiter()

    @app.middleware('http')
    async def security(request, call_next):
        if request.url.path.startswith('/api/'):
            if request.method != 'GET':
                origin = request.headers.get('origin')
                if origin and origin != str(request.base_url).rstrip('/'):
                    return JSONResponse({'detail': 'Origem não permitida.'}, status_code=403)
            if request.url.path not in {'/api/entrar', '/api/cadastrar'}:
                try:
                    user = await run_in_threadpool(auth.session_user, request.cookies.get('sessao', ''))
                except pymysql.MySQLError:
                    return JSONResponse({'detail': 'MySQL indisponível. Confira o servidor.'}, status_code=503)
                if not user:
                    return JSONResponse({'detail': 'Entre na sua conta para continuar.'}, status_code=401)
                request.state.user = user
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(pymysql.MySQLError)
    async def database_error(request, error):
        return JSONResponse({'detail': 'Não foi possível consultar ou salvar no MySQL. Confira o serviço e reinicie o servidor se necessário.'}, status_code=503)

    async def payload(request):
        try:
            if len(await request.body()) > 8192:
                raise HTTPException(413, 'Dados maiores que o permitido.')
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError
            return body
        except (ValueError, TypeError):
            raise HTTPException(400, 'Dados inválidos.')

    @app.post('/api/entrar')
    async def login(request: Request):
        body = await payload(request)
        email = str(body.get('email', '')).strip().lower()
        if not limiter.check(request.client.host if request.client else 'local', email, 'login'):
            raise HTTPException(429, 'Muitas tentativas. Aguarde 15 minutos e tente novamente.')
        password = body.get('senha', '')
        if not isinstance(password, str) or len(password) > 128:
            raise HTTPException(401, 'E-mail ou senha incorretos.')
        user = await run_in_threadpool(auth.login, email, password)
        if not user:
            raise HTTPException(401, 'E-mail ou senha incorretos.')
        return await open_session(user, request)

    async def open_session(user, request):
        old_token = request.cookies.get('sessao', '')
        if old_token:
            await run_in_threadpool(auth.logout, old_token)
        token = await run_in_threadpool(auth.new_session, user['id'])
        response = JSONResponse({'usuario': user})
        response.set_cookie('sessao', token, max_age=SESSION_DAYS * 86400,
                            httponly=True, samesite='strict', secure=secure_cookie)
        return response

    @app.post('/api/cadastrar', status_code=201)
    async def register(request: Request):
        body = await payload(request)
        email = str(body.get('email', '')).strip().lower()
        if not limiter.check(request.client.host if request.client else 'local', email, 'register'):
            raise HTTPException(429, 'Muitos cadastros. Aguarde 15 minutos e tente novamente.')
        try:
            user = await run_in_threadpool(auth.register, body.get('nome', ''), email, body.get('senha', ''))
        except ValueError as error:
            raise HTTPException(422, str(error))
        response = await open_session(user, request)
        response.status_code = 201
        return response

    @app.get('/api/conta')
    def account(request: Request):
        return request.state.user

    @app.post('/api/sair')
    async def logout(request: Request):
        await run_in_threadpool(auth.logout, request.cookies.get('sessao', ''))
        response = JSONResponse({'ok': True})
        response.delete_cookie('sessao', httponly=True, samesite='strict', secure=secure_cookie)
        return response

    @app.get('/api/cadastros')
    def catalogs():
        with connect() as con, con.cursor() as cur:
            cur.execute('SELECT id, nome FROM categorias ORDER BY nome')
            categories = cur.fetchall()
            cur.execute('SELECT id, nome FROM formas_pagamento ORDER BY nome')
            return {'categorias': categories, 'formas': cur.fetchall()}

    @app.get('/api/painel')
    def dashboard(request: Request, mes: str):
        try:
            start, end = periodo_mes(mes)
        except ValueError as error:
            raise HTTPException(422, str(error))
        with connect() as con, con.cursor() as cur:
            cur.execute(DETAILS + 'WHERE d.usuario_id=%s AND d.data_despesa BETWEEN %s AND %s ORDER BY d.data_despesa DESC, d.id DESC', (request.state.user['id'], start, end))
            rows = cur.fetchall()
            cur.execute('SELECT valor FROM orcamentos_usuarios WHERE usuario_id=%s AND ano=%s AND mes=%s', (request.state.user['id'], start.year, start.month))
            budget = cur.fetchone()
            cur.execute(DETAILS + 'WHERE d.usuario_id=%s AND d.data_pagamento IS NULL ORDER BY d.data_despesa, d.id', (request.state.user['id'],))
            upcoming = cur.fetchall()
        daily = {day: Decimal('0') for day in range(1, end.day + 1)}
        categories = {}
        for row in rows:
            daily[row['data_despesa'].day] += row['valor']
            categories[row['categoria']] = categories.get(row['categoria'], Decimal('0')) + row['valor']
        total = sum(daily.values(), Decimal('0'))
        limit = budget['valor'] if budget else None
        paid_total = sum((row['valor'] for row in rows if row.get('data_pagamento')), Decimal('0'))
        return {
            'total': str(total), 'quantidade': len(rows),
            'total_pago': str(paid_total), 'total_em_aberto': str(total - paid_total),
            'proximas_total': str(sum((row['valor'] for row in upcoming), Decimal('0'))),
            'proximas': [serialize_expense(row) for row in upcoming],
            'orcamento': str(limit) if limit is not None else None,
            'saldo': str(limit - total) if limit is not None else None,
            'diario': [{'dia': day, 'valor': str(value)} for day, value in daily.items()],
            'categorias': [{'nome': key, 'valor': str(value)} for key, value in sorted(categories.items(), key=lambda item: item[1], reverse=True)],
            'despesas': [serialize_expense(row) for row in rows],
        }

    @app.post('/api/despesas', status_code=201)
    async def add_expense(request: Request):
        body = await payload(request)
        try:
            day, value, description, category, payment, paid_on = expense_fields(body)
            kind = body.get('tipo', 'unica')
            if kind not in {'unica', 'parcelada', 'assinatura'}:
                raise ValueError('Tipo de despesa inválido.')
            count_raw = body.get('parcelas', 1)
            if isinstance(count_raw, bool) or not str(count_raw).isdigit():
                raise ValueError('Informe uma quantidade inteira de parcelas ou mensalidades.')
            count = int(count_raw) if kind != 'unica' else 1
            if not 1 <= count <= 60:
                raise ValueError('Escolha de 1 a 60 parcelas ou mensalidades.')
            plan = ([(monthly_date(day, offset), value) for offset in range(count)]
                    if kind == 'assinatura' else installment_plan(value, count, day))
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error))
        with connect() as con:
            try:
                with con.cursor() as cur:
                    cur.execute('SELECT id FROM categorias WHERE id=%s', (category,))
                    if not cur.fetchone():
                        raise HTTPException(422, 'Selecione uma categoria válida.')
                    cur.execute('SELECT id FROM formas_pagamento WHERE id=%s', (payment,))
                    if not cur.fetchone():
                        raise HTTPException(422, 'Selecione uma forma de pagamento válida.')
                    group = str(uuid4())
                    ids = []
                    for index, (due, amount) in enumerate(plan, 1):
                        cur.execute('INSERT INTO despesas (data_despesa, descricao, valor, categoria_id, forma_pagamento_id, usuario_id, data_pagamento, tipo, parcelamento_id, numero_parcela, total_parcelas) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (due, description, amount, category, payment, request.state.user['id'], paid_on if index == 1 else None, kind, group, index, count))
                        ids.append(cur.lastrowid)
                con.commit()
            except Exception:
                con.rollback()
                raise
        return {'id': ids[0], 'ids': ids, 'quantidade': count, 'mes': day.strftime('%m/%Y')}

    @app.put('/api/despesas/{expense_id}')
    async def edit_expense(expense_id: int, request: Request):
        body = await payload(request)
        try:
            day, value, description, category, payment, paid_on = expense_fields(body)
            version = int(body.get('versao', 0))
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error))
        with connect() as con:
            try:
                with con.cursor() as cur:
                    cur.execute('SELECT id, versao FROM despesas WHERE id=%s AND usuario_id=%s FOR UPDATE', (expense_id, request.state.user['id']))
                    existing = cur.fetchone()
                    if not existing:
                        raise HTTPException(404, 'Despesa não encontrada.')
                    if existing['versao'] != version:
                        raise HTTPException(409, 'Esta despesa foi alterada em outro aparelho. Atualize o painel e abra a edição novamente.')
                    for table, item in [('categorias', category), ('formas_pagamento', payment)]:
                        cur.execute(f'SELECT id FROM {table} WHERE id=%s', (item,))
                        if not cur.fetchone():
                            raise HTTPException(422, 'Categoria ou pagamento inválido.')
                    cur.execute('UPDATE despesas SET data_despesa=%s, descricao=%s, valor=%s, categoria_id=%s, forma_pagamento_id=%s, data_pagamento=%s, versao=versao+1 WHERE id=%s AND usuario_id=%s AND versao=%s', (day, description, value, category, payment, paid_on, expense_id, request.state.user['id'], version))
                con.commit()
            except Exception:
                con.rollback()
                raise
        return {'ok': True, 'mes': day.strftime('%m/%Y')}

    @app.post('/api/despesas/{expense_id}/estender')
    async def extend_subscription(expense_id: int, request: Request):
        body = await payload(request)
        try:
            raw = body.get('meses', 0)
            if isinstance(raw, bool) or not str(raw).isdigit():
                raise ValueError('Informe uma quantidade inteira de meses.')
            count = int(raw)
            version = int(body.get('versao', 0))
            amount = converter_valor(str(body.get('valor', '')))
            if not 1 <= count <= 60:
                raise ValueError('Adicione de 1 a 60 meses por vez.')
        except (ValueError, TypeError) as error:
            raise HTTPException(422, str(error))
        with connect() as con:
            try:
                with con.cursor() as cur:
                    cur.execute('SELECT id, versao, tipo, parcelamento_id FROM despesas WHERE id=%s AND usuario_id=%s', (expense_id, request.state.user['id']))
                    current = cur.fetchone()
                    if not current:
                        raise HTTPException(404, 'Assinatura não encontrada.')
                    if current['tipo'] != 'assinatura':
                        raise HTTPException(422, 'Somente assinaturas podem ser estendidas.')
                    cur.execute('SELECT id, versao, data_despesa, descricao, categoria_id, forma_pagamento_id, numero_parcela FROM despesas WHERE usuario_id=%s AND parcelamento_id=%s ORDER BY numero_parcela FOR UPDATE', (request.state.user['id'], current['parcelamento_id']))
                    group_rows = cur.fetchall()
                    selected = next((row for row in group_rows if row['id'] == expense_id), None)
                    if not selected or selected['versao'] != version:
                        raise HTTPException(409, 'A assinatura foi alterada. Atualize o painel antes de estender.')
                    last = group_rows[-1]
                    new_count = last['numero_parcela'] + count
                    if new_count > 240:
                        raise HTTPException(422, 'O limite é de 240 mensalidades por assinatura.')
                    latest_due = max(row['data_despesa'] for row in group_rows)
                    first_anchor = group_rows[0]['data_despesa'].day
                    for offset in range(1, count + 1):

                        due = monthly_date(latest_due, offset)
                        due = due.replace(day=min(first_anchor, monthrange(due.year, due.month)[1]))
                        cur.execute('INSERT INTO despesas (data_despesa, descricao, valor, categoria_id, forma_pagamento_id, usuario_id, data_pagamento, tipo, parcelamento_id, numero_parcela, total_parcelas) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)', (due, last['descricao'], amount, last['categoria_id'], last['forma_pagamento_id'], request.state.user['id'], None, 'assinatura', current['parcelamento_id'], last['numero_parcela'] + offset, new_count))
                    cur.execute('UPDATE despesas SET total_parcelas=%s, versao=versao+1 WHERE usuario_id=%s AND parcelamento_id=%s', (new_count, request.state.user['id'], current['parcelamento_id']))
                con.commit()
            except Exception:
                con.rollback()
                raise
        return {'ok': True, 'adicionadas': count}

    @app.put('/api/orcamento')
    async def save_budget(request: Request):
        body = await payload(request)
        try:
            start, end = periodo_mes(str(body.get('mes', '')))
            raw = str(body.get('valor', '')).strip()
            value = Decimal('0.00') if raw in {'0', '0,00', '0.00'} else converter_valor(raw)
        except ValueError as error:
            raise HTTPException(422, str(error))
        with connect() as con:
            try:
                with con.cursor() as cur:
                    cur.execute('INSERT INTO orcamentos_usuarios (usuario_id, ano, mes, valor) VALUES (%s,%s,%s,%s) ON DUPLICATE KEY UPDATE valor=%s', (request.state.user['id'], start.year, start.month, value, value))
                con.commit()
            except Exception:
                con.rollback()
                raise
        return {'ok': True}

    @app.get('/')
    def index():
        return FileResponse(ROOT / 'web/dist/index.html')

    app.mount('/', StaticFiles(directory=ROOT / 'web/dist'), name='interface')
    return app


def main():
    parser = argparse.ArgumentParser(description='Painel local de despesas')
    parser.add_argument('--rede-local', action='store_true', help='Permite acesso pelo celular na mesma rede Wi-Fi')
    parser.add_argument('--porta', type=int, default=8000)
    parser.add_argument('--https', action='store_true', help='Cookies seguros; usar somente quando servido por HTTPS')
    args = parser.parse_args()
    if not 1 <= args.porta <= 65535:
        raise ValueError('A porta precisa estar entre 1 e 65535.')

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(('0.0.0.0' if args.rede_local else '127.0.0.1', args.porta))
    config = ConfigParser()
    config.read(ROOT / 'config.ini', encoding='utf-8-sig')
    settings = config['mysql']
    password = getpass('Senha do MySQL (não aparece ao digitar): ')

    def connect():
        return pymysql.connect(host=settings['host'], port=settings.getint('port'),
                               user=settings['user'], password=password, database=settings['database'],
                               charset='utf8mb4', connect_timeout=5, read_timeout=15, write_timeout=15,
                               cursorclass=pymysql.cursors.DictCursor)

    with connect() as con, con.cursor() as cur:
        cur.execute('SELECT 1')
    prepare_accounts(connect, ROOT)
    prepare_installments(connect, ROOT)
    print(f'\nAbra: http://127.0.0.1:{args.porta}')
    print('Entre com o e-mail e a senha da sua conta no aplicativo.', flush=True)
    if args.rede_local:
        addresses = sorted({address for address in socket.gethostbyname_ex(socket.gethostname())[2]
                            if not address.startswith('127.')})
        for address in addresses:
            print(f'Endereço para testar no celular: http://{address}:{args.porta}')
        if not addresses:
            print(f'No celular, use http://IP-DO-COMPUTADOR:{args.porta}. Consulte o IPv4 do computador.')
        print('O celular e o computador devem estar na mesma rede Wi-Fi.')
        print('Use apenas sua rede de confiança. A conexão local usa HTTP.')
    print('Mantenha este terminal aberto. Ctrl+C encerra o servidor.\n')
    uvicorn.run(create_app(connect, secure_cookie=args.https), host='0.0.0.0' if args.rede_local else '127.0.0.1', port=args.porta, access_log=False)


if __name__ == '__main__':
    try:
        main()
    except pymysql.MySQLError as error:
        print(f'Não foi possível conectar ao MySQL (código {error.args[0]}). Confira config.ini e a senha.')
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print('\nServidor encerrado.')
    except (OSError, RuntimeError, ValueError) as error:
        print(f'Não foi possível iniciar: {error}')
        print('Se a porta estiver ocupada, encerre o servidor antigo com Ctrl+C ou escolha --porta 8002.')
        sys.exit(1)
