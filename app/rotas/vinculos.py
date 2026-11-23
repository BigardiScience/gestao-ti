"""Entrega, devolução, transferência e substituição de aparelhos."""
import json

from flask import Blueprint, Response, abort, flash, jsonify, redirect, render_template, request, url_for

from ..db import consultar
from ..seguranca import admin_obrigatorio, login_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import aparelhos as svc_aparelhos
from ..servicos import colaboradores as svc_colaboradores
from ..servicos import termos as svc_termos
from ..servicos import vinculos as svc
from ..servicos.configuracoes import cfg_int
from ..servicos.pdf import gerar_comprovante_devolucao
from ..util import valor_datetime_local
from . import id_inteiro, ou_404

bp = Blueprint("vinculos", __name__)


def _enviar_se_pedido(termo_id, form):
    if termo_id and form.get("enviar_agora") in ("1", "on"):
        resultado = svc_termos.enviar(termo_id)
        if resultado.ok:
            flash("Termo enviado para o e-mail do colaborador." if resultado.status == "enviado" else
                  "Termo “enviado” em modo de simulação: veja a mensagem em Configurações > E-mails enviados.",
                  "sucesso")
        else:
            flash(f"O termo foi gerado, mas o e-mail não foi enviado: {resultado.erro}", "erro")
        return True
    return False


def _depois_de_gerar(termo_id, form, destino_sem_termo):
    if not termo_id:
        return redirect(destino_sem_termo)
    if not _enviar_se_pedido(termo_id, form):
        flash("Termo de responsabilidade gerado. Revise e envie para assinatura.", "info")
    return redirect(url_for("termos.detalhe", termo_id=termo_id))


