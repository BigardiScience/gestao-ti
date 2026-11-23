"""Telas de aparelhos."""
from flask import Blueprint, Response, flash, g, redirect, render_template, request, url_for

from ..seguranca import admin_obrigatorio, login_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import aparelhos as svc
from ..servicos import importacao as svc_importacao
from ..servicos import unidades as svc_unidades
from ..servicos.pdf import gerar_etiqueta
from ..validacao import formatar_moeda
from . import filtros_da_url, importacao_comum, ou_404

bp = Blueprint("aparelhos", __name__, url_prefix="/aparelhos")

FILTROS = ("q", "unidade", "status", "operadora", "marca", "termo", "responsavel", "devolucao")


def _contexto_form(**extra):
    return dict(unidades=svc_unidades.opcoes(), estados=svc.ESTADOS, status_cadastro=svc.STATUS_CADASTRO,
                marcas=sorted(set(svc.SUGESTOES_MARCAS) | set(svc.valores_distintos("marca")), key=str.lower),
                operadoras=sorted(set(svc.SUGESTOES_OPERADORAS) | set(svc.valores_distintos("operadora")),
                                  key=str.lower), **extra)


@bp.get("")
@login_obrigatorio
def lista():
    filtros = filtros_da_url(*FILTROS)
    ordem = request.args.get("ordem", "recentes")
    return render_template("aparelhos/lista.html", pagina=svc.listar(filtros, ordem), filtros=filtros, ordem=ordem,
                           unidades=svc_unidades.listar(), operadoras=svc.valores_distintos("operadora"),
                           marcas=svc.valores_distintos("marca"))


@bp.route("/novo", methods=["GET", "POST"])
@admin_obrigatorio
def novo():
    form, erros = {"estado_conservacao": "Novo", "status": "disponivel"}, {}
    if request.args.get("unidade"):
        form["unidade_id"] = request.args["unidade"]
    if request.method == "POST":
        form = request.form
        try:
            aparelho_id = svc.criar(form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        else:
            flash("Aparelho cadastrado.", "sucesso")
            if form.get("proximo") == "outro":
                return redirect(url_for("aparelhos.novo", unidade=form.get("unidade_id")))
            return redirect(url_for("aparelhos.detalhe", aparelho_id=aparelho_id))
    return render_template("aparelhos/form.html", **_contexto_form(form=form, erros=erros, aparelho=None))


@bp.get("/<int:aparelho_id>")
@login_obrigatorio
def detalhe(aparelho_id):
    aparelho = ou_404(svc.obter(aparelho_id))
    from ..servicos.chamados import do_equipamento
    return render_template("aparelhos/detalhe.html", aparelho=aparelho, historico=svc.historico(aparelho_id),
                           vinculos=svc.vinculos(aparelho_id),
                           chamados_equipamento=do_equipamento(g.usuario, aparelho_id=aparelho_id),
                           motivos_baixa=svc.MOTIVOS_BAIXA, transicoes=sorted(svc.TRANSICOES.get(aparelho["status"], [])),
                           ajuda_status=svc.STATUS_AJUDA)


@bp.route("/<int:aparelho_id>/editar", methods=["GET", "POST"])
@admin_obrigatorio
def editar(aparelho_id):
    aparelho = ou_404(svc.obter(aparelho_id))
    erros = {}
    form = dict(aparelho)
    form["valor"] = formatar_moeda(aparelho["valor_centavos"]).replace("R$ ", "") if aparelho["valor_centavos"] else ""
    if request.method == "POST":
        form = request.form
        try:
            alterou = svc.atualizar(aparelho_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Alterações salvas." if alterou else "Nenhuma alteração para salvar.", "sucesso" if alterou else "info")
            return redirect(url_for("aparelhos.detalhe", aparelho_id=aparelho_id))
    return render_template("aparelhos/form.html", **_contexto_form(form=form, erros=erros, aparelho=aparelho))


@bp.post("/<int:aparelho_id>/status")
@admin_obrigatorio
def alterar_status(aparelho_id):
    try:
        svc.alterar_status(aparelho_id, request.form.get("status"), request.form.get("motivo"),
                           request.form.get("observacao"))
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()), "erro")
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Status atualizado.", "sucesso")
    return redirect(url_for("aparelhos.detalhe", aparelho_id=aparelho_id))


@bp.post("/<int:aparelho_id>/excluir")
@admin_obrigatorio
def excluir(aparelho_id):
    try:
        svc.excluir(aparelho_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("aparelhos.detalhe", aparelho_id=aparelho_id))
    flash("Aparelho excluído.", "sucesso")
    return redirect(url_for("aparelhos.lista"))


@bp.get("/<int:aparelho_id>/etiqueta.pdf")
@login_obrigatorio
def etiqueta(aparelho_id):
    aparelho = ou_404(svc.obter(aparelho_id))
    return Response(gerar_etiqueta(aparelho), mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="etiqueta-{aparelho["imei"]}.pdf"'})


@bp.route("/importar", methods=["GET", "POST"])
@admin_obrigatorio
def importar():
    return importacao_comum.tratar("aparelhos", "aparelhos.lista")


@bp.get("/modelo-importacao.xlsx")
@admin_obrigatorio
def modelo_importacao():
    return Response(svc_importacao.gerar_modelo("aparelhos"),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="modelo-importacao-aparelhos.xlsx"'})
