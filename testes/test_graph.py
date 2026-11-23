"""Integração com Microsoft Graph contra um servidor local que imita o Entra ID e o Graph."""
import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import TRACOS, Navegador, codigo_do_email, criar_aparelho, criar_colaborador, criar_unidade, ultimo_email

TENANT = "11111111-2222-3333-4444-555555555555"
CLIENTE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def jwt(dados: dict) -> str:
    codificar = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return f"{codificar({'alg': 'none'})}.{codificar(dados)}.assinatura"


class Microsoft:
    """Estado do servidor simulado."""

    def __init__(self):
        self.mensagens = []
        self.tokens_pedidos = []
        self.cenario = "ok"
        self.falhas_429 = 0


def criar_servidor(estado: Microsoft):
    class Manipulador(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, status, corpo, cabecalhos=None):
            dados = json.dumps(corpo).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for k, v in (cabecalhos or {}).items():
                self.send_header(k, v)
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)

        def do_POST(self):
            tamanho = int(self.headers.get("Content-Length", 0))
            corpo = self.rfile.read(tamanho)
            caminho = urlparse(self.path).path
            if caminho.endswith("/oauth2/v2.0/token"):
                dados = {k: v[0] for k, v in parse_qs(corpo.decode()).items()}
                estado.tokens_pedidos.append(dados)
                if dados.get("client_secret") != "segredo-certo":
                    return self._json(401, {"error": "invalid_client",
                                            "error_description": "AADSTS7000215: Invalid client secret provided."})
                if dados["grant_type"] == "client_credentials":
                    return self._json(200, {"access_token": jwt({"roles": ["Mail.Send"], "tid": TENANT}),
                                            "expires_in": 3599, "token_type": "Bearer"})
                if dados["grant_type"] == "authorization_code":
                    assert dados["code_verifier"] and dados["code"] == "codigo-ms"
                    return self._json(200, {"access_token": jwt({"scp": "Mail.Send Mail.Send.Shared User.Read"}),
                                            "refresh_token": "rt-1", "expires_in": 3599,
                                            "id_token": jwt({"preferred_username": "ti.celulares@empresa.com.br"})})
                if dados["grant_type"] == "refresh_token":
                    return self._json(200, {"access_token": jwt({"scp": "Mail.Send"}), "refresh_token": "rt-2",
                                            "expires_in": 3599})
                return self._json(400, {"error": "unsupported_grant_type"})
            if caminho.endswith("/sendMail"):
                if estado.cenario == "403":
                    return self._json(403, {"error": {"code": "ErrorAccessDenied", "message": "Access is denied."}})
                if estado.cenario == "429" and estado.falhas_429 < 1:
                    estado.falhas_429 += 1
                    return self._json(429, {"error": {"code": "TooManyRequests"}}, {"Retry-After": "0"})
                estado.mensagens.append({"caminho": caminho, "autorizacao": self.headers.get("Authorization"),
                                         "corpo": json.loads(corpo)})
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return None
            return self._json(404, {"error": {"code": "NotFound"}})

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Manipulador)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor


