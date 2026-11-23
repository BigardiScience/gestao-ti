"""Histórico geral de movimentações."""
from flask import Blueprint, render_template

from ..seguranca import filtro_unidades, login_obrigatorio
from ..servicos import movimentacoes as svc
from ..servicos import unidades as svc_unidades
from ..servicos.registro import ROTULOS_GRUPOS
from ..db import consultar
from . import filtros_da_url, id_inteiro

bp = Blueprint("movimentacoes", __name__, url_prefix="/movimentacoes")


@bp.get("")
@login_obrigatorio
def lista():
    filtros = filtros_da_url("q", "unidade", "tipo", "de", "ate", "colaborador", "aparelho_id", "colaborador_id")
    # histórico de um aparelho ou de um colaborador específico (links "ver todo o histórico")
    escopo = None
    for chave, sql in (("aparelho_id", "SELECT marca || ' ' || modelo AS nome FROM aparelhos WHERE id = ?"),
                       ("colaborador_id", "SELECT nome FROM colaboradores WHERE id = ?")):
        if chave in filtros:
            numero = id_inteiro(filtros[chave])
            restricao, parametros = filtro_unidades("unidade_id")
            achado = consultar(sql + restricao, [numero] + parametros, um=True) if numero else None
            if achado:
                filtros[chave], escopo = numero, achado["nome"]
            else:
                filtros.pop(chave)
    return render_template("movimentacoes/lista.html", pagina=svc.listar(filtros), filtros=filtros, escopo=escopo,
                           unidades=svc_unidades.listar(), grupos=ROTULOS_GRUPOS)
