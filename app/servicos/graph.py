"""Cliente do Microsoft Graph para envio de e-mails pelo Outlook / Microsoft 365.

Dois modos de autenticação no Microsoft Entra ID:

* aplicativo (recomendado): fluxo "client credentials". O sistema envia como uma caixa
  compartilhada (ex.: celulares@empresa.com.br) sem depender da conta de um funcionário.
  Permissão: Mail.Send do tipo Aplicativo (com consentimento do administrador) ou,
  de forma mais restrita, a função "Application Mail.Send" do RBAC para aplicativos do
  Exchange Online limitada a essa caixa.
* delegado: um usuário entra com a conta Microsoft uma vez ("Conectar conta"); o sistema
  guarda o refresh token cifrado e envia em nome dessa conta (ou de uma caixa
  compartilhada em que ela tenha permissão "Enviar como"). Permissões: Mail.Send e
  Mail.Send.Shared do tipo Delegado.
"""
import base64
import hashlib
import json
import re
import threading
import time
from urllib.parse import quote, urlencode

import requests
from flask import current_app

from .configuracoes import cfg, salvar, segredo

ESCOPOS_DELEGADOS = ("offline_access openid profile https://graph.microsoft.com/User.Read "
                     "https://graph.microsoft.com/Mail.Send https://graph.microsoft.com/Mail.Send.Shared")

_cache_tokens: dict = {}
_trava = threading.Lock()

MENSAGENS_AADSTS = {
    "700016": "O ID do aplicativo (client ID) não foi encontrado neste locatário. Confira o ID do aplicativo e o "
              "ID do diretório (tenant ID).",
    "7000215": "O segredo do cliente está incorreto. Copie a coluna “Valor” do segredo (e não o “ID do segredo”).",
    "7000222": "O segredo do cliente expirou. Crie um novo segredo no Entra ID e atualize-o aqui.",
    "90002": "Locatário não encontrado. Confira o ID do diretório (tenant ID).",
    "900023": "O ID do diretório (tenant ID) é inválido.",
    "65001": "O aplicativo ainda não recebeu o consentimento do administrador para as permissões solicitadas.",
    "70011": "Escopo de permissão inválido. Confira as permissões do aplicativo no Entra ID.",
    "700082": "A conexão com a conta Microsoft expirou por inatividade. Clique em “Conectar conta Microsoft”.",
    "50173": "A conta Microsoft mudou de senha ou a sessão foi revogada. Conecte a conta novamente.",
    "50076": "A conta exige nova autenticação (MFA). Conecte a conta Microsoft novamente.",
    "50079": "A conta exige nova autenticação (MFA). Conecte a conta Microsoft novamente.",
    "70008": "O código de autorização expirou ou já foi usado. Tente conectar novamente.",
    "53003": "O acesso foi bloqueado por uma política de Acesso Condicional do Microsoft Entra ID.",
    "50011": "O endereço de retorno (redirect URI) não está cadastrado no aplicativo do Entra ID.",
    "500113": "O aplicativo não tem um endereço de retorno (redirect URI) cadastrado.",
    "7000218": "O aplicativo exige o segredo do cliente. Preencha o segredo nas configurações.",
    "50034": "A conta informada não existe neste locatário.",
}


class ErroGraph(Exception):
    def __init__(self, mensagem: str, *, status: int | None = None, codigo: str | None = None,
                 transitorio: bool = False, detalhe: str | None = None):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.status = status
        self.codigo = codigo
        self.transitorio = transitorio
        self.detalhe = detalhe


# ------------------------------------------------------------------ configuração

def configuracao() -> dict:
    return {
        "autenticacao": cfg("graph_autenticacao") or "aplicativo",
        "tenant": (cfg("graph_tenant_id") or "").strip(),
        "client_id": (cfg("graph_client_id") or "").strip(),
        "client_secret": segredo("graph_client_secret"),
        "remetente": (cfg("graph_remetente") or "").strip(),
        "refresh_token": segredo("graph_refresh_token"),
        "conta": cfg("graph_conta") or "",
    }


def configurada(c: dict | None = None) -> bool:
    c = c or configuracao()
    if not (c["tenant"] and c["client_id"] and c["client_secret"]):
        return False
    if c["autenticacao"] == "aplicativo":
        return bool(c["remetente"])
    return bool(c["refresh_token"])


