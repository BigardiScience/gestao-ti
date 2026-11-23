"""Telas de unidades (filiais)."""
from flask import Blueprint, flash, redirect, render_template, request, url_for

from ..seguranca import admin_obrigatorio, login_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import teams as svc_teams
from ..servicos import unidades as svc
from ..servicos.registro import auditar
from . import ou_404

bp = Blueprint("unidades", __name__, url_prefix="/unidades")


@bp.get("")
@login_obrigatorio
def lista():
    return render_template("unidades/lista.html", unidades=svc.listar(com_contagens=True))


@bp.route("/nova", methods=["GET", "POST"])
@admin_obrigatorio
def nova():
    form, erros = {"ativo": "1"}, {}
    if request.method == "POST":
        form = request.form
        try:
            svc.criar(form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        else:
            flash("Unidade cadastrada.", "sucesso")
            return redirect(url_for("unidades.lista"))
    return render_template("unidades/form.html", form=form, erros=erros, unidade=None, ufs=svc.UFS,
                           outras=svc.listar(incluir_inativas=False), tambem=_tambem(form))


@bp.route("/<int:unidade_id>/editar", methods=["GET", "POST"])
@admin_obrigatorio
def editar(unidade_id):
    unidade = ou_404(svc.obter(unidade_id))
    form, erros = {c: v for c, v in unidade.items() if c != "teams_webhook"}, {}
    form["ativo"] = "1" if unidade["ativo"] else "0"
    form["chamados_ativo"] = "1" if unidade["chamados_ativo"] else "0"
    if request.method == "POST":
        form = request.form
        try:
            svc.atualizar(unidade_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Unidade atualizada.", "sucesso")
            return redirect(url_for("unidades.lista"))
    outras = [u for u in svc.listar(incluir_inativas=False) if u["id"] != unidade_id]
    return render_template("unidades/form.html", form=form, erros=erros, unidade=unidade, ufs=svc.UFS,
                           outras=outras, tambem=_tambem(form))


def _tambem(form) -> set:
    if request.method == "POST":
        return {int(v) for v in request.form.getlist("teams_tambem") if v.isdigit()}
    return set(svc_teams.ids_tambem(form.get("teams_tambem")))


@bp.post("/<int:unidade_id>/teams/testar")
@admin_obrigatorio
def testar_teams(unidade_id):
    unidade = ou_404(svc.obter(unidade_id))
    link = svc_teams.link_da_unidade(unidade_id)
    if not link:
        flash("Cadastre o link do fluxo do Teams antes de testar.", "erro")
    else:
        try:
            svc_teams.testar(link, unidade["nome"])
        except svc_teams.ErroTeams as erro:
            flash(f"O teste não chegou ao Teams: {erro}", "erro")
        else:
            flash("Mensagem de teste enviada. Confira no canal do Teams.", "sucesso")
        auditar("unidade_teams_testado", "unidade", unidade_id)
    return redirect(url_for("unidades.editar", unidade_id=unidade_id))


@bp.post("/<int:unidade_id>/excluir")
@admin_obrigatorio
def excluir(unidade_id):
    try:
        svc.excluir(unidade_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Unidade excluída.", "sucesso")
    return redirect(url_for("unidades.lista"))
