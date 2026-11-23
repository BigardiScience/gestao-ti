"""Telas dos termos de responsabilidade."""
import json
import time

from flask import Blueprint, Response, current_app, flash, redirect, render_template, request, url_for

from ..db import consultar
from ..seguranca import admin_obrigatorio, garantir_acesso_unidade, login_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import colaboradores as svc_colaboradores
from ..servicos import email as svc_email
from ..servicos import termos as svc
from ..servicos import unidades as svc_unidades
from ..servicos.registro import auditar
from ..util import agora_bd
from . import destino_seguro, filtros_da_url, ou_404

bp = Blueprint("termos", __name__, url_prefix="/termos")

FILTROS = ("q", "status", "unidade", "colaborador", "departamento", "de", "ate")


@bp.get("")
@login_obrigatorio
def lista():
    filtros = filtros_da_url(*FILTROS)
    contagens = svc.contagens()
    sem_termo = svc.vinculos_sem_termo()
    if filtros.get("status") == "sem_termo":
        return render_template("termos/sem_termo.html", vinculos=sem_termo, contagens=contagens,
                               filtros=filtros, total_sem_termo=len(sem_termo))
    ordem = request.args.get("ordem", "recentes")
    return render_template("termos/lista.html", pagina=svc.listar(filtros, ordem), filtros=filtros, ordem=ordem,
                           contagens=contagens, total_sem_termo=len(sem_termo), unidades=svc_unidades.listar(),
                           departamentos=svc_colaboradores.departamentos(), filtros_status=svc.FILTROS_STATUS)


@bp.get("/<int:termo_id>")
@login_obrigatorio
def detalhe(termo_id):
    termo = ou_404(svc.obter(termo_id))
    integridade = svc.integridade_arquivo(termo) if termo["status"] == "assinado" else None
    return render_template(
        "termos/detalhe.html", termo=termo, conteudo=svc.conteudo(termo), eventos=svc.eventos(termo_id),
        emails=svc_email.do_termo(termo_id), integridade=integridade,
        selo_ok=svc.selo_valido(termo) if termo["status"] == "assinado" else None,
        evidencias=json.loads(termo["evidencias_json"]) if termo["evidencias_json"] else None,
        rotulos_eventos=svc.EVENTOS, agora=agora_bd(),
    )


@bp.get("/<int:termo_id>/pdf")
@login_obrigatorio
def pdf(termo_id):
    termo = ou_404(svc.obter(termo_id))
    try:
        conteudo = svc.pdf_do_termo(termo)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("termos.detalhe", termo_id=termo_id))
    if termo["status"] == "assinado":
        auditar("termo_pdf_baixado", "termo", termo_id, {"codigo": termo["codigo"]})
        nome = f"{termo['codigo']}-assinado.pdf"
    else:
        nome = f"{termo['codigo']}-previa.pdf"
    disposicao = "inline" if request.args.get("ver") else "attachment"
    return Response(conteudo, mimetype="application/pdf",
                    headers={"Content-Disposition": f'{disposicao}; filename="{nome}"'})


def _mensagem_envio(resultado, reenvio: bool):
    if resultado.ok:
        if resultado.status == "simulado":
            flash("Modo de simulação: o e-mail não saiu do sistema. Veja a mensagem (com o link de assinatura) em "
                  "Configurações > E-mails enviados.", "aviso")
        else:
            flash("Link de assinatura reenviado." if reenvio else "Termo enviado para assinatura.", "sucesso")
    else:
        flash(f"Não foi possível enviar o e-mail: {resultado.erro}", "erro")


@bp.post("/<int:termo_id>/enviar")
@admin_obrigatorio
def enviar(termo_id):
    termo = ou_404(svc.obter(termo_id))
    try:
        resultado = svc.enviar(termo_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        _mensagem_envio(resultado, termo["status"] == "enviado")
    return redirect(destino_seguro(request.form.get("voltar"), url_for("termos.detalhe", termo_id=termo_id)))


@bp.post("/<int:termo_id>/lembrete")
@admin_obrigatorio
def lembrete(termo_id):
    ou_404(svc.obter(termo_id))
    try:
        resultado = svc.enviar(termo_id, lembrete=True)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        if resultado.ok:
            flash("Lembrete enviado." if resultado.status == "enviado" else "Lembrete registrado (simulação).", "sucesso")
        else:
            flash(f"Não foi possível enviar o lembrete: {resultado.erro}", "erro")
    return redirect(url_for("termos.detalhe", termo_id=termo_id))


@bp.post("/<int:termo_id>/cancelar")
@admin_obrigatorio
def cancelar(termo_id):
    try:
        svc.cancelar(termo_id, request.form.get("motivo"))
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()), "erro")
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Termo cancelado. Se a entrega continua valendo, emita um novo termo.", "sucesso")
    return redirect(url_for("termos.detalhe", termo_id=termo_id))


@bp.post("/emitir/<int:vinculo_id>")
@admin_obrigatorio
def emitir(vinculo_id):
    vinculo = ou_404(consultar("SELECT v.*, a.unidade_id FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id "
                               "WHERE v.id = ?", (vinculo_id,), um=True))
    garantir_acesso_unidade(vinculo["unidade_id"])
    try:
        termo_id = svc.emitir(vinculo_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(destino_seguro(request.form.get("voltar"), url_for("termos.lista", status="sem_termo")))
    if request.form.get("enviar_agora") in ("1", "on"):
        _mensagem_envio(svc.enviar(termo_id), False)
    else:
        flash("Termo emitido. Revise e envie para assinatura.", "sucesso")
    return redirect(url_for("termos.detalhe", termo_id=termo_id))


@bp.post("/lote")
@admin_obrigatorio
def lote():
    acao = request.form.get("acao")
    intervalo = 0 if current_app.config["TESTING"] else current_app.config["INTERVALO_ENVIO_LOTE"]
    ok = falhas = 0
    if acao == "emitir_pendentes":
        enviar = request.form.get("enviar_agora") in ("1", "on")
        for indice, vinculo in enumerate(svc.vinculos_sem_termo()):
            try:
                termo_id = svc.emitir(vinculo["id"])
                if enviar:
                    if indice:
                        time.sleep(intervalo)
                    resultado = svc.enviar(termo_id)
                    falhas += not resultado.ok
                ok += 1
            except ErroNegocio:
                falhas += 1
        flash(f"{ok} termo(s) emitido(s)" + (" e enviado(s)" if enviar else "") +
              (f"; {falhas} com problema." if falhas else "."), "sucesso" if not falhas else "aviso")
        return redirect(url_for("termos.lista", status="enviado" if enviar else "gerado"))
    if acao in ("enviar_gerados", "lembrar_aguardando", "reenviar_expirados"):
        status = {"enviar_gerados": "gerado", "lembrar_aguardando": "enviado", "reenviar_expirados": "expirado"}[acao]
        alvos = svc.listar_todos({"status": status})
        for indice, termo in enumerate(alvos):
            if indice:
                time.sleep(intervalo)
            try:
                resultado = svc.enviar(termo["id"], lembrete=acao == "lembrar_aguardando")
                ok += resultado.ok
                falhas += not resultado.ok
            except ErroNegocio:
                falhas += 1
        flash(f"{ok} e-mail(s) enviado(s)" + (f", {falhas} falha(s)." if falhas else "."),
              "sucesso" if not falhas else "aviso")
        return redirect(url_for("termos.lista", status="enviado"))
    flash("Ação desconhecida.", "erro")
    return redirect(url_for("termos.lista"))
