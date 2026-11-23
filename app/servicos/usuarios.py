"""Usuários do sistema e suas permissões.

Dois jeitos de entrar:
- pelo Active Directory (padrão): o administrador só marca quem é da TI (perfil e unidades); quem entra
  pelo AD sem marcação é colaborador e só abre os próprios pedidos;
- conta local (e-mail e senha do sistema): só para o administrador de emergência, caso o AD falhe.
"""
import sqlite3
from datetime import datetime, time, timedelta

from ..db import condicao_busca, consultar, escalar, executar, transacao
from ..seguranca import PERFIS, hash_senha, senha_temporaria
from ..util import Pagina, agora, agora_bd, fuso, para_bd
from ..validacao import email_valido, ler_data, limpar_texto
from . import ErroNegocio, ErroValidacao
from .registro import auditar

ORIGENS = {"ad": "Usuário do Windows (AD)", "local": "Conta local (emergência ou teste)"}
ACOES_ACESSO = ("usuario_criado", "usuario_alterado", "usuario_excluido", "usuario_pre_cadastrado",
                "cobertura_liberada", "cobertura_encerrada", "cobertura_expirada", "senha_redefinida")


def _unidades_do_usuario(usuario_id: int) -> dict:
    linhas = consultar("SELECT uu.unidade_id, uu.criado_em, uu.expira_em, un.nome, p.nome AS criado_por_nome "
                       "FROM usuarios_unidades uu JOIN unidades un ON un.id = uu.unidade_id "
                       "LEFT JOIN usuarios p ON p.id = uu.criado_por WHERE uu.usuario_id = ? ORDER BY un.nome",
                       (usuario_id,))
    agora_txt = agora_bd()
    return {"permanentes": [l for l in linhas if not l["expira_em"]],
            "coberturas": [l for l in linhas if l["expira_em"] and l["expira_em"] > agora_txt]}


def listar_ti() -> list:
    usuarios = consultar("SELECT id, nome, email, perfil, origem, login_ad, ativo, deve_trocar_senha, "
                         "ultimo_acesso_em, bloqueado_ate, ad_sincronizado_em FROM usuarios "
                         "WHERE perfil IN ('admin', 'tecnico', 'consulta') ORDER BY ativo DESC, nome COLLATE NOCASE")
    for usuario in usuarios:
        unidades = _unidades_do_usuario(usuario["id"])
        usuario["unidades"] = [u["nome"] for u in unidades["permanentes"]]
        usuario["coberturas"] = unidades["coberturas"]
    return usuarios


def listar_colaboradores(busca: str = "", pagina: int = 1, por_pagina: int = 30) -> Pagina:
    condicao, parametros = condicao_busca(busca, "u.nome || ' ' || COALESCE(u.login_ad, '') || ' ' || "
                                                 "COALESCE(u.setor, '') || ' ' || COALESCE(un.nome, '')", digitos=False)
    base = (f"FROM usuarios u LEFT JOIN unidades un ON un.id = u.unidade_id WHERE u.perfil = 'colaborador'{condicao}")
    total = escalar(f"SELECT COUNT(*) {base}", parametros)
    itens = consultar(f"SELECT u.id, u.nome, u.email, u.login_ad, u.origem, u.setor, u.ativo, u.ultimo_acesso_em, "
                      f"u.ad_sincronizado_em, un.nome AS unidade_nome, (SELECT COUNT(*) FROM chamados c WHERE "
                      f"c.solicitante_id = u.id) AS pedidos {base} ORDER BY u.nome COLLATE NOCASE LIMIT ? OFFSET ?",
                      (*parametros, por_pagina, (pagina - 1) * por_pagina))
    return Pagina(itens, total, pagina, por_pagina)


def obter(usuario_id: int):
    usuario = consultar("SELECT u.id, u.nome, u.email, u.perfil, u.origem, u.login_ad, u.unidade_id, u.setor, u.ad_dn, "
                        "u.ad_sincronizado_em, u.ativo, u.deve_trocar_senha, u.ultimo_acesso_em, "
                        "un.nome AS unidade_nome FROM usuarios u LEFT JOIN unidades un ON un.id = u.unidade_id "
                        "WHERE u.id = ?", (usuario_id,), um=True)
    if usuario:
        unidades = _unidades_do_usuario(usuario_id)
        usuario["unidades"] = [u["unidade_id"] for u in unidades["permanentes"]]
        usuario["coberturas"] = unidades["coberturas"]
    return usuario


