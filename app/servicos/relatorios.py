"""Relatórios com filtros e escolha de colunas, exportados para Excel (.xlsx)."""
import re
from dataclasses import dataclass
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ..db import consultar
from ..seguranca import eh_admin
from ..util import agora_bd, agora_local, dias_desde, fmt_data, fmt_datahora, limites_dia_utc, local
from ..validacao import formatar_cpf, formatar_moeda, formatar_telefone, ler_data
from . import aparelhos as svc_aparelhos
from . import colaboradores as svc_colaboradores
from . import movimentacoes as svc_movimentacoes
from . import termos as svc_termos
from .configuracoes import nome_empresa
from .registro import ROTULOS_GRUPOS, TIPOS_MOVIMENTACAO


@dataclass
class Coluna:
    chave: str
    titulo: str
    largura: int = 18
    tipo: str = "texto"  # texto | data | datahora | moeda | inteiro
    padrao: bool = True
    sensivel: bool = False  # dado pessoal: só o administrador exporta


def _c(*args, **kwargs):
    return Coluna(*args, **kwargs)


COLUNAS_APARELHO = [
    _c("marca", "Marca", 14), _c("modelo", "Modelo", 22), _c("numero_serie", "Número de série", 18),
    _c("imei", "IMEI", 18), _c("imei2", "IMEI 2", 18, padrao=False), _c("linha", "Linha", 16),
    _c("operadora", "Operadora", 12), _c("unidade", "Unidade", 20), _c("status", "Status", 16),
    _c("estado", "Estado de conservação", 14),
    _c("responsavel", "Responsável", 30), _c("cpf", "CPF do responsável", 16, padrao=False, sensivel=True),
    _c("email", "E-mail do responsável", 30, padrao=False), _c("departamento", "Departamento", 18),
    _c("cargo", "Cargo", 20, padrao=False), _c("entregue_em", "Entregue em", 17, "datahora"),
    _c("dias_em_uso", "Dias com o responsável", 12, "inteiro", padrao=False),
    _c("devolucao_prevista", "Devolução prevista", 14, "data", padrao=False),
    _c("termo_status", "Status do termo", 22), _c("termo_codigo", "Nº do termo", 16),
    _c("termo_assinado_em", "Termo assinado em", 17, "datahora"),
    _c("data_aquisicao", "Data de aquisição", 14, "data"), _c("valor", "Valor", 13, "moeda"),
    _c("acessorios", "Acessórios", 26, padrao=False), _c("observacoes", "Observações", 32, padrao=False),
    _c("status_desde", "No status atual desde", 17, "datahora", padrao=False),
    _c("cadastrado_em", "Cadastrado em", 17, "datahora", padrao=False),
]

COLUNAS_DISPONIVEIS = [
    _c("marca", "Marca", 14), _c("modelo", "Modelo", 22), _c("numero_serie", "Número de série", 18),
    _c("imei", "IMEI", 18), _c("imei2", "IMEI 2", 18, padrao=False), _c("linha", "Linha", 16),
    _c("operadora", "Operadora", 12), _c("unidade", "Unidade", 20), _c("estado", "Estado de conservação", 14),
    _c("status_desde", "Disponível desde", 17, "datahora"), _c("dias_parado", "Dias sem responsável", 12, "inteiro"),
    _c("data_aquisicao", "Data de aquisição", 14, "data"), _c("valor", "Valor", 13, "moeda"),
    _c("acessorios", "Acessórios", 26, padrao=False), _c("observacoes", "Observações", 32, padrao=False),
]

