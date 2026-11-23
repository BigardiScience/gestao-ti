"""Telas administrativas: configurações, edição com histórico, exclusões e usuários."""
import io

from PIL import Image

from conftest import Navegador, criar_aparelho, criar_colaborador, criar_unidade


def consulta(app, sql, parametros=(), um=True):
    with app.app_context():
        from app.db import consultar
        return consultar(sql, parametros, um=um)


def test_configuracoes_gerais_e_logotipo(app, admin):
    imagem = io.BytesIO()
    Image.new("RGB", (400, 120), (255, 199, 44)).save(imagem, "PNG")
    imagem.seek(0)
    r = admin.post("/configuracoes", {"empresa_nome": "Empresa Exemplo", "empresa_cnpj": "11.222.333/0001-81",
                                      "empresa_cidade": "Sorocaba", "ti_email_contato": "ti@empresa.com.br",
                                      "url_base": "https://celulares.empresa.com.br/", "fuso_horario": "America/Sao_Paulo",
                                      "limite_aparelhos_colaborador": "2", "link_validade_dias": "5",
                                      "dias_aparelho_parado": "30", "assinatura_exigir_codigo": "1",
                                      "logo": (imagem, "logo.png")}, content_type="multipart/form-data")
    assert r.status_code == 302
    with app.app_context():
        from app.servicos.configuracoes import cfg
        assert cfg("url_base") == "https://celulares.empresa.com.br" and cfg("logo_arquivo").endswith(".png")
    assert admin.get("/marca/logo.png").status_code == 200
    assert Navegador(app.test_client()).get("/marca/logo.png").status_code == 200  # usado na página pública
    r = admin.post("/configuracoes", {"empresa_nome": "", "url_base": "ftp://x", "empresa_cnpj": "123",
                                      "limite_aparelhos_colaborador": "-1", "link_validade_dias": "0",
                                      "dias_aparelho_parado": "30", "fuso_horario": "America/Sao_Paulo"})
    texto = r.get_data(as_text=True)
    assert "Informe o nome da empresa" in texto and "CNPJ inválido" in texto and "número de 1 a 60" in texto
    # o logotipo entra no PDF do termo
    unidade = criar_unidade(admin)
    aparelho = criar_aparelho(admin, unidade)
    r, _ = criar_colaborador(admin, unidade, aparelho_id=aparelho)
    termo_id = int(r.headers["Location"].split("/")[-1])
    pdf = admin.get(f"/termos/{termo_id}/pdf").data
    assert b"/Subtype /Image" in pdf
    texto_termo = admin.get(f"/termos/{termo_id}").get_data(as_text=True)
    assert "CNPJ 11.222.333/0001-81" in texto_termo and "Sorocaba/SP" in texto_termo


def test_edicao_registra_alteracoes(app, admin):
    unidade = criar_unidade(admin)
    outra = criar_unidade(admin, "Fábrica")
    aparelho = criar_aparelho(admin, unidade)
    dados = consulta(app, "SELECT * FROM aparelhos WHERE id = ?", (aparelho,))
    formulario = {k: dados[k] or "" for k in ("marca", "modelo", "numero_serie", "imei", "imei2", "operadora",
                                               "estado_conservacao", "acessorios", "observacoes", "data_aquisicao")}
    formulario.update({"linha": "(19) 99999-1234", "unidade_id": outra, "valor": "2.000,00"})
    r = admin.post(f"/aparelhos/{aparelho}/editar", formulario)
    assert r.status_code == 302
    pagina = admin.get(f"/aparelhos/{aparelho}").get_data(as_text=True)
    assert "Alteração cadastral" in pagina and "Linha:" in pagina and "Fábrica" in pagina
    assert admin.post(f"/aparelhos/{aparelho}/editar", formulario).status_code == 302  # sem mudanças

    _, colaborador = criar_colaborador(admin, unidade)
    cid = consulta(app, "SELECT id FROM colaboradores WHERE cpf = ?", (colaborador["cpf"],))["id"]
    colaborador.update({"cargo": "Coordenador", "departamento": "Logística"})
    assert admin.post(f"/colaboradores/{cid}/editar", colaborador).status_code == 302
    assert "Cargo:" in admin.get(f"/colaboradores/{cid}").get_data(as_text=True)


