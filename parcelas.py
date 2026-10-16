from calendar import monthrange
from datetime import date
from decimal import Decimal
from despesas import converter_data, converter_valor


def monthly_date(first, offset):
    month_index = first.year * 12 + first.month - 1 + offset
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    if not 1000 <= year <= 9999:
        raise ValueError('As parcelas ultrapassam o intervalo de datas permitido.')
    return date(year, month, min(first.day, monthrange(year, month)[1]))


def installment_plan(total, count, first):
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= 60:
        raise ValueError('Escolha de 1 a 60 parcelas.')
    cents = int(total * 100)
    if cents < count:
        raise ValueError('Cada parcela precisa ter pelo menos R$ 0,01.')
    base, remainder = divmod(cents, count)
    return [(monthly_date(first, index), Decimal(base + (index < remainder)) / 100)
            for index in range(count)]


def expense_fields(body):
    day = converter_data(str(body.get('data', '')))
    value = converter_valor(str(body.get('valor', '')))
    description = str(body.get('descricao', '')).strip()
    if not 1 <= len(description) <= 255:
        raise ValueError('A descrição deve ter de 1 a 255 caracteres.')
    category = int(body.get('categoria_id', 0))
    payment = int(body.get('forma_pagamento_id', 0))
    paid = body.get('pago', False)
    if not isinstance(paid, bool):
        raise ValueError('Selecione se a despesa está paga ou em aberto.')
    paid_on = converter_data(str(body.get('data_pagamento', ''))) if paid else None
    if paid_on and paid_on > date.today():
        raise ValueError('Um pagamento realizado não pode ter data futura.')
    return day, value, description, category, payment, paid_on
