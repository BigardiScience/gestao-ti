"""Cadastro e controle dos aparelhos celulares."""
from datetime import date

from ..db import condicao_busca, consultar, escalar, executar, normalizar, transacao
from ..seguranca import filtro_unidades, garantir_acesso_unidade
from ..util import agora_bd, paginar
from ..validacao import (formatar_telefone, imei_valido, ler_data, ler_valor_centavos, limpar_texto,
                         limpar_texto_longo, normalizar_telefone, somente_digitos, telefone_valido)
from . import ErroNegocio, ErroValidacao
from .registro import auditar, diferencas, movimentar

STATUS = {
    "disponivel": "Disponível",
    "em_uso": "Em utilização",
    "manutencao": "Em manutenção",
    "devolvido": "Devolvido",
    "baixado": "Baixado",
}
STATUS_AJUDA = {
    "disponivel": "Pronto para ser entregue a um colaborador.",
    "em_uso": "Vinculado a um colaborador.",
    "manutencao": "Em conserto ou assistência técnica.",
    "devolvido": "Devolvido e aguardando conferência ou formatação antes de voltar a ficar disponível.",
    "baixado": "Fora do patrimônio (furto, perda, dano sem conserto, venda...).",
}
# Status que o administrador pode escolher ao cadastrar (em utilização só via entrega)
STATUS_CADASTRO = ["disponivel", "manutencao", "devolvido"]
ESTADOS = ["Novo", "Ótimo", "Bom", "Regular", "Danificado"]
MOTIVOS_BAIXA = ["Furto ou roubo", "Perda", "Dano sem conserto", "Obsolescência", "Venda ou doação", "Outro"]
SUGESTOES_MARCAS = ["Samsung", "Apple", "Motorola", "Xiaomi", "Realme", "Asus", "Nokia", "Positivo", "LG"]
SUGESTOES_OPERADORAS = ["Vivo", "Claro", "TIM", "Algar Telecom", "Sercomtel"]

ROTULOS_CAMPOS = {
    "marca": "Marca", "modelo": "Modelo", "numero_serie": "Número de série", "imei": "IMEI",
    "imei2": "IMEI 2", "linha": "Linha", "operadora": "Operadora", "unidade_id": "Unidade",
    "data_aquisicao": "Data de aquisição", "valor_centavos": "Valor", "estado_conservacao": "Estado de conservação",
    "acessorios": "Acessórios", "observacoes": "Observações",
}

SQL_LISTA = """
    SELECT a.*, u.nome AS unidade_nome, u.sigla AS unidade_sigla,
           v.id AS vinculo_id, v.entregue_em, v.devolucao_prevista,
           c.id AS colaborador_id, c.nome AS colaborador_nome, c.departamento AS colaborador_departamento,
           c.situacao AS colaborador_situacao,
           t.id AS termo_id, t.status AS termo_status, t.token_expira_em AS termo_expira_em,
           t.codigo AS termo_codigo, t.assinado_em AS termo_assinado_em
      FROM aparelhos a
      JOIN unidades u ON u.id = a.unidade_id
      LEFT JOIN vinculos v ON v.aparelho_id = a.id AND v.devolvido_em IS NULL
      LEFT JOIN colaboradores c ON c.id = v.colaborador_id
      LEFT JOIN termos t ON t.vinculo_id = v.id AND t.status <> 'cancelado'
"""

EXPRESSAO_BUSCA = ("a.marca || ' ' || a.modelo || ' ' || a.imei || ' ' || coalesce(a.imei2, '') || ' ' || "
                   "coalesce(a.linha, '') || ' ' || a.numero_serie || ' ' || coalesce(a.operadora, '') || ' ' || "
                   "coalesce(c.nome, '')")

