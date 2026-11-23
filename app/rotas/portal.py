"""Portal do colaborador: acesso por código enviado ao e-mail corporativo."""
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, g, redirect, render_template, request, session, url_for

from ..db import consultar, executar, transacao
from ..seguranca import hash_token, limitador, novo_codigo_numerico, portal_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import colaboradores as svc_colaboradores
from ..servicos import email as svc_email
from ..servicos import termos as svc_termos
from ..servicos.configuracoes import cfg, nome_empresa
from ..util import agora, agora_bd, ip_cliente, navegador_cliente, para_bd
from ..validacao import email_valido, somente_digitos

bp = Blueprint("portal", __name__, url_prefix="/portal")


def _enviar_codigo(colaborador: dict):
    codigo = novo_codigo_numerico()
    with transacao():
        executar("UPDATE codigos_verificacao SET usado_em = ? WHERE colaborador_id = ? AND finalidade = 'portal' "
                 "AND usado_em IS NULL", (agora_bd(), colaborador["id"]))
        executar("INSERT INTO codigos_verificacao (finalidade, colaborador_id, codigo_hash, expira_em, ip) "
                 "VALUES ('portal', ?, ?, ?, ?)",
                 (colaborador["id"], hash_token(f"portal:{colaborador['id']}:{codigo}"),
                  para_bd(agora() + timedelta(minutes=15)), ip_cliente()))
    html = render_template("emails/codigo.html", codigo=codigo, finalidade="portal", empresa=nome_empresa(),
                           primeiro_nome=colaborador["nome"].split()[0])
    svc_email.enviar(colaborador["email"], f"Seu código de acesso: {codigo}", html, finalidade="codigo_portal",
                     colaborador_id=colaborador["id"], ocultar=[codigo], assunto_registro="Seu código de acesso")


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    if g.get("colaborador_portal"):
        return redirect(url_for("portal.inicio"))
    erro = None
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        if not email_valido(email):
            erro = "Informe o seu e-mail corporativo."
        elif limitador.excedeu(f"portal-ip:{ip_cliente()}", 10, 900) or limitador.excedeu(f"portal-email:{email}", 3, 600):
            erro = "Muitos pedidos de código. Aguarde alguns minutos e tente novamente."
        else:
            limitador.registrar(f"portal-ip:{ip_cliente()}")
            limitador.registrar(f"portal-email:{email}")
            colaborador = svc_colaboradores.obter_por_email(email)
            if colaborador:
                _enviar_codigo(colaborador)
            session["portal_email"] = email
            # mesma resposta exista ou não o e-mail, para não revelar quem está cadastrado
            flash("Se este e-mail estiver cadastrado, você vai receber um código de 6 dígitos em instantes.", "info")
            return redirect(url_for("portal.codigo"))
    return render_template("portal/entrar.html", erro=erro)


@bp.route("/codigo", methods=["GET", "POST"])
def codigo():
    email = session.get("portal_email")
    if not email:
        return redirect(url_for("portal.entrar"))
    erro = None
    if request.method == "POST":
        informado = somente_digitos(request.form.get("codigo"))
        colaborador = svc_colaboradores.obter_por_email(email)
        registro = consultar("SELECT * FROM codigos_verificacao WHERE colaborador_id = ? AND finalidade = 'portal' "
                             "AND usado_em IS NULL ORDER BY id DESC LIMIT 1",
                             (colaborador["id"],), um=True) if colaborador else None
        if not registro or registro["expira_em"] < agora_bd() or registro["tentativas"] >= 5:
            erro = "Código expirado ou inválido. Peça um novo código."
        elif hash_token(f"portal:{colaborador['id']}:{informado}") != registro["codigo_hash"]:
            executar("UPDATE codigos_verificacao SET tentativas = tentativas + 1 WHERE id = ?", (registro["id"],))
            erro = "Código incorreto. Confira o e-mail e digite os 6 números."
        else:
            executar("UPDATE codigos_verificacao SET usado_em = ? WHERE id = ?", (agora_bd(), registro["id"]))
            session.pop("portal_email", None)
            session["colaborador_id"] = colaborador["id"]
            session.permanent = True
            return redirect(url_for("portal.inicio"))
    return render_template("portal/codigo.html", erro=erro, email=email)


@bp.get("")
@portal_obrigatorio
def inicio():
    colaborador = g.colaborador_portal
    termos = [t for t in svc_colaboradores.termos(colaborador["id"]) if t["status"] != "cancelado"]
    for termo in termos:
        termo["status_efetivo"] = svc_termos.status_efetivo(termo)
    return render_template("portal/inicio.html", colaborador=colaborador,
                           aparelhos=svc_colaboradores.aparelhos_ativos(colaborador["id"]), termos=termos,
                           contato=cfg("ti_email_contato"))


def _termo_proprio(termo_id: int):
    termo = svc_termos.obter(termo_id, verificar_acesso=False)
    if not termo or termo["colaborador_id"] != g.colaborador_portal["id"] or termo["status"] == "cancelado":
        abort(404)
    return termo


@bp.route("/termos/<int:termo_id>", methods=["GET", "POST"])
@portal_obrigatorio
def termo(termo_id):
    termo = _termo_proprio(termo_id)
    if termo["status"] != "enviado":
        return render_template("portal/termo.html", termo=termo, conteudo=svc_termos.conteudo(termo))
    if request.method == "GET":
        svc_termos.marcar_visualizado(termo, "portal")
        return _pagina_assinatura(termo)
    try:
        svc_termos.assinar(termo_id, request.form, metodo="portal", ip=ip_cliente(), user_agent=navegador_cliente())
    except ErroValidacao as erro:
        return _pagina_assinatura(termo, request.form, erro.erros), 422
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("portal.termo", termo_id=termo_id))
    flash("Termo assinado. Uma cópia em PDF foi enviada para o seu e-mail.", "sucesso")
    return redirect(url_for("portal.termo", termo_id=termo_id))


def _pagina_assinatura(termo, form=None, erros=None):
    return render_template("assinatura/assinar.html", termo=termo, conteudo=svc_termos.conteudo(termo),
                           form=form or {}, erros=erros or {}, exige_codigo=False, codigo_enviado_para=None,
                           aviso_privacidade=cfg("aviso_privacidade"), metodo="portal",
                           acao_url=url_for("portal.termo", termo_id=termo["id"]))


@bp.get("/termos/<int:termo_id>/pdf")
@portal_obrigatorio
def pdf(termo_id):
    termo = _termo_proprio(termo_id)
    try:
        conteudo = svc_termos.pdf_do_termo(termo)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("portal.inicio"))
    sufixo = "assinado" if termo["status"] == "assinado" else "previa"
    return Response(conteudo, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{termo["codigo"]}-{sufixo}.pdf"'})


@bp.post("/sair")
def sair():
    session.pop("colaborador_id", None)
    flash("Você saiu do portal.", "info")
    return redirect(url_for("portal.entrar"))
