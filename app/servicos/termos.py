"""Termos de responsabilidade: emissão, envio, assinatura eletrônica e verificação.

Como funciona a assinatura eletrônica
-------------------------------------
1. Na emissão, os dados do colaborador e do aparelho são "congelados" num conteúdo
   (JSON). O hash SHA-256 desse conteúdo identifica exatamente o que será assinado.
2. O colaborador recebe no e-mail corporativo um link exclusivo (token aleatório de
   256 bits; o banco guarda só o hash do token e uma cópia cifrada para os lembretes).
3. Para assinar ele confirma o CPF, digita o nome completo, informa o código de
   6 dígitos enviado ao e-mail (opcional, ligado por padrão), desenha ou digita a
   assinatura e aceita o termo.
4. O sistema registra data/hora (UTC e horário local), IP, navegador, método de
   autenticação e a trilha de eventos; sela esse registro com HMAC-SHA256 e gera o
   PDF final com uma página de evidências. O hash SHA-256 do PDF fica guardado e
   permite verificar a autenticidade do arquivo em /verificar.
"""
import json
import re
from datetime import timedelta
from pathlib import Path

from flask import current_app, render_template

from ..db import condicao_busca, consultar, escalar, executar, normalizar, transacao
from ..seguranca import (cifrar, decifrar, filtro_unidades, garantir_acesso_unidade, hash_token, limitador,
                         novo_codigo_numerico, novo_token, selo)
from ..util import (agora, agora_bd, agora_local, codigo_legivel, data_extenso, de_bd, fmt_data, fmt_datahora,
                    ip_cliente, json_canonico, limites_dia_utc, local, navegador_cliente, para_bd, paginar, sha256)
from ..validacao import (formatar_cnpj, formatar_cpf, formatar_moeda, formatar_telefone, ler_data, mascarar_email,
                         somente_digitos)
from . import ErroNegocio, ErroValidacao
from .configuracoes import cfg, cfg_bool, cfg_int, emails_notificacao, nome_empresa, url_base
from .registro import ATUAL, movimentar, usuario_atual_id

STATUS = {
    "gerado": "Aguardando envio",
    "enviado": "Aguardando assinatura",
    "assinado": "Assinado",
    "cancelado": "Cancelado",
    "expirado": "Link expirado",
}
FILTROS_STATUS = {
    "pendente": "Pendentes (todos os não assinados)",
    "gerado": "Aguardando envio",
    "enviado": "Aguardando assinatura",
    "expirado": "Link expirado",
    "assinado": "Assinados",
    "cancelado": "Cancelados",
}
EVENTOS = {
    "emitido": "Termo emitido",
    "enviado": "Link de assinatura enviado",
    "reenviado": "Link reenviado",
    "lembrete": "Lembrete enviado",
    "falha_envio": "Falha no envio do e-mail",
    "visualizado": "Termo aberto pelo colaborador",
    "codigo_enviado": "Código de confirmação enviado",
    "codigo_verificado": "Código de confirmação conferido",
    "assinado": "Termo assinado",
    "cancelado": "Termo cancelado",
    "download": "PDF baixado",
}

# Campos que podem ser usados no texto do termo (Configurações > Termo)
CAMPOS_MODELO = {
    "empresa_nome": "Nome da empresa",
    "empresa_cnpj": "CNPJ da empresa",
    "colaborador_nome": "Nome do colaborador",
    "colaborador_cpf": "CPF do colaborador",
    "colaborador_matricula": "Matrícula",
    "colaborador_cargo": "Cargo",
    "colaborador_departamento": "Departamento",
    "colaborador_unidade": "Unidade do colaborador",
    "colaborador_email": "E-mail corporativo",
    "aparelho_marca": "Marca do aparelho",
    "aparelho_modelo": "Modelo do aparelho",
    "aparelho_serie": "Número de série",
    "aparelho_imei": "IMEI",
    "aparelho_linha": "Linha telefônica",
    "aparelho_operadora": "Operadora",
    "aparelho_estado": "Estado de conservação na entrega",
    "aparelho_acessorios": "Acessórios entregues",
    "aparelho_valor": "Valor de referência",
    "data_entrega": "Data da entrega",
    "codigo_termo": "Número do termo",
}

SQL_TERMO = """
    SELECT t.*, c.nome AS colaborador_nome, c.cpf AS colaborador_cpf, c.email AS colaborador_email,
           c.departamento AS colaborador_departamento, c.situacao AS colaborador_situacao,
           a.marca, a.modelo, a.imei, a.linha, a.unidade_id AS aparelho_unidade_id,
           un.nome AS unidade_nome, v.entregue_em, v.devolvido_em AS vinculo_encerrado_em,
           v.vinculo_origem_id, ue.nome AS emitido_por_nome, uc.nome AS cancelado_por_nome
      FROM termos t
      JOIN colaboradores c ON c.id = t.colaborador_id
      JOIN aparelhos a ON a.id = t.aparelho_id
      JOIN unidades un ON un.id = a.unidade_id
      JOIN vinculos v ON v.id = t.vinculo_id
      LEFT JOIN usuarios ue ON ue.id = t.emitido_por
      LEFT JOIN usuarios uc ON uc.id = t.cancelado_por
"""


# ------------------------------------------------------------------ utilidades

