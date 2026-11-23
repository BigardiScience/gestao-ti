"""Plataforma de Gestão de Celulares Corporativos."""
import json
import logging
import secrets
import time
from datetime import timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, flash, g, jsonify, redirect, render_template, request, session, url_for
from flask.sessions import SecureCookieSessionInterface
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

VERSAO = "1.4.9"


class SessaoSegura(SecureCookieSessionInterface):
    """Cookie de sessão marcado como Secure sempre que a requisição chega por HTTPS."""

    def get_cookie_secure(self, app):
        try:
            return bool(app.config.get("COOKIE_SEGURO") or request.is_secure)
        except RuntimeError:
            return bool(app.config.get("COOKIE_SEGURO"))


def create_app(config: dict | None = None) -> Flask:
    from .config import montar_config
    configuracao = montar_config(config)
    app = Flask(__name__, instance_path=str(configuracao["PASTA_DADOS"]))
    app.config.update(configuracao)
    app.config.update(
        SESSION_COOKIE_NAME="celulares_sessao",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(hours=10),
        JSON_AS_ASCII=False,
    )
    app.session_interface = SessaoSegura()
    if app.config["CONFIAR_PROXY"]:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

    _configurar_logs(app)
    _inicializar_banco(app)
    _registrar_template(app)
    _registrar_rotas(app)
    from .estaticos import servir
    app.view_functions["static"] = servir
    _registrar_ganchos(app)
    _registrar_erros(app)
    _registrar_comandos(app)

    if app.config["AGENDADOR_ATIVO"] and not app.config["TESTING"]:
        from .servicos import agendador
        agendador.iniciar(app)
    return app


# ------------------------------------------------------------------ inicialização

def _configurar_logs(app):
    nivel = getattr(logging, str(app.config["LOG_NIVEL"]).upper(), logging.INFO)
    if not app.config["TESTING"]:
        arquivo = RotatingFileHandler(Path(app.config["PASTA_LOGS"]) / "aplicacao.log", maxBytes=5 * 1024 * 1024,
                                      backupCount=5, encoding="utf-8")
        arquivo.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
        arquivo.setLevel(nivel)
        app.logger.addHandler(arquivo)
    app.logger.setLevel(nivel)


def _inicializar_banco(app):
    from .db import conectar, migrar
    from .servicos.configuracoes import garantir_padroes
    conexao = conectar(app.config["BANCO"])
    try:
        migrar(conexao)
        garantir_padroes(conexao)
        tem_usuario = conexao.execute("SELECT COUNT(*) FROM usuarios").fetchone()[0] > 0
    finally:
        conexao.close()
    arquivo = Path(app.config["PASTA_DADOS"]) / "codigo-instalacao.txt"
    if tem_usuario:
        arquivo.unlink(missing_ok=True)
        app.config["CODIGO_INSTALACAO"] = None
        return
    if arquivo.exists():
        codigo = arquivo.read_text(encoding="utf-8").strip()
    else:
        codigo = "-".join(secrets.token_hex(2).upper() for _ in range(3))
        arquivo.write_text(codigo, encoding="utf-8")
    app.config["CODIGO_INSTALACAO"] = codigo
    if not app.config["TESTING"]:
        mensagem = (f"\n{'=' * 64}\n  Primeiro acesso: abra o sistema no navegador e informe o\n"
                    f"  código de instalação {codigo}\n  (ele também está em {arquivo})\n{'=' * 64}\n")
        print(mensagem, flush=True)
        app.logger.warning("Aguardando instalação. Código: %s", codigo)


