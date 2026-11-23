"""Funções auxiliares: datas/fuso horário, formatação e paginação."""
import hashlib
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from flask import request, url_for

FORMATO_BD = "%Y-%m-%d %H:%M:%S"
MESES = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
ALFABETO_CODIGO = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O e 1/I para evitar confusão


# ------------------------------------------------------------------ fuso horário

def fuso():
    from .servicos.configuracoes import cfg
    nome = cfg("fuso_horario") or "America/Sao_Paulo"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(nome)
    except Exception:  # Windows sem o pacote tzdata
        return timezone(timedelta(hours=-3), "BRT")


def agora() -> datetime:
    return datetime.now(timezone.utc)


def agora_bd() -> str:
    return agora().strftime(FORMATO_BD)


def para_bd(momento: datetime) -> str:
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=fuso())
    return momento.astimezone(timezone.utc).strftime(FORMATO_BD)


def de_bd(texto) -> datetime | None:
    if not texto:
        return None
    if isinstance(texto, datetime):
        return texto if texto.tzinfo else texto.replace(tzinfo=timezone.utc)
    try:  # fromisoformat é bem mais rápido que strptime (faz diferença em relatórios com milhares de linhas)
        momento = datetime.fromisoformat(str(texto)[:19])
    except ValueError:
        momento = datetime.strptime(str(texto)[:19], FORMATO_BD)
    return momento.replace(tzinfo=timezone.utc)


def local(texto) -> datetime | None:
    momento = de_bd(texto)
    return momento.astimezone(fuso()) if momento else None


def agora_local() -> datetime:
    return agora().astimezone(fuso())


def ler_datahora_local(valor) -> str | None:
    """Converte o valor de um <input type="datetime-local"> (horário local) para o padrão do banco (UTC)."""
    if not valor:
        return None
    texto = str(valor).strip().replace("T", " ")
    for formato in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            momento = datetime.strptime(texto, formato).replace(tzinfo=fuso())
            return para_bd(momento)
        except ValueError:
            continue
    raise ValueError("Data e hora inválidas.")


def valor_datetime_local(texto_bd=None) -> str:
    """Valor para preencher um <input type="datetime-local">."""
    momento = local(texto_bd) if texto_bd else agora_local()
    return momento.strftime("%Y-%m-%dT%H:%M")


def limites_dia_utc(dia: date, fim: bool = False) -> str:
    """Início (ou fim) de um dia no horário local, convertido para UTC."""
    hora = datetime(dia.year, dia.month, dia.day, 23, 59, 59) if fim else datetime(dia.year, dia.month, dia.day)
    return para_bd(hora.replace(tzinfo=fuso()))


# ------------------------------------------------------------------ formatação

def fmt_data(valor) -> str:
    if not valor:
        return ""
    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y")
    texto = str(valor)
    if len(texto) == 10:  # campo só de data (AAAA-MM-DD), sem fuso
        return datetime.strptime(texto, "%Y-%m-%d").strftime("%d/%m/%Y")
    return local(texto).strftime("%d/%m/%Y")


def fmt_datahora(valor, segundos: bool = False) -> str:
    if not valor:
        return ""
    momento = local(valor)
    return momento.strftime("%d/%m/%Y %H:%M:%S" if segundos else "%d/%m/%Y %H:%M")


def fmt_quando(valor) -> str:
    """Data e hora curtas para listas: "Hoje 14:05", "Ontem 09:30", "16/11 22:27" ou "16/11/2025"."""
    if not valor:
        return ""
    momento, hoje = local(valor), agora_local().date()
    if momento.date() == hoje:
        return momento.strftime("Hoje %H:%M")
    if momento.date() == hoje - timedelta(days=1):
        return momento.strftime("Ontem %H:%M")
    if momento.year == hoje.year:
        return momento.strftime("%d/%m %H:%M")
    return momento.strftime("%d/%m/%Y")


def fmt_quando_frase(valor) -> str:
    """Para o colaborador: "hoje, 9:12", "ontem, 14:40" ou "em 12/11"."""
    if not valor:
        return ""
    momento, hoje = local(valor), agora_local().date()
    hora = f"{momento.hour}:{momento.minute:02d}"
    if momento.date() == hoje:
        return f"hoje, {hora}"
    if momento.date() == hoje - timedelta(days=1):
        return f"ontem, {hora}"
    return "em " + (momento.strftime("%d/%m") if momento.year == hoje.year else momento.strftime("%d/%m/%Y"))


