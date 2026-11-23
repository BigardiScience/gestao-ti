"""Telas de computadores (desktops e notebooks), organizadas por unidade."""
import secrets
from pathlib import Path

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect, render_template, request, session,
                   url_for)

from ..seguranca import (eh_tecnico, login_obrigatorio, pode_editar_unidade, tecnico_obrigatorio,
                         ve_dados_de_rede)
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import computadores as svc
from ..servicos import importacao as svc_importacao
from ..servicos.registro import auditar
from . import filtros_da_url, id_inteiro, ou_404
from .importacao_comum import _arquivo_temporario, _limpar

bp = Blueprint("computadores", __name__, url_prefix="/computadores")

FILTROS = ("q", "situacao", "atencao", "tipo", "sep", "setor")


def _unidade_escolhida(unidades: list[dict]) -> int | None:
    """Unidade da URL; sem ela, o técnico começa pela própria unidade e os demais por todas."""
    valor = request.args.get("unidade")
    visiveis = {u["id"] for u in unidades}
    if valor == "todas":
        return None
    if valor:
        unidade = id_inteiro(valor)
        return unidade if unidade in visiveis else None
    proprias = g.usuario.get("unidades") or set()
    if eh_tecnico() and len(proprias) == 1 and next(iter(proprias)) in visiveis:
        return next(iter(proprias))
    return None


def _filtros_e_unidade():
    unidades = svc.unidades_visiveis()
    filtros = filtros_da_url(*FILTROS)
    unidade = _unidade_escolhida(unidades)
    if unidade:
        filtros["unidade"] = unidade
    return unidades, filtros, unidade


@bp.get("")
@login_obrigatorio
def lista():
    unidades, filtros, unidade = _filtros_e_unidade()
    ordem = request.args.get("ordem", "nome")
    return render_template("computadores/lista.html", pagina=svc.listar(filtros, ordem), filtros=filtros,
                           ordem=ordem, unidades=unidades, unidade=unidade, numeros=svc.indicadores(filtros),
                           setores=svc.valores_distintos("setor"), rede=ve_dados_de_rede())