COLUNAS_COLABORADOR = [
    _c("nome", "Nome", 30), _c("cpf", "CPF", 16, sensivel=True), _c("email", "E-mail corporativo", 30),
    _c("matricula", "Matrícula", 12, padrao=False), _c("cargo", "Cargo", 20), _c("departamento", "Departamento", 18),
    _c("unidade", "Unidade", 20), _c("telefone", "Telefone corporativo", 16), _c("situacao", "Situação", 11),
    _c("qtd_aparelhos", "Qtde. de aparelhos", 10, "inteiro"), _c("aparelho", "Aparelho", 26),
    _c("imei", "IMEI", 18), _c("linha", "Linha", 16), _c("operadora", "Operadora", 12, padrao=False),
    _c("entregue_em", "Entregue em", 17, "datahora"), _c("termo_status", "Status do termo", 22),
    _c("termo_codigo", "Nº do termo", 16, padrao=False), _c("termo_assinado_em", "Termo assinado em", 17, "datahora"),
]

COLUNAS_TERMO = [
    _c("codigo", "Nº do termo", 16), _c("status", "Status", 22), _c("colaborador", "Colaborador", 30),
    _c("cpf", "CPF", 16, padrao=False, sensivel=True), _c("email", "E-mail", 30, padrao=False),
    _c("departamento", "Departamento", 18), _c("unidade", "Unidade", 20), _c("aparelho", "Aparelho", 26),
    _c("imei", "IMEI", 18), _c("linha", "Linha", 16, padrao=False), _c("emitido_em", "Emitido em", 17, "datahora"),
    _c("emitido_por", "Emitido por", 20, padrao=False), _c("enviado_em", "Enviado em", 17, "datahora"),
    _c("envios", "Envios", 8, "inteiro", padrao=False), _c("lembretes", "Lembretes", 9, "inteiro", padrao=False),
    _c("visualizado_em", "Aberto pelo colaborador em", 17, "datahora", padrao=False),
    _c("assinado_em", "Assinado em", 17, "datahora"), _c("cancelado_em", "Cancelado em", 17, "datahora", padrao=False),
    _c("motivo_cancelamento", "Motivo do cancelamento", 30, padrao=False),
    _c("codigo_verificacao", "Código de verificação", 18, padrao=False),
    _c("pdf_hash", "Hash SHA-256 do PDF", 66, padrao=False),
]

COLUNAS_MOVIMENTACAO = [
    _c("data", "Data e hora", 17, "datahora"), _c("tipo", "Tipo", 20), _c("descricao", "Descrição", 60),
    _c("aparelho", "Aparelho", 26), _c("imei", "IMEI", 18), _c("linha", "Linha", 16, padrao=False),
    _c("unidade", "Unidade", 20), _c("colaborador", "Colaborador", 28), _c("destino", "Colaborador de destino", 28),
    _c("departamento", "Departamento", 18, padrao=False), _c("termo", "Nº do termo", 16, padrao=False),
    _c("usuario", "Registrado por", 20),
]

FILTROS_ROTULOS = {
    "unidade": "Unidade", "marca": "Marca", "modelo": "Modelo", "status": "Status do aparelho",
    "colaborador": "Nome do colaborador", "departamento": "Departamento", "de": "Período: de",
    "ate": "Período: até", "termo": "Status do termo", "tipo_mov": "Tipo de movimentação",
    "situacao": "Situação do colaborador",
}

TERMO_APARELHO_OPCOES = {"assinado": "Assinado", "pendente": "Pendente de assinatura", "sem_termo": "Sem termo"}


def _status_termo(linha: dict, prefixo: str = "termo_") -> str:
    status = linha.get(f"{prefixo}status")
    if not status:
        return "Sem termo" if linha.get("vinculo_id") or linha.get("entregue_em") else ""
    if status == "enviado" and (linha.get(f"{prefixo}expira_em") or "") < agora_bd():
        return svc_termos.STATUS["expirado"]
    return svc_termos.STATUS.get(status, status)


# ------------------------------------------------------------------ fontes de dados

