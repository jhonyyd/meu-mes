from calendar import monthrange
from configparser import ConfigParser
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from getpass import getpass
from pathlib import Path
import re
import sys

import pymysql


def converter_valor(texto):

    texto = texto.strip()
    if not re.fullmatch(r"[0-9]+(?:[.,][0-9]{1,2})?", texto):
        raise ValueError("Informe um valor como 150,90, sem separador de milhar.")
    try:
        valor = Decimal(texto.replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError("Valor inválido.") from exc
    if not Decimal("0") < valor <= Decimal("9999999999.99"):
        raise ValueError("O valor deve ser maior que zero e até 9999999999,99.")
    return valor.quantize(Decimal("0.01"))


def converter_data(texto):
    try:
        if not re.fullmatch(r"[0-9]{2}/[0-9]{2}/[0-9]{4}", texto.strip()):
            raise ValueError
        resultado = datetime.strptime(texto.strip(), "%d/%m/%Y").date()
        if resultado.year < 1000:
            raise ValueError
        return resultado
    except ValueError as exc:
        raise ValueError("Informe uma data válida no formato DD/MM/AAAA.") from exc


def periodo_mes(texto):
    if not re.fullmatch(r"[0-9]{2}/[0-9]{4}", texto.strip()):
        raise ValueError("Informe o mês no formato MM/AAAA.")
    mes, ano = map(int, texto.strip().split("/"))
    if not 1000 <= ano <= 9999 or not 1 <= mes <= 12:
        raise ValueError("Mês ou ano inválido.")
    return date(ano, mes, 1), date(ano, mes, monthrange(ano, mes)[1])


def dinheiro(valor):
    return "R$ " + f"{valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def perguntar_validado(mensagem, conversor):
    while True:
        try:
            return conversor(input(mensagem))
        except ValueError as erro:
            print(erro)


def escolher(connection, tabela, titulo):

    if tabela not in {"categorias", "formas_pagamento"}:
        raise ValueError("Cadastro desconhecido.")
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT id, nome FROM {tabela} ORDER BY nome")
        opcoes = {str(row["id"]): row["nome"] for row in cursor.fetchall()}
    if not opcoes:
        raise ValueError(f"Nenhum item em {titulo}. Confira os cadastros iniciais.")
    print(f"\n{titulo}:")
    for codigo, nome in opcoes.items():
        print(f"  {codigo} - {nome}")
    while True:
        codigo = input("Digite o número: ").strip()
        if codigo in opcoes:
            return int(codigo), opcoes[codigo]
        print("Escolha um dos números listados.")


def cadastrar(connection):
    hoje = date.today().strftime("%d/%m/%Y")
    data_despesa = perguntar_validado(
        f"Data DD/MM/AAAA [Enter = {hoje}]: ",
        lambda texto: converter_data(texto.strip() or hoje),
    )
    while True:
        descricao = input("Descrição: ").strip()
        if 1 <= len(descricao) <= 255:
            break
        print("A descrição deve ter de 1 a 255 caracteres.")
    valor = perguntar_validado("Valor (ex.: 35,90): ", converter_valor)
    categoria_id, categoria = escolher(connection, "categorias", "Categorias")
    pagamento_id, pagamento = escolher(connection, "formas_pagamento", "Formas de pagamento")
    print(f"\n{data_despesa:%d/%m/%Y} | {descricao} | {dinheiro(valor)}")
    print(f"Categoria: {categoria} | Pagamento: {pagamento}")
    if input("Salvar? [s/N]: ").strip().lower() != "s":
        print("Cadastro cancelado.")
        return
    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO despesas (data_despesa, descricao, valor, categoria_id, forma_pagamento_id) "
                "VALUES (%s, %s, %s, %s, %s)",
                (data_despesa, descricao, valor, categoria_id, pagamento_id),
            )
            identificador = cursor.lastrowid
        connection.commit()
    except pymysql.MySQLError:
        connection.rollback()
        raise
    print(f"Despesa salva! Código: {identificador}.")


def listar(connection):
    padrao = date.today().strftime("%m/%Y")
    inicio, fim = perguntar_validado(
        f"Mês MM/AAAA [Enter = {padrao}]: ",
        lambda texto: periodo_mes(texto.strip() or padrao),
    )

    try:
        connection.begin()
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS quantidade, COALESCE(SUM(valor), 0) AS total "
                "FROM despesas WHERE data_despesa BETWEEN %s AND %s", (inicio, fim),
            )
            resumo = cursor.fetchone()
            print(f"\n{inicio:%m/%Y}: {resumo['quantidade']} despesas | Total: {dinheiro(resumo['total'])}")
            offset = 0
            while offset < resumo["quantidade"]:
                cursor.execute(
                    "SELECT id, data_despesa, descricao, valor, categoria, forma_pagamento "
                    "FROM vw_despesas_detalhadas WHERE data_despesa BETWEEN %s AND %s "
                    "ORDER BY data_despesa DESC, id DESC LIMIT %s OFFSET %s",
                    (inicio, fim, 20, offset),
                )
                for row in cursor.fetchall():
                    print(f"\n#{row['id']} | {row['data_despesa']:%d/%m/%Y} | {dinheiro(row['valor'])}")
                    print(f"  {row['descricao']} | {row['categoria']} | {row['forma_pagamento']}")
                offset += 20
                if offset < resumo["quantidade"] and input("\nVer mais? [s/N]: ").strip().lower() != "s":
                    break
    finally:
        connection.rollback()


def main():
    print("O cadastro agora usa contas separadas na interface web.")
    print("Inicie com: .\\.venv\\Scripts\\python.exe servidor.py")
    print("Entre pelo navegador com o e-mail e a senha da sua conta.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nOperação encerrada. Confira a listagem antes de repetir um cadastro interrompido.")
        sys.exit(0)
