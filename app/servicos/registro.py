"""Registro de auditoria (ações administrativas) e de movimentações dos aparelhos."""
import json

from flask import g, has_app_context

from ..db import executar
from ..util import agora_bd, ip_cliente

TIPOS_MOVIMENTACAO = {
    "cadastro": "Cadastro do aparelho",
    "alteracao": "Alteração cadastral",
    "status": "Alteração de status",
    "entrega": "Entrega",
    "devolucao": "Devolução",
    "transferencia": "Transferência",
    "substituicao": "Substituição",
    "baixa": "Baixa",
    "termo_emitido": "Termo emitido",
    "termo_enviado": "Termo enviado",
    "termo_assinado": "Termo assinado",
    "termo_cancelado": "Termo cancelado",
    "colaborador_cadastro": "Cadastro de colaborador",
    "colaborador_alteracao": "Alteração de colaborador",
    "colaborador_desligamento": "Desligamento",
    "colaborador_reativacao": "Reativação de colaborador",
}

# Agrupamentos usados nos filtros da tela de movimentações
GRUPOS_MOVIMENTACAO = {
    "entrega": ["entrega"],
    "devolucao": ["devolucao"],
    "transferencia": ["transferencia"],
    "substituicao": ["substituicao"],
    "cadastro": ["cadastro", "colaborador_cadastro"],
    "alteracao": ["alteracao", "status", "baixa", "colaborador_alteracao",
                  "colaborador_desligamento", "colaborador_reativacao"],
    "termo": ["termo_emitido", "termo_enviado", "termo_assinado", "termo_cancelado"],
}

ROTULOS_GRUPOS = {
    "entrega": "Entregas",
    "devolucao": "Devoluções",
    "transferencia": "Transferências",
    "substituicao": "Substituições",
    "cadastro": "Cadastros",
    "alteracao": "Alterações e status",
    "termo": "Termos",
}


ATUAL = object()  # marcador: "usar o usuário logado" (None significa "nenhum usuário", ex.: o colaborador)


def usuario_atual_id():
    usuario = g.get("usuario") if has_app_context() else None
    return usuario["id"] if usuario else None


def _usuario_id(usuario_id):
    return usuario_atual_id() if usuario_id is ATUAL else usuario_id


def movimentar(tipo: str, descricao: str, *, aparelho_id=None, colaborador_id=None,
               colaborador_destino_id=None, vinculo_id=None, termo_id=None, unidade_id=None,
               detalhes=None, usuario_id=ATUAL, ocorrido_em=None):
    if tipo not in TIPOS_MOVIMENTACAO:
        raise ValueError(f"Tipo de movimentação desconhecido: {tipo}")
    executar(
        """INSERT INTO movimentacoes (tipo, ocorrido_em, aparelho_id, colaborador_id, colaborador_destino_id,
               vinculo_id, termo_id, unidade_id, usuario_id, descricao, detalhes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (tipo, ocorrido_em or agora_bd(), aparelho_id, colaborador_id, colaborador_destino_id, vinculo_id,
         termo_id, unidade_id, _usuario_id(usuario_id), descricao,
         json.dumps(detalhes, ensure_ascii=False) if detalhes else None),
    )


def auditar(acao: str, entidade: str | None = None, entidade_id=None, detalhes=None, usuario=None):
    usuario = usuario or (g.get("usuario") if has_app_context() else None)
    executar(
        """INSERT INTO auditoria (usuario_id, usuario_nome, acao, entidade, entidade_id, detalhes, ip)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (usuario["id"] if usuario else None, usuario["nome"] if usuario else None, acao, entidade, entidade_id,
         json.dumps(detalhes, ensure_ascii=False, default=str) if detalhes else None, ip_cliente()),
    )


def diferencas(antes: dict, depois: dict, rotulos: dict) -> dict:
    """Campos alterados: {rótulo: [antes, depois]} — guardado no histórico."""
    mudancas = {}
    for campo, rotulo in rotulos.items():
        a, d = antes.get(campo), depois.get(campo)
        if (a or None) != (d or None):
            mudancas[rotulo] = [a, d]
    return mudancas