ORDENACOES = {
    "recentes": "a.criado_em DESC, a.id DESC",
    "aparelho": "a.marca COLLATE NOCASE, a.modelo COLLATE NOCASE, a.id",
    "imei": "a.imei",
    "linha": "a.linha IS NULL, a.linha",
    "unidade": "u.nome COLLATE NOCASE, a.marca COLLATE NOCASE",
    "status": "a.status, a.marca COLLATE NOCASE",
    "responsavel": "c.nome IS NULL, c.nome COLLATE NOCASE",
}


# ------------------------------------------------------------------ consulta

def montar_filtros(filtros: dict) -> tuple[str, list]:
    sql, parametros = "WHERE 1=1", []
    restricao, p = filtro_unidades("a.unidade_id")
    sql += restricao
    parametros += p
    busca, p = condicao_busca(filtros.get("q"), EXPRESSAO_BUSCA)
    sql += busca
    parametros += p
    if filtros.get("unidade"):
        sql += " AND a.unidade_id = ?"
        parametros.append(int(filtros["unidade"]))
    status = filtros.get("status")
    if status == "ativos":
        sql += " AND a.status <> 'baixado'"
    elif status in STATUS:
        sql += " AND a.status = ?"
        parametros.append(status)
    if filtros.get("operadora"):
        sql += " AND a.operadora = ? COLLATE NOCASE"
        parametros.append(filtros["operadora"])
    if filtros.get("marca"):
        sql += " AND a.marca = ? COLLATE NOCASE"
        parametros.append(filtros["marca"])
    if filtros.get("modelo"):
        sql += " AND normalizar(a.modelo) LIKE ?"
        parametros.append(f"%{normalizar(filtros['modelo'])}%")
    termo = filtros.get("termo")
    if termo == "assinado":
        sql += " AND t.status = 'assinado'"
    elif termo == "pendente":
        sql += " AND t.status IN ('gerado', 'enviado')"
    elif termo == "sem_termo":
        sql += " AND v.id IS NOT NULL AND t.id IS NULL"
    if filtros.get("responsavel") == "sem":
        sql += " AND v.id IS NULL"
    elif filtros.get("responsavel") == "com":
        sql += " AND v.id IS NOT NULL"
    if filtros.get("devolucao") == "atrasada":
        from ..util import agora_local
        sql += " AND v.devolucao_prevista IS NOT NULL AND v.devolucao_prevista < ?"
        parametros.append(agora_local().date().isoformat())
    if filtros.get("colaborador"):
        sql += " AND normalizar(c.nome) LIKE ?"
        parametros.append(f"%{normalizar(filtros['colaborador'])}%")
    if filtros.get("departamento"):
        sql += " AND c.departamento = ? COLLATE NOCASE"
        parametros.append(filtros["departamento"])
    return sql, parametros


def listar(filtros: dict, ordem: str = "recentes", por_pagina: int = 25):
    where, parametros = montar_filtros(filtros)
    ordenacao = ORDENACOES.get(ordem, ORDENACOES["recentes"])
    return paginar(f"{SQL_LISTA} {where} ORDER BY {ordenacao}", parametros, por_pagina=por_pagina)


def listar_todos(filtros: dict, ordem: str = "aparelho"):
    where, parametros = montar_filtros(filtros)
    return consultar(f"{SQL_LISTA} {where} ORDER BY {ORDENACOES.get(ordem, ORDENACOES['aparelho'])}", parametros)


def obter(aparelho_id: int, verificar_acesso: bool = True):
    aparelho = consultar(f"{SQL_LISTA} WHERE a.id = ?", (aparelho_id,), um=True)
    if aparelho and verificar_acesso:
        garantir_acesso_unidade(aparelho["unidade_id"])
    return aparelho


def buscar_por_imei(imei: str):
    numeros = somente_digitos(imei)
    if len(numeros) != 15:
        return None
    restricao, p = filtro_unidades("a.unidade_id")
    return consultar(f"SELECT a.id FROM aparelhos a WHERE (a.imei = ? OR a.imei2 = ?) {restricao}",
                     [numeros, numeros] + p, um=True)


