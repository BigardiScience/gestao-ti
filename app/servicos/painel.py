"""Números do painel inicial (dashboard)."""
import math

from ..db import consultar, escalar
from ..seguranca import filtro_unidades
from .configuracoes import cfg
from ..util import agora_bd

ORDEM_FROTA = ["em_uso", "disponivel", "manutencao", "devolvido"]
LIMITE_ICONES = 400  # acima disso, cada ícone do painel representa mais de um aparelho


def _restricao(unidade_id, coluna="a.unidade_id"):
    sql, parametros = filtro_unidades(coluna)
    if unidade_id:
        sql += f" AND {coluna} = ?"
        parametros.append(unidade_id)
    return sql, parametros


def resumo(unidade_id: int | None = None) -> dict:
    restricao, p = _restricao(unidade_id)
    por_status = {s: 0 for s in ORDEM_FROTA + ["baixado"]}
    for linha in consultar(f"SELECT a.status, COUNT(*) AS n FROM aparelhos a WHERE 1=1 {restricao} GROUP BY a.status", p):
        por_status[linha["status"]] = linha["n"]
    total = sum(por_status.values())
    ativos = total - por_status["baixado"]

    agora_txt = agora_bd()
    termos = consultar(
        f"""SELECT SUM(t.status IN ('gerado', 'enviado')) AS pendentes,
                   SUM(t.status = 'enviado' AND t.token_expira_em < ?) AS expirados,
                   SUM(t.status = 'gerado') AS nao_enviados,
                   SUM(t.status = 'assinado') AS assinados
              FROM termos t JOIN aparelhos a ON a.id = t.aparelho_id WHERE 1=1 {restricao}""",
        [agora_txt] + p, um=True)
    termos = {k: int(v or 0) for k, v in termos.items()}

    em_uso_assinados = escalar(
        f"""SELECT COUNT(*) FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id
             WHERE v.devolvido_em IS NULL {restricao}
               AND EXISTS (SELECT 1 FROM termos t WHERE t.vinculo_id = v.id AND t.status = 'assinado')""", p)
    em_uso_total = escalar(f"SELECT COUNT(*) FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id "
                           f"WHERE v.devolvido_em IS NULL {restricao}", p)
    cobertura = round(100 * em_uso_assinados / em_uso_total) if em_uso_total else None
    sem_termo = escalar(
        f"""SELECT COUNT(*) FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id
             WHERE v.devolvido_em IS NULL {restricao}
               AND NOT EXISTS (SELECT 1 FROM termos t WHERE t.vinculo_id = v.id AND t.status <> 'cancelado')""", p)
    termos["sem_termo"] = int(sem_termo or 0)
    termos["aguardando"] = termos["pendentes"] - termos["nao_enviados"] - termos["expirados"]

    segmentos = []
    for status in ORDEM_FROTA:
        valor = por_status[status]
        segmentos.append({"status": status, "valor": valor,
                          "pct": round(100 * valor / ativos, 1) if ativos else 0,
                          "largura": max(1, round(100 * valor / ativos)) if ativos and valor else 0})
    return {
        "total": total, "ativos": ativos, "por_status": por_status, "termos": termos, "segmentos": segmentos,
        "cobertura": cobertura, "cobertura_assinados": em_uso_assinados, "cobertura_total": em_uso_total,
    }


def frota_em_icones(por_status: dict) -> dict:
    """Um ícone de celular por aparelho ativo, agrupados por status (ou 1 ícone = N aparelhos em frotas grandes)."""
    ativos = sum(por_status.get(s, 0) for s in ORDEM_FROTA)
    escala = max(1, math.ceil(ativos / LIMITE_ICONES))
    grupos = [{"status": s, "valor": por_status[s], "icones": max(1, round(por_status[s] / escala))}
              for s in ORDEM_FROTA if por_status.get(s)]
    tamanho = "g" if ativos <= 48 else ("m" if ativos <= 150 else "p")
    return {"grupos": grupos, "escala": escala, "tamanho": tamanho, "ativos": ativos}


def primeiros_passos() -> dict:
    """Situação da implantação, para guiar quem está começando a usar o sistema."""
    contagens = {chave: escalar(sql) or 0 for chave, sql in (
        ("unidades", "SELECT COUNT(*) FROM unidades WHERE ativo = 1"),
        ("aparelhos", "SELECT COUNT(*) FROM aparelhos"),
        ("colaboradores", "SELECT COUNT(*) FROM colaboradores WHERE situacao = 'ativo'"),
        ("entregas", "SELECT COUNT(*) FROM vinculos"),
    )}
    contagens["outlook"] = cfg("email_modo") == "graph" and cfg("graph_status") == "ok"
    feitos = sum(bool(contagens[c]) for c in ("unidades", "aparelhos", "colaboradores", "entregas", "outlook"))
    return {**contagens, "feitos": feitos, "total": 5, "concluido": bool(contagens["entregas"])}


def por_unidade() -> list[dict]:
    restricao, p = filtro_unidades("u.id")
    linhas = consultar(
        f"""SELECT u.id, u.nome, u.sigla,
                   SUM(a.status = 'em_uso') AS em_uso, SUM(a.status = 'disponivel') AS disponivel,
                   SUM(a.status = 'manutencao') AS manutencao, SUM(a.status = 'devolvido') AS devolvido,
                   SUM(a.status = 'baixado') AS baixado, COUNT(a.id) AS total
              FROM unidades u LEFT JOIN aparelhos a ON a.unidade_id = u.id
             WHERE u.ativo = 1 {restricao}
             GROUP BY u.id ORDER BY u.nome COLLATE NOCASE""", p)
    resultado = []
    for linha in linhas:
        for chave in ("em_uso", "disponivel", "manutencao", "devolvido", "baixado", "total"):
            linha[chave] = int(linha.get(chave) or 0)
        ativos = linha["total"] - linha["baixado"]
        linha["ativos"] = ativos
        linha["segmentos"] = [{"status": s, "valor": linha[s],
                               "largura": max(1, round(100 * linha[s] / ativos)) if ativos and linha[s] else 0}
                              for s in ORDEM_FROTA]
        resultado.append(linha)
    return resultado


def ultimas_movimentacoes(unidade_id: int | None = None, limite: int = 8) -> list[dict]:
    restricao, p = _restricao(unidade_id, "m.unidade_id")
    return consultar(
        f"""SELECT m.*, a.marca, a.modelo, c.nome AS colaborador_nome, cd.nome AS destino_nome, u.nome AS usuario_nome
              FROM movimentacoes m
              LEFT JOIN aparelhos a ON a.id = m.aparelho_id
              LEFT JOIN colaboradores c ON c.id = m.colaborador_id
              LEFT JOIN colaboradores cd ON cd.id = m.colaborador_destino_id
              LEFT JOIN usuarios u ON u.id = m.usuario_id
             WHERE m.tipo IN ('cadastro', 'entrega', 'devolucao', 'transferencia', 'substituicao', 'baixa',
                              'termo_assinado', 'status') {restricao}
             ORDER BY m.ocorrido_em DESC, m.id DESC LIMIT ?""", p + [limite])
