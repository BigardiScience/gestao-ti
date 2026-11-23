"""Infraestrutura dos testes: aplicação isolada em pasta temporária e um "navegador" com CSRF."""
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402


def gerar_imei(base14: str) -> str:
    """Completa 14 dígitos com o dígito verificador (Luhn)."""
    soma = 0
    for i, c in enumerate(reversed(base14)):
        d = int(c)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        soma += d
    return base14 + str((10 - soma % 10) % 10)


def gerar_cpf(base9: str) -> str:
    numeros = [int(c) for c in base9]
    for tamanho in (9, 10):
        soma = sum(numeros[i] * (tamanho + 1 - i) for i in range(tamanho))
        numeros.append((soma * 10) % 11 % 10)
    return "".join(map(str, numeros))


class Navegador:
    def __init__(self, cliente):
        self.cliente = cliente

    def csrf(self) -> str:
        with self.cliente.session_transaction() as sessao:
            if "_csrf" not in sessao:
                sessao["_csrf"] = "token-de-teste-" + str(id(self))
            return sessao["_csrf"]

    def get(self, url, **kwargs):
        return self.cliente.get(url, **kwargs)

    def post(self, url, data=None, **kwargs):
        dados = dict(data or {})
        dados.setdefault("_csrf", self.csrf())
        return self.cliente.post(url, data=dados, **kwargs)


@pytest.fixture
def app(tmp_path):
    aplicacao = create_app({"PASTA_DADOS": tmp_path / "dados", "TESTING": True, "AGENDADOR_ATIVO": False,
                            "INTERVALO_ENVIO_LOTE": 0})
    yield aplicacao


@pytest.fixture
def anonimo(app):
    return Navegador(app.test_client())


SENHA_ADMIN = "Senha-forte-123"


@pytest.fixture
def admin(app):
    nav = Navegador(app.test_client())
    resposta = nav.post("/instalacao", {
        "codigo": app.config["CODIGO_INSTALACAO"], "empresa_nome": "Grupo Teste", "url_base": "http://localhost",
        "nome": "Ana Administradora", "email": "ti@empresa.com.br", "senha": SENHA_ADMIN, "confirmar": SENHA_ADMIN,
    })
    assert resposta.status_code == 302, resposta.data[:500]
    return nav


def criar_unidade(nav, nome="Matriz", cidade="Sorocaba", uf="SP") -> int:
    r = nav.post("/unidades/nova", {"nome": nome, "cidade": cidade, "uf": uf, "ativo": "1"})
    assert r.status_code == 302, r.data.decode()[:2000]
    with nav.cliente.application.app_context():
        from app.db import escalar
        return escalar("SELECT id FROM unidades WHERE nome = ?", (nome,))


_contador = {"imei": 0, "cpf": 0}


def criar_aparelho(nav, unidade_id, **extra) -> int:
    _contador["imei"] += 1
    n = _contador["imei"]
    dados = {"marca": "Samsung", "modelo": f"Galaxy A{50 + n}", "numero_serie": f"SER{n:06d}",
             "imei": gerar_imei(f"3569380356{n:04d}"), "linha": f"119{n:08d}", "operadora": "Vivo",
             "unidade_id": unidade_id, "estado_conservacao": "Novo", "status": "disponivel",
             "acessorios": "Carregador e capa", "valor": "1.899,90"}
    dados.update(extra)
    r = nav.post("/aparelhos/novo", dados)
    assert r.status_code == 302, r.data.decode()[:3000]
    return int(r.headers["Location"].rstrip("/").split("/")[-1])


def criar_colaborador(nav, unidade_id, nome=None, **extra):
    _contador["cpf"] += 1
    n = _contador["cpf"]
    nome = nome or f"Colaborador Teste {n}"
    dados = {"nome": nome, "cpf": gerar_cpf(f"{123456700 + n:09d}"), "email": f"colab{n}@empresa.com.br",
             "cargo": "Analista", "departamento": "Financeiro", "unidade_id": unidade_id}
    dados.update(extra)
    r = nav.post("/colaboradores/novo", dados)
    assert r.status_code == 302, r.data.decode()[:3000]
    return r, dados


def ultimo_email(app, finalidade=None):
    with app.app_context():
        from app.db import consultar
        sql = "SELECT * FROM emails"
        parametros = []
        if finalidade:
            sql += " WHERE finalidade = ?"
            parametros.append(finalidade)
        return consultar(sql + " ORDER BY id DESC LIMIT 1", parametros, um=True)


def token_do_email(email) -> str:
    achado = re.search(r"/assinar/([A-Za-z0-9_-]{20,})", email["corpo_html"])
    assert achado, "link de assinatura não encontrado no e-mail"
    return achado.group(1)


def codigo_do_email(email) -> str:
    achado = re.search(r">(\d{6})<", email["corpo_html"])
    assert achado, "código não encontrado no e-mail"
    return achado.group(1)


TRACOS = '{"w": 600, "h": 190, "tracos": [[[40, 120], [80, 80], [120, 130], [160, 70], [220, 140], [300, 90]], [[320, 100], [380, 110]]]}'
