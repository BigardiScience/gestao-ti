"""Chamados: login pelo AD (LDAPS), permissões no servidor, fluxo do pedido, prazos e avisos."""
import io
import re
from datetime import date, datetime, time
from types import SimpleNamespace

import pytest

from conftest import Navegador, criar_unidade
from ldap_falso import ControladorFalso, dn

SENHA = "Windows-2026!"
USUARIOS = {
    "maria.silva": {"senha": SENHA, "dn": dn("Maria Silva", "Fiscal", "SP - Sorocaba"), "nome": "Maria Silva",
                    "mail": "maria.silva@empresa.com.br"},
    "joao.souza": {"senha": SENHA, "dn": dn("João Souza", "Produção", "MG - Uberaba"), "nome": "João Souza",
                   "mail": "joao.souza@empresa.com.br"},
    "paulo.reis": {"senha": SENHA, "dn": dn("Paulo Reis", "Expedição", "SP - Santos"), "nome": "Paulo Reis",
                   "mail": "paulo.reis@empresa.com.br"},
    "ana.lima": {"senha": SENHA, "dn": dn("Ana Lima", "Informática", "SP - Sorocaba"), "nome": "Ana Lima",
                 "mail": "ana.lima@empresa.com.br"},
    "bruno.costa": {"senha": SENHA, "dn": dn("Bruno Costa", "Informática", "MG - Uberaba"), "nome": "Bruno Costa",
                    "mail": "bruno.costa@empresa.com.br"},
    "rita.gomes": {"senha": SENHA, "dn": dn("Rita Gomes", "Qualidade", "PE - Recife", "Nordeste"),
                    "nome": "Rita Gomes", "mail": "rita@filial.com.br"},
    "carla.bahia": {"senha": SENHA, "dn": dn("Carla Bahia", "Compras", "BA - Camaçari"), "nome": "Carla Bahia"},
    "conta.desligada": {"senha": SENHA, "dn": dn("Conta Desligada", "Fiscal", "SP - Sorocaba"),
                        "nome": "Conta Desligada", "uac": "514"},
    "travado.ad": {"senha": SENHA, "dn": dn("Travado AD", "Fiscal", "SP - Sorocaba"), "nome": "Travado AD",
                   "bloqueado_ad": True},
}
LINK_SOR = "https://prod-11.brazilsouth.logic.azure.com/workflows/sorocaba/triggers/manual/paths/invoke?sig=a"
LINK_UBE = "https://prod-12.brazilsouth.logic.azure.com/workflows/uberaba/triggers/manual/paths/invoke?sig=b"
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
       b"\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82")


def sql(app, consulta, parametros=(), um=True):
    with app.app_context():
        from app.db import consultar
        return consultar(consulta, parametros, um=um)


def executar_sql(app, comando, parametros=()):
    with app.app_context():
        from app.db import executar
        executar(comando, parametros)


def emails(app, finalidade=None):
    linhas = sql(app, "SELECT destinatario, assunto, finalidade FROM emails ORDER BY id", um=False)
    return [l for l in linhas if not finalidade or l["finalidade"] == finalidade]


def entrar(app, usuario, senha=SENHA, ip="172.16.160.40"):
    nav = Navegador(app.test_client())
    nav.cliente.environ_base["REMOTE_ADDR"] = ip
    resposta = nav.post("/entrar", {"usuario": usuario, "senha": senha})
    return nav, resposta


def editar_unidade(admin, unidade_id, nome, cidade, uf, ou, ativo_chamados=True, link=None, tambem=()):
    dados = {"nome": nome, "cidade": cidade, "uf": uf, "ativo": "1", "ou_ad": ou,
             "chamados_ativo": "1" if ativo_chamados else ""}
    if link:
        dados["teams_link"] = link
    if tambem:
        dados["teams_tambem"] = [str(t) for t in tambem]
    r = admin.post(f"/unidades/{unidade_id}/editar", dados)
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]


def configurar_ad(admin, dc, servidores=None, ca=True):
    r = admin.post("/configuracoes/ad", {
        "ad_ativo": "1", "ad_dominio": "empresa.local",
        "ad_servidores": servidores or f"localhost:{dc.porta}", "ad_porta": "636", "ad_verificar_certificado": "1",
        "ad_ca_pem": dc.ca_pem if ca else "", "ad_timeout": "5"})
    assert r.status_code == 302, r.get_data(as_text=True)[:3000]


@pytest.fixture
def amb(app, admin, monkeypatch):
    from app.seguranca import limitador
    limitador._eventos.clear()  # o limite de pedidos por hora não passa de um teste para o outro
    enviados = []
    monkeypatch.setattr("app.servicos.teams.postar", lambda link, mensagem: enviados.append((link, mensagem)))
    sor = criar_unidade(admin, "Sorocaba")
    ube = criar_unidade(admin, "Uberaba", cidade="Uberaba", uf="MG")
    sts = criar_unidade(admin, "Santos", cidade="Santos")
    nde = criar_unidade(admin, "Nordeste", cidade="Recife", uf="PE")
    editar_unidade(admin, sor, "Sorocaba", "Sorocaba", "SP", "SP - Sorocaba", link=LINK_SOR)
    editar_unidade(admin, ube, "Uberaba", "Uberaba", "MG", "MG - Uberaba", link=LINK_UBE)
    editar_unidade(admin, sts, "Santos", "Santos", "SP", "SP - Santos", tambem=(sor, ube))
    editar_unidade(admin, nde, "Nordeste", "Recife", "PE", "PE - Recife", ativo_chamados=False)
    with ControladorFalso(USUARIOS) as dc:
        configurar_ad(admin, dc)
        for login, nome, unidades in (("ana.lima", "Ana Lima", (sor, sts)), ("bruno.costa", "Bruno Costa", (ube, sts))):
            r = admin.post("/configuracoes/usuarios/novo", {"origem": "ad", "login_ad": login, "nome": nome,
                                                            "perfil": "tecnico", "unidades_tecnico": [str(u) for u in unidades]})
            assert r.status_code == 302, r.get_data(as_text=True)[:2000]
        yield SimpleNamespace(app=app, admin=admin, dc=dc, teams=enviados, sor=sor, ube=ube, sts=sts, nde=nde)


