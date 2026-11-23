"""Avisos dos chamados e tarefas automáticas.

- Teams (canal da unidade): chamado novo, lembretes de quem ainda não assumiu, prazo perto e vencido.
- Outlook: o colaborador recebe "recebido", "respondido" e "resolvido"; o técnico responsável recebe
  a resposta do colaborador; o administrador recebe as escaladas; toda a TI recebe o resumo das 8h.
- Tarefa de 1 em 1 minuto: lembretes, prazos, encerramento automático e cobertura de férias vencida.

Os avisos saem numa thread logo depois da ação (a tela de quem abriu não espera o Teams/Outlook).
"""
import threading
from datetime import timedelta

from flask import current_app, render_template, url_for

from ..db import consultar, escalar, executar, transacao
from ..util import agora, agora_bd, agora_local, de_bd, fmt_data, fmt_datahora, para_bd
from . import chamados as svc
from . import expediente, teams
from .configuracoes import cfg, cfg_bool, cfg_int, emails_notificacao, url_base
from .registro import auditar

LEMBRETE_COMUM_MIN = 30
LEMBRETE_URGENTE_MIN = 15


# ------------------------------------------------------------------ infraestrutura

def _depois(funcao, *args, **kwargs):
    app = current_app._get_current_object()
    if app.config.get("AVISOS_SINCRONOS") or app.config.get("TESTING"):
        funcao(*args, **kwargs)
        return
    base = url_base()

    def rodar():
        with app.app_context(), app.test_request_context("/", base_url=base):
            try:
                funcao(*args, **kwargs)
            except Exception:
                app.logger.exception("Falha ao enviar aviso de chamado")

    threading.Thread(target=rodar, name="avisos-chamados", daemon=True).start()


def link(endpoint: str, **valores) -> str:
    return url_base().rstrip("/") + url_for(endpoint, **valores)


def _email(destino: str | None, assunto: str, *, finalidade: str = "chamado", modelo: str = "emails/chamado.html",
           **contexto):
    if not destino:
        return None
    from .email import enviar
    contexto.setdefault("para_ti", finalidade != "chamado")
    html = render_template(modelo, assunto=assunto, **contexto)
    return enviar(destino, assunto, html, finalidade=finalidade)


def emails_admins() -> list[str]:
    lista = [l["email"] for l in consultar("SELECT email FROM usuarios WHERE perfil = 'admin' AND ativo = 1 "
                                           "AND email IS NOT NULL AND email <> ''")]
    return sorted(set(lista)) or emails_notificacao()


def _fatos(c: dict) -> list:
    solicitante = c["solicitante_nome"] + (f" ({c['solicitante_setor']})" if c.get("solicitante_setor") else "")
    return [("Solicitante", solicitante), ("Unidade", c["unidade_nome"]),
            ("Categoria", svc.CATEGORIAS[c["categoria"]][0]), ("Prioridade", svc.PRIORIDADES[c["prioridade"]]),
            ("Prazo", fmt_datahora(c["prazo_em"]))]


def _cartao_chamado(c: dict, titulo: str, alerta: bool = False) -> dict:
    if c["categoria"] == "seguranca":
        return teams.cartao(titulo, texto="Segurança da informação. Os detalhes ficam só no sistema "
                                          "(veem apenas a TI da unidade e o administrador).",
                            fatos=[f for f in _fatos(c) if f[0] != "Categoria"],
                            link=link("chamados.detalhe", chamado_id=c["id"]), alerta=True)
    return teams.cartao(titulo, texto=c["assunto"], fatos=_fatos(c), link=link("chamados.detalhe", chamado_id=c["id"]),
                        alerta=alerta)


def _teams(chamado: dict, mensagem: dict, *, contar_aviso: bool = False) -> int:
    with transacao():
        canais = teams.enfileirar(chamado["unidade_id"], mensagem, chamado["id"])
        if contar_aviso:
            executar("UPDATE chamados SET avisos_enviados = avisos_enviados + 1, ultimo_aviso_em = ? WHERE id = ?",
                     (agora_bd(), chamado["id"]))
    if canais:
        teams.enviar_pendentes()
    return canais


def _n(c) -> str:
    return svc.numero(c["id"])


# ------------------------------------------------------------------ eventos do chamado

def ao_abrir(chamado_id: int, aberto_pela_ti: bool = False):
    _depois(_ao_abrir, chamado_id, aberto_pela_ti)