def limpar_cache():
    with _trava:
        _cache_tokens.clear()


def _autoridade() -> str:
    return current_app.config["GRAPH_AUTORIDADE"]


def _api() -> str:
    return current_app.config["GRAPH_API"]


# ------------------------------------------------------------------ tokens

def _erro_token(status: int, corpo: dict) -> ErroGraph:
    descricao = corpo.get("error_description") or ""
    codigo = corpo.get("error") or ""
    achado = re.search(r"AADSTS(\d+)", descricao)
    if achado and achado.group(1) in MENSAGENS_AADSTS:
        mensagem = MENSAGENS_AADSTS[achado.group(1)]
    elif codigo == "invalid_client":
        mensagem = "Credenciais do aplicativo inválidas. Confira o ID do aplicativo e o segredo do cliente."
    elif codigo == "invalid_grant":
        mensagem = "A autorização da conta Microsoft não é mais válida. Conecte a conta novamente."
    elif status >= 500:
        mensagem = "O Microsoft Entra ID está indisponível no momento. Tente novamente em alguns minutos."
    else:
        mensagem = "O Microsoft Entra ID recusou a autenticação."
    if achado:
        mensagem += f" (código AADSTS{achado.group(1)})"
    return ErroGraph(mensagem, status=status, codigo=codigo, transitorio=status >= 500 or status == 429,
                     detalhe=descricao[:500])


def _post_token(tenant: str, dados: dict) -> dict:
    url = f"{_autoridade()}/{quote(tenant)}/oauth2/v2.0/token"
    try:
        resposta = requests.post(url, data=dados, timeout=(10, 30))
    except requests.RequestException as erro:
        raise ErroGraph("Não foi possível conectar ao Microsoft Entra ID. Verifique a internet e o proxy do "
                        "servidor.", transitorio=True, detalhe=str(erro)) from None
    try:
        corpo = resposta.json()
    except ValueError:
        corpo = {}
    if resposta.status_code != 200 or "access_token" not in corpo:
        raise _erro_token(resposta.status_code, corpo)
    return corpo


def obter_token(c: dict | None = None, forcar: bool = False) -> str:
    c = c or configuracao()
    chave = (c["autenticacao"], c["tenant"], c["client_id"])
    with _trava:
        item = _cache_tokens.get(chave)
        if item and not forcar and item["expira"] - 120 > time.time():
            return item["token"]
    if c["autenticacao"] == "delegado":
        if not c["refresh_token"]:
            raise ErroGraph("Nenhuma conta Microsoft conectada. Clique em “Conectar conta Microsoft”.")
        corpo = _post_token(c["tenant"], {
            "client_id": c["client_id"], "client_secret": c["client_secret"], "grant_type": "refresh_token",
            "refresh_token": c["refresh_token"], "scope": ESCOPOS_DELEGADOS,
        })
        if corpo.get("refresh_token") and corpo["refresh_token"] != c["refresh_token"]:
            salvar({"graph_refresh_token": corpo["refresh_token"]})
    else:
        corpo = _post_token(c["tenant"], {
            "client_id": c["client_id"], "client_secret": c["client_secret"],
            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials",
        })
    token = corpo["access_token"]
    with _trava:
        _cache_tokens[chave] = {"token": token, "expira": time.time() + int(corpo.get("expires_in", 3600))}
    return token


def decodificar_token(token: str) -> dict:
    """Lê as declarações (claims) do token só para diagnóstico — sem validar a assinatura."""
    try:
        carga = token.split(".")[1]
        carga += "=" * (-len(carga) % 4)
        return json.loads(base64.urlsafe_b64decode(carga.encode()))
    except (IndexError, ValueError):
        return {}


# ------------------------------------------------------------------ envio

