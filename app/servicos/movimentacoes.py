"""Consulta do histórico de movimentações (tela e relatório)."""
from ..db import condicao_busca, consultar, normalizar
from ..seguranca import filtro_unidades
from ..util import limites_dia_utc, paginar
from ..validacao import ler_data
from .registro import GRUPOS_MOVIMENTACAO

SQL = """
    SELECT m.*, a.marca, a.modelo, a.imei, a.linha, un.nome AS unidade_nome,
           c.nome AS colaborador_nome, c.departamento AS colaborador_departamento,
           cd.nome AS destino_nome, us.nome AS usuario_nome, t.codigo AS termo_codigo
      FROM movimentacoes m
      LEFT JOIN aparelhos a ON a.id = m.aparelho_id
      LEFT JOIN unidades un ON un.id = m.unidade_id
      LEFT JOIN colaboradores c ON c.id = m.colaborador_id
      LEFT JOIN colaboradores cd ON cd.id = m.colaborador_destino_id
      LEFT JOIN usuarios us ON us.id = m.usuario_id
      LEFT JOIN termos t ON t.id = m.termo_id
"""


def montar_filtros(filtros: dict) -> tuple[str, list]:
    sql, parametros = "WHERE 1=1", []
    restricao, p = filtro_unidades("m.unidade_id")
    sql += restricao
    parametros += p
    busca, p = condicao_busca(filtros.get("q"), "coalesce(a.marca, '') || ' ' || coalesce(a.modelo, '') || ' ' || "
                                                "coalesce(a.imei, '') || ' ' || coalesce(c.nome, '') || ' ' || "
                                                "coalesce(cd.nome, '') || ' ' || m.descricao")
    sql += busca
    parametros += p
    if filtros.get("unidade"):
        sql += " AND m.unidade_id = ?"
        parametros.append(int(filtros["unidade"]))
    grupo = GRUPOS_MOVIMENTACAO.get(filtros.get("tipo") or "")
    if grupo:
        sql += f" AND m.tipo IN ({','.join('?' * len(grupo))})"
        parametros += grupo
    if filtros.get("aparelho_id"):
        sql += " AND m.aparelho_id = ?"
        parametros.append(int(filtros["aparelho_id"]))
    if filtros.get("colaborador_id"):
        sql += " AND (m.colaborador_id = ? OR m.colaborador_destino_id = ?)"
        parametros += [int(filtros["colaborador_id"])] * 2
    if filtros.get("colaborador"):
        sql += " AND (normalizar(c.nome) LIKE ? OR normalizar(cd.nome) LIKE ?)"
        parametros += [f"%{normalizar(filtros['colaborador'])}%"] * 2
    if filtros.get("departamento"):
        sql += " AND (c.departamento = ? COLLATE NOCASE OR cd.departamento = ? COLLATE NOCASE)"
        parametros += [filtros["departamento"]] * 2
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
            sql += f" AND m.ocorrido_em {operador} ?"
            parametros.append(limites_dia_utc(dia, fim=fim))
    return sql, parametros


def listar(filtros: dict, por_pagina: int = 25):
    where, parametros = montar_filtros(filtros)
    return paginar(f"{SQL} {where} ORDER BY m.ocorrido_em DESC, m.id DESC", parametros, por_pagina=por_pagina)


def listar_todos(filtros: dict):
    where, parametros = montar_filtros(filtros)
    return consultar(f"{SQL} {where} ORDER BY m.ocorrido_em DESC, m.id DESC", parametros)