def _ao_abrir(chamado_id: int, aberto_pela_ti: bool):
    c = svc.obter(chamado_id)
    seguranca = c["categoria"] == "seguranca"
    if not (aberto_pela_ti and c["responsavel_id"]):
        titulo = f"Chamado de segurança da informação nº {_n(c)}" if seguranca else f"Novo chamado nº {_n(c)}"
        _teams(c, _cartao_chamado(c, titulo), contar_aviso=True)
    if seguranca:
        for destino in emails_admins():
            _email(destino, f"Chamado de segurança da informação nº {_n(c)} – {c['unidade_nome']}",
                   finalidade="chamado_ti", titulo=f"Novo chamado de segurança da informação nº {_n(c)}",
                   paragrafos=[f"{c['solicitante_nome']} ({c['unidade_nome']}) abriu um pedido em “E-mail ou site "
                               "estranho”. Os detalhes ficam só no sistema.",
                               "Se houver dado pessoal envolvido, o encarregado (DPO) tem 3 dias úteis para decidir "
                               "sobre a comunicação à ANPD."],
                   botao={"texto": "Abrir chamado", "url": link("chamados.detalhe", chamado_id=c["id"])})
    if c["solicitante_email"]:
        paragrafos = ["A TI vai responder por aqui e em “Meus pedidos”."]
        if seguranca:
            paragrafos.insert(0, "Não clique em mais nada naquele e-mail ou site e não apague a mensagem: a TI pode "
                                 "precisar dela.")
        _email(c["solicitante_email"], f"Recebemos o seu pedido nº {_n(c)}",
               titulo=("A TI registrou o seu pedido" if aberto_pela_ti else "Recebemos o seu pedido") + f" nº {_n(c)}",
               paragrafos=paragrafos, citacao=c["assunto"],
               botao={"texto": "Ver meus pedidos", "url": link("pedidos.detalhe", chamado_id=c["id"])})


def ao_assumir(chamado_id: int, nome: str):
    _depois(_ao_assumir, chamado_id, nome)


def _ao_assumir(chamado_id: int, nome: str):
    c = svc.obter(chamado_id)
    if c["avisos_enviados"]:
        _teams(c, teams.aviso_curto(f"Chamado nº {_n(c)} assumido por {nome}.",
                                    link("chamados.detalhe", chamado_id=c["id"])))


def ao_responder_ti(chamado_id: int, mensagem_id: int):
    _depois(_ao_responder_ti, chamado_id, mensagem_id)


def _ao_responder_ti(chamado_id: int, mensagem_id: int):
    c = svc.obter(chamado_id)
    texto = escalar("SELECT texto FROM chamados_mensagens WHERE id = ?", (mensagem_id,))
    if not c["solicitante_email"]:
        return
    if c["situacao"] == "aguardando":
        assunto = f"A TI precisa de uma resposta sua – pedido nº {_n(c)}"
        titulo, botao = "A TI precisa de uma resposta sua", "Responder"
    else:
        assunto = f"A TI respondeu o seu pedido nº {_n(c)}"
        titulo, botao = "A TI respondeu o seu pedido", "Ver o pedido"
    _email(c["solicitante_email"], assunto, titulo=f"{titulo} (nº {_n(c)})",
           paragrafos=[f"Pedido: {c['assunto']}"], citacao=texto,
           botao={"texto": botao, "url": link("pedidos.detalhe", chamado_id=c["id"])})


def ao_responder_colaborador(chamado_id: int, mensagem_id: int | None = None, reaberto: bool = False):
    _depois(_ao_responder_colaborador, chamado_id, mensagem_id, reaberto)


def _ao_responder_colaborador(chamado_id: int, mensagem_id, reaberto: bool):
    c = svc.obter(chamado_id)
    texto = escalar("SELECT texto FROM chamados_mensagens WHERE id = ?", (mensagem_id,)) if mensagem_id else None
    acao = "reabriu o pedido (ainda com problema)" if reaberto else "respondeu"
    responsavel = consultar("SELECT nome, email FROM usuarios WHERE id = ? AND ativo = 1", (c["responsavel_id"],),
                            um=True) if c["responsavel_id"] else None
    if responsavel and responsavel["email"]:
        _email(responsavel["email"], f"{c['solicitante_nome']} {acao} – chamado nº {_n(c)}", finalidade="chamado_ti",
               titulo=f"{c['solicitante_nome']} {acao}", paragrafos=[f"Chamado nº {_n(c)}: {c['assunto']}"],
               citacao=texto, botao={"texto": "Abrir chamado", "url": link("chamados.detalhe", chamado_id=c["id"])})
    else:
        _teams(c, teams.aviso_curto(f"{c['solicitante_nome']} {acao} no chamado nº {_n(c)} (sem responsável).",
                                    link("chamados.detalhe", chamado_id=c["id"])))


