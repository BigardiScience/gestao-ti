"""Preferências do sistema guardadas no banco (editáveis pela tela de Configurações)."""
from flask import g, has_app_context

from ..db import consultar, executar, transacao

TERMO_TITULO_PADRAO = "Termo de responsabilidade pelo uso de aparelho celular corporativo"

TERMO_TEXTO_PADRAO = """Pelo presente termo, eu, {{colaborador_nome}}, inscrito(a) no CPF sob o nº {{colaborador_cpf}}, ocupante do cargo de {{colaborador_cargo}} no departamento {{colaborador_departamento}}, declaro ter recebido de {{empresa_nome}} o aparelho celular corporativo e os acessórios descritos acima, no estado de conservação indicado, e assumo a responsabilidade pela sua guarda e uso nas condições a seguir.

1. O aparelho e a linha telefônica são de propriedade da empresa e destinam-se exclusivamente ao desempenho das minhas atividades profissionais.

2. Comprometo-me a zelar pela conservação do aparelho e dos acessórios, utilizando-os com cuidado e de acordo com as orientações do fabricante e do departamento de TI.

3. Não emprestarei, cederei ou transferirei o aparelho ou a linha a terceiros, nem instalarei aplicativos ou alterarei configurações sem autorização do departamento de TI.

4. Em caso de perda, furto ou roubo, comunicarei imediatamente o departamento de TI e, nos casos de furto ou roubo, registrarei boletim de ocorrência e entregarei uma cópia à empresa.

5. Estou ciente de que danos causados por mau uso, negligência ou dolo poderão ser ressarcidos por mim, nos termos do art. 462, § 1º, da Consolidação das Leis do Trabalho (CLT). O desgaste natural do uso normal não é de minha responsabilidade.

6. Estou ciente de que o aparelho pode ser gerenciado remotamente pela empresa (configuração, atualização, bloqueio e localização em caso de perda) e de que não devo guardar nele informações pessoais que não queira compartilhar com a empresa.

7. Devolverei o aparelho, a linha e os acessórios sempre que solicitado e, obrigatoriamente, em caso de desligamento, afastamento prolongado ou mudança de função que dispense o seu uso.

8. Os dados pessoais informados neste termo serão tratados pela empresa exclusivamente para o controle dos bens corporativos, conforme a Lei Geral de Proteção de Dados (Lei nº 13.709/2018).

9. Concordo com a assinatura eletrônica deste termo e reconheço a sua validade como prova de autoria e integridade, nos termos do art. 10, § 2º, da Medida Provisória nº 2.200-2/2001."""

AVISO_PRIVACIDADE_PADRAO = (
    "Os seus dados são tratados pela empresa apenas para o controle dos aparelhos corporativos, "
    "conforme a LGPD. Ao assinar, registramos a data, a hora, o endereço IP e o navegador usados, "
    "como evidência da assinatura."
)

