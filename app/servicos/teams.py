"""Avisos no Microsoft Teams pelo app Workflows ("Enviar alertas de webhook para um canal").

Cada unidade tem o link do fluxo do canal dela (guardado criptografado; só o administrador vê ou troca).
Uma unidade sem TI própria (Santos) manda os avisos para os canais das unidades que a atendem.
Os avisos entram numa fila no banco e saem logo em seguida; se o Teams falhar, o agendador tenta de novo.
O webhook não permite editar um cartão já enviado: quando alguém assume, sai uma mensagem curta.
"""
import json
import re
import threading
from datetime import timedelta
from urllib.parse import urlparse

import requests
from flask import current_app

from ..db import consultar, executar
from ..seguranca import cifrar, decifrar
from ..util import agora, agora_bd, para_bd

MAX_TENTATIVAS = 6
_trava_envio = threading.Lock()


class ErroTeams(Exception):
    pass


# ------------------------------------------------------------------ cartões (Adaptive Card 1.4)

def _mensagem(corpo: list, acoes: list | None = None) -> dict:
    conteudo = {"$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard",
                "version": "1.4", "msteams": {"width": "Full"}, "body": corpo}
    if acoes:
        conteudo["actions"] = acoes
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": conteudo}]}


def _sem_link(texto) -> str:
    """O Teams interpreta um pouco de Markdown: impede que o texto digitado pelo colaborador vire link."""
    return re.sub(r"\]\s*\(", "] (", str(texto or "")).replace("<", "‹").replace(">", "›")


def cartao(titulo: str, *, texto: str | None = None, fatos=(), link: str | None = None,
           rotulo_link: str = "Abrir chamado", alerta: bool = False) -> dict:
    corpo = [{"type": "TextBlock", "text": _sem_link(titulo), "weight": "Bolder", "size": "Medium", "wrap": True,
              "color": "Attention" if alerta else "Default"}]
    if texto:
        corpo.append({"type": "TextBlock", "text": _sem_link(texto), "wrap": True, "spacing": "Small"})
    if fatos:
        corpo.append({"type": "FactSet", "facts": [{"title": t, "value": _sem_link(v) or "—"} for t, v in fatos]})
    acoes = [{"type": "Action.OpenUrl", "title": rotulo_link, "url": link}] if link else None
    return _mensagem(corpo, acoes)


def aviso_curto(texto: str, link: str | None = None, rotulo_link: str = "Abrir chamado") -> dict:
    corpo = [{"type": "TextBlock", "text": _sem_link(texto), "wrap": True}]
    return _mensagem(corpo, [{"type": "Action.OpenUrl", "title": rotulo_link, "url": link}] if link else None)


# ------------------------------------------------------------------ links das unidades

def link_valido(url: str) -> bool:
    partes = urlparse((url or "").strip())
    return partes.scheme == "https" and bool(partes.netloc) and " " not in url


def guardar_link(unidade_id: int, url: str | None):
    executar("UPDATE unidades SET teams_webhook = ? WHERE id = ?", (cifrar(url.strip()) if url else None, unidade_id))


def link_da_unidade(unidade_id: int) -> str:
    valor = consultar("SELECT teams_webhook FROM unidades WHERE id = ?", (unidade_id,), um=True)
    return decifrar(valor["teams_webhook"]) if valor and valor["teams_webhook"] else ""


def ids_tambem(texto: str | None) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", texto or "")]


def destinos(unidade_id: int) -> list[str]:
    """Links dos canais que recebem os avisos da unidade (o próprio e os das unidades que a atendem)."""
    unidade = consultar("SELECT teams_webhook, teams_tambem FROM unidades WHERE id = ?", (unidade_id,), um=True)
    if not unidade:
        return []
    links = []
    if unidade["teams_webhook"]:
        links.append(decifrar(unidade["teams_webhook"]))
    for outro in ids_tambem(unidade["teams_tambem"]):
        if outro != unidade_id:
            link = link_da_unidade(outro)
            if link:
                links.append(link)
    vistos, unicos = set(), []
    for link in links:
        if link and link not in vistos:
            vistos.add(link)
            unicos.append(link)
    return unicos


# ------------------------------------------------------------------ fila de envio

