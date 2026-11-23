"""Relatório mensal de chamados: quantos foram resolvidos dentro e fora do prazo (auditoria e planilha mensal).

Regras (as mesmas da tela do chamado):
- Conta no mês o chamado **resolvido** naquele mês (data em que a TI marcou como resolvido pela última vez;
  se a pessoa reabriu e a TI resolveu de novo, vale a última vez).
- "No prazo" é decidido na hora em que a TI resolve, com o prazo já descontando o tempo em "Aguardando usuário"
  e fora do horário de atendimento. Fica gravado no chamado (não muda se as regras de prazo mudarem depois).
- Chamado encerrado porque a pessoa não respondeu não entra em "resolvidos": aparece à parte.
"""
import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..db import consultar
from ..util import agora_local, fuso, local, para_bd
from . import expediente
from .chamados import CATEGORIAS, PRIORIDADES, _visibilidade, numero

MESES = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
         "novembro", "dezembro")


def ler_mes(texto: str | None) -> tuple[int, int]:
    """"2026-11" -> (2026, 11). Vazio ou inválido: o mês atual."""
    try:
        ano, mes = (int(p) for p in (texto or "").split("-"))
        if 2000 <= ano <= 2100 and 1 <= mes <= 12:
            return ano, mes
    except ValueError:
        pass
    hoje = agora_local()
    return hoje.year, hoje.month


def nome_mes(ano: int, mes: int) -> str:
    return f"{MESES[mes - 1]} de {ano}"


def _limites(ano: int, mes: int) -> tuple[str, str]:
    inicio = datetime(ano, mes, 1, tzinfo=fuso())
    fim = datetime(ano + (mes == 12), mes % 12 + 1, 1, tzinfo=fuso())
    return para_bd(inicio), para_bd(fim)


def _pct(parte: int, total: int) -> float | None:
    return round(100 * parte / total, 1) if total else None


def gerar(usuario, ano: int, mes: int, unidade_id: int | None = None) -> dict:
    inicio, fim = _limites(ano, mes)
    visibilidade, parametros = _visibilidade(usuario)
    filtro_unidade, extra = (" AND c.unidade_id = ?", [unidade_id]) if unidade_id else ("", [])
    linhas = consultar(
        "SELECT c.id, c.assunto, c.categoria, c.prioridade, c.situacao, c.criado_em, c.prazo_em, c.resolvido_em, "
        "c.fechado_em, c.resolvido_no_prazo, c.pausa_total_min, c.aberto_por_id, c.solicitante_id, u.nome AS unidade_nome, "
        "s.nome AS solicitante_nome, s.setor AS solicitante_setor, r.nome AS responsavel_nome "
        "FROM chamados c JOIN unidades u ON u.id = c.unidade_id JOIN usuarios s ON s.id = c.solicitante_id "
        "LEFT JOIN usuarios r ON r.id = c.responsavel_id "
        "WHERE ((c.resolvido_em >= ? AND c.resolvido_em < ? AND c.situacao IN ('resolvido', 'fechado')) "
        "OR (c.situacao = 'fechado' AND c.resolvido_em IS NULL AND c.fechado_em >= ? AND c.fechado_em < ?))"
        f"{visibilidade}{filtro_unidade} ORDER BY c.resolvido_em, c.fechado_em, c.id",
        (inicio, fim, inicio, fim, *parametros, *extra))
    abertos = consultar(f"SELECT COUNT(*) AS n FROM chamados c WHERE c.criado_em >= ? AND c.criado_em < ?"
                        f"{visibilidade}{filtro_unidade}", (inicio, fim, *parametros, *extra), um=True)["n"]
    exp = expediente.atual()
    resolvidos, sem_resposta = [], []
    for l in linhas:
        if l["resolvido_em"]:
            criado, resolvido = local(l["criado_em"]), local(l["resolvido_em"])
            l["tempo_util_min"] = max(0, exp.minutos_entre(criado, resolvido) - (l["pausa_total_min"] or 0))
            l["no_prazo"] = l["resolvido_no_prazo"] == 1
            resolvidos.append(l)
        else:
            sem_resposta.append(l)

    def resumo(itens: list) -> dict:
        no_prazo = sum(1 for i in itens if i["no_prazo"])
        tempos = [i["tempo_util_min"] for i in itens]
        return {"resolvidos": len(itens), "no_prazo": no_prazo, "fora_prazo": len(itens) - no_prazo,
                "pct_no_prazo": _pct(no_prazo, len(itens)),
                "tempo_medio_min": round(sum(tempos) / len(tempos)) if tempos else None}

    def agrupar(chave, rotulo, ordem=None) -> list:
        grupos = {}
        for i in resolvidos:
            grupos.setdefault(rotulo(i), []).append(i)
        nomes = ordem if ordem else sorted(grupos, key=lambda n: (n == "Sem responsável", n.lower()))
        return [dict(resumo(grupos[n]), nome=n) for n in nomes if n in grupos]

    return {
        "ano": ano, "mes": mes, "titulo": nome_mes(ano, mes), "gerado_em": agora_local(),
        "total": dict(resumo(resolvidos), abertos=abertos, sem_resposta=len(sem_resposta)),
        "por_unidade": agrupar("unidade", lambda i: i["unidade_nome"]),
        "por_prioridade": agrupar("prioridade", lambda i: PRIORIDADES[i["prioridade"]],
                                  [PRIORIDADES[p] for p in PRIORIDADES]),
        "por_categoria": agrupar("categoria", lambda i: CATEGORIAS[i["categoria"]][0]),
        "por_responsavel": agrupar("responsavel", lambda i: i["responsavel_nome"] or "Sem responsável"),
        "resolvidos": resolvidos, "sem_resposta": sem_resposta,
    }