def abrir_pedido(nav, categoria="impressora", descricao="A impressora do fiscal puxa duas folhas.", impacto="pouco",
                 **extra):
    dados = {"categoria": categoria, "descricao": descricao, "impacto": impacto, "enviar": "1"}
    dados.update(extra)
    r = nav.post("/pedidos/novo", dados, content_type="multipart/form-data")
    assert r.status_code == 302 and "/enviado" in r.headers["Location"], r.get_data(as_text=True)[:3000]
    return int(re.search(r"/pedidos/(\d+)/enviado", r.headers["Location"]).group(1))


# ------------------------------------------------------------------ prazos em horário útil

def test_expediente_e_feriados(app):
    with app.app_context():
        from app.servicos import expediente
        from app.util import fuso
        exp = expediente.Expediente(dias=frozenset({1, 2, 3, 4, 5}), inicio=time(8), fim=time(18))
        tz = fuso()
        segunda = datetime(2026, 11, 16, 10, 0, tzinfo=tz)
        assert exp.somar_minutos(segunda, 240) == datetime(2026, 11, 16, 14, 0, tzinfo=tz)
        sexta = datetime(2026, 11, 13, 17, 0, tzinfo=tz)
        assert exp.somar_minutos(sexta, 240) == datetime(2026, 11, 16, 11, 0, tzinfo=tz)
        quinta = datetime(2026, 11, 19, 16, 0, tzinfo=tz)  # sexta 20/11 é feriado nacional
        assert exp.somar_minutos(quinta, 240) == datetime(2026, 11, 23, 10, 0, tzinfo=tz)
        assert exp.somar_minutos(segunda, exp.duracao_em_minutos("1d")) == datetime(2026, 11, 17, 10, 0, tzinfo=tz)
        assert exp.somar_minutos(segunda, exp.duracao_em_minutos("3d")) == datetime(2026, 11, 19, 10, 0, tzinfo=tz)
        sabado = datetime(2026, 11, 14, 9, 0, tzinfo=tz)
        assert exp.somar_minutos(sabado, 60) == datetime(2026, 11, 16, 9, 0, tzinfo=tz)
        assert exp.minutos_entre(sexta, datetime(2026, 11, 16, 11, 0, tzinfo=tz)) == 240
        assert exp.minutos_entre(datetime(2026, 11, 16, 11, 0, tzinfo=tz), sexta) == -240
        assert expediente.pascoa(2026) == date(2026, 4, 5)
        feriados = expediente.feriados_do_ano(2026)
        for dia in (date(2026, 4, 3), date(2026, 2, 16), date(2026, 2, 17), date(2026, 6, 4), date(2026, 11, 20)):
            assert dia in feriados
        assert date(2026, 2, 17) not in expediente.feriados_do_ano(2026, carnaval=False)
        datas, invalidas = expediente.ler_datas("09/07/2026\n2026-01-25\nontem")
        assert datas == [date(2026, 1, 25), date(2026, 7, 9)] and invalidas == ["ontem"]


# ------------------------------------------------------------------ login pelo AD

def test_login_pelo_ad(amb):
    app = amb.app
    nav, r = entrar(app, "maria.silva", "errada")
    assert r.status_code == 200 and "Usuário ou senha incorretos" in r.get_data(as_text=True)
    for forma in ("maria.silva", "EMPRESA\\maria.silva", "Maria.Silva@empresa.com.br"):
        nav, r = entrar(app, forma)
        assert r.status_code == 302 and r.headers["Location"].endswith("/pedidos/novo"), forma
    maria = sql(app, "SELECT * FROM usuarios WHERE login_ad = 'maria.silva'")
    assert maria["perfil"] == "colaborador" and maria["origem"] == "ad" and maria["setor"] == "Fiscal"
    assert maria["unidade_id"] == amb.sor and maria["email"] == "maria.silva@empresa.com.br"
    assert maria["senha_hash"] is None  # a senha do AD nunca é guardada
    assert "maria.silva@empresa.local" in amb.dc.binds

    # a senha do Windows só passa por HTTPS (localhost liberado para testes)
    from app.rotas.auth import conexao_segura
    for endereco, seguro in (("http://ti.empresa.local", False), ("https://ti.empresa.local", True),
                             ("http://localhost:8000", True), ("http://172.16.160.20", False)):
        with app.test_request_context("/entrar", base_url=endereco):
            assert conexao_segura() is seguro, endereco

    # técnico pré-cadastrado entra já como técnico e cai na fila
    nav, r = entrar(app, "ana.lima")
    assert r.status_code == 302 and r.headers["Location"].endswith("/chamados")
    assert sql(app, "SELECT perfil FROM usuarios WHERE login_ad = 'ana.lima'")["perfil"] == "tecnico"

    # unidade inativa no AD, conta desativada, conta bloqueada no AD e unidade com pedidos desligados
    for login, trecho in (("carla.bahia", "ainda não usa este sistema"), ("conta.desligada", "desativado no AD"),
                          ("travado.ad", "bloqueado no AD"), ("rita.gomes", "ainda não usa este sistema")):
        nav, r = entrar(app, login)
        assert r.status_code == 200 and trecho in r.get_data(as_text=True), login

    # desativado no sistema pelo administrador: o AD aceita a senha, mas o sistema não deixa entrar
    r = amb.admin.post(f"/configuracoes/usuarios/{maria['id']}/editar",
                       {"nome": "Maria Silva", "login_ad": "maria.silva", "perfil": "colaborador", "ativo": ""})
    assert r.status_code == 302, r.get_data(as_text=True)[:1500]
    nav, r = entrar(app, "maria.silva")
    assert r.status_code == 200 and "desativado no sistema" in r.get_data(as_text=True)

    # 5 senhas erradas seguidas bloqueiam, mesmo com a senha certa depois
    for _ in range(5):
        entrar(app, "joao.souza", "errada")
    nav, r = entrar(app, "joao.souza")
    assert r.status_code == 200 and "bloqueado por excesso" in r.get_data(as_text=True)


