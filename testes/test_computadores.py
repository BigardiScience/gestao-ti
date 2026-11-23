"""Computadores: cadastro, validações, permissões no servidor (técnico x consulta), importação e exportação."""
import io
import re
import time

from openpyxl import Workbook, load_workbook

from conftest import Navegador, criar_aparelho, criar_unidade


def consulta(app, sql, parametros=(), um=True):
    with app.app_context():
        from app.db import consultar
        return consultar(sql, parametros, um=um)


def criar_usuario(app, admin, nome, email, perfil, **extra) -> Navegador:
    dados = {"origem": "local", "nome": nome, "email": email, "perfil": perfil}
    dados.update(extra)
    r = admin.post("/configuracoes/usuarios/novo", dados)
    assert r.status_code == 302, r.get_data(as_text=True)[:1500]
    senha = re.findall(r"Senha temporária: ([a-z0-9-]+)", admin.get("/configuracoes/usuarios").get_data(as_text=True))[-1]
    nav = Navegador(app.test_client())
    assert nav.post("/entrar", {"email": email, "senha": senha}).status_code == 302
    nav.post("/minha-conta/senha", {"atual": senha, "nova": "Nova-senha-123", "confirmar": "Nova-senha-123"})
    return nav


def novo_pc(nav, unidade_id, nome, **extra):
    dados = {"nome": nome, "unidade_id": unidade_id, "tipo": "desktop", "situacao": "em_uso",
             "usuario_nome": "Maria Silva", "usuario_login": "maria.silva", "setor": "Fiscal",
             "usuario_situacao": "ativo", "sistema": "Windows 11 Pro", "processador": "Intel Core i3-13100",
             "memoria_gb": "8", "sep": "ok"}
    dados.update(extra)
    return nav.post("/computadores/novo", dados)


def id_criado(resposta) -> int:
    assert resposta.status_code == 302, resposta.get_data(as_text=True)[:3000]
    return int(resposta.headers["Location"].rstrip("/").split("/")[-1])


def preparar(app, admin):
    sor = criar_unidade(admin, "Sorocaba")
    ube = criar_unidade(admin, "Uberaba", cidade="Uberaba", uf="MG")
    r = admin.post(f"/unidades/{sor}/editar", {"nome": "Sorocaba", "cidade": "Sorocaba", "uf": "SP", "ativo": "1",
                                               "redes": "172.16.161.0/24"})
    assert r.status_code == 302, r.get_data(as_text=True)[:1500]
    tec_sor = criar_usuario(app, admin, "Técnico Sorocaba", "tec.sor@empresa.com.br", "tecnico", unidades_tecnico=[sor])
    tec_ube = criar_usuario(app, admin, "Técnico Uberaba", "tec.ube@empresa.com.br", "tecnico", unidades_tecnico=[ube])
    leitor = criar_usuario(app, admin, "Leitor", "leitor@empresa.com.br", "consulta", acesso="selecionadas",
                           unidades=[sor])
    return sor, ube, tec_sor, tec_ube, leitor


