import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import unittest
import copy
from datetime import date, timedelta
from decimal import Decimal
from fastapi.testclient import TestClient
import pymysql
from servidor import create_app
from autenticacao import password_hash, password_matches, now_utc, token_hash

PASSWORD='uma frase de teste longa'
HASH=password_hash(PASSWORD)

class Database:
    def __init__(self):
        self.rows=[dict(id=1,usuario_id=1,data_despesa=date(2026,10,16),descricao='Restaurante',valor=Decimal('90.00'),categoria='Lazer',forma_pagamento='Pix'),dict(id=2,usuario_id=2,data_despesa=date(2026,10,16),descricao='Privado',valor=Decimal('999.00'),categoria='Lazer',forma_pagamento='Pix')]
        self.users=[dict(id=i,nome=f'Pessoa {i}',email=f'pessoa{i}@teste.com',senha_hash=HASH) for i in (1,2)]
        for row in self.rows:
            row.update(categoria_id=1,forma_pagamento_id=1,data_pagamento=None,tipo='unica',parcelamento_id=None,numero_parcela=1,total_parcelas=1,versao=1)
        self.budgets={};self.sessions={};self.commits=0;self.rollbacks=0;self.lastrowid=0;self.fail_insert=False
        self.depth=0;self.snapshot=None;self.fail_after=None;self.inserts=0
    def __enter__(self):
        if self.depth==0:self.snapshot=copy.deepcopy((self.rows,self.budgets))
        self.depth+=1
        return self
    def __exit__(self,*args):self.depth-=1
    def cursor(self):return self
    def execute(self,sql,params=None):
        if 'FROM despesas d JOIN' in sql:
            assert 'WHERE d.usuario_id=%s' in sql
            if 'd.data_pagamento IS NULL' in sql:
                self.result=sorted([r.copy() for r in self.rows if r['usuario_id']==params[0] and r['data_pagamento'] is None],key=lambda r:(r['data_despesa'],r['id']))
            else:self.result=[r.copy() for r in self.rows if r['usuario_id']==params[0] and params[1]<=r['data_despesa']<=params[2]]
        elif sql.startswith('SELECT id, versao'):
            if 'WHERE usuario_id=%s AND parcelamento_id=%s' in sql:
                self.result=sorted([r.copy() for r in self.rows if r['usuario_id']==params[0] and r['parcelamento_id']==params[1]],key=lambda r:r['numero_parcela'])
            else:self.result=[r.copy() for r in self.rows if r['id']==params[0] and r['usuario_id']==params[1]]
        elif sql.startswith('SELECT valor FROM orcamentos_usuarios'):
            assert 'WHERE usuario_id=%s' in sql
            v=self.budgets.get(params);self.result=[] if v is None else [{'valor':v}]
        elif sql.startswith('SELECT id, nome FROM'):
            self.result=[{'id':1,'nome':'Lazer' if 'categorias' in sql else 'Pix'}]
        elif sql.startswith('SELECT id FROM'):
            self.result=[{'id':1}] if params[0]==1 else []
        elif sql.startswith('INSERT INTO despesas'):
            assert 'usuario_id' in sql
            self.inserts+=1
            if self.fail_insert or (self.fail_after and self.inserts>=self.fail_after):raise pymysql.OperationalError(2006,'simulated')
            self.lastrowid=len(self.rows)+1
            self.rows.append(dict(id=self.lastrowid,usuario_id=params[5],data_despesa=params[0],descricao=params[1],valor=params[2],categoria_id=params[3],forma_pagamento_id=params[4],categoria='Lazer',forma_pagamento='Pix',data_pagamento=params[6],tipo=params[7],parcelamento_id=params[8],numero_parcela=params[9],total_parcelas=params[10],versao=1))
        elif sql.startswith('UPDATE despesas SET data_despesa'):
            assert 'AND usuario_id=%s AND versao=%s' in sql
            for r in self.rows:
                if r['id']==params[6] and r['usuario_id']==params[7] and r['versao']==params[8]:
                    r.update(data_despesa=params[0],descricao=params[1],valor=params[2],categoria_id=params[3],forma_pagamento_id=params[4],data_pagamento=params[5],versao=r['versao']+1)
        elif sql.startswith('UPDATE despesas SET total_parcelas'):
            for r in self.rows:
                if r['usuario_id']==params[1] and r['parcelamento_id']==params[2]:r.update(total_parcelas=params[0],versao=r['versao']+1)
        elif sql.startswith('INSERT INTO orcamentos_usuarios'):self.budgets[tuple(params[:3])]=params[3]
        elif sql.startswith('SELECT id, nome, email, senha_hash'):self.result=[u.copy() for u in self.users if u['email']==params[0]]
        elif sql.startswith('INSERT INTO usuarios'):
            if any(u['email']==params[1] for u in self.users):raise pymysql.IntegrityError(1062,'duplicate')
            self.lastrowid=len(self.users)+1
            self.users.append(dict(id=self.lastrowid,nome=params[0],email=params[1],senha_hash=params[2]))
        elif sql.startswith('INSERT INTO sessoes'):self.sessions[params[0]]=(params[1],params[2])
        elif sql.startswith('SELECT u.id'):
            sess=self.sessions.get(params[0]);self.result=[]
            if sess and sess[1]>params[1]:self.result=[{k:u[k] for k in ('id','nome','email')} for u in self.users if u['id']==sess[0]]
        elif sql.startswith('DELETE FROM sessoes WHERE expira_em'):self.sessions={k:v for k,v in self.sessions.items() if v[1]>params[0]}
        elif sql.startswith('DELETE FROM sessoes WHERE token_hash'):self.sessions.pop(params[0],None)
        else:raise AssertionError(sql)
    def fetchall(self):return self.result
    def fetchone(self):return self.result[0] if self.result else None
    def commit(self):
        self.commits+=1
        self.snapshot=copy.deepcopy((self.rows,self.budgets))
    def rollback(self):
        self.rollbacks+=1
        if self.snapshot:self.rows,self.budgets=copy.deepcopy(self.snapshot)

