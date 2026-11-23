"""Entregas, devoluções, transferências e substituições de aparelhos.

Um "vínculo" liga um aparelho a um colaborador do momento da entrega até a devolução.
O banco garante que cada aparelho tenha no máximo um vínculo aberto.
"""
from ..db import consultar, escalar, executar, transacao
from ..seguranca import garantir_acesso_unidade
from ..util import agora_bd, de_bd, fmt_data, ler_datahora_local
from ..validacao import formatar_telefone, ler_data, limpar_texto, limpar_texto_longo
from . import ErroNegocio, ErroValidacao
from . import aparelhos as svc_aparelhos
from . import termos as svc_termos
from .configuracoes import cfg_int
from .registro import movimentar

MOTIVOS_ENCERRAMENTO = {
    "devolucao": "Devolução",
    "transferencia": "Transferência",
    "substituicao": "Substituição",
    "desligamento": "Desligamento",
    "baixa": "Baixa",
}
DESTINOS_DEVOLUCAO = ["devolvido", "disponivel", "manutencao", "baixado"]


def obter(vinculo_id: int):
    vinculo = consultar(
        """SELECT v.*, c.nome AS colaborador_nome, c.email AS colaborador_email, c.unidade_id AS colaborador_unidade_id,
                  c.situacao AS colaborador_situacao, c.departamento AS colaborador_departamento,
                  a.marca, a.modelo, a.imei, a.linha, a.numero_serie, a.status AS aparelho_status,
                  a.estado_conservacao, a.unidade_id AS aparelho_unidade_id, a.acessorios AS aparelho_acessorios,
                  ue.nome AS entregue_por_nome, ur.nome AS recebido_por_nome
             FROM vinculos v
             JOIN colaboradores c ON c.id = v.colaborador_id
             JOIN aparelhos a ON a.id = v.aparelho_id
             LEFT JOIN usuarios ue ON ue.id = v.entregue_por
             LEFT JOIN usuarios ur ON ur.id = v.recebido_por
            WHERE v.id = ?""",
        (vinculo_id,), um=True)
    if vinculo:
        garantir_acesso_unidade(vinculo["aparelho_unidade_id"])
    return vinculo


def _ler_momento(valor, campo: str, erros: dict, padrao_agora: bool = True):
    try:
        momento = ler_datahora_local(valor) if valor else (agora_bd() if padrao_agora else None)
    except ValueError as erro:
        erros[campo] = str(erro)
        return None
    if momento and de_bd(momento) > de_bd(agora_bd()) and (de_bd(momento) - de_bd(agora_bd())).total_seconds() > 300:
        erros[campo] = "A data não pode estar no futuro."
    return momento


def _checar_limite(colaborador_id: int, adicionais: int = 1):
    limite = cfg_int("limite_aparelhos_colaborador", 0)
    if limite <= 0:
        return
    atuais = escalar("SELECT COUNT(*) FROM vinculos WHERE colaborador_id = ? AND devolvido_em IS NULL",
                     (colaborador_id,))
    if atuais + adicionais > limite:
        raise ErroNegocio(f"Este colaborador já tem {atuais} aparelho(s) e o limite da empresa é {limite}. "
                          "Ajuste o limite em Configurações, se necessário.")


def _colaborador_ativo(colaborador_id: int):
    colaborador = consultar("SELECT * FROM colaboradores WHERE id = ?", (colaborador_id,), um=True)
    if not colaborador:
        raise ErroValidacao({"colaborador_id": "Selecione o colaborador."})
    garantir_acesso_unidade(colaborador["unidade_id"])
    if colaborador["situacao"] != "ativo":
        raise ErroNegocio(f"{colaborador['nome']} está desligado(a) e não pode receber aparelhos.")
    return colaborador