def opcoes_disponiveis(texto: str = "", aparelho_id: int | None = None, limite: int = 20) -> list[dict]:
    """Aparelhos disponíveis para o campo de escolha com busca (entrega e troca)."""
    restricao, parametros = filtro_unidades("a.unidade_id")
    busca, p = condicao_busca(texto, "a.marca || ' ' || a.modelo || ' ' || a.imei || ' ' || coalesce(a.linha, '') || ' ' || "
                                     "a.numero_serie || ' ' || u.nome")
    sql = (f"SELECT a.id, a.marca, a.modelo, a.imei, a.linha, u.nome AS unidade_nome FROM aparelhos a "
           f"JOIN unidades u ON u.id = a.unidade_id WHERE a.status = 'disponivel' {restricao} {busca}")
    parametros += p
    if aparelho_id:
        sql += " AND a.id = ?"
        parametros.append(aparelho_id)
    opcoes = []
    for linha in consultar(sql + " ORDER BY a.marca COLLATE NOCASE, a.modelo COLLATE NOCASE, a.id LIMIT ?",
                           parametros + [limite]):
        detalhe = f"IMEI {linha['imei']}"
        if linha["linha"]:
            detalhe += f" · {formatar_telefone(linha['linha'])}"
        opcoes.append({"id": linha["id"], "titulo": f"{linha['marca']} {linha['modelo']}",
                       "detalhe": f"{detalhe} · {linha['unidade_nome']}"})
    return opcoes


def opcao_disponivel(aparelho_id) -> dict | None:
    try:
        numero = int(aparelho_id)
    except (TypeError, ValueError):
        return None
    opcoes = opcoes_disponiveis(aparelho_id=numero, limite=1)
    return opcoes[0] if opcoes else None


def existe_disponivel() -> bool:
    restricao, parametros = filtro_unidades("unidade_id")
    return bool(escalar(f"SELECT 1 FROM aparelhos WHERE status = 'disponivel' {restricao} LIMIT 1", parametros))


def historico(aparelho_id: int):
    return consultar(
        """SELECT m.*, us.nome AS usuario_nome, c.nome AS colaborador_nome, cd.nome AS destino_nome,
                  t.codigo AS termo_codigo
             FROM movimentacoes m
             LEFT JOIN usuarios us ON us.id = m.usuario_id
             LEFT JOIN colaboradores c ON c.id = m.colaborador_id
             LEFT JOIN colaboradores cd ON cd.id = m.colaborador_destino_id
             LEFT JOIN termos t ON t.id = m.termo_id
            WHERE m.aparelho_id = ?
            ORDER BY m.ocorrido_em DESC, m.id DESC""",
        (aparelho_id,),
    )


def vinculos(aparelho_id: int):
    return consultar(
        """SELECT v.*, c.nome AS colaborador_nome, c.departamento, ue.nome AS entregue_por_nome,
                  ur.nome AS recebido_por_nome,
                  (SELECT t.id FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado') AS termo_id,
                  (SELECT t.status FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado') AS termo_status,
                  (SELECT t.codigo FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado') AS termo_codigo
             FROM vinculos v
             JOIN colaboradores c ON c.id = v.colaborador_id
             LEFT JOIN usuarios ue ON ue.id = v.entregue_por
             LEFT JOIN usuarios ur ON ur.id = v.recebido_por
            WHERE v.aparelho_id = ?
            ORDER BY v.entregue_em DESC, v.id DESC""",
        (aparelho_id,),
    )


def valores_distintos(coluna: str):
    if coluna not in ("marca", "operadora", "modelo"):
        raise ValueError(coluna)
    restricao, parametros = filtro_unidades("unidade_id")
    return [l["v"] for l in consultar(
        f"SELECT DISTINCT {coluna} AS v FROM aparelhos WHERE {coluna} IS NOT NULL AND {coluna} <> '' {restricao} "
        f"ORDER BY {coluna} COLLATE NOCASE", parametros)]


