"""Pedidos para a TI — telas do colaborador (linguagem simples, letras e botões grandes)."""
import re

from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from ..seguranca import limitador, pedidos_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import chamados as svc
from ..servicos.configuracoes import cfg, cfg_int
from ..util import ip_cliente, navegador_cliente
from . import id_inteiro

bp = Blueprint("pedidos", __name__, url_prefix="/pedidos")


def _meu(chamado_id: int) -> dict:
    chamado = svc.obter(chamado_id)
    if not chamado or chamado["solicitante_id"] != g.usuario["id"]:
        abort(404)
    return chamado


# Navegador de celular ou tablet: aí "neste computador" não faz sentido (a pessoa está no celular).
PARECE_CELULAR = re.compile(r"Mobi|Android|iPhone|iPad|iPod", re.I)


def _passo_inicial(form, erros) -> int:
    """São sempre 3 passos ("Passo X de 3"). Categorias sem equipamento pulam do 1 direto para o 3."""
    categoria = form.get("categoria")
    if erros.get("categoria") or categoria not in svc.CATEGORIAS:
        return 1
    if erros:
        return 3
    passo = int(form["ir"]) if (form.get("ir") or "") in ("1", "2", "3") else 1
    if passo == 2 and categoria not in svc.CATEGORIAS_COM_EQUIPAMENTO:
        passo = 3
    return passo


def _listas(usuario, opcoes: dict) -> dict:
    """O que aparece no passo 2: a lista de computadores (depois de "Não, é em outro") e a de celulares."""
    origem = opcoes["origem"]
    computadores = [{"valor": f"comp:{c['id']}", "rotulo": c["rotulo"], "icone": "computador"}
                    for c in opcoes["computadores"]]
    if not computadores:  # nada no inventário em nome da pessoa: opções simples, que vão como texto para a TI
        setor = (usuario.get("setor") or "").strip()
        for valor, (rotulo, _) in svc.COMPUTADORES_SEM_CADASTRO.items():
            if origem and valor == ("pc:notebook" if origem["tipo"] == "notebook" else "pc:mesa"):
                continue  # o computador de onde a pessoa abriu já é o "Sim, neste"
            if valor == "pc:mesa" and setor:
                rotulo = f"{rotulo} ({setor})"
            computadores.append({"valor": valor, "rotulo": rotulo, "icone": "computador"})
    computadores.append({"valor": "nao_sei", "rotulo": "Não sei qual é", "icone": "pergunta"})
    celulares = [{"valor": f"cel:{a['id']}", "rotulo": a["rotulo"], "icone": "celular"} for a in opcoes["celulares"]]
    if not celulares:
        celulares.append({"valor": "cel:empresa", "rotulo": "O celular da empresa que está comigo", "icone": "celular"})
    celulares.append({"valor": "nao_sei", "rotulo": "Não sei qual é", "icone": "pergunta"})
    return {"computadores": computadores, "celulares": celulares}


@bp.route("/novo", methods=["GET", "POST"])
@pedidos_obrigatorio
def novo():
    usuario = g.usuario
    opcoes = svc.opcoes_equipamento(usuario, ip_cliente())
    # "Sim, neste" aparece quando a máquina foi reconhecida pelo IP ou quando a pessoa está num computador
    # (fora do inventário, o pedido guarda "neste computador" + o IP de onde veio, para a TI localizar).
    pode_este = bool(opcoes["origem"]) or not PARECE_CELULAR.search(navegador_cliente())
    form, erros = {}, {}
    passo = 1
    if request.method == "POST":
        form = request.form.to_dict()
        escolhida = form.pop("escolher_categoria", None)
        if escolhida in svc.CATEGORIAS:  # navegação sem JavaScript: cada passo volta ao servidor
            if escolhida != form.get("categoria"):
                form.pop("equipamento", None)
            form["categoria"] = escolhida
            form["ir"] = "2"
        equipamento = form.pop("escolher_equipamento", None)
        if equipamento == "outro":
            form.update(mostrar_lista="1", ir="2")
        elif equipamento:
            form.update(equipamento=equipamento, ir="3")
        if form.get("enviar") == "1" and limitador.excedeu(f"pedidos:{usuario['id']}", 15, 3600):
            flash("Você já fez muitos pedidos na última hora. Se for urgente, fale com a TI por telefone.", "erro")
        elif form.get("enviar") == "1":
            limitador.registrar(f"pedidos:{usuario['id']}")
            try:
                anexo = svc.ler_anexo(request.files.get("anexo"))
                chamado_id = svc.criar_pedido(usuario, form, anexo, ip_cliente())
            except ErroValidacao as erro:
                erros = erro.erros
                flash(str(erro), "erro")
            except ErroNegocio as erro:
                flash(str(erro), "erro")
            else:
                return redirect(url_for("pedidos.enviado", chamado_id=chamado_id))
        passo = _passo_inicial(form, erros)
    return render_template("pedidos/novo.html", form=form, erros=erros, passo=passo, opcoes=opcoes,
                           pode_este=pode_este, listas=_listas(usuario, opcoes),
                           CATEGORIAS=svc.CATEGORIAS, COM_EQUIPAMENTO=svc.CATEGORIAS_COM_EQUIPAMENTO,
                           EXEMPLOS=svc.EXEMPLOS, IMPACTOS=svc.IMPACTOS)


