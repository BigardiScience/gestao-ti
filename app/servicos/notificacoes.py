"""Alertas de pendências (painel e e-mail diário) e envio automático de lembretes."""
import time
from datetime import timedelta

from flask import current_app, render_template, url_for

from ..db import consultar, escalar
from ..seguranca import eh_admin, filtro_unidades
from ..util import agora, agora_bd, agora_local, para_bd, plural
from . import termos as svc_termos
from .configuracoes import cfg, cfg_bool, cfg_int, emails_notificacao, nome_empresa, url_base


def alertas(incluir_admin: bool | None = None) -> list[dict]:
    """Pendências que pedem ação. Cada item: nível, título, descrição, quantidade, link e ação."""
    if incluir_admin is None:
        incluir_admin = eh_admin()
    restricao, p = filtro_unidades("a.unidade_id")
    agora_txt = agora_bd()
    itens = []

    def adicionar(chave, nivel, quantidade, titulo, descricao, url, acao):
        if quantidade:
            itens.append({"chave": chave, "nivel": nivel, "quantidade": quantidade, "titulo": titulo,
                          "descricao": descricao, "url": url, "acao": acao})

    if incluir_admin and cfg("email_modo") != "graph":
        itens.append({"chave": "simulacao", "nivel": "aviso", "quantidade": None,
                      "titulo": "E-mails em modo de simulação",
                      "descricao": "Nenhum e-mail sai do sistema: as mensagens ficam só no histórico. Conecte o "
                                   "Outlook para enviar os termos de verdade.",
                      "url": url_for("configuracoes.integracao"), "acao": "Configurar integração"})
    if incluir_admin and cfg("email_modo") == "graph" and cfg("graph_status") == "erro":
        itens.append({"chave": "integracao", "nivel": "critico", "quantidade": None,
                      "titulo": "Falha na integração com o Outlook",
                      "descricao": cfg("graph_status_mensagem") or "O último teste de conexão falhou.",
                      "url": url_for("configuracoes.integracao"), "acao": "Ver integração"})

    sem_termo = len(svc_termos.vinculos_sem_termo())
    adicionar("sem_termo", "critico", sem_termo, f"{plural(sem_termo, 'entrega')} sem termo de responsabilidade",
              "Aparelhos em uso sem termo emitido (por exemplo, vindos da planilha antiga).",
              url_for("termos.lista", status="sem_termo"), "Emitir termos")

    gerados = escalar(f"SELECT COUNT(*) FROM termos t JOIN aparelhos a ON a.id = t.aparelho_id "
                      f"WHERE t.status = 'gerado' {restricao}", p)
    adicionar("gerados", "aviso", gerados, f"{plural(gerados, 'termo')} aguardando envio",
              "Termos gerados que ainda não foram enviados ao colaborador.",
              url_for("termos.lista", status="gerado"), "Revisar e enviar")

    aguardando = consultar(
        f"""SELECT COUNT(*) AS n, MIN(t.enviado_em) AS mais_antigo,
                   SUM(v.vinculo_origem_id IS NOT NULL) AS transferencias
              FROM termos t JOIN aparelhos a ON a.id = t.aparelho_id JOIN vinculos v ON v.id = t.vinculo_id
             WHERE t.status = 'enviado' AND t.token_expira_em >= ? {restricao}""", [agora_txt] + p, um=True)
    if aguardando["n"]:
        from ..util import dias_desde
        dias = dias_desde(aguardando["mais_antigo"]) or 0
        descricao = f"O mais antigo foi enviado há {plural(dias, 'dia')}."
        if aguardando["transferencias"]:
            descricao += f" {plural(aguardando['transferencias'], 'é de transferência ou troca', 'são de transferências ou trocas')}."
        adicionar("aguardando", "aviso" if dias >= 3 else "info", aguardando["n"],
                  f"{plural(aguardando['n'], 'termo')} aguardando assinatura", descricao,
                  url_for("termos.lista", status="enviado"), "Ver termos")

    expirados = escalar(f"SELECT COUNT(*) FROM termos t JOIN aparelhos a ON a.id = t.aparelho_id "
                        f"WHERE t.status = 'enviado' AND t.token_expira_em < ? {restricao}", [agora_txt] + p)
    adicionar("expirados", "critico", expirados, f"{plural(expirados, 'link')} de assinatura expirado(s)",
              "O colaborador não assinou dentro do prazo. Reenvie o link.",
              url_for("termos.lista", status="expirado"), "Reenviar")

    restricao_c, pc = filtro_unidades("c.unidade_id")
    desligados = escalar(
        f"""SELECT COUNT(DISTINCT c.id) FROM colaboradores c JOIN vinculos v ON v.colaborador_id = c.id
             WHERE c.situacao = 'desligado' AND v.devolvido_em IS NULL {restricao_c}""", pc)
    adicionar("desligados", "critico", desligados,
              f"{plural(desligados, 'colaborador desligado', 'colaboradores desligados')} com aparelho",
              "Registre a devolução dos aparelhos de quem saiu da empresa.",
              url_for("colaboradores.lista", situacao="desligado", aparelho="com"), "Ver colaboradores")

    atrasadas = escalar(
        f"""SELECT COUNT(*) FROM vinculos v JOIN aparelhos a ON a.id = v.aparelho_id
             WHERE v.devolvido_em IS NULL AND v.devolucao_prevista IS NOT NULL AND v.devolucao_prevista < ?
             {restricao}""", [agora_local().date().isoformat()] + p)
    adicionar("atrasadas", "critico", atrasadas, f"{plural(atrasadas, 'devolução', 'devoluções')} em atraso",
              "Empréstimos com data prevista de devolução vencida.",
              url_for("aparelhos.lista", status="em_uso", devolucao="atrasada"), "Ver aparelhos")

    devolvidos = escalar(f"SELECT COUNT(*) FROM aparelhos a WHERE a.status = 'devolvido' {restricao}", p)
    adicionar("devolvidos", "info", devolvidos, f"{plural(devolvidos, 'aparelho devolvido', 'aparelhos devolvidos')} "
              "aguardando conferência", "Confira e formate antes de marcar como disponível.",
              url_for("aparelhos.lista", status="devolvido"), "Conferir")

    dias_parado = max(1, cfg_int("dias_aparelho_parado", 30))
    limite = para_bd(agora() - timedelta(days=dias_parado))
    parados = escalar(f"SELECT COUNT(*) FROM aparelhos a WHERE a.status = 'disponivel' AND a.status_desde < ? "
                      f"{restricao}", [limite] + p)
    adicionar("parados", "info", parados, f"{plural(parados, 'aparelho')} sem responsável há mais de {dias_parado} dias",
              "Aparelhos disponíveis e parados: podem ser redistribuídos.",
              url_for("aparelhos.lista", status="disponivel"), "Ver disponíveis")

    inconsistentes = escalar(
        f"""SELECT COUNT(*) FROM aparelhos a WHERE a.status = 'em_uso'
              AND NOT EXISTS (SELECT 1 FROM vinculos v WHERE v.aparelho_id = a.id AND v.devolvido_em IS NULL)
              {restricao}""", p)
    adicionar("inconsistentes", "critico", inconsistentes, f"{plural(inconsistentes, 'aparelho')} em uso sem responsável",
              "O status diz “em utilização”, mas não há entrega aberta. Revise o cadastro.",
              url_for("aparelhos.lista", status="em_uso", responsavel="sem"), "Revisar")

    if incluir_admin:
        from .email import falhas_recentes
        falhas = falhas_recentes()
        adicionar("falhas_email", "critico", falhas, f"{plural(falhas, 'e-mail')} com falha nos últimos 3 dias",
                  "Confira o motivo e reenvie.", url_for("configuracoes.emails", status="falha"), "Ver e-mails")

    from flask import g
    from . import chamados as svc_chamados
    usuario = g.get("usuario")
    if svc_chamados.eh_ti(usuario):
        dados = svc_chamados.contador(usuario)
        if dados["respostas"] == 1:
            r = dados["resposta"]
            adicionar("respostas_chamados", "aviso", 1, f"{r['nome']} respondeu o chamado #{r['numero']}",
                      "A pessoa escreveu no pedido e a TI ainda não viu.",
                      url_for("chamados.detalhe", chamado_id=r["id"]), "Abrir chamado")
        else:
            adicionar("respostas_chamados", "aviso", dados["respostas"],
                      f"{dados['respostas']} chamados com resposta nova do colaborador",
                      "Respostas que a TI ainda não viu nos seus chamados ou nos sem responsável.",
                      url_for("chamados.fila", respostas="1", unidade="todas"), "Ver chamados")

    ordem = {"critico": 0, "aviso": 1, "info": 2}
    itens.sort(key=lambda item: ordem[item["nivel"]])
    return itens


