"""Arquivos estáticos (CSS, JavaScript, ícones) com cache longo e compressão gzip.

Cada URL leva uma "impressão digital" do conteúdo (?v=...). Assim o navegador guarda o arquivo por
um ano e só baixa de novo quando ele muda numa atualização do sistema.
"""
import gzip
import hashlib
import os
from functools import lru_cache
from pathlib import Path

from flask import current_app, request, send_from_directory, url_for
from werkzeug.security import safe_join

COMPRIMIR = {"text/css", "text/javascript", "application/javascript", "image/svg+xml"}
UM_ANO = 365 * 24 * 3600


@lru_cache(maxsize=64)
def _impressao(caminho: str, alterado_em: float) -> str:
    return hashlib.sha256(Path(caminho).read_bytes()).hexdigest()[:10]


@lru_cache(maxsize=64)
def _comprimido(caminho: str, alterado_em: float) -> bytes:
    return gzip.compress(Path(caminho).read_bytes(), compresslevel=9, mtime=0)


def url_estatico(arquivo: str) -> str:
    """URL do arquivo com a impressão digital do conteúdo."""
    caminho = safe_join(current_app.static_folder, arquivo)
    try:
        versao = _impressao(caminho, os.path.getmtime(caminho))
    except (OSError, TypeError):
        versao = "0"
    return url_for("static", filename=arquivo, v=versao)


def servir(filename):
    versionado = "v" in request.args
    resposta = send_from_directory(current_app.static_folder, filename, max_age=UM_ANO if versionado else 0)
    if versionado:
        resposta.cache_control.immutable = True
    else:
        resposta.cache_control.no_cache = True
    if (resposta.status_code == 200 and resposta.mimetype in COMPRIMIR
            and "gzip" in request.headers.get("Accept-Encoding", "")):
        caminho = safe_join(current_app.static_folder, filename)
        dados = _comprimido(caminho, os.path.getmtime(caminho))
        if hasattr(resposta.response, "close"):
            resposta.response.close()
        resposta.direct_passthrough = False
        resposta.set_data(dados)
        etiqueta, fraca = resposta.get_etag()
        if etiqueta:
            resposta.set_etag(f"{etiqueta}-gz", weak=fraca)
        resposta.headers["Content-Encoding"] = "gzip"
        resposta.headers["Vary"] = "Accept-Encoding"
    return resposta
