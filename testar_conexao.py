from configparser import ConfigParser
from getpass import getpass
from pathlib import Path
import sys

try:
    import pymysql
except ImportError:
    print("Instale as dependências: .\\.venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    print("Em seguida, execute: .\\.venv\\Scripts\\python.exe servidor.py --rede-local")
    sys.exit(1)


def main():
    config = ConfigParser()
    config.read(Path(__file__).with_name("config.ini"), encoding="utf-8-sig")
    try:
        settings = config["mysql"]
        port = settings.getint("port")
        if not 1 <= port <= 65535:
            raise ValueError("porta inválida")
    except (KeyError, ValueError):
        print("Confira a seção [mysql] e a porta em config.ini.")
        return 1

    print(f"Conexão local: {settings['host']}:{port} / {settings['database']}")
    password = getpass("Senha do MySQL: ")
    try:
        with pymysql.connect(
            host=settings["host"], port=port, user=settings["user"],
            password=password, database=settings["database"],
            charset="utf8mb4", connect_timeout=5, read_timeout=10,
            cursorclass=pymysql.cursors.DictCursor,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT VERSION() AS versao, DATABASE() AS banco")
                server = cursor.fetchone()
                print(f"Conectado ao MySQL {server['versao']} — banco {server['banco']}")
                cursor.execute(
                    "SELECT TABLE_NAME AS nome, TABLE_TYPE AS tipo "
                    "FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s",
                    (settings["database"],),
                )
                objects = {row["nome"]: row["tipo"] for row in cursor.fetchall()}
                expected = {
                    "categorias": "BASE TABLE", "formas_pagamento": "BASE TABLE",
                    "despesas": "BASE TABLE", "orcamentos_mensais": "BASE TABLE",
                    "vw_despesas_detalhadas": "VIEW", "vw_gastos_diarios": "VIEW",
                    "vw_gastos_mensais": "VIEW", "vw_gastos_por_categoria": "VIEW",
                }
                missing = [name for name, kind in expected.items() if objects.get(name) != kind]
                if missing:
                    print("Estrutura incompleta: " + ", ".join(missing))
                    return 1
                cursor.execute(
                    "SELECT (SELECT COUNT(*) FROM categorias) AS categorias, "
                    "(SELECT COUNT(*) FROM formas_pagamento) AS formas_pagamento, "
                    "(SELECT COUNT(*) FROM despesas) AS despesas"
                )
                for name, count in cursor.fetchone().items():
                    print(f"{name}: {count}")
                print("CONEXÃO E ESTRUTURA VALIDADAS. Nenhum dado foi alterado.")
        return 0
    except pymysql.MySQLError as error:
        code = error.args[0] if error.args else "desconhecido"
        messages = {
            1045: "Usuário ou senha recusados. Confira o usuário em config.ini e a senha do Workbench.",
            1044: "Este usuário não tem acesso ao banco controle_despesas.",
            1049: "Banco não encontrado. Confira se conectou ao mesmo servidor do Workbench.",
            2003: "Servidor indisponível. Confira o serviço MySQL80, o host e a porta.",
            1146: "Uma tabela esperada não existe neste banco.",
        }
        print(f"Erro MySQL {code}: {messages.get(code, 'Falha ao consultar o banco; informe o código para diagnóstico.')}")
        return 1
    except RuntimeError:
        print("Confira se instalou PyMySQL[rsa] usando requirements.txt.")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nTeste cancelado. Execute no terminal integrado do VS Code.")
        sys.exit(1)
