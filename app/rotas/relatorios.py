"""Relatórios e exportação para Excel."""
from flask import Blueprint, Response, g, render_template, request

from ..seguranca import login_obrigatorio
from ..servicos import aparelhos as svc_aparelhos
from ..servicos import colaboradores as svc_colaboradores
from ..servicos import relatorios as svc
from ..servicos import termos as svc_termos
from ..servicos import unidades as svc_unidades
from ..servicos.registro import ROTULOS_GRUPOS, auditar

bp = Blueprint("relatorios", __name__, url_prefix="/relatorios")


def _tipo():
    tipo = request.args.get("tipo", "inventario")
    return tipo if tipo in svc.RELATORIOS else "inventario"


@bp.get("")
@login_obrigatorio
def inicio():
    tipo = _tipo()
    filtros = svc.filtros_do_pedido(tipo, request.args)
    chaves = request.args.getlist("colunas")
    previa = None
    if request.args.get("previa"):
        colunas, linhas, total = svc.previa(tipo, filtros, chaves)
        previa = {"colunas": colunas, "linhas": linhas, "total": total}
    escolhidas = {c.chave for c in svc.colunas_escolhidas(tipo, chaves)}
    return render_template(
        "relatorios/inicio.html", tipo=tipo, relatorios=svc.RELATORIOS, relatorio=svc.RELATORIOS[tipo],
        filtros=filtros, colunas=svc.colunas_permitidas(tipo), escolhidas=escolhidas, previa=previa,
        unidades=svc_unidades.listar(), marcas=svc_aparelhos.valores_distintos("marca"),
        departamentos=svc_colaboradores.departamentos(), status_aparelho=svc_aparelhos.STATUS,
        status_termo=svc_termos.FILTROS_STATUS if tipo == "termos" else svc.TERMO_APARELHO_OPCOES,
        grupos=ROTULOS_GRUPOS, rotulos_filtros=svc.FILTROS_ROTULOS,
    )


@bp.get("/exportar")
@login_obrigatorio
def exportar():
    tipo = _tipo()
    filtros = svc.filtros_do_pedido(tipo, request.args)
    conteudo, nome, total = svc.exportar(tipo, filtros, request.args.getlist("colunas"), g.usuario["nome"])
    auditar("relatorio_exportado", "relatorio", None, {"relatorio": tipo, "filtros": filtros, "registros": total})
    return Response(conteudo, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})