def descrever(aparelho: dict) -> str:
    """Texto curto que identifica o aparelho em históricos e e-mails."""
    final_imei = (aparelho.get("imei") or "")[-6:]
    return f"{aparelho['marca']} {aparelho['modelo']} (IMEI final {final_imei})"


# ------------------------------------------------------------------ cadastro

def validar(form: dict, aparelho_id: int | None = None) -> dict:
    erros = {}
    dados = {
        "marca": limpar_texto(form.get("marca"), 60),
        "modelo": limpar_texto(form.get("modelo"), 80),
        "numero_serie": limpar_texto(form.get("numero_serie"), 60).upper(),
        "imei": somente_digitos(form.get("imei")),
        "imei2": somente_digitos(form.get("imei2")) or None,
        "linha": normalizar_telefone(form.get("linha")) or None,
        "operadora": limpar_texto(form.get("operadora"), 40) or None,
        "estado_conservacao": (form.get("estado_conservacao") or "").strip(),
        "acessorios": limpar_texto(form.get("acessorios"), 300) or None,
        "observacoes": limpar_texto_longo(form.get("observacoes"), 2000) or None,
        "data_aquisicao": None,
        "valor_centavos": None,
        "unidade_id": None,
    }
    for campo, rotulo in (("marca", "a marca"), ("modelo", "o modelo"), ("numero_serie", "o número de série")):
        if not dados[campo]:
            erros[campo] = f"Informe {rotulo}."

    if not dados["imei"]:
        erros["imei"] = "Informe o IMEI (disque *#06# no aparelho para consultar)."
    elif not imei_valido(form.get("imei")):
        erros["imei"] = "IMEI inválido: são 15 dígitos e o último é verificador. Confira a digitação."
    if dados["imei2"]:
        if not imei_valido(form.get("imei2")):
            erros["imei2"] = "IMEI 2 inválido. Confira a digitação."
        elif dados["imei2"] == dados["imei"]:
            erros["imei2"] = "O IMEI 2 deve ser diferente do IMEI principal."

    outro = aparelho_id or 0
    if "imei" not in erros and dados["imei"]:
        repetido = consultar("SELECT id, marca, modelo FROM aparelhos WHERE (imei = ? OR imei2 = ?) AND id <> ?",
                             (dados["imei"], dados["imei"], outro), um=True)
        if repetido:
            erros["imei"] = f"Este IMEI já está cadastrado ({repetido['marca']} {repetido['modelo']})."
    if dados["imei2"] and "imei2" not in erros:
        if escalar("SELECT id FROM aparelhos WHERE (imei = ? OR imei2 = ?) AND id <> ?",
                   (dados["imei2"], dados["imei2"], outro)):
            erros["imei2"] = "Este IMEI já está cadastrado em outro aparelho."
    if dados["numero_serie"] and escalar(
            "SELECT id FROM aparelhos WHERE numero_serie = ? COLLATE NOCASE AND id <> ?", (dados["numero_serie"], outro)):
        erros["numero_serie"] = "Já existe um aparelho com este número de série."

    if dados["linha"]:
        if not telefone_valido(dados["linha"]):
            erros["linha"] = "Linha inválida. Informe o DDD e o número, por exemplo (11) 98765-4321."
        else:
            dono = consultar("SELECT id, marca, modelo FROM aparelhos WHERE linha = ? AND status <> 'baixado' AND id <> ?",
                             (dados["linha"], outro), um=True)
            if dono:
                erros["linha"] = (f"A linha {formatar_telefone(dados['linha'])} já está no aparelho "
                                  f"{dono['marca']} {dono['modelo']}. Retire-a de lá antes.")
        if not dados["operadora"]:
            erros["operadora"] = "Informe a operadora da linha."

    try:
        dados["unidade_id"] = int(form.get("unidade_id") or 0) or None
    except ValueError:
        dados["unidade_id"] = None
    if not dados["unidade_id"]:
        erros["unidade_id"] = "Selecione a unidade."
    else:
        unidade = consultar("SELECT id, ativo FROM unidades WHERE id = ?", (dados["unidade_id"],), um=True)
        if not unidade:
            erros["unidade_id"] = "Unidade não encontrada."
        elif not unidade["ativo"] and not aparelho_id:
            erros["unidade_id"] = "Esta unidade está inativa."

    if dados["estado_conservacao"] not in ESTADOS:
        erros["estado_conservacao"] = "Selecione o estado de conservação."

    try:
        aquisicao = ler_data(form.get("data_aquisicao"))
        if aquisicao and aquisicao > date.today():
            erros["data_aquisicao"] = "A data de aquisição não pode estar no futuro."
        dados["data_aquisicao"] = aquisicao.isoformat() if aquisicao else None
    except ValueError as erro:
        erros["data_aquisicao"] = str(erro)
    try:
        dados["valor_centavos"] = ler_valor_centavos(form.get("valor"))
    except ValueError as erro:
        erros["valor"] = str(erro)

    if aparelho_id is None:
        status = form.get("status") or "disponivel"
        if status not in STATUS_CADASTRO:
            erros["status"] = "Selecione um status válido. Para colocar em utilização, use “Entregar aparelho”."
        dados["status"] = status

    if erros:
        raise ErroValidacao(erros)
    return dados