def test_ldaps_reserva_e_certificado(amb):
    app = amb.app
    # controlador principal fora do ar: usa a reserva
    configurar_ad(amb.admin, amb.dc, servidores=f"localhost:1\nlocalhost:{amb.dc.porta}")
    nav, r = entrar(app, "paulo.reis")
    assert r.status_code == 302
    # sem a CA cadastrada o certificado não é confiável: não entra (nunca manda a senha sem TLS válido)
    configurar_ad(amb.admin, amb.dc, ca=False)
    binds_antes = len(amb.dc.binds)
    nav, r = entrar(app, "paulo.reis")
    assert r.status_code == 200 and "Não foi possível falar com o AD" in r.get_data(as_text=True)
    assert len(amb.dc.binds) == binds_antes
    r = amb.admin.post("/configuracoes/ad/testar", {"teste_login": "paulo.reis", "teste_senha": SENHA})
    assert "não é confiável" in r.get_data(as_text=True)
    configurar_ad(amb.admin, amb.dc)
    r = amb.admin.post("/configuracoes/ad/testar", {"teste_login": "paulo.reis", "teste_senha": SENHA})
    texto = r.get_data(as_text=True)
    assert "SP - Santos" in texto and "Expedição" in texto
    # porta 389 (LDAP sem criptografia) é recusada
    r = amb.admin.post("/configuracoes/ad", {"ad_ativo": "1", "ad_dominio": "empresa.local",
                                             "ad_servidores": "localhost", "ad_porta": "389", "ad_timeout": "5",
                                             "ad_verificar_certificado": "1"})
    assert r.status_code == 200 and "LDAPS" in r.get_data(as_text=True)


def test_colaborador_nao_entra_na_ti_e_conta_de_emergencia(amb):
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    for url in ("/", "/computadores", "/chamados", "/aparelhos", "/unidades"):
        r = maria.get(url)
        assert r.status_code == 302 and "/pedidos/novo" in r.headers["Location"], url
    assert maria.get("/configuracoes/usuarios").status_code in (302, 403)
    assert maria.post("/computadores/novo", {"nome": "X"}).status_code == 403
    assert maria.post("/chamados/1/assumir").status_code in (302, 403, 404)
    assert maria.get("/chamados/contador.json").status_code in (302, 403)
    assert maria.get("/pedidos/novo").status_code == 200
    # conta local com o AD ligado: entra, mas fica registrado e gera aviso
    nav = Navegador(app.test_client())
    r = nav.post("/entrar", {"usuario": "ti@empresa.com.br", "senha": "Senha-forte-123"})
    assert r.status_code == 302
    assert any("emergência" in e["assunto"] for e in emails(app))


# ------------------------------------------------------------------ fluxo completo

def test_pedido_do_colaborador_ate_resolver(amb):
    app = amb.app
    admin = amb.admin
    r = admin.post("/computadores/novo", {"nome": "SOR-FIS-01", "unidade_id": amb.sor, "tipo": "desktop",
                                          "situacao": "em_uso", "usuario_nome": "Maria Silva", "usuario_login": "maria.silva",
                                          "setor": "Fiscal", "usuario_situacao": "ativo", "ip": "172.16.160.25", "sep": "ok"})
    assert r.status_code == 302, r.get_data(as_text=True)[:2000]
    pc = sql(app, "SELECT id FROM computadores WHERE nome = 'SOR-FIS-01'")["id"]

    maria, _ = entrar(app, "maria.silva", ip="172.16.160.25")
    pagina = maria.get("/pedidos/novo").get_data(as_text=True)
    assert "O problema é neste computador?" in pagina and "172.16.160.25" not in pagina  # nada de IP na tela

    # sem JavaScript: cada botão leva ao próximo passo sem perder nada
    r = maria.post("/pedidos/novo", {"escolher_categoria": "computador"})
    assert 'data-passo="2"' in r.get_data(as_text=True) and "Passo 2 de 3" in r.get_data(as_text=True)
    r = maria.post("/pedidos/novo", {"categoria": "computador", "escolher_equipamento": "origem"})
    assert "Passo 3 de 3" in r.get_data(as_text=True)
    r = maria.post("/pedidos/novo", {"categoria": "computador", "equipamento": "origem", "enviar": "1",
                                     "descricao": "Travou tudo", "impacto": ""})
    assert r.status_code == 200 and "impedindo o seu trabalho" in r.get_data(as_text=True)

    chamado_id = abrir_pedido(maria, categoria="computador", equipamento="origem", impacto="parou",
                              descricao="O computador desliga sozinho depois de uns minutos.",
                              anexo=(io.BytesIO(PNG), "tela.png"))
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["prioridade"] == "alta" and c["situacao"] == "novo" and c["unidade_id"] == amb.sor
    assert c["computador_id"] == pc and c["origem_computador_id"] == pc and c["origem_ip"] == "172.16.160.25"
    assert c["prazo_em"] > c["criado_em"]
    assert [link for link, _ in amb.teams] == [LINK_SOR]
    cartao = amb.teams[0][1]["attachments"][0]["content"]
    assert cartao["type"] == "AdaptiveCard" and cartao["actions"][0]["title"] == "Abrir chamado"
    assert any(e["destinatario"] == "maria.silva@empresa.com.br" and "Recebemos" in e["assunto"] for e in emails(app))
    assert "Pronto! Seu pedido foi enviado." in maria.get(f"/pedidos/{chamado_id}/enviado").get_data(as_text=True)
    anexo = sql(app, "SELECT id FROM chamados_anexos WHERE chamado_id = ?", (chamado_id,))["id"]

    ana, _ = entrar(app, "ana.lima")
    bruno, _ = entrar(app, "bruno.costa")
    assert ana.get("/chamados/contador.json").get_json()["novos"] == 1
    assert bruno.get("/chamados/contador.json").get_json()["novos"] == 0  # não é da unidade dele
    assert "O computador desliga sozinho" in ana.get("/chamados").get_data(as_text=True)

    # técnico de outra unidade: lê e escreve nota interna, mas não altera nada (nem por requisição direta)
    pagina = bruno.get(f"/chamados/{chamado_id}").get_data(as_text=True)
    assert "Só leitura" in pagina
    for url, dados in ((f"/chamados/{chamado_id}/assumir", {}), (f"/chamados/{chamado_id}/resolver", {}),
                       (f"/chamados/{chamado_id}/mensagem", {"tipo": "resposta", "texto": "Oi"}),
                       (f"/chamados/{chamado_id}/alterar", {"prioridade": "baixa"}),
                       (f"/chamados/{chamado_id}/transferir", {"unidade_id": amb.ube, "motivo": "Teste de rota"})):
        assert bruno.post(url, dados).status_code == 403, url
    assert bruno.get(f"/chamados/{chamado_id}/transferir").status_code == 403
    assert bruno.post(f"/chamados/{chamado_id}/mensagem", {"tipo": "nota", "texto": "Já vi isso: é a fonte."}).status_code == 302
    assert bruno.get(f"/chamados/anexo/{anexo}").status_code == 200

    # atendimento
    assert ana.post(f"/chamados/{chamado_id}/assumir").status_code == 302
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (chamado_id,))["situacao"] == "atendimento"
    assert any("assumido por Ana Lima" in str(m) for _, m in amb.teams)
    ana.post(f"/chamados/{chamado_id}/mensagem", {"tipo": "resposta", "texto": "A luz da fonte fica laranja?",
                                                  "aguardar": "1"})
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["situacao"] == "aguardando" and c["pausado_em"]
    assert any("precisa de uma resposta" in e["assunto"] for e in emails(app))

    # o colaborador vê a resposta, mas nunca a nota interna nem a situação técnica
    pagina = maria.get(f"/pedidos/{chamado_id}").get_data(as_text=True)
    assert "A luz da fonte fica laranja?" in pagina and "é a fonte" not in pagina
    assert "A TI precisa de uma resposta sua" in pagina
    assert maria.get(f"/pedidos/anexo/{anexo}").status_code == 200
    joao, _ = entrar(app, "joao.souza")
    assert joao.get(f"/pedidos/{chamado_id}").status_code == 404
    assert joao.get(f"/pedidos/anexo/{anexo}").status_code == 404

    maria.post(f"/pedidos/{chamado_id}/responder", {"texto": "Fica sim, piscando."})
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["situacao"] == "atendimento" and c["pausado_em"] is None and c["pausa_total_min"] >= 0
    assert any(e["destinatario"] == "ana.lima@empresa.com.br" and "respondeu" in e["assunto"] for e in emails(app))

    r = maria.post(f"/pedidos/{chamado_id}/responder", {"texto": "print", "anexo": (io.BytesIO(b"texto qualquer"), "nota.txt")},
                   content_type="multipart/form-data", follow_redirects=True)
    assert "Envie só foto" in r.get_data(as_text=True)
    assert sql(app, "SELECT COUNT(*) AS n FROM chamados_anexos WHERE chamado_id = ?", (chamado_id,))["n"] == 1

    assert ana.post(f"/chamados/{chamado_id}/resolver").status_code == 302
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (chamado_id,))["situacao"] == "resolvido"
    assert any("foi resolvido" in e["assunto"] for e in emails(app))
    assert "Ainda com problema?" in maria.get("/pedidos").get_data(as_text=True)
    maria.post(f"/pedidos/{chamado_id}/responder", {"texto": "Voltou a desligar."})
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (chamado_id,))["situacao"] == "atendimento"

    # alterações ficam no histórico e na auditoria
    ana.post(f"/chamados/{chamado_id}/alterar", {"prioridade": "media", "categoria": "computador",
                                                 "responsavel_id": "", "assunto": "PC do fiscal desligando"})
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["prioridade"] == "media" and c["responsavel_id"] is None and c["assunto"] == "PC do fiscal desligando"
    acoes = {l["acao"] for l in sql(app, "SELECT acao FROM auditoria WHERE entidade = 'chamado'", um=False)}
    assert {"chamado_aberto", "chamado_assumido", "chamado_respondido", "chamado_nota", "chamado_situacao",
            "chamado_alterado", "chamado_reaberto"} <= acoes
    # ficha do computador lista o chamado
    assert "PC do fiscal desligando" in admin.get(f"/computadores/{pc}").get_data(as_text=True)