def test_cadastro_e_validacoes(app, admin):
    sor, ube, tec_sor, *_ = preparar(app, admin)
    pc = id_criado(novo_pc(tec_sor, sor, "pcsor10", ip="172.16.161.10", mac="02-00-5e-10-77-50"))
    salvo = consulta(app, "SELECT * FROM computadores WHERE id = ?", (pc,))
    assert salvo["nome"] == "PCSOR10" and salvo["mac"] == "02:00:5E:10:77:50" and salvo["memoria_gb"] == 8

    erros = {
        "IP fora da faixa": dict(nome="PCSOR11", ip="10.0.0.5"),
        "IP repetido": dict(nome="PCSOR11", ip="172.16.161.10"),
        "MAC repetido": dict(nome="PCSOR11", mac="02:00:5E:10:77:50"),
        "MAC inválido": dict(nome="PCSOR11", mac="04:BF:1B:OC:B8:EB"),
        "nome repetido": dict(nome="pcsor10"),
        "em uso sem usuário": dict(nome="PCSOR11", usuario_nome="", usuario_login=""),
        "memória inválida": dict(nome="PCSOR11", memoria_gb="8HB"),
        "desativado sem motivo": dict(nome="PCSOR11", situacao="desativado"),
        "ID remoto sem ferramenta": dict(nome="PCSOR11", remoto_id="842 776 171"),
    }
    for caso, extra in erros.items():
        r = novo_pc(tec_sor, sor, **{"nome": "X", **extra})
        assert r.status_code == 200, caso
    assert consulta(app, "SELECT COUNT(*) AS n FROM computadores")["n"] == 1

    # notebook: IP e MAC do Wi-Fi; desktop ignora os campos de Wi-Fi
    nb = id_criado(novo_pc(tec_sor, sor, "NOTSOR001", tipo="notebook", ip="172.16.161.50", ip_wifi="172.16.161.51",
                           mac="02:00:5E:20:EC:7A", mac_wifi="02:00:5E:20:EC:7B"))
    assert consulta(app, "SELECT ip_wifi FROM computadores WHERE id = ?", (nb,))["ip_wifi"] == "172.16.161.51"
    assert novo_pc(tec_sor, sor, "PCSOR12", ip="172.16.161.51").status_code == 200  # IP do Wi-Fi conta como usado

    # ordem natural: PCSOR9 antes de PCSOR10
    id_criado(novo_pc(tec_sor, sor, "PCSOR9", situacao="reserva", usuario_nome="", usuario_login=""))
    lista = tec_sor.get("/computadores?ordem=nome").get_data(as_text=True)
    assert lista.index("PCSOR9<") < lista.index("PCSOR10<")

    # histórico registra as alterações e a desativação pede motivo e destino do disco
    r = tec_sor.post(f"/computadores/{pc}/editar", {**consulta(app, "SELECT * FROM computadores WHERE id = ?", (pc,)),
                                                    "memoria_gb": "16", "nobreak": "1", "situacao": "desativado",
                                                    "desativacao_motivo": "Obsoleto", "destino_disco": "Destruído"})
    assert r.status_code == 302
    historico = consulta(app, "SELECT tipo, descricao FROM computadores_historico WHERE computador_id = ? "
                              "ORDER BY id", (pc,), um=False)
    assert [h["tipo"] for h in historico] == ["cadastro", "desativacao"]
    # desativado libera o IP para outra máquina
    id_criado(novo_pc(tec_sor, sor, "PCSOR13", ip="172.16.161.10"))


def test_tecnico_ve_todas_e_altera_so_a_sua_unidade(app, admin):
    sor, ube, tec_sor, tec_ube, _ = preparar(app, admin)
    pc = id_criado(novo_pc(tec_sor, sor, "PCSOR10", ip="172.16.161.10"))

    # Uberaba vê o computador de Sorocaba, inclusive IP...
    lista = tec_ube.get("/computadores?unidade=todas").get_data(as_text=True)
    assert "PCSOR10" in lista and "172.16.161.10" in lista
    assert tec_ube.get(f"/computadores/{pc}").status_code == 200
    # ...mas nenhuma alteração passa, nem por requisição direta
    assert tec_ube.get(f"/computadores/{pc}/editar").status_code == 403
    assert tec_ube.post(f"/computadores/{pc}/editar", {"nome": "HACK", "unidade_id": ube, "tipo": "desktop",
                                                       "situacao": "reserva"}).status_code == 403
    assert tec_ube.post(f"/computadores/{pc}/excluir").status_code == 403
    assert tec_ube.post(f"/computadores/{pc}/conferir").status_code == 403
    assert novo_pc(tec_ube, sor, "INTRUSO").status_code == 200  # recusado com erro no campo Unidade
    assert consulta(app, "SELECT COUNT(*) AS n FROM computadores WHERE nome = 'INTRUSO'")["n"] == 0
    assert consulta(app, "SELECT nome FROM computadores WHERE id = ?", (pc,))["nome"] == "PCSOR10"
    # técnico não leva um computador da unidade dele para outra
    meu = id_criado(novo_pc(tec_ube, ube, "PCUBE01"))
    dados = consulta(app, "SELECT * FROM computadores WHERE id = ?", (meu,))
    assert tec_ube.post(f"/computadores/{meu}/editar", {**dados, "unidade_id": sor}).status_code == 200
    assert consulta(app, "SELECT unidade_id FROM computadores WHERE id = ?", (meu,))["unidade_id"] == ube

    # a tela começa pela unidade do técnico
    inicio = tec_ube.get("/computadores").get_data(as_text=True)
    assert "PCUBE01" in inicio and "PCSOR10" not in inicio
    # o dono altera, confere e exclui
    assert tec_sor.post(f"/computadores/{pc}/conferir").status_code == 302
    assert consulta(app, "SELECT conferido_em FROM computadores WHERE id = ?", (pc,))["conferido_em"]
    assert tec_sor.post(f"/computadores/{pc}/excluir").status_code == 302
    assert consulta(app, "SELECT acao FROM auditoria WHERE acao = 'computador_excluido'")

    # celulares: o técnico continua só consultando, e só a unidade dele
    aparelho_sor = criar_aparelho(admin, sor)
    criar_aparelho(admin, ube)
    assert tec_ube.get("/aparelhos/novo").status_code == 403
    assert tec_ube.get(f"/aparelhos/{aparelho_sor}").status_code == 404
    assert tec_ube.get("/configuracoes/usuarios").status_code == 403