def _registrar_template(app):
    from . import util, validacao
    from .seguranca import (PERFIS, csrf_token, eh_admin, eh_tecnico, pagina_inicial, pode_cadastrar_computador,
                            pode_editar_unidade, ve_dados_de_rede)
    from .servicos import aparelhos as svc_aparelhos
    from .servicos import chamados as svc_chamados
    from .servicos import computadores as svc_computadores
    from .servicos import termos as svc_termos
    from .servicos.configuracoes import cfg, nome_empresa
    from .servicos.registro import TIPOS_MOVIMENTACAO

    filtros = {
        "data": util.fmt_data, "datahora": util.fmt_datahora, "quando": util.fmt_quando,
        "datahora_seg": lambda v: util.fmt_datahora(v, segundos=True), "relativo": util.tempo_relativo,
        "cpf": validacao.formatar_cpf, "cpf_mascarado": validacao.mascarar_cpf, "cnpj": validacao.formatar_cnpj,
        "telefone": validacao.formatar_telefone, "moeda": validacao.formatar_moeda,
        "email_mascarado": validacao.mascarar_email, "dias": util.dias_desde,
        "tamanho_arquivo": lambda n: f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, n // 1024)} KB",
        "milhar": lambda n: f"{int(n or 0):,}".replace(",", "."),
    }
    filtros["fromjson"] = lambda texto: json.loads(texto) if texto else None
    filtros["processador_curto"] = svc_computadores.processador_curto
    # listas de opções para os campos de seleção
    filtros["opcoes"] = lambda itens, chave="id", texto="nome": [(i[chave], i[texto]) for i in itens]
    filtros["pares"] = lambda itens: [(i, i) for i in itens]
    filtros["opcoes_status"] = lambda chaves: [(c, svc_aparelhos.STATUS[c]) for c in chaves]
    filtros["itens_dict"] = lambda d: list(d.items())
    filtros["quando_frase"] = util.fmt_quando_frase
    filtros["numero_chamado"] = svc_chamados.numero
    filtros["dia_hora"] = util.fmt_dia_hora
    from .servicos import expediente as _expediente
    filtros["duracao_util"] = _expediente.texto_duracao
    filtros["iniciais"] = util.iniciais
    from markupsafe import Markup, escape
    from .estaticos import url_estatico
    from .servicos.pdf import assinatura_svg

    def negrito(texto):
        partes = str(escape(texto or "")).split("**")
        return Markup("".join(f"<strong>{p}</strong>" if i % 2 else p for i, p in enumerate(partes)))

    filtros["negrito"] = negrito
    filtros["assinatura_svg"] = lambda desenho: Markup(assinatura_svg(desenho))
    app.jinja_env.filters.update(filtros)
    # globais ficam visíveis também dentro das macros importadas (_macros.html)
    app.jinja_env.globals.update(
        csrf_token=csrf_token, url_com=util.url_com, agora_bd=util.agora_bd, STATUS_APARELHO=svc_aparelhos.STATUS,
        STATUS_TERMO=svc_termos.STATUS, TIPOS_MOVIMENTACAO=TIPOS_MOVIMENTACAO, eh_admin=eh_admin, cfg=cfg,
        nome_empresa=nome_empresa, PERFIS=PERFIS, plural=util.plural, versao=VERSAO, estatico=url_estatico,
        paragrafos=svc_termos.paragrafos, eh_tecnico=eh_tecnico, pode_editar_unidade=pode_editar_unidade,
        pode_cadastrar_computador=pode_cadastrar_computador, ve_dados_de_rede=ve_dados_de_rede,
        SITUACOES_COMPUTADOR=svc_computadores.SITUACOES, TIPOS_COMPUTADOR=svc_computadores.TIPOS,
        ROTULOS_REDE=svc_computadores.ROTULOS_REDE,
        CATEGORIAS_CHAMADO=svc_chamados.CATEGORIAS, PRIORIDADES_CHAMADO=svc_chamados.PRIORIDADES,
        OPCOES_CATEGORIA_CHAMADO=[(chave, dados[0]) for chave, dados in svc_chamados.CATEGORIAS.items()],
        SITUACOES_CHAMADO=svc_chamados.SITUACOES, SITUACOES_PEDIDO=svc_chamados.SITUACOES_COLABORADOR,
        pagina_inicial=pagina_inicial, eh_ti_chamados=lambda: svc_chamados.eh_ti(g.get("usuario")),
    )
    app.jinja_env.trim_blocks = True
    app.jinja_env.lstrip_blocks = True

    def contagem_alertas():
        from .servicos.notificacoes import contagem_alertas as contar
        if "_alertas" not in g:
            g._alertas = contar()
        return g._alertas

    def contagem_termos_pendentes():
        if "_termos_pendentes" not in g:
            g._termos_pendentes = svc_termos.contagens()["pendente"]
        return g._termos_pendentes

    def contagem_chamados():
        """Número do menu "Chamados": novos sem responsável + respostas de colaborador ainda não vistas."""
        if "_chamados_novos" not in g:
            if svc_chamados.eh_ti(g.get("usuario")):
                dados = svc_chamados.contador(g.usuario)
                g._chamados_novos = dados["novos"] + dados["respostas"]
            else:
                g._chamados_novos = 0
        return g._chamados_novos

    def respostas_novas_pedidos():
        """Selo do botão "Meus pedidos": respostas da TI que a pessoa ainda não viu."""
        if "_respostas_novas" not in g:
            g._respostas_novas = sum(svc_chamados.nao_lidas_do_colaborador(g.usuario).values()) if g.get("usuario") else 0
        return g._respostas_novas

    def nome_unidade(unidade_id):
        if not unidade_id:
            return ""
        from .db import escalar
        return escalar("SELECT nome FROM unidades WHERE id = ?", (unidade_id,)) or ""

    @app.context_processor
    def contexto():
        return {
            "usuario": g.get("usuario"), "colaborador_portal": g.get("colaborador_portal"),
            "contagem_alertas": contagem_alertas, "contagem_termos_pendentes": contagem_termos_pendentes,
            "contagem_chamados": contagem_chamados, "nome_unidade": nome_unidade,
            "respostas_novas_pedidos": respostas_novas_pedidos,
        }