def test_assistente_sempre_em_tres_passos(amb):
    """O caso do teste real: pessoa sem computador no inventário, abrindo de uma máquina não reconhecida."""
    app = amb.app
    maria, _ = entrar(app, "maria.silva", ip="10.9.9.9")
    maria.cliente.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/131.0 Safari/537.36"

    pagina = maria.get("/pedidos/novo").get_data(as_text=True)
    assert "Passo 1 de 3" in pagina and " de 2" not in pagina

    # Computador: o passo 2 aparece sempre, igual ao protótipo
    r = maria.post("/pedidos/novo", {"escolher_categoria": "computador"}).get_data(as_text=True)
    assert "Passo 2 de 3" in r and "O problema é neste computador?" in r
    assert "Sim, neste" in r and "Não, é em outro" in r and "10.9.9.9" not in r
    r = maria.post("/pedidos/novo", {"categoria": "computador", "escolher_equipamento": "outro"}).get_data(as_text=True)
    assert "Passo 2 de 3" in r and "Qual é o computador?" in r
    assert "Meu computador da mesa (Fiscal)" in r and "Meu notebook" in r and "Não sei qual é" in r
    r = maria.post("/pedidos/novo", {"categoria": "computador", "escolher_equipamento": "este"}).get_data(as_text=True)
    assert "Passo 3 de 3" in r and 'name="ir" value="2"' in r  # "Voltar" do passo 3 volta para o 2

    pedido = abrir_pedido(maria, categoria="computador", equipamento="este", impacto="parou",
                          descricao="Não liga desde cedo.")
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (pedido,))
    assert c["computador_id"] is None and c["origem_ip"] == "10.9.9.9" and "Neste computador" in c["equipamento_texto"]
    assert f">{pedido}<" in maria.get(f"/pedidos/{pedido}/enviado").get_data(as_text=True)  # número sem zeros à esquerda
    ana, _ = entrar(app, "ana.lima")
    tela = ana.get(f"/chamados/{pedido}").get_data(as_text=True)
    assert "Neste computador" in tela and "10.9.9.9" in tela

    pedido = abrir_pedido(maria, categoria="computador", equipamento="pc:mesa", descricao="O da mesa está lento.")
    assert "computador de mesa da pessoa" in sql(app, "SELECT equipamento_texto FROM chamados WHERE id = ?", (pedido,))["equipamento_texto"]

    # categorias sem equipamento vão do passo 1 direto ao 3, sem mudar o total
    r = maria.post("/pedidos/novo", {"escolher_categoria": "impressora"}).get_data(as_text=True)
    assert "Passo 3 de 3" in r and " de 2" not in r and 'name="ir" value="1"' in r

    # Celular: sem aparelho no cadastro, a lista oferece "o celular da empresa que está comigo"
    r = maria.post("/pedidos/novo", {"escolher_categoria": "celular"}).get_data(as_text=True)
    assert "Passo 2 de 3" in r and "O celular da empresa que está comigo" in r and "Não sei qual é" in r

    # no navegador do celular, "neste computador" não faz sentido: vai direto para a lista
    maria.cliente.environ_base["HTTP_USER_AGENT"] = "Mozilla/5.0 (Linux; Android 14) Chrome/131.0 Mobile Safari/537.36"
    r = maria.post("/pedidos/novo", {"escolher_categoria": "computador"}).get_data(as_text=True)
    assert "Passo 2 de 3" in r and "Qual computador está com problema?" in r and "Sim, neste" not in r