def ao_resolver(chamado_id: int):
    _depois(_ao_resolver, chamado_id)


def _ao_resolver(chamado_id: int):
    c = svc.obter(chamado_id)
    if not c["solicitante_email"]:
        return
    dias = cfg_int("chamados_fechar_resolvido_dias", 3)
    _email(c["solicitante_email"], f"Seu pedido nº {_n(c)} foi resolvido",
           titulo=f"Seu pedido nº {_n(c)} foi resolvido", citacao=c["assunto"],
           paragrafos=["Se estiver tudo certo, não precisa fazer nada: o pedido será encerrado sozinho em "
                       f"{dias} {'dia útil' if dias == 1 else 'dias úteis'}.",
                       "Ainda com problema? Use o botão abaixo e conte o que continua acontecendo."],
           botao={"texto": "Ainda com problema? Avise a TI", "url": link("pedidos.detalhe", chamado_id=c["id"])})


def ao_atribuir(chamado_id: int, responsavel: dict, por_nome: str):
    _depois(_ao_atribuir, chamado_id, responsavel, por_nome)


def _ao_atribuir(chamado_id: int, responsavel: dict, por_nome: str):
    c = svc.obter(chamado_id)
    if responsavel.get("email"):
        _email(responsavel["email"], f"{por_nome} passou o chamado nº {_n(c)} para você", finalidade="chamado_ti",
               titulo=f"Chamado nº {_n(c)} agora é seu", paragrafos=[f"{por_nome} colocou você como responsável."],
               citacao=c["assunto"], fatos=_fatos(c),
               botao={"texto": "Abrir chamado", "url": link("chamados.detalhe", chamado_id=c["id"])})


def ao_transferir(chamado_id: int, origem: str, por_nome: str):
    _depois(_ao_transferir, chamado_id, origem, por_nome)


def _ao_transferir(chamado_id: int, origem: str, por_nome: str):
    c = svc.obter(chamado_id)
    _teams(c, _cartao_chamado(c, f"Chamado nº {_n(c)} transferido de {origem} por {por_nome}"), contar_aviso=True)


def ao_dados_pessoais(chamado_id: int, por_nome: str):
    _depois(_ao_dados_pessoais, chamado_id, por_nome)


def _ao_dados_pessoais(chamado_id: int, por_nome: str):
    c = svc.obter(chamado_id)
    prazo = fmt_data(svc.prazo_anpd(c))
    destinos = set(emails_admins())
    if cfg("chamados_encarregado_email"):
        destinos.add(cfg("chamados_encarregado_email").strip())
    for destino in sorted(destinos):
        _email(destino, f"Incidente com possível dado pessoal – chamado nº {_n(c)}", finalidade="chamado_ti",
               titulo="Incidente de segurança com possível dado pessoal",
               paragrafos=[f"{por_nome} indicou que o chamado nº {_n(c)} ({c['unidade_nome']}) envolve dado pessoal.",
                           f"Pela LGPD (Resolução CD/ANPD nº 15/2024), se houver risco ou dano relevante aos titulares, "
                           f"a comunicação à ANPD deve ser feita em até 3 dias úteis: até {prazo}. Quem decide é o "
                           "encarregado (DPO).",
                           "A TI pode enviar o registro completo do incidente pelo próprio chamado."],
               botao={"texto": "Abrir chamado", "url": link("chamados.detalhe", chamado_id=c["id"])})


def registro_ao_encarregado(chamado_id: int, destino: str):
    """Registro completo do incidente para o DPO (envio na hora, com o resultado para a tela)."""
    c = svc.obter(chamado_id)
    from .email import enviar
    html = render_template("emails/chamado_registro.html", c=c, mensagens=svc.conversa(chamado_id),
                           prazo_anpd=svc.prazo_anpd(c), CATEGORIAS=svc.CATEGORIAS, PRIORIDADES=svc.PRIORIDADES,
                           SITUACOES=svc.SITUACOES)
    return enviar(destino, f"Registro de incidente de segurança – chamado nº {_n(c)}", html, finalidade="chamado_dpo")


# ------------------------------------------------------------------ tarefa de 1 em 1 minuto

def job_chamados() -> str:
    exp = expediente.atual()
    momento = agora()
    partes = [_lembretes(exp, momento), _prazos(exp, momento), _encerrar(exp, momento)]
    from .usuarios import expirar_coberturas
    partes.append(expirar_coberturas())
    resultado = teams.enviar_pendentes()
    if not resultado.startswith("0 aviso(s) no Teams enviados, 0"):
        partes.append(resultado)
    return " ".join(p for p in partes if p) or "Nada a fazer."


