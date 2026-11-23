"""Configurações: empresa, termo, notificações, Outlook, usuários, e-mails, auditoria e backup."""
import re
import secrets
from io import BytesIO
from pathlib import Path

from flask import (Blueprint, Response, current_app, flash, g, redirect, render_template, request, send_file,
                   session, url_for)

from ..db import consultar
from ..seguranca import PERFIS, admin_obrigatorio
from ..servicos import ErroNegocio, ErroValidacao
from ..servicos import ad as svc_ad
from ..servicos import agendador as svc_agendador
from ..servicos import chamados as svc_chamados
from ..servicos import expediente as svc_expediente
from ..servicos import teams as svc_teams
from ..servicos import backup as svc_backup
from ..servicos import email as svc_email
from ..servicos import graph as svc_graph
from ..servicos import termos as svc_termos
from ..servicos import unidades as svc_unidades
from ..servicos import usuarios as svc_usuarios
from ..servicos.configuracoes import (AVISO_PRIVACIDADE_PADRAO, TERMO_TEXTO_PADRAO, TERMO_TITULO_PADRAO, cfg, cfg_int,
                                      salvar, segredo, url_base)
from ..servicos.registro import auditar
from ..util import agora_local, limites_dia_utc, numero_pagina, paginar
from ..validacao import cnpj_valido, email_valido, ler_data, limpar_texto, limpar_texto_longo
from . import filtros_da_url, id_inteiro, ou_404

bp = Blueprint("configuracoes", __name__, url_prefix="/configuracoes")

FUSOS = {
    "America/Sao_Paulo": "Brasília (SP, RJ, MG, Sul, Nordeste, GO, DF)",
    "America/Manaus": "Amazonas (UTC−4)",
    "America/Cuiaba": "Mato Grosso (UTC−4)",
    "America/Campo_Grande": "Mato Grosso do Sul (UTC−4)",
    "America/Porto_Velho": "Rondônia (UTC−4)",
    "America/Boa_Vista": "Roraima (UTC−4)",
    "America/Rio_Branco": "Acre (UTC−5)",
    "America/Noronha": "Fernando de Noronha (UTC−2)",
}
GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
DOMINIO = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")

ACOES_AUDITORIA = {
    "login": "Entrou no sistema", "login_recusado": "Tentativa de acesso recusada", "logout": "Saiu do sistema",
    "instalacao": "Instalação inicial", "senha_alterada": "Alterou a própria senha",
    "senha_redefinida": "Redefiniu a senha de um usuário", "usuario_criado": "Criou usuário",
    "usuario_alterado": "Alterou usuário", "usuario_excluido": "Excluiu usuário", "unidade_criada": "Criou unidade",
    "unidade_alterada": "Alterou unidade", "computador_excluido": "Excluiu computador",
    "computadores_exportados": "Exportou computadores",
    "unidade_excluida": "Excluiu unidade", "aparelho_excluido": "Excluiu aparelho",
    "colaborador_excluido": "Excluiu colaborador", "configuracao_alterada": "Alterou configurações",
    "integracao_alterada": "Alterou a integração com o Outlook", "integracao_testada": "Testou a integração",
    "integracao_desconectada": "Desconectou a integração", "integracao_conectada": "Conectou conta Microsoft",
    "relatorio_exportado": "Exportou relatório", "importacao": "Importou planilha",
    "termo_pdf_baixado": "Baixou termo assinado", "backup_gerado": "Gerou backup", "backup_baixado": "Baixou backup",
    "modelo_termo_alterado": "Alterou o modelo do termo", "tarefa_executada": "Executou tarefa automática",
    "usuario_pre_cadastrado": "Pré-cadastrou pessoa (chamado)", "cobertura_liberada": "Liberou cobertura de unidade",
    "cobertura_encerrada": "Encerrou cobertura de unidade", "cobertura_expirada": "Cobertura de unidade expirou",
    "ad_testado": "Testou o login pelo AD", "unidade_teams_alterado": "Alterou o canal do Teams da unidade",
    "unidade_teams_testado": "Testou o canal do Teams", "chamado_aberto": "Abriu chamado",
    "chamado_assumido": "Assumiu chamado", "chamado_respondido": "Respondeu chamado", "chamado_nota": "Escreveu nota interna",
    "chamado_mensagem": "Solicitante escreveu no chamado", "chamado_situacao": "Mudou a situação do chamado",
    "chamado_alterado": "Alterou chamado (prioridade, categoria ou responsável)", "chamado_transferido": "Transferiu chamado",
    "chamado_reaberto": "Reabriu chamado", "chamado_fechado": "Chamado encerrado automaticamente",
    "chamado_lido": "Leu chamado de segurança da informação", "chamado_anexo_aberto": "Abriu anexo de chamado de segurança",
    "chamado_seguranca_registro": "Atualizou registro de incidente", "chamado_enviado_dpo": "Enviou incidente ao DPO",
}


def _numero(form, campo, minimo, maximo, erros, rotulo):
    try:
        valor = int(form.get(campo) or 0)
    except ValueError:
        valor = -1
    if not minimo <= valor <= maximo:
        erros[campo] = f"{rotulo}: informe um número de {minimo} a {maximo}."
    return valor


# ------------------------------------------------------------------ geral

