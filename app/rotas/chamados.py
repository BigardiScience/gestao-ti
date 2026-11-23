"""Chamados — telas da equipe de TI: fila, atendimento, abertura em nome de alguém e transferência."""
from flask import Blueprint, Response, abort, flash, g, jsonify, redirect, render_template, request, send_file, url_for

from ..db import consultar
from ..seguranca import chamados_obrigatorio, ve_dados_de_rede
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import chamados as svc
from ..servicos import expediente
from ..servicos import relatorio_chamados as rel
from ..servicos.registro import auditar
from ..util import numero_pagina, valor_datetime_local
from . import id_inteiro

bp = Blueprint("chamados", __name__, url_prefix="/chamados")


def _chamado_visivel(chamado_id: int) -> dict:
    chamado = svc.obter(chamado_id)
    if not chamado or not svc.pode_ver(g.usuario, chamado):
        abort(404)
    return chamado


def _executar(acao, chamado, *args, sucesso: str | None = None, ancora: str = ""):
    try:
        acao(chamado, g.usuario, *args)
    except svc.SemPermissao as erro:
        abort(403, description=str(erro))
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()) or str(erro), "erro")
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        if sucesso:
            flash(sucesso, "sucesso")
    return redirect(url_for("chamados.detalhe", chamado_id=chamado["id"]) + ancora)


def _unidade_padrao(unidades: list) -> int | None:
    """O técnico abre a fila na unidade dele (a do AD, se ele a atende; senão a primeira que atende)."""
    atendidas = svc.unidades_atendidas(g.usuario)
    if atendidas is None:
        return None
    if g.usuario.get("unidade_id") in atendidas:
        return g.usuario["unidade_id"]
    ids = [u["id"] for u in unidades if u["id"] in atendidas]
    return ids[0] if ids else None


@bp.get("")
@chamados_obrigatorio
def fila():
    unidades = consultar("SELECT id, nome FROM unidades WHERE ativo = 1 ORDER BY nome COLLATE NOCASE")
    # a unidade da pessoa vem primeiro nas abas (Sorocaba, Uberaba, Nordeste, Santos para quem é de Sorocaba)
    unidades.sort(key=lambda u: u["id"] != g.usuario.get("unidade_id"))
    abertos = svc.abertos_por_unidade(g.usuario)
    for u in unidades:
        u["total"] = abertos.get(u["id"], 0)
    escolha = request.args.get("unidade")
    if escolha == "todas":
        unidade = None
    elif escolha and id_inteiro(escolha) in {u["id"] for u in unidades}:
        unidade = id_inteiro(escolha)
    else:
        unidade = _unidade_padrao(unidades)
    filtros = {
        "unidade": unidade,
        "situacao": request.args.get("situacao") if request.args.get("situacao") in svc.FILTROS_SITUACAO else "abertos",
        "q": (request.args.get("q") or "").strip()[:80],
        "meus": request.args.get("meus") == "1",
        "sem_responsavel": request.args.get("sem_responsavel") == "1",
        "respostas": request.args.get("respostas") == "1",
        "categoria": request.args.get("categoria") if request.args.get("categoria") in svc.CATEGORIAS else None,
        "prioridade": request.args.get("prioridade") if request.args.get("prioridade") in svc.PRIORIDADES else None,
        "solicitante": id_inteiro(request.args.get("solicitante")),
    }
    pagina = svc.listar_fila(g.usuario, filtros, numero_pagina())
    return render_template("chamados/fila.html", pagina=pagina, filtros=filtros, unidades=unidades, unidade=unidade,
                           numeros=dict(svc.indicadores(g.usuario, unidade), respostas=svc.contador(g.usuario)["respostas"]),
                           total_abertos=sum(abertos.values()),
                           atendidas=svc.unidades_atendidas(g.usuario),
                           solicitante=consultar("SELECT id, nome FROM usuarios WHERE id = ?",
                                                 (filtros["solicitante"],), um=True) if filtros["solicitante"] else None)