def _erro_api(resposta, remetente: str) -> ErroGraph:
    try:
        erro = resposta.json().get("error", {})
    except ValueError:
        erro = {}
    codigo = erro.get("code") or ""
    texto = erro.get("message") or ""
    status = resposta.status_code
    if status == 403 or codigo in ("ErrorAccessDenied", "AccessDenied"):
        mensagem = (f"Acesso negado ao enviar pela caixa {remetente}. Confira a permissão Mail.Send (com "
                    "consentimento do administrador) ou a atribuição do RBAC para aplicativos no Exchange "
                    "Online — alterações podem levar até 2 horas para valer.")
    elif status == 404 or codigo in ("ErrorInvalidUser", "ResourceNotFound", "MailboxNotEnabledForRESTAPI",
                                     "MailboxNotFound"):
        mensagem = (f"A caixa de e-mail {remetente} não foi encontrada ou não tem Exchange Online. Confira o "
                    "endereço do remetente.")
    elif codigo == "ErrorInvalidRecipients" or "recipient" in texto.lower():
        mensagem = "O endereço do destinatário foi recusado pelo Microsoft 365."
    elif status == 413 or codigo == "ErrorMessageSizeExceeded":
        mensagem = "A mensagem ficou grande demais para o Microsoft 365."
    elif status == 429:
        mensagem = "O limite de envios do Microsoft 365 foi atingido. O sistema tentará de novo em instantes."
    elif status == 401:
        mensagem = "O Microsoft 365 recusou o token de acesso. Teste a conexão nas configurações."
    elif status >= 500:
        mensagem = "O Microsoft 365 está instável no momento. Tente novamente em alguns minutos."
    else:
        mensagem = f"O Microsoft 365 recusou o envio ({status} {codigo})."
    return ErroGraph(mensagem, status=status, codigo=codigo, transitorio=status in (429, 500, 502, 503, 504),
                     detalhe=texto[:500])


def caminho_envio(c: dict) -> str:
    if c["autenticacao"] == "delegado" and (not c["remetente"] or c["remetente"].lower() == c["conta"].lower()):
        return "/me/sendMail"
    return f"/users/{quote(c['remetente'], safe='@')}/sendMail"


def enviar_email(destinatario: str, assunto: str, html: str, anexos: list | None = None,
                 c: dict | None = None) -> int:
    """Envia a mensagem. Repete em falhas temporárias. Devolve o número de tentativas."""
    c = c or configuracao()
    if not configurada(c):
        raise ErroGraph("A integração com o Microsoft 365 não está configurada. Abra Configurações > Outlook.")
    mensagem = {
        "subject": assunto,
        "body": {"contentType": "HTML", "content": html},
        "toRecipients": [{"emailAddress": {"address": destinatario}}],
    }
    if anexos:
        mensagem["attachments"] = [{
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": anexo["nome"],
            "contentType": anexo.get("tipo", "application/octet-stream"),
            "contentBytes": base64.b64encode(anexo["conteudo"]).decode("ascii"),
        } for anexo in anexos]
    url = _api() + caminho_envio(c)
    forcar_token = False
    for tentativa in range(1, 4):
        token = obter_token(c, forcar=forcar_token)
        try:
            resposta = requests.post(url, json={"message": mensagem, "saveToSentItems": True},
                                     headers={"Authorization": f"Bearer {token}"}, timeout=(10, 60))
        except requests.RequestException as erro:
            falha = ErroGraph("Não foi possível conectar ao Microsoft 365.", transitorio=True, detalhe=str(erro))
            espera = 2 * tentativa
        else:
            if resposta.status_code in (200, 202):
                return tentativa
            falha = _erro_api(resposta, c["remetente"] or c["conta"])
            if resposta.status_code == 401 and not forcar_token:
                forcar_token, falha.transitorio = True, True
            try:
                espera = min(10.0, float(resposta.headers.get("Retry-After", 2 * tentativa)))
            except ValueError:
                espera = 2 * tentativa
        if not falha.transitorio or tentativa == 3:
            raise falha
        time.sleep(espera if not current_app.config.get("TESTING") else 0)
    raise ErroGraph("Falha desconhecida no envio.")


# ------------------------------------------------------------------ autorização delegada (OAuth 2.0 + PKCE)

def desafio_pkce(verificador: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verificador.encode()).digest()).decode().rstrip("=")