@bp.route("", methods=["GET", "POST"])
@admin_obrigatorio
def geral():
    erros = {}
    if request.method == "POST":
        form = request.form
        valores = {
            "empresa_nome": limpar_texto(form.get("empresa_nome"), 120),
            "empresa_cnpj": "".join(c for c in (form.get("empresa_cnpj") or "").upper() if c.isalnum()),
            "empresa_cidade": limpar_texto(form.get("empresa_cidade"), 80),
            "ti_email_contato": (form.get("ti_email_contato") or "").strip(),
            "url_base": (form.get("url_base") or "").strip().rstrip("/"),
            "fuso_horario": form.get("fuso_horario") or "America/Sao_Paulo",
            "assinatura_exigir_codigo": "1" if form.get("assinatura_exigir_codigo") else "0",
        }
        if not valores["empresa_nome"]:
            erros["empresa_nome"] = "Informe o nome da empresa."
        if valores["empresa_cnpj"] and not cnpj_valido(valores["empresa_cnpj"]):
            erros["empresa_cnpj"] = "CNPJ inválido."
        if valores["ti_email_contato"] and not email_valido(valores["ti_email_contato"]):
            erros["ti_email_contato"] = "E-mail inválido."
        if not valores["url_base"].startswith(("http://", "https://")):
            erros["url_base"] = "Informe o endereço completo (http:// ou https://)."
        if valores["fuso_horario"] not in FUSOS:
            erros["fuso_horario"] = "Selecione um fuso horário."
        valores["limite_aparelhos_colaborador"] = _numero(form, "limite_aparelhos_colaborador", 0, 50, erros,
                                                          "Limite de aparelhos")
        valores["link_validade_dias"] = _numero(form, "link_validade_dias", 1, 60, erros, "Validade do link")
        valores["dias_aparelho_parado"] = _numero(form, "dias_aparelho_parado", 1, 365, erros, "Aparelho parado")
        logo = request.files.get("logo")
        if logo and logo.filename:
            try:
                valores["logo_arquivo"] = _salvar_logo(logo)
            except ValueError as erro:
                erros["logo"] = str(erro)
        elif form.get("remover_logo"):
            valores["logo_arquivo"] = ""
        if erros:
            flash("Revise os campos destacados.", "erro")
        else:
            salvar(valores)
            auditar("configuracao_alterada", "configuracao", None, {"secao": "geral"})
            flash("Configurações salvas.", "sucesso")
            return redirect(url_for("configuracoes.geral"))
    form = request.form if request.method == "POST" else _valores(
        "empresa_nome", "empresa_cnpj", "empresa_cidade", "ti_email_contato", "url_base", "fuso_horario",
        "assinatura_exigir_codigo", "limite_aparelhos_colaborador", "link_validade_dias", "dias_aparelho_parado")
    return render_template("configuracoes/geral.html", erros=erros, fusos=FUSOS, form=form)


def _valores(*chaves) -> dict:
    return {chave: cfg(chave) for chave in chaves}


def _salvar_logo(arquivo) -> str:
    from PIL import Image, UnidentifiedImageError
    dados = arquivo.read()
    if len(dados) > 2 * 1024 * 1024:
        raise ValueError("O logotipo deve ter até 2 MB.")
    try:
        imagem = Image.open(BytesIO(dados))
        imagem.load()
    except (UnidentifiedImageError, OSError):
        raise ValueError("Envie uma imagem PNG ou JPG.") from None
    if imagem.format not in ("PNG", "JPEG"):
        raise ValueError("Envie uma imagem PNG ou JPG.")
    imagem = imagem.convert("RGBA")
    imagem.thumbnail((600, 200))  # suficiente para o tamanho em que aparece no PDF (55 x 13 mm); deixa o termo menor
    fundo = Image.new("RGBA", imagem.size, (255, 255, 255, 255))
    fundo.alpha_composite(imagem)
    nome = f"logo-{secrets.token_hex(4)}.png"
    destino = Path(current_app.config["PASTA_UPLOADS"]) / nome
    fundo.convert("RGB").save(destino, "PNG", optimize=True)  # regravar remove metadados do arquivo original
    antigo = cfg("logo_arquivo")
    if antigo:
        (Path(current_app.config["PASTA_UPLOADS"]) / antigo).unlink(missing_ok=True)
    return nome


# ------------------------------------------------------------------ modelo do termo

@bp.route("/termo", methods=["GET", "POST"])
@admin_obrigatorio
def termo():
    erros = {}
    form = None
    if request.method == "POST":
        form = request.form
        if form.get("acao") == "restaurar":
            salvar({"termo_titulo": TERMO_TITULO_PADRAO, "termo_texto": TERMO_TEXTO_PADRAO,
                    "aviso_privacidade": AVISO_PRIVACIDADE_PADRAO, "termo_versao": cfg_int("termo_versao", 1) + 1})
            auditar("modelo_termo_alterado", "configuracao", None, {"restaurado": True})
            flash("Texto padrão restaurado. Termos já emitidos não mudam.", "sucesso")
            return redirect(url_for("configuracoes.termo"))
        titulo = limpar_texto(form.get("termo_titulo"), 200)
        texto = limpar_texto_longo(form.get("termo_texto"), 20000)
        aviso = limpar_texto_longo(form.get("aviso_privacidade"), 2000)
        if not titulo:
            erros["termo_titulo"] = "Informe o título do termo."
        if len(texto) < 50:
            erros["termo_texto"] = "O texto do termo está curto demais."
        desconhecidos = svc_termos.campos_desconhecidos(texto)
        if desconhecidos:
            erros["termo_texto"] = "Campos desconhecidos: " + ", ".join("{{" + c + "}}" for c in desconhecidos) + "."
        if form.get("acao") == "previa" and not erros:
            pdf = svc_termos_previa(titulo, texto)
            return Response(pdf, mimetype="application/pdf",
                            headers={"Content-Disposition": 'inline; filename="previa-termo.pdf"'})
        if not erros:
            mudou = titulo != cfg("termo_titulo") or texto != cfg("termo_texto")
            salvar({"termo_titulo": titulo, "termo_texto": texto, "aviso_privacidade": aviso,
                    "termo_versao": cfg_int("termo_versao", 1) + (1 if mudou else 0)})
            auditar("modelo_termo_alterado", "configuracao", None, {"versao": cfg_int("termo_versao", 1)})
            flash("Modelo do termo salvo. Ele vale para os próximos termos; os já emitidos não mudam.", "sucesso")
            return redirect(url_for("configuracoes.termo"))
        flash("Revise os campos destacados.", "erro")
    form = form or _valores("termo_titulo", "termo_texto", "aviso_privacidade")
    return render_template("configuracoes/termo.html", erros=erros, form=form, campos=svc_termos.CAMPOS_MODELO)