def _filtros_relatorio():
    ano, mes = rel.ler_mes(request.args.get("mes"))
    unidades = consultar("SELECT id, nome FROM unidades WHERE ativo = 1 ORDER BY nome COLLATE NOCASE")
    unidade = id_inteiro(request.args.get("unidade"))
    unidade = unidade if unidade in {u["id"] for u in unidades} else None
    return ano, mes, unidades, unidade


@bp.get("/relatorio")
@chamados_obrigatorio
def relatorio():
    """Relatório do mês: resolvidos dentro e fora do prazo (auditoria e planilha mensal)."""
    ano, mes, unidades, unidade = _filtros_relatorio()
    return render_template("chamados/relatorio.html", dados=rel.gerar(g.usuario, ano, mes, unidade),
                           unidades=unidades, unidade=unidade, mes_valor=f"{ano}-{mes:02d}")


@bp.get("/relatorio.xlsx")
@chamados_obrigatorio
def relatorio_planilha():
    ano, mes, unidades, unidade = _filtros_relatorio()
    dados = rel.gerar(g.usuario, ano, mes, unidade)
    nome_unidade = next((u["nome"] for u in unidades if u["id"] == unidade), "Todas")
    conteudo, nome = rel.planilha(dados, nome_unidade, g.usuario["nome"])
    auditar("relatorio_exportado", "relatorio", None, {"relatorio": "chamados_mensal", "mes": f"{ano}-{mes:02d}",
                                                         "unidade": unidade, "registros": dados["total"]["resolvidos"]})
    return Response(conteudo, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{nome}"'})


@bp.get("/contador.json")
@chamados_obrigatorio
def contador():
    return jsonify(svc.contador(g.usuario))


@bp.route("/novo", methods=["GET", "POST"])
@chamados_obrigatorio
def novo():
    atendidas = svc.unidades_atendidas(g.usuario)
    unidades = [u for u in consultar("SELECT id, nome FROM unidades WHERE ativo = 1 ORDER BY nome COLLATE NOCASE")
                if atendidas is None or u["id"] in atendidas]
    if not unidades:
        abort(403, description="Você não atende nenhuma unidade, por isso não pode abrir chamados em nome de alguém.")
    erros = {}
    padrao = g.usuario.get("unidade_id") if any(u["id"] == g.usuario.get("unidade_id") for u in unidades) \
        else unidades[0]["id"]
    form = {"unidade_id": str(padrao), "canal": "telefone", "prioridade": "media", "assumir": "1"}
    if request.method == "POST":
        form = request.form
        try:
            anexo = svc.ler_anexo(request.files.get("anexo"))
            chamado_id = svc.criar_por_ti(g.usuario, form, anexo)
        except svc.SemPermissao as erro:
            abort(403, description=str(erro))
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash(f"Chamado nº {svc.numero(chamado_id)} aberto.", "sucesso")
            return redirect(url_for("chamados.detalhe", chamado_id=chamado_id))
    computador = consultar("SELECT id, nome, usuario_nome FROM computadores WHERE id = ?",
                           (id_inteiro(form.get("computador_id")),), um=True) if form.get("computador_id") else None
    return render_template("chamados/novo.html", form=form, erros=erros, unidades=unidades,
                           pessoas=svc.pessoas_conhecidas(atendidas), computador=computador,
                           CATEGORIAS=svc.CATEGORIAS, PRIORIDADES=svc.PRIORIDADES, CANAIS=svc.CANAIS)


@bp.get("/computadores.json")
@chamados_obrigatorio
def buscar_computadores():
    """Para o campo "Equipamento" do chamado aberto pela TI (mesmo formato do seletor de aparelhos)."""
    from ..db import condicao_busca
    condicao, parametros = condicao_busca(request.args.get("q", "")[:60],
                                          "c.nome || ' ' || COALESCE(c.usuario_nome, '') || ' ' || COALESCE(c.ip, '')")
    itens = consultar(f"SELECT c.id, c.nome, c.usuario_nome, c.setor, u.nome AS unidade FROM computadores c "
                      f"JOIN unidades u ON u.id = c.unidade_id WHERE c.situacao <> 'desativado'{condicao} "
                      f"ORDER BY c.nome LIMIT 20", parametros)
    return jsonify([{"id": i["id"], "titulo": i["nome"],
                     "detalhe": " · ".join(p for p in (i["usuario_nome"], i["setor"], i["unidade"]) if p)}
                    for i in itens])


@bp.get("/<int:chamado_id>")
@chamados_obrigatorio
def detalhe(chamado_id):
    chamado = _chamado_visivel(chamado_id)
    svc.registrar_leitura(chamado, g.usuario)
    resposta_nova = bool(chamado.get("resposta_em"))
    svc.marcar_resposta_vista(chamado, g.usuario)
    pode = svc.pode_atender(g.usuario, chamado)
    exp = expediente.atual()
    return render_template(
        "chamados/detalhe.html", chamado=chamado, mensagens=svc.conversa(chamado_id), pode_atender=pode,
        prazo=svc.situacao_do_prazo(chamado, exp), equipamento=svc.equipamento(chamado),
        pedidos_90=svc.pedidos_recentes(chamado["solicitante_id"]),
        tecnicos=svc.tecnicos_da_unidade(chamado["unidade_id"]) if pode else [],
        unidades=consultar("SELECT id, nome FROM unidades WHERE ativo = 1 AND id <> ? ORDER BY nome",
                           (chamado["unidade_id"],)) if pode else [],
        rede=ve_dados_de_rede(), prazo_anpd=svc.prazo_anpd(chamado) if chamado["categoria"] == "seguranca" else None,
        conhecido_em=valor_datetime_local(chamado["seg_conhecido_em"]) if chamado["seg_conhecido_em"] else "",
        situacoes=svc.situacoes_possiveis(chamado), resposta_nova=resposta_nova,
        CATEGORIAS=svc.CATEGORIAS, PRIORIDADES=svc.PRIORIDADES, SITUACOES=svc.SITUACOES,
        IMPACTOS=svc.IMPACTOS)


@bp.get("/<int:chamado_id>/novidades.json")
@chamados_obrigatorio
def novidades(chamado_id):
    """Atualização da conversa em tempo real: mensagens depois da última que a tela já mostra."""
    chamado = _chamado_visivel(chamado_id)
    novas = svc.conversa(chamado_id, depois=id_inteiro(request.args.get("depois")) or 0)
    respondeu = [m for m in novas if m["tipo"] == "mensagem" and m["autor_id"] == chamado["solicitante_id"]]
    if respondeu:
        svc.marcar_resposta_vista(chamado, g.usuario)  # apareceu na tela de quem atende: já foi vista
    html = render_template("chamados/_novidades.html", mensagens=novas, chamado=chamado, equipamento={},
                           IMPACTOS=svc.IMPACTOS) if novas else ""
    return jsonify(html=html, ultima=novas[-1]["id"] if novas else None, situacao=chamado["situacao"],
                   respondeu=bool(respondeu), nome=chamado["solicitante_nome"], numero=svc.numero(chamado_id),
                   resposta_em=chamado.get("resposta_em") or "")


@bp.post("/<int:chamado_id>/assumir")
@chamados_obrigatorio
def assumir(chamado_id):
    return _executar(svc.assumir, _chamado_visivel(chamado_id), sucesso="Chamado assumido.")


@bp.post("/<int:chamado_id>/mensagem")
@chamados_obrigatorio
def mensagem(chamado_id):
    chamado = _chamado_visivel(chamado_id)
    try:
        anexo = svc.ler_anexo(request.files.get("anexo"))
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()), "erro")
        return redirect(url_for("chamados.detalhe", chamado_id=chamado_id) + "#responder")
    if request.form.get("tipo") == "nota":
        return _executar(svc.nota, chamado, request.form, anexo, sucesso="Nota interna registrada.", ancora="#conversa")
    aguardar = request.form.get("aguardar") in ("1", "on")
    return _executar(svc.responder, chamado, request.form, anexo, ancora="#conversa",
                     sucesso="Resposta enviada. O chamado está aguardando o usuário." if aguardar
                     else "Resposta enviada ao colaborador.")