def enfileirar(unidade_id: int, mensagem: dict, chamado_id: int | None = None) -> int:
    """Coloca o aviso na fila para cada canal da unidade. Devolve quantos canais vão receber."""
    conteudo = json.dumps(mensagem, ensure_ascii=False)
    canais = destinos(unidade_id)
    for link in canais:
        executar("INSERT INTO avisos_teams (chamado_id, destino, conteudo) VALUES (?, ?, ?)",
                 (chamado_id, cifrar(link), conteudo))
    return len(canais)


def postar(link: str, mensagem: dict):
    try:
        resposta = requests.post(link, json=mensagem, timeout=(5, 15))
    except requests.RequestException as erro:
        raise ErroTeams(f"Sem conexão com o Teams: {erro.__class__.__name__}") from None
    if resposta.status_code >= 300:
        raise ErroTeams(f"O Teams recusou o aviso (HTTP {resposta.status_code}): {resposta.text[:200]}")


def enviar_pendentes(limite: int = 30) -> str:
    """Envia o que está na fila. Seguro para rodar em paralelo (cada aviso é reservado antes do envio)."""
    if not _trava_envio.acquire(blocking=False):
        return "Envio já em andamento."
    try:
        # avisos presos em "enviando" (processo caiu no meio) voltam para a fila
        executar("UPDATE avisos_teams SET situacao = 'pendente' WHERE situacao = 'enviando' AND atualizado_em < ?",
                 (para_bd(agora() - timedelta(minutes=5)),))
        enviados = falhas = 0
        for aviso in consultar("SELECT id, destino, conteudo, tentativas FROM avisos_teams WHERE situacao = 'pendente' "
                               "AND (proxima_em IS NULL OR proxima_em <= ?) ORDER BY id LIMIT ?", (agora_bd(), limite)):
            reservado = executar("UPDATE avisos_teams SET situacao = 'enviando', atualizado_em = ? WHERE id = ? "
                                 "AND situacao = 'pendente'", (agora_bd(), aviso["id"])).rowcount
            if not reservado:
                continue
            try:
                postar(decifrar(aviso["destino"]), json.loads(aviso["conteudo"]))
            except ErroTeams as erro:
                tentativas = aviso["tentativas"] + 1
                desistir = tentativas >= MAX_TENTATIVAS
                executar("UPDATE avisos_teams SET situacao = ?, tentativas = ?, erro = ?, proxima_em = ?, "
                         "atualizado_em = ? WHERE id = ?",
                         ("falha" if desistir else "pendente", tentativas, str(erro)[:400],
                          para_bd(agora() + timedelta(minutes=2 ** tentativas)), agora_bd(), aviso["id"]))
                current_app.logger.warning("Teams: aviso %s não saiu (%s)", aviso["id"], erro)
                falhas += 1
            else:
                executar("UPDATE avisos_teams SET situacao = 'enviado', tentativas = tentativas + 1, erro = NULL, "
                         "enviado_em = ?, atualizado_em = ? WHERE id = ?", (agora_bd(), agora_bd(), aviso["id"]))
                enviados += 1
        return f"{enviados} aviso(s) no Teams enviados, {falhas} com falha."
    finally:
        _trava_envio.release()


def disparar():
    """Envia a fila logo depois da ação, sem segurar a tela de quem abriu o chamado."""
    app = current_app._get_current_object()
    if app.config.get("AVISOS_SINCRONOS") or app.config.get("TESTING"):
        enviar_pendentes()
        return

    def rodar():
        with app.app_context():
            try:
                enviar_pendentes()
            except Exception:
                app.logger.exception("Falha ao enviar avisos do Teams")

    threading.Thread(target=rodar, name="avisos-teams", daemon=True).start()


def falhas_recentes(horas: int = 72) -> int:
    return consultar("SELECT COUNT(*) AS n FROM avisos_teams WHERE situacao = 'falha' AND criado_em >= ?",
                     (para_bd(agora() - timedelta(hours=horas)),), um=True)["n"]


def testar(link: str, unidade: str):
    postar(link, cartao(f"Teste: avisos de chamados da unidade {unidade}",
                        texto="Se você está vendo esta mensagem, a ligação com o canal funcionou.",
                        fatos=[("Enviado em", agora_bd() + " (UTC)")]))