def svc_termos_previa(titulo: str, texto: str) -> bytes:
    """PDF de exemplo com dados fictícios, para conferir o texto antes de salvar."""
    from ..servicos.pdf import gerar_termo
    from ..util import agora_bd, data_extenso
    from ..validacao import formatar_cnpj
    valores = {chave: f"[{rotulo}]" for chave, rotulo in svc_termos.CAMPOS_MODELO.items()}
    valores.update({"empresa_nome": cfg("empresa_nome") or "Empresa", "colaborador_nome": "Maria Exemplo da Silva",
                    "colaborador_cpf": "123.456.789-09", "colaborador_cargo": "Analista",
                    "colaborador_departamento": "Financeiro"})
    conteudo = {
        "codigo": "TR-EXEMPLO", "titulo": titulo, "emitido_em": agora_bd(),
        "empresa": {"nome": cfg("empresa_nome") or "Empresa",
                    "cnpj": formatar_cnpj(cfg("empresa_cnpj")) if cfg("empresa_cnpj") else ""},
        "secoes": [
            {"titulo": "Identificação do colaborador", "campos": [["Nome", "Maria Exemplo da Silva"],
                                                                 ["CPF", "123.456.789-09"], ["Cargo", "Analista"],
                                                                 ["Departamento", "Financeiro"], ["Unidade", "Matriz"]]},
            {"titulo": "Aparelho entregue", "campos": [["Marca e modelo", "Samsung Galaxy A54"],
                                                      ["Número de série", "R58T12ABCDE"], ["IMEI", "356938035643809"],
                                                      ["Linha", "(11) 98765-4321"], ["Operadora", "Vivo"],
                                                      ["Estado de conservação", "Novo"], ["Acessórios", "Carregador"]]},
        ],
        "texto": svc_termos.renderizar_texto(texto, valores), "observacoes": "",
        "local_data": f"{cfg('empresa_cidade') or 'Cidade'}, {data_extenso()}",
        "signatario": {"nome": "Maria Exemplo da Silva", "cpf": "123.456.789-09", "email": "maria@empresa.com.br"},
    }
    return gerar_termo(conteudo, previa=True)


# ------------------------------------------------------------------ notificações e tarefas

@bp.route("/notificacoes", methods=["GET", "POST"])
@admin_obrigatorio
def notificacoes():
    erros = {}
    form = None
    if request.method == "POST":
        form = request.form
        valores = {
            "lembrete_ativo": "1" if form.get("lembrete_ativo") else "0",
            "lembrete_horario_comercial": "1" if form.get("lembrete_horario_comercial") else "0",
            "notificar_ti_assinatura": "1" if form.get("notificar_ti_assinatura") else "0",
            "resumo_diario_ativo": "1" if form.get("resumo_diario_ativo") else "0",
            "resumo_diario_hora": (form.get("resumo_diario_hora") or "08:00").strip(),
        }
        valores["lembrete_intervalo_dias"] = _numero(form, "lembrete_intervalo_dias", 1, 30, erros,
                                                     "Intervalo entre lembretes")
        valores["lembrete_maximo"] = _numero(form, "lembrete_maximo", 0, 10, erros, "Número de lembretes")
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", valores["resumo_diario_hora"]):
            erros["resumo_diario_hora"] = "Use o formato HH:MM, por exemplo 08:00."
        emails = [e.strip() for e in re.split(r"[;,\s]+", form.get("notificacao_emails") or "") if e.strip()]
        invalidos = [e for e in emails if not email_valido(e)]
        if invalidos:
            erros["notificacao_emails"] = "E-mails inválidos: " + ", ".join(invalidos)
        valores["notificacao_emails"] = ", ".join(emails)
        if erros:
            flash("Revise os campos destacados.", "erro")
        else:
            salvar(valores)
            svc_agendador.reprogramar(["resumo_diario"])
            auditar("configuracao_alterada", "configuracao", None, {"secao": "notificacoes"})
            flash("Preferências de notificação salvas.", "sucesso")
            return redirect(url_for("configuracoes.notificacoes"))
    form = form or _valores("lembrete_ativo", "lembrete_intervalo_dias", "lembrete_maximo",
                            "lembrete_horario_comercial", "notificar_ti_assinatura", "resumo_diario_ativo",
                            "resumo_diario_hora", "notificacao_emails")
    return render_template("configuracoes/notificacoes.html", erros=erros, form=form,
                           tarefas=svc_agendador.situacao(), destinatarios_padrao=_admins_emails())