def test_painel_atendimento_muda_situacao(amb):
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    pedido = abrir_pedido(maria, descricao="A impressora do corredor não puxa o papel.")
    ana, _ = entrar(app, "ana.lima")

    fila = ana.get("/chamados").get_data(as_text=True)
    assert f"#{pedido}" in fila and "Sem responsável" in fila and "Média" in fila

    # Novo -> Em atendimento pelo painel: quem mudou vira responsável
    assert ana.post(f"/chamados/{pedido}/alterar", {"situacao": "atendimento", "prioridade": "media",
                                                    "categoria": "impressora", "responsavel_id": ""}).status_code == 302
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (pedido,))
    assert c["situacao"] == "atendimento" and c["responsavel_id"] is not None

    # "Aguardando usuário" só depois de a TI perguntar algo (senão a pessoa não sabe o que responder)
    r = ana.post(f"/chamados/{pedido}/alterar", {"situacao": "aguardando"}, follow_redirects=True)
    assert "escreva a pergunta" in r.get_data(as_text=True)
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (pedido,))["situacao"] == "atendimento"
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "É a impressora HP do corredor?"})
    ana.post(f"/chamados/{pedido}/alterar", {"situacao": "aguardando"})
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (pedido,))
    assert c["situacao"] == "aguardando" and c["pausado_em"]

    # volta a correr o prazo
    ana.post(f"/chamados/{pedido}/alterar", {"situacao": "atendimento"})
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (pedido,))
    assert c["situacao"] == "atendimento" and c["pausado_em"] is None

    # resolver e reabrir pelo mesmo campo
    ana.post(f"/chamados/{pedido}/alterar", {"situacao": "resolvido"})
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (pedido,))["situacao"] == "resolvido"
    assert any("foi resolvido" in e["assunto"] for e in emails(app))
    ana.post(f"/chamados/{pedido}/alterar", {"situacao": "atendimento"})
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (pedido,))["situacao"] == "atendimento"
    assert ana.post(f"/chamados/{pedido}/alterar", {"situacao": "fechado"}).status_code == 302
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (pedido,))["situacao"] == "atendimento"

    # técnico de outra unidade não muda nada, nem por requisição direta
    bruno, _ = entrar(app, "bruno.costa")
    assert bruno.post(f"/chamados/{pedido}/alterar", {"situacao": "resolvido"}).status_code == 403

    tela = ana.get(f"/chamados/{pedido}").get_data(as_text=True)
    assert "Responder à Maria" in tela and "Isso está impedindo o trabalho" in tela and "Salvar alterações" in tela


def test_aviso_de_resposta_do_colaborador(amb):
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    pedido = abrir_pedido(maria, descricao="O mouse parou de funcionar.")
    ana, _ = entrar(app, "ana.lima")
    bruno, _ = entrar(app, "bruno.costa")
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "Já trocou a pilha?", "aguardar": "1"})
    assert ana.get("/chamados/contador.json").get_json()["respostas"] == 0

    maria.post(f"/pedidos/{pedido}/responder", {"texto": "Troquei e continua."})
    dados = ana.get("/chamados/contador.json").get_json()
    assert dados["respostas"] == 1 and dados["resposta"]["id"] == pedido and dados["resposta"]["nome"] == "Maria Silva"
    assert bruno.get("/chamados/contador.json").get_json()["respostas"] == 0  # não é dele
    assert "Maria respondeu" in ana.get("/chamados").get_data(as_text=True)
    assert "Maria Silva respondeu o chamado" in ana.get("/alertas").get_data(as_text=True)
    assert f"#{pedido}" in ana.get("/chamados?respostas=1&unidade=todas").get_data(as_text=True)

    bruno.get(f"/chamados/{pedido}")  # outro técnico abrindo não conta como visto
    assert ana.get("/chamados/contador.json").get_json()["respostas"] == 1
    assert "msg--nova" in ana.get(f"/chamados/{pedido}").get_data(as_text=True)  # a mensagem nova vem marcada
    assert ana.get("/chamados/contador.json").get_json()["respostas"] == 0
    assert "msg--nova" not in ana.get(f"/chamados/{pedido}").get_data(as_text=True)
    assert "Maria respondeu" not in ana.get("/chamados").get_data(as_text=True)


def test_conversa_em_tempo_real(amb):
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    pedido = abrir_pedido(maria, descricao="O teclado parou de funcionar.")
    ana, _ = entrar(app, "ana.lima")
    ana.post(f"/chamados/{pedido}/assumir")
    ultima = sql(app, "SELECT MAX(id) AS n FROM chamados_mensagens WHERE chamado_id = ?", (pedido,))["n"]
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "nota", "texto": "Nota que só a TI vê."})
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "Já tentou outra porta USB?"})

    d = maria.get(f"/pedidos/{pedido}/novidades.json?depois={ultima}").get_json()
    assert "outra porta USB" in d["html"] and "só a TI vê" not in d["html"] and d["respondeu"]
    assert maria.get(f"/pedidos/{pedido}/novidades.json?depois={d['ultima']}").get_json()["html"] == ""
    joao, _ = entrar(app, "joao.souza")
    assert joao.get(f"/pedidos/{pedido}/novidades.json").status_code == 404

    antes = d["ultima"]
    maria.post(f"/pedidos/{pedido}/responder", {"texto": "Tentei, continua igual."})
    assert ana.get("/chamados/contador.json").get_json()["respostas"] == 1
    d = ana.get(f"/chamados/{pedido}/novidades.json?depois={antes}").get_json()
    assert "continua igual" in d["html"] and d["respondeu"] and d["nome"] == "Maria Silva"
    assert ana.get("/chamados/contador.json").get_json()["respostas"] == 0  # apareceu na tela: já foi vista
    assert maria.get(f"/chamados/{pedido}/novidades.json").status_code in (302, 403)


