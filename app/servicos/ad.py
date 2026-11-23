"""Login pelo Active Directory: confere usuário e senha no controlador (LDAPS) e mantém o cadastro em dia.

A unidade e o setor de cada pessoa vêm da posição dela no AD
(OU=<setor>,OU=Setores,OU=<unidade>,...). Cada unidade do sistema informa a sua "OU no AD".
"""
import re
import ssl
from dataclasses import dataclass
from datetime import timedelta

from flask import current_app

from ..db import consultar, escalar, executar, normalizar, transacao
from ..util import agora, agora_bd, de_bd, para_bd
from . import ErroNegocio
from .configuracoes import cfg, cfg_bool, cfg_int
from .ldap import (ConexaoLDAP, CredenciaisInvalidas, ErroLDAP, ServidorIndisponivel, base_do_dominio, filtro_e,
                   filtro_igual, unidade_e_setor)

MAX_TENTATIVAS = 5
BLOQUEIO_MINUTOS = 15
ATRIBUTOS = ["sAMAccountName", "displayName", "givenName", "sn", "mail", "userPrincipalName", "userAccountControl"]
PADRAO_LOGIN = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


@dataclass
class DadosAD:
    login: str
    nome: str
    email: str | None
    dn: str
    unidade_ou: str | None
    setor: str | None


def ativo() -> bool:
    return cfg_bool("ad_ativo") and bool(cfg("ad_dominio")) and bool(cfg("ad_servidores"))


def normalizar_login(digitado: str) -> str | None:
    """Aceita nome.sobrenome, DOMINIO\\nome.sobrenome ou o e-mail (usa a parte antes do @)."""
    texto = (digitado or "").strip().lower()
    if "\\" in texto:
        texto = texto.rsplit("\\", 1)[1]
    if "@" in texto:
        texto = texto.split("@", 1)[0]
    return texto if PADRAO_LOGIN.fullmatch(texto) else None


def servidores() -> list[tuple[str, int]]:
    lista = []
    padrao = cfg_int("ad_porta", 636) or 636
    for item in re.split(r"[\s,;]+", cfg("ad_servidores") or ""):
        if not item:
            continue
        host, _, porta = item.partition(":")
        lista.append((host, int(porta) if porta.isdigit() else padrao))
    return lista


def contexto_tls() -> ssl.SSLContext:
    contexto = ssl.create_default_context()
    contexto.minimum_version = ssl.TLSVersion.TLSv1_2
    # O Python 3.13 passou a exigir o modo "estrito" do X.509, que recusa certificados de CAs internas
    # antigas (sem alguns campos opcionais). A cadeia e o nome do servidor continuam sendo conferidos.
    if hasattr(ssl, "VERIFY_X509_STRICT"):
        contexto.verify_flags &= ~ssl.VERIFY_X509_STRICT
    pem = (cfg("ad_ca_pem") or "").strip()
    if pem:
        contexto.load_verify_locations(cadata=pem)
    if not cfg_bool("ad_verificar_certificado"):
        contexto.check_hostname = False
        contexto.verify_mode = ssl.CERT_NONE
    return contexto