def _admins_emails() -> list:
    return [u["email"] for u in consultar("SELECT email FROM usuarios WHERE perfil = 'admin' AND ativo = 1")]


@bp.post("/tarefas/<nome>/executar")
@admin_obrigatorio
def executar_tarefa(nome):
    if nome not in {"lembretes", "resumo_diario", "backup", "limpeza"}:
        return redirect(url_for("configuracoes.notificacoes"))
    resultados = svc_agendador.executar_pendentes(current_app._get_current_object(), forcar=[nome])
    auditar("tarefa_executada", "tarefa", None, {"tarefa": nome})
    for _, resultado in resultados:
        flash(f"Tarefa executada: {resultado}", "info")
    if not resultados:
        flash("A tarefa já está em execução. Tente de novo em alguns minutos.", "aviso")
    return redirect(request.form.get("voltar") == "backup" and url_for("configuracoes.backup")
                    or url_for("configuracoes.notificacoes"))


# ------------------------------------------------------------------ integração com Outlook / Microsoft 365

def _redirect_uri() -> str:
    return f"{url_base()}{url_for('configuracoes.retorno_microsoft')}"


@bp.route("/integracao", methods=["GET", "POST"])
@admin_obrigatorio
def integracao():
    erros, resultado = {}, None
    form = None
    if request.method == "POST":
        form = request.form
        acao = form.get("acao", "salvar")
        if acao == "testar":
            destino = (form.get("destino_teste") or "").strip()
            if destino and not email_valido(destino):
                erros["destino_teste"] = "Informe um e-mail válido para o teste."
            else:
                resultado = svc_graph.testar_conexao(destino or None)
                auditar("integracao_testada", "configuracao", None, {"ok": resultado["ok"]})
        else:
            modo = form.get("email_modo") if form.get("email_modo") in svc_email.MODOS else "simulacao"
            autenticacao = form.get("graph_autenticacao") if form.get("graph_autenticacao") in (
                "aplicativo", "delegado") else "aplicativo"
            tenant = (form.get("graph_tenant_id") or "").strip()
            client_id = (form.get("graph_client_id") or "").strip()
            remetente = (form.get("graph_remetente") or "").strip().lower()
            segredo_novo = (form.get("graph_client_secret") or "").strip()
            if tenant and not (GUID.match(tenant) or DOMINIO.match(tenant)):
                erros["graph_tenant_id"] = "Use o ID do diretório (GUID) ou o domínio, ex.: empresa.onmicrosoft.com."
            if client_id and not GUID.match(client_id):
                erros["graph_client_id"] = "O ID do aplicativo é um GUID, ex.: 1a2b3c4d-...."
            if remetente and not email_valido(remetente):
                erros["graph_remetente"] = "Informe o e-mail da caixa remetente."
            if modo == "graph":
                if not tenant:
                    erros["graph_tenant_id"] = "Obrigatório para enviar pelo Microsoft 365."
                if not client_id:
                    erros["graph_client_id"] = "Obrigatório para enviar pelo Microsoft 365."
                if not segredo_novo and not segredo("graph_client_secret"):
                    erros["graph_client_secret"] = "Informe o segredo do cliente."
                if autenticacao == "aplicativo" and not remetente:
                    erros["graph_remetente"] = "Informe a caixa que vai enviar os e-mails, ex.: celulares@empresa.com.br."
            if erros:
                flash("Revise os campos destacados.", "erro")
            else:
                valores = {"email_modo": modo, "graph_autenticacao": autenticacao, "graph_tenant_id": tenant,
                           "graph_client_id": client_id, "graph_remetente": remetente}
                mudou_credencial = (tenant != cfg("graph_tenant_id") or client_id != cfg("graph_client_id")
                                    or autenticacao != cfg("graph_autenticacao") or bool(segredo_novo))
                if segredo_novo:
                    valores["graph_client_secret"] = segredo_novo
                if mudou_credencial:
                    valores.update({"graph_status": "nao_testado", "graph_status_mensagem": ""})
                    if autenticacao == "aplicativo" or tenant != cfg("graph_tenant_id") or client_id != cfg("graph_client_id"):
                        valores.update({"graph_refresh_token": "", "graph_conta": ""})
                salvar(valores)
                svc_graph.limpar_cache()
                auditar("integracao_alterada", "configuracao", None,
                        {"modo": modo, "autenticacao": autenticacao, "remetente": remetente})
                flash("Integração salva. Use “Testar conexão” para confirmar o envio." if modo == "graph" else
                      "Modo de simulação ativado: os e-mails ficam só no histórico.", "sucesso")
                return redirect(url_for("configuracoes.integracao"))
    if not form or form.get("acao") == "testar":
        form = dict(_valores("email_modo", "graph_autenticacao", "graph_tenant_id", "graph_client_id",
                             "graph_remetente"), destino_teste=(form or {}).get("destino_teste", g.usuario["email"]))
    return render_template("configuracoes/integracao.html", erros=erros, form=form, resultado=resultado,
                           tem_segredo=bool(cfg("graph_client_secret")), redirect_uri=_redirect_uri(),
                           modos=svc_email.MODOS)