@bp.get("/exportar.xlsx")
@login_obrigatorio
def exportar():
    _, filtros, _ = _filtros_e_unidade()
    conteudo, nome, total = svc.exportar(filtros, request.args.get("ordem", "nome"), g.usuario["nome"])
    auditar("computadores_exportados", "computador", None,
            {"filtros": {k: v for k, v in filtros.items()}, "registros": total})
    return Response(conteudo, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


def _contexto_form(**extra):
    return dict(unidades=svc.unidades_para_cadastro(), tipos=svc.TIPOS, situacoes=svc.SITUACOES,
                situacoes_usuario=svc.SITUACOES_USUARIO, sep=svc.SEP, ferramentas=svc.FERRAMENTAS_REMOTO,
                motivos=svc.MOTIVOS_DESATIVACAO, destinos=svc.DESTINOS_DISCO,
                setores=svc.valores_distintos("setor"), monitores=svc.valores_distintos("monitor"),
                sistemas=sorted(set(svc.SUGESTOES_SISTEMA) | set(svc.valores_distintos("sistema")), key=str.lower),
                processadores=svc.valores_distintos("processador"), marcas=svc.valores_distintos("marca"), **extra)


@bp.route("/novo", methods=["GET", "POST"])
@tecnico_obrigatorio
def novo():
    unidades = svc.unidades_para_cadastro()
    padrao = id_inteiro(request.args.get("unidade")) or (unidades[0]["id"] if len(unidades) == 1 else "")
    form, erros = {"tipo": request.args.get("tipo") or "desktop", "situacao": "em_uso", "unidade_id": padrao,
                   "usuario_situacao": "ativo"}, {}
    if request.method == "POST":
        form = request.form
        try:
            computador_id = svc.criar(form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        else:
            flash("Computador cadastrado.", "sucesso")
            if form.get("proximo") == "outro":
                return redirect(url_for("computadores.novo", unidade=form.get("unidade_id"), tipo=form.get("tipo")))
            return redirect(url_for("computadores.detalhe", computador_id=computador_id))
    return render_template("computadores/form.html", **_contexto_form(form=form, erros=erros, computador=None))


@bp.get("/<int:computador_id>")
@login_obrigatorio
def detalhe(computador_id):
    computador = ou_404(svc.obter(computador_id))
    from ..servicos.chamados import do_equipamento
    return render_template("computadores/detalhe.html", c=computador, historico=svc.historico(computador_id),
                           pode_editar=pode_editar_unidade(computador["unidade_id"]), rede=ve_dados_de_rede(),
                           chamados_equipamento=do_equipamento(g.usuario, computador_id=computador_id))


@bp.route("/<int:computador_id>/editar", methods=["GET", "POST"])
@tecnico_obrigatorio
def editar(computador_id):
    computador = ou_404(svc.obter(computador_id))
    if not pode_editar_unidade(computador["unidade_id"]):
        abort(403, description="Você só pode alterar computadores da sua unidade.")
    form = dict(computador)
    form["nobreak"] = "" if computador["nobreak"] is None else str(computador["nobreak"])
    erros = {}
    if request.method == "POST":
        form = request.form
        try:
            alterou = svc.atualizar(computador_id, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Alterações salvas." if alterou else "Nenhuma alteração para salvar.", "sucesso" if alterou else "info")
            return redirect(url_for("computadores.detalhe", computador_id=computador_id))
    return render_template("computadores/form.html", **_contexto_form(form=form, erros=erros, computador=computador))


@bp.post("/<int:computador_id>/conferir")
@tecnico_obrigatorio
def conferir(computador_id):
    try:
        svc.conferir(computador_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Conferência registrada.", "sucesso")
    return redirect(url_for("computadores.detalhe", computador_id=computador_id))


@bp.post("/<int:computador_id>/excluir")
@tecnico_obrigatorio
def excluir(computador_id):
    try:
        svc.excluir(computador_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
        return redirect(url_for("computadores.detalhe", computador_id=computador_id))
    flash("Computador excluído.", "sucesso")
    return redirect(url_for("computadores.lista"))


# ------------------------------------------------------------------ importação (uma planilha por unidade)

@bp.route("/importar", methods=["GET", "POST"])
@tecnico_obrigatorio
def importar():
    unidades = svc.unidades_para_cadastro()
    sessao = "importacao_computadores"
    resultado = None
    unidade_id = id_inteiro(request.values.get("unidade_id")) or (unidades[0]["id"] if len(unidades) == 1 else None)
    if request.method == "POST":
        acao = request.form.get("acao")
        conteudo = None
        if acao == "importar_validado":
            guardado = session.get(sessao) or {}
            caminho = _arquivo_temporario(guardado.get("chave", ""))
            if not caminho:
                flash("A planilha validada expirou. Envie o arquivo novamente.", "erro")
                return redirect(request.path)
            conteudo, unidade_id = caminho.read_bytes(), guardado.get("unidade_id")
        else:
            arquivo = request.files.get("arquivo")
            if unidade_id not in {u["id"] for u in unidades}:
                flash("Escolha a unidade dos computadores.", "erro")
            elif not arquivo or not arquivo.filename:
                flash("Escolha a planilha (.xlsx) para importar.", "erro")
            elif not arquivo.filename.lower().endswith((".xlsx", ".xlsm")):
                flash("Envie uma planilha do Excel no formato .xlsx (no Excel: Salvar como > Pasta de Trabalho do Excel).",
                      "erro")
            else:
                conteudo = arquivo.read()
        if conteudo is not None:
            try:
                resultado = svc.importar_planilha(conteudo, unidade_id, gravar=acao == "importar_validado")
            except ErroNegocio as erro:
                flash(str(erro), "erro")
            else:
                if resultado["gravado"]:
                    _limpar(sessao)
                    flash(f"{resultado['linhas']} computador(es) importado(s).", "sucesso")
                    return redirect(url_for("computadores.lista", unidade=unidade_id))
                if not resultado["erros"] and not resultado["estrutura"]:
                    _limpar(sessao)
                    chave = secrets.token_urlsafe(18)
                    (Path(current_app.config["PASTA_TEMP"]) / f"importacao-{chave}.xlsx").write_bytes(conteudo)
                    session[sessao] = {"chave": chave, "unidade_id": unidade_id}
                    resultado["pronto"] = True
    nome_unidade = next((u["nome"] for u in unidades if u["id"] == unidade_id), "")
    return render_template("computadores/importar.html", resultado=resultado, unidades=unidades,
                           unidade_id=unidade_id, nome_unidade=nome_unidade,
                           modelo=svc_importacao.MODELOS["computadores"])


@bp.get("/modelo-importacao.xlsx")
@tecnico_obrigatorio
def modelo_importacao():
    return Response(svc_importacao.gerar_modelo("computadores"),
                    mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": 'attachment; filename="modelo-importacao-computadores.xlsx"'})
