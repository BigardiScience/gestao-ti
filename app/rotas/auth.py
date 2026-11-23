"""Entrada, saída, instalação inicial e troca de senha."""
import time
from datetime import timedelta

from flask import Blueprint, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import consultar, executar, transacao
from ..seguranca import (conferir_senha, hash_senha, limitador, pagina_inicial, pedidos_obrigatorio,
                         problema_senha)
from ..servicos import ErroNegocio
from ..servicos import ad as svc_ad
from ..servicos.configuracoes import emails_notificacao, nome_empresa, salvar
from ..servicos.registro import auditar
from ..util import agora, agora_bd, de_bd, ip_cliente, para_bd
from ..validacao import email_valido, limpar_texto
from . import destino_seguro

bp = Blueprint("auth", __name__)

MAX_TENTATIVAS = 5
BLOQUEIO_MINUTOS = 15


def _iniciar_sessao(usuario: dict):
    voltar = session.get("voltar_para")
    session.clear()
    session.permanent = True
    session["usuario_id"] = usuario["id"]
    session["versao_sessao"] = usuario["versao_sessao"]
    session["ativo_em"] = int(time.time())
    if usuario["perfil"] == "colaborador":
        session["sessao_curta"] = True
    return voltar


def _depois_de_entrar(usuario: dict, metodo: str):
    voltar = _iniciar_sessao(usuario)
    g.usuario = usuario
    auditar("login", "usuario", usuario["id"], {"metodo": metodo}, usuario=usuario)
    return redirect(destino_seguro(voltar, pagina_inicial(usuario)))


def _avisar_conta_local(usuario: dict):
    """Com o AD ligado, a conta local é só de emergência: todo uso dela gera um aviso (ISO 27001 e TISAX)."""
    from ..servicos.avisos_chamados import emails_admins
    from ..servicos.email import enviar
    for destino in sorted(set(emails_notificacao()) | set(emails_admins())):
        enviar(destino, f"Conta local de emergência usada – {nome_empresa()}",
               render_template("emails/conta_emergencia.html", usuario=usuario, ip=ip_cliente(),
                               quando=agora_bd()), finalidade="notificacao", usuario_id=usuario["id"])