@bp.get("/integracao/conectar")
@admin_obrigatorio
def conectar_microsoft():
    c = svc_graph.configuracao()
    if not (c["tenant"] and c["client_id"] and c["client_secret"]):
        flash("Preencha e salve o ID do diretório, o ID do aplicativo e o segredo antes de conectar a conta.", "erro")
        return redirect(url_for("configuracoes.integracao"))
    estado, verificador = secrets.token_urlsafe(24), secrets.token_urlsafe(48)
    session["oauth_microsoft"] = {"estado": estado, "verificador": verificador}
    return redirect(svc_graph.url_autorizacao(_redirect_uri(), estado, verificador))


@bp.get("/integracao/retorno")
@admin_obrigatorio
def retorno_microsoft():
    dados = session.pop("oauth_microsoft", None)
    if request.args.get("error"):
        flash(f"A Microsoft não autorizou a conexão: {request.args.get('error_description', request.args['error'])[:300]}",
              "erro")
        return redirect(url_for("configuracoes.integracao"))
    if not dados or not secrets.compare_digest(dados["estado"], request.args.get("state", "")):
        flash("A resposta da Microsoft não confere com o pedido de conexão. Tente conectar de novo.", "erro")
        return redirect(url_for("configuracoes.integracao"))
    try:
        conta = svc_graph.concluir_autorizacao(request.args.get("code", ""), _redirect_uri(), dados["verificador"])
    except svc_graph.ErroGraph as erro:
        flash(erro.mensagem, "erro")
    else:
        salvar({"graph_status": "nao_testado", "graph_status_mensagem": ""})
        auditar("integracao_conectada", "configuracao", None, {"conta": conta})
        flash(f"Conta {conta} conectada. Agora teste a conexão.", "sucesso")
    return redirect(url_for("configuracoes.integracao"))


@bp.post("/integracao/desconectar")
@admin_obrigatorio
def desconectar_microsoft():
    salvar({"graph_client_secret": "", "graph_refresh_token": "", "graph_conta": "", "graph_status": "nao_configurado",
            "graph_status_mensagem": "", "email_modo": "simulacao"})
    svc_graph.limpar_cache()
    auditar("integracao_desconectada", "configuracao", None)
    flash("Integração desconectada. O segredo foi apagado e o sistema voltou ao modo de simulação.", "sucesso")
    return redirect(url_for("configuracoes.integracao"))


# ------------------------------------------------------------------ Active Directory

@bp.route("/ad", methods=["GET", "POST"])
@admin_obrigatorio
def active_directory():
    erros, resultado = {}, None
    form = None
    if request.method == "POST":
        form = request.form
        servidores = [p for p in re.split(r"[\s,;]+", form.get("ad_servidores") or "") if p]
        dominio = (form.get("ad_dominio") or "").strip().lower().strip(".")
        valores = {
            "ad_ativo": "1" if form.get("ad_ativo") else "0",
            "ad_dominio": dominio,
            "ad_servidores": "\n".join(servidores),
            "ad_verificar_certificado": "1" if form.get("ad_verificar_certificado") else "0",
            "ad_ca_pem": (form.get("ad_ca_pem") or "").strip(),
        }
        valores["ad_porta"] = _numero(form, "ad_porta", 1, 65535, erros, "Porta")
        valores["ad_timeout"] = _numero(form, "ad_timeout", 2, 30, erros, "Tempo limite")
        if dominio and not DOMINIO.match(dominio):
            erros["ad_dominio"] = "Domínio inválido. Ex.: empresa.local"
        invalidos = [p for p in servidores if not re.fullmatch(r"[A-Za-z0-9.-]+(:\d{1,5})?", p)]
        if invalidos:
            erros["ad_servidores"] = "Nomes inválidos: " + ", ".join(invalidos)
        if valores["ad_ativo"] == "1" and (not dominio or not servidores):
            erros["ad_ativo"] = "Para ligar, informe o domínio e pelo menos um controlador."
        if valores["ad_ca_pem"] and "BEGIN CERTIFICATE" not in valores["ad_ca_pem"]:
            erros["ad_ca_pem"] = "Cole o certificado no formato PEM (começa com -----BEGIN CERTIFICATE-----)."
        if valores["ad_porta"] == 389 and not erros:
            erros["ad_porta"] = "A porta 389 é LDAP sem criptografia. Use LDAPS (636)."
        if erros:
            flash("Revise os campos destacados.", "erro")
        else:
            salvar(valores)
            auditar("configuracao_alterada", "configuracao", None,
                    {"secao": "active_directory", "ativo": valores["ad_ativo"], "dominio": dominio,
                     "servidores": servidores, "verificar_certificado": valores["ad_verificar_certificado"]})
            flash("Configuração do Active Directory salva.", "sucesso")
            return redirect(url_for("configuracoes.active_directory"))
    form = form or _valores("ad_ativo", "ad_dominio", "ad_servidores", "ad_porta", "ad_verificar_certificado",
                            "ad_ca_pem", "ad_timeout")
    unidades = consultar("SELECT id, nome, ou_ad, ativo, chamados_ativo FROM unidades ORDER BY nome COLLATE NOCASE")
    return render_template("configuracoes/ad.html", erros=erros, form=form, unidades=unidades, resultado=resultado)