def status_efetivo(termo: dict) -> str:
    if termo["status"] == "enviado" and termo.get("token_expira_em") and termo["token_expira_em"] < agora_bd():
        return "expirado"
    return termo["status"]


def _usuario_id():
    return usuario_atual_id()


def registrar_evento(termo_id: int, evento: str, detalhes=None, *, usuario_id=ATUAL, ip=None, user_agent=None):
    executar(
        "INSERT INTO termo_eventos (termo_id, evento, ocorrido_em, usuario_id, ip, user_agent, detalhes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (termo_id, evento, agora_bd(), _usuario_id() if usuario_id is ATUAL else usuario_id,
         ip if ip is not None else ip_cliente(), user_agent if user_agent is not None else navegador_cliente(),
         json.dumps(detalhes, ensure_ascii=False) if isinstance(detalhes, (dict, list)) else detalhes),
    )


def eventos(termo_id: int):
    return consultar(
        "SELECT e.*, u.nome AS usuario_nome FROM termo_eventos e LEFT JOIN usuarios u ON u.id = e.usuario_id "
        "WHERE e.termo_id = ? ORDER BY e.ocorrido_em, e.id", (termo_id,))


def _proximo_codigo() -> str:
    ano = agora_local().year
    nome = f"termo-{ano}"
    executar("INSERT INTO contadores (nome, valor) VALUES (?, 1) ON CONFLICT(nome) DO UPDATE SET valor = valor + 1",
             (nome,))
    numero = escalar("SELECT valor FROM contadores WHERE nome = ?", (nome,))
    return f"TR-{ano}-{numero:06d}"


def renderizar_texto(texto: str, valores: dict) -> str:
    def trocar(m):
        chave = m.group(1).strip()
        return str(valores.get(chave, m.group(0))) if valores.get(chave) not in (None, "") else (
            "—" if chave in valores else m.group(0))
    return re.sub(r"\{\{\s*([a-z_]+)\s*\}\}", trocar, texto or "")


def campos_desconhecidos(texto: str) -> list[str]:
    return sorted({m for m in re.findall(r"\{\{\s*([a-z_]+)\s*\}\}", texto or "") if m not in CAMPOS_MODELO})


def paragrafos(texto: str) -> list[str]:
    blocos = re.split(r"\n\s*\n", (texto or "").strip())
    return [" ".join(b.split()) for b in blocos if b.strip()]


# ------------------------------------------------------------------ emissão

def montar_conteudo(vinculo_id: int, codigo: str, emitido_em: str) -> dict:
    v = consultar(
        """SELECT v.*, c.nome, c.cpf, c.email, c.matricula, c.cargo, c.departamento,
                  uc.nome AS unidade_colaborador, uc.cidade AS cidade_colaborador, uc.uf AS uf_colaborador,
                  a.marca, a.modelo, a.numero_serie, a.imei, a.imei2, a.linha, a.operadora, a.valor_centavos,
                  a.acessorios AS acessorios_aparelho, ua.nome AS unidade_aparelho
             FROM vinculos v
             JOIN colaboradores c ON c.id = v.colaborador_id
             JOIN unidades uc ON uc.id = c.unidade_id
             JOIN aparelhos a ON a.id = v.aparelho_id
             JOIN unidades ua ON ua.id = a.unidade_id
            WHERE v.id = ?""", (vinculo_id,), um=True)
    if not v:
        raise ErroNegocio("Entrega não encontrada.")
    entrega_local = local(v["entregue_em"])
    acessorios = v["acessorios_entrega"] or ""
    valores = {
        "empresa_nome": nome_empresa(),
        "empresa_cnpj": formatar_cnpj(cfg("empresa_cnpj")) if cfg("empresa_cnpj") else "",
        "colaborador_nome": v["nome"],
        "colaborador_cpf": formatar_cpf(v["cpf"]),
        "colaborador_matricula": v["matricula"] or "",
        "colaborador_cargo": v["cargo"],
        "colaborador_departamento": v["departamento"],
        "colaborador_unidade": v["unidade_colaborador"],
        "colaborador_email": v["email"],
        "aparelho_marca": v["marca"],
        "aparelho_modelo": v["modelo"],
        "aparelho_serie": v["numero_serie"],
        "aparelho_imei": v["imei"],
        "aparelho_linha": formatar_telefone(v["linha"]) if v["linha"] else "",
        "aparelho_operadora": v["operadora"] or "",
        "aparelho_estado": v["estado_entrega"] or "",
        "aparelho_acessorios": acessorios,
        "aparelho_valor": formatar_moeda(v["valor_centavos"]) if v["valor_centavos"] else "",
        "data_entrega": entrega_local.strftime("%d/%m/%Y"),
        "codigo_termo": codigo,
    }
    cidade = v["cidade_colaborador"] or cfg("empresa_cidade")
    if cidade and v["uf_colaborador"] and v["cidade_colaborador"]:
        cidade = f"{cidade}/{v['uf_colaborador']}"
    local_data = f"{cidade}, {data_extenso(entrega_local)}" if cidade else data_extenso(entrega_local)

    colaborador = [["Nome", v["nome"]], ["CPF", formatar_cpf(v["cpf"])]]
    if v["matricula"]:
        colaborador.append(["Matrícula", v["matricula"]])
    colaborador += [["Cargo", v["cargo"]], ["Departamento", v["departamento"]],
                    ["Unidade", v["unidade_colaborador"]], ["E-mail corporativo", v["email"]]]
    aparelho = [["Marca e modelo", f"{v['marca']} {v['modelo']}"], ["Número de série", v["numero_serie"]],
                ["IMEI", v["imei"]]]
    if v["imei2"]:
        aparelho.append(["IMEI 2", v["imei2"]])
    aparelho += [["Linha", valores["aparelho_linha"] or "Sem linha"], ["Operadora", v["operadora"] or "—"],
                 ["Estado de conservação", v["estado_entrega"] or "—"], ["Acessórios", acessorios or "Nenhum"]]
    if v["valor_centavos"]:
        aparelho.append(["Valor de referência", valores["aparelho_valor"]])
    aparelho.append(["Data da entrega", entrega_local.strftime("%d/%m/%Y %H:%M")])
    if v["devolucao_prevista"]:
        aparelho.append(["Devolução prevista", fmt_data(v["devolucao_prevista"])])

    return {
        "formato": 1,
        "codigo": codigo,
        "titulo": cfg("termo_titulo"),
        "empresa": {"nome": nome_empresa(), "cnpj": valores["empresa_cnpj"]},
        "emitido_em": emitido_em,
        "modelo_versao": cfg_int("termo_versao", 1),
        "secoes": [
            {"titulo": "Identificação do colaborador", "campos": colaborador},
            {"titulo": "Aparelho entregue", "campos": aparelho},
        ],
        "texto": renderizar_texto(cfg("termo_texto"), valores),
        "observacoes": v["observacoes_entrega"] or "",
        "local_data": local_data,
        "signatario": {"nome": v["nome"], "cpf": formatar_cpf(v["cpf"]), "email": v["email"]},
    }