def _validar(form, atual=None) -> dict:
    from .ad import normalizar_login
    erros = {}
    origem = atual["origem"] if atual else (form.get("origem") or "ad")
    dados = {
        "origem": origem if origem in ORIGENS else "ad",
        "nome": limpar_texto(form.get("nome"), 120),
        "email": (form.get("email") or "").strip().lower() or None,
        "perfil": form.get("perfil") or "",
        "ativo": 1 if form.get("ativo") in ("1", "on") else 0,
        "login_ad": None,
        "unidade_id": (atual or {}).get("unidade_id"),
        "unidades": [],
    }
    usuario_id = atual["id"] if atual else 0
    if len(dados["nome"]) < 3:
        erros["nome"] = "Informe o nome."
    if dados["origem"] == "ad":
        dados["login_ad"] = normalizar_login(form.get("login_ad") or (atual or {}).get("login_ad") or "")
        if not dados["login_ad"]:
            erros["login_ad"] = "Informe o usuário do Windows (ex.: maria.silva)."
        elif escalar("SELECT id FROM usuarios WHERE login_ad = ? AND id <> ?", (dados["login_ad"], usuario_id)):
            erros["login_ad"] = "Já existe um cadastro com este usuário do Windows."
        if dados["email"] and not email_valido(dados["email"]):
            erros["email"] = "E-mail inválido."
        if dados["perfil"] not in PERFIS:
            erros["perfil"] = "Selecione o perfil."
    else:
        # conta local: entra pelo e-mail (administrador de emergência) ou por um nome de usuário (teste antes do AD)
        from .ad import PADRAO_LOGIN
        login = (form.get("login_ad") or "").strip().lower()
        if login and not PADRAO_LOGIN.fullmatch(login):
            erros["login_ad"] = "Use só letras minúsculas, números, ponto ou hífen (ex.: teste.colaborador)."
        elif login and escalar("SELECT id FROM usuarios WHERE login_ad = ? AND id <> ?", (login, usuario_id)):
            erros["login_ad"] = "Já existe um cadastro com este usuário."
        dados["login_ad"] = login or None
        if dados["email"] and not email_valido(dados["email"]):
            erros["email"] = "E-mail inválido."
        elif dados["email"] and escalar("SELECT id FROM usuarios WHERE email = ? COLLATE NOCASE AND origem = 'local' "
                                        "AND id <> ?", (dados["email"], usuario_id)):
            erros["email"] = "Já existe uma conta local com este e-mail."
        elif not dados["email"] and not login:
            erros["login_ad"] = "Informe o usuário (ou o e-mail) que a pessoa vai digitar para entrar."
        if dados["perfil"] not in PERFIS:
            erros["perfil"] = "Selecione o perfil."
        try:
            dados["unidade_id"] = int(form.get("unidade_id") or 0) or None
        except ValueError:
            dados["unidade_id"] = None
        if dados["unidade_id"] and not escalar("SELECT id FROM unidades WHERE id = ?", (dados["unidade_id"],)):
            dados["unidade_id"] = None
        if dados["perfil"] == "colaborador" and not dados["unidade_id"]:
            erros["unidade_id"] = "Selecione a unidade da pessoa: os pedidos dela vão para a TI dessa unidade."
    if dados["perfil"] == "tecnico":
        dados["unidades"] = _ler_unidades(form, "unidades_tecnico")
        if not dados["unidades"]:
            erros["unidades_tecnico"] = "Selecione a unidade (ou as unidades) que o técnico atende."
    if dados["perfil"] == "consulta" and form.get("acesso") == "selecionadas":
        dados["unidades"] = _ler_unidades(form, "unidades")
        if not dados["unidades"]:
            erros["unidades"] = "Selecione ao menos uma unidade ou escolha “Todas as unidades”."
    if erros:
        raise ErroValidacao(erros)
    return dados


def _ler_unidades(form, campo: str) -> list[int]:
    valores = form.getlist(campo) if hasattr(form, "getlist") else form.get(campo, [])
    try:
        ids = {int(u) for u in valores}
    except (TypeError, ValueError):
        return []
    existentes = {l["id"] for l in consultar("SELECT id FROM unidades")}
    return sorted(ids & existentes)


def _gravar_unidades(usuario_id: int, unidades: list[int], por_id=None):
    """Acesso permanente às unidades. Mantém a data de quem já tinha e não mexe nas coberturas temporárias."""
    atuais = {l["unidade_id"] for l in consultar("SELECT unidade_id FROM usuarios_unidades WHERE usuario_id = ? "
                                                 "AND expira_em IS NULL", (usuario_id,))}
    for unidade_id in atuais - set(unidades):
        executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ? AND expira_em IS NULL",
                 (usuario_id, unidade_id))
    for unidade_id in set(unidades) - atuais:
        # uma cobertura temporária da mesma unidade vira acesso permanente
        executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ?", (usuario_id, unidade_id))
        executar("INSERT INTO usuarios_unidades (usuario_id, unidade_id, criado_em, criado_por) VALUES (?, ?, ?, ?)",
                 (usuario_id, unidade_id, agora_bd(), por_id))