def criar(form: dict, origem: str = "cadastro") -> int:
    dados = validar(form)
    garantir_acesso_unidade(dados["unidade_id"])
    with transacao():
        cursor = executar(
            """INSERT INTO aparelhos (marca, modelo, numero_serie, imei, imei2, linha, operadora, unidade_id,
                   data_aquisicao, valor_centavos, estado_conservacao, status, acessorios, observacoes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (dados["marca"], dados["modelo"], dados["numero_serie"], dados["imei"], dados["imei2"], dados["linha"],
             dados["operadora"], dados["unidade_id"], dados["data_aquisicao"], dados["valor_centavos"],
             dados["estado_conservacao"], dados["status"], dados["acessorios"], dados["observacoes"]),
        )
        aparelho_id = cursor.lastrowid
        texto = "Aparelho cadastrado" + (" pela importação de planilha" if origem == "importacao" else "")
        movimentar("cadastro", f"{texto} com status “{STATUS[dados['status']]}”.",
                   aparelho_id=aparelho_id, unidade_id=dados["unidade_id"])
    return aparelho_id


def atualizar(aparelho_id: int, form: dict):
    atual = obter(aparelho_id)
    if not atual:
        raise ErroNegocio("Aparelho não encontrado.")
    dados = validar(form, aparelho_id)
    garantir_acesso_unidade(dados["unidade_id"])
    mudancas = diferencas(atual, dados, ROTULOS_CAMPOS)
    if not mudancas:
        return False
    if "Unidade" in mudancas:
        nomes = {u["id"]: u["nome"] for u in consultar("SELECT id, nome FROM unidades")}
        mudancas["Unidade"] = [nomes.get(v, v) for v in mudancas["Unidade"]]
    with transacao():
        executar(
            """UPDATE aparelhos SET marca = ?, modelo = ?, numero_serie = ?, imei = ?, imei2 = ?, linha = ?,
                   operadora = ?, unidade_id = ?, data_aquisicao = ?, valor_centavos = ?, estado_conservacao = ?,
                   acessorios = ?, observacoes = ?, atualizado_em = datetime('now')
                 WHERE id = ?""",
            (dados["marca"], dados["modelo"], dados["numero_serie"], dados["imei"], dados["imei2"], dados["linha"],
             dados["operadora"], dados["unidade_id"], dados["data_aquisicao"], dados["valor_centavos"],
             dados["estado_conservacao"], dados["acessorios"], dados["observacoes"], aparelho_id),
        )
        movimentar("alteracao", "Dados alterados: " + ", ".join(mudancas.keys()) + ".",
                   aparelho_id=aparelho_id, unidade_id=dados["unidade_id"], detalhes=mudancas)
    return True


TRANSICOES = {
    "disponivel": {"manutencao", "devolvido", "baixado"},
    "manutencao": {"disponivel", "devolvido", "baixado"},
    "devolvido": {"disponivel", "manutencao", "baixado"},
    "baixado": {"disponivel"},
}


def alterar_status(aparelho_id: int, novo_status: str, motivo: str | None = None, observacao: str | None = None):
    aparelho = obter(aparelho_id)
    if not aparelho:
        raise ErroNegocio("Aparelho não encontrado.")
    atual = aparelho["status"]
    if atual == "em_uso":
        raise ErroNegocio("O aparelho está com um colaborador. Registre a devolução, a transferência ou a "
                          "substituição antes de mudar o status.")
    if novo_status not in TRANSICOES.get(atual, set()):
        raise ErroNegocio(f"Não é possível mudar de “{STATUS.get(atual)}” para “{STATUS.get(novo_status, novo_status)}”.")
    observacao = limpar_texto(observacao, 300) or None
    if novo_status == "baixado":
        if motivo not in MOTIVOS_BAIXA:
            raise ErroValidacao({"motivo": "Selecione o motivo da baixa."})
        if motivo == "Outro" and not observacao:
            raise ErroValidacao({"observacao": "Descreva o motivo da baixa."})
    if atual == "baixado" and not observacao:
        raise ErroValidacao({"observacao": "Explique por que o aparelho está voltando ao patrimônio."})
    with transacao():
        if atual == "baixado" and aparelho["linha"] and escalar(
                "SELECT id FROM aparelhos WHERE linha = ? AND status <> 'baixado' AND id <> ?",
                (aparelho["linha"], aparelho_id)):
            raise ErroNegocio("A linha deste aparelho já está em uso em outro aparelho. Edite o cadastro e "
                              "remova a linha antes de reativá-lo.")
        executar(
            """UPDATE aparelhos SET status = ?, status_desde = ?, motivo_baixa = ?, atualizado_em = datetime('now')
                WHERE id = ?""",
            (novo_status, agora_bd(), motivo if novo_status == "baixado" else None, aparelho_id),
        )
        if novo_status == "baixado":
            texto = f"Aparelho baixado do patrimônio. Motivo: {motivo}."
        elif atual == "baixado":
            texto = "Aparelho reativado no patrimônio."
        else:
            texto = f"Status alterado de “{STATUS[atual]}” para “{STATUS[novo_status]}”."
        if observacao:
            texto += f" {observacao}"
        movimentar("baixa" if novo_status == "baixado" else "status", texto, aparelho_id=aparelho_id,
                   unidade_id=aparelho["unidade_id"],
                   detalhes={"de": STATUS[atual], "para": STATUS[novo_status], "motivo": motivo})


def excluir(aparelho_id: int):
    aparelho = obter(aparelho_id)
    if not aparelho:
        raise ErroNegocio("Aparelho não encontrado.")
    if escalar("SELECT COUNT(*) FROM vinculos WHERE aparelho_id = ?", (aparelho_id,)):
        raise ErroNegocio("Este aparelho já teve entregas registradas. Para manter o histórico, use “Baixar” "
                          "em vez de excluir.")
    with transacao():
        executar("DELETE FROM movimentacoes WHERE aparelho_id = ?", (aparelho_id,))
        executar("DELETE FROM aparelhos WHERE id = ?", (aparelho_id,))
        auditar("aparelho_excluido", "aparelho", aparelho_id,
                {"aparelho": f"{aparelho['marca']} {aparelho['modelo']}", "imei": aparelho["imei"]})
