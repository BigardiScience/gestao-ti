"""Páginas públicas: assinatura do termo pelo link do e-mail e verificação de autenticidade."""
import time
from pathlib import Path

from flask import (Blueprint, abort, current_app, flash, redirect, render_template, request, send_file, session,
                   url_for)

from ..seguranca import limitador
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import termos as svc
from ..servicos.configuracoes import cfg, cfg_bool
from ..util import ip_cliente, navegador_cliente

bp = Blueprint("assinatura", __name__)

VALIDADE_DOWNLOAD = 2 * 3600  # depois de assinar, o PDF fica disponível nesta sessão por 2 horas


def _pagina(termo, form=None, erros=None, codigo_enviado_para=None, metodo="link", acao_url=None):
    return render_template(
        "assinatura/assinar.html", termo=termo, conteudo=svc.conteudo(termo), form=form or {}, erros=erros or {},
        exige_codigo=metodo == "link" and cfg_bool("assinatura_exigir_codigo"),
        codigo_enviado_para=codigo_enviado_para or session.get(f"codigo_enviado_{termo['id']}"),
        aviso_privacidade=cfg("aviso_privacidade"), metodo=metodo, acao_url=acao_url or request.path,
    )


@bp.route("/assinar/<token>", methods=["GET", "POST"])
def assinar(token):
    if limitador.excedeu(f"assinar-ip:{ip_cliente()}", 120, 600):
        abort(429)
    termo, situacao = svc.localizar_por_token(token)
    if situacao != "ok":
        limitador.registrar(f"assinar-ip:{ip_cliente()}")
        return render_template("assinatura/indisponivel.html", situacao=situacao, termo=termo,
                               contato=cfg("ti_email_contato")), 404 if situacao == "invalido" else 410
    if request.method == "GET":
        svc.marcar_visualizado(termo, "link")
        return _pagina(termo)

    form = request.form
    if form.get("acao") == "enviar_codigo":
        try:
            destino = svc.solicitar_codigo(termo, form.get("cpf"))
        except ErroValidacao as erro:
            return _pagina(termo, form, erro.erros), 422
        except ErroNegocio as erro:
            return _pagina(termo, form, {"codigo": str(erro)}), 429
        session[f"codigo_enviado_{termo['id']}"] = destino
        return _pagina(termo, form, codigo_enviado_para=destino)

    try:
        assinado = svc.assinar(termo["id"], form, metodo="link", ip=ip_cliente(), user_agent=navegador_cliente())
    except ErroValidacao as erro:
        return _pagina(termo, form, erro.erros), 422
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("assinatura.assinar", token=token))
    session.pop(f"codigo_enviado_{termo['id']}", None)
    session["termo_assinado"] = {"id": assinado["id"], "ate": time.time() + VALIDADE_DOWNLOAD}
    return redirect(url_for("assinatura.concluido"))


def _termo_da_sessao():
    dados = session.get("termo_assinado")
    if not dados or dados.get("ate", 0) < time.time():
        return None
    return svc.obter(dados["id"], verificar_acesso=False)


@bp.get("/assinar/concluido")
def concluido():
    termo = _termo_da_sessao()
    if not termo:
        return redirect(url_for("assinatura.verificar"))
    return render_template("assinatura/concluido.html", termo=termo)


@bp.get("/assinar/concluido/pdf")
def pdf_assinado():
    termo = _termo_da_sessao()
    if not termo or termo["status"] != "assinado":
        abort(404)
    caminho = svc.caminho_pdf(termo)
    if not caminho:
        abort(404)
    return send_file(caminho, mimetype="application/pdf", as_attachment=True,
                     download_name=f"{termo['codigo']}-assinado.pdf")


@bp.route("/verificar", methods=["GET", "POST"])
def verificar():
    resultado, erro, codigo = None, None, (request.values.get("codigo") or "").strip()
    if request.method == "POST" or codigo:
        if limitador.excedeu(f"verificar:{ip_cliente()}", 30, 600):
            erro = "Muitas consultas seguidas. Aguarde alguns minutos."
        else:
            limitador.registrar(f"verificar:{ip_cliente()}")
            arquivo = request.files.get("arquivo")
            if arquivo and arquivo.filename:
                dados = arquivo.read()
                resultado = svc.verificar_arquivo(dados)
                if not resultado:
                    erro = ("Este arquivo não corresponde a nenhum termo assinado nesta plataforma. Se ele foi "
                            "alterado depois da assinatura, mesmo que só um pouco, a verificação não o reconhece.")
            elif codigo:
                resultado = svc.verificar_codigo(codigo)
                if not resultado:
                    erro = "Código de verificação não encontrado. Confira os 12 caracteres no rodapé do termo."
            else:
                erro = "Envie o arquivo PDF ou informe o código de verificação."
    return render_template("assinatura/verificar.html", resultado=resultado, erro=erro, codigo=codigo)


@bp.get("/marca/logo.png")
def logo():
    arquivo = cfg("logo_arquivo")
    caminho = Path(current_app.config["PASTA_UPLOADS"]) / arquivo if arquivo else None
    if not caminho or not caminho.exists():
        abort(404)
    resposta = send_file(caminho, mimetype="image/png", max_age=3600)
    return resposta

