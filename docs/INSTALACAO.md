# Instalação em servidor

O sistema é uma aplicação Python que escuta numa porta (padrão 8000). Em produção, coloque-o atrás de um
proxy reverso com HTTPS (nginx no Linux ou IIS no Windows).

**Ponto importante:** os colaboradores abrem o link de assinatura pelo e-mail, muitas vezes no celular e
fora da rede da empresa. O endereço configurado em *Configurações > Geral > Endereço do sistema* precisa
abrir de onde eles estiverem, com **HTTPS** (ex.: `https://celulares.empresa.com.br`). Combine o DNS e o
certificado com quem cuida disso na empresa.

Requisitos: Python 3.10+, 1 vCPU, 1 GB de RAM e alguns GB de disco (cada termo assinado ocupa de 10 a 40 KB).

---

## Opção A: VM Linux (Ubuntu) com PM2 e nginx

### 1. Copiar o projeto e instalar as dependências

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip nginx
sudo mkdir -p /opt/celulares && sudo chown $USER /opt/celulares
# copie o conteúdo do .zip (ou faça git clone) para /opt/celulares
cd /opt/celulares
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.exemplo .env
```

Edite o `.env`:

```ini
HOST=127.0.0.1
PORTA=8000
URL_BASE=https://celulares.empresa.com.br
CONFIAR_PROXY=1        # registra o IP real de quem assina (vem do nginx)
```

### 2. Manter o sistema no ar com PM2

```bash
pm2 start ecosystem.config.js
pm2 save
pm2 logs celulares     # mostra o código de instalação no primeiro início
```

Sem PM2, use o systemd (crie `/etc/systemd/system/celulares.service`):

```ini
[Unit]
Description=Gestao de Celulares Corporativos
After=network.target

