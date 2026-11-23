"""Horário de atendimento da TI: dias úteis, feriados e contagem de prazos em tempo útil.

Os prazos dos chamados correm só dentro do expediente (por padrão, segunda a sexta, das 8h às 18h).
"1 dia útil" equivale a um expediente inteiro: aberto na segunda às 10h, vence na terça às 10h.
Feriados nacionais entram sozinhos (inclusive os que dependem da Páscoa); a empresa pode
acrescentar os municipais e as pontes em Configurações > Chamados.
"""
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ..util import agora, fuso
from .configuracoes import cfg, cfg_bool

FIXOS = [
    (1, 1, "Confraternização Universal"),
    (4, 21, "Tiradentes"),
    (5, 1, "Dia do Trabalho"),
    (9, 7, "Independência do Brasil"),
    (10, 12, "Nossa Senhora Aparecida"),
    (11, 2, "Finados"),
    (11, 15, "Proclamação da República"),
    (11, 20, "Dia Nacional de Zumbi e da Consciência Negra"),
    (12, 25, "Natal"),
]
DIAS_SEMANA = {1: "Segunda", 2: "Terça", 3: "Quarta", 4: "Quinta", 5: "Sexta", 6: "Sábado", 7: "Domingo"}


def pascoa(ano: int) -> date:
    """Domingo de Páscoa (algoritmo de Meeus/Jones/Butcher)."""
    a, b, c = ano % 19, ano // 100, ano % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mes = (h + l_ - 7 * m + 114) // 31
    dia = (h + l_ - 7 * m + 114) % 31 + 1
    return date(ano, mes, dia)


def feriados_do_ano(ano: int, *, carnaval: bool = True, corpus_christi: bool = True, extras=()) -> dict:
    p = pascoa(ano)
    lista = {date(ano, mes, dia): nome for mes, dia, nome in FIXOS}
    lista[p - timedelta(days=2)] = "Sexta-feira Santa"
    if carnaval:
        lista[p - timedelta(days=48)] = "Carnaval"
        lista[p - timedelta(days=47)] = "Carnaval"
    if corpus_christi:
        lista[p + timedelta(days=60)] = "Corpus Christi"
    for dia in extras:
        if dia.year == ano:
            lista.setdefault(dia, "Feriado da empresa")
    return dict(sorted(lista.items()))


def ler_datas(texto: str) -> tuple[list[date], list[str]]:
    """Uma data por linha (dd/mm/aaaa). Devolve (datas, linhas inválidas)."""
    datas, invalidas = [], []
    for linha in (texto or "").replace(",", "\n").replace(";", "\n").splitlines():
        linha = linha.strip()
        if not linha:
            continue
        achado = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})", linha) or re.match(r"^(\d{4})-(\d{2})-(\d{2})", linha)
        try:
            if achado and "/" in achado.group(0):
                datas.append(date(int(achado.group(3)), int(achado.group(2)), int(achado.group(1))))
            elif achado:
                datas.append(date(int(achado.group(1)), int(achado.group(2)), int(achado.group(3))))
            else:
                invalidas.append(linha)
        except ValueError:
            invalidas.append(linha)
    return sorted(set(datas)), invalidas


def ler_hora(texto: str, padrao: time) -> time:
    try:
        horas, minutos = (texto or "").strip().split(":")[:2]
        return time(int(horas), int(minutos))
    except (ValueError, TypeError):
        return padrao


def ler_duracao(texto: str) -> tuple[int, str]:
    """"4h" -> (4, "h"); "1d" -> (1, "d"); "30m" -> (30, "m")."""
    achado = re.fullmatch(r"\s*(\d{1,4})\s*([hdm])\s*", (texto or "").lower())
    if not achado:
        return 0, "h"
    return int(achado.group(1)), achado.group(2)


