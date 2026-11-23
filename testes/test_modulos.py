"""Permissões, relatórios Excel, importação, portal do colaborador, tarefas automáticas e backup."""
import io
import re
import zipfile
from datetime import datetime

from openpyxl import Workbook, load_workbook

from conftest import (TRACOS, Navegador, codigo_do_email, criar_aparelho, criar_colaborador, criar_unidade,
                      gerar_cpf, gerar_imei, ultimo_email)


def consulta(app, sql, parametros=(), um=True):
    with app.app_context():
        from app.db import consultar
        return consultar(sql, parametros, um=um)


def entrar(app, email, senha):
    nav = Navegador(app.test_client())
    r = nav.post("/entrar", {"email": email, "senha": senha})
    assert r.status_code == 302, r.get_data(as_text=True)[:500]
    return nav


# ------------------------------------------------------------------ todas as telas abrem

def test_todas_as_telas_abrem(app, admin):
    unidade = criar_unidade(admin)
    aparelho = criar_aparelho(admin, unidade)
    criar_aparelho(admin, unidade)
    r, _ = criar_colaborador(admin, unidade, aparelho_id=aparelho)
    termo_id = int(r.headers["Location"].split("/")[-1])
    vinculo = consulta(app, "SELECT id FROM vinculos")["id"]
    colaborador = consulta(app, "SELECT id FROM colaboradores")["id"]
    admin.post(f"/termos/{termo_id}/enviar")
    email_id = consulta(app, "SELECT id FROM emails")["id"]
    paginas = [
        "/", "/?unidade=1", "/busca?q=samsung", "/alertas", "/aparelhos", "/aparelhos?status=em_uso&termo=pendente",
        "/aparelhos/novo", f"/aparelhos/{aparelho}", f"/aparelhos/{aparelho}/editar", "/aparelhos/importar",
        "/colaboradores", "/colaboradores?situacao=todos", "/colaboradores/novo", f"/colaboradores/{colaborador}",
        f"/colaboradores/{colaborador}/editar", "/colaboradores/importar", "/unidades", "/unidades/nova",
        f"/unidades/{unidade}/editar", "/entregas/nova", f"/vinculos/{vinculo}/devolver",
        f"/vinculos/{vinculo}/transferir", f"/vinculos/{vinculo}/substituir", "/termos", "/termos?status=pendente",
        "/termos?status=sem_termo", f"/termos/{termo_id}", "/movimentacoes", "/relatorios",
        "/relatorios?tipo=termos&previa=1", "/relatorios?tipo=movimentacoes&previa=1",
        "/relatorios?tipo=por_unidade&previa=1", "/configuracoes", "/configuracoes/termo",
        "/configuracoes/notificacoes", "/configuracoes/integracao", "/configuracoes/usuarios",
        "/configuracoes/usuarios/novo", "/configuracoes/usuarios/1/editar", "/configuracoes/emails",
        f"/configuracoes/emails/{email_id}", f"/configuracoes/emails/{email_id}/corpo", "/configuracoes/auditoria",
        "/configuracoes/backup", "/minha-conta/senha", "/verificar", "/portal/entrar", "/saude",
    ]
    for pagina in paginas:
        resposta = admin.get(pagina)
        assert resposta.status_code == 200, f"{pagina}: {resposta.status_code}\n{resposta.get_data(as_text=True)[:800]}"
    assert admin.get("/pagina-que-nao-existe").status_code == 404
    for modelo in ("/aparelhos/modelo-importacao.xlsx", "/colaboradores/modelo-importacao.xlsx"):
        conteudo = admin.get(modelo).data
        assert load_workbook(io.BytesIO(conteudo)).sheetnames[0] in ("Aparelhos", "Colaboradores")


# ------------------------------------------------------------------ permissões

