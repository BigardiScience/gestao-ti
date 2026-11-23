"""Cadastro dos colaboradores que recebem aparelhos."""
from ..db import condicao_busca, consultar, escalar, executar, normalizar, transacao
from ..seguranca import filtro_unidades, garantir_acesso_unidade
from ..util import agora_bd, paginar
from ..validacao import (cpf_valido, email_valido, formatar_cpf, limpar_texto, limpar_texto_longo,
                         normalizar_telefone, somente_digitos, telefone_valido)
from . import ErroNegocio, ErroValidacao
from .registro import auditar, diferencas, movimentar

SITUACOES = {"ativo": "Ativo", "desligado": "Desligado"}

ROTULOS_CAMPOS = {
    "nome": "Nome", "cpf": "CPF", "email": "E-mail", "matricula": "Matrícula", "cargo": "Cargo",
    "departamento": "Departamento", "unidade_id": "Unidade", "telefone": "Telefone corporativo",
    "observacoes": "Observações",
}

SQL_LISTA = """
    SELECT c.*, u.nome AS unidade_nome,
           (SELECT COUNT(*) FROM vinculos v WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL) AS aparelhos_ativos,
           (SELECT COUNT(*) FROM termos t WHERE t.colaborador_id = c.id AND t.status IN ('gerado', 'enviado'))
               AS termos_pendentes,
           (SELECT group_concat(a.marca || ' ' || a.modelo, ', ')
              FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id
             WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL) AS aparelhos_descricao
      FROM colaboradores c
      JOIN unidades u ON u.id = c.unidade_id
"""

EXPRESSAO_BUSCA = ("c.nome || ' ' || c.cpf || ' ' || c.email || ' ' || coalesce(c.matricula, '') || ' ' || "
                   "c.cargo || ' ' || c.departamento || ' ' || coalesce(c.telefone, '')")

ORDENACOES = {
    "nome": "c.nome COLLATE NOCASE",
    "recentes": "c.criado_em DESC, c.id DESC",
    "departamento": "c.departamento COLLATE NOCASE, c.nome COLLATE NOCASE",
    "unidade": "u.nome COLLATE NOCASE, c.nome COLLATE NOCASE",
    "aparelhos": "aparelhos_ativos DESC, c.nome COLLATE NOCASE",
}


def montar_filtros(filtros: dict) -> tuple[str, list]:
    sql, parametros = "WHERE 1=1", []
    restricao, p = filtro_unidades("c.unidade_id")
    sql += restricao
    parametros += p
    busca, p = condicao_busca(filtros.get("q"), EXPRESSAO_BUSCA)
    sql += busca
    parametros += p
    if filtros.get("unidade"):
        sql += " AND c.unidade_id = ?"
        parametros.append(int(filtros["unidade"]))
    if filtros.get("departamento"):
        sql += " AND c.departamento = ? COLLATE NOCASE"
        parametros.append(filtros["departamento"])
    if filtros.get("nome"):
        sql += " AND normalizar(c.nome) LIKE ?"
        parametros.append(f"%{normalizar(filtros['nome'])}%")
    situacao = filtros.get("situacao")
    if situacao in SITUACOES:
        sql += " AND c.situacao = ?"
        parametros.append(situacao)
    aparelho = filtros.get("aparelho")
    if aparelho == "com":
        sql += " AND EXISTS (SELECT 1 FROM vinculos v WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL)"
    elif aparelho == "sem":
        sql += " AND NOT EXISTS (SELECT 1 FROM vinculos v WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL)"
    termo = filtros.get("termo")
    if termo == "pendente":
        sql += " AND EXISTS (SELECT 1 FROM termos t WHERE t.colaborador_id = c.id AND t.status IN ('gerado','enviado'))"
    elif termo == "assinado":
        sql += (" AND EXISTS (SELECT 1 FROM vinculos v JOIN termos t ON t.vinculo_id = v.id AND t.status = 'assinado'"
                " WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL)")
    return sql, parametros


def listar(filtros: dict, ordem: str = "nome", por_pagina: int = 25):
    where, parametros = montar_filtros(filtros)
    return paginar(f"{SQL_LISTA} {where} ORDER BY {ORDENACOES.get(ordem, ORDENACOES['nome'])}", parametros,
                   por_pagina=por_pagina)


def obter(colaborador_id: int, verificar_acesso: bool = True):
    colaborador = consultar(f"{SQL_LISTA} WHERE c.id = ?", (colaborador_id,), um=True)
    if colaborador and verificar_acesso:
        garantir_acesso_unidade(colaborador["unidade_id"])
    return colaborador


def obter_por_email(email: str):
    return consultar("SELECT * FROM colaboradores WHERE email = ? COLLATE NOCASE AND situacao = 'ativo'",
                     ((email or "").strip(),), um=True)