def test_selo_de_respostas_novas_do_colaborador(amb):
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    pedido = abrir_pedido(maria, descricao="O monitor ficou azul.")
    ana, _ = entrar(app, "ana.lima")
    assert maria.get("/pedidos/nao-lidas.json").get_json()["total"] == 0
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "Qual monitor?"})
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "nota", "texto": "Interno."})  # nota não conta
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "E desde quando?"})
    d = maria.get("/pedidos/nao-lidas.json").get_json()
    assert d["total"] == 2 and d["pedidos"][str(pedido)] == 2
    lista = maria.get("/pedidos").get_data(as_text=True)
    assert f'data-novas-pedido="{pedido}"' in lista and ">2<" in lista and "2 respostas novas da TI" in lista
    maria.get(f"/pedidos/{pedido}")  # abriu: viu
    assert maria.get("/pedidos/nao-lidas.json").get_json()["total"] == 0
    ana.post(f"/chamados/{pedido}/mensagem", {"tipo": "resposta", "texto": "Consegue mandar uma foto?"})
    assert maria.get("/pedidos/nao-lidas.json").get_json()["total"] == 1
    maria.get(f"/pedidos/{pedido}/novidades.json?depois=0")  # apareceu na tela aberta
    assert maria.get("/pedidos/nao-lidas.json").get_json()["total"] == 0


def test_relatorio_mensal_no_prazo(amb):
    import io
    from openpyxl import load_workbook
    app = amb.app
    maria, _ = entrar(app, "maria.silva")
    a = abrir_pedido(maria, descricao="Primeiro problema do mês.")
    b = abrir_pedido(maria, descricao="Segundo problema do mês.")
    ana, _ = entrar(app, "ana.lima")
    ana.post(f"/chamados/{a}/resolver")
    # b: o prazo já tinha vencido quando a TI resolveu
    executar_sql(app, "UPDATE chamados SET prazo_em = datetime('now', '-2 hours') WHERE id = ?", (b,))
    ana.post(f"/chamados/{b}/resolver")
    assert sql(app, "SELECT resolvido_no_prazo FROM chamados WHERE id = ?", (a,))["resolvido_no_prazo"] == 1
    assert sql(app, "SELECT resolvido_no_prazo FROM chamados WHERE id = ?", (b,))["resolvido_no_prazo"] == 0

    fila = ana.get("/chamados?situacao=concluidos").get_data(as_text=True)
    assert f"#{a}" in fila and "no prazo" in fila and "fora do prazo" in fila
    assert "Resolvidos no mês" in ana.get("/chamados").get_data(as_text=True)

    tela = ana.get("/chamados/relatorio").get_data(as_text=True)
    assert "Dentro do prazo" in tela and "50,0%" in tela and "Segundo problema" in tela
    r = ana.get("/chamados/relatorio.xlsx")
    assert r.status_code == 200
    livro = load_workbook(io.BytesIO(r.data))
    resumo = {l[0]: l[1] for l in livro["Resumo"].iter_rows(min_row=4, max_row=10, values_only=True)}
    assert resumo["Resolvidos no mês"] == 2 and resumo["Resolvidos dentro do prazo"] == 1
    assert resumo["Resolvidos fora do prazo"] == 1
    assert [l[11] for l in livro["Resolvidos"].iter_rows(min_row=2, values_only=True)] == ["Sim", "Não"]

    # reaberto e resolvido de novo: vale a última vez
    ana.post(f"/chamados/{b}/reabrir")
    assert sql(app, "SELECT resolvido_no_prazo FROM chamados WHERE id = ?", (b,))["resolvido_no_prazo"] is None
    assert maria.get("/chamados/relatorio").status_code in (302, 403)


def test_seguranca_da_informacao_restrita(amb):
    app = amb.app
    amb.admin.post("/configuracoes/chamados", {
        "chamados_dias": ["1", "2", "3", "4", "5"], "chamados_inicio": "08:00", "chamados_fim": "18:00",
        "chamados_carnaval": "1", "chamados_corpus_christi": "1", "prazo_alta_n": "4", "prazo_alta_u": "h",
        "prazo_media_n": "1", "prazo_media_u": "d", "prazo_baixa_n": "3", "prazo_baixa_u": "d",
        "chamados_fechar_resolvido_dias": "3", "chamados_fechar_aguardando_dias": "5", "chamados_resumo_ativo": "1",
        "chamados_resumo_hora": "08:00", "chamados_encarregado_email": "dpo@empresa.com.br"})
    maria, _ = entrar(app, "maria.silva")
    chamado_id = abrir_pedido(maria, categoria="seguranca", impacto="",
                              descricao="Recebi um e-mail do banco pedindo minha senha e cliquei no link.")
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["prioridade"] == "alta"
    assert "cliquei no link" not in str(amb.teams[-1][1])  # o cartão do Teams não leva os detalhes
    assert any("segurança da informação" in e["assunto"] and e["destinatario"] == "ti@empresa.com.br" for e in emails(app))

    bruno, _ = entrar(app, "bruno.costa")
    assert bruno.get(f"/chamados/{chamado_id}").status_code == 404
    assert "cliquei no link" not in bruno.get("/chamados?unidade=todas").get_data(as_text=True)
    assert bruno.post(f"/chamados/{chamado_id}/mensagem", {"tipo": "nota", "texto": "x"}).status_code == 404

    ana, _ = entrar(app, "ana.lima")
    assert "cliquei no link" in ana.get(f"/chamados/{chamado_id}").get_data(as_text=True)
    assert sql(app, "SELECT COUNT(*) AS n FROM auditoria WHERE acao = 'chamado_lido' AND entidade_id = ?",
               (chamado_id,))["n"] == 1
    r = ana.post(f"/chamados/{chamado_id}/seguranca", {"seg_conhecido_em": "2026-11-18T09:30",
                                                       "seg_acoes": "Senha trocada e sessões encerradas.",
                                                       "seg_dados_pessoais": "sim"})
    assert r.status_code == 302
    assert any(e["destinatario"] == "dpo@empresa.com.br" and "dado pessoal" in e["assunto"] for e in emails(app))
    assert ana.post(f"/chamados/{chamado_id}/encarregado").status_code == 302
    assert any(e["finalidade"] == "chamado_dpo" for e in emails(app))
    with app.app_context():
        from app.servicos import chamados as svc
        assert svc.prazo_anpd(svc.obter(chamado_id)) == date(2026, 11, 24)  # 20/11 é feriado