SQL_APARELHOS = """
    SELECT a.*, u.nome AS unidade_nome, v.id AS vinculo_id, v.entregue_em, v.devolucao_prevista,
           c.nome AS colaborador_nome, c.cpf AS colaborador_cpf, c.email AS colaborador_email,
           c.departamento AS colaborador_departamento, c.cargo AS colaborador_cargo,
           t.status AS termo_status, t.codigo AS termo_codigo, t.assinado_em AS termo_assinado_em,
           t.token_expira_em AS termo_expira_em
      FROM aparelhos a
      JOIN unidades u ON u.id = a.unidade_id
      LEFT JOIN vinculos v ON v.aparelho_id = a.id AND v.devolvido_em IS NULL
      LEFT JOIN colaboradores c ON c.id = v.colaborador_id
      LEFT JOIN termos t ON t.vinculo_id = v.id AND t.status <> 'cancelado'
"""


def _linhas_aparelhos(filtros: dict, status_fixo: str | None = None, ordem: str = "a.marca, a.modelo") -> list:
    filtros = dict(filtros)
    if status_fixo:
        filtros["status"] = status_fixo
    where, parametros = svc_aparelhos.montar_filtros(filtros)
    for chave, operador, fim in (("de", ">=", False), ("ate", "<=", True)):
        try:
            dia = ler_data(filtros.get(chave))
        except ValueError:
            dia = None
        if dia:
            where += f" AND v.entregue_em {operador} ?"
            parametros.append(limites_dia_utc(dia, fim=fim))
    linhas = []
    for a in consultar(f"{SQL_APARELHOS} {where} ORDER BY u.nome COLLATE NOCASE, {ordem}", parametros):
        linhas.append({
            "unidade_id": a["unidade_id"],
            "marca": a["marca"], "modelo": a["modelo"], "numero_serie": a["numero_serie"], "imei": a["imei"],
            "imei2": a["imei2"] or "", "linha": formatar_telefone(a["linha"]) if a["linha"] else "",
            "operadora": a["operadora"] or "", "unidade": a["unidade_nome"],
            "status": svc_aparelhos.STATUS[a["status"]], "estado": a["estado_conservacao"],
            "responsavel": a["colaborador_nome"] or "", "cpf": formatar_cpf(a["colaborador_cpf"]) if a["colaborador_cpf"] else "",
            "email": a["colaborador_email"] or "", "departamento": a["colaborador_departamento"] or "",
            "cargo": a["colaborador_cargo"] or "", "entregue_em": a["entregue_em"],
            "dias_em_uso": dias_desde(a["entregue_em"]) if a["entregue_em"] else None,
            "devolucao_prevista": a["devolucao_prevista"],
            "termo_status": _status_termo(a), "termo_codigo": a["termo_codigo"] or "",
            "termo_assinado_em": a["termo_assinado_em"], "data_aquisicao": a["data_aquisicao"],
            "valor": a["valor_centavos"], "acessorios": a["acessorios"] or "", "observacoes": a["observacoes"] or "",
            "status_desde": a["status_desde"], "dias_parado": dias_desde(a["status_desde"]),
            "cadastrado_em": a["criado_em"],
        })
    return linhas