@bp.post("/ad/testar")
@admin_obrigatorio
def testar_ad():
    login = request.form.get("teste_login") or ""
    try:
        resultado = svc_ad.testar(login, request.form.get("teste_senha") or "")
    except ErroNegocio as erro:
        flash(f"O teste falhou: {erro}", "erro")
        resultado = None
    auditar("ad_testado", "configuracao", None, {"usuario": login[:80], "ok": bool(resultado)})
    form = _valores("ad_ativo", "ad_dominio", "ad_servidores", "ad_porta", "ad_verificar_certificado", "ad_ca_pem",
                    "ad_timeout")
    unidades = consultar("SELECT id, nome, ou_ad, ativo, chamados_ativo FROM unidades ORDER BY nome COLLATE NOCASE")
    return render_template("configuracoes/ad.html", erros={}, form=form, unidades=unidades, resultado=resultado,
                           teste_login=login)


# ------------------------------------------------------------------ chamados

CHAVES_CHAMADOS = ("chamados_dias", "chamados_inicio", "chamados_fim", "chamados_feriados", "chamados_carnaval",
                   "chamados_corpus_christi", "chamados_prazo_alta", "chamados_prazo_media", "chamados_prazo_baixa",
                   "chamados_fechar_resolvido_dias", "chamados_fechar_aguardando_dias", "chamados_resumo_ativo",
                   "chamados_resumo_hora", "chamados_encarregado_email", "chamados_contato_ti")


@bp.route("/chamados", methods=["GET", "POST"])
@admin_obrigatorio
def chamados():
    erros = {}
    form = None
    if request.method == "POST":
        form = request.form
        hora = re.compile(r"([01]\d|2[0-3]):[0-5]\d")
        dias = sorted({d for d in form.getlist("chamados_dias") if d in "1234567" and len(d) == 1})
        valores = {
            "chamados_dias": ",".join(dias),
            "chamados_inicio": (form.get("chamados_inicio") or "").strip(),
            "chamados_fim": (form.get("chamados_fim") or "").strip(),
            "chamados_carnaval": "1" if form.get("chamados_carnaval") else "0",
            "chamados_corpus_christi": "1" if form.get("chamados_corpus_christi") else "0",
            "chamados_resumo_ativo": "1" if form.get("chamados_resumo_ativo") else "0",
            "chamados_resumo_hora": (form.get("chamados_resumo_hora") or "08:00").strip(),
            "chamados_encarregado_email": (form.get("chamados_encarregado_email") or "").strip().lower(),
            "chamados_contato_ti": limpar_texto(form.get("chamados_contato_ti"), 120),
        }
        if not dias:
            erros["chamados_dias"] = "Marque pelo menos um dia de atendimento."
        for chave in ("chamados_inicio", "chamados_fim", "chamados_resumo_hora"):
            if not hora.fullmatch(valores[chave]):
                erros[chave] = "Use o formato HH:MM, por exemplo 08:00."
        if not erros and valores["chamados_fim"] <= valores["chamados_inicio"]:
            erros["chamados_fim"] = "O fim do expediente precisa ser depois do início."
        datas, invalidas = svc_expediente.ler_datas(form.get("chamados_feriados"))
        if invalidas:
            erros["chamados_feriados"] = "Datas inválidas: " + ", ".join(invalidas[:5]) + ". Use DD/MM/AAAA, uma por linha."
        valores["chamados_feriados"] = "\n".join(d.strftime("%d/%m/%Y") for d in datas)
        for prioridade, rotulo in svc_chamados.PRIORIDADES.items():
            numero = _numero(form, f"prazo_{prioridade}_n", 1, 999, erros, f"Prazo {rotulo}")
            unidade = form.get(f"prazo_{prioridade}_u") if form.get(f"prazo_{prioridade}_u") in ("h", "d") else "h"
            valores[f"chamados_prazo_{prioridade}"] = f"{numero}{unidade}"
            if f"prazo_{prioridade}_n" in erros:
                erros[f"chamados_prazo_{prioridade}"] = erros.pop(f"prazo_{prioridade}_n")
        valores["chamados_fechar_resolvido_dias"] = _numero(form, "chamados_fechar_resolvido_dias", 1, 30, erros,
                                                            "Encerrar resolvidos")
        valores["chamados_fechar_aguardando_dias"] = _numero(form, "chamados_fechar_aguardando_dias", 1, 60, erros,
                                                             "Encerrar sem resposta")
        if valores["chamados_encarregado_email"] and not email_valido(valores["chamados_encarregado_email"]):
            erros["chamados_encarregado_email"] = "E-mail inválido."
        if erros:
            flash("Revise os campos destacados.", "erro")
        else:
            salvar(valores)
            svc_agendador.reprogramar(["chamados_resumo"])
            auditar("configuracao_alterada", "configuracao", None, {"secao": "chamados"})
            flash("Configurações dos chamados salvas. Os prazos novos valem para os próximos chamados.", "sucesso")
            return redirect(url_for("configuracoes.chamados"))
    form = form or _valores(*CHAVES_CHAMADOS)
    prazos = {}
    for prioridade in svc_chamados.PRIORIDADES:
        if request.method == "POST":
            prazos[prioridade] = (request.form.get(f"prazo_{prioridade}_n"), request.form.get(f"prazo_{prioridade}_u"))
        else:
            quantidade, unidade = svc_expediente.ler_duracao(form.get(f"chamados_prazo_{prioridade}"))
            prazos[prioridade] = (str(quantidade), unidade)
    exp = svc_expediente.atual()
    ano = agora_local().year
    feriados = [(d, nome) for d, nome in exp.feriados(ano).items() if d >= agora_local().date()][:14]
    canais = consultar("SELECT id, nome, teams_webhook IS NOT NULL AND teams_webhook <> '' AS tem_link, teams_tambem, "
                       "chamados_ativo, ou_ad FROM unidades WHERE ativo = 1 ORDER BY nome COLLATE NOCASE")
    nomes = {c["id"]: c["nome"] for c in canais}
    for canal in canais:
        canal["tambem"] = [nomes[i] for i in svc_teams.ids_tambem(canal["teams_tambem"]) if i in nomes]
    return render_template("configuracoes/chamados.html", erros=erros, form=form, prazos=prazos, feriados=feriados,
                           dias_semana=svc_expediente.DIAS_SEMANA, canais=canais,
                           falhas_teams=svc_teams.falhas_recentes(), PRIORIDADES=svc_chamados.PRIORIDADES)


