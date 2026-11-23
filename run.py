"""Inicia a Plataforma de Gestão de Celulares Corporativos.

Uso:  python run.py
A porta e o endereço vêm do arquivo .env (PORTA e HOST). Padrão: 0.0.0.0:8000.
"""
import socket
import sys

from app import VERSAO, create_app

app = create_app()


def porta_em_uso(host: str, porta: int) -> bool:
    """Verdadeiro se já há um programa atendendo nessa porta (por exemplo, o sistema aberto em outra janela)."""
    destino = "127.0.0.1" if host in ("0.0.0.0", "") else host
    try:
        with socket.create_connection((destino, porta), timeout=0.5):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    host, porta = app.config["HOST"], app.config["PORTA"]
    endereco = "localhost" if host in ("0.0.0.0", "127.0.0.1") else host
    if porta_em_uso(host, porta):
        print(f"\nA porta {porta} já está em uso: o sistema provavelmente já está aberto em outra janela.")
        print("Feche a outra janela (ou reinicie o computador) e abra de novo. Depois de uma atualização isso é")
        print("obrigatório: a versão antiga continua rodando até ser fechada. Também é possível mudar a PORTA no .env.\n")
        sys.exit(1)
    try:
        from waitress import serve
    except ImportError:
        print("Aviso: o pacote 'waitress' não está instalado; usando o servidor de testes do Flask.")
        print(f"Versão {VERSAO}. Acesse http://{endereco}:{porta}")
        app.run(host=host, port=porta, debug=False, threaded=True)
    else:
        print(f"Plataforma no ar (versão {VERSAO}): http://{endereco}:{porta}  (Ctrl+C para parar)")
        serve(app, host=host, port=porta, threads=12, ident="celulares")