def test_usuario_de_consulta_restrito_por_unidade(app, admin):
    u1 = criar_unidade(admin, "Matriz")
    u2 = criar_unidade(admin, "Filial Norte")
    a1 = criar_aparelho(admin, u1)
    a2 = criar_aparelho(admin, u2)
    criar_colaborador(admin, u1, nome="Elisa Matriz", cpf="529.982.247-25")
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Paulo Consulta", "email": "paulo@empresa.com.br",
                                                    "perfil": "consulta", "acesso": "selecionadas", "unidades": [u1]})
    assert r.status_code == 302
    senha = re.search(r"Senha temporária: ([a-z0-9-]+)", admin.get("/configuracoes/usuarios").get_data(as_text=True))
    assert senha
    nav = entrar(app, "paulo@empresa.com.br", senha.group(1))
    assert nav.get("/").headers["Location"].endswith("/minha-conta/senha")
    nav.post("/minha-conta/senha", {"atual": senha.group(1), "nova": "Consulta-123", "confirmar": "Consulta-123"})
    assert nav.get("/").status_code == 200

    lista = nav.get("/aparelhos").get_data(as_text=True)
    modelo_a1 = consulta(app, "SELECT modelo FROM aparelhos WHERE id = ?", (a1,))["modelo"]
    modelo_a2 = consulta(app, "SELECT modelo FROM aparelhos WHERE id = ?", (a2,))["modelo"]
    assert modelo_a1 in lista and modelo_a2 not in lista
    assert nav.get(f"/aparelhos/{a2}").status_code == 404
    assert nav.get(f"/aparelhos/{a1}").status_code == 200
    assert nav.get("/aparelhos/novo").status_code == 403
    assert nav.post("/unidades/nova", {"nome": "Hack"}).status_code == 403
    assert nav.post(f"/aparelhos/{a1}/status", {"status": "manutencao"}).status_code == 403
    assert nav.get("/configuracoes").status_code == 403
    colaborador = consulta(app, "SELECT id FROM colaboradores")["id"]
    pagina = nav.get(f"/colaboradores/{colaborador}").get_data(as_text=True)
    assert "***.982.247-**" in pagina and "529.982.247-25" not in pagina
    planilha = load_workbook(io.BytesIO(nav.get("/relatorios/exportar?tipo=colaboradores").data))
    cabecalho = [c.value for c in planilha.active[1]]
    assert "CPF" not in cabecalho and "Nome" in cabecalho
    planilha_admin = load_workbook(io.BytesIO(admin.get("/relatorios/exportar?tipo=colaboradores").data))
    assert "CPF" in [c.value for c in planilha_admin.active[1]]


def test_admin_nao_remove_o_proprio_acesso(app, admin):
    r = admin.post("/configuracoes/usuarios/1/editar", {"nome": "Ana", "email": "ti@empresa.com.br",
                                                        "perfil": "consulta", "ativo": "1"})
    assert "não pode remover o seu próprio acesso" in r.get_data(as_text=True)


# ------------------------------------------------------------------ relatórios

def test_relatorios_excel(app, admin):
    unidade = criar_unidade(admin, "Matriz")
    outra = criar_unidade(admin, "Filial/Sul: Galpão")  # nome com caracteres proibidos em abas do Excel
    a1 = criar_aparelho(admin, unidade, imei=gerar_imei("01234567890123"))
    criar_aparelho(admin, outra)
    criar_colaborador(admin, unidade, nome="Fábio Relatório", aparelho_id=a1)
    esperados = {
        "inventario": ("Marca", 2), "em_uso": ("Responsável", 1), "disponiveis": ("Disponível desde", 1),
        "colaboradores": ("Nome", 1), "termos": ("Nº do termo", 1), "movimentacoes": ("Tipo", None),
        "por_unidade": ("Unidade", 2),
    }
    for tipo, (coluna, linhas) in esperados.items():
        r = admin.get(f"/relatorios/exportar?tipo={tipo}")
        assert r.status_code == 200 and r.mimetype.endswith("spreadsheetml.sheet")
        assert re.search(r'filename=".+\.xlsx"', r.headers["Content-Disposition"])
        livro = load_workbook(io.BytesIO(r.data))
        aba = livro.worksheets[0]
        cabecalho = [c.value for c in aba[1]]
        assert coluna in cabecalho, (tipo, cabecalho)
        assert "Sobre este relatório" in livro.sheetnames
        if linhas is not None:
            assert aba.max_row - 1 == linhas, (tipo, aba.max_row)
        assert aba.auto_filter.ref and aba.freeze_panes == "A2"
    livro = load_workbook(io.BytesIO(admin.get("/relatorios/exportar?tipo=inventario").data))
    aba = livro.worksheets[0]
    cabecalho = [c.value for c in aba[1]]
    imeis = [aba.cell(row=i, column=cabecalho.index("IMEI") + 1).value for i in range(2, aba.max_row + 1)]
    assert "012345678901237" in imeis or any(str(v).startswith("0123") for v in imeis)
    assert all(isinstance(v, str) for v in imeis)  # IMEI como texto, sem perder o zero inicial
    valor = aba.cell(row=2, column=cabecalho.index("Valor") + 1).value
    assert valor == 1899.9
    # filtros e escolha de colunas
    r = admin.get(f"/relatorios/exportar?tipo=inventario&unidade={outra}&colunas=marca&colunas=imei")
    aba = load_workbook(io.BytesIO(r.data)).worksheets[0]
    assert [c.value for c in aba[1]] == ["Marca", "IMEI"] and aba.max_row == 2
    por_unidade = load_workbook(io.BytesIO(admin.get("/relatorios/exportar?tipo=por_unidade").data))
    assert "Filial-Sul- Galpão" in por_unidade.sheetnames
    entregue = load_workbook(io.BytesIO(admin.get("/relatorios/exportar?tipo=em_uso").data)).worksheets[0]
    cab = [c.value for c in entregue[1]]
    assert isinstance(entregue.cell(row=2, column=cab.index("Entregue em") + 1).value, datetime)