def consultar_ad(login: str, senha: str) -> DadosAD:
    """Confere a senha no primeiro controlador que responder e lê os dados do usuário."""
    dominio = (cfg("ad_dominio") or "").strip()
    if not dominio or not servidores():
        raise ErroLDAP("O login pelo AD ainda não foi configurado.")
    base = base_do_dominio(dominio)
    contexto = contexto_tls()
    timeout = cfg_int("ad_timeout", 5) or 5
    falhas = []
    for host, porta in servidores():
        try:
            with ConexaoLDAP(host, porta, timeout=timeout, contexto=contexto) as conexao:
                conexao.bind(f"{login}@{dominio}", senha)
                filtro = filtro_e(filtro_igual("objectClass", "user"), filtro_igual("sAMAccountName", login))
                entradas = conexao.buscar(base, filtro, ATRIBUTOS)
        except ServidorIndisponivel as erro:  # tenta o próximo controlador
            current_app.logger.warning("AD: %s indisponível: %s %s", host, erro.mensagem, erro.detalhe)
            falhas.append(erro)
            continue
        if not entradas:
            raise ErroLDAP("A senha confere, mas o usuário não foi encontrado no domínio.")
        entrada = entradas[0]
        try:
            controle = int(entrada.valor("userAccountControl") or 0)
        except ValueError:
            controle = 0
        if controle & 2:
            raise CredenciaisInvalidas("Este usuário está desativado no AD.")
        nome = entrada.valor("displayName") or " ".join(
            p for p in (entrada.valor("givenName"), entrada.valor("sn")) if p) or login
        unidade_ou, setor = unidade_e_setor(entrada.dn)
        return DadosAD(login=(entrada.valor("sAMAccountName") or login).lower(), nome=nome.strip()[:120],
                       email=(entrada.valor("mail") or "").strip().lower() or None, dn=entrada.dn,
                       unidade_ou=unidade_ou, setor=(setor or "")[:60] or None)
    detalhe = falhas[-1].mensagem if falhas else ""
    raise ServidorIndisponivel("Não foi possível falar com o AD agora. Tente de novo em alguns minutos.", detalhe)


# ------------------------------------------------------------------ bloqueio por tentativas

def _chave(login: str) -> str:
    return f"ad:{login}"


