"""Telas da aplicação (blueprints do Flask)."""
from urllib.parse import urlparse

from flask import abort, request


def filtros_da_url(*chaves) -> dict:
    return {chave: request.args.get(chave, "").strip() for chave in chaves if request.args.get(chave, "").strip()}


def id_inteiro(valor) -> int | None:
    try:
        numero = int(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def destino_seguro(destino: str | None, padrao: str) -> str:
    """Evita redirecionamento para outros sites (open redirect)."""
    if not destino:
        return padrao
    partes = urlparse(destino)
    if partes.scheme or partes.netloc or not destino.startswith("/") or destino.startswith("//"):
        return padrao
    return destino


def ou_404(registro):
    if not registro:
        abort(404)
    return registro