def _novo_vinculo(aparelho: dict, colaborador: dict, momento: str, estado: str, acessorios, observacoes,
                  devolucao_prevista, origem_id=None) -> int:
    cursor = executar(
        """INSERT INTO vinculos (aparelho_id, colaborador_id, vinculo_origem_id, entregue_em, entregue_por,
               estado_entrega, acessorios_entrega, observacoes_entrega, devolucao_prevista)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (aparelho["id"], colaborador["id"], origem_id, momento, _usuario(), estado, acessorios, observacoes,
         devolucao_prevista),
    )
    executar("UPDATE aparelhos SET status = 'em_uso', status_desde = ?, estado_conservacao = ?, "
             "atualizado_em = datetime('now') WHERE id = ?", (momento, estado, aparelho["id"]))
    if not colaborador.get("telefone") and aparelho.get("linha"):
        executar("UPDATE colaboradores SET telefone = ?, atualizado_em = datetime('now') WHERE id = ?",
                 (aparelho["linha"], colaborador["id"]))
    return cursor.lastrowid


def _usuario():
    from flask import g
    usuario = g.get("usuario")
    return usuario["id"] if usuario else None


def _dados_entrega(form: dict, erros: dict, estado_padrao: str):
    estado = (form.get("estado") or estado_padrao or "").strip()
    if estado not in svc_aparelhos.ESTADOS:
        erros["estado"] = "Selecione o estado de conservação na entrega."
    acessorios = limpar_texto(form.get("acessorios"), 300) or None
    observacoes = limpar_texto_longo(form.get("observacoes"), 1000) or None
    prevista = None
    try:
        data = ler_data(form.get("devolucao_prevista"))
        prevista = data.isoformat() if data else None
    except ValueError as erro:
        erros["devolucao_prevista"] = str(erro)
    return estado, acessorios, observacoes, prevista


# ------------------------------------------------------------------ entrega

def entregar(form: dict) -> tuple[int, int | None]:
    """Entrega um aparelho disponível a um colaborador. Devolve (vínculo, termo emitido ou None)."""
    erros = {}
    try:
        aparelho_id = int(form.get("aparelho_id") or 0)
        colaborador_id = int(form.get("colaborador_id") or 0)
    except ValueError:
        aparelho_id = colaborador_id = 0
    if not aparelho_id:
        erros["aparelho_id"] = "Selecione o aparelho."
    if not colaborador_id:
        erros["colaborador_id"] = "Selecione o colaborador."
    if erros:
        raise ErroValidacao(erros)

    aparelho = svc_aparelhos.obter(aparelho_id)
    if not aparelho:
        raise ErroValidacao({"aparelho_id": "Aparelho não encontrado."})
    if aparelho["status"] != "disponivel":
        raise ErroNegocio(f"O aparelho {aparelho['marca']} {aparelho['modelo']} não está disponível "
                          f"(status: {svc_aparelhos.STATUS[aparelho['status']]}).")
    colaborador = _colaborador_ativo(colaborador_id)

    momento = _ler_momento(form.get("entregue_em"), "entregue_em", erros)
    estado, acessorios, observacoes, prevista = _dados_entrega(form, erros, aparelho["estado_conservacao"])
    acessorios = acessorios or aparelho.get("acessorios")
    if erros:
        raise ErroValidacao(erros)
    _checar_limite(colaborador_id)

    gerar_termo = form.get("gerar_termo", "1") in ("1", "on")
    termo_id = None
    with transacao():
        vinculo_id = _novo_vinculo(aparelho, colaborador, momento, estado, acessorios, observacoes, prevista)
        texto = f"Entregue a {colaborador['nome']}."
        if prevista:
            texto += f" Devolução prevista para {fmt_data(prevista)}."
        movimentar("entrega", texto, aparelho_id=aparelho_id, colaborador_id=colaborador_id, vinculo_id=vinculo_id,
                   unidade_id=aparelho["unidade_id"], ocorrido_em=momento,
                   detalhes={"estado": estado, "acessorios": acessorios})
        if gerar_termo:
            termo_id = svc_termos.emitir(vinculo_id)
    return vinculo_id, termo_id


# ------------------------------------------------------------------ devolução

def devolver(vinculo_id: int, form: dict):
    vinculo = obter(vinculo_id)
    if not vinculo or vinculo["devolvido_em"]:
        raise ErroNegocio("Esta entrega já foi encerrada.")
    erros = {}
    momento = _ler_momento(form.get("devolvido_em"), "devolvido_em", erros)
    if momento and momento < vinculo["entregue_em"]:
        erros["devolvido_em"] = "A devolução não pode ser anterior à entrega."
    estado = (form.get("estado") or "").strip()
    if estado not in svc_aparelhos.ESTADOS:
        erros["estado"] = "Selecione o estado em que o aparelho foi devolvido."
    destino = form.get("destino") or "devolvido"
    if destino not in DESTINOS_DEVOLUCAO:
        erros["destino"] = "Selecione o que acontece com o aparelho."
    motivo_baixa = form.get("motivo_baixa") or None
    if destino == "baixado" and motivo_baixa not in svc_aparelhos.MOTIVOS_BAIXA:
        erros["motivo_baixa"] = "Selecione o motivo da baixa."
    acessorios = limpar_texto(form.get("acessorios"), 300) or None
    observacoes = limpar_texto_longo(form.get("observacoes"), 1000) or None
    if erros:
        raise ErroValidacao(erros)

    motivo = "baixa" if destino == "baixado" else "devolucao"
    with transacao():
        _encerrar(vinculo, momento, motivo, estado, acessorios, observacoes)
        executar("UPDATE aparelhos SET status = ?, status_desde = ?, estado_conservacao = ?, motivo_baixa = ?, "
                 "atualizado_em = datetime('now') WHERE id = ?",
                 (destino, momento, estado, motivo_baixa if destino == "baixado" else None, vinculo["aparelho_id"]))
        texto = (f"Devolvido por {vinculo['colaborador_nome']} em estado “{estado}”. "
                 f"Novo status: {svc_aparelhos.STATUS[destino]}.")
        if observacoes:
            texto += f" {observacoes}"
        movimentar("devolucao", texto, aparelho_id=vinculo["aparelho_id"], colaborador_id=vinculo["colaborador_id"],
                   vinculo_id=vinculo_id, unidade_id=vinculo["aparelho_unidade_id"], ocorrido_em=momento,
                   detalhes={"estado": estado, "acessorios": acessorios, "destino": destino})
        if destino == "baixado":
            movimentar("baixa", f"Aparelho baixado do patrimônio na devolução. Motivo: {motivo_baixa}.",
                       aparelho_id=vinculo["aparelho_id"], unidade_id=vinculo["aparelho_unidade_id"],
                       ocorrido_em=momento)


def _encerrar(vinculo: dict, momento: str, motivo: str, estado=None, acessorios=None, observacoes=None):
    cursor = executar(
        """UPDATE vinculos SET devolvido_em = ?, recebido_por = ?, estado_devolucao = ?, acessorios_devolucao = ?,
               observacoes_devolucao = ?, motivo_encerramento = ?
            WHERE id = ? AND devolvido_em IS NULL""",
        (momento, _usuario(), estado, acessorios, observacoes, motivo, vinculo["id"]),
    )
    if cursor.rowcount != 1:
        raise ErroNegocio("Esta entrega já foi encerrada por outra pessoa. Atualize a página.")
    svc_termos.cancelar_pendentes_do_vinculo(
        vinculo["id"], "Entrega encerrada antes da assinatura (" + MOTIVOS_ENCERRAMENTO[motivo].lower() + ").")


# ------------------------------------------------------------------ transferência

def transferir(vinculo_id: int, form: dict) -> tuple[int, int | None]:
    """Passa o aparelho direto de um colaborador para outro, encerrando o vínculo anterior."""
    vinculo = obter(vinculo_id)
    if not vinculo or vinculo["devolvido_em"]:
        raise ErroNegocio("Esta entrega já foi encerrada.")
    erros = {}
    try:
        destino_id = int(form.get("colaborador_id") or 0)
    except ValueError:
        destino_id = 0
    if not destino_id:
        erros["colaborador_id"] = "Selecione quem vai receber o aparelho."
    elif destino_id == vinculo["colaborador_id"]:
        erros["colaborador_id"] = "Selecione um colaborador diferente do atual."
    momento = _ler_momento(form.get("entregue_em"), "entregue_em", erros)
    if momento and momento < vinculo["entregue_em"]:
        erros["entregue_em"] = "A transferência não pode ser anterior à entrega atual."
    estado, acessorios, observacoes, prevista = _dados_entrega(form, erros, vinculo["estado_conservacao"])
    if erros:
        raise ErroValidacao(erros)
    destino = _colaborador_ativo(destino_id)
    _checar_limite(destino_id)
    aparelho = svc_aparelhos.obter(vinculo["aparelho_id"])
    acessorios = acessorios or vinculo["acessorios_entrega"]

    termo_id = None
    with transacao():
        _encerrar(vinculo, momento, "transferencia", estado, vinculo["acessorios_entrega"],
                  f"Transferido para {destino['nome']}.")
        novo_id = _novo_vinculo(aparelho, destino, momento, estado, acessorios, observacoes, prevista,
                                origem_id=vinculo_id)
        texto = f"Transferido de {vinculo['colaborador_nome']} para {destino['nome']}."
        if observacoes:
            texto += f" {observacoes}"
        movimentar("transferencia", texto, aparelho_id=aparelho["id"], colaborador_id=vinculo["colaborador_id"],
                   colaborador_destino_id=destino_id, vinculo_id=novo_id, unidade_id=aparelho["unidade_id"],
                   ocorrido_em=momento)
        if form.get("gerar_termo", "1") in ("1", "on"):
            termo_id = svc_termos.emitir(novo_id)
    return novo_id, termo_id


# ------------------------------------------------------------------ substituição

def substituir(vinculo_id: int, form: dict) -> tuple[int, int | None]:
    """Troca o aparelho do colaborador por outro disponível (ex.: defeito, upgrade)."""
    vinculo = obter(vinculo_id)
    if not vinculo or vinculo["devolvido_em"]:
        raise ErroNegocio("Esta entrega já foi encerrada.")
    erros = {}
    try:
        novo_aparelho_id = int(form.get("aparelho_id") or 0)
    except ValueError:
        novo_aparelho_id = 0
    if not novo_aparelho_id:
        erros["aparelho_id"] = "Selecione o novo aparelho."
    momento = _ler_momento(form.get("entregue_em"), "entregue_em", erros)
    if momento and momento < vinculo["entregue_em"]:
        erros["entregue_em"] = "A substituição não pode ser anterior à entrega atual."
    estado_antigo = (form.get("estado_devolucao") or "").strip()
    if estado_antigo not in svc_aparelhos.ESTADOS:
        erros["estado_devolucao"] = "Selecione o estado do aparelho recolhido."
    destino_antigo = form.get("destino") or "manutencao"
    if destino_antigo not in DESTINOS_DEVOLUCAO:
        erros["destino"] = "Selecione o que acontece com o aparelho recolhido."
    motivo_baixa = form.get("motivo_baixa") or None
    if destino_antigo == "baixado" and motivo_baixa not in svc_aparelhos.MOTIVOS_BAIXA:
        erros["motivo_baixa"] = "Selecione o motivo da baixa."
    motivo = limpar_texto(form.get("motivo"), 300)
    if not motivo:
        erros["motivo"] = "Explique o motivo da substituição (ex.: tela quebrada, defeito na bateria)."
    if erros:
        raise ErroValidacao(erros)

    novo = svc_aparelhos.obter(novo_aparelho_id)
    if not novo or novo["status"] != "disponivel":
        raise ErroValidacao({"aparelho_id": "O aparelho escolhido não está disponível."})
    estado_novo = (form.get("estado") or novo["estado_conservacao"]).strip()
    if estado_novo not in svc_aparelhos.ESTADOS:
        raise ErroValidacao({"estado": "Selecione o estado do novo aparelho."})
    colaborador = _colaborador_ativo(vinculo["colaborador_id"])
    acessorios = limpar_texto(form.get("acessorios"), 300) or novo.get("acessorios")
    observacoes = limpar_texto_longo(form.get("observacoes"), 1000) or None

    termo_id = None
    with transacao():
        _encerrar(vinculo, momento, "substituicao", estado_antigo, None, f"Substituído: {motivo}")
        executar("UPDATE aparelhos SET status = ?, status_desde = ?, estado_conservacao = ?, motivo_baixa = ?, "
                 "atualizado_em = datetime('now') WHERE id = ?",
                 (destino_antigo, momento, estado_antigo, motivo_baixa if destino_antigo == "baixado" else None,
                  vinculo["aparelho_id"]))
        novo_vinculo_id = _novo_vinculo(novo, colaborador, momento, estado_novo, acessorios, observacoes, None,
                                        origem_id=vinculo_id)
        antigo_txt = f"{vinculo['marca']} {vinculo['modelo']}"
        novo_txt = f"{novo['marca']} {novo['modelo']}"
        movimentar("substituicao",
                   f"Recolhido de {vinculo['colaborador_nome']} e substituído por {novo_txt}. Motivo: {motivo}. "
                   f"Novo status: {svc_aparelhos.STATUS[destino_antigo]}.",
                   aparelho_id=vinculo["aparelho_id"], colaborador_id=vinculo["colaborador_id"],
                   vinculo_id=vinculo_id, unidade_id=vinculo["aparelho_unidade_id"], ocorrido_em=momento)
        movimentar("substituicao",
                   f"Entregue a {vinculo['colaborador_nome']} em substituição ao {antigo_txt}. Motivo: {motivo}.",
                   aparelho_id=novo["id"], colaborador_id=vinculo["colaborador_id"], vinculo_id=novo_vinculo_id,
                   unidade_id=novo["unidade_id"], ocorrido_em=momento)
        if destino_antigo == "baixado":
            movimentar("baixa", f"Aparelho baixado do patrimônio na substituição. Motivo: {motivo_baixa}.",
                       aparelho_id=vinculo["aparelho_id"], unidade_id=vinculo["aparelho_unidade_id"],
                       ocorrido_em=momento)
        if form.get("gerar_termo", "1") in ("1", "on"):
            termo_id = svc_termos.emitir(novo_vinculo_id)
    return novo_vinculo_id, termo_id


def descricao_curta(vinculo: dict) -> str:
    linha = f", linha {formatar_telefone(vinculo['linha'])}" if vinculo.get("linha") else ""
    return f"{vinculo['marca']} {vinculo['modelo']}{linha}"