def emitir(vinculo_id: int) -> int:
    vinculo = consultar("SELECT * FROM vinculos WHERE id = ?", (vinculo_id,), um=True)
    if not vinculo:
        raise ErroNegocio("Entrega não encontrada.")
    if vinculo["devolvido_em"]:
        raise ErroNegocio("Esta entrega já foi encerrada; não é possível emitir termo para ela.")
    if escalar("SELECT id FROM termos WHERE vinculo_id = ? AND status <> 'cancelado'", (vinculo_id,)):
        raise ErroNegocio("Já existe um termo para esta entrega. Cancele-o antes de emitir outro.")
    with transacao():
        emitido_em = agora_bd()
        codigo = _proximo_codigo()
        conteudo = montar_conteudo(vinculo_id, codigo, emitido_em)
        cursor = executar(
            """INSERT INTO termos (codigo, vinculo_id, colaborador_id, aparelho_id, status, conteudo_json,
                   hash_conteudo, modelo_versao, emitido_em, emitido_por)
               VALUES (?, ?, ?, ?, 'gerado', ?, ?, ?, ?, ?)""",
            (codigo, vinculo_id, vinculo["colaborador_id"], vinculo["aparelho_id"], json_canonico(conteudo),
             sha256(json_canonico(conteudo)), conteudo["modelo_versao"], emitido_em, _usuario_id()),
        )
        termo_id = cursor.lastrowid
        registrar_evento(termo_id, "emitido", {"hash_conteudo": sha256(json_canonico(conteudo))})
        unidade = escalar("SELECT unidade_id FROM aparelhos WHERE id = ?", (vinculo["aparelho_id"],))
        movimentar("termo_emitido", f"Termo {codigo} emitido para {conteudo['signatario']['nome']}.",
                   aparelho_id=vinculo["aparelho_id"], colaborador_id=vinculo["colaborador_id"],
                   vinculo_id=vinculo_id, termo_id=termo_id, unidade_id=unidade)
    return termo_id


def conteudo(termo: dict) -> dict:
    return json.loads(termo["conteudo_json"])


# ------------------------------------------------------------------ consulta

def obter(termo_id: int, verificar_acesso: bool = True):
    termo = consultar(f"{SQL_TERMO} WHERE t.id = ?", (termo_id,), um=True)
    if termo:
        if verificar_acesso:
            garantir_acesso_unidade(termo["aparelho_unidade_id"])
        termo["status_efetivo"] = status_efetivo(termo)
    return termo


