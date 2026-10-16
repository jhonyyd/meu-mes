import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tempfile
import unittest
from migrar_parcelas import prepare_installments, COLUMNS

class Schema:
    def __init__(self,root,migrated=False):self.root=root;self.migrated=migrated;self.queries=[];self.result=[]
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def cursor(self):return self
    def commit(self):pass
    def execute(self,sql,params=None):
        self.queries.append(sql)
        if 'GET_LOCK' in sql:self.result=[{'obtido':1}]
        elif 'information_schema.COLUMNS' in sql:self.result=[{'COLUMN_NAME':name} for name in COLUMNS] if self.migrated else []
        elif sql=='SELECT * FROM despesas':self.result=[{'id':1,'descricao':'Original','valor':'90.00'}]
        elif sql.startswith('ALTER TABLE'):
            assert list((self.root/'backups').glob('antes-parcelas-*.json'))
            self.migrated=True
        elif 'information_schema.STATISTICS' in sql:self.result=[{'INDEX_NAME':'ix_despesa_aberta'}] if self.migrated else []
    def fetchall(self):return self.result
    def fetchone(self):return self.result[0] if self.result else None

class MigrationInstallmentTests(unittest.TestCase):
    def test_backup_and_repeat(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            db=Schema(Path(folder))
            prepare_installments(lambda:db,folder)
            alterations=sum(q.startswith('ALTER TABLE') for q in db.queries)
            prepare_installments(lambda:db,folder)
            self.assertEqual(sum(q.startswith('ALTER TABLE') for q in db.queries),alterations)
            self.assertEqual(len(list((Path(folder)/'backups').glob('*.json'))),1)
            self.assertFalse(any(q.strip().upper().startswith(('DROP','TRUNCATE','UPDATE despesas'.upper())) for q in db.queries))

if __name__=='__main__':unittest.main()