PADROES = {
    "empresa_nome": "",
    "empresa_cnpj": "",
    "empresa_cidade": "",
    "ti_email_contato": "",
    "url_base": "",
    "fuso_horario": "America/Sao_Paulo",
    "logo_arquivo": "",
    "termo_titulo": TERMO_TITULO_PADRAO,
    "termo_texto": TERMO_TEXTO_PADRAO,
    "termo_versao": "1",
    "aviso_privacidade": AVISO_PRIVACIDADE_PADRAO,
    "link_validade_dias": "7",
    "assinatura_exigir_codigo": "1",
    "limite_aparelhos_colaborador": "0",
    "lembrete_ativo": "1",
    "lembrete_intervalo_dias": "2",
    "lembrete_maximo": "3",
    "lembrete_horario_comercial": "1",
    "notificar_ti_assinatura": "1",
    "resumo_diario_ativo": "1",
    "resumo_diario_hora": "08:00",
    "notificacao_emails": "",
    "dias_aparelho_parado": "30",
    "backup_ativo": "1",
    "backup_hora": "02:30",
    "backup_retencao": "14",
    "email_modo": "simulacao",
    "graph_autenticacao": "aplicativo",
    "graph_tenant_id": "",
    "graph_client_id": "",
    "graph_client_secret": "",
    "graph_remetente": "",
    "graph_refresh_token": "",
    "graph_conta": "",
    "graph_status": "nao_configurado",
    "graph_status_mensagem": "",
    "graph_verificado_em": "",
    # login pelo Active Directory (LDAPS)
    "ad_ativo": "0",
    "ad_dominio": "",
    "ad_servidores": "",
    "ad_porta": "636",
    "ad_verificar_certificado": "1",
    "ad_ca_pem": "",
    "ad_timeout": "5",
    # chamados
    "chamados_dias": "1,2,3,4,5",
    "chamados_inicio": "08:00",
    "chamados_fim": "18:00",
    "chamados_feriados": "",
    "chamados_carnaval": "1",
    "chamados_corpus_christi": "1",
    "chamados_prazo_alta": "4h",
    "chamados_prazo_media": "1d",
    "chamados_prazo_baixa": "3d",
    "chamados_fechar_resolvido_dias": "3",
    "chamados_fechar_aguardando_dias": "5",
    "chamados_resumo_ativo": "1",
    "chamados_resumo_hora": "08:00",
    "chamados_encarregado_email": "",
    "chamados_contato_ti": "",
}

# Guardados cifrados (Fernet) — nunca aparecem na tela nem nos logs
SEGREDOS = {"graph_client_secret", "graph_refresh_token"}


def garantir_padroes(conexao):
    for chave, valor in PADROES.items():
        conexao.execute("INSERT OR IGNORE INTO configuracoes (chave, valor) VALUES (?, ?)", (chave, valor))


def _todas() -> dict:
    if has_app_context() and "_cfg" in g:
        return g._cfg
    valores = dict(PADROES)
    for linha in consultar("SELECT chave, valor FROM configuracoes"):
        valores[linha["chave"]] = linha["valor"] if linha["valor"] is not None else ""
    if has_app_context():
        g._cfg = valores
    return valores


def cfg(chave: str) -> str:
    return _todas().get(chave, PADROES.get(chave, ""))


def cfg_int(chave: str, padrao: int = 0) -> int:
    try:
        return int(cfg(chave))
    except (TypeError, ValueError):
        return padrao


def cfg_bool(chave: str) -> bool:
    return cfg(chave) in ("1", "true", "sim")


def segredo(chave: str) -> str:
    from ..seguranca import decifrar
    return decifrar(cfg(chave))


def salvar(valores: dict):
    from ..seguranca import cifrar
    with transacao():
        for chave, valor in valores.items():
            if chave not in PADROES:
                raise KeyError(f"Configuração desconhecida: {chave}")
            valor = "" if valor is None else str(valor)
            if chave in SEGREDOS and valor:
                valor = cifrar(valor)
            executar(
                "INSERT INTO configuracoes (chave, valor, atualizado_em) VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, atualizado_em = excluded.atualizado_em",
                (chave, valor),
            )
    limpar_cache()


def limpar_cache():
    if has_app_context():
        g.pop("_cfg", None)


def url_base() -> str:
    from flask import current_app, request
    configurada = (cfg("url_base") or current_app.config.get("URL_BASE") or "").rstrip("/")
    if configurada:
        return configurada
    try:
        return request.host_url.rstrip("/")
    except RuntimeError:
        return "http://localhost:8000"


def nome_empresa() -> str:
    return cfg("empresa_nome") or "Empresa"


def emails_notificacao() -> list[str]:
    """Destinatários dos avisos para o TI: lista configurada ou, na falta dela, os administradores ativos."""
    lista = [e.strip() for e in (cfg("notificacao_emails") or "").replace(";", ",").split(",") if e.strip()]
    if lista:
        return lista
    return [u["email"] for u in consultar("SELECT email FROM usuarios WHERE perfil = 'admin' AND ativo = 1")]