# ------------------------------------------------------------------ importação

def _planilha(cabecalho, linhas) -> bytes:
    livro = Workbook()
    aba = livro.active
    aba.append(cabecalho)
    for linha in linhas:
        aba.append(linha)
    buffer = io.BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


def test_importacao_de_colaboradores_e_aparelhos(app, admin):
    criar_unidade(admin, "Matriz")
    cpf1, cpf2 = gerar_cpf("012345678"), gerar_cpf("987654321")
    conteudo = _planilha(["Nome completo", "CPF", "E-mail corporativo", "Cargo", "Setor", "Filial"],
                         [["Gabriel Import", int(cpf1), "gabriel@empresa.com.br", "Vendedor", "Comercial", "Matriz"],
                          ["Helena Import", cpf2, "helena@empresa.com.br", "Gerente", "Comercial", "Fábrica Nova"]])
    r = admin.post("/colaboradores/importar", {"acao": "validar", "arquivo": (io.BytesIO(conteudo), "c.xlsx")},
                   content_type="multipart/form-data")
    texto = r.get_data(as_text=True)
    assert "não está cadastrada" in texto and consulta(app, "SELECT COUNT(*) AS n FROM colaboradores")["n"] == 0
    r = admin.post("/colaboradores/importar", {"acao": "validar", "criar_unidades": "1",
                                               "arquivo": (io.BytesIO(conteudo), "c.xlsx")},
                   content_type="multipart/form-data")
    assert "Planilha válida" in r.get_data(as_text=True)
    assert consulta(app, "SELECT COUNT(*) AS n FROM unidades")["n"] == 1  # validação não grava nada
    r = admin.post("/colaboradores/importar", {"acao": "importar_validado"})
    assert r.status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM colaboradores")["n"] == 2
    assert consulta(app, "SELECT cpf FROM colaboradores WHERE nome = 'Gabriel Import'")["cpf"] == cpf1  # zero à esquerda
    assert consulta(app, "SELECT COUNT(*) AS n FROM unidades")["n"] == 2

    imei1, imei2 = gerar_imei("35000000000011"), gerar_imei("35000000000022")
    aparelhos = _planilha(
        ["Marca", "Modelo", "Nº de série", "IMEI", "Linha", "Operadora", "Unidade", "Estado de conservação",
         "Status", "CPF ou e-mail do responsável", "Data de entrega"],
        [["Motorola", "Moto G84", "MG1", int(imei1), "(19) 99999-0001", "TIM", "Matriz", "Bom", "Em utilização",
          "gabriel@empresa.com.br", datetime(2025, 3, 10)],
         ["Motorola", "Moto G84", "MG2", imei2, None, None, "matriz", "Novo", "Disponível", None, None],
         ["Motorola", "Moto G84", "MG2", imei2, None, None, "Matriz", "Novo", "Disponível", None, None]])
    r = admin.post("/aparelhos/importar", {"acao": "validar", "arquivo": (io.BytesIO(aparelhos), "a.xlsx")},
                   content_type="multipart/form-data")
    texto = r.get_data(as_text=True)
    assert "Linha 4" in texto and "já" in texto
    sem_duplicado = _planilha(
        ["Marca", "Modelo", "Nº de série", "IMEI", "Linha", "Operadora", "Unidade", "Estado de conservação",
         "Status", "CPF ou e-mail do responsável", "Data de entrega"],
        [["Motorola", "Moto G84", "MG1", int(imei1), "(19) 99999-0001", "TIM", "Matriz", "Bom", "Em utilização",
          "gabriel@empresa.com.br", datetime(2025, 3, 10)],
         ["Motorola", "Moto G84", "MG2", imei2, None, None, "matriz", "Novo", "Disponível", None, None]])
    r = admin.post("/aparelhos/importar", {"acao": "importar", "arquivo": (io.BytesIO(sem_duplicado), "a.xlsx")},
                   content_type="multipart/form-data")
    assert r.status_code == 302
    assert consulta(app, "SELECT status FROM aparelhos WHERE imei = ?", (imei1,))["status"] == "em_uso"
    vinculo = consulta(app, "SELECT * FROM vinculos")
    assert vinculo["entregue_em"].startswith("2025-03-10")
    assert "Entregas sem termo" in admin.get("/termos?status=sem_termo").get_data(as_text=True)