def url_autorizacao(redirect_uri: str, estado: str, verificador: str) -> str:
    c = configuracao()
    parametros = {
        "client_id": c["client_id"], "response_type": "code", "redirect_uri": redirect_uri,
        "response_mode": "query", "scope": ESCOPOS_DELEGADOS, "state": estado,
        "code_challenge": desafio_pkce(verificador), "code_challenge_method": "S256", "prompt": "select_account",
    }
    return f"{_autoridade()}/{quote(c['tenant'])}/oauth2/v2.0/authorize?{urlencode(parametros)}"


def concluir_autorizacao(codigo: str, redirect_uri: str, verificador: str) -> str:
    c = configuracao()
    corpo = _post_token(c["tenant"], {
        "client_id": c["client_id"], "client_secret": c["client_secret"], "grant_type": "authorization_code",
        "code": codigo, "redirect_uri": redirect_uri, "code_verifier": verificador, "scope": ESCOPOS_DELEGADOS,
    })
    if not corpo.get("refresh_token"):
        raise ErroGraph("A Microsoft não devolveu o token de atualização. Confira se a permissão offline_access "
                        "está liberada.")
    dados = decodificar_token(corpo.get("id_token") or corpo["access_token"])
    conta = dados.get("preferred_username") or dados.get("upn") or dados.get("email") or ""
    salvar({"graph_refresh_token": corpo["refresh_token"], "graph_conta": conta})
    with _trava:
        _cache_tokens[("delegado", c["tenant"], c["client_id"])] = {
            "token": corpo["access_token"], "expira": time.time() + int(corpo.get("expires_in", 3600))}
    return conta


# ------------------------------------------------------------------ teste de conexão

def testar_conexao(destino_teste: str | None = None) -> dict:
    from . import email as svc_email
    from ..util import agora_bd
    c = configuracao()
    etapas = []

    def concluir(ok: bool, mensagem: str):
        salvar({"graph_status": "ok" if ok else "erro", "graph_status_mensagem": mensagem[:500],
                "graph_verificado_em": agora_bd()})
        return {"ok": ok, "etapas": etapas}

    faltando = [nome for nome, valor in (("ID do diretório (tenant)", c["tenant"]),
                                         ("ID do aplicativo (client)", c["client_id"]),
                                         ("segredo do cliente", c["client_secret"])) if not valor]
    if c["autenticacao"] == "aplicativo" and not c["remetente"]:
        faltando.append("caixa de e-mail remetente")
    if faltando:
        etapas.append(("erro", "Preencha: " + ", ".join(faltando) + "."))
        return concluir(False, etapas[-1][1])
    if c["autenticacao"] == "delegado" and not c["refresh_token"]:
        etapas.append(("erro", "Conecte uma conta Microsoft antes de testar."))
        return concluir(False, etapas[-1][1])

    try:
        token = obter_token(c, forcar=True)
    except ErroGraph as erro:
        etapas.append(("erro", erro.mensagem))
        return concluir(False, erro.mensagem)
    etapas.append(("ok", "Autenticação no Microsoft Entra ID realizada com sucesso."))

    dados = decodificar_token(token)
    if c["autenticacao"] == "aplicativo":
        if "Mail.Send" in (dados.get("roles") or []):
            etapas.append(("ok", "Permissão Mail.Send (aplicativo) presente no token."))
        else:
            etapas.append(("aviso", "O token não traz a permissão Mail.Send. Isso é esperado se o envio foi "
                                    "liberado pelo RBAC para aplicativos do Exchange Online; caso contrário, "
                                    "conceda Mail.Send (aplicativo) com consentimento do administrador."))
    else:
        escopos = (dados.get("scp") or "").split()
        if "Mail.Send" in escopos:
            etapas.append(("ok", f"Conta conectada: {c['conta']} — permissão Mail.Send concedida."))
        else:
            etapas.append(("aviso", "A permissão Mail.Send não apareceu no token delegado."))

    if destino_teste:
        from flask import render_template
        html = render_template("emails/teste.html", remetente=c["remetente"] or c["conta"])
        resultado = svc_email.enviar(destino_teste, "Teste da plataforma de gestão de celulares", html,
                                     finalidade="teste", forcar_modo="graph")
        if not resultado.ok:
            etapas.append(("erro", resultado.erro))
            return concluir(False, resultado.erro)
        etapas.append(("ok", f"E-mail de teste enviado para {destino_teste}. Confira a caixa de entrada."))
    return concluir(True, "Conexão verificada.")