def _linhas_colaboradores(filtros: dict) -> list:
    filtros = dict(filtros)
    termo = filtros.pop("termo", None)
    where, parametros = svc_colaboradores.montar_filtros(filtros)
    if termo == "assinado":
        where += " AND t.status = 'assinado'"
    elif termo == "pendente":
        where += " AND t.status IN ('gerado', 'enviado')"
    elif termo == "sem_termo":
        where += " AND v.id IS NOT NULL AND t.id IS NULL"
    if filtros.get("marca"):
        where += " AND a.marca = ? COLLATE NOCASE"
        parametros.append(filtros["marca"])
    sql = f"""SELECT c.*, u.nome AS unidade_nome, a.marca, a.modelo, a.imei, a.linha, a.operadora, v.id AS vinculo_id,
                     v.entregue_em, t.status AS termo_status, t.codigo AS termo_codigo, t.assinado_em AS termo_assinado_em,
                     t.token_expira_em AS termo_expira_em,
                     (SELECT COUNT(*) FROM vinculos v2 WHERE v2.colaborador_id = c.id AND v2.devolvido_em IS NULL)
                         AS qtd_aparelhos
                FROM colaboradores c
                JOIN unidades u ON u.id = c.unidade_id
                LEFT JOIN vinculos v ON v.colaborador_id = c.id AND v.devolvido_em IS NULL
                LEFT JOIN aparelhos a ON a.id = v.aparelho_id
                LEFT JOIN termos t ON t.vinculo_id = v.id AND t.status <> 'cancelado'
                {where}
               ORDER BY c.nome COLLATE NOCASE, v.entregue_em"""
    linhas = []
    for c in consultar(sql, parametros):
        linhas.append({
            "unidade_id": c["unidade_id"],
            "nome": c["nome"], "cpf": formatar_cpf(c["cpf"]), "email": c["email"], "matricula": c["matricula"] or "",
            "cargo": c["cargo"], "departamento": c["departamento"], "unidade": c["unidade_nome"],
            "telefone": formatar_telefone(c["telefone"]) if c["telefone"] else "",
            "situacao": svc_colaboradores.SITUACOES[c["situacao"]], "qtd_aparelhos": c["qtd_aparelhos"],
            "aparelho": f"{c['marca']} {c['modelo']}" if c["marca"] else "", "imei": c["imei"] or "",
            "linha": formatar_telefone(c["linha"]) if c["linha"] else "", "operadora": c["operadora"] or "",
            "entregue_em": c["entregue_em"], "termo_status": _status_termo(c) if c["vinculo_id"] else "",
            "termo_codigo": c["termo_codigo"] or "", "termo_assinado_em": c["termo_assinado_em"],
        })
    return linhas


def _linhas_termos(filtros: dict) -> list:
    filtros = dict(filtros)
    filtros["status"] = filtros.pop("termo", None)
    linhas = []
    for t in svc_termos.listar_todos(filtros):
        linhas.append({
            "codigo": t["codigo"], "status": svc_termos.STATUS[t["status_efetivo"]], "colaborador": t["colaborador_nome"],
            "cpf": formatar_cpf(t["colaborador_cpf"]), "email": t["colaborador_email"],
            "departamento": t["colaborador_departamento"], "unidade": t["unidade_nome"],
            "aparelho": f"{t['marca']} {t['modelo']}", "imei": t["imei"],
            "linha": formatar_telefone(t["linha"]) if t["linha"] else "", "emitido_em": t["emitido_em"],
            "emitido_por": t["emitido_por_nome"] or "", "enviado_em": t["enviado_em"], "envios": t["envios"],
            "lembretes": t["lembretes"], "visualizado_em": t["visualizado_em"], "assinado_em": t["assinado_em"],
            "cancelado_em": t["cancelado_em"], "motivo_cancelamento": t["motivo_cancelamento"] or "",
            "codigo_verificacao": t["codigo_verificacao"] or "", "pdf_hash": t["pdf_hash"] or "",
        })
    return linhas


def _linhas_movimentacoes(filtros: dict) -> list:
    filtros = dict(filtros)
    filtros["tipo"] = filtros.pop("tipo_mov", None)
    linhas = []
    for m in svc_movimentacoes.listar_todos(filtros):
        linhas.append({
            "data": m["ocorrido_em"], "tipo": TIPOS_MOVIMENTACAO.get(m["tipo"], m["tipo"]), "descricao": m["descricao"],
            "aparelho": f"{m['marca']} {m['modelo']}" if m["marca"] else "", "imei": m["imei"] or "",
            "linha": formatar_telefone(m["linha"]) if m["linha"] else "", "unidade": m["unidade_nome"] or "",
            "colaborador": m["colaborador_nome"] or "", "destino": m["destino_nome"] or "",
            "departamento": m["colaborador_departamento"] or "", "termo": m["termo_codigo"] or "",
            "usuario": m["usuario_nome"] or "Sistema",
        })
    return linhas