@bp.route("/entregas/nova", methods=["GET", "POST"])
@admin_obrigatorio
def entregar():
    form = {"aparelho_id": request.args.get("aparelho", ""), "colaborador_id": request.args.get("colaborador", ""),
            "entregue_em": valor_datetime_local(), "gerar_termo": "1"}
    erros = {}
    if request.method == "POST":
        form = request.form
        try:
            _, termo_id = svc.entregar(form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Entrega registrada.", "sucesso")
            return _depois_de_gerar(termo_id, form, url_for("aparelhos.detalhe", aparelho_id=form["aparelho_id"]))
    return render_template("vinculos/entrega.html", form=form, erros=erros, estados=svc_aparelhos.ESTADOS,
                           tem_disponiveis=svc_aparelhos.existe_disponivel(),
                           tem_colaboradores=svc_colaboradores.existe_ativo(),
                           aparelho_escolhido=svc_aparelhos.opcao_disponivel(form.get("aparelho_id")),
                           colaborador_escolhido=svc_colaboradores.opcao_selecao(form.get("colaborador_id")),
                           limite=cfg_int("limite_aparelhos_colaborador", 0))


@bp.get("/opcoes/colaboradores")
@admin_obrigatorio
def opcoes_colaboradores():
    """Busca de colaboradores para os campos de escolha (devolve no máximo 20)."""
    return jsonify(svc_colaboradores.opcoes_selecao(request.args.get("q", "")[:80],
                                                    excluir=id_inteiro(request.args.get("excluir"))))


@bp.get("/opcoes/aparelhos")
@admin_obrigatorio
def opcoes_aparelhos():
    """Busca de aparelhos disponíveis para os campos de escolha (devolve no máximo 20)."""
    return jsonify(svc_aparelhos.opcoes_disponiveis(request.args.get("q", "")[:80]))


def _vinculo_aberto(vinculo_id):
    vinculo = ou_404(svc.obter(vinculo_id))
    if vinculo["devolvido_em"]:
        flash("Esta entrega já foi encerrada.", "info")
        return vinculo, redirect(url_for("aparelhos.detalhe", aparelho_id=vinculo["aparelho_id"]))
    return vinculo, None


@bp.route("/vinculos/<int:vinculo_id>/devolver", methods=["GET", "POST"])
@admin_obrigatorio
def devolver(vinculo_id):
    vinculo, desvio = _vinculo_aberto(vinculo_id)
    if desvio:
        return desvio
    form = {"devolvido_em": valor_datetime_local(), "estado": vinculo["estado_conservacao"], "destino": "devolvido",
            "acessorios": vinculo["acessorios_entrega"] or ""}
    erros = {}
    if request.method == "POST":
        form = request.form
        try:
            svc.devolver(vinculo_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Devolução registrada. Você pode baixar o comprovante de devolução no histórico do aparelho.",
                  "sucesso")
            return redirect(url_for("aparelhos.detalhe", aparelho_id=vinculo["aparelho_id"]))
    return render_template("vinculos/devolucao.html", vinculo=vinculo, form=form, erros=erros,
                           estados=svc_aparelhos.ESTADOS, destinos=svc.DESTINOS_DEVOLUCAO,
                           motivos_baixa=svc_aparelhos.MOTIVOS_BAIXA, ajuda_status=svc_aparelhos.STATUS_AJUDA)


@bp.route("/vinculos/<int:vinculo_id>/transferir", methods=["GET", "POST"])
@admin_obrigatorio
def transferir(vinculo_id):
    vinculo, desvio = _vinculo_aberto(vinculo_id)
    if desvio:
        return desvio
    form = {"entregue_em": valor_datetime_local(), "estado": vinculo["estado_conservacao"],
            "acessorios": vinculo["acessorios_entrega"] or "", "gerar_termo": "1"}
    erros = {}
    if request.method == "POST":
        form = request.form
        try:
            _, termo_id = svc.transferir(vinculo_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Transferência registrada.", "sucesso")
            return _depois_de_gerar(termo_id, form, url_for("aparelhos.detalhe", aparelho_id=vinculo["aparelho_id"]))
    escolhido = svc_colaboradores.opcao_selecao(form.get("colaborador_id"))
    if escolhido and escolhido["id"] == vinculo["colaborador_id"]:
        escolhido = None
    return render_template("vinculos/transferencia.html", vinculo=vinculo, form=form, erros=erros,
                           colaborador_escolhido=escolhido, estados=svc_aparelhos.ESTADOS)


@bp.route("/vinculos/<int:vinculo_id>/substituir", methods=["GET", "POST"])
@admin_obrigatorio
def substituir(vinculo_id):
    vinculo, desvio = _vinculo_aberto(vinculo_id)
    if desvio:
        return desvio
    form = {"entregue_em": valor_datetime_local(), "estado_devolucao": vinculo["estado_conservacao"],
            "destino": "manutencao", "gerar_termo": "1"}
    erros = {}
    if request.method == "POST":
        form = request.form
        try:
            _, termo_id = svc.substituir(vinculo_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Substituição registrada.", "sucesso")
            return _depois_de_gerar(termo_id, form, url_for("colaboradores.detalhe",
                                                            colaborador_id=vinculo["colaborador_id"]))
    return render_template("vinculos/substituicao.html", vinculo=vinculo, form=form, erros=erros,
                           tem_disponiveis=svc_aparelhos.existe_disponivel(), estados=svc_aparelhos.ESTADOS,
                           aparelho_escolhido=svc_aparelhos.opcao_disponivel(form.get("aparelho_id")),
                           destinos=svc.DESTINOS_DEVOLUCAO, motivos_baixa=svc_aparelhos.MOTIVOS_BAIXA,
                           ajuda_status=svc_aparelhos.STATUS_AJUDA)


@bp.get("/vinculos/<int:vinculo_id>/comprovante.pdf")
@login_obrigatorio
def comprovante(vinculo_id):
    vinculo = ou_404(svc.obter(vinculo_id))
    if not vinculo["devolvido_em"]:
        abort(404)
    aparelho = svc_aparelhos.obter(vinculo["aparelho_id"])
    colaborador = svc_colaboradores.obter(vinculo["colaborador_id"], verificar_acesso=False)
    registro = consultar("SELECT detalhes FROM movimentacoes WHERE vinculo_id = ? AND tipo = 'devolucao' "
                         "ORDER BY id DESC LIMIT 1", (vinculo_id,), um=True)
    detalhes = json.loads(registro["detalhes"]) if registro and registro["detalhes"] else {}
    if detalhes.get("destino"):
        destino = svc_aparelhos.STATUS.get(detalhes["destino"], "")
    else:
        destino = {"transferencia": "Transferido para outro colaborador", "substituicao": "Recolhido na troca de aparelho"
                   }.get(vinculo["motivo_encerramento"], svc_aparelhos.STATUS.get(aparelho["status"], ""))
    pdf = gerar_comprovante_devolucao(vinculo, aparelho, colaborador, destino)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="comprovante-devolucao-{vinculo_id}.pdf"'})