DIAS_CURTOS = ("seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom.")


def fmt_dia_hora(valor) -> str:
    """Prazos e mensagens da TI: "hoje, 17:12", "amanhã, 10:00", "ontem, 9:40", "sex., 18:00" ou "12/11, 10:00"."""
    if not valor:
        return ""
    momento, hoje = local(valor), agora_local().date()
    dias = (momento.date() - hoje).days
    hora = f"{momento.hour}:{momento.minute:02d}"
    if dias == 0:
        return f"hoje, {hora}"
    if dias == 1:
        return f"amanhã, {hora}"
    if dias == -1:
        return f"ontem, {hora}"
    if 1 < dias < 7:
        return f"{DIAS_CURTOS[momento.weekday()]}, {hora}"
    return momento.strftime("%d/%m" if momento.year == hoje.year else "%d/%m/%Y") + f", {hora}"


def iniciais(nome) -> str:
    """"Maria Silva" -> "MS"; "Gustavo Henrique Martins" -> "GM"."""
    partes = [p for p in str(nome or "").split() if p[:1].isalpha() and p.lower() not in ("da", "de", "do", "das", "dos", "e")]
    if not partes:
        return "?"
    return (partes[0][0] + (partes[-1][0] if len(partes) > 1 else "")).upper()


def data_extenso(momento: datetime | date | None = None) -> str:
    momento = momento or agora_local()
    return f"{momento.day} de {MESES[momento.month - 1]} de {momento.year}"


def tempo_relativo(valor) -> str:
    momento = de_bd(valor)
    if not momento:
        return ""
    segundos = int((agora() - momento).total_seconds())
    if segundos < 60:
        return "agora"
    if segundos < 3600:
        m = segundos // 60
        return f"há {m} min"
    if segundos < 86400:
        h = segundos // 3600
        return f"há {h} h"
    dias = segundos // 86400
    if dias == 1:
        return "ontem"
    if dias < 30:
        return f"há {dias} dias"
    return fmt_data(valor)


def dias_desde(valor) -> int | None:
    momento = de_bd(valor)
    if not momento:
        return None
    return max(0, (agora() - momento).days)


def plural(n, singular: str, plural_: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_ or singular + 's')}"


# ------------------------------------------------------------------ identificadores e hashes

def sha256(dados: bytes | str) -> str:
    if isinstance(dados, str):
        dados = dados.encode("utf-8")
    return hashlib.sha256(dados).hexdigest()