def montar_filtros(filtros: dict) -> tuple[str, list]:
    sql, parametros = "WHERE 1=1", []
    restricao, p = filtro_unidades("a.unidade_id")
    sql += restricao
    parametros += p
    busca, p = condicao_busca(filtros.get("q"), "t.codigo || ' ' || c.nome || ' ' || a.marca || ' ' || a.modelo "
                                                "|| ' ' || a.imei || ' ' || c.cpf")
    sql += busca
    parametros += p
    status = filtros.get("status")
    agora_txt = agora_bd()
    if status == "pendente":
        sql += " AND t.status IN ('gerado', 'enviado')"
    elif status == "expirado":
        sql += " AND t.status = 'enviado' AND t.token_expira_em < ?"
        parametros.append(agora_txt)
    elif status == "enviado":
        sql += " AND t.status = 'enviado' AND (t.token_expira_em IS NULL OR t.token_expira_em >= ?)"
        parametros.append(agora_txt)
    elif status in ("gerado", "assinado", "cancelado"):
        sql += " AND t.status = ?"
        parametros.append(status)
    if filtros.get("unidade"):
        sql += " AND a.unidade_id = ?"
        parametros.append(int(filtros["unidade"]))
    if filtros.get("colaborador"):
        sql += " AND normalizar(c.nome) LIKE ?"
        parametros.append(f"%{normalizar(filtros['colaborador'])}%")
    if filtros.get("departamento"):
        sql += " AND c.departamento = ? COLLATE NOCASE"
        parametros.append(filtros["departamento"])
    if filtros.get("marca"):
        sql += " AND a.marca = ? COLLATE NOCASE"
        parametros.append(filtros["marca"])
    if filtros.get("modelo"):
        sql += " AND normalizar(a.modelo) LIKE ?"
        parametros.append(f"%{normalizar(filtros['modelo'])}%")
    for chave, operador, fim in (("de", ">=", False), ("ate", "<=", True)):
        try:
            dia = ler_data(filtros.get(chave))
        except ValueError:
            dia = None
        if dia:
            sql += f" AND t.emitido_em {operador} ?"
            parametros.append(limites_dia_utc(dia, fim=fim))
    return sql, parametros


ORDENACOES = {
    "recentes": "t.emitido_em DESC, t.id DESC",
    "colaborador": "c.nome COLLATE NOCASE, t.emitido_em DESC",
    "assinatura": "t.assinado_em IS NULL, t.assinado_em DESC",
    "codigo": "t.codigo DESC",
}


def listar(filtros: dict, ordem: str = "recentes", por_pagina: int = 25):
    where, parametros = montar_filtros(filtros)
    pagina = paginar(f"{SQL_TERMO} {where} ORDER BY {ORDENACOES.get(ordem, ORDENACOES['recentes'])}", parametros,
                     por_pagina=por_pagina)
    for termo in pagina.itens:
        termo["status_efetivo"] = status_efetivo(termo)
    return pagina


def listar_todos(filtros: dict):
    where, parametros = montar_filtros(filtros)
    termos = consultar(f"{SQL_TERMO} {where} ORDER BY t.emitido_em DESC, t.id DESC", parametros)
    for termo in termos:
        termo["status_efetivo"] = status_efetivo(termo)
    return termos


def contagens() -> dict:
    """Quantidade de termos por situação (usada nas abas e no contador do menu, em toda página)."""
    restricao, parametros = filtro_unidades("a.unidade_id")
    # sem restrição de unidade não precisa juntar com aparelhos: a contagem sai direto do índice por status
    origem = "termos t JOIN aparelhos a ON a.id = t.aparelho_id" if restricao else "termos t"
    linha = consultar(
        f"""SELECT t.status, COUNT(*) AS n, SUM(t.status = 'enviado' AND t.token_expira_em < ?) AS expirados
              FROM {origem} WHERE 1=1 {restricao} GROUP BY t.status""",
        [agora_bd()] + parametros)
    resultado = {"gerado": 0, "enviado": 0, "expirado": 0, "assinado": 0, "cancelado": 0}
    for item in linha:
        resultado[item["status"]] = int(item["n"])
        if item["status"] == "enviado":
            resultado["expirado"] = int(item["expirados"] or 0)
            resultado["enviado"] -= resultado["expirado"]
    resultado["pendente"] = resultado.get("gerado", 0) + resultado.get("enviado", 0) + resultado.get("expirado", 0)
    return resultado


def vinculos_sem_termo():
    """Entregas em aberto que não têm termo vigente (ex.: importadas da planilha antiga)."""
    restricao, parametros = filtro_unidades("a.unidade_id")
    return consultar(
        f"""SELECT v.id, v.entregue_em, c.nome AS colaborador_nome, a.marca, a.modelo, a.id AS aparelho_id
              FROM vinculos v
              JOIN colaboradores c ON c.id = v.colaborador_id
              JOIN aparelhos a ON a.id = v.aparelho_id
             WHERE v.devolvido_em IS NULL
               AND NOT EXISTS (SELECT 1 FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado')
               {restricao}
             ORDER BY v.entregue_em""", parametros)


# ------------------------------------------------------------------ envio por e-mail

def _link(token: str) -> str:
    return f"{url_base()}/assinar/{token}"


