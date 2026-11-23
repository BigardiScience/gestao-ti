"""Autenticação, permissões, proteção CSRF, criptografia e limite de tentativas."""
import hashlib
import hmac
import secrets
import threading
import time
from functools import wraps

from cryptography.fernet import Fernet, InvalidToken
from flask import abort, current_app, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# Hierarquia: o administrador gerencia usuários e tudo o mais; o técnico de TI vê os computadores e chamados de
# todas as unidades e altera só os da(s) unidade(s) dele; a consulta só visualiza as unidades liberadas; o
# colaborador (qualquer pessoa do AD) só abre e acompanha os próprios pedidos.
PERFIS = {"admin": "Administrador de TI", "tecnico": "Técnico de TI", "consulta": "Usuário de consulta",
          "colaborador": "Colaborador"}
PERFIS_TI = ("admin", "tecnico", "consulta")


# ------------------------------------------------------------------ criptografia de segredos

def _fernet() -> Fernet:
    return Fernet(current_app.config["CHAVE_CRIPTOGRAFIA"])


def cifrar(texto: str) -> str:
    if not texto:
        return ""
    return _fernet().encrypt(texto.encode("utf-8")).decode("ascii")


def decifrar(texto: str) -> str:
    if not texto:
        return ""
    try:
        return _fernet().decrypt(texto.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        current_app.logger.error("Não foi possível decifrar um segredo: a chave mestra mudou?")
        return ""


def selo(dados: str) -> str:
    """HMAC-SHA256 que sela as evidências de assinatura contra alteração no banco."""
    return hmac.new(current_app.config["CHAVE_SELO"], dados.encode("utf-8"), hashlib.sha256).hexdigest()


# ------------------------------------------------------------------ senhas e tokens

def hash_senha(senha: str) -> str:
    return generate_password_hash(senha)


def conferir_senha(senha_hash: str, senha: str) -> bool:
    try:
        return check_password_hash(senha_hash, senha)
    except (ValueError, TypeError):
        return False


def problema_senha(senha: str) -> str | None:
    """Política mínima: 8 caracteres, com letras e números."""
    if len(senha or "") < 8:
        return "A senha precisa ter pelo menos 8 caracteres."
    if not any(c.isalpha() for c in senha) or not any(c.isdigit() for c in senha):
        return "A senha precisa ter letras e números."
    return None


def senha_temporaria() -> str:
    letras = "abcdefghjkmnpqrstuvwxyz"
    return "".join(secrets.choice(letras) for _ in range(4)) + "-" + "".join(
        secrets.choice("23456789") for _ in range(4)) + "-" + "".join(secrets.choice(letras) for _ in range(4))


def novo_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def novo_codigo_numerico(digitos: int = 6) -> str:
    return "".join(secrets.choice("0123456789") for _ in range(digitos))


# ------------------------------------------------------------------ CSRF

def csrf_token() -> str:
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


def verificar_csrf():
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return
    enviado = request.form.get("_csrf") or request.headers.get("X-CSRF-Token") or ""
    esperado = session.get("_csrf") or ""
    if not esperado or not hmac.compare_digest(enviado, esperado):
        abort(400, description="A página ficou aberta por muito tempo ou a sessão expirou. "
                               "Recarregue a página e tente novamente.")


# ------------------------------------------------------------------ limite de tentativas (memória)

class LimitadorTaxa:
    """Conta eventos por chave numa janela de tempo. Protege login, códigos e reenvios."""

    def __init__(self):
        self._eventos: dict[str, list[float]] = {}
        self._trava = threading.Lock()

    def excedeu(self, chave: str, maximo: int, janela: int) -> bool:
        agora = time.monotonic()
        with self._trava:
            eventos = [t for t in self._eventos.get(chave, []) if agora - t < janela]
            self._eventos[chave] = eventos
            return len(eventos) >= maximo

    def registrar(self, chave: str):
        with self._trava:
            self._eventos.setdefault(chave, []).append(time.monotonic())
            if len(self._eventos) > 20000:  # evita crescer sem limite
                self._eventos.clear()

    def limpar(self, chave: str):
        with self._trava:
            self._eventos.pop(chave, None)


limitador = LimitadorTaxa()


# ------------------------------------------------------------------ sessão e permissões

def usuario_atual():
    return g.get("usuario")


def eh_admin() -> bool:
    usuario = usuario_atual()
    return bool(usuario and usuario["perfil"] == "admin")


def pagina_inicial(usuario) -> str:
    """Primeira tela de cada perfil depois de entrar."""
    if not usuario:
        return url_for("auth.entrar")
    if usuario["perfil"] == "colaborador":
        return url_for("pedidos.novo")
    if usuario["perfil"] == "tecnico":
        return url_for("chamados.fila")
    return url_for("painel.inicio")


def _exigir_login():
    if not usuario_atual():
        if request.path.endswith(".json") or request.accept_mimetypes.best == "application/json":
            abort(401, description="Sessão encerrada. Entre novamente.")
        if request.method == "GET":
            session["voltar_para"] = request.full_path if request.query_string else request.path
        return redirect(url_for("auth.entrar"))
    return None


def login_obrigatorio(funcao):
    """Telas da equipe de TI. O colaborador nunca passa daqui, nem por requisição direta."""
    @wraps(funcao)
    def envoltorio(*args, **kwargs):
        resposta = _exigir_login()
        if resposta:
            return resposta
        if usuario_atual()["perfil"] not in PERFIS_TI:
            if request.method == "GET":
                return redirect(pagina_inicial(usuario_atual()))
            abort(403, description="Esta área é exclusiva da equipe de TI.")
        return funcao(*args, **kwargs)
    return envoltorio


def chamados_obrigatorio(funcao):
    """Fila e atendimento de chamados: só administrador e técnicos (o resto é conferido em cada chamado)."""
    @wraps(funcao)
    def envoltorio(*args, **kwargs):
        resposta = _exigir_login()
        if resposta:
            return resposta
        if usuario_atual()["perfil"] not in ("admin", "tecnico"):
            if usuario_atual()["perfil"] == "colaborador" and request.method == "GET":
                return redirect(pagina_inicial(usuario_atual()))
            abort(403, description="Os chamados são atendidos só pela equipe técnica de TI.")
        return funcao(*args, **kwargs)
    return envoltorio


def pedidos_obrigatorio(funcao):
    """Telas de "Pedidos para a TI": qualquer pessoa que entrou (colaborador ou equipe de TI)."""
    @wraps(funcao)
    def envoltorio(*args, **kwargs):
        resposta = _exigir_login()
        if resposta:
            return resposta
        return funcao(*args, **kwargs)
    return envoltorio


def admin_obrigatorio(funcao):
    @wraps(funcao)
    @login_obrigatorio
    def envoltorio(*args, **kwargs):
        if not eh_admin():
            abort(403, description="Esta ação é exclusiva do administrador de TI.")
        return funcao(*args, **kwargs)
    return envoltorio


def unidades_permitidas():
    """None = todas as unidades. Caso contrário, conjunto de IDs liberados ao usuário de consulta."""
    usuario = usuario_atual()
    if not usuario or usuario["perfil"] == "admin":
        return None
    return usuario.get("unidades")


def filtro_unidades(coluna: str) -> tuple[str, list]:
    """Trecho SQL que restringe a consulta às unidades do usuário (vazio para quem vê tudo)."""
    permitidas = unidades_permitidas()
    if permitidas is None:
        return "", []
    if not permitidas:
        return " AND 0", []
    marcadores = ",".join("?" * len(permitidas))
    return f" AND {coluna} IN ({marcadores})", list(permitidas)


def garantir_acesso_unidade(unidade_id):
    permitidas = unidades_permitidas()
    if permitidas is not None and unidade_id not in permitidas:
        abort(404)


# ------------------------------------------------------------------ permissões dos computadores

def eh_tecnico() -> bool:
    usuario = usuario_atual()
    return bool(usuario and usuario["perfil"] == "tecnico")


def unidades_editaveis() -> set | None:
    """None = pode alterar computadores de qualquer unidade (administrador); senão, os IDs liberados."""
    usuario = usuario_atual()
    if not usuario:
        return set()
    if usuario["perfil"] == "admin":
        return None
    if usuario["perfil"] == "tecnico":
        return set(usuario.get("unidades") or ())
    return set()


def pode_editar_unidade(unidade_id) -> bool:
    editaveis = unidades_editaveis()
    return editaveis is None or unidade_id in editaveis


def pode_cadastrar_computador() -> bool:
    editaveis = unidades_editaveis()
    return editaveis is None or bool(editaveis)


def ve_dados_de_rede() -> bool:
    """IP, MAC e acesso remoto ajudam a mapear a rede: só a equipe de TI vê (ISO 27001, controle 8.3)."""
    usuario = usuario_atual()
    return bool(usuario and usuario["perfil"] in ("admin", "tecnico"))


def filtro_unidades_computadores(coluna: str) -> tuple[str, list]:
    """A equipe de TI vê os computadores de todas as unidades; a consulta, só os das unidades liberadas."""
    usuario = usuario_atual()
    if usuario and usuario["perfil"] in ("admin", "tecnico"):
        return "", []
    return filtro_unidades(coluna)


def garantir_edicao_unidade(unidade_id):
    """Barra no servidor qualquer alteração fora das unidades do usuário (mesmo por requisição direta)."""
    if not pode_editar_unidade(unidade_id):
        abort(403, description="Você só pode alterar computadores da sua unidade.")


def tecnico_obrigatorio(funcao):
    """Administrador ou técnico de TI com ao menos uma unidade."""
    @wraps(funcao)
    @login_obrigatorio
    def envoltorio(*args, **kwargs):
        if not pode_cadastrar_computador():
            abort(403, description="Esta ação é exclusiva da equipe de TI.")
        return funcao(*args, **kwargs)
    return envoltorio


def colaborador_portal():
    return g.get("colaborador_portal")


def portal_obrigatorio(funcao):
    @wraps(funcao)
    def envoltorio(*args, **kwargs):
        if not colaborador_portal():
            flash("Entre com o seu e-mail corporativo para ver os seus termos.", "info")
            return redirect(url_for("portal.entrar"))
        return funcao(*args, **kwargs)
    return envoltorio
