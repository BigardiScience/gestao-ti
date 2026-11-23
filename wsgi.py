"""Ponto de entrada WSGI (usado por waitress, gunicorn e pelo comando flask)."""
from app import create_app

app = create_app()
