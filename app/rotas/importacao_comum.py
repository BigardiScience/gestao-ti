"""Fluxo de importação compartilhado por aparelhos e colaboradores: validar, conferir e importar."""
import re
import secrets
from pathlib import Path

from flask import current_app, flash, redirect, render_template, request, session, url_for

from ..servicos import ErroNegocio
from ..servicos import importacao as svc_importacao


def _arquivo_temporario(chave: str) -> Path | None:
    if not chave or not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", chave):
        return None
    caminho = Path(current_app.config["PASTA_TEMP"]) / f"importacao-{chave}.xlsx"
    return caminho if caminho.exists() else None


def tratar(tipo: str, destino_lista: str):
    resultado = None
    sessao = f"importacao_{tipo}"
    if request.method == "POST":
        acao = request.form.get("acao")
        criar_unidades = request.form.get("criar_unidades") == "1"
        conteudo = None
        if acao == "importar_validado":
            caminho = _arquivo_temporario(session.get(sessao, {}).get("chave", ""))
            if not caminho:
                flash("A planilha validada expirou. Envie o arquivo novamente.", "erro")
                return redirect(request.path)
            conteudo = caminho.read_bytes()
            criar_unidades = session.get(sessao, {}).get("criar_unidades", False)
        else:
            arquivo = request.files.get("arquivo")
            if not arquivo or not arquivo.filename:
                flash("Escolha a planilha (.xlsx) para importar.", "erro")
            elif not arquivo.filename.lower().endswith((".xlsx", ".xlsm")):
                flash("Envie uma planilha do Excel no formato .xlsx.", "erro")
            else:
                conteudo = arquivo.read()
        if conteudo is not None:
            gravar = acao in ("importar", "importar_validado")
            try:
                resultado = svc_importacao.processar(tipo, conteudo, gravar=gravar, criar_unidades=criar_unidades)
            except ErroNegocio as erro:
                flash(str(erro), "erro")
            else:
                if resultado["gravado"]:
                    _limpar(sessao)
                    rotulo = "aparelho(s)" if tipo == "aparelhos" else "colaborador(es)"
                    texto = f"{resultado['linhas']} {rotulo} importado(s)."
                    if resultado["unidades_criadas"]:
                        texto += f" Unidades criadas: {', '.join(resultado['unidades_criadas'])}."
                    if resultado.get("entregas"):
                        texto += (f" {resultado['entregas']} aparelho(s) já estavam com colaboradores: emita os termos em "
                                  "Termos > Entregas sem termo.")
                    flash(texto, "sucesso")
                    return redirect(url_for(destino_lista))
                if not resultado["erros"] and not resultado["estrutura"]:
                    _limpar(sessao)
                    chave = secrets.token_urlsafe(18)
                    (Path(current_app.config["PASTA_TEMP"]) / f"importacao-{chave}.xlsx").write_bytes(conteudo)
                    session[sessao] = {"chave": chave, "criar_unidades": criar_unidades}
                    resultado["pronto"] = True
    return render_template("aparelhos/importar.html", resultado=resultado, tipo=tipo,
                           modelo=svc_importacao.MODELOS[tipo])


def _limpar(sessao: str):
    anterior = session.pop(sessao, None)
    if anterior:
        caminho = _arquivo_temporario(anterior.get("chave", ""))
        if caminho:
            caminho.unlink(missing_ok=True)