@dataclass
class Expediente:
    dias: frozenset
    inicio: time
    fim: time
    carnaval: bool = True
    corpus_christi: bool = True
    extras: tuple = ()
    _cache: dict = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------ calendário

    def feriados(self, ano: int) -> dict:
        if ano not in self._cache:
            self._cache[ano] = feriados_do_ano(ano, carnaval=self.carnaval, corpus_christi=self.corpus_christi,
                                               extras=self.extras)
        return self._cache[ano]

    def eh_dia_util(self, dia: date) -> bool:
        return dia.isoweekday() in self.dias and dia not in self.feriados(dia.year)

    @property
    def minutos_por_dia(self) -> int:
        return max(0, (self.fim.hour * 60 + self.fim.minute) - (self.inicio.hour * 60 + self.inicio.minute))

    def _abertura(self, dia: date) -> datetime:
        return datetime.combine(dia, self.inicio, tzinfo=fuso())

    def _fechamento(self, dia: date) -> datetime:
        return datetime.combine(dia, self.fim, tzinfo=fuso())

    def proximo_dia_util(self, dia: date) -> date:
        for _ in range(400):
            dia += timedelta(days=1)
            if self.eh_dia_util(dia):
                return dia
        raise ValueError("Nenhum dia útil configurado.")

    def normalizar(self, momento: datetime) -> datetime:
        """O próprio momento, se estiver no expediente; senão, a próxima abertura."""
        local = momento.astimezone(fuso())
        dia = local.date()
        if self.eh_dia_util(dia):
            if local < self._abertura(dia):
                return self._abertura(dia)
            if local < self._fechamento(dia):
                return local
        return self._abertura(self.proximo_dia_util(dia))

    def no_expediente(self, momento: datetime | None = None) -> bool:
        momento = (momento or agora()).astimezone(fuso())
        return self.eh_dia_util(momento.date()) and self._abertura(momento.date()) <= momento < \
            self._fechamento(momento.date())

    # ------------------------------------------------------------ contas

    def somar_minutos(self, inicio: datetime, minutos: int) -> datetime:
        if self.minutos_por_dia <= 0 or not self.dias:
            return inicio + timedelta(minutes=minutos)
        momento = self.normalizar(inicio)
        restante = max(0, int(minutos))
        for _ in range(5000):
            disponivel = int((self._fechamento(momento.date()) - momento).total_seconds() // 60)
            if restante <= disponivel:
                return momento + timedelta(minutes=restante)
            restante -= disponivel
            momento = self._abertura(self.proximo_dia_util(momento.date()))
        return momento

    def minutos_entre(self, inicio: datetime, fim: datetime) -> int:
        """Minutos de expediente entre dois momentos (negativo se fim vier antes do início)."""
        if fim < inicio:
            return -self.minutos_entre(fim, inicio)
        if self.minutos_por_dia <= 0 or not self.dias:
            return int((fim - inicio).total_seconds() // 60)
        a, b = inicio.astimezone(fuso()), fim.astimezone(fuso())
        total, dia = 0.0, a.date()
        if (b.date() - dia).days > 3660:
            dia = b.date() - timedelta(days=3660)
        while dia <= b.date():
            if self.eh_dia_util(dia):
                comeco = max(a, self._abertura(dia))
                termino = min(b, self._fechamento(dia))
                if termino > comeco:
                    total += (termino - comeco).total_seconds()
            dia += timedelta(days=1)
        return int(total // 60)

    def somar_dias(self, inicio: datetime, dias: int) -> datetime:
        return self.somar_minutos(inicio, dias * self.minutos_por_dia)

    def duracao_em_minutos(self, texto: str) -> int:
        quantidade, unidade = ler_duracao(texto)
        if unidade == "d":
            return quantidade * self.minutos_por_dia
        if unidade == "m":
            return quantidade
        return quantidade * 60


def atual() -> Expediente:
    dias = frozenset(int(d) for d in re.findall(r"[1-7]", cfg("chamados_dias") or "1,2,3,4,5"))
    extras, _ = ler_datas(cfg("chamados_feriados"))
    return Expediente(dias=dias or frozenset({1, 2, 3, 4, 5}),
                      inicio=ler_hora(cfg("chamados_inicio"), time(8, 0)),
                      fim=ler_hora(cfg("chamados_fim"), time(18, 0)),
                      carnaval=cfg_bool("chamados_carnaval"), corpus_christi=cfg_bool("chamados_corpus_christi"),
                      extras=tuple(extras))


def texto_duracao(minutos: int) -> str:
    """240 -> "4 h"; 75 -> "1 h 15 min"; 1500 -> "25 h" (sempre tempo útil)."""
    minutos = abs(int(minutos))
    if minutos < 60:
        return f"{minutos} min"
    horas, resto = divmod(minutos, 60)
    return f"{horas} h {resto} min" if resto else f"{horas} h"


def texto_prazo_config(texto: str) -> str:
    quantidade, unidade = ler_duracao(texto)
    if unidade == "d":
        return f"{quantidade} dia útil" if quantidade == 1 else f"{quantidade} dias úteis"
    if unidade == "m":
        return f"{quantidade} min úteis"
    return f"{quantidade} hora útil" if quantidade == 1 else f"{quantidade} horas úteis"