# ------------------------------------------------------------------ usuários

@bp.get("/usuarios")
@admin_obrigatorio
def usuarios():
    if request.args.get("lista") == "colaboradores":
        busca = (request.args.get("q") or "").strip()[:80]
        return render_template("configuracoes/usuarios.html", lista="colaboradores", busca=busca,
                               pagina=svc_usuarios.listar_colaboradores(busca, numero_pagina()),
                               usuarios=[])
    return render_template("configuracoes/usuarios.html", lista="ti", usuarios=svc_usuarios.listar_ti(),
                           ad_ativo=svc_ad.ativo())


@bp.get("/usuarios/acessos")
@admin_obrigatorio
def acessos():
    return render_template("configuracoes/acessos.html", relatorio=svc_usuarios.relatorio_acessos(),
                           ACOES=ACOES_AUDITORIA, gerado_em=agora_local())


@bp.route("/usuarios/novo", methods=["GET", "POST"])
@admin_obrigatorio
def usuario_novo():
    erros = {}
    form = {"perfil": request.args.get("perfil") if request.args.get("perfil") in PERFIS else "tecnico",
            "origem": "local" if request.args.get("origem") == "local" else "ad", "acesso": "todas"}
    if request.method == "POST":
        form = request.form
        try:
            _, temporaria = svc_usuarios.criar(form, g.usuario["id"])
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        else:
            if temporaria:
                entra_com = (form.get("login_ad") or form.get("email") or "").strip().lower()
                flash(f"Conta local criada. Usuário para entrar: {entra_com}. Senha temporária: {temporaria} — "
                      "no primeiro acesso a pessoa cria a própria senha.", "senha")
            else:
                flash("Pronto. No primeiro acesso com o usuário do Windows, a pessoa já entra com este perfil.", "sucesso")
            return redirect(url_for("configuracoes.usuarios"))
    return render_template("configuracoes/usuario_form.html", erros=erros, form=form, registro=None,
                           unidades=svc_unidades.listar(), **_unidades_marcadas(None))


@bp.route("/usuarios/<int:usuario_id>/editar", methods=["GET", "POST"])
@admin_obrigatorio
def usuario_editar(usuario_id):
    registro = ou_404(svc_usuarios.obter(usuario_id))
    erros = {}
    form = dict(registro, ativo="1" if registro["ativo"] else "0", email=registro["email"] or "",
                login_ad=registro["login_ad"] or "", acesso="selecionadas" if registro["unidades"] else "todas")
    if request.method == "POST":
        form = request.form
        try:
            svc_usuarios.atualizar(usuario_id, form, g.usuario["id"])
        except ErroValidacao as erro:
            erros = erro.erros
            flash(str(erro), "erro")
        except ErroNegocio as erro:
            flash(str(erro), "erro")
        else:
            flash("Usuário atualizado.", "sucesso")
            return redirect(url_for("configuracoes.usuarios"))
    return render_template("configuracoes/usuario_form.html", erros=erros, form=form, registro=registro,
                           unidades=svc_unidades.listar(), **_unidades_marcadas(registro))


@bp.post("/usuarios/<int:usuario_id>/cobertura")
@admin_obrigatorio
def cobertura_liberar(usuario_id):
    try:
        svc_usuarios.liberar_cobertura(usuario_id, request.form, g.usuario["id"])
    except ErroValidacao as erro:
        flash(" ".join(erro.erros.values()), "erro")
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Cobertura liberada. O acesso termina sozinho na data informada.", "sucesso")
    return redirect(url_for("configuracoes.usuario_editar", usuario_id=usuario_id) + "#cobertura")