def enviar(termo_id: int, *, lembrete: bool = False, usuario_id=None):
    """Envia (ou reenvia) o link de assinatura. Devolve o resultado do envio do e-mail."""
    from . import email as svc_email
    termo = obter(termo_id, verificar_acesso=False)
    if not termo:
        raise ErroNegocio("Termo não encontrado.")
    if termo["status"] not in ("gerado", "enviado"):
        raise ErroNegocio(f"Este termo está “{STATUS[termo['status']]}” e não pode ser enviado.")
    if termo["vinculo_encerrado_em"]:
        raise ErroNegocio("A entrega deste termo já foi encerrada.")

    validade = max(1, cfg_int("link_validade_dias", 7))
    token = None
    expira = de_bd(termo["token_expira_em"]) if termo["token_expira_em"] else None
    if termo["token_cifrado"] and expira and expira - agora() > timedelta(hours=24):
        token = decifrar(termo["token_cifrado"]) or None
    if not token:
        token = novo_token()
        expira = agora() + timedelta(days=validade)
        with transacao():
            executar("UPDATE termos SET token_hash = ?, token_cifrado = ?, token_expira_em = ? WHERE id = ?",
                     (hash_token(token), cifrar(token), para_bd(expira), termo_id))

    dados_c = conteudo(termo)
    contexto = {
        "termo": termo, "conteudo": dados_c, "link": _link(token), "expira_em": fmt_datahora(para_bd(expira)),
        "empresa": nome_empresa(), "contato_ti": cfg("ti_email_contato"), "lembrete": lembrete,
        "primeiro_nome": termo["colaborador_nome"].split()[0],
    }
    if lembrete:
        assunto = "Lembrete: o seu termo de responsabilidade aguarda assinatura"
    else:
        assunto = "Assine o termo de responsabilidade do seu celular corporativo"
    html = render_template("emails/termo.html", **contexto)
    quem = usuario_id if usuario_id is not None else _usuario_id()
    resultado = svc_email.enviar(
        termo["colaborador_email"], assunto, html, finalidade="lembrete" if lembrete else "termo",
        termo_id=termo_id, colaborador_id=termo["colaborador_id"], usuario_id=quem, ocultar=[token],
    )
    with transacao():
        if not resultado.ok:
            registrar_evento(termo_id, "falha_envio", {"erro": resultado.erro}, usuario_id=quem)
            return resultado
        agora_txt = agora_bd()
        if lembrete:
            executar("UPDATE termos SET lembretes = lembretes + 1, ultimo_lembrete_em = ? WHERE id = ?",
                     (agora_txt, termo_id))
            registrar_evento(termo_id, "lembrete", {"para": termo["colaborador_email"]}, usuario_id=quem)
        else:
            primeiro = termo["status"] == "gerado"
            executar("""UPDATE termos SET status = 'enviado', enviado_em = coalesce(enviado_em, ?),
                            ultimo_envio_em = ?, envios = envios + 1 WHERE id = ?""",
                     (agora_txt, agora_txt, termo_id))
            registrar_evento(termo_id, "enviado" if primeiro else "reenviado",
                             {"para": termo["colaborador_email"], "valido_ate": para_bd(expira)},
                             usuario_id=quem)
            movimentar("termo_enviado",
                       f"Termo {termo['codigo']} {'enviado' if primeiro else 'reenviado'} para "
                       f"{termo['colaborador_email']}.",
                       aparelho_id=termo["aparelho_id"], colaborador_id=termo["colaborador_id"],
                       vinculo_id=termo["vinculo_id"], termo_id=termo_id, unidade_id=termo["aparelho_unidade_id"],
                       usuario_id=quem)
    return resultado


def cancelar(termo_id: int, motivo: str):
    termo = obter(termo_id)
    if not termo:
        raise ErroNegocio("Termo não encontrado.")
    if termo["status"] not in ("gerado", "enviado"):
        raise ErroNegocio("Só é possível cancelar termos que ainda não foram assinados.")
    motivo = " ".join((motivo or "").split())[:300]
    if not motivo:
        raise ErroValidacao({"motivo": "Informe o motivo do cancelamento."})
    with transacao():
        _cancelar(termo, motivo)


def _cancelar(termo: dict, motivo: str):
    executar("""UPDATE termos SET status = 'cancelado', cancelado_em = ?, cancelado_por = ?, motivo_cancelamento = ?,
                    token_hash = NULL, token_cifrado = NULL WHERE id = ? AND status IN ('gerado', 'enviado')""",
             (agora_bd(), _usuario_id(), motivo, termo["id"]))
    registrar_evento(termo["id"], "cancelado", {"motivo": motivo})
    movimentar("termo_cancelado", f"Termo {termo['codigo']} cancelado. Motivo: {motivo}",
               aparelho_id=termo["aparelho_id"], colaborador_id=termo["colaborador_id"],
               vinculo_id=termo["vinculo_id"], termo_id=termo["id"],
               unidade_id=escalar("SELECT unidade_id FROM aparelhos WHERE id = ?", (termo["aparelho_id"],)))


def cancelar_pendentes_do_vinculo(vinculo_id: int, motivo: str):
    for termo in consultar("SELECT * FROM termos WHERE vinculo_id = ? AND status IN ('gerado', 'enviado')",
                           (vinculo_id,)):
        _cancelar(termo, motivo)


# ------------------------------------------------------------------ acesso pelo link

def localizar_por_token(token: str):
    """Devolve (termo, situação). Situação: 'ok', 'invalido', 'expirado', 'assinado' ou 'cancelado'."""
    if not token or len(token) > 100:
        return None, "invalido"
    termo = consultar(f"{SQL_TERMO} WHERE t.token_hash = ?", (hash_token(token),), um=True)
    if not termo:
        return None, "invalido"
    termo["status_efetivo"] = status_efetivo(termo)
    if termo["status"] == "assinado":
        return termo, "assinado"
    if termo["status"] == "cancelado":
        return termo, "cancelado"
    if termo["status"] != "enviado":
        return None, "invalido"
    if termo["status_efetivo"] == "expirado":
        return termo, "expirado"
    return termo, "ok"