@bp.get("/<int:chamado_id>/enviado")
@pedidos_obrigatorio
def enviado(chamado_id):
    chamado = _meu(chamado_id)
    return render_template("pedidos/enviado.html", chamado=chamado)


@bp.get("")
@pedidos_obrigatorio
def lista():
    return render_template("pedidos/lista.html", pedidos=svc.meus_pedidos(g.usuario))


@bp.get("/<int:chamado_id>")
@pedidos_obrigatorio
def detalhe(chamado_id):
    chamado = _meu(chamado_id)
    svc.marcar_lido_pelo_colaborador(chamado_id)
    return render_template("pedidos/detalhe.html", chamado=chamado, mensagens=svc.conversa(chamado_id, True),
                           pode_reabrir=svc.pode_reabrir(chamado),
                           dias_reabrir=cfg_int("chamados_fechar_resolvido_dias", 3))


@bp.get("/nao-lidas.json")
@pedidos_obrigatorio
def nao_lidas():
    """Número de respostas novas da TI, por pedido (o selo de "Meus pedidos")."""
    pedidos = svc.nao_lidas_do_colaborador(g.usuario)
    return jsonify(total=sum(pedidos.values()), pedidos={str(k): v for k, v in pedidos.items()})


@bp.get("/<int:chamado_id>/novidades.json")
@pedidos_obrigatorio
def novidades(chamado_id):
    """Atualização da conversa em tempo real (só o que é público: nunca a nota interna)."""
    chamado = _meu(chamado_id)
    novas = svc.conversa(chamado_id, True, depois=id_inteiro(request.args.get("depois")) or 0)
    if novas:
        svc.marcar_lido_pelo_colaborador(chamado_id, novas[-1]["id"])  # apareceu na tela: já foi vista
    html = render_template("pedidos/_novidades.html", mensagens=novas) if novas else ""
    return jsonify(html=html, ultima=novas[-1]["id"] if novas else None, situacao=chamado["situacao"],
                   respondeu=any(m["tipo"] == "resposta" for m in novas))


@bp.post("/<int:chamado_id>/responder")
@pedidos_obrigatorio
def responder(chamado_id):
    chamado = _meu(chamado_id)
    try:
        anexo = svc.ler_anexo(request.files.get("anexo"))
        svc.mensagem_colaborador(chamado, g.usuario, request.form, anexo)
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()) or str(erro), "erro")
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Pedido reaberto. A TI já foi avisada." if chamado["situacao"] == "resolvido"
              else "Mensagem enviada para a TI.", "sucesso")
    return redirect(url_for("pedidos.detalhe", chamado_id=chamado_id) + "#conversa")


@bp.get("/anexo/<int:anexo_id>")
@pedidos_obrigatorio
def anexo(anexo_id):
    registro = svc.obter_anexo(anexo_id)
    if not registro or not registro["mensagem_publico"]:
        abort(404)
    _meu(registro["chamado_id"])
    caminho = svc.caminho_anexo(registro)
    if not caminho:
        abort(404)
    return send_file(caminho, mimetype=registro["tipo"], download_name=registro["nome"],
                     as_attachment=not registro["tipo"].startswith("image/") or registro["tipo"] == "image/heic")


def contato_ti() -> str:
    return (cfg("chamados_contato_ti") or "").strip()