@bp.post("/<int:chamado_id>/resolver")
@chamados_obrigatorio
def resolver(chamado_id):
    return _executar(svc.resolver, _chamado_visivel(chamado_id), sucesso="Chamado marcado como resolvido.")


@bp.post("/<int:chamado_id>/reabrir")
@chamados_obrigatorio
def reabrir(chamado_id):
    return _executar(svc.reabrir, _chamado_visivel(chamado_id), sucesso="Chamado reaberto.")


@bp.post("/<int:chamado_id>/alterar")
@chamados_obrigatorio
def alterar(chamado_id):
    return _executar(svc.alterar, _chamado_visivel(chamado_id), request.form, sucesso="Atendimento atualizado.")


@bp.route("/<int:chamado_id>/transferir", methods=["GET", "POST"])
@chamados_obrigatorio
def transferir(chamado_id):
    chamado = _chamado_visivel(chamado_id)
    if not svc.pode_atender(g.usuario, chamado):
        abort(403, description="Só a equipe que atende a unidade pode transferir este chamado.")
    erros, form = {}, {}
    if request.method == "POST":
        form = request.form
        try:
            svc.transferir(chamado, g.usuario, form)
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            destino = consultar("SELECT nome FROM unidades WHERE id = ?", (id_inteiro(form.get("unidade_id")),), um=True)
            flash(f"Chamado transferido para {destino['nome']}. A equipe de lá foi avisada.", "sucesso")
            novo = svc.obter(chamado_id)
            if svc.pode_ver(g.usuario, novo):
                return redirect(url_for("chamados.detalhe", chamado_id=chamado_id))
            return redirect(url_for("chamados.fila"))
    unidades = consultar("SELECT id, nome FROM unidades WHERE ativo = 1 AND id <> ? ORDER BY nome COLLATE NOCASE",
                         (chamado["unidade_id"],))
    return render_template("chamados/transferir.html", chamado=chamado, unidades=unidades, form=form, erros=erros)


