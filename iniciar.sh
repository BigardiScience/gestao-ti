#!/usr/bin/env bash
# Inicia a plataforma no Linux/macOS: cria o ambiente virtual na primeira vez e instala as dependências.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  echo "Criando o ambiente virtual (.venv)..."
  python3 -m venv .venv
fi
. .venv/bin/activate
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt
python run.py