def criar(form, por_id=None) -> tuple[int, str | None]:
    dados = _validar(form, None)
    temporaria = senha_temporaria() if dados["origem"] == "local" else None
    with transacao():
        cursor = executar(
            "INSERT INTO usuarios (nome, email, senha_hash, perfil, origem, login_ad, unidade_id, ativo, deve_trocar_senha) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dados["nome"], dados["email"], hash_senha(temporaria) if temporaria else None, dados["perfil"],
             dados["origem"], dados["login_ad"], dados["unidade_id"] if dados["origem"] == "local" else None,
             dados["ativo"] if "ativo" in form else 1, 1 if temporaria else 0))
        _gravar_unidades(cursor.lastrowid, dados["unidades"], por_id)
        auditar("usuario_criado", "usuario", cursor.lastrowid,
                {"login": dados["login_ad"] or dados["email"], "origem": dados["origem"], "perfil": dados["perfil"],
                 "unidades": dados["unidades"]})
    return cursor.lastrowid, temporaria


def _admins_ativos(excluindo: int) -> int:
    return escalar("SELECT COUNT(*) FROM usuarios WHERE perfil = 'admin' AND ativo = 1 AND id <> ?", (excluindo,))


def atualizar(usuario_id: int, form, usuario_atual_id: int):
    atual = obter(usuario_id)
    if not atual:
        raise ErroNegocio("Usuário não encontrado.")
    dados = _validar(form, atual)
    if usuario_id == usuario_atual_id and (dados["perfil"] != "admin" or not dados["ativo"]):
        raise ErroNegocio("Você não pode remover o seu próprio acesso de administrador.")
    if atual["perfil"] == "admin" and (dados["perfil"] != "admin" or not dados["ativo"]) and not _admins_ativos(usuario_id):
        raise ErroNegocio("É preciso manter pelo menos um administrador ativo.")
    mudou_acesso = (dados["perfil"] != atual["perfil"] or dados["ativo"] != atual["ativo"]
                    or sorted(dados["unidades"]) != sorted(atual["unidades"]))
    with transacao():
        executar("UPDATE usuarios SET nome = ?, email = ?, perfil = ?, ativo = ?, login_ad = ?, unidade_id = ?, "
                 "atualizado_em = datetime('now'), versao_sessao = versao_sessao + ? WHERE id = ?",
                 (dados["nome"], dados["email"], dados["perfil"], dados["ativo"], dados["login_ad"],
                  dados["unidade_id"], 1 if mudou_acesso else 0, usuario_id))
        _gravar_unidades(usuario_id, dados["unidades"] if dados["perfil"] in ("consulta", "tecnico") else [],
                         usuario_atual_id)
        if dados["perfil"] != "tecnico":
            executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND expira_em IS NOT NULL", (usuario_id,))
        auditar("usuario_alterado", "usuario", usuario_id,
                {"login": dados["login_ad"] or dados["email"], "perfil": [atual["perfil"], dados["perfil"]],
                 "ativo": dados["ativo"], "unidades": [atual["unidades"], dados["unidades"]]})


def redefinir_senha(usuario_id: int) -> str:
    usuario = obter(usuario_id)
    if not usuario:
        raise ErroNegocio("Usuário não encontrado.")
    if usuario["origem"] != "local":
        raise ErroNegocio("Este usuário entra com a senha do Windows. A troca é feita no AD.")
    temporaria = senha_temporaria()
    with transacao():
        executar("UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 1, tentativas_falhas = 0, bloqueado_ate = NULL, "
                 "versao_sessao = versao_sessao + 1 WHERE id = ?", (hash_senha(temporaria), usuario_id))
        auditar("senha_redefinida", "usuario", usuario_id)
    return temporaria


def excluir(usuario_id: int, usuario_atual_id: int):
    """Só apaga contas que nunca registraram nada; as demais são desativadas para preservar a auditoria."""
    usuario = obter(usuario_id)
    if not usuario:
        raise ErroNegocio("Usuário não encontrado.")
    if usuario_id == usuario_atual_id:
        raise ErroNegocio("Você não pode excluir a sua própria conta.")
    if usuario["perfil"] == "admin" and not _admins_ativos(usuario_id):
        raise ErroNegocio("É preciso manter pelo menos um administrador ativo.")
    try:
        with transacao():
            executar("DELETE FROM usuarios_unidades WHERE usuario_id = ?", (usuario_id,))
            executar("DELETE FROM usuarios WHERE id = ?", (usuario_id,))
            auditar("usuario_excluido", "usuario", usuario_id,
                    {"login": usuario["login_ad"] or usuario["email"], "perfil": usuario["perfil"]})
    except sqlite3.IntegrityError:
        raise ErroNegocio(f"{usuario['nome']} já tem ações registradas no sistema. Para manter o histórico, "
                          "desative o usuário em vez de excluir.") from None