class AccountTests(unittest.TestCase):
    def setUp(self):
        self.db=Database();self.app=create_app(lambda:self.db);self.client=TestClient(self.app)
    def login(self,client=None,account=1):
        client=client or self.client
        r=client.post('/api/entrar',json={'email':f'pessoa{account}@teste.com','senha':PASSWORD})
        self.assertEqual(r.status_code,200,r.text);return r
    def expense(self):return dict(data='17/10/2026',descricao='Mercado',valor='35,90',categoria_id=1,forma_pagamento_id=1)
    def test_authentication_origin_cookies(self):
        self.assertEqual(self.client.get('/api/painel?mes=10/2026').status_code,401)
        self.assertEqual(self.client.post('/api/entrar',json={'email':'pessoa1@teste.com','senha':'errada'}).status_code,401)
        self.assertEqual(self.client.post('/api/entrar',json={},headers={'Origin':'http://outro-site'}).status_code,403)
        r=self.login();self.assertIn('HttpOnly',r.headers['set-cookie']);self.assertIn('SameSite=strict',r.headers['set-cookie'])
        self.assertEqual(self.client.get('/api/conta').json()['id'],1)
    def test_register_empty_hashed_duplicate(self):
        body={'nome':'Nova pessoa','email':' NOVA@TESTE.COM ','senha':PASSWORD}
        r=self.client.post('/api/cadastrar',json=body);self.assertEqual(r.status_code,201,r.text)
        self.assertNotIn('senha_hash',r.text);saved=self.db.users[-1]['senha_hash']
        self.assertNotEqual(saved,PASSWORD);self.assertTrue(password_matches(PASSWORD,saved))
        self.assertEqual(self.client.get('/api/painel?mes=10/2026').json()['quantidade'],0)
        self.assertEqual(self.client.post('/api/cadastrar',json=body).status_code,422)
    def test_register_validation(self):
        for b in [{'nome':'A','email':'invalido','senha':PASSWORD},{'nome':'A','email':'a@b.com','senha':'curta'},{'nome':'','email':'a@b.com','senha':PASSWORD}]:self.assertEqual(self.client.post('/api/cadastrar',json=b).status_code,422)
    def test_isolation_totals_calendar(self):
        self.login();r=self.client.get('/api/painel?mes=10/2026').json()
        self.assertEqual(r['total'],'90.00');self.assertEqual(r['quantidade'],1);self.assertNotIn('Privado',str(r))
        self.assertEqual(len(r['diario']),31);self.assertEqual(r['diario'][15]['valor'],'90.00');self.assertIsNone(r['saldo'])
        self.assertEqual(len(self.client.get('/api/painel?mes=02/2024').json()['diario']),29)
        self.assertEqual(self.client.get('/api/painel?mes=13/2026').status_code,422)
    def test_forged_owner_ignored(self):
        self.login();body=self.expense();body['usuario_id']=2
        self.assertEqual(self.client.post('/api/despesas',json=body).status_code,201)
        self.assertEqual(self.db.rows[-1]['usuario_id'],1)
        self.assertEqual(self.client.get('/api/painel?mes=10/2026').json()['total'],'125.90')
        other=TestClient(self.app);self.login(other,2)
        self.assertEqual(other.get('/api/painel?mes=10/2026').json()['total'],'999.00')
    def test_validation_rollback(self):
        self.login();before=self.db.commits
        for key,v in [('valor','-1'),('valor','1,234'),('data','31/02/2026'),('descricao','  '),('categoria_id',999)]:
            b=self.expense();b[key]=v;self.assertEqual(self.client.post('/api/despesas',json=b).status_code,422)
        self.assertEqual(self.db.commits,before);self.db.fail_insert=True
        self.assertEqual(self.client.post('/api/despesas',json=self.expense()).status_code,503);self.assertGreater(self.db.rollbacks,0)
    def test_separate_budget(self):
        self.login();self.client.put('/api/orcamento',json={'mes':'10/2026','valor':'3000','usuario_id':2})
        self.assertEqual(self.client.get('/api/painel?mes=10/2026').json()['saldo'],'2910.00')
        other=TestClient(self.app);self.login(other,2)
        self.assertIsNone(other.get('/api/painel?mes=10/2026').json()['orcamento'])
    def test_logout_expiration(self):
        self.login();token=self.client.cookies.get('sessao');self.client.post('/api/sair')
        self.assertNotIn(token_hash(token),self.db.sessions);self.assertEqual(self.client.get('/api/conta').status_code,401)
        self.login();key=token_hash(self.client.cookies.get('sessao'));self.db.sessions[key]=(1,now_utc()-timedelta(seconds=1))
        self.assertEqual(self.client.get('/api/conta').status_code,401)
    def test_rate_limit(self):
        for i in range(8):self.assertEqual(self.client.post('/api/entrar',json={'email':'pessoa1@teste.com','senha':'errada'}).status_code,401)
        self.assertEqual(self.client.post('/api/entrar',json={'email':'pessoa1@teste.com','senha':PASSWORD}).status_code,429)
    def test_session_after_restart(self):
        self.login();restarted=TestClient(create_app(lambda:self.db));restarted.cookies.set('sessao',self.client.cookies.get('sessao'))
        self.assertEqual(restarted.get('/api/conta').status_code,200)
    def test_static(self):
        self.assertEqual(self.client.get('/').status_code,200);self.assertEqual(self.client.get('/app.js').status_code,200)

if __name__=='__main__':unittest.main()