def localizar(cpf_ou_email: str):
    """Usado na importação: encontra o colaborador pelo CPF ou pelo e-mail."""
    valor = (cpf_ou_email or "").strip()
    if not valor:
        return None
    if "@" in valor:
        return consultar("SELECT * FROM colaboradores WHERE email = ? COLLATE NOCASE ORDER BY situacao = 'ativo' DESC",
                         (valor,), um=True)
    return consultar("SELECT * FROM colaboradores WHERE cpf = ?", (somente_digitos(valor).zfill(11),), um=True)


def aparelhos_ativos(colaborador_id: int):
    return consultar(
        """SELECT v.id AS vinculo_id, v.entregue_em, v.devolucao_prevista, v.estado_entrega, v.acessorios_entrega,
                  a.id AS aparelho_id, a.marca, a.modelo, a.imei, a.linha, a.operadora, a.numero_serie,
                  t.id AS termo_id, t.status AS termo_status, t.codigo AS termo_codigo,
                  t.token_expira_em AS termo_expira_em, t.assinado_em AS termo_assinado_em
             FROM vinculos v
             JOIN aparelhos a ON a.id = v.aparelho_id
             LEFT JOIN termos t ON t.vinculo_id = v.id AND t.status <> 'cancelado'
            WHERE v.colaborador_id = ? AND v.devolvido_em IS NULL
            ORDER BY v.entregue_em DESC""",
        (colaborador_id,),
    )


def historico_vinculos(colaborador_id: int):
    return consultar(
        """SELECT v.*, a.marca, a.modelo, a.imei, a.linha,
                  (SELECT t.id FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado') AS termo_id,
                  (SELECT t.status FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado') AS termo_status
             FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id
            WHERE v.colaborador_id = ? AND v.devolvido_em IS NOT NULL
            ORDER BY v.devolvido_em DESC""",
        (colaborador_id,),
    )


def termos(colaborador_id: int):
    return consultar(
        """SELECT t.*, a.marca, a.modelo, a.imei FROM termos t JOIN aparelhos a ON a.id = t.aparelho_id
            WHERE t.colaborador_id = ? ORDER BY t.emitido_em DESC, t.id DESC""",
        (colaborador_id,),
    )


def historico(colaborador_id: int):
    return consultar(
        """SELECT m.*, us.nome AS usuario_nome, a.marca, a.modelo, cd.nome AS destino_nome, co.nome AS colaborador_nome
             FROM movimentacoes m
             LEFT JOIN usuarios us ON us.id = m.usuario_id
             LEFT JOIN aparelhos a ON a.id = m.aparelho_id
             LEFT JOIN colaboradores cd ON cd.id = m.colaborador_destino_id
             LEFT JOIN colaboradores co ON co.id = m.colaborador_id
            WHERE m.colaborador_id = ? OR m.colaborador_destino_id = ?
            ORDER BY m.ocorrido_em DESC, m.id DESC LIMIT 100""",
        (colaborador_id, colaborador_id),
    )


def departamentos():
    restricao, parametros = filtro_unidades("unidade_id")
    return [l["d"] for l in consultar(
        f"SELECT DISTINCT departamento AS d FROM colaboradores WHERE departamento <> '' {restricao} "
        "ORDER BY departamento COLLATE NOCASE", parametros)]


def cargos():
    return [l["c"] for l in consultar("SELECT DISTINCT cargo AS c FROM colaboradores ORDER BY cargo COLLATE NOCASE")]


def opcoes_selecao(texto: str = "", excluir: int | None = None, colaborador_id: int | None = None,
                   limite: int = 20) -> list[dict]:
    """Colaboradores ativos para o campo de escolha com busca (entrega e transferência)."""
    restricao, parametros = filtro_unidades("c.unidade_id")
    busca, p = condicao_busca(texto, "c.nome || ' ' || c.departamento || ' ' || u.nome || ' ' || "
                                     "coalesce(c.matricula, '') || ' ' || c.email")
    sql = f"""SELECT c.id, c.nome, c.departamento, u.nome AS unidade_nome,
                     (SELECT COUNT(*) FROM vinculos v WHERE v.colaborador_id = c.id AND v.devolvido_em IS NULL)
                         AS aparelhos_ativos
                FROM colaboradores c JOIN unidades u ON u.id = c.unidade_id
               WHERE c.situacao = 'ativo' {restricao} {busca}"""
    parametros += p
    if excluir:
        sql += " AND c.id <> ?"
        parametros.append(excluir)
    if colaborador_id:
        sql += " AND c.id = ?"
        parametros.append(colaborador_id)
    opcoes = []
    for linha in consultar(sql + " ORDER BY c.nome COLLATE NOCASE LIMIT ?", parametros + [limite]):
        detalhe = f"{linha['departamento']} · {linha['unidade_nome']}"
        if linha["aparelhos_ativos"]:
            detalhe += f" · já tem {linha['aparelhos_ativos']} aparelho{'s' if linha['aparelhos_ativos'] > 1 else ''}"
        opcoes.append({"id": linha["id"], "titulo": linha["nome"], "detalhe": detalhe})
    return opcoes