def marcar_visualizado(termo: dict, metodo: str = "link"):
    with transacao():
        if not termo.get("visualizado_em"):
            executar("UPDATE termos SET visualizado_em = ? WHERE id = ? AND visualizado_em IS NULL",
                     (agora_bd(), termo["id"]))
        ultimo = escalar("SELECT ocorrido_em FROM termo_eventos WHERE termo_id = ? AND evento = 'visualizado' "
                         "ORDER BY id DESC LIMIT 1", (termo["id"],))
        # registra no máximo uma visualização a cada 10 minutos
        if not ultimo or (agora() - de_bd(ultimo)).total_seconds() > 600:
            registrar_evento(termo["id"], "visualizado", {"via": metodo}, usuario_id=None)


def solicitar_codigo(termo: dict, cpf_informado: str) -> str:
    """Confere o CPF e envia o código de 6 dígitos ao e-mail do colaborador. Devolve o e-mail mascarado."""
    from . import email as svc_email
    chave_cpf = f"cpf-termo:{termo['id']}"
    if limitador.excedeu(chave_cpf, 6, 900):
        raise ErroNegocio("Muitas tentativas. Aguarde alguns minutos e tente novamente.")
    if somente_digitos(cpf_informado) != termo["colaborador_cpf"]:
        limitador.registrar(chave_cpf)
        raise ErroValidacao({"cpf": "O CPF não confere com o cadastro deste termo."})
    chave_envio = f"codigo-termo:{termo['id']}"
    if limitador.excedeu(chave_envio, 3, 600):
        raise ErroNegocio("Já enviamos alguns códigos. Confira a sua caixa de entrada (e o lixo eletrônico) "
                          "ou aguarde 10 minutos para pedir outro.")
    limitador.registrar(chave_envio)
    codigo = novo_codigo_numerico()
    with transacao():
        executar("UPDATE codigos_verificacao SET usado_em = ? WHERE termo_id = ? AND finalidade = 'assinatura' "
                 "AND usado_em IS NULL", (agora_bd(), termo["id"]))
        executar("""INSERT INTO codigos_verificacao (finalidade, colaborador_id, termo_id, codigo_hash, expira_em, ip)
                    VALUES ('assinatura', ?, ?, ?, ?, ?)""",
                 (termo["colaborador_id"], termo["id"], hash_token(f"{termo['id']}:{codigo}"),
                  para_bd(agora() + timedelta(minutes=15)), ip_cliente()))
    html = render_template("emails/codigo.html", codigo=codigo, finalidade="assinatura", termo=termo,
                           empresa=nome_empresa(), primeiro_nome=termo["colaborador_nome"].split()[0])
    resultado = svc_email.enviar(termo["colaborador_email"], f"Código para assinar o termo: {codigo}", html,
                                 finalidade="codigo_assinatura", termo_id=termo["id"],
                                 colaborador_id=termo["colaborador_id"], ocultar=[codigo],
                                 assunto_registro="Código para assinar o termo")
    with transacao():
        registrar_evento(termo["id"], "codigo_enviado" if resultado.ok else "falha_envio",
                         {"para": mascarar_email(termo["colaborador_email"])}, usuario_id=None)
    if not resultado.ok:
        raise ErroNegocio("Não conseguimos enviar o código agora. Tente novamente em instantes ou fale com o TI.")
    return mascarar_email(termo["colaborador_email"])


def _conferir_codigo(termo: dict, codigo: str) -> bool:
    codigo = somente_digitos(codigo)
    registro = consultar("""SELECT * FROM codigos_verificacao WHERE termo_id = ? AND finalidade = 'assinatura'
                              AND usado_em IS NULL ORDER BY id DESC LIMIT 1""", (termo["id"],), um=True)
    if not registro or registro["expira_em"] < agora_bd() or registro["tentativas"] >= 5:
        return False
    if hash_token(f"{termo['id']}:{codigo}") != registro["codigo_hash"]:
        executar("UPDATE codigos_verificacao SET tentativas = tentativas + 1 WHERE id = ?", (registro["id"],))
        return False
    executar("UPDATE codigos_verificacao SET usado_em = ? WHERE id = ?", (agora_bd(), registro["id"]))
    return True


# ------------------------------------------------------------------ assinatura

def validar_tracos(texto: str) -> dict | None:
    """Confere o desenho da assinatura enviado pelo navegador (lista de traços com pontos x,y)."""
    try:
        dados = json.loads(texto or "")
        largura, altura = float(dados["w"]), float(dados["h"])
        tracos = dados["tracos"]
    except (ValueError, KeyError, TypeError):
        return None
    if not (50 <= largura <= 5000 and 30 <= altura <= 3000) or not isinstance(tracos, list) or not tracos:
        return None
    if len(tracos) > 120 or sum(len(t) for t in tracos if isinstance(t, list)) > 12000:
        return None
    limpos, comprimento = [], 0.0
    for traco in tracos:
        if not isinstance(traco, list):
            return None
        pontos = []
        for ponto in traco:
            if not (isinstance(ponto, list) and len(ponto) == 2):
                return None
            x, y = float(ponto[0]), float(ponto[1])
            if not (-20 <= x <= largura + 20 and -20 <= y <= altura + 20):
                return None
            if pontos:
                comprimento += ((x - pontos[-1][0]) ** 2 + (y - pontos[-1][1]) ** 2) ** 0.5
            pontos.append([round(x, 1), round(y, 1)])
        if pontos:
            limpos.append(pontos)
    if comprimento < 40:
        return None
    return {"w": round(largura, 1), "h": round(altura, 1), "tracos": limpos}