def test_exclusoes_e_inativacao(app, admin):
    unidade = criar_unidade(admin)
    vazia = criar_unidade(admin, "Unidade vazia")
    aparelho = criar_aparelho(admin, unidade)
    r = admin.post(f"/unidades/{unidade}/excluir", follow_redirects=True)
    assert "inative-a em vez de excluir" in r.get_data(as_text=True)
    assert admin.post(f"/unidades/{vazia}/excluir").status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM unidades")["n"] == 1

    r, dados = criar_colaborador(admin, unidade, aparelho_id=aparelho)
    r = admin.post(f"/unidades/{unidade}/editar", {"nome": "Matriz", "ativo": ""}, follow_redirects=True)
    assert "em utilização" in r.get_data(as_text=True)

    sobra = criar_aparelho(admin, unidade)
    assert admin.post(f"/aparelhos/{sobra}/excluir").status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM aparelhos WHERE id = ?", (sobra,))["n"] == 0
    r = admin.post(f"/aparelhos/{aparelho}/excluir", follow_redirects=True)
    assert "Baixar" in r.get_data(as_text=True)

    _, solto = criar_colaborador(admin, unidade, nome="Pessoa Sem Aparelho")
    sid = consulta(app, "SELECT id FROM colaboradores WHERE cpf = ?", (solto["cpf"],))["id"]
    admin.post(f"/colaboradores/{sid}/desligar")
    assert consulta(app, "SELECT situacao FROM colaboradores WHERE id = ?", (sid,))["situacao"] == "desligado"
    admin.post(f"/colaboradores/{sid}/reativar")
    assert consulta(app, "SELECT situacao FROM colaboradores WHERE id = ?", (sid,))["situacao"] == "ativo"
    assert admin.post(f"/colaboradores/{sid}/excluir").status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM colaboradores WHERE id = ?", (sid,))["n"] == 0


def test_notificacoes_backup_e_tarefas(app, admin):
    r = admin.post("/configuracoes/notificacoes", {"lembrete_ativo": "1", "lembrete_intervalo_dias": "3",
                                                   "lembrete_maximo": "2", "resumo_diario_ativo": "1",
                                                   "resumo_diario_hora": "07:45",
                                                   "notificacao_emails": "ti@empresa.com.br; gestor@empresa.com.br"})
    assert r.status_code == 302
    with app.app_context():
        from app.servicos.configuracoes import cfg, emails_notificacao
        assert cfg("lembrete_intervalo_dias") == "3"
        assert emails_notificacao() == ["ti@empresa.com.br", "gestor@empresa.com.br"]
    r = admin.post("/configuracoes/notificacoes", {"resumo_diario_hora": "25:00", "lembrete_intervalo_dias": "0",
                                                   "lembrete_maximo": "1", "notificacao_emails": "invalido"})
    texto = r.get_data(as_text=True)
    assert "HH:MM" in texto and "E-mails inválidos" in texto
    assert admin.post("/configuracoes/backup", {"backup_ativo": "1", "backup_hora": "01:15",
                                                "backup_retencao": "3"}).status_code == 302
    for _ in range(2):
        admin.post("/configuracoes/backup/gerar")
    assert "backup-" in admin.get("/configuracoes/backup").get_data(as_text=True)
    r = admin.post("/configuracoes/tarefas/limpeza/executar", follow_redirects=True)
    assert "Tarefa executada" in r.get_data(as_text=True)
    assert "Gerou backup" in admin.get("/configuracoes/auditoria").get_data(as_text=True)


def test_edicao_de_usuario_e_redefinicao_de_senha(app, admin):
    u1 = criar_unidade(admin, "Matriz")
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Rita Consulta", "email": "rita@empresa.com.br",
                                                    "perfil": "consulta", "acesso": "todas"})
    assert r.status_code == 302
    rita = consulta(app, "SELECT id FROM usuarios WHERE email = 'rita@empresa.com.br'")["id"]
    r = admin.post(f"/configuracoes/usuarios/{rita}/editar", {"nome": "Rita Consulta", "email": "rita@empresa.com.br",
                                                              "perfil": "consulta", "acesso": "selecionadas",
                                                              "unidades": [u1], "ativo": "1"})
    assert r.status_code == 302
    assert consulta(app, "SELECT unidade_id FROM usuarios_unidades WHERE usuario_id = ?", (rita,))["unidade_id"] == u1
    r = admin.post(f"/configuracoes/usuarios/{rita}/senha", follow_redirects=True)
    assert "Nova senha temporária" in r.get_data(as_text=True)
    r = admin.post(f"/configuracoes/usuarios/{rita}/editar", {"nome": "Rita Consulta", "email": "rita@empresa.com.br",
                                                              "perfil": "consulta", "acesso": "todas"})
    assert consulta(app, "SELECT ativo FROM usuarios WHERE id = ?", (rita,))["ativo"] == 0
