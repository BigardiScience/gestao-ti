"""Backup do banco de dados e dos documentos assinados."""
import re
import secrets
import sqlite3
import zipfile
from pathlib import Path

from flask import current_app

from ..db import conectar
from ..util import agora_local
from .configuracoes import cfg_bool, cfg_int

NOME_VALIDO = re.compile(r"^backup-\d{8}-\d{6}\.zip$")

LEIA_ME = """Backup da Plataforma de Gestão de Celulares Corporativos

Conteúdo:
  celulares.db   banco de dados (SQLite)
  documentos/    termos assinados em PDF
  uploads/       logotipo da empresa

Para restaurar:
  1. Pare a aplicação.
  2. Na pasta de dados (instance/), substitua celulares.db e as pastas documentos/ e uploads/
     pelas deste arquivo. Apague também celulares.db-wal e celulares.db-shm, se existirem.
  3. Mantenha o arquivo chave-mestra.key original (ele não vai no backup por segurança):
     sem ele, os segredos da integração precisam ser cadastrados de novo.
  4. Inicie a aplicação.
"""


def gerar_backup() -> Path:
    pasta = Path(current_app.config["PASTA_BACKUPS"])
    nome = f"backup-{agora_local().strftime('%Y%m%d-%H%M%S')}.zip"
    destino = pasta / nome
    copia = Path(current_app.config["PASTA_TEMP"]) / f"backup-{secrets.token_hex(6)}.db"
    origem = conectar(current_app.config["BANCO"])
    alvo = sqlite3.connect(str(copia))
    try:
        origem.backup(alvo)  # cópia consistente, mesmo com o sistema em uso
    finally:
        alvo.close()
        origem.close()
    try:
        with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as arquivo:
            arquivo.write(copia, "celulares.db")
            for pasta_origem, prefixo in ((current_app.config["PASTA_DOCUMENTOS"], "documentos"),
                                          (current_app.config["PASTA_UPLOADS"], "uploads")):
                raiz = Path(pasta_origem)
                for item in sorted(raiz.rglob("*")):
                    if item.is_file():
                        arquivo.write(item, f"{prefixo}/{item.relative_to(raiz).as_posix()}")
            arquivo.writestr("LEIA-ME.txt", LEIA_ME)
    finally:
        copia.unlink(missing_ok=True)
    aplicar_retencao()
    return destino


def listar() -> list[dict]:
    pasta = Path(current_app.config["PASTA_BACKUPS"])
    itens = []
    for arquivo in sorted(pasta.glob("backup-*.zip"), reverse=True):
        if NOME_VALIDO.match(arquivo.name):
            dados = arquivo.stat()
            itens.append({"nome": arquivo.name, "tamanho": dados.st_size,
                          "data": arquivo.name[7:11] + "-" + arquivo.name[11:13] + "-" + arquivo.name[13:15],
                          "hora": arquivo.name[16:18] + ":" + arquivo.name[18:20]})
    return itens


def caminho(nome: str) -> Path | None:
    if not NOME_VALIDO.match(nome or ""):
        return None
    arquivo = Path(current_app.config["PASTA_BACKUPS"]) / nome
    return arquivo if arquivo.exists() else None


def aplicar_retencao():
    manter = max(1, cfg_int("backup_retencao", 14))
    for antigo in [b["nome"] for b in listar()][manter:]:
        (Path(current_app.config["PASTA_BACKUPS"]) / antigo).unlink(missing_ok=True)


def job_backup() -> str:
    if not cfg_bool("backup_ativo"):
        return "Backup automático desativado."
    return f"Backup {gerar_backup().name} gerado."