# ------------------------------------------------------------------ planilha

_TITULO = Font(bold=True, size=14)
_NEGRITO = Font(bold=True)
_CABECALHO = PatternFill("solid", fgColor="172033")
_CABECALHO_FONTE = Font(bold=True, color="FFFFFF")
_FORA = PatternFill("solid", fgColor="FDE4D6")


def _cabecalho(aba, linha: int, nomes: list):
    for coluna, nome in enumerate(nomes, 1):
        celula = aba.cell(row=linha, column=coluna, value=nome)
        celula.fill, celula.font = _CABECALHO, _CABECALHO_FONTE
        celula.alignment = Alignment(vertical="center", wrap_text=True)


def _larguras(aba, larguras: list):
    for coluna, largura in enumerate(larguras, 1):
        aba.column_dimensions[get_column_letter(coluna)].width = largura


def _horas(minutos):
    return round(minutos / 60, 1) if minutos is not None else None


def planilha(dados: dict, unidade_nome: str, usuario_nome: str) -> tuple[bytes, str]:
    livro = Workbook()
    aba = livro.active
    aba.title = "Resumo"
    aba["A1"] = f"Chamados de {dados['titulo']}"
    aba["A1"].font = _TITULO
    aba["A2"] = (f"Unidade: {unidade_nome} · gerado em {dados['gerado_em']:%d/%m/%Y %H:%M} por {usuario_nome}. "
                 "Prazo em horário útil, sem contar o tempo em “Aguardando usuário”.")
    t = dados["total"]
    linhas = [("Chamados abertos no mês", t["abertos"]), ("Resolvidos no mês", t["resolvidos"]),
              ("Resolvidos dentro do prazo", t["no_prazo"]), ("Resolvidos fora do prazo", t["fora_prazo"]),
              ("% dentro do prazo", t["pct_no_prazo"] / 100 if t["pct_no_prazo"] is not None else None),
              ("Tempo médio de resolução (horas úteis)", _horas(t["tempo_medio_min"])),
              ("Encerrados sem resposta do usuário", t["sem_resposta"])]
    for i, (rotulo, valor) in enumerate(linhas, 4):
        aba.cell(row=i, column=1, value=rotulo).font = _NEGRITO
        celula = aba.cell(row=i, column=2, value=valor)
        if rotulo.startswith("%"):
            celula.number_format = "0.0%"
    linha = 4 + len(linhas) + 1
    for titulo, chave in (("Por unidade", "por_unidade"), ("Por prioridade", "por_prioridade"),
                          ("Por categoria", "por_categoria"), ("Por responsável", "por_responsavel")):
        aba.cell(row=linha, column=1, value=titulo).font = _NEGRITO
        _cabecalho(aba, linha + 1, ["", "Resolvidos", "No prazo", "Fora do prazo", "% no prazo", "Tempo médio (h úteis)"])
        linha += 2
        for g in dados[chave]:
            for coluna, valor in enumerate((g["nome"], g["resolvidos"], g["no_prazo"], g["fora_prazo"],
                                            g["pct_no_prazo"] / 100 if g["pct_no_prazo"] is not None else None,
                                            _horas(g["tempo_medio_min"])), 1):
                celula = aba.cell(row=linha, column=coluna, value=valor)
                if coluna == 5:
                    celula.number_format = "0.0%"
            linha += 1
        linha += 1
    _larguras(aba, [40, 12, 12, 14, 12, 20])

    det = livro.create_sheet("Resolvidos")
    nomes = ["Nº", "Unidade", "Categoria", "Prioridade", "Assunto", "Solicitante", "Setor", "Responsável",
             "Aberto em", "Prazo", "Resolvido em", "No prazo?", "Tempo útil (h)", "Aberto por"]
    _cabecalho(det, 1, nomes)
    for n, c in enumerate(dados["resolvidos"], 2):
        valores = [int(numero(c["id"])), c["unidade_nome"], CATEGORIAS[c["categoria"]][0], PRIORIDADES[c["prioridade"]],
                   c["assunto"], c["solicitante_nome"], c["solicitante_setor"] or "", c["responsavel_nome"] or "",
                   local(c["criado_em"]).replace(tzinfo=None), local(c["prazo_em"]).replace(tzinfo=None),
                   local(c["resolvido_em"]).replace(tzinfo=None), "Sim" if c["no_prazo"] else "Não",
                   _horas(c["tempo_util_min"]),
                   "TI" if c["aberto_por_id"] and c["aberto_por_id"] != c["solicitante_id"] else "Próprio usuário"]
        for coluna, valor in enumerate(valores, 1):
            celula = det.cell(row=n, column=coluna, value=valor)
            if coluna in (9, 10, 11):
                celula.number_format = "dd/mm/yyyy hh:mm"
            if not c["no_prazo"]:
                celula.fill = _FORA
    det.freeze_panes = "A2"
    det.auto_filter.ref = f"A1:{get_column_letter(len(nomes))}{max(1, len(dados['resolvidos']) + 1)}"
    _larguras(det, [7, 16, 18, 10, 44, 22, 16, 20, 16, 16, 16, 10, 13, 12])

    if dados["sem_resposta"]:
        sem = livro.create_sheet("Encerrados sem resposta")
        _cabecalho(sem, 1, ["Nº", "Unidade", "Categoria", "Assunto", "Solicitante", "Aberto em", "Encerrado em"])
        for n, c in enumerate(dados["sem_resposta"], 2):
            for coluna, valor in enumerate([int(numero(c["id"])), c["unidade_nome"], CATEGORIAS[c["categoria"]][0],
                                            c["assunto"], c["solicitante_nome"],
                                            local(c["criado_em"]).replace(tzinfo=None),
                                            local(c["fechado_em"]).replace(tzinfo=None)], 1):
                celula = sem.cell(row=n, column=coluna, value=valor)
                if coluna in (6, 7):
                    celula.number_format = "dd/mm/yyyy hh:mm"
        _larguras(sem, [7, 16, 18, 44, 22, 16, 16])

    saida = io.BytesIO()
    livro.save(saida)
    return saida.getvalue(), f"chamados-{dados['ano']}-{dados['mes']:02d}.xlsx"