def contagem_alertas() -> int:
    return sum(1 for a in alertas() if a["nivel"] in ("critico", "aviso"))


# ------------------------------------------------------------------ tarefas automáticas

def _horario_comercial() -> bool:
    agora_l = agora_local()
    return agora_l.weekday() < 5 and 8 <= agora_l.hour < 18


def job_lembretes() -> str:
    if not cfg_bool("lembrete_ativo"):
        return "Lembretes desativados."
    if cfg_bool("lembrete_horario_comercial") and not _horario_comercial():
        return "Fora do horário comercial."
    intervalo = max(1, cfg_int("lembrete_intervalo_dias", 2))
    maximo = max(0, cfg_int("lembrete_maximo", 3))
    limite = para_bd(agora() - timedelta(days=intervalo))
    pendentes = consultar(
        """SELECT t.id FROM termos t JOIN vinculos v ON v.id = t.vinculo_id
            WHERE t.status = 'enviado' AND v.devolvido_em IS NULL AND t.lembretes < ?
              AND max(coalesce(t.ultimo_lembrete_em, ''), coalesce(t.ultimo_envio_em, '')) <= ?
            ORDER BY t.enviado_em""", (maximo, limite))
    enviados = falhas = 0
    for indice, termo in enumerate(pendentes):
        if indice:
            time.sleep(current_app.config.get("INTERVALO_ENVIO_LOTE", 2.5) if not current_app.config["TESTING"] else 0)
        try:
            resultado = svc_termos.enviar(termo["id"], lembrete=True)
            enviados += resultado.ok
            falhas += not resultado.ok
        except Exception:
            falhas += 1
            current_app.logger.exception("Falha ao enviar lembrete do termo %s", termo["id"])
    return f"{enviados} lembrete(s) enviado(s), {falhas} falha(s)."