@pytest.fixture
def microsoft(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    estado = Microsoft()
    servidor = criar_servidor(estado)
    estado.url = f"http://127.0.0.1:{servidor.server_address[1]}"
    yield estado
    servidor.shutdown()


@pytest.fixture
def app_graph(tmp_path, microsoft):
    from app import create_app
    from app.servicos import graph
    graph.limpar_cache()
    return create_app({"PASTA_DADOS": tmp_path / "dados", "TESTING": True, "AGENDADOR_ATIVO": False,
                       "GRAPH_AUTORIDADE": microsoft.url, "GRAPH_API": f"{microsoft.url}/v1.0"})


@pytest.fixture
def admin_graph(app_graph):
    nav = Navegador(app_graph.test_client())
    r = nav.post("/instalacao", {"codigo": app_graph.config["CODIGO_INSTALACAO"], "empresa_nome": "Grupo Graph",
                                 "url_base": "https://celulares.empresa.com.br", "nome": "Ana Admin",
                                 "email": "ti@empresa.com.br", "senha": "Senha-forte-1", "confirmar": "Senha-forte-1"})
    assert r.status_code == 302
    return nav


def configurar(nav, **extra):
    dados = {"acao": "salvar", "email_modo": "graph", "graph_autenticacao": "aplicativo", "graph_tenant_id": TENANT,
             "graph_client_id": CLIENTE, "graph_client_secret": "segredo-certo",
             "graph_remetente": "celulares@empresa.com.br"}
    dados.update(extra)
    return nav.post("/configuracoes/integracao", dados)


def test_validacao_da_configuracao(admin_graph):
    r = admin_graph.post("/configuracoes/integracao", {"acao": "salvar", "email_modo": "graph",
                                                       "graph_autenticacao": "aplicativo", "graph_tenant_id": "x y",
                                                       "graph_client_id": "123"})
    texto = r.get_data(as_text=True)
    assert "ID do aplicativo é um GUID" in texto and "Informe o segredo do cliente" in texto


def test_segredo_errado_mostra_mensagem_clara(app_graph, admin_graph, microsoft):
    configurar(admin_graph, graph_client_secret="segredo-errado")
    r = admin_graph.post("/configuracoes/integracao", {"acao": "testar", "destino_teste": ""})
    texto = r.get_data(as_text=True)
    assert "segredo do cliente está incorreto" in texto and "AADSTS7000215" in texto
    with app_graph.app_context():
        from app.servicos.configuracoes import cfg
        assert cfg("graph_status") == "erro"


def test_envio_real_pelo_graph(app_graph, admin_graph, microsoft):
    assert configurar(admin_graph).status_code == 302
    with app_graph.app_context():
        from app.db import consultar
        guardado = consultar("SELECT valor FROM configuracoes WHERE chave = 'graph_client_secret'", um=True)["valor"]
    assert guardado and "segredo-certo" not in guardado  # segredo cifrado no banco
    assert "segredo-certo" not in admin_graph.get("/configuracoes/integracao").get_data(as_text=True)

    r = admin_graph.post("/configuracoes/integracao", {"acao": "testar", "destino_teste": "ana@empresa.com.br"})
    texto = r.get_data(as_text=True)
    assert "Autenticação no Microsoft Entra ID realizada" in texto and "Mail.Send (aplicativo) presente" in texto
    assert "E-mail de teste enviado" in texto
    teste = microsoft.mensagens[-1]
    assert teste["caminho"] == "/v1.0/users/celulares@empresa.com.br/sendMail"
    assert teste["autorizacao"].startswith("Bearer ")
    assert teste["corpo"]["message"]["toRecipients"][0]["emailAddress"]["address"] == "ana@empresa.com.br"
    assert microsoft.tokens_pedidos[-1]["scope"] == "https://graph.microsoft.com/.default"

    # fluxo do termo com e-mails reais
    unidade = criar_unidade(admin_graph)
    aparelho = criar_aparelho(admin_graph, unidade)
    r, dados = criar_colaborador(admin_graph, unidade, nome="Nina Graph", aparelho_id=aparelho)
    termo_id = int(r.headers["Location"].split("/")[-1])
    admin_graph.post(f"/termos/{termo_id}/enviar")
    mensagem = microsoft.mensagens[-1]["corpo"]["message"]
    assert mensagem["toRecipients"][0]["emailAddress"]["address"] == dados["email"]
    assert mensagem["body"]["contentType"] == "HTML"
    import re
    token = re.search(r"/assinar/([A-Za-z0-9_-]{20,})", mensagem["body"]["content"]).group(1)
    assert mensagem["body"]["content"].count("https://celulares.empresa.com.br/assinar/") >= 1
    registro = ultimo_email(app_graph, "termo")
    assert registro["status"] == "enviado" and token not in registro["corpo_html"]  # link oculto no histórico

    colaborador = Navegador(app_graph.test_client())
    colaborador.post(f"/assinar/{token}", {"acao": "enviar_codigo", "cpf": dados["cpf"]})
    corpo_codigo = microsoft.mensagens[-1]["corpo"]["message"]["body"]["content"]
    codigo = codigo_do_email({"corpo_html": corpo_codigo})
    assert codigo not in ultimo_email(app_graph, "codigo_assinatura")["corpo_html"]
    r = colaborador.post(f"/assinar/{token}", {"acao": "assinar", "cpf": dados["cpf"], "codigo": codigo,
                                               "nome": "Nina Graph", "tipo_assinatura": "desenho", "tracos": TRACOS,
                                               "aceite": "1"})
    assert r.status_code == 302
    confirmacao = [m for m in microsoft.mensagens if m["corpo"]["message"].get("attachments")]
    assert confirmacao, "a confirmação deve levar o PDF anexo"
    anexo = confirmacao[-1]["corpo"]["message"]["attachments"][0]
    assert anexo["@odata.type"] == "#microsoft.graph.fileAttachment" and anexo["contentType"] == "application/pdf"
    assert base64.b64decode(anexo["contentBytes"])[:4] == b"%PDF"


def test_falhas_do_graph(app_graph, admin_graph, microsoft):
    configurar(admin_graph)
    unidade = criar_unidade(admin_graph)
    aparelho = criar_aparelho(admin_graph, unidade)
    r, _ = criar_colaborador(admin_graph, unidade, aparelho_id=aparelho)
    termo_id = int(r.headers["Location"].split("/")[-1])

    microsoft.cenario = "403"
    r = admin_graph.post(f"/termos/{termo_id}/enviar", follow_redirects=True)
    assert "Acesso negado ao enviar pela caixa celulares@empresa.com.br" in r.get_data(as_text=True)
    registro = ultimo_email(app_graph, "termo")
    assert registro["status"] == "falha" and "Acesso negado" in registro["erro"]
    with app_graph.app_context():
        from app.db import consultar
        assert consultar("SELECT status FROM termos WHERE id = ?", (termo_id,), um=True)["status"] == "gerado"
    assert "com falha" in admin_graph.get("/alertas").get_data(as_text=True)

    microsoft.cenario = "429"  # primeira tentativa recebe 429; o sistema repete e consegue
    admin_graph.post(f"/termos/{termo_id}/enviar")
    registro = ultimo_email(app_graph, "termo")
    assert registro["status"] == "enviado" and registro["tentativas"] == 2


def test_conta_delegada_com_pkce(app_graph, admin_graph, microsoft):
    configurar(admin_graph, graph_autenticacao="delegado", graph_remetente="")
    r = admin_graph.get("/configuracoes/integracao/conectar")
    destino = urlparse(r.headers["Location"])
    parametros = {k: v[0] for k, v in parse_qs(destino.query).items()}
    assert destino.path == f"/{TENANT}/oauth2/v2.0/authorize"
    assert parametros["code_challenge_method"] == "S256" and parametros["client_id"] == CLIENTE
    assert "offline_access" in parametros["scope"] and "Mail.Send" in parametros["scope"]
    assert parametros["redirect_uri"] == "https://celulares.empresa.com.br/configuracoes/integracao/retorno"
    r = admin_graph.get(f"/configuracoes/integracao/retorno?code=codigo-ms&state=estado-falso")
    assert "não confere" in admin_graph.get("/configuracoes/integracao").get_data(as_text=True)
    admin_graph.get("/configuracoes/integracao/conectar")
    with admin_graph.cliente.session_transaction() as sessao:
        estado = sessao["oauth_microsoft"]["estado"]
    r = admin_graph.get(f"/configuracoes/integracao/retorno?code=codigo-ms&state={estado}", follow_redirects=True)
    assert "ti.celulares@empresa.com.br conectada" in r.get_data(as_text=True)
    with app_graph.app_context():
        from app.servicos.configuracoes import cfg, segredo
        assert segredo("graph_refresh_token") == "rt-1" and "rt-1" not in cfg("graph_refresh_token")
        from app.servicos import graph
        graph.limpar_cache()
    r = admin_graph.post("/configuracoes/integracao", {"acao": "testar", "destino_teste": "ana@empresa.com.br"})
    assert "E-mail de teste enviado" in r.get_data(as_text=True)
    assert microsoft.mensagens[-1]["caminho"] == "/v1.0/me/sendMail"
    assert microsoft.tokens_pedidos[-1]["grant_type"] == "refresh_token"
    with app_graph.app_context():
        from app.servicos.configuracoes import segredo
        assert segredo("graph_refresh_token") == "rt-2"  # rotação do refresh token guardada

    r = admin_graph.post("/configuracoes/integracao/desconectar", follow_redirects=True)
    with app_graph.app_context():
        from app.servicos.configuracoes import cfg
        assert cfg("email_modo") == "simulacao" and cfg("graph_client_secret") == "" and cfg("graph_conta") == ""