def _registrar_rotas(app):
    from .rotas import (aparelhos, assinatura, auth, chamados, colaboradores, computadores, configuracoes,
                        movimentacoes, painel, pedidos, portal, relatorios, termos, unidades, vinculos)
    for modulo in (auth, painel, aparelhos, colaboradores, unidades, vinculos, termos, movimentacoes, relatorios,
                   configuracoes, assinatura, portal, computadores, pedidos, chamados):
        app.register_blueprint(modulo.bp)


SEM_RENOVAR_SESSAO = {"chamados.contador", "chamados.novidades", "pedidos.novidades", "pedidos.nao_lidas"}


def _registrar_ganchos(app):
    from . import util
    from .db import consultar, fechar_db
    from .seguranca import verificar_csrf

    @app.before_request
    def carregar_sessao():
        g.usuario = None
        g.colaborador_portal = None
        if request.endpoint == "static":
            return
        usuario_id = session.get("usuario_id")
        if usuario_id and _sessao_inativa():
            minutos = _limite_inatividade()
            session.pop("usuario_id", None)
            session.pop("ativo_em", None)
            flash(f"Sua sessão foi encerrada depois de {minutos} minutos sem uso. Entre novamente.", "info")
            usuario_id = None
        if usuario_id:
            usuario = consultar("SELECT id, nome, email, perfil, origem, login_ad, unidade_id, setor, ativo, "
                                "deve_trocar_senha, versao_sessao FROM usuarios WHERE id = ?", (usuario_id,), um=True)
            if usuario and usuario["ativo"] and usuario["versao_sessao"] == session.get("versao_sessao"):
                if usuario["perfil"] in ("tecnico", "consulta"):
                    unidades = {l["unidade_id"] for l in consultar(
                        "SELECT unidade_id FROM usuarios_unidades WHERE usuario_id = ? "
                        "AND (expira_em IS NULL OR expira_em > ?)", (usuario_id, util.agora_bd()))}
                    # técnico: as unidades dele (nunca "todas"); consulta sem unidades marcadas = todas
                    usuario["unidades"] = unidades if usuario["perfil"] == "tecnico" else (unidades or None)
                g.usuario = usuario
            else:
                session.pop("usuario_id", None)
        colaborador_id = session.get("colaborador_id")
        if colaborador_id:
            colaborador = consultar("SELECT * FROM colaboradores WHERE id = ? AND situacao = 'ativo'",
                                    (colaborador_id,), um=True)
            if colaborador:
                g.colaborador_portal = colaborador
            else:
                session.pop("colaborador_id", None)

    def _limite_inatividade() -> int:
        # máquinas compartilhadas do chão de fábrica: a sessão do colaborador dura menos
        if session.get("sessao_curta"):
            return int(app.config["SESSAO_INATIVIDADE_COLABORADOR_MIN"])
        return int(app.config["SESSAO_INATIVIDADE_MIN"])

    def _sessao_inativa() -> bool:
        """Encerra a sessão após o tempo configurado sem uso (ISO 27001 e TISAX: bloqueio por inatividade)."""
        agora = int(time.time())
        ultimo = session.get("ativo_em")
        limite = _limite_inatividade() * 60
        if ultimo and limite > 0 and agora - int(ultimo) > limite:
            return True
        if request.endpoint in SEM_RENOVAR_SESSAO:  # o contador automático da fila não conta como uso
            return False
        if not ultimo or agora - int(ultimo) >= 30:  # regrava no máximo a cada 30 s
            session["ativo_em"] = agora
        return False

    @app.before_request
    def proteger():
        if request.endpoint == "static":
            return None
        verificar_csrf()
        if app.config.get("CODIGO_INSTALACAO") and request.endpoint not in ("auth.instalacao", "static", "saude"):
            if consultar("SELECT id FROM usuarios LIMIT 1", um=True):
                app.config["CODIGO_INSTALACAO"] = None
            else:
                return redirect(url_for("auth.instalacao"))
        usuario = g.get("usuario")
        if usuario and usuario["deve_trocar_senha"] and request.endpoint not in (
                "auth.trocar_senha", "auth.sair", "static"):
            return redirect(url_for("auth.trocar_senha"))
        return None

    @app.after_request
    def cabecalhos(resposta):
        politica = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self'; script-src 'self'; "
                    "object-src 'none'; base-uri 'self'; frame-ancestors 'self'; frame-src 'self'; "
                    "form-action 'self' https://login.microsoftonline.com")
        resposta.headers.setdefault("Content-Security-Policy", politica)
        resposta.headers["X-Content-Type-Options"] = "nosniff"
        resposta.headers["Referrer-Policy"] = "no-referrer"
        resposta.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resposta.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        if request.is_secure:
            resposta.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            resposta.headers["Cache-Control"] = "no-store"
        return resposta

    app.teardown_appcontext(fechar_db)

    @app.get("/saude")
    def saude():
        consultar("SELECT 1")
        return jsonify(status="ok", versao=VERSAO)