def json_canonico(dados) -> str:
    return json.dumps(dados, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def codigo_legivel(blocos: int = 3, tamanho: int = 4) -> str:
    return "-".join("".join(secrets.choice(ALFABETO_CODIGO) for _ in range(tamanho)) for _ in range(blocos))


def ip_cliente() -> str:
    try:
        return request.remote_addr or ""
    except RuntimeError:
        return ""


def navegador_cliente() -> str:
    try:
        return (request.headers.get("User-Agent") or "")[:400]
    except RuntimeError:
        return ""


# ------------------------------------------------------------------ paginação e URLs

class Pagina:
    def __init__(self, itens, total, pagina, por_pagina):
        self.itens = itens
        self.total = total
        self.pagina = pagina
        self.por_pagina = por_pagina

    @property
    def paginas(self) -> int:
        return max(1, -(-self.total // self.por_pagina))

    @property
    def inicio(self) -> int:
        return 0 if self.total == 0 else (self.pagina - 1) * self.por_pagina + 1

    @property
    def fim(self) -> int:
        return min(self.total, self.pagina * self.por_pagina)

    def __iter__(self):
        return iter(self.itens)

    def __len__(self):
        return len(self.itens)


def numero_pagina() -> int:
    try:
        return max(1, int(request.args.get("pagina", 1)))
    except ValueError:
        return 1


def _sem_ordenacao(sql: str) -> str:
    """Remove o ORDER BY final (fora de parênteses e de textos): contar não precisa ordenar."""
    profundidade, aspas, corte = 0, None, None
    maiusculo = sql.upper()
    for i, caractere in enumerate(sql):
        if aspas:
            if caractere == aspas:
                aspas = None
        elif caractere in ("'", '"'):
            aspas = caractere
        elif caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        elif profundidade == 0 and maiusculo.startswith("ORDER BY", i) and (i == 0 or not sql[i - 1].isalnum()):
            corte = i
    return sql[:corte] if corte is not None else sql


def _posicao_topo(sql: str, palavra: str) -> int:
    """Posição da primeira `palavra` fora de parênteses e de textos (ou -1)."""
    profundidade, aspas = 0, None
    maiusculo = sql.upper()
    for i, caractere in enumerate(sql):
        if aspas:
            if caractere == aspas:
                aspas = None
        elif caractere in ("'", '"'):
            aspas = caractere
        elif caractere == "(":
            profundidade += 1
        elif caractere == ")":
            profundidade -= 1
        elif profundidade == 0 and maiusculo.startswith(palavra, i) and not sql[i - 1:i].isalnum() \
                and not sql[i + len(palavra):i + len(palavra) + 1].isalnum():
            return i
    return -1


_JUNCAO_POR_CHAVE = re.compile(r"\s+LEFT\s+JOIN\s+\w+\s+(\w+)\s+ON\s+(\w+)\.id\s*=\s*[\w.]+(?=\s)", re.I)


def sql_contagem(sql: str) -> str:
    """Consulta que conta os registros de `sql` do jeito mais barato possível.

    Um LEFT JOIN pela chave primária da outra tabela nunca muda a quantidade de linhas; se nenhum
    filtro usa essa tabela, ele é retirado da contagem (o SQLite não faz isso sozinho dentro de subconsultas).
    """
    sql = _sem_ordenacao(sql)
    inicio = _posicao_topo(sql, "FROM")
    if inicio < 0 or "?" in sql[:inicio] or any(_posicao_topo(sql, p) >= 0
                                                 for p in ("GROUP BY", "DISTINCT", "UNION", "HAVING", "LIMIT")):
        return f"SELECT COUNT(*) FROM ({sql})"
    corpo = sql[inicio:]
    mudou = True
    while mudou:
        mudou = False
        for juncao in reversed(list(_JUNCAO_POR_CHAVE.finditer(corpo))):
            apelido = juncao.group(1)
            if juncao.group(2) != apelido:
                continue
            resto = corpo[:juncao.start()] + corpo[juncao.end():]
            if not re.search(rf"\b{re.escape(apelido)}\.", resto):
                corpo, mudou = resto, True
                break
    return f"SELECT COUNT(*) {corpo}"


def paginar(sql: str, parametros: list, pagina: int | None = None, por_pagina: int = 25) -> Pagina:
    from .db import consultar, escalar
    pagina = pagina or numero_pagina()
    inicio = _posicao_topo(sql, "FROM")
    if "normalizar(" in sql and inicio > 0:
        # busca por texto: cada linha passa pela normalização (cara). Uma consulta só devolve a página e o total.
        itens = consultar(f"{sql[:inicio]}, COUNT(*) OVER () AS _total {sql[inicio:]} LIMIT ? OFFSET ?",
                          list(parametros) + [por_pagina, (pagina - 1) * por_pagina])
        if itens or pagina == 1:
            total = itens[0]["_total"] if itens else 0
            for item in itens:
                del item["_total"]
            return Pagina(itens, total, pagina, por_pagina)
    total = escalar(sql_contagem(sql), parametros) or 0
    paginas = max(1, -(-total // por_pagina))
    pagina = min(pagina, paginas)
    itens = consultar(f"{sql} LIMIT ? OFFSET ?", list(parametros) + [por_pagina, (pagina - 1) * por_pagina])
    return Pagina(itens, total, pagina, por_pagina)


def url_com(**alteracoes) -> str:
    """URL da página atual, mantendo os filtros e trocando só o que foi informado."""
    argumentos = request.args.to_dict(flat=False)
    for chave, valor in alteracoes.items():
        if valor is None or valor == "":
            argumentos.pop(chave, None)
        else:
            argumentos[chave] = [valor]
    if alteracoes and "pagina" not in alteracoes:
        argumentos.pop("pagina", None)  # mudou filtro/ordem: volta para a 1ª página
    return url_for(request.endpoint, **(request.view_args or {}), **argumentos)