RELATORIOS = {
    "inventario": {
        "titulo": "Inventário completo", "descricao": "Todos os celulares cadastrados, com o responsável atual.",
        "colunas": COLUNAS_APARELHO, "filtros": ["unidade", "marca", "modelo", "status", "colaborador", "departamento",
                                                 "termo"],
        "dados": lambda f: _linhas_aparelhos(f),
    },
    "em_uso": {
        "titulo": "Aparelhos em utilização", "descricao": "Aparelhos vinculados a colaboradores, com o status do termo.",
        "colunas": COLUNAS_APARELHO, "filtros": ["unidade", "marca", "modelo", "colaborador", "departamento", "de",
                                                 "ate", "termo"],
        "dados": lambda f: _linhas_aparelhos(f, "em_uso"), "periodo": "data da entrega",
    },
    "disponiveis": {
        "titulo": "Aparelhos disponíveis", "descricao": "Aparelhos sem responsável, prontos para entrega.",
        "colunas": COLUNAS_DISPONIVEIS, "filtros": ["unidade", "marca", "modelo"],
        "dados": lambda f: _linhas_aparelhos(f, "disponivel"),
    },
    "por_unidade": {
        "titulo": "Relatório por unidade", "descricao": "Resumo por filial e uma aba com os aparelhos de cada unidade.",
        "colunas": COLUNAS_APARELHO, "filtros": ["unidade", "status", "marca", "modelo"],
        "dados": lambda f: _linhas_aparelhos(f), "por_aba": True,
    },
    "colaboradores": {
        "titulo": "Relatório de colaboradores", "descricao": "Colaboradores e os aparelhos que estão com cada um.",
        "colunas": COLUNAS_COLABORADOR, "filtros": ["unidade", "colaborador", "departamento", "situacao", "termo",
                                                    "marca"],
        "dados": _linhas_colaboradores,
    },
    "termos": {
        "titulo": "Relatório de termos", "descricao": "Termos emitidos, pendentes e assinados, com os responsáveis.",
        "colunas": COLUNAS_TERMO, "filtros": ["unidade", "termo", "colaborador", "departamento", "de", "ate", "marca",
                                              "modelo"],
        "dados": _linhas_termos, "periodo": "data de emissão",
    },
    "movimentacoes": {
        "titulo": "Histórico de movimentações", "descricao": "Entregas, devoluções, transferências e demais registros.",
        "colunas": COLUNAS_MOVIMENTACAO, "filtros": ["unidade", "tipo_mov", "de", "ate", "colaborador", "departamento",
                                                     "marca", "modelo"],
        "dados": _linhas_movimentacoes, "periodo": "data da movimentação",
    },
}


def colunas_permitidas(tipo: str) -> list[Coluna]:
    return [c for c in RELATORIOS[tipo]["colunas"] if eh_admin() or not c.sensivel]


def colunas_escolhidas(tipo: str, chaves: list[str] | None) -> list[Coluna]:
    permitidas = colunas_permitidas(tipo)
    if not chaves:
        return [c for c in permitidas if c.padrao]
    escolhidas = [c for c in permitidas if c.chave in chaves]
    return escolhidas or [c for c in permitidas if c.padrao]


def filtros_do_pedido(tipo: str, argumentos) -> dict:
    return {chave: (argumentos.get(chave) or "").strip() for chave in RELATORIOS[tipo]["filtros"]
            if (argumentos.get(chave) or "").strip()}