def test_consulta_nao_ve_rede_nem_altera(app, admin):
    sor, ube, tec_sor, tec_ube, leitor = preparar(app, admin)
    pc = id_criado(novo_pc(tec_sor, sor, "PCSOR10", ip="172.16.161.10", mac="02:00:5E:10:77:50",
                           remoto_ferramenta="AnyDesk", remoto_id="842 776 171"))
    outro = id_criado(novo_pc(tec_ube, ube, "PCUBE01"))
    pagina = leitor.get(f"/computadores/{pc}").get_data(as_text=True)
    assert "PCSOR10" in pagina and "172.16.161.10" not in pagina and "842 776 171" not in pagina
    lista = leitor.get("/computadores").get_data(as_text=True)
    assert "PCSOR10" in lista and "02:00:5E" not in lista and "PCUBE01" not in lista
    assert leitor.get(f"/computadores/{outro}").status_code == 404
    assert "PCSOR10" not in leitor.get("/computadores?q=172.16.161.10").get_data(as_text=True)
    assert leitor.get("/computadores/novo").status_code == 403
    assert leitor.post(f"/computadores/{pc}/conferir").status_code == 403
    assert leitor.post(f"/computadores/{pc}/excluir").status_code == 403
    assert leitor.get("/computadores/importar").status_code == 403
    planilha = load_workbook(io.BytesIO(leitor.get("/computadores/exportar.xlsx").data))
    cabecalho = [c.value for c in planilha["Computadores"][1]]
    assert "Nome da máquina" in cabecalho and "IP" not in cabecalho and "MAC" not in cabecalho
    completa = load_workbook(io.BytesIO(tec_sor.get("/computadores/exportar.xlsx?unidade=todas").data))
    assert "IP" in [c.value for c in completa["Computadores"][1]]
    assert completa["Computadores"].max_row == 3
    assert consulta(app, "SELECT COUNT(*) AS n FROM auditoria WHERE acao = 'computadores_exportados'")["n"] == 2


def test_indicadores_e_atencao(app, admin):
    sor, ube, tec_sor, *_ = preparar(app, admin)
    id_criado(novo_pc(tec_sor, sor, "PC1"))
    id_criado(novo_pc(tec_sor, sor, "PC2", sep="pendente"))
    id_criado(novo_pc(tec_sor, sor, "PC3", usuario_situacao="desligado"))
    id_criado(novo_pc(tec_sor, sor, "PC4", situacao="reserva", usuario_nome="", usuario_login=""))
    with app.app_context():  # conferência vencida há mais de 6 meses
        from app.db import executar
        executar("UPDATE computadores SET criado_em = '2025-01-01 10:00:00' WHERE nome = 'PC4'")
    with app.test_request_context():
        from flask import g
        from app.servicos import computadores as svc
        g.usuario = {"id": 1, "perfil": "admin"}
        numeros = svc.indicadores({"unidade": sor})
    assert (numeros["total"], numeros["em_uso"], numeros["reserva"], numeros["atencao"]) == (4, 3, 1, 3)
    atencao = tec_sor.get("/computadores?atencao=1").get_data(as_text=True)
    assert "PC2" in atencao and "PC3" in atencao and "PC4" in atencao and ">PC1<" not in atencao
    assert "Usuário desligado" in atencao and "Conferir" in atencao