def _nome_confere(digitado: str, cadastrado: str) -> bool:
    return normalizar(digitado) == normalizar(cadastrado)


def assinar(termo_id: int, form: dict, *, metodo: str = "link", ip: str = "", user_agent: str = "") -> dict:
    """Valida os dados da assinatura e conclui o termo. `metodo`: 'link' (e-mail) ou 'portal'."""
    termo = obter(termo_id, verificar_acesso=False)
    if not termo or termo["status"] != "enviado":
        raise ErroNegocio("Este termo não está disponível para assinatura.")
    if metodo == "link" and status_efetivo(termo) == "expirado":
        raise ErroNegocio("O link de assinatura expirou. Peça ao TI para reenviar o termo.")

    erros = {}
    if somente_digitos(form.get("cpf")) != termo["colaborador_cpf"]:
        erros["cpf"] = "O CPF não confere com o cadastro deste termo."
    nome = " ".join((form.get("nome") or "").split())
    if not nome:
        erros["nome"] = "Digite o seu nome completo."
    elif not _nome_confere(nome, termo["colaborador_nome"]):
        erros["nome"] = f"O nome digitado não confere com o cadastro: {termo['colaborador_nome']}."
    tipo = form.get("tipo_assinatura") or "desenho"
    desenho = None
    if tipo == "desenho":
        desenho = validar_tracos(form.get("tracos"))
        if not desenho:
            erros["tracos"] = "Desenhe a sua assinatura no quadro (ou escolha “Digitar”)."
    elif tipo != "digitada":
        erros["tracos"] = "Escolha como deseja assinar."
    if form.get("aceite") not in ("1", "on", "sim"):
        erros["aceite"] = "Para assinar, confirme que leu e concorda com o termo."
    exige_codigo = metodo == "link" and cfg_bool("assinatura_exigir_codigo")
    if not erros and exige_codigo and not _conferir_codigo(termo, form.get("codigo")):
        erros["codigo"] = "Código incorreto ou expirado. Peça um novo código."
    if erros:
        raise ErroValidacao(erros)

    assinado_em = agora_bd()
    codigo_verificacao = codigo_legivel()
    dados_c = conteudo(termo)
    trilha = []
    for e in eventos(termo_id):
        if e["evento"] in ("download", "falha_envio"):
            continue
        if e["usuario_nome"]:
            por = e["usuario_nome"]
        elif e["evento"] in ("enviado", "reenviado", "lembrete"):
            por = "Sistema"
        else:
            por = termo["colaborador_nome"]
        trilha.append({"evento": EVENTOS.get(e["evento"], e["evento"]), "quando": e["ocorrido_em"],
                       "ip": e["ip"] or "", "por": por})
    trilha.append({"evento": EVENTOS["assinado"], "quando": assinado_em, "ip": ip, "por": termo["colaborador_nome"]})
    if metodo == "portal":
        autenticacao = "Acesso ao portal do colaborador com código enviado ao e-mail corporativo"
    elif exige_codigo:
        autenticacao = "Link exclusivo enviado ao e-mail corporativo + código de confirmação enviado ao mesmo e-mail"
    else:
        autenticacao = "Link exclusivo enviado ao e-mail corporativo"
    evidencias = {
        "documento": {"codigo": termo["codigo"], "titulo": dados_c["titulo"], "hash_conteudo": termo["hash_conteudo"],
                      "emitido_em": termo["emitido_em"]},
        "signatario": {"nome": termo["colaborador_nome"], "cpf": formatar_cpf(termo["colaborador_cpf"]),
                       "email": termo["colaborador_email"], "nome_digitado": nome},
        "autenticacao": {"metodo": autenticacao, "cpf_confirmado": True, "codigo_confirmado": exige_codigo},
        "assinatura": {"tipo": tipo, "desenho": desenho, "aceite": True},
        "registro": {"assinado_em_utc": assinado_em, "assinado_em_local": fmt_datahora(assinado_em, segundos=True),
                     "fuso_horario": cfg("fuso_horario"), "ip": ip, "navegador": user_agent},
        "verificacao": {"codigo": codigo_verificacao, "url": f"{url_base()}/verificar"},
        "trilha": trilha,
    }
    evidencias_txt = json_canonico(evidencias)
    selo_txt = selo(evidencias_txt)

    from .pdf import gerar_termo
    pdf = gerar_termo(dados_c, evidencias=evidencias, selo=selo_txt)
    pdf_hash = sha256(pdf)
    relativo = Path("termos") / assinado_em[:4] / assinado_em[5:7] / (
        f"{termo['codigo']}-{codigo_verificacao.replace('-', '')}.pdf")
    destino = Path(current_app.config["PASTA_DOCUMENTOS"]) / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(pdf)
    try:
        destino.chmod(0o640)
    except OSError:
        pass

    with transacao():
        cursor = executar(
            """UPDATE termos SET status = 'assinado', assinado_em = ?, evidencias_json = ?, selo = ?,
                   codigo_verificacao = ?, pdf_caminho = ?, pdf_hash = ?, token_cifrado = NULL
                WHERE id = ? AND status = 'enviado'""",
            (assinado_em, evidencias_txt, selo_txt, codigo_verificacao, relativo.as_posix(), pdf_hash, termo_id))
        if cursor.rowcount != 1:
            destino.unlink(missing_ok=True)
            raise ErroNegocio("Este termo já foi assinado.")
        registrar_evento(termo_id, "assinado", {"metodo": metodo, "pdf_hash": pdf_hash}, usuario_id=None,
                         ip=ip, user_agent=user_agent)
        movimentar("termo_assinado", f"Termo {termo['codigo']} assinado eletronicamente por "
                                     f"{termo['colaborador_nome']}.",
                   aparelho_id=termo["aparelho_id"], colaborador_id=termo["colaborador_id"],
                   vinculo_id=termo["vinculo_id"], termo_id=termo_id, unidade_id=termo["aparelho_unidade_id"],
                   usuario_id=None)
    assinado = obter(termo_id, verificar_acesso=False)
    _notificar_assinatura(assinado, pdf)
    return assinado