def job_resumo_diario() -> str:
    if not cfg_bool("resumo_diario_ativo"):
        return "Resumo diário desativado."
    lista = [a for a in alertas(incluir_admin=True) if a["chave"] != "simulacao"]
    if not lista:
        return "Nenhuma pendência — resumo não enviado."
    html = render_template("emails/resumo.html", alertas=lista, empresa=nome_empresa(), url=url_base())
    destinatarios = emails_notificacao()
    from .email import enviar
    total = sum(a["quantidade"] or 1 for a in lista)
    for destinatario in destinatarios:
        enviar(destinatario, f"Celulares corporativos: {plural(total, 'pendência')} para hoje", html,
               finalidade="resumo")
    return f"Resumo enviado para {len(destinatarios)} destinatário(s)."


def job_limpeza() -> str:
    from ..db import executar
    limite = para_bd(agora() - timedelta(days=7))
    apagados = executar("DELETE FROM codigos_verificacao WHERE criado_em < ?", (limite,)).rowcount
    pasta = current_app.config["PASTA_TEMP"]
    removidos = 0
    for arquivo in pasta.glob("*"):
        if arquivo.is_file() and time.time() - arquivo.stat().st_mtime > 86400:
            arquivo.unlink(missing_ok=True)
            removidos += 1
    return f"{apagados} código(s) e {removidos} arquivo(s) temporário(s) removidos."