# ------------------------------------------------------------------ portal do colaborador

def test_portal_do_colaborador(app, admin):
    unidade = criar_unidade(admin)
    a1 = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, nome="Igor Portal", aparelho_id=a1)
    termo_id = int(r.headers["Location"].split("/")[-1])
    admin.post(f"/termos/{termo_id}/enviar")
    _, outro = criar_colaborador(admin, unidade, nome="Julia Outra")

    portal = Navegador(app.test_client())
    r = portal.post("/portal/entrar", {"email": "naoexiste@empresa.com.br"})
    assert r.headers["Location"].endswith("/portal/codigo")  # não revela se o e-mail existe
    r = portal.post("/portal/entrar", {"email": dados["email"]})
    codigo = codigo_do_email(ultimo_email(app, "codigo_portal"))
    assert portal.post("/portal/codigo", {"codigo": "123"}).status_code == 200
    r = portal.post("/portal/codigo", {"codigo": codigo})
    assert r.headers["Location"].endswith("/portal")
    inicio = portal.get("/portal").get_data(as_text=True)
    assert "Igor" in inicio and "Assinar" in inicio
    r = portal.post(f"/portal/termos/{termo_id}", {"cpf": dados["cpf"], "nome": "Igor Portal",
                                                   "tipo_assinatura": "digitada", "aceite": "1"})
    assert r.status_code == 302
    termo = consulta(app, "SELECT status, evidencias_json FROM termos WHERE id = ?", (termo_id,))
    assert termo["status"] == "assinado" and "portal" in termo["evidencias_json"]
    assert portal.get(f"/portal/termos/{termo_id}/pdf").data[:4] == b"%PDF"
    # não enxerga termos de outra pessoa
    a2 = criar_aparelho(admin, unidade)
    outro_id = consulta(app, "SELECT id FROM colaboradores WHERE cpf = ?", (outro["cpf"],))["id"]
    admin.post("/entregas/nova", {"aparelho_id": a2, "colaborador_id": outro_id})
    termo_outro = consulta(app, "SELECT id FROM termos WHERE colaborador_id = ?", (outro_id,))["id"]
    assert portal.get(f"/portal/termos/{termo_outro}").status_code == 404
    assert portal.get("/aparelhos").status_code == 302  # portal não dá acesso à área do TI


# ------------------------------------------------------------------ tarefas automáticas

