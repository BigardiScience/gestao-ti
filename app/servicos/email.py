"""Envio de e-mails e histórico de envios.

No modo "simulação" nada sai do servidor: a mensagem completa fica gravada no histórico
(Configurações > E-mails enviados), o que permite testar todos os fluxos antes de ligar
o Microsoft 365. No modo "graph" o envio usa o Microsoft Graph (veja graph.py).
"""
from dataclasses import dataclass

from flask import current_app, g, has_app_context

from ..db import consultar, executar
from ..util import agora_bd
from .configuracoes import cfg
from .graph import ErroGraph, enviar_email

FINALIDADES = {
    "termo": "Termo para assinatura",
    "lembrete": "Lembrete de assinatura",
    "codigo_assinatura": "Código para assinar",
    "codigo_portal": "Código de acesso ao portal",
    "confirmacao": "Confirmação de assinatura",
    "notificacao": "Aviso ao TI",
    "resumo": "Resumo diário de pendências",
    "teste": "Teste de conexão",
    "chamado": "Pedido para a TI (colaborador)",
    "chamado_ti": "Aviso de chamado (TI)",
    "chamados_resumo": "Resumo diário dos chamados",
    "chamado_dpo": "Registro de incidente para o DPO",
}
STATUS = {"enviado": "Enviado", "simulado": "Simulado", "falha": "Falha"}
MODOS = {"simulacao": "Simulação (os e-mails ficam só no histórico)", "graph": "Microsoft 365 (Outlook)"}


@dataclass
class ResultadoEnvio:
    ok: bool
    status: str
    erro: str | None = None
    email_id: int | None = None


def _ocultar(texto: str, segredos) -> str:
    for segredo in segredos or []:
        if segredo:
            texto = texto.replace(segredo, "••••••")
    return texto


def enviar(destinatario: str, assunto: str, html: str, *, finalidade: str, termo_id=None, colaborador_id=None,
           usuario_id=None, anexos=None, ocultar=None, assunto_registro=None, forcar_modo=None) -> ResultadoEnvio:
    if finalidade not in FINALIDADES:
        raise ValueError(finalidade)
    modo = forcar_modo or cfg("email_modo") or "simulacao"
    if usuario_id is None and has_app_context() and g.get("usuario"):
        usuario_id = g.usuario["id"]
    erro, tentativas = None, 1
    if modo == "graph":
        remetente = cfg("graph_remetente") or cfg("graph_conta")
        try:
            tentativas = enviar_email(destinatario, assunto, html, anexos)
            status = "enviado"
        except ErroGraph as falha:
            status, erro = "falha", falha.mensagem
            current_app.logger.warning("Falha ao enviar e-mail (%s) para %s: %s | %s", finalidade, destinatario,
                                       falha.mensagem, falha.detalhe or "")
        # segredos (links de assinatura, códigos) não ficam gravados quando o e-mail é real
        corpo_registro = _ocultar(html, ocultar)
        assunto_log = _ocultar(assunto_registro or assunto, ocultar)
    else:
        modo, status, remetente = "simulacao", "simulado", "simulação"
        corpo_registro, assunto_log = html, assunto
    cursor = executar(
        """INSERT INTO emails (criado_em, modo, remetente, destinatario, assunto, finalidade, status, erro, tentativas,
               termo_id, colaborador_id, usuario_id, corpo_html, anexos)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (agora_bd(), modo, remetente, destinatario, assunto_log, finalidade, status, erro, tentativas, termo_id,
         colaborador_id, usuario_id, corpo_registro, ", ".join(a["nome"] for a in anexos) if anexos else None),
    )
    return ResultadoEnvio(ok=status != "falha", status=status, erro=erro, email_id=cursor.lastrowid)


def listar(filtros: dict, por_pagina: int = 30):
    from ..util import limites_dia_utc, paginar
    from ..validacao import ler_data
    sql = """SELECT e.id, e.criado_em, e.modo, e.remetente, e.destinatario, e.assunto, e.finalidade, e.status,
                    e.erro, e.tentativas, e.termo_id, e.anexos, t.codigo AS termo_codigo
               FROM emails e LEFT JOIN termos t ON t.id = e.termo_id WHERE 1=1"""
    parametros = []
    if filtros.get("finalidade") in FINALIDADES:
        sql += " AND e.finalidade = ?"
        parametros.append(filtros["finalidade"])
    if filtros.get("status") in STATUS:
        sql += " AND e.status = ?"
        parametros.append(filtros["status"])
    if filtros.get("q"):
        sql += " AND (e.destinatario LIKE ? OR e.assunto LIKE ?)"
        parametros += [f"%{filtros['q'].strip()}%"] * 2
    for chave, operador, fim in (("de", ">=", False), ("ate", "<=", True)):
        try:
            dia = ler_data(filtros.get(chave))
        except ValueError:
            dia = None
        if dia:
            sql += f" AND e.criado_em {operador} ?"
            parametros.append(limites_dia_utc(dia, fim=fim))
    return paginar(sql + " ORDER BY e.criado_em DESC, e.id DESC", parametros, por_pagina=por_pagina)


def obter(email_id: int):
    return consultar("SELECT e.*, t.codigo AS termo_codigo FROM emails e LEFT JOIN termos t ON t.id = e.termo_id "
                     "WHERE e.id = ?", (email_id,), um=True)


def do_termo(termo_id: int):
    return consultar("SELECT id, criado_em, destinatario, assunto, finalidade, status, erro, modo FROM emails "
                     "WHERE termo_id = ? ORDER BY criado_em DESC, id DESC", (termo_id,))


def do_colaborador(colaborador_id: int, limite: int = 15):
    return consultar("SELECT id, criado_em, destinatario, assunto, finalidade, status, erro, modo FROM emails "
                     "WHERE colaborador_id = ? ORDER BY criado_em DESC, id DESC LIMIT ?", (colaborador_id, limite))


def falhas_recentes(horas: int = 72) -> int:
    from datetime import timedelta
    from ..util import agora, para_bd
    limite = para_bd(agora() - timedelta(hours=horas))
    return consultar("SELECT COUNT(*) AS n FROM emails WHERE status = 'falha' AND criado_em >= ?", (limite,),
                     um=True)["n"]