def test_importacao_por_unidade(app, admin):
    sor, ube, tec_sor, tec_ube, _ = preparar(app, admin)
    livro = Workbook()
    aba = livro.active
    aba.append(["Máquina", "Tipo", "Usuários", "Setor", "SO", "MAC", "Nobreak", "MONITOR", "utilitarios", "SEP",
                "AnyDesk", "Memória", "Processador", "SoftwaresEspecificos", "OBS", "Win Update"])
    aba.append(["PCSOR001", "Desktop", "treinamento.sor", "Treinamento", "Windows 11 Pro", "02:00:5E:10:64:D2", "NÃO",
                21, "Headset", "OK", "842 776 171", "8GB", "Intel Core i3-13100", None, None, "OK"])
    aba.append(["PCSOR206", "Desktop", None, "Desenvolvimento", "Windows 11 Pro", "02:00:5E:40:19:1E", "Não", 27,
                "Webcam/Headset", "OK", None, "64GB", "Intel Core i9-14900KF", None, None, "OK"])
    aba.append(["NOTSOR005", "Notebook", None, "TI", "Windows 11 Pro", "02-00-5E-30-3B-4F", "-", None, "N/",
                "ok", None, "16", "Intel Core i5-1145G7", None, "Notebook de empréstimo", None])
    buffer = io.BytesIO()
    livro.save(buffer)

    # Uberaba não importa para Sorocaba
    assert tec_ube.post("/computadores/importar", {"unidade_id": sor, "acao": "validar", "arquivo": (
        io.BytesIO(buffer.getvalue()), "pcs.xlsx")}, content_type="multipart/form-data").status_code == 200
    assert consulta(app, "SELECT COUNT(*) AS n FROM computadores")["n"] == 0

    r = tec_sor.post("/computadores/importar", {"unidade_id": sor, "acao": "validar", "arquivo": (
        io.BytesIO(buffer.getvalue()), "pcs.xlsx")}, content_type="multipart/form-data")
    texto = r.get_data(as_text=True)
    assert "Planilha válida" in texto, texto[texto.find("lista-erros"):][:1500]
    r = tec_sor.post("/computadores/importar", {"acao": "importar_validado"})
    assert r.status_code == 302
    pcs = {c["nome"]: c for c in consulta(app, "SELECT * FROM computadores", um=False)}
    assert set(pcs) == {"PCSOR001", "PCSOR206", "NOTSOR005"}
    um = pcs["PCSOR001"]
    assert (um["usuario_login"], um["situacao"], um["remoto_ferramenta"], um["remoto_id"], um["headset"],
            um["nobreak"], um["monitor"], um["memoria_gb"]) == ("treinamento.sor", "em_uso", "AnyDesk", "842 776 171",
                                                                  1, 0, '21"', 8)
    assert pcs["PCSOR206"]["situacao"] == "reserva" and pcs["PCSOR206"]["webcam"] == 1
    assert pcs["NOTSOR005"]["tipo"] == "notebook" and pcs["NOTSOR005"]["mac"] == "02:00:5E:30:3B:4F"
    assert pcs["NOTSOR005"]["nobreak"] is None
    assert all(c["unidade_id"] == sor for c in pcs.values())

    # a mesma planilha de novo: nomes repetidos são recusados e nada é gravado
    r = tec_sor.post("/computadores/importar", {"unidade_id": sor, "acao": "validar", "arquivo": (
        io.BytesIO(buffer.getvalue()), "pcs.xlsx")}, content_type="multipart/form-data")
    assert "Já existe um computador chamado PCSOR001" in r.get_data(as_text=True)
    modelo = load_workbook(io.BytesIO(tec_sor.get("/computadores/modelo-importacao.xlsx").data))
    assert modelo["Computadores"]["A1"].value == "Nome da máquina *"