def test_lembretes_resumo_e_backup(app, admin):
    unidade = criar_unidade(admin)
    a = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, aparelho_id=a)
    termo_id = int(r.headers["Location"].split("/")[-1])
    admin.post(f"/termos/{termo_id}/enviar")
    with app.app_context():
        from app.db import executar
        from app.servicos.configuracoes import salvar
        salvar({"lembrete_horario_comercial": "0"})
        executar("UPDATE termos SET ultimo_envio_em = '2020-01-01 10:00:00' WHERE id = ?", (termo_id,))
    from app.servicos import agendador
    resultados = dict(agendador.executar_pendentes(app, forcar=["lembretes"]))
    assert resultados["lembretes"].startswith("1 lembrete")
    assert consulta(app, "SELECT lembretes FROM termos WHERE id = ?", (termo_id,))["lembretes"] == 1
    assert ultimo_email(app, "lembrete")["destinatario"] == dados["email"]
    # o lembrete não repete antes do intervalo
    assert dict(agendador.executar_pendentes(app, forcar=["lembretes"]))["lembretes"].startswith("0 lembrete")

    resultados = dict(agendador.executar_pendentes(app, forcar=["resumo_diario"]))
    assert "Resumo enviado" in resultados["resumo_diario"]
    resumo = ultimo_email(app, "resumo")
    assert resumo["destinatario"] == "ti@empresa.com.br" and "aguardando assinatura" in resumo["corpo_html"]
    assert "http://localhost/termos" in resumo["corpo_html"]

    resultados = dict(agendador.executar_pendentes(app, forcar=["backup"]))
    assert "gerado" in resultados["backup"]
    nome = consulta(app, "SELECT ultimo_resultado FROM tarefas WHERE nome = 'backup'")["ultimo_resultado"].split()[1]
    caminho = app.config["PASTA_BACKUPS"] / nome
    with zipfile.ZipFile(caminho) as arquivo:
        nomes = arquivo.namelist()
    assert "celulares.db" in nomes and "LEIA-ME.txt" in nomes
    r = admin.get(f"/configuracoes/backup/{nome}")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    assert admin.get("/configuracoes/backup/../../etc/passwd").status_code in (302, 404)

    # execução normal respeita o horário agendado (nada vence agora)
    assert agendador.executar_pendentes(app) == [] or all(n == "lembretes" for n, _ in agendador.executar_pendentes(app))


def test_assinatura_sem_codigo_quando_desativado(app, admin):
    unidade = criar_unidade(admin)
    a = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, nome="Karla Sem Codigo", aparelho_id=a)
    termo_id = int(r.headers["Location"].split("/")[-1])
    with app.app_context():
        from app.servicos.configuracoes import salvar
        salvar({"assinatura_exigir_codigo": "0"})
    admin.post(f"/termos/{termo_id}/enviar")
    from conftest import token_do_email
    token = token_do_email(ultimo_email(app, "termo"))
    nav = Navegador(app.test_client())
    assert "Código recebido" not in nav.get(f"/assinar/{token}").get_data(as_text=True)
    r = nav.post(f"/assinar/{token}", {"acao": "assinar", "cpf": dados["cpf"], "nome": "Karla Sem Codigo",
                                       "tipo_assinatura": "desenho", "tracos": TRACOS, "aceite": "1"})
    assert r.status_code == 302


def test_modelo_do_termo_editavel(app, admin):
    r = admin.post("/configuracoes/termo", {"acao": "salvar", "termo_titulo": "Termo de uso",
                                            "termo_texto": "Eu, {{colaborador_nome}}, recebi o {{campo_que_nao_existe}}." * 3,
                                            "aviso_privacidade": "x"})
    assert "Campos desconhecidos" in r.get_data(as_text=True)
    texto = "Eu, {{colaborador_nome}}, recebi o aparelho {{aparelho_modelo}} da {{empresa_nome}} em perfeito estado. " * 2
    r = admin.post("/configuracoes/termo", {"acao": "previa", "termo_titulo": "Termo de uso", "termo_texto": texto,
                                            "aviso_privacidade": "x"})
    assert r.data[:4] == b"%PDF"
    admin.post("/configuracoes/termo", {"acao": "salvar", "termo_titulo": "Termo de uso", "termo_texto": texto,
                                        "aviso_privacidade": "x"})
    unidade = criar_unidade(admin)
    a = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, nome="Lucas Modelo", aparelho_id=a)
    termo_id = int(r.headers["Location"].split("/")[-1])
    pagina = admin.get(f"/termos/{termo_id}").get_data(as_text=True)
    assert "Eu, Lucas Modelo, recebi o aparelho" in pagina and "Grupo Teste" in pagina
