"""Carregamento de configuração da aplicação.

A configuração vem de três lugares, nesta ordem de prioridade:
1. valores passados para create_app() (usado nos testes);
2. variáveis de ambiente (ou o arquivo .env na raiz do projeto);
3. valores padrão definidos aqui.

As preferências que o administrador altera pela tela (nome da empresa,
integração com o Outlook, textos do termo...) ficam no banco de dados,
na tabela "configuracoes" — veja app/servicos/configuracoes.py.
"""
import base64
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RAIZ = Path(__file__).resolve().parent.parent


def ler_arquivo_env(caminho: Path) -> None:
    """Lê um arquivo .env simples (CHAVE=valor) sem sobrescrever o ambiente."""
    if not caminho.exists():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        chave, valor = chave.strip(), valor.strip()
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            valor = valor[1:-1]
        os.environ.setdefault(chave, valor)


def _bool(valor, padrao=False):
    if valor is None or valor == "":
        return padrao
    return str(valor).strip().lower() in ("1", "true", "sim", "yes", "on")


def _derivar(chave_mestra: bytes, finalidade: bytes) -> bytes:
    """Deriva uma chave independente para cada finalidade a partir da chave mestra."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=b"gestao-celulares", info=finalidade
    ).derive(chave_mestra)


def _carregar_chave_mestra(pasta: Path) -> bytes:
    """Usa CHAVE_MESTRA do ambiente ou cria/lê o arquivo chave-mestra.key."""
    do_ambiente = os.environ.get("CHAVE_MESTRA")
    if do_ambiente:
        return do_ambiente.strip().encode()
    arquivo = pasta / "chave-mestra.key"
    if arquivo.exists():
        return arquivo.read_bytes().strip()
    chave = base64.urlsafe_b64encode(secrets.token_bytes(32))
    arquivo.write_bytes(chave)
    try:
        os.chmod(arquivo, 0o600)
    except OSError:
        pass
    return chave


def montar_config(sobrescrever: dict | None = None) -> dict:
    ler_arquivo_env(RAIZ / ".env")
    sobrescrever = dict(sobrescrever or {})

    pasta = Path(sobrescrever.pop("PASTA_DADOS", None) or os.environ.get("PASTA_DADOS") or RAIZ / "instance")
    pasta = pasta.resolve()
    for sub in ("", "documentos", "backups", "logs", "uploads", "temp"):
        (pasta / sub).mkdir(parents=True, exist_ok=True)

    cfg = {
        "PASTA_DADOS": pasta,
        "BANCO": pasta / "celulares.db",
        "PASTA_DOCUMENTOS": pasta / "documentos",
        "PASTA_BACKUPS": pasta / "backups",
        "PASTA_LOGS": pasta / "logs",
        "PASTA_UPLOADS": pasta / "uploads",
        "PASTA_TEMP": pasta / "temp",
        "HOST": os.environ.get("HOST", "0.0.0.0"),
        "PORTA": int(os.environ.get("PORTA", "8000")),
        "URL_BASE": os.environ.get("URL_BASE", "").rstrip("/"),
        "CONFIAR_PROXY": _bool(os.environ.get("CONFIAR_PROXY"), False),
        "COOKIE_SEGURO": _bool(os.environ.get("COOKIE_SEGURO"), False),
        "AGENDADOR_ATIVO": _bool(os.environ.get("AGENDADOR_ATIVO"), True),
        "GRAPH_AUTORIDADE": os.environ.get("GRAPH_AUTORIDADE", "https://login.microsoftonline.com").rstrip("/"),
        "GRAPH_API": os.environ.get("GRAPH_API", "https://graph.microsoft.com/v1.0").rstrip("/"),
        "LOG_NIVEL": os.environ.get("LOG_NIVEL", "INFO"),
        "MAX_CONTENT_LENGTH": 12 * 1024 * 1024,
        # Minutos sem uso até a sessão ser encerrada (0 desliga)
        "SESSAO_INATIVIDADE_MIN": int(os.environ.get("SESSAO_INATIVIDADE_MIN", "30")),
        "SESSAO_INATIVIDADE_COLABORADOR_MIN": int(os.environ.get("SESSAO_INATIVIDADE_COLABORADOR_MIN", "10")),
        # Avisos de chamados (Teams e e-mail): enviados numa thread logo após a ação (os testes enviam na hora)
        "AVISOS_SINCRONOS": False,
        "TESTING": False,
        # Intervalo (segundos) entre envios em lote, para respeitar o limite do Exchange Online
        "INTERVALO_ENVIO_LOTE": float(os.environ.get("INTERVALO_ENVIO_LOTE", "2.5")),
    }
    cfg.update(sobrescrever)

    chave_mestra = _carregar_chave_mestra(pasta)
    cfg["SECRET_KEY"] = _derivar(chave_mestra, b"sessao").hex()
    cfg["CHAVE_CRIPTOGRAFIA"] = base64.urlsafe_b64encode(_derivar(chave_mestra, b"criptografia"))
    cfg["CHAVE_SELO"] = _derivar(chave_mestra, b"selo-assinatura")
    return cfg
