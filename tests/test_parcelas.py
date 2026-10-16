import unittest
from datetime import date, timedelta
from decimal import Decimal
import test_contas as fixtures
from parcelas import installment_plan, monthly_date

class ScheduleTests(unittest.TestCase):
    def test_cents_and_anchor_day(self):
        plan=installment_plan(Decimal('100.00'),3,date(2026,1,31))
        self.assertEqual([v for d,v in plan],[Decimal('33.34'),Decimal('33.33'),Decimal('33.33')])
        self.assertEqual([d for d,v in plan],[date(2026,1,31),date(2026,2,28),date(2026,3,31)])
        self.assertEqual(monthly_date(date(2024,1,31),1),date(2024,2,29))
        self.assertEqual(sum(v for d,v in plan),Decimal('100.00'))
    def test_invalid_counts_and_tiny_amount(self):
        for count in [0,61,True,1.5]:
            with self.assertRaises(ValueError):installment_plan(Decimal('100'),count,date.today())
        with self.assertRaises(ValueError):installment_plan(Decimal('0.01'),3,date.today())

class InstallmentTests(unittest.TestCase):
    setUp=fixtures.AccountTests.setUp
    login=fixtures.AccountTests.login
    expense=fixtures.AccountTests.expense
    def test_three_installments_across_months(self):
        self.login();body=self.expense();body.update(tipo='parcelada',parcelas=3,valor='100',data='31/01/2026')
        r=self.client.post('/api/despesas',json=body)
        self.assertEqual(r.status_code,201,r.text);self.assertEqual(r.json()['quantidade'],3)
        rows=self.db.rows[-3:]
        self.assertEqual([r['numero_parcela'] for r in rows],[1,2,3])
        self.assertEqual([r['data_despesa'].day for r in rows],[31,28,31])
        self.assertEqual(sum(r['valor'] for r in rows),Decimal('100'))
        d=self.client.get('/api/painel?mes=02/2026').json()
        self.assertEqual(d['total'],'33.33');self.assertEqual(d['proximas_total'],'190.00')
    def test_early_payment_and_edit_ownership(self):
        self.login();body=self.expense();body.update(versao=1,pago=True,data_pagamento=date.today().strftime('%d/%m/%Y'))
        self.assertEqual(self.client.put('/api/despesas/2',json=body).status_code,404)
        body.update(data='31/12/2026',valor='80')
        self.assertEqual(self.client.put('/api/despesas/1',json=body).status_code,200)
        d=self.client.get('/api/painel?mes=12/2026').json()
        self.assertEqual(d['total'],'80.00');self.assertEqual(d['total_pago'],'80.00');self.assertEqual(d['proximas_total'],'0')
        self.assertEqual(self.client.put('/api/despesas/1',json=body).status_code,409)
        body.update(versao=2,pago=False)
        self.assertEqual(self.client.put('/api/despesas/1',json=body).status_code,200)
        self.assertEqual(self.client.get('/api/painel?mes=12/2026').json()['total_em_aberto'],'80.00')
    def test_payment_future_rejected(self):
        self.login();body=self.expense();body.update(pago=True,data_pagamento=(date.today()+timedelta(days=1)).strftime('%d/%m/%Y'))
        self.assertEqual(self.client.post('/api/despesas',json=body).status_code,422)
    def test_subscription_extend_and_sum(self):
        self.login();body=self.expense();body.update(tipo='assinatura',parcelas=2,valor='29,90',data='31/01/2026')
        r=self.client.post('/api/despesas',json=body);self.assertEqual(r.status_code,201,r.text)
        expense_id=r.json()['id']
        r=self.client.post(f'/api/despesas/{expense_id}/estender',json={'meses':1,'valor':'39,90','versao':1})
        self.assertEqual(r.status_code,200,r.text)
        rows=[r for r in self.db.rows if r['tipo']=='assinatura']
        self.assertEqual(len(rows),3);self.assertEqual(rows[-1]['data_despesa'],date(2026,3,31))
        self.assertEqual(rows[-1]['valor'],Decimal('39.90'));self.assertEqual(rows[0]['valor'],Decimal('29.90'))
        self.assertTrue(all(r['total_parcelas']==3 for r in rows))
        d=self.client.get('/api/painel?mes=03/2026').json();self.assertEqual(d['proximas_total'],'189.70')
        self.assertEqual(self.client.post(f'/api/despesas/{expense_id}/estender',json={'meses':1,'valor':'39,90','versao':1}).status_code,409)
        self.assertEqual(self.client.post('/api/despesas/2/estender',json={'meses':1,'valor':'10','versao':1}).status_code,404)
    def test_partial_schedule_rolls_back(self):
        self.login();body=self.expense();body.update(tipo='parcelada',parcelas=3)
        original=len(self.db.rows);self.db.fail_after=2
        self.assertEqual(self.client.post('/api/despesas',json=body).status_code,503)
        self.assertEqual(len(self.db.rows),original)

if __name__=='__main__':unittest.main()