def _lembretes(exp, momento) -> str:
    enviados = 0
    for c in consultar(f"SELECT {svc.COLUNAS} {svc.JUNCOES} WHERE c.situacao = 'novo' AND c.responsavel_id IS NULL "
                       "AND c.avisos_enviados >= 1 AND c.ultimo_aviso_em IS NOT NULL"):
        seguranca = c["categoria"] == "seguranca"
        intervalo = LEMBRETE_URGENTE_MIN if seguranca or c["prioridade"] == "alta" else LEMBRETE_COMUM_MIN
        if exp.minutos_entre(de_bd(c["ultimo_aviso_em"]), momento) < intervalo:
            continue
        aberto = expediente.texto_duracao(exp.minutos_entre(de_bd(c["criado_em"]), momento))
        if seguranca or c["avisos_enviados"] == 1:
            _teams(c, _cartao_chamado(c, f"Lembrete: ninguém assumiu o chamado nº {_n(c)} (aberto há {aberto} úteis)",
                                      alerta=True), contar_aviso=True)
        elif c["avisos_enviados"] == 2:
            for destino in emails_admins():
                _email(destino, f"Ninguém assumiu o chamado nº {_n(c)} – {c['unidade_nome']}", finalidade="chamado_ti",
                       titulo=f"Ninguém assumiu o chamado nº {_n(c)}",
                       paragrafos=[f"O chamado está há {aberto} úteis sem responsável, mesmo depois do lembrete no Teams."],
                       citacao=c["assunto"], fatos=_fatos(c),
                       botao={"texto": "Abrir chamado", "url": link("chamados.detalhe", chamado_id=c["id"])})
            _teams(c, teams.aviso_curto(f"Chamado nº {_n(c)} continua sem responsável: o administrador foi avisado.",
                                        link("chamados.detalhe", chamado_id=c["id"])), contar_aviso=True)
        else:
            continue
        enviados += 1
    return f"{enviados} lembrete(s)." if enviados else ""