def descrever_filtros(tipo: str, filtros: dict) -> list[tuple[str, str]]:
    from ..servicos import unidades as svc_unidades
    descricao = []
    for chave, valor in filtros.items():
        texto = valor
        if chave == "unidade":
            unidade = svc_unidades.obter(int(valor))
            texto = unidade["nome"] if unidade else valor
        elif chave == "status":
            texto = svc_aparelhos.STATUS.get(valor, "Ativos (exceto baixados)" if valor == "ativos" else valor)
        elif chave == "termo":
            texto = (svc_termos.FILTROS_STATUS.get(valor) if tipo == "termos" else TERMO_APARELHO_OPCOES.get(valor)) or valor
        elif chave == "tipo_mov":
            texto = ROTULOS_GRUPOS.get(valor, valor)
        elif chave == "situacao":
            texto = svc_colaboradores.SITUACOES.get(valor, valor)
        elif chave in ("de", "ate"):
            texto = fmt_data(valor)
        descricao.append((FILTROS_ROTULOS.get(chave, chave), texto))
    return descricao


def gerar_dados(tipo: str, filtros: dict) -> list[dict]:
    return RELATORIOS[tipo]["dados"](filtros)


def valor_exibicao(coluna: Coluna, valor) -> str:
    if valor in (None, ""):
        return ""
    if coluna.tipo == "datahora":
        return fmt_datahora(valor)
    if coluna.tipo == "data":
        return fmt_data(valor)
    if coluna.tipo == "moeda":
        return formatar_moeda(valor)
    return str(valor)


# ------------------------------------------------------------------ Excel

CABECALHO_FUNDO = PatternFill("solid", fgColor="182230")
CABECALHO_FONTE = Font(bold=True, color="FFFFFF", name="Calibri", size=11)


def _valor_excel(coluna: Coluna, valor):
    if valor in (None, ""):
        return None
    if coluna.tipo == "datahora":
        return local(valor).replace(tzinfo=None)
    if coluna.tipo == "data":
        texto = str(valor)
        if len(texto) == 10:
            from datetime import date
            return date.fromisoformat(texto)
        return local(valor).date()
    if coluna.tipo == "moeda":
        return round(int(valor) / 100, 2)
    if coluna.tipo == "inteiro":
        return int(valor)
    return str(valor)


FORMATOS = {"datahora": "DD/MM/YYYY HH:MM", "data": "DD/MM/YYYY", "moeda": '"R$" #,##0.00', "inteiro": "0"}


def _preencher_aba(aba, colunas: list[Coluna], linhas: list[dict]):
    """Escreve a aba linha a linha (modo de escrita contínua do openpyxl: rápido e com pouca memória).

    Larguras, congelamento do cabeçalho e filtro precisam ser definidos antes da primeira linha.
    """
    formatos = []
    for indice, coluna in enumerate(colunas, start=1):
        maior = max([len(str(valor_exibicao(coluna, l.get(coluna.chave)))) for l in linhas[:500]] + [0])
        aba.column_dimensions[get_column_letter(indice)].width = max(10, min(60, max(coluna.largura, maior + 2)))
        # IMEI, CPF e linha vão como texto (str): o Excel não os converte em número nem perde zeros à esquerda
        formatos.append(FORMATOS.get(coluna.tipo))
    aba.freeze_panes = "A2"
    aba.auto_filter.ref = f"A1:{get_column_letter(len(colunas))}{max(1, len(linhas) + 1)}"
    cabecalho = []
    for coluna in colunas:
        celula = WriteOnlyCell(aba, coluna.titulo)
        celula.fill, celula.font = CABECALHO_FUNDO, CABECALHO_FONTE
        celula.alignment = Alignment(vertical="center", wrap_text=True)
        cabecalho.append(celula)
    aba.append(cabecalho)
    for linha in linhas:
        valores = []
        for coluna, formato in zip(colunas, formatos):
            valor = _valor_excel(coluna, linha.get(coluna.chave))
            if formato and valor is not None:
                valor = WriteOnlyCell(aba, valor)
                valor.number_format = formato
            valores.append(valor)
        aba.append(valores)