def opcao_selecao(colaborador_id) -> dict | None:
    """A opção já escolhida (ao voltar ao formulário com erro ou vindo da página do colaborador)."""
    try:
        numero = int(colaborador_id)
    except (TypeError, ValueError):
        return None
    opcoes = opcoes_selecao(colaborador_id=numero, limite=1)
    return opcoes[0] if opcoes else None


def existe_ativo() -> bool:
    restricao, parametros = filtro_unidades("unidade_id")
    return bool(escalar(f"SELECT 1 FROM colaboradores WHERE situacao = 'ativo' {restricao} LIMIT 1", parametros))


# ------------------------------------------------------------------ cadastro

def validar(form: dict, colaborador_id: int | None = None) -> dict:
    erros = {}
    dados = {
        "nome": limpar_texto(form.get("nome"), 150),
        "cpf": somente_digitos(form.get("cpf")),
        "email": (form.get("email") or "").strip().lower(),
        "matricula": limpar_texto(form.get("matricula"), 30) or None,
        "cargo": limpar_texto(form.get("cargo"), 80),
        "departamento": limpar_texto(form.get("departamento"), 80),
        "telefone": normalizar_telefone(form.get("telefone")) or None,
        "observacoes": limpar_texto_longo(form.get("observacoes"), 2000) or None,
        "unidade_id": None,
    }
    outro = colaborador_id or 0
    if len(dados["nome"]) < 3:
        erros["nome"] = "Informe o nome completo."
    if not dados["cpf"]:
        erros["cpf"] = "Informe o CPF."
    elif len(dados["cpf"]) < 11 and dados["cpf"].isdigit() and len(dados["cpf"]) >= 9:
        dados["cpf"] = dados["cpf"].zfill(11)  # planilhas costumam perder os zeros à esquerda
    if dados["cpf"] and "cpf" not in erros:
        if not cpf_valido(dados["cpf"]):
            erros["cpf"] = "CPF inválido. Confira os dígitos."
        else:
            existente = consultar("SELECT id, nome FROM colaboradores WHERE cpf = ? AND id <> ?",
                                  (dados["cpf"], outro), um=True)
            if existente:
                erros["cpf"] = f"Este CPF já está cadastrado ({existente['nome']})."
    if not dados["email"]:
        erros["email"] = "Informe o e-mail corporativo: é para ele que o termo será enviado."
    elif not email_valido(dados["email"]):
        erros["email"] = "E-mail inválido."
    else:
        existente = consultar(
            "SELECT id, nome FROM colaboradores WHERE email = ? COLLATE NOCASE AND situacao = 'ativo' AND id <> ?",
            (dados["email"], outro), um=True)
        if existente:
            erros["email"] = f"Este e-mail já pertence a outro colaborador ativo ({existente['nome']})."
    if not dados["cargo"]:
        erros["cargo"] = "Informe o cargo ou a função."
    if not dados["departamento"]:
        erros["departamento"] = "Informe o departamento."
    if dados["telefone"] and not telefone_valido(dados["telefone"]):
        erros["telefone"] = "Telefone inválido. Informe o DDD e o número."
    try:
        dados["unidade_id"] = int(form.get("unidade_id") or 0) or None
    except ValueError:
        dados["unidade_id"] = None
    if not dados["unidade_id"]:
        erros["unidade_id"] = "Selecione a unidade de trabalho."
    elif not escalar("SELECT id FROM unidades WHERE id = ?", (dados["unidade_id"],)):
        erros["unidade_id"] = "Unidade não encontrada."
    if erros:
        raise ErroValidacao(erros)
    return dados