def test_santos_transferencia_e_abrir_por_alguem(amb):
    app = amb.app
    paulo, _ = entrar(app, "paulo.reis")
    chamado_id = abrir_pedido(paulo, descricao="O leitor de código de barras parou.", impacto="parou",
                              categoria="outro")
    assert sorted(link for link, _ in amb.teams) == sorted([LINK_SOR, LINK_UBE])  # Santos avisa Sorocaba e Uberaba
    ana, _ = entrar(app, "ana.lima")
    bruno, _ = entrar(app, "bruno.costa")
    assert ana.post(f"/chamados/{chamado_id}/assumir").status_code == 302
    assert "Transferir para" in ana.get(f"/chamados/{chamado_id}/transferir").get_data(as_text=True)
    r = ana.post(f"/chamados/{chamado_id}/transferir", {"unidade_id": amb.ube, "motivo": "Bruno está indo para lá hoje."})
    assert r.status_code == 302
    c = sql(app, "SELECT * FROM chamados WHERE id = ?", (chamado_id,))
    assert c["unidade_id"] == amb.ube and c["responsavel_id"] is None and c["situacao"] == "novo"
    assert amb.teams[-1][0] == LINK_UBE and "transferido" in str(amb.teams[-1][1])
    assert ana.post(f"/chamados/{chamado_id}/assumir").status_code == 403
    assert bruno.post(f"/chamados/{chamado_id}/assumir").status_code == 302

    # abrir em nome de alguém: só nas unidades que atende
    dados = {"login": "nova.pessoa", "nome": "Nova Pessoa", "unidade_id": amb.sor, "canal": "whatsapp",
             "categoria": "acesso", "prioridade": "media", "descricao": "Pediu acesso à pasta do RH.", "assumir": "1"}
    assert bruno.post("/chamados/novo", dados).status_code == 403
    r = ana.post("/chamados/novo", dados)
    assert r.status_code == 302
    novo = sql(app, "SELECT * FROM chamados ORDER BY id DESC LIMIT 1")
    pessoa = sql(app, "SELECT * FROM usuarios WHERE login_ad = 'nova.pessoa'")
    assert novo["solicitante_id"] == pessoa["id"] and pessoa["perfil"] == "colaborador"
    assert novo["situacao"] == "atendimento" and novo["responsavel_id"] == sql(
        app, "SELECT id FROM usuarios WHERE login_ad = 'ana.lima'")["id"]
    assert "WhatsApp" in ana.get(f"/chamados/{novo['id']}").get_data(as_text=True) or \
        "pelo WhatsApp" in ana.get(f"/chamados/{novo['id']}").get_data(as_text=True)


def test_tarefas_automaticas_e_cobertura(amb):
    app = amb.app
    from app.servicos.agendador import executar_pendentes
    maria, _ = entrar(app, "maria.silva")
    parado = abrir_pedido(maria, descricao="A internet caiu no fiscal.", categoria="rede", impacto="pouco")
    longe = "2020-01-06 12:00:00"
    executar_sql(app, "UPDATE chamados SET ultimo_aviso_em = ? WHERE id = ?", (longe, parado))
    executar_pendentes(app, forcar=["chamados"])
    assert sql(app, "SELECT avisos_enviados FROM chamados WHERE id = ?", (parado,))["avisos_enviados"] == 2
    assert "Lembrete" in str(amb.teams[-1][1])
    executar_sql(app, "UPDATE chamados SET ultimo_aviso_em = ? WHERE id = ?", (longe, parado))
    executar_pendentes(app, forcar=["chamados"])
    assert any("Ninguém assumiu" in e["assunto"] for e in emails(app))

    executar_sql(app, "UPDATE chamados SET prazo_em = ? WHERE id = ?", ("2020-01-06 12:00:00", parado))
    executar_pendentes(app, forcar=["chamados"])
    assert sql(app, "SELECT aviso_prazo FROM chamados WHERE id = ?", (parado,))["aviso_prazo"] == "vencido"
    assert any("venceu o prazo" in e["assunto"] for e in emails(app))
    ana, _ = entrar(app, "ana.lima")
    assert "Atrasado" in ana.get("/chamados?situacao=atrasados").get_data(as_text=True)

    resolvido = abrir_pedido(maria, descricao="Mouse quebrado no fiscal.", categoria="outro", impacto="nao")
    ana.post(f"/chamados/{resolvido}/resolver")
    executar_sql(app, "UPDATE chamados SET resolvido_em = ? WHERE id = ?", (longe, resolvido))
    aguardando = abrir_pedido(maria, descricao="Preciso instalar o leitor de PDF.", categoria="sistemas", impacto="nao")
    ana.post(f"/chamados/{aguardando}/mensagem", {"tipo": "resposta", "texto": "Qual máquina?", "aguardar": "1"})
    executar_sql(app, "UPDATE chamados SET pausado_em = ? WHERE id = ?", (longe, aguardando))
    executar_pendentes(app, forcar=["chamados"])
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (resolvido,))["situacao"] == "fechado"
    assert sql(app, "SELECT situacao FROM chamados WHERE id = ?", (aguardando,))["situacao"] == "fechado"
    assert any("Encerramos" in e["assunto"] for e in emails(app))
    assert "Encerrado" in maria.get("/pedidos").get_data(as_text=True)

    # cobertura de férias: Bruno atende Sorocaba até hoje; depois perde o acesso sozinho
    bruno_id = sql(app, "SELECT id FROM usuarios WHERE login_ad = 'bruno.costa'")["id"]
    hoje = datetime.now().strftime("%Y-%m-%d")
    r = amb.admin.post(f"/configuracoes/usuarios/{bruno_id}/cobertura", {"unidade_id": amb.sor, "ate": hoje})
    assert r.status_code == 302
    assert "Cobertura de férias" in amb.admin.get(f"/configuracoes/usuarios/{bruno_id}/editar").get_data(as_text=True)
    bruno, _ = entrar(app, "bruno.costa")
    novo = abrir_pedido(maria, descricao="Teclado falhando.", categoria="outro", impacto="nao")
    assert bruno.post(f"/chamados/{novo}/assumir").status_code == 302
    executar_sql(app, "UPDATE usuarios_unidades SET expira_em = ? WHERE usuario_id = ? AND unidade_id = ?",
                 ("2020-01-01 00:00:00", bruno_id, amb.sor))
    executar_pendentes(app, forcar=["chamados"])
    assert sql(app, "SELECT COUNT(*) AS n FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ?",
               (bruno_id, amb.sor))["n"] == 0
    assert sql(app, "SELECT COUNT(*) AS n FROM auditoria WHERE acao = 'cobertura_expirada'")["n"] == 1
    assert bruno.post(f"/chamados/{novo}/resolver").status_code == 403
    texto = amb.admin.get("/configuracoes/usuarios/acessos").get_data(as_text=True)
    assert "Bruno Costa" in texto and "Cobertura de unidade expirou" in texto

    # resumo das 8h: cada técnico recebe só as unidades que atende
    resultado = dict(executar_pendentes(app, forcar=["chamados_resumo"]))["chamados_resumo"]
    with app.test_request_context("/"):
        from app.servicos import expediente
        from app.util import agora_local
        dia_util = expediente.atual().eh_dia_util(agora_local().date())
    if dia_util:
        resumos = emails(app, "chamados_resumo")
        assert {e["destinatario"] for e in resumos} >= {"ana.lima@empresa.com.br", "ti@empresa.com.br"}, resultado
        assert "bruno.costa@empresa.com.br" not in {e["destinatario"] for e in resumos}  # Uberaba sem chamados abertos
    else:
        assert "não é dia útil" in resultado