def minutos_bloqueado(login: str) -> int:
    linha = consultar("SELECT bloqueado_ate FROM login_tentativas WHERE chave = ?", (_chave(login),), um=True)
    if linha and linha["bloqueado_ate"] and linha["bloqueado_ate"] > agora_bd():
        return max(1, int((de_bd(linha["bloqueado_ate"]) - agora()).total_seconds() // 60) + 1)
    return 0


def registrar_falha(login: str):
    linha = consultar("SELECT falhas FROM login_tentativas WHERE chave = ?", (_chave(login),), um=True)
    falhas = (linha["falhas"] if linha else 0) + 1
    bloqueio = para_bd(agora() + timedelta(minutes=BLOQUEIO_MINUTOS)) if falhas >= MAX_TENTATIVAS else None
    executar("INSERT INTO login_tentativas (chave, falhas, bloqueado_ate, atualizado_em) VALUES (?, ?, ?, ?) "
             "ON CONFLICT(chave) DO UPDATE SET falhas = excluded.falhas, bloqueado_ate = excluded.bloqueado_ate, "
             "atualizado_em = excluded.atualizado_em",
             (_chave(login), 0 if bloqueio else falhas, bloqueio, agora_bd()))


def limpar_falhas(login: str):
    executar("DELETE FROM login_tentativas WHERE chave = ?", (_chave(login),))


# ------------------------------------------------------------------ cadastro a partir do AD

def unidade_da_ou(nome_ou: str | None):
    if not nome_ou:
        return None
    alvo = normalizar(nome_ou)
    for unidade in consultar("SELECT id, nome, ativo, chamados_ativo, ou_ad FROM unidades "
                             "WHERE ou_ad IS NOT NULL AND ou_ad <> ''"):
        if normalizar(unidade["ou_ad"]) == alvo:
            return unidade
    return None


def sincronizar(dados: DadosAD) -> dict:
    """Cria ou atualiza o usuário a partir do AD. Recusa quem não pode entrar."""
    usuario = consultar("SELECT * FROM usuarios WHERE login_ad = ?", (dados.login,), um=True)
    if usuario and usuario["origem"] == "local":
        # uma conta local (ex.: usuário de teste) nunca vira conta do AD sozinha: a senha local continuaria valendo
        raise ErroNegocio("Já existe uma conta local do sistema com este usuário. Fale com a TI.")
    unidade = unidade_da_ou(dados.unidade_ou)
    unidade_id = unidade["id"] if unidade and unidade["ativo"] else None
    if usuario and not usuario["ativo"]:
        raise ErroNegocio("Este usuário está desativado no sistema. Fale com a TI.")
    perfil = usuario["perfil"] if usuario else "colaborador"
    # colaborador só entra se a unidade dele já usa os pedidos (implantação por etapas); a equipe de TI sempre entra
    if perfil == "colaborador" and not (unidade_id and unidade["chamados_ativo"]):
        raise ErroNegocio("Sua unidade ainda não usa este sistema. Fale com a TI.")
    agora_txt = agora_bd()
    with transacao():
        if usuario:
            executar("UPDATE usuarios SET nome = ?, email = ?, unidade_id = ?, setor = ?, ad_dn = ?, "
                     "ad_sincronizado_em = ?, ultimo_acesso_em = ?, origem = 'ad', "
                     "atualizado_em = datetime('now') WHERE id = ?",
                     (dados.nome, dados.email, unidade_id or usuario["unidade_id"], dados.setor, dados.dn, agora_txt,
                      agora_txt, usuario["id"]))
            usuario_id = usuario["id"]
            if (usuario["nome"], usuario["setor"]) != (dados.nome, dados.setor):
                from .chamados import recalcular_busca
                recalcular_busca(solicitante_id=usuario_id)
        else:
            usuario_id = executar(
                "INSERT INTO usuarios (nome, email, perfil, origem, login_ad, unidade_id, setor, ad_dn, "
                "ad_sincronizado_em, ultimo_acesso_em) VALUES (?, ?, 'colaborador', 'ad', ?, ?, ?, ?, ?, ?)",
                (dados.nome, dados.email, dados.login, unidade_id, dados.setor, dados.dn, agora_txt, agora_txt),
            ).lastrowid
    return consultar("SELECT * FROM usuarios WHERE id = ?", (usuario_id,), um=True)


def entrar(digitado: str, senha: str) -> dict:
    """Fluxo completo do login pelo AD. Devolve o usuário ou levanta ErroNegocio com a mensagem para a tela."""
    login = normalizar_login(digitado)
    if not login:
        raise ErroNegocio("Usuário ou senha incorretos.")
    minutos = minutos_bloqueado(login)
    if minutos:
        raise ErroNegocio(f"Acesso bloqueado por excesso de tentativas. Tente de novo em {minutos} min.")
    try:
        dados = consultar_ad(login, senha)
    except CredenciaisInvalidas as erro:
        registrar_falha(login)
        raise ErroNegocio(erro.mensagem) from None
    except ErroLDAP as erro:
        current_app.logger.warning("AD: falha no login de %s: %s %s", login, erro.mensagem, erro.detalhe)
        raise ErroNegocio(erro.mensagem) from None
    limpar_falhas(login)
    return sincronizar(dados)


def testar(login: str, senha: str) -> dict:
    """Usado em Configurações > Active Directory: confere sem criar usuário."""
    login_ok = normalizar_login(login)
    if not login_ok:
        raise ErroNegocio("Informe um usuário do domínio, por exemplo maria.silva.")
    try:
        dados = consultar_ad(login_ok, senha)
    except ErroLDAP as erro:
        raise ErroNegocio(erro.mensagem + (f" ({erro.detalhe})" if erro.detalhe else "")) from None
    unidade = unidade_da_ou(dados.unidade_ou)
    return {"login": dados.login, "nome": dados.nome, "email": dados.email, "dn": dados.dn,
            "unidade_ou": dados.unidade_ou, "setor": dados.setor,
            "unidade": unidade["nome"] if unidade else None,
            "unidade_ativa": bool(unidade and unidade["ativo"])}


def pre_cadastrado(login: str) -> int | None:
    return escalar("SELECT id FROM usuarios WHERE login_ad = ?", (login,))
