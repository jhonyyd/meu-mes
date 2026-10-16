import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from migrar_contas import _prepare

class SchemaDatabase:
    def __init__(self,root,migrated=False):
        self.root=root;self.migrated=migrated;self.queries=[];self.result=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return self
    def commit(self):pass
    def execute(self,sql,params=None):
        self.queries.append(sql)
        if 'information_schema.COLUMNS' in sql:self.result=[{'COLUMN_NAME':'usuario_id'}] if self.migrated else []
        elif sql.startswith('SELECT * FROM '):self.result=[{'id':1,'descricao':'Lançamento preservado'}]
        elif sql.startswith('ALTER TABLE'):
            assert list((self.root/'backups').glob('*.json')), 'Backup deve existir antes do ALTER'
    def fetchone(self):return self.result[0] if self.result else None
    def fetchall(self):return self.result

class MigrationTests(unittest.TestCase):
    def test_backup_before_update_and_legacy_owner(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);db=SchemaDatabase(root);auth=MagicMock();auth.count_users.return_value=0
            with patch('migrar_contas.MySQLAuth',return_value=auth),patch('builtins.input',side_effect=['Dono','dono@teste.com']),patch('migrar_contas.getpass',return_value='uma frase de teste longa'),patch('builtins.print'):
                _prepare(lambda:db,root)
            files=list((root/'backups').glob('*.json'));self.assertEqual(len(files),1)
            data=json.loads(files[0].read_text(encoding='utf-8'))
            self.assertIn('despesas',data);self.assertIn('orcamentos_mensais',data)
            self.assertEqual(data['despesas'][0]['descricao'],'Lançamento preservado')
            self.assertTrue(auth.register.call_args.kwargs['claim_legacy'])
            self.assertFalse(any(q.strip().upper().startswith(('DROP','TRUNCATE')) for q in db.queries))
    def test_repeat_keeps_existing_accounts_and_schema(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root=Path(folder);db=SchemaDatabase(root,True);auth=MagicMock();auth.count_users.return_value=1
            with patch('migrar_contas.MySQLAuth',return_value=auth),patch('builtins.input') as ask:
                _prepare(lambda:db,root)
            auth.register.assert_not_called();ask.assert_not_called()
            self.assertFalse(any(q.startswith('ALTER TABLE') for q in db.queries))

if __name__=='__main__':unittest.main()