def _notificar_assinatura(termo: dict, pdf: bytes):
    from . import email as svc_email
    try:
        anexo = [{"nome": f"{termo['codigo']}.pdf", "tipo": "application/pdf", "conteudo": pdf}]
        html = render_template("emails/confirmacao.html", termo=termo, empresa=nome_empresa(),
                               primeiro_nome=termo["colaborador_nome"].split()[0],
                               url_verificacao=f"{url_base()}/verificar", url_portal=f"{url_base()}/portal",
                               contato_ti=cfg("ti_email_contato"))
        svc_email.enviar(termo["colaborador_email"], f"Termo assinado: {termo['codigo']}", html,
                         finalidade="confirmacao", termo_id=termo["id"], colaborador_id=termo["colaborador_id"],
                         anexos=anexo)
        if cfg_bool("notificar_ti_assinatura"):
            html_ti = render_template("emails/ti_assinatura.html", termo=termo, empresa=nome_empresa(),
                                      url=f"{url_base()}/termos/{termo['id']}")
            for destinatario in emails_notificacao():
                svc_email.enviar(destinatario, f"Termo {termo['codigo']} assinado por {termo['colaborador_nome']}",
                                 html_ti, finalidade="notificacao", termo_id=termo["id"])
    except Exception:  # a assinatura já foi concluída; falha de aviso não pode desfazê-la
        current_app.logger.exception("Falha ao enviar as confirmações da assinatura do termo %s", termo["id"])


# ------------------------------------------------------------------ PDF e verificação

def caminho_pdf(termo: dict) -> Path | None:
    if not termo.get("pdf_caminho"):
        return None
    caminho = (Path(current_app.config["PASTA_DOCUMENTOS"]) / termo["pdf_caminho"]).resolve()
    raiz = Path(current_app.config["PASTA_DOCUMENTOS"]).resolve()
    if raiz not in caminho.parents or not caminho.exists():
        return None
    return caminho


def pdf_do_termo(termo: dict) -> bytes:
    """PDF assinado (o arquivo guardado) ou, se ainda não assinado, uma prévia marcada como tal."""
    caminho = caminho_pdf(termo)
    if termo["status"] == "assinado":
        if not caminho:
            raise ErroNegocio("O arquivo do termo assinado não foi encontrado no servidor. Restaure o backup.")
        return caminho.read_bytes()
    from .pdf import gerar_termo
    return gerar_termo(conteudo(termo), previa=True, cancelado=termo["status"] == "cancelado")


def integridade_arquivo(termo: dict) -> bool | None:
    caminho = caminho_pdf(termo)
    if not caminho:
        return None
    return sha256(caminho.read_bytes()) == termo["pdf_hash"]


def selo_valido(termo: dict) -> bool:
    return bool(termo.get("evidencias_json")) and selo(termo["evidencias_json"]) == termo.get("selo")


def _nome_reduzido(nome: str) -> str:
    partes = [p for p in nome.split() if normalizar(p) not in ("da", "de", "do", "das", "dos", "e")]
    if not partes:
        return nome
    return " ".join([partes[0]] + [f"{p[0]}." for p in partes[1:]])


def verificar_codigo(codigo: str):
    codigo = re.sub(r"[^A-Za-z0-9]", "", codigo or "").upper()
    if len(codigo) != 12:
        return None
    formatado = f"{codigo[:4]}-{codigo[4:8]}-{codigo[8:]}"
    termo = consultar(f"{SQL_TERMO} WHERE t.codigo_verificacao = ? AND t.status = 'assinado'", (formatado,), um=True)
    return _resultado_verificacao(termo) if termo else None


def verificar_arquivo(dados: bytes):
    termo = consultar(f"{SQL_TERMO} WHERE t.pdf_hash = ? AND t.status = 'assinado'", (sha256(dados),), um=True)
    return _resultado_verificacao(termo, arquivo_confere=True) if termo else None


def _resultado_verificacao(termo: dict, arquivo_confere: bool = False) -> dict:
    return {
        "codigo": termo["codigo"],
        "codigo_verificacao": termo["codigo_verificacao"],
        "assinado_em": termo["assinado_em"],
        "signatario": termo["colaborador_nome"] if arquivo_confere else _nome_reduzido(termo["colaborador_nome"]),
        "aparelho": f"{termo['marca']} {termo['modelo']}" if arquivo_confere else None,
        "pdf_hash": termo["pdf_hash"],
        "registro_integro": selo_valido(termo),
        "arquivo_confere": arquivo_confere,
        "empresa": nome_empresa(),
    }