@bp.post("/usuarios/<int:usuario_id>/cobertura/<int:unidade_id>/encerrar")
@admin_obrigatorio
def cobertura_encerrar(usuario_id, unidade_id):
    try:
        svc_usuarios.encerrar_cobertura(usuario_id, unidade_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Cobertura encerrada.", "sucesso")
    return redirect(url_for("configuracoes.usuario_editar", usuario_id=usuario_id) + "#cobertura")


def _unidades_marcadas(registro) -> dict:
    """Unidades marcadas no formulário: as do cadastro (GET) ou as enviadas (POST com erro)."""
    if request.method == "POST":
        return {"unidades_marcadas": {id_inteiro(u) for u in request.form.getlist("unidades")},
                "tecnico_marcadas": {id_inteiro(u) for u in request.form.getlist("unidades_tecnico")}}
    proprias = set(registro["unidades"]) if registro else set()
    tecnico = bool(registro and registro["perfil"] == "tecnico")
    return {"unidades_marcadas": set() if tecnico else proprias, "tecnico_marcadas": proprias if tecnico else set()}


@bp.post("/usuarios/<int:usuario_id>/excluir")
@admin_obrigatorio
def usuario_excluir(usuario_id):
    try:
        svc_usuarios.excluir(usuario_id, g.usuario["id"])
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash("Usuário excluído.", "sucesso")
    return redirect(url_for("configuracoes.usuarios"))


@bp.post("/usuarios/<int:usuario_id>/senha")
@admin_obrigatorio
def usuario_senha(usuario_id):
    try:
        temporaria = svc_usuarios.redefinir_senha(usuario_id)
    except ErroNegocio as erro:
        flash(str(erro), "erro")
    else:
        flash(f"Nova senha temporária: {temporaria} — ela será trocada no próximo acesso.", "senha")
    return redirect(request.referrer if request.referrer and "/configuracoes/usuarios" in request.referrer
                    else url_for("configuracoes.usuarios"))


# ------------------------------------------------------------------ e-mails enviados

@bp.get("/emails")
@admin_obrigatorio
def emails():
    filtros = filtros_da_url("finalidade", "status", "q", "de", "ate")
    return render_template("configuracoes/emails.html", pagina=svc_email.listar(filtros), filtros=filtros,
                           finalidades=svc_email.FINALIDADES, status=svc_email.STATUS)


@bp.get("/emails/<int:email_id>")
@admin_obrigatorio
def email_detalhe(email_id):
    return render_template("configuracoes/email_detalhe.html", email=ou_404(svc_email.obter(email_id)),
                           finalidades=svc_email.FINALIDADES, status=svc_email.STATUS)


@bp.get("/emails/<int:email_id>/corpo")
@admin_obrigatorio
def email_corpo(email_id):
    email = ou_404(svc_email.obter(email_id))
    resposta = Response(email["corpo_html"] or "<p>Corpo não registrado.</p>", mimetype="text/html")
    # o e-mail usa estilos embutidos; a página roda isolada (sandbox), sem scripts
    resposta.headers["Content-Security-Policy"] = ("default-src 'none'; style-src 'unsafe-inline'; img-src data: 'self'; "
                                                   "frame-ancestors 'self'; sandbox allow-popups allow-popups-to-escape-sandbox")
    return resposta


# ------------------------------------------------------------------ auditoria

@bp.get("/auditoria")
@admin_obrigatorio
def auditoria():
    filtros = filtros_da_url("acao", "usuario", "de", "ate")
    sql, parametros = "SELECT * FROM auditoria WHERE 1=1", []
    if filtros.get("acao"):
        sql += " AND acao = ?"
        parametros.append(filtros["acao"])
    if id_inteiro(filtros.get("usuario")):
        sql += " AND usuario_id = ?"
        parametros.append(int(filtros["usuario"]))
    for chave, operador, fim in (("de", ">=", False), ("ate", "<=", True)):
        try:
            dia = ler_data(filtros.get(chave))
        except ValueError:
            dia = None
        if dia:
            sql += f" AND ocorrido_em {operador} ?"
            parametros.append(limites_dia_utc(dia, fim=fim))
    pagina = paginar(sql + " ORDER BY ocorrido_em DESC, id DESC", parametros, por_pagina=40)
    return render_template("configuracoes/auditoria.html", pagina=pagina, filtros=filtros, acoes=ACOES_AUDITORIA,
                           usuarios=consultar("SELECT id, nome FROM usuarios ORDER BY nome COLLATE NOCASE"))


# ------------------------------------------------------------------ backup

@bp.route("/backup", methods=["GET", "POST"])
@admin_obrigatorio
def backup():
    erros = {}
    if request.method == "POST":
        form = request.form
        hora = (form.get("backup_hora") or "").strip()
        if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", hora):
            erros["backup_hora"] = "Use o formato HH:MM."
        retencao = _numero(form, "backup_retencao", 1, 365, erros, "Quantidade de backups")
        if not erros:
            salvar({"backup_ativo": "1" if form.get("backup_ativo") else "0", "backup_hora": hora,
                    "backup_retencao": retencao})
            svc_agendador.reprogramar(["backup"])
            auditar("configuracao_alterada", "configuracao", None, {"secao": "backup"})
            flash("Configuração de backup salva.", "sucesso")
            return redirect(url_for("configuracoes.backup"))
        flash("Revise os campos destacados.", "erro")
    tarefa = next((t for t in svc_agendador.situacao() if t["nome"] == "backup"), {})
    form = request.form if request.method == "POST" else _valores("backup_ativo", "backup_hora", "backup_retencao")
    return render_template("configuracoes/backup.html", backups=svc_backup.listar(), erros=erros, tarefa=tarefa,
                           pasta=current_app.config["PASTA_DADOS"], form=form)


@bp.post("/backup/gerar")
@admin_obrigatorio
def backup_gerar():
    arquivo = svc_backup.gerar_backup()
    auditar("backup_gerado", "backup", None, {"arquivo": arquivo.name})
    flash(f"Backup {arquivo.name} gerado.", "sucesso")
    return redirect(url_for("configuracoes.backup"))


@bp.get("/backup/<nome>")
@admin_obrigatorio
def backup_baixar(nome):
    caminho = svc_backup.caminho(nome)
    if not caminho:
        flash("Backup não encontrado.", "erro")
        return redirect(url_for("configuracoes.backup"))
    auditar("backup_baixado", "backup", None, {"arquivo": nome})
    return send_file(caminho, as_attachment=True, download_name=nome, mimetype="application/zip")