def test_consulta_nao_ve_chamados(amb):
    app = amb.app
    r = amb.admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Carlos Consulta",
                                                        "email": "consulta@empresa.com.br", "perfil": "consulta"})
    assert r.status_code == 302
    senha = re.findall(r"Senha temporária: ([a-z0-9-]+)", amb.admin.get("/configuracoes/usuarios").get_data(as_text=True))[-1]
    nav = Navegador(app.test_client())
    assert nav.post("/entrar", {"usuario": "consulta@empresa.com.br", "senha": senha}).status_code == 302
    nav.post("/minha-conta/senha", {"atual": senha, "nova": "Nova-senha-123", "confirmar": "Nova-senha-123"})
    assert nav.get("/chamados").status_code == 403
    assert nav.get("/chamados/1").status_code == 403


def test_usuarios_de_teste_antes_do_ad(app, admin):
    """Antes de ligar o AD, o administrador cria contas locais com nome de usuário para testar tudo."""
    sor = criar_unidade(admin, "Sorocaba")
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Teste Colaborador",
                                                    "login_ad": "teste.colaborador", "perfil": "colaborador"})
    assert r.status_code == 200 and "Selecione a unidade da pessoa" in r.get_data(as_text=True)
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Sem Usuario", "perfil": "consulta"})
    assert r.status_code == 200 and "Informe o usuário" in r.get_data(as_text=True)
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Teste Errado", "login_ad": "Teste Errado",
                                                    "perfil": "consulta"})
    assert "letras minúsculas" in r.get_data(as_text=True)

    def criar(login, perfil, **extra):
        r = admin.post("/configuracoes/usuarios/novo", dict({"origem": "local", "nome": login.replace(".", " ").title(),
                                                             "login_ad": login, "perfil": perfil}, **extra))
        assert r.status_code == 302, r.get_data(as_text=True)[:1500]
        texto = admin.get("/configuracoes/usuarios").get_data(as_text=True)
        assert f"Usuário para entrar: {login}" in texto
        return re.findall(r"Senha temporária: ([a-z0-9-]+)", texto)[-1]

    senha_col = criar("teste.colaborador", "colaborador", unidade_id=sor)
    senha_tec = criar("teste.tecnico", "tecnico", unidades_tecnico=[str(sor)], unidade_id=sor)
    assert "Colaborador de teste" in admin.get("/configuracoes/usuarios?lista=colaboradores").get_data(as_text=True)

    # colaborador de teste: entra pelo usuário, cria a senha na tela simples e já faz pedido
    col = Navegador(app.test_client())
    assert "incorretos" in col.post("/entrar", {"usuario": "teste.colaborador", "senha": "errada-123"}).get_data(as_text=True)
    assert "ainda não foi ligada" in col.post("/entrar", {"usuario": "maria.silva", "senha": "x1"}).get_data(as_text=True)
    assert col.post("/entrar", {"usuario": "Teste.Colaborador", "senha": senha_col}).status_code == 302
    r = col.get("/pedidos/novo")
    assert r.status_code == 302 and r.headers["Location"].endswith("/minha-conta/senha")
    pagina = col.get("/minha-conta/senha").get_data(as_text=True)
    assert "Crie a sua senha" in pagina and "ped-cartao" in pagina  # tela do colaborador, sem o menu da TI
    r = col.post("/minha-conta/senha", {"atual": senha_col, "nova": "Teste-2026x", "confirmar": "Teste-2026x"})
    assert r.status_code == 302 and r.headers["Location"].endswith("/pedidos/novo")
    chamado_id = abrir_pedido(col, descricao="Teste do pedido antes do AD.", impacto="pouco")
    assert sql(app, "SELECT unidade_id FROM chamados WHERE id = ?", (chamado_id,))["unidade_id"] == sor
    assert col.get("/computadores").status_code == 302
    assert "Teste do pedido antes do AD" in admin.get("/chamados?unidade=todas").get_data(as_text=True)

    # técnico de teste cai na fila depois de criar a senha
    tec = Navegador(app.test_client())
    assert tec.post("/entrar", {"usuario": "teste.tecnico", "senha": senha_tec}).status_code == 302
    r = tec.post("/minha-conta/senha", {"atual": senha_tec, "nova": "Tecnico-2026", "confirmar": "Tecnico-2026"})
    assert r.headers["Location"].endswith("/chamados")
    assert tec.post(f"/chamados/{chamado_id}/assumir").status_code == 302

    # uma conta local nunca vira conta do AD por ter o mesmo nome
    with app.test_request_context("/"):
        from app.servicos import ErroNegocio
        from app.servicos.ad import DadosAD, sincronizar
        with pytest.raises(ErroNegocio):
            sincronizar(DadosAD(login="teste.colaborador", nome="Outro", email=None, dn="CN=x", unidade_ou=None,
                                setor=None))