def _nome_aba(nome: str, usados: set) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "-", nome).strip()[:28] or "Unidade"
    candidato, n = base, 2
    while candidato.lower() in usados:
        candidato = f"{base[:25]} {n}"
        n += 1
    usados.add(candidato.lower())
    return candidato


def exportar(tipo: str, filtros: dict, chaves_colunas: list[str] | None, usuario_nome: str) -> tuple[bytes, str, int]:
    relatorio = RELATORIOS[tipo]
    colunas = colunas_escolhidas(tipo, chaves_colunas)
    linhas = gerar_dados(tipo, filtros)
    livro = Workbook(write_only=True)
    livro.properties.creator = "Plataforma de Gestão de Celulares Corporativos"
    livro.properties.title = relatorio["titulo"]

    if relatorio.get("por_aba"):
        principal = livro.create_sheet("Resumo por unidade")
        resumo = {}
        for linha in linhas:
            item = resumo.setdefault(linha["unidade"], {s: 0 for s in svc_aparelhos.STATUS.values()})
            item[linha["status"]] += 1
        rotulos = list(svc_aparelhos.STATUS.values())
        colunas_resumo = [_c("unidade", "Unidade", 26)] + [_c(f"s{i}", r, 14, "inteiro") for i, r in enumerate(rotulos)] + [
            _c("total", "Total", 10, "inteiro")]
        linhas_resumo = []
        for unidade, contagem in sorted(resumo.items(), key=lambda x: x[0].lower()):
            linha = {"unidade": unidade, "total": sum(contagem.values())}
            linha.update({f"s{i}": contagem[r] for i, r in enumerate(rotulos)})
            linhas_resumo.append(linha)
        _preencher_aba(principal, colunas_resumo, linhas_resumo)
        usados = {principal.title.lower(), "sobre este relatório"}
        for unidade in sorted(resumo, key=str.lower):
            aba = livro.create_sheet(_nome_aba(unidade, usados))
            _preencher_aba(aba, colunas, [l for l in linhas if l["unidade"] == unidade])
    else:
        _preencher_aba(livro.create_sheet(relatorio["titulo"][:31]), colunas, linhas)

    sobre = livro.create_sheet("Sobre este relatório")
    sobre.column_dimensions["A"].width = 26
    sobre.column_dimensions["B"].width = 70
    info = [("Relatório", relatorio["titulo"]), ("Empresa", nome_empresa()),
            ("Gerado em", agora_local().strftime("%d/%m/%Y %H:%M")), ("Gerado por", usuario_nome),
            ("Registros", len(linhas))]
    if relatorio.get("periodo") and (filtros.get("de") or filtros.get("ate")):
        info.append(("O período considera", relatorio["periodo"]))
    for rotulo, valor in descrever_filtros(tipo, filtros):
        info.append((f"Filtro: {rotulo}", valor))
    if not filtros:
        info.append(("Filtros", "Nenhum (todos os registros)"))
    negrito = Font(bold=True)
    for rotulo, valor in info:
        celula = WriteOnlyCell(sobre, rotulo)
        celula.font = negrito
        sobre.append([celula, valor])

    buffer = BytesIO()
    livro.save(buffer)
    nome = f"{re.sub(r'[^a-z0-9]+', '-', svc_termos.normalizar(relatorio['titulo'])).strip('-')}-" \
           f"{agora_local().strftime('%Y%m%d-%H%M')}.xlsx"
    return buffer.getvalue(), nome, len(linhas)


def previa(tipo: str, filtros: dict, chaves_colunas: list[str] | None, limite: int = 15):
    colunas = colunas_escolhidas(tipo, chaves_colunas)
    linhas = gerar_dados(tipo, filtros)
    tabela = [[valor_exibicao(c, l.get(c.chave)) for c in colunas] for l in linhas[:limite]]
    return colunas, tabela, len(linhas)