def test_busca_geral_encontra_por_ip(app, admin):
    sor, ube, tec_sor, *_ = preparar(app, admin)
    pc = id_criado(novo_pc(tec_sor, sor, "PCSOR10", ip="172.16.161.30"))
    r = tec_sor.get("/busca?q=172.16.161.30")
    assert r.status_code == 302 and r.headers["Location"].endswith(f"/computadores/{pc}")
    assert tec_sor.get("/busca?q=maria").status_code == 302


def test_usuarios_tecnico_exclusao_e_inatividade(app, admin):
    sor = criar_unidade(admin, "Sorocaba")
    # técnico precisa de unidade
    r = admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Sem Unidade", "email": "sem@empresa.com.br",
                                                    "perfil": "tecnico"})
    assert r.status_code == 200 and "Selecione a unidade" in r.get_data(as_text=True)
    # conta nunca usada pode ser excluída; quem já entrou no sistema só pode ser desativado
    admin.post("/configuracoes/usuarios/novo", {"origem": "local", "nome": "Engano", "email": "engano@empresa.com.br",
                                                "perfil": "tecnico", "unidades_tecnico": [sor]})
    engano = consulta(app, "SELECT id FROM usuarios WHERE email = 'engano@empresa.com.br'")["id"]
    assert admin.post(f"/configuracoes/usuarios/{engano}/excluir").status_code == 302
    assert not consulta(app, "SELECT id FROM usuarios WHERE id = ?", (engano,))
    tecnico = criar_usuario(app, admin, "Usado", "usado@empresa.com.br", "tecnico", unidades_tecnico=[sor])
    usado = consulta(app, "SELECT id FROM usuarios WHERE email = 'usado@empresa.com.br'")["id"]
    texto = admin.post(f"/configuracoes/usuarios/{usado}/excluir", follow_redirects=True).get_data(as_text=True)
    assert "desative o usuário" in texto and consulta(app, "SELECT id FROM usuarios WHERE id = ?", (usado,))
    assert tecnico.post(f"/configuracoes/usuarios/{usado}/excluir").status_code == 403

    # 30 minutos parado: a sessão é encerrada
    assert tecnico.get("/computadores").status_code == 200
    with tecnico.cliente.session_transaction() as sessao:
        sessao["ativo_em"] = int(time.time()) - 31 * 60
    r = tecnico.get("/computadores")
    assert r.status_code == 302 and "/entrar" in r.headers["Location"]


def test_migracao_preserva_usuarios_existentes(tmp_path):
    """Banco da versão 1.2 (esquema 2) com usuários e auditoria: a migração recria a tabela sem perder nada."""
    from app.db import MIGRACOES, conectar, migrar
    caminho = tmp_path / "antigo.db"
    conexao = conectar(caminho)
    conexao.execute("PRAGMA journal_mode = WAL")
    for numero, script in enumerate(MIGRACOES[:2], start=1):
        conexao.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {numero};\nCOMMIT;")
    conexao.execute("INSERT INTO unidades (id, nome) VALUES (1, 'Sorocaba')")
    conexao.execute("INSERT INTO usuarios (id, nome, email, senha_hash, perfil) VALUES (1, 'Ana', 'a@x.com', 'h', 'admin')")
    conexao.execute("INSERT INTO usuarios (id, nome, email, senha_hash, perfil) VALUES (2, 'Bia', 'b@x.com', 'h', 'consulta')")
    conexao.execute("INSERT INTO usuarios_unidades (usuario_id, unidade_id) VALUES (2, 1)")
    conexao.execute("INSERT INTO auditoria (usuario_id, usuario_nome, acao) VALUES (1, 'Ana', 'login')")
    assert migrar(conexao) == len(MIGRACOES)
    assert conexao.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] == 2
    assert conexao.execute("SELECT COUNT(*) FROM usuarios_unidades").fetchone()[0] == 1
    assert conexao.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert not conexao.execute("PRAGMA foreign_key_check").fetchall()
    conexao.execute("INSERT INTO usuarios (nome, email, senha_hash, perfil) VALUES ('Caio', 'c@x.com', 'h', 'tecnico')")
    # a referência da auditoria continua protegendo o usuário 1
    import sqlite3
    try:
        conexao.execute("DELETE FROM usuarios WHERE id = 1")
        raise AssertionError("deveria ter sido barrado pela chave estrangeira")
    except sqlite3.IntegrityError:
        pass
    conexao.close()
