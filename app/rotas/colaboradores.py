"""Telas de colaboradores."""
from flask import Blueprint, Response, flash, redirect, render_template, request, url_for

from ..db import transacao
from ..seguranca import admin_obrigatorio, eh_admin, login_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import aparelhos as svc_aparelhos
from ..servicos import colaboradores as svc
from ..servicos import email as svc_email
from ..servicos import importacao as svc_importacao
from ..servicos import unidades as svc_unidades
from ..servicos import vinculos as svc_vinculos
from . import filtros_da_url, importacao_comum, ou_404

bp = Blueprint("colaboradores", __name__, url_prefix="/colaboradores")

FILTROS = ("q", "unidade", "departamento", "situacao", "aparelho", "termo")


def _contexto_form(**extra):
    return dict(unidades=svc_unidades.opcoes(), departamentos=svc.departamentos(), cargos=svc.cargos(), **extra)


@bp.get("")
@login_obrigatorio
def lista():
    filtros = filtros_da_url(*FILTROS)
    if "situacao" not in request.args:
        filtros["situacao"] = "ativo"
    elif filtros.get("situacao") == "todos":
        filtros.pop("situacao")
    ordem = request.args.get("ordem", "nome")
    return render_template("colaboradores/lista.html", pagina=svc.listar(filtros, ordem), filtros=filtros, ordem=ordem,
                           unidades=svc_unidades.listar(), departamentos=svc.departamentos())


@bp.route("/novo", methods=["GET", "POST"])
@admin_obrigatorio
def novo():
    form, erros = {}, {}
    if request.method == "POST":
        form = request.form
        aparelho_id = form.get("aparelho_id")
        try:
            with transacao():
                colaborador_id = svc.criar(form)
                termo_id = None
                if aparelho_id:
                    _, termo_id = svc_vinculos.entregar({
                        "aparelho_id": aparelho_id, "colaborador_id": colaborador_id, "gerar_termo": "1",
                        "acessorios": form.get("acessorios", ""), "entregue_em": "",
                    })
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro) if "aparelho_id" not in erros else erros["aparelho_id"], "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            if termo_id:
                flash("Colaborador cadastrado e aparelho entregue. Revise o termo de responsabilidade e envie para "
                      "assinatura.", "sucesso")
                return redirect(url_for("termos.detalhe", termo_id=termo_id))
            flash("Colaborador cadastrado.", "sucesso")
            return redirect(url_for("colaboradores.detalhe", colaborador_id=colaborador_id))
    return render_template("colaboradores/form.html", **_contexto_form(
        form=form, erros=erros, colaborador=None, tem_disponiveis=svc_aparelhos.existe_disponivel(),
        aparelho_escolhido=svc_aparelhos.opcao_disponivel(form.get("aparelho_id"))))


@bp.get("/<int:colaborador_id>")
@login_obrigatorio
def detalhe(colaborador_id):
    colaborador = ou_404(svc.obter(colaborador_id))
    return render_template(
        "colaboradores/detalhe.html", colaborador=colaborador, aparelhos=svc.aparelhos_ativos(colaborador_id),
        anteriores=svc.historico_vinculos(colaborador_id), termos=svc.termos(colaborador_id),
        historico=svc.historico(colaborador_id),
        emails=svc_email.do_colaborador(colaborador_id) if eh_admin() else [],
    )


@bp.route("/<int:colaborador_id>/editar", methods=["GET", "POST"])
@admin_obrigatorio
def editar(colaborador_id):
    colaborador = ou_404(svc.obter(colaborador_id))
    form, erros = dict(colaborador), {}
    if request.method == "POST":
        form = request.form
        try:
            alterou = svc.atualizar(colaborador_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Alterações salvas." if alterou else "Nenhuma alteração para salvar.", "sucesso" if alterou else "info")
            return redirect(url_for("colaboradores.detalhe", colaborador_id=colaborador_id))
    return render_template("colaboradores/form.html",
                           **_contexto_form(form=form, erros=erros, colaborador=colaborador))


@bp.post("/<int:colaborador_id>/desligar")
@admin_obrigatorio
def desligar(colaborador_id):
    try:
        pendentes = svc.desligar(colaborador_id, request.form.get("observacao"))
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        if pendentes:
            flash(f"Colaborador desligado. Ainda há {pendentes} aparelho(s) com ele: registre a devolução.", "aviso")
        else:
            flash("Colaborador desligado.", "sucesso")
    return redirect(url_for("colaboradores.detalhe", colaborador_id=colaborador_id))


@bp.post("/<int:colaborador_id>/reativar")
@admin_obrigatorio
def reativar(colaborador_id):
    try:
        svc.reativar(colaborador_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Colaborador reativado.", "sucesso")
    return redirect(url_for("colaboradores.detalhe", colaborador_id=colaborador_id))


@bp.post("/<int:colaborador_id>/excluir")
@admin_obrigatorio
def excluir(colaborador_id):
    try:
        svc.excluir(colaborador_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("colaboradores.detalhe", colaborador_id=colaborador_id))
    flash("Colaborador excluído.", "sucesso")
    return redirect(url_for("colaboradores.lista"))


@bp.route("/importar", methods=["GET", "POST"])
@admin_obrigatorio
def importar():
    return importacao_comum.tratar("colaboradores", "colaboradores.lista")


@bp.get("/modelo-importacao.xlsx")
@admin_obrigatorio
def modelo_importacao():
    return Response(svc_importacao.gerar_modelo("colaboradores"),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="modelo-importacao-colaboradores.xlsx"'})