[Service]
User=celulares
WorkingDirectory=/opt/celulares
ExecStart=/opt/celulares/.venv/bin/python run.py
Restart=always
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now celulares
journalctl -u celulares -f
```

### 3. nginx com HTTPS

`/etc/nginx/sites-available/celulares`:

```nginx
server {
    listen 80;
    server_name celulares.empresa.com.br;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name celulares.empresa.com.br;

    ssl_certificate     /etc/ssl/celulares/fullchain.pem;
    ssl_certificate_key /etc/ssl/celulares/privkey.pem;

    client_max_body_size 12m;   # planilhas de importação e PDFs para verificação

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
        proxy_set_header X-Forwarded-Port  $server_port;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/celulares /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Com DNS público apontando para o servidor, o Let's Encrypt resolve o certificado:
`sudo apt install certbot python3-certbot-nginx && sudo certbot --nginx -d celulares.empresa.com.br`.

### 4. Primeiro acesso

Abra `https://celulares.empresa.com.br`, informe o código de instalação (está no log e em
`instance/codigo-instalacao.txt`) e crie o administrador. Depois:

1. *Unidades*: cadastre as filiais (ou deixe a importação criar).
2. *Colaboradores* e *Aparelhos*: cadastre ou importe as planilhas atuais (há modelos para baixar).
3. *Termos > Entregas sem termo*: emita e envie os termos de quem já está com aparelho.
4. *Configurações > Outlook*: conecte o Microsoft 365 ([guia](INTEGRACAO-OUTLOOK.md)).

---

## Opção B: Windows Server

1. Instale o Python 3.10+ (marque *Add python.exe to PATH*).
2. Copie o projeto para `C:\Celulares` e rode `iniciar.bat` uma vez (cria o `.venv` e instala tudo).
3. Para rodar como serviço, use o [NSSM](https://nssm.cc/):

   ```bat
   nssm install Celulares "C:\Celulares\.venv\Scripts\python.exe" "C:\Celulares\run.py"
   nssm set Celulares AppDirectory "C:\Celulares"
   nssm start Celulares
   ```

4. Publique com HTTPS pelo IIS (módulos *URL Rewrite* e *Application Request Routing*, proxy reverso para
   `http://127.0.0.1:8000`) e defina `CONFIAR_PROXY=1` no `.env`.

---

## Atualizar para uma versão nova

**Windows (iniciar.bat):** feche a janela preta do sistema, extraia o zip novo por cima da pasta atual
(substituindo os arquivos) e rode o `iniciar.bat` de novo. Se a janela antiga continuar aberta, a versão antiga
segue rodando e as telas aparecem desconfiguradas; o `iniciar.bat` avisa quando a porta já está em uso.
A versão em uso aparece em *Configurações > Geral*.

**Servidor (PM2, systemd ou NSSM):**

1. Gere um backup (*Configurações > Backup > Gerar backup agora*).
2. Pare o sistema (`pm2 stop celulares` ou `nssm stop Celulares`).
3. Substitua os arquivos do projeto, **mantendo** a pasta `instance/` e o arquivo `.env`.
4. `pip install -r requirements.txt` dentro do `.venv`.
5. Inicie de novo. As mudanças no banco são aplicadas automaticamente.

### Depois de atualizar para a 1.3 (computadores)

1. Em *Unidades*, edite cada unidade e informe a **faixa de IP** (ex.: `172.16.161.0/24`). O cadastro de
   computadores passa a recusar IPs de outra rede.
2. Em *Configurações > Usuários*, crie um usuário **Técnico de TI** para cada responsável e marque a unidade
   dele. O técnico vê os computadores de todas as unidades e altera só os da sua.
3. Em *Computadores*, use **⋯ > Importar planilha**, uma planilha por unidade. O botão *Baixar modelo* traz
   as colunas e as listas de valores aceitos.

A sessão é encerrada após 30 minutos sem uso. Para mudar, defina `SESSAO_INATIVIDADE_MIN` no `.env`.

### Depois de atualizar para a 1.4 (chamados)

Siga o [guia de implantação dos chamados](CHAMADOS.md): Active Directory, OU de cada unidade, equipe de TI,
canais do Teams, horário e prazos, e o piloto em Sorocaba. Pontos que mudam para quem já usa o sistema:

- Com o login pelo AD ligado, todos entram com o usuário do Windows. As contas locais criadas antes continuam
  funcionando (entrando pelo e-mail), mas cada uso gera um aviso. Recadastre os técnicos como *Usuário do
  Windows (AD)* e deixe só a conta de emergência como local.
- Atrás do nginx, `CONFIAR_PROXY=1` no `.env` é obrigatório para reconhecer a máquina de quem abre o pedido.
- A tela do colaborador encerra a sessão após 10 minutos sem uso (`SESSAO_INATIVIDADE_COLABORADOR_MIN`).

## Backups fora do servidor

Os backups diários ficam em `instance/backups/`. Copie para outro lugar, por exemplo com cron:

```bash
# todo dia às 3h, envia os backups para um compartilhamento de rede montado em /mnt/backup
0 3 * * * rsync -a /opt/celulares/instance/backups/ /mnt/backup/celulares/
```

Guarde também uma cópia do `instance/chave-mestra.key` em local seguro (cofre de senhas, por exemplo).
Ele não vai no backup de propósito.

## Restaurar um backup

1. Pare o sistema.
2. Descompacte o `.zip` e copie `celulares.db`, `documentos/` e `uploads/` para a pasta `instance/`
   (apague `celulares.db-wal` e `celulares.db-shm`, se existirem).
3. Mantenha o `chave-mestra.key` original e inicie o sistema.

## Tarefas automáticas

Lembretes de assinatura, resumo diário para o TI, backup e limpeza rodam sozinhos dentro do próprio
sistema (`AGENDADOR_ATIVO=1`). Se preferir controlar pelo cron, desligue o agendador interno e use:

```bash
*/30 * * * * cd /opt/celulares && .venv/bin/flask --app wsgi executar-tarefas >> instance/logs/tarefas.log 2>&1
```