def _entrar_local(identificador: str, senha: str):
    """Conta local: o administrador de emergência (pelo e-mail) ou um usuário de teste criado antes de ligar o AD
    (pelo nome de usuário). Devolve (resposta, erro); (None, None) quando não existe conta local com esse nome."""
    identificador = identificador.strip().lower()
    if "@" in identificador:
        usuario = consultar("SELECT * FROM usuarios WHERE email = ? COLLATE NOCASE AND origem = 'local'",
                            (identificador,), um=True)
    else:
        usuario = consultar("SELECT * FROM usuarios WHERE login_ad = ? AND origem = 'local'", (identificador,), um=True)
    if not usuario:
        return None, None
    if usuario["bloqueado_ate"] and usuario["bloqueado_ate"] > agora_bd():
        minutos = max(1, int((de_bd(usuario["bloqueado_ate"]) - agora()).total_seconds() // 60) + 1)
        return None, f"Acesso bloqueado por excesso de tentativas. Tente de novo em {minutos} min."
    if usuario["ativo"] and conferir_senha(usuario["senha_hash"], senha):
        executar("UPDATE usuarios SET tentativas_falhas = 0, bloqueado_ate = NULL, ultimo_acesso_em = ? "
                 "WHERE id = ?", (agora_bd(), usuario["id"]))
        resposta = _depois_de_entrar(usuario, "conta local")
        if svc_ad.ativo():
            _avisar_conta_local(usuario)
        return resposta, None
    tentativas = usuario["tentativas_falhas"] + 1
    bloqueio = para_bd(agora() + timedelta(minutes=BLOQUEIO_MINUTOS)) if tentativas >= MAX_TENTATIVAS else None
    executar("UPDATE usuarios SET tentativas_falhas = ?, bloqueado_ate = ? WHERE id = ?",
             (0 if bloqueio else tentativas, bloqueio, usuario["id"]))
    auditar("login_recusado", "usuario", usuario["id"], {"usuario": identificador}, usuario=usuario)
    if not usuario["ativo"]:
        return None, "Este usuário está desativado. Fale com o administrador de TI."
    return None, "Usuário ou senha incorretos."


def conexao_segura() -> bool:
    """A senha do Windows só trafega com HTTPS (localhost liberado para testes no próprio servidor)."""
    return request.is_secure or request.host.split(":")[0] in ("localhost", "127.0.0.1", "[::1]")


@bp.route("/entrar", methods=["GET", "POST"])
def entrar():
    if g.get("usuario"):
        return redirect(pagina_inicial(g.usuario))
    erro = None
    digitado = ""
    if request.method == "POST":
        digitado = (request.form.get("usuario") or request.form.get("email") or "").strip()
        senha = request.form.get("senha") or ""
        chave_ip = f"login-ip:{ip_cliente()}"
        if limitador.excedeu(chave_ip, 20, 900):
            erro = "Muitas tentativas a partir deste computador. Aguarde 15 minutos."
        elif not digitado or not senha:
            erro = "Informe o usuário e a senha."
        else:
            # primeiro as contas locais (emergência ou teste); se não houver, o usuário do Windows
            resposta, erro = _entrar_local(digitado, senha)
            if not resposta and not erro:
                if svc_ad.ativo() and not conexao_segura():
                    erro = ("Por segurança, a senha do Windows só pode ser usada pelo endereço com HTTPS. "
                            "Fale com a TI.")
                elif svc_ad.ativo():
                    try:
                        usuario = svc_ad.entrar(digitado, senha)
                    except ErroNegocio as falha:
                        erro = str(falha)
                        auditar("login_recusado", "usuario", None, {"usuario": digitado[:80], "metodo": "AD"})
                    else:
                        limitador.limpar(chave_ip)
                        return _depois_de_entrar(usuario, "AD")
                elif "@" in digitado:
                    erro = "Usuário ou senha incorretos."
                else:
                    erro = "A entrada com o usuário do Windows ainda não foi ligada. Fale com a TI."
            if resposta:
                limitador.limpar(chave_ip)
                return resposta
            limitador.registrar(chave_ip)
    return render_template("auth/entrar.html", erro=erro, digitado=digitado, ad_ativo=svc_ad.ativo())


@bp.post("/sair")
def sair():
    usuario = g.get("usuario")
    if usuario:
        auditar("logout", "usuario", usuario["id"])
    session.clear()
    flash("Você saiu do sistema.", "info")
    return redirect(url_for("auth.entrar"))


@bp.route("/instalacao", methods=["GET", "POST"])
def instalacao():
    if not current_app.config.get("CODIGO_INSTALACAO") or consultar("SELECT id FROM usuarios LIMIT 1", um=True):
        return redirect(url_for("auth.entrar"))
    erros, form = {}, request.form if request.method == "POST" else {
        "url_base": request.host_url.rstrip("/"), "empresa_nome": ""}
    if request.method == "POST":
        if limitador.excedeu(f"instalacao:{ip_cliente()}", 10, 900):
            erros["codigo"] = "Muitas tentativas. Aguarde 15 minutos."
        codigo = (form.get("codigo") or "").strip().upper()
        if codigo != current_app.config["CODIGO_INSTALACAO"]:
            limitador.registrar(f"instalacao:{ip_cliente()}")
            erros["codigo"] = "Código incorreto. Ele aparece no terminal ao iniciar o sistema e no arquivo " \
                              "instance/codigo-instalacao.txt."
        empresa = limpar_texto(form.get("empresa_nome"), 120)
        if not empresa:
            erros["empresa_nome"] = "Informe o nome da empresa."
        url = (form.get("url_base") or "").strip().rstrip("/")
        if not url.startswith(("http://", "https://")):
            erros["url_base"] = "Informe o endereço completo, começando com http:// ou https://."
        nome = limpar_texto(form.get("nome"), 120)
        if len(nome) < 3:
            erros["nome"] = "Informe o seu nome."
        email = (form.get("email") or "").strip().lower()
        if not email_valido(email):
            erros["email"] = "Informe um e-mail válido."
        senha = form.get("senha") or ""
        problema = problema_senha(senha)
        if problema:
            erros["senha"] = problema
        elif senha != form.get("confirmar"):
            erros["confirmar"] = "As senhas não conferem."
        if not erros:
            with transacao():
                cursor = executar("INSERT INTO usuarios (nome, email, senha_hash, perfil) VALUES (?, ?, ?, 'admin')",
                                  (nome, email, hash_senha(senha)))
                salvar({"empresa_nome": empresa, "url_base": url, "ti_email_contato": email})
            current_app.config["CODIGO_INSTALACAO"] = None
            from pathlib import Path
            (Path(current_app.config["PASTA_DADOS"]) / "codigo-instalacao.txt").unlink(missing_ok=True)
            usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (cursor.lastrowid,), um=True)
            _iniciar_sessao(usuario)
            g.usuario = usuario
            auditar("instalacao", "usuario", usuario["id"], {"empresa": empresa}, usuario=usuario)
            flash("Tudo pronto. Comece cadastrando as unidades da empresa.", "sucesso")
            return redirect(url_for("unidades.lista"))
    return render_template("auth/instalacao.html", erros=erros, form=form)


@bp.route("/minha-conta/senha", methods=["GET", "POST"])
@pedidos_obrigatorio
def trocar_senha():
    if g.usuario.get("origem") == "ad":
        flash("Você entra com a senha do Windows. Para trocar, use Ctrl+Alt+Del > Alterar uma senha no computador.", "info")
        return redirect(pagina_inicial(g.usuario))
    erros = {}
    if request.method == "POST":
        usuario = consultar("SELECT * FROM usuarios WHERE id = ?", (g.usuario["id"],), um=True)
        if not conferir_senha(usuario["senha_hash"], request.form.get("atual") or ""):
            erros["atual"] = "Senha atual incorreta."
        nova = request.form.get("nova") or ""
        problema = problema_senha(nova)
        if problema:
            erros["nova"] = problema
        elif nova != request.form.get("confirmar"):
            erros["confirmar"] = "As senhas não conferem."
        elif conferir_senha(usuario["senha_hash"], nova):
            erros["nova"] = "Escolha uma senha diferente da atual."
        if not erros:
            executar("UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 0, versao_sessao = versao_sessao + 1, "
                     "atualizado_em = datetime('now') WHERE id = ?", (hash_senha(nova), usuario["id"]))
            session["versao_sessao"] = usuario["versao_sessao"] + 1
            auditar("senha_alterada", "usuario", usuario["id"])
            flash("Senha alterada. As outras sessões abertas com a senha antiga foram encerradas.", "sucesso")
            return redirect(pagina_inicial(g.usuario))
    modelo = "pedidos/senha.html" if g.usuario["perfil"] == "colaborador" else "auth/senha.html"
    return render_template(modelo, erros=erros, obrigatoria=g.usuario["deve_trocar_senha"])