def _registrar_erros(app):
    mensagens = {
        400: ("Não foi possível concluir", "A requisição não pôde ser processada."),
        403: ("Acesso restrito", "Seu perfil não tem permissão para esta área."),
        404: ("Página não encontrada", "O endereço não existe ou o registro não está disponível para você."),
        405: ("Ação não permitida", "Esta página não aceita esse tipo de envio."),
        413: ("Arquivo grande demais", "Envie um arquivo de até 10 MB."),
        500: ("Algo deu errado", "Ocorreu um erro inesperado. O problema foi registrado no log do sistema."),
    }

    @app.errorhandler(HTTPException)
    def erro_http(erro):
        titulo, texto = mensagens.get(erro.code, (erro.name, erro.description))
        if erro.code in (400, 403) and erro.description and "browser (or proxy)" not in erro.description \
                and "You don't have the permission" not in erro.description:
            texto = erro.description
        if request.accept_mimetypes.best == "application/json":
            return jsonify(erro=texto), erro.code
        return render_template("erros/erro.html", codigo=erro.code, titulo=titulo, texto=texto), erro.code

    @app.errorhandler(Exception)
    def erro_interno(erro):
        app.logger.exception("Erro não tratado em %s", request.path)
        titulo, texto = mensagens[500]
        return render_template("erros/erro.html", codigo=500, titulo=titulo, texto=texto), 500


def _registrar_comandos(app):
    import click

    @app.cli.command("executar-tarefas")
    @click.option("--tarefa", multiple=True, help="Executa só esta tarefa (lembretes, resumo_diario, backup, limpeza).")
    def executar_tarefas(tarefa):
        """Executa as tarefas automáticas vencidas (para uso com cron/agendador)."""
        from .servicos import agendador
        for nome, resultado in agendador.executar_pendentes(app, forcar=list(tarefa) or None):
            click.echo(f"{nome}: {resultado}")

    @app.cli.command("redefinir-senha")
    @click.argument("email")
    def redefinir_senha(email):
        """Gera uma senha temporária para o usuário (use se perder o acesso de administrador)."""
        from .db import executar
        from .seguranca import hash_senha, senha_temporaria
        temporaria = senha_temporaria()
        with app.app_context():
            cursor = executar("UPDATE usuarios SET senha_hash = ?, deve_trocar_senha = 1, tentativas_falhas = 0, "
                              "bloqueado_ate = NULL, ativo = 1, versao_sessao = versao_sessao + 1 "
                              "WHERE email = ? COLLATE NOCASE", (hash_senha(temporaria), email))
            if cursor.rowcount:
                click.echo(f"Senha temporária de {email}: {temporaria}")
            else:
                click.echo("Usuário não encontrado.")

    @app.cli.command("dados-exemplo")
    def dados_exemplo():
        """Cria dados fictícios para conhecer o sistema (só com o banco vazio)."""
        from .demo import popular
        with app.test_request_context("/"):
            try:
                click.echo(popular())
            except RuntimeError as erro:
                raise click.ClickException(str(erro)) from None

    @app.cli.command("backup")
    def backup():
        """Gera um backup agora."""
        from .servicos.backup import gerar_backup
        with app.app_context():
            click.echo(f"Backup gerado: {gerar_backup()}")