# ------------------------------------------------------------------ cobertura de férias

def liberar_cobertura(usuario_id: int, form, por_id: int):
    usuario = obter(usuario_id)
    if not usuario or usuario["perfil"] != "tecnico":
        raise ErroNegocio("A cobertura temporária é só para técnicos.")
    erros = {}
    try:
        unidade_id = int(form.get("unidade_id") or 0)
    except ValueError:
        unidade_id = 0
    unidade = consultar("SELECT id, nome FROM unidades WHERE id = ?", (unidade_id,), um=True)
    if not unidade:
        erros["unidade_id"] = "Selecione a unidade."
    elif unidade_id in usuario["unidades"]:
        erros["unidade_id"] = "Este técnico já atende esta unidade."
    try:
        ate = ler_data(form.get("ate"))
    except ValueError:
        ate = None
    hoje = agora().astimezone(fuso()).date()
    if not ate or ate < hoje:
        erros["ate"] = "Informe até quando (hoje ou depois)."
    elif ate > hoje + timedelta(days=120):
        erros["ate"] = "A cobertura pode durar no máximo 120 dias."
    if erros:
        raise ErroValidacao(erros)
    expira = para_bd(datetime.combine(ate + timedelta(days=1), time(0, 0), tzinfo=fuso()))
    with transacao():
        executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ?", (usuario_id, unidade_id))
        executar("INSERT INTO usuarios_unidades (usuario_id, unidade_id, criado_em, criado_por, expira_em) "
                 "VALUES (?, ?, ?, ?, ?)", (usuario_id, unidade_id, agora_bd(), por_id, expira))
        auditar("cobertura_liberada", "usuario", usuario_id,
                {"unidade": unidade["nome"], "ate": ate.isoformat(), "tecnico": usuario["nome"]})


def encerrar_cobertura(usuario_id: int, unidade_id: int):
    nome = escalar("SELECT un.nome FROM usuarios_unidades uu JOIN unidades un ON un.id = uu.unidade_id "
                   "WHERE uu.usuario_id = ? AND uu.unidade_id = ? AND uu.expira_em IS NOT NULL", (usuario_id, unidade_id))
    if not nome:
        raise ErroNegocio("Cobertura não encontrada.")
    with transacao():
        executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ? AND expira_em IS NOT NULL",
                 (usuario_id, unidade_id))
        auditar("cobertura_encerrada", "usuario", usuario_id, {"unidade": nome})


def expirar_coberturas() -> str:
    vencidas = consultar("SELECT uu.usuario_id, uu.unidade_id, un.nome AS unidade, u.nome AS tecnico "
                         "FROM usuarios_unidades uu JOIN unidades un ON un.id = uu.unidade_id "
                         "JOIN usuarios u ON u.id = uu.usuario_id WHERE uu.expira_em IS NOT NULL AND uu.expira_em <= ?",
                         (agora_bd(),))
    for linha in vencidas:
        with transacao():
            executar("DELETE FROM usuarios_unidades WHERE usuario_id = ? AND unidade_id = ? AND expira_em IS NOT NULL",
                     (linha["usuario_id"], linha["unidade_id"]))
            auditar("cobertura_expirada", "usuario", linha["usuario_id"],
                    {"unidade": linha["unidade"], "tecnico": linha["tecnico"]})
    return f"{len(vencidas)} cobertura(s) temporária(s) encerrada(s)." if vencidas else ""


# ------------------------------------------------------------------ revisão de acessos (ISO 27001 / TISAX)

def relatorio_acessos(meses: int = 6) -> dict:
    equipe = []
    for usuario in listar_ti():
        unidades = _unidades_do_usuario(usuario["id"])
        equipe.append(dict(usuario, permanentes=unidades["permanentes"], coberturas=unidades["coberturas"]))
    marcas = ",".join("?" * len(ACOES_ACESSO))
    desde = para_bd(agora() - timedelta(days=31 * meses))
    eventos = consultar(f"SELECT a.ocorrido_em, a.usuario_nome, a.acao, a.detalhes, a.entidade_id, u.nome AS alvo "
                        f"FROM auditoria a LEFT JOIN usuarios u ON a.entidade = 'usuario' AND u.id = a.entidade_id "
                        f"WHERE a.acao IN ({marcas}) AND a.ocorrido_em >= ? ORDER BY a.id DESC LIMIT 500",
                        (*ACOES_ACESSO, desde))
    emergencia = consultar("SELECT a.ocorrido_em, a.usuario_nome, a.ip FROM auditoria a WHERE a.acao = 'login' "
                           "AND a.detalhes LIKE '%conta local%' AND a.ocorrido_em >= ? ORDER BY a.id DESC LIMIT 100",
                           (desde,))
    return {"equipe": equipe, "eventos": eventos, "emergencia": emergencia, "meses": meses}