def criar(form: dict, origem: str = "cadastro") -> int:
    dados = validar(form)
    garantir_acesso_unidade(dados["unidade_id"])
    with transacao():
        cursor = executar(
            """INSERT INTO colaboradores (nome, cpf, email, matricula, cargo, departamento, unidade_id, telefone,
                   observacoes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dados["nome"], dados["cpf"], dados["email"], dados["matricula"], dados["cargo"], dados["departamento"],
             dados["unidade_id"], dados["telefone"], dados["observacoes"]),
        )
        colaborador_id = cursor.lastrowid
        texto = "Colaborador cadastrado" + (" pela importação de planilha." if origem == "importacao" else ".")
        movimentar("colaborador_cadastro", texto, colaborador_id=colaborador_id, unidade_id=dados["unidade_id"])
    return colaborador_id


def atualizar(colaborador_id: int, form: dict) -> bool:
    atual = obter(colaborador_id)
    if not atual:
        raise ErroNegocio("Colaborador não encontrado.")
    dados = validar(form, colaborador_id)
    garantir_acesso_unidade(dados["unidade_id"])
    mudancas = diferencas(atual, dados, ROTULOS_CAMPOS)
    if not mudancas:
        return False
    if "CPF" in mudancas:
        mudancas["CPF"] = [formatar_cpf(v) for v in mudancas["CPF"]]
    if "Unidade" in mudancas:
        nomes = {u["id"]: u["nome"] for u in consultar("SELECT id, nome FROM unidades")}
        mudancas["Unidade"] = [nomes.get(v, v) for v in mudancas["Unidade"]]
    with transacao():
        executar(
            """UPDATE colaboradores SET nome = ?, cpf = ?, email = ?, matricula = ?, cargo = ?, departamento = ?,
                   unidade_id = ?, telefone = ?, observacoes = ?, atualizado_em = datetime('now') WHERE id = ?""",
            (dados["nome"], dados["cpf"], dados["email"], dados["matricula"], dados["cargo"], dados["departamento"],
             dados["unidade_id"], dados["telefone"], dados["observacoes"], colaborador_id),
        )
        movimentar("colaborador_alteracao", "Dados alterados: " + ", ".join(mudancas.keys()) + ".",
                   colaborador_id=colaborador_id, unidade_id=dados["unidade_id"], detalhes=mudancas)
    return True


def desligar(colaborador_id: int, observacao: str | None = None) -> int:
    """Marca o colaborador como desligado. Devolve quantos aparelhos ainda estão com ele."""
    colaborador = obter(colaborador_id)
    if not colaborador:
        raise ErroNegocio("Colaborador não encontrado.")
    if colaborador["situacao"] == "desligado":
        raise ErroNegocio("Este colaborador já está desligado.")
    with transacao():
        executar("UPDATE colaboradores SET situacao = 'desligado', desligado_em = ?, atualizado_em = datetime('now') "
                 "WHERE id = ?", (agora_bd(), colaborador_id))
        texto = "Colaborador desligado."
        if colaborador["aparelhos_ativos"]:
            texto += f" Ainda está com {colaborador['aparelhos_ativos']} aparelho(s): devolução pendente."
        if observacao:
            texto += f" {limpar_texto(observacao, 300)}"
        movimentar("colaborador_desligamento", texto, colaborador_id=colaborador_id,
                   unidade_id=colaborador["unidade_id"])
    return colaborador["aparelhos_ativos"]


def reativar(colaborador_id: int):
    colaborador = obter(colaborador_id)
    if not colaborador or colaborador["situacao"] != "desligado":
        raise ErroNegocio("Colaborador não encontrado ou já ativo.")
    if escalar("SELECT id FROM colaboradores WHERE email = ? COLLATE NOCASE AND situacao = 'ativo' AND id <> ?",
               (colaborador["email"], colaborador_id)):
        raise ErroNegocio("O e-mail deste colaborador agora pertence a outro colaborador ativo. "
                          "Edite o e-mail antes de reativar.")
    with transacao():
        executar("UPDATE colaboradores SET situacao = 'ativo', desligado_em = NULL, atualizado_em = datetime('now') "
                 "WHERE id = ?", (colaborador_id,))
        movimentar("colaborador_reativacao", "Colaborador reativado.", colaborador_id=colaborador_id,
                   unidade_id=colaborador["unidade_id"])


def excluir(colaborador_id: int):
    colaborador = obter(colaborador_id)
    if not colaborador:
        raise ErroNegocio("Colaborador não encontrado.")
    if escalar("SELECT COUNT(*) FROM vinculos WHERE colaborador_id = ?", (colaborador_id,)):
        raise ErroNegocio("Este colaborador já recebeu aparelhos. Para manter o histórico, use “Desligar” "
                          "em vez de excluir.")
    with transacao():
        executar("DELETE FROM codigos_verificacao WHERE colaborador_id = ?", (colaborador_id,))
        executar("UPDATE emails SET colaborador_id = NULL WHERE colaborador_id = ?", (colaborador_id,))
        executar("DELETE FROM movimentacoes WHERE colaborador_id = ? OR colaborador_destino_id = ?",
                 (colaborador_id, colaborador_id))
        executar("DELETE FROM colaboradores WHERE id = ?", (colaborador_id,))
        auditar("colaborador_excluido", "colaborador", colaborador_id, {"nome": colaborador["nome"]})