@bp.post("/<int:chamado_id>/seguranca")
@chamados_obrigatorio
def seguranca(chamado_id):
    return _executar(svc.salvar_seguranca, _chamado_visivel(chamado_id), request.form,
                     sucesso="Registro do incidente salvo.", ancora="#incidente")


@bp.post("/<int:chamado_id>/encarregado")
@chamados_obrigatorio
def encarregado(chamado_id):
    return _executar(svc.enviar_ao_encarregado, _chamado_visivel(chamado_id),
                     sucesso="Registro completo enviado ao encarregado (DPO).", ancora="#incidente")


@bp.get("/anexo/<int:anexo_id>")
@chamados_obrigatorio
def anexo(anexo_id):
    registro = svc.obter_anexo(anexo_id)
    if not registro:
        abort(404)
    chamado = _chamado_visivel(registro["chamado_id"])
    caminho = svc.caminho_anexo(registro)
    if not caminho:
        abort(404)
    if chamado["categoria"] == "seguranca":
        from ..servicos.registro import auditar
        auditar("chamado_anexo_aberto", "chamado", chamado["id"], {"anexo": registro["nome"]})
    return send_file(caminho, mimetype=registro["tipo"], download_name=registro["nome"],
                     as_attachment=not registro["tipo"].startswith("image/") or registro["tipo"] == "image/heic")