def _prazos(exp, momento) -> str:
    avisos = 0
    limite_busca = para_bd(momento + timedelta(days=5))
    for c in consultar(f"SELECT {svc.COLUNAS} {svc.JUNCOES} WHERE c.situacao IN ('novo', 'atendimento') "
                       "AND (c.aviso_prazo IS NULL OR c.aviso_prazo = 'perto') AND c.prazo_em <= ?", (limite_busca,)):
        prazo = de_bd(c["prazo_em"])
        responsavel = consultar("SELECT email FROM usuarios WHERE id = ? AND ativo = 1", (c["responsavel_id"],),
                                um=True) if c["responsavel_id"] else None
        url = link("chamados.detalhe", chamado_id=c["id"])
        if prazo <= momento:
            _teams(c, _cartao_chamado(c, f"Chamado nº {_n(c)} venceu o prazo", alerta=True))
            destinos = set(emails_admins()) | ({responsavel["email"]} if responsavel and responsavel["email"] else set())
            for destino in sorted(destinos):
                _email(destino, f"Chamado nº {_n(c)} venceu o prazo – {c['unidade_nome']}", finalidade="chamado_ti",
                       titulo=f"Chamado nº {_n(c)} venceu o prazo", citacao=c["assunto"], fatos=_fatos(c),
                       paragrafos=[f"Responsável: {c['responsavel_nome'] or 'ninguém'}."],
                       botao={"texto": "Abrir chamado", "url": url})
            executar("UPDATE chamados SET aviso_prazo = 'vencido' WHERE id = ?", (c["id"],))
            avisos += 1
        elif c["aviso_prazo"] is None:
            restante = exp.minutos_entre(momento, prazo)
            if restante > max(30, svc.sla_minutos(c["prioridade"], exp) // 4):
                continue
            texto = f"Chamado nº {_n(c)} vence em {expediente.texto_duracao(restante)} úteis ({fmt_datahora(c['prazo_em'])})."
            _teams(c, teams.aviso_curto(texto + (f" Responsável: {c['responsavel_nome']}." if c["responsavel_nome"]
                                                 else " Ainda sem responsável."), url))
            if responsavel and responsavel["email"]:
                _email(responsavel["email"], f"Chamado nº {_n(c)} vence em breve", finalidade="chamado_ti",
                       titulo=texto, citacao=c["assunto"], botao={"texto": "Abrir chamado", "url": url})
            executar("UPDATE chamados SET aviso_prazo = 'perto' WHERE id = ?", (c["id"],))
            avisos += 1
    return f"{avisos} aviso(s) de prazo." if avisos else ""


def _fechar(c: dict, motivo: str):
    with transacao():
        executar("UPDATE chamados SET situacao = 'fechado', fechado_em = ?, atualizado_em = ? WHERE id = ? "
                 "AND situacao = ?", (agora_bd(), agora_bd(), c["id"], c["situacao"]))
        executar("INSERT INTO chamados_mensagens (chamado_id, tipo, texto, publico) VALUES (?, 'evento', ?, 1)",
                 (c["id"], motivo))
        auditar("chamado_fechado", "chamado", c["id"], {"motivo": motivo, "de": c["situacao"]})


def _encerrar(exp, momento) -> str:
    fechados = 0
    dias_resolvido = cfg_int("chamados_fechar_resolvido_dias", 3) or 3
    dias_aguardando = cfg_int("chamados_fechar_aguardando_dias", 5) or 5
    for c in consultar(f"SELECT {svc.COLUNAS} {svc.JUNCOES} WHERE c.situacao = 'resolvido' AND c.resolvido_em <= ?",
                       (para_bd(momento - timedelta(hours=1)),)):
        if exp.somar_dias(de_bd(c["resolvido_em"]), dias_resolvido) <= momento:
            _fechar(c, f"Pedido encerrado: resolvido há {dias_resolvido} "
                       f"{'dia útil' if dias_resolvido == 1 else 'dias úteis'}.")
            fechados += 1
    for c in consultar(f"SELECT {svc.COLUNAS} {svc.JUNCOES} WHERE c.situacao = 'aguardando' AND c.pausado_em <= ?",
                       (para_bd(momento - timedelta(hours=1)),)):
        if exp.somar_dias(de_bd(c["pausado_em"]), dias_aguardando) <= momento:
            _fechar(c, f"Pedido encerrado: sem resposta em {dias_aguardando} "
                       f"{'dia útil' if dias_aguardando == 1 else 'dias úteis'}.")
            fechados += 1
            if c["solicitante_email"]:
                _email(c["solicitante_email"], f"Encerramos o seu pedido nº {_n(c)}",
                       titulo=f"Encerramos o seu pedido nº {_n(c)}", citacao=c["assunto"],
                       paragrafos=[f"A TI pediu uma resposta sua e não recebeu nada em {dias_aguardando} dias úteis, "
                                   "por isso o pedido foi encerrado.",
                                   "Se ainda precisar de ajuda, é só fazer um novo pedido."],
                       botao={"texto": "Fazer novo pedido", "url": link("pedidos.novo")})
    return f"{fechados} chamado(s) encerrado(s)." if fechados else ""


# ------------------------------------------------------------------ resumo diário

def job_resumo() -> str:
    if not cfg_bool("chamados_resumo_ativo"):
        return "Resumo dos chamados desligado."
    exp = expediente.atual()
    if not exp.eh_dia_util(agora_local().date()):
        return "Hoje não é dia útil: resumo não enviado."
    enviados = 0
    for pessoa in consultar("SELECT id, nome, email, perfil FROM usuarios WHERE ativo = 1 AND perfil IN "
                            "('admin', 'tecnico') AND email IS NOT NULL AND email <> ''"):
        if pessoa["perfil"] == "tecnico":
            pessoa["unidades"] = {l["unidade_id"] for l in consultar(
                "SELECT unidade_id FROM usuarios_unidades WHERE usuario_id = ? AND (expira_em IS NULL OR expira_em > ?)",
                (pessoa["id"], agora_bd()))}
            if not pessoa["unidades"]:
                continue
        unidades = svc.unidades_atendidas(pessoa)
        filtros = {"situacao": "abertos", "unidades": unidades}
        abertos = svc.listar_fila(pessoa, filtros, 1, 200)
        if not abertos.total:
            continue
        atrasados = [c for c in abertos.itens if c["prazo"]["estado"] == "atrasado"]
        sem_responsavel = [c for c in abertos.itens if not c["responsavel_id"]]
        assunto = (f"Chamados de hoje: {abertos.total} aberto(s), {len(atrasados)} atrasado(s), "
                   f"{len(sem_responsavel)} sem responsável")
        _email(pessoa["email"], assunto, finalidade="chamados_resumo", modelo="emails/chamados_resumo.html",
               pessoa=pessoa, total=abertos.total, atrasados=atrasados[:15], sem_responsavel=sem_responsavel[:15],
               link_fila=link("chamados.fila"), link_chamado=lambda i: link("chamados.detalhe", chamado_id=i),
               numero=svc.numero)
        enviados += 1
    return f"Resumo dos chamados enviado para {enviados} pessoa(s)."
