@echo off
setlocal
REM Inicia a plataforma no Windows: cria o ambiente virtual na primeira vez e instala as dependencias.
cd /d "%~dp0"

REM Ambiente virtual criado pela metade (ex.: tentativa sem Python instalado): apaga e cria de novo
if exist .venv if not exist .venv\Scripts\python.exe rmdir /s /q .venv

if not exist .venv\Scripts\python.exe (
  set "PYTHON="
  py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYTHON=py -3"
  if not defined PYTHON python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYTHON=python"
  if not defined PYTHON goto sem_python
  echo Criando o ambiente virtual .venv ...
  call %%PYTHON%% -m venv .venv || goto erro
)

call .venv\Scripts\activate.bat || goto erro
echo Conferindo as dependencias...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt || goto erro
python run.py
pause
exit /b

:sem_python
echo.
echo  Python 3.10 ou mais novo nao foi encontrado neste computador.
echo.
echo  1. Instale o Python pelo site oficial (a pagina vai abrir agora).
echo     No instalador, MARQUE a opcao "Add python.exe to PATH".
echo  2. Se o Windows abrir a Microsoft Store ao digitar "python", desligue o atalho em:
echo     Configuracoes ^> Aplicativos ^> Configuracoes avancadas do aplicativo ^>
echo     Aliases de execucao do aplicativo ^> desative "python.exe" e "python3.exe".
echo  3. Feche esta janela e rode o iniciar.bat de novo.
echo.
start "" https://www.python.org/downloads/windows/
pause
exit /b 1

:erro
echo.
echo  Algo deu errado acima. Tire um print desta janela para analisar.
pause
exit /b 1
