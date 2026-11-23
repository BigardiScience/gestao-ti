"""Painel inicial, busca geral e alertas."""
import re

from flask import Blueprint, redirect, render_template, request, url_for

from ..db import consultar
from ..seguranca import filtro_unidades, login_obrigatorio
from ..servicos import aparelhos as svc_aparelhos
from ..servicos import colaboradores as svc_colaboradores
from ..servicos import computadores as svc_computadores
from ..servicos import painel as svc_painel
from ..servicos import unidades as svc_unidades
from ..servicos.notificacoes import alertas
from . import id_inteiro

bp = Blueprint("painel", __name__)


@bp.get("/")
@login_obrigatorio
def inicio():
    unidade_id = id_inteiro(request.args.get("unidade"))
    unidades = svc_unidades.opcoes()
    if unidade_id and unidade_id not in {u["id"] for u in unidades}:
        unidade_id = None
    resumo = svc_painel.resumo(unidade_id)
    por_unidade = svc_painel.por_unidade() if not unidade_id else []
    return render_template(
        "painel/inicio.html",
        resumo=resumo, frota=svc_painel.frota_em_icones(resumo["por_status"]), passos=svc_painel.primeiros_passos(),
        por_unidade=por_unidade, maior_unidade=max([u["ativos"] for u in por_unidade] + [1]),
        ultimas=svc_painel.ultimas_movimentacoes(unidade_id, limite=7), alertas=alertas()[:5], unidades=unidades,
        unidade_id=unidade_id, unidade_atual=next((u for u in unidades if u["id"] == unidade_id), None),
    )


@bp.get("/busca")
@login_obrigatorio
def busca():
    termo = (request.args.get("q") or "").strip()
    if not termo:
        return redirect(url_for("painel.inicio"))
    digitos = re.sub(r"\D", "", termo)
    if len(digitos) == 15 and re.fullmatch(r"[\d\s./-]+", termo):  # leitor de código de barras / IMEI
        achado = svc_aparelhos.buscar_por_imei(digitos)
        if achado:
            return redirect(url_for("aparelhos.detalhe", aparelho_id=achado["id"]))
    codigo = re.fullmatch(r"(?i)tr-?\d{4}-?\d{1,6}", termo)
    termos = []
    if codigo:
        restricao, p = filtro_unidades("a.unidade_id")
        partes = re.findall(r"\d+", termo)
        alvo = f"TR-{partes[0]}-{int(partes[1]):06d}" if len(partes) == 2 else termo.upper()
        termos = consultar(f"SELECT t.id, t.codigo, c.nome FROM termos t JOIN colaboradores c ON c.id = t.colaborador_id "
                           f"JOIN aparelhos a ON a.id = t.aparelho_id WHERE t.codigo = ? {restricao}", [alvo] + p)
        if len(termos) == 1:
            return redirect(url_for("termos.detalhe", termo_id=termos[0]["id"]))
    computadores = svc_computadores.buscar(termo)
    aparelhos = svc_aparelhos.listar({"q": termo}, ordem="aparelho", por_pagina=12)
    colaboradores = svc_colaboradores.listar({"q": termo}, por_pagina=12)
    totais = (computadores.total, aparelhos.total, colaboradores.total, len(termos))
    if totais == (1, 0, 0, 0):
        return redirect(url_for("computadores.detalhe", computador_id=computadores.itens[0]["id"]))
    if totais == (0, 1, 0, 0):
        return redirect(url_for("aparelhos.detalhe", aparelho_id=aparelhos.itens[0]["id"]))
    if totais == (0, 0, 1, 0):
        return redirect(url_for("colaboradores.detalhe", colaborador_id=colaboradores.itens[0]["id"]))
    return render_template("painel/busca.html", termo=termo, aparelhos=aparelhos, colaboradores=colaboradores,
                           termos=termos, computadores=computadores)


@bp.get("/alertas")
@login_obrigatorio
def lista_alertas():
    return render_template("painel/alertas.html", alertas=alertas())
