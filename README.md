# Gestão de TI — computadores e celulares corporativos

Sistema web para o departamento de TI controlar, em todas as unidades:

- **Chamados** (Pedidos para a TI): o colaborador entra com o usuário do Windows (AD), pede ajuda em três
  passos e acompanha a resposta; a TI atende numa fila por unidade, com prazos em horário útil e avisos no
  Teams e no Outlook. Substitui o GLPI ([docs/CHAMADOS.md](docs/CHAMADOS.md));
- **Computadores** (desktops e notebooks): inventário por unidade, rede, acesso remoto, antivírus,
  periféricos, conferência periódica e descarte seguro;
- **Celulares**: inventário, colaboradores responsáveis, termos de responsabilidade com assinatura
  eletrônica, histórico completo, relatórios em Excel e envio de e-mails pelo Outlook / Microsoft 365.

- **Linguagem:** Python 3.10 ou mais novo, com Flask
- **Banco de dados:** SQLite (um arquivo, sem servidor separado; aguenta folgado milhares de aparelhos)
- **Roda em:** Windows ou Linux (Ubuntu), com navegador em computador ou celular
- **Sem dependência de serviços externos**, exceto o Microsoft 365 (e-mails e avisos do Teams) e o Active
  Directory da empresa (login por LDAPS)

---

## Começo rápido (5 minutos)

1. Instale o Python 3.10+ (no Windows, marque *“Add python.exe to PATH”* no instalador).
2. Descompacte esta pasta e dê dois cliques em **`iniciar.bat`** (Windows) ou rode **`./iniciar.sh`** (Linux).
   O script cria o ambiente virtual, instala as dependências e inicia o sistema.
3. O terminal mostra um **código de instalação**. Abra <http://localhost:8000>, informe o código e crie o
   administrador de TI.
4. Para conhecer o sistema com dados fictícios (só funciona com o banco vazio):

   ```bash
   # com o ambiente virtual ativo, na pasta do projeto
   flask --app wsgi dados-exemplo
   ```

   Para recomeçar do zero, pare o sistema e apague a pasta `instance/`.

> Os e-mails começam em **modo de simulação**: nada sai do servidor e cada mensagem (com o link de
> assinatura) fica em *Configurações > E-mails enviados*. Assim dá para testar todos os fluxos antes de
> ligar o Microsoft 365. Veja [docs/INTEGRACAO-OUTLOOK.md](docs/INTEGRACAO-OUTLOOK.md).

Instalação em servidor (VM Ubuntu com PM2 e HTTPS, ou Windows Server): [docs/INSTALACAO.md](docs/INSTALACAO.md).

---

## O que o sistema faz

| Área | Recursos |
|---|---|
| **Chamados** | Login pelo AD (LDAPS, controlador reserva, a senha nunca é guardada). Colaborador: oito botões grandes (computador, celular, impressora, internet, senha ou acesso, programa, e-mail ou site estranho, outra coisa), máquina reconhecida pelo IP do inventário, “Isso está impedindo o seu trabalho?” vira a prioridade, foto ou print, *Meus pedidos* com a situação em frase e “Ainda com problema? Avise a TI”. TI: fila com abas por unidade, indicadores clicáveis, busca, “Meus”/“Sem responsável”, prazos em horário útil (pausam em “Aguardando usuário”), conversa com notas internas, transferência entre unidades, abertura em nome de alguém e chamados na ficha do computador e do celular. Avisos: cartão no Teams por unidade (Santos avisa Sorocaba e Uberaba), lembretes e escalada ao administrador, e-mails ao colaborador e ao responsável, resumo das 8h, aviso do Windows com som. Segurança da informação restrita à unidade, com registro do incidente e prazo da ANPD. |
| **Computadores** | Desktops e notebooks por unidade: nome da máquina, usuário (nome, login, setor e situação: ativo, férias, desligado), sistema, processador, memória, IP e MAC (cabo e Wi-Fi no notebook), SEP, acesso remoto (ferramenta e ID, nunca senha), nobreak, monitor, webcam, headset, softwares, série e patrimônio. IP e MAC não se repetem e o IP precisa ser da faixa da unidade. Indicadores clicáveis (em uso, reserva, manutenção, precisam de atenção), conferência a cada 6 meses, desativação com motivo e destino do disco, histórico de alterações, importação por unidade e exportação para Excel. |
| **Aparelhos** | Cadastro com marca, modelo, nº de série, IMEI (validado), IMEI 2, linha, operadora, unidade, data de aquisição, valor, estado de conservação, acessórios. Status: disponível, em utilização, em manutenção, devolvido, baixado (com motivo). Página de detalhes com responsável atual e histórico completo. Etiqueta em PDF com código de barras do IMEI para imprimir. Busca por leitor de código de barras. |
| **Colaboradores** | Nome, CPF (validado), e-mail corporativo, matrícula, cargo, departamento, unidade, telefone corporativo. Um ou mais aparelhos por pessoa (limite configurável). Desligamento com alerta de devolução pendente. |
| **Movimentações** | Entrega, devolução (com comprovante em PDF), transferência entre colaboradores, substituição (troca de aparelho), mudanças de status. Tudo registrado automaticamente, com quem fez e quando. |
| **Termos de responsabilidade** | Gerados automaticamente na entrega, com texto editável. Revisão antes do envio, envio por e-mail, lembretes automáticos, reenvio, cancelamento, links com validade, emissão em lote para entregas antigas. |
| **Assinatura eletrônica** | Link exclusivo + confirmação de CPF + código enviado ao e-mail + assinatura desenhada (dedo/mouse) ou digitada + aceite. PDF final com página de evidências (IP, navegador, data/hora, trilha de eventos, hashes) e verificação pública de autenticidade. |
| **Portal do colaborador** | Acesso com código enviado ao e-mail corporativo (sem senha). Mostra os aparelhos com a pessoa, permite assinar e baixar os termos. |
| **Painel** | A frota em ícones (um por aparelho, colorido pelo status), cobertura de termos assinados, o que precisa de atenção, distribuição por unidade, últimas movimentações e um guia de primeiros passos enquanto o sistema está vazio. |
| **Relatórios (.xlsx)** | Inventário, em utilização, disponíveis, por unidade (uma aba por filial), colaboradores, termos e movimentações. Filtros (unidade, marca/modelo, status, colaborador, departamento, período, status do termo) e escolha de colunas. |
| **Importação** | Planilhas de aparelhos e colaboradores, com modelo para baixar, validação linha a linha e entregas já existentes (para migrar as planilhas atuais). |
| **Outlook / Microsoft 365** | Microsoft Graph com Entra ID: modo aplicativo (caixa compartilhada, recomendado) ou conta conectada (OAuth com PKCE). Teste de conexão, histórico de e-mails com destinatário, assunto, data e finalidade, tratamento de falhas e novas tentativas. |
| **Notificações** | Lembretes de assinatura, aviso ao TI quando um termo é assinado e resumo diário de pendências (termos aguardando, links expirados, aparelhos sem responsável, devoluções e transferências pendentes). |
| **Acesso** | Todos entram com o usuário do Windows (AD); a conta local fica só para emergência e cada uso gera aviso. **Administrador de TI** (tudo, inclusive usuários), **Técnico de TI** (vê computadores e chamados de todas as unidades e altera só os das unidades que atende, com cobertura de férias com data de fim), **Usuário de consulta** (só visualiza o inventário das unidades liberadas, sem IP, MAC, acesso remoto nem chamados) e **Colaborador** (só os próprios pedidos). As permissões são conferidas no servidor em cada ação. Sessão encerrada após 30 minutos sem uso (10 na tela do colaborador). Revisão de acessos para a auditoria semestral. Termos de celular continuam no portal do colaborador. |
| **Segurança e LGPD** | Senhas com scrypt, bloqueio após tentativas, proteção CSRF, cabeçalhos de segurança (CSP), segredos cifrados, CPF mascarado para quem não é administrador, auditoria de ações, backup diário automático. |

---

## Como a assinatura eletrônica funciona

1. Na emissão, os dados do colaborador e do aparelho são **congelados**: mudanças posteriores no cadastro
   não alteram um termo já emitido. O conteúdo recebe um hash SHA-256.
2. O colaborador recebe no e-mail corporativo um **link exclusivo** (token aleatório; o banco guarda só o
   hash). O link vale pelo prazo configurado (padrão 7 dias) e pode ser reenviado.
3. Para assinar, ele **confirma o CPF**, informa o **código de 6 dígitos** enviado ao e-mail (pode ser
   desligado em Configurações), digita o **nome completo**, **desenha ou digita** a assinatura e **aceita** o termo.
4. O sistema registra data e hora (local e UTC), IP, navegador, método de autenticação e a trilha de eventos;
   sela esse registro com HMAC-SHA256 e gera o **PDF final com uma página de evidências**.
5. O hash do PDF fica guardado. Em **/verificar**, qualquer pessoa pode enviar o PDF (ou o código de
   verificação) e confirmar que o documento é autêntico e não foi alterado.

Esse tipo de assinatura eletrônica com registro de evidências é comum entre empresas e colaboradores e se
apoia no art. 10, § 2º, da MP 2.200-2/2001 (o próprio termo traz a concordância do colaborador). O texto
padrão do termo é um ponto de partida: **vale revisar com o RH ou o jurídico** antes de usar.

---

## Estrutura do projeto

```
app/
  __init__.py          criação da aplicação, segurança e comandos
  config.py            configuração (.env) e chaves derivadas da chave mestra
  db.py                SQLite, migrações e busca
  estaticos.py         CSS, JavaScript e ícones com cache longo e compressão gzip
  seguranca.py         login, perfis, CSRF, criptografia, limite de tentativas
  validacao.py         CPF, CNPJ (inclusive alfanumérico), IMEI, telefone, datas
  servicos/            regras de negócio (chamados, avisos_chamados, expediente, ad, ldap,
                       teams, aparelhos, colaboradores, vinculos, termos, pdf, email,
                       graph, relatorios, importacao, notificacoes, agendador, backup,
                       painel, usuarios, configuracoes, computadores)
  rotas/               telas (uma por área)
  templates/           HTML (Jinja) e modelos de e-mail
  static/              CSS, JavaScript e ícones (sem bibliotecas externas)
testes/                testes automatizados (pytest) e teste no navegador
docs/                  instalação, chamados (implantação) e integração com o Outlook
instance/              criada ao iniciar: banco, PDFs, backups, logs e chave mestra
```

## Comandos úteis

```bash
python run.py                                   # inicia o sistema (waitress)
flask --app wsgi dados-exemplo                  # dados fictícios (banco vazio)
flask --app wsgi backup                         # gera um backup agora
flask --app wsgi executar-tarefas               # roda lembretes/resumo/backup vencidos (para cron)
flask --app wsgi executar-tarefas --tarefa lembretes
flask --app wsgi redefinir-senha ti@empresa.com.br   # se perder o acesso de administrador
python -m pytest testes                         # testes automatizados (pip install pytest)
```

## Testes

`python -m pytest testes` executa 58 testes. Os de chamados rodam contra um controlador de domínio de mentira
(LDAPS de verdade, com certificado próprio) e cobrem login pelo AD (formatos de usuário, senha errada,
bloqueio, conta desativada ou bloqueada no AD, OU sem unidade, controlador reserva, certificado não confiável),
colaborador isolado das telas da TI, o pedido em três passos (também sem JavaScript), anexos, técnico de outra
unidade que lê e escreve nota mas não altera nada por requisição direta, segurança da informação restrita,
Santos avisando Sorocaba e Uberaba, transferência, abertura em nome de alguém, prazos em horário útil com
feriados, lembretes, escalada, encerramento automático e cobertura de férias que expira sozinha. Os demais cobrem computadores (validações de IP/MAC/faixa da unidade,
técnico que vê todas as unidades mas não altera as outras nem por requisição direta, consulta sem dados de
rede, indicadores, importação por unidade, migração do banco), instalação, login e bloqueio, CSRF, cadastros e
validações, entrega, geração e envio do termo, assinatura com código, PDF e verificação de autenticidade,
devolução, transferência, substituição, limite por colaborador, desligamento, emissão em lote, permissões
por perfil e por unidade, todos os relatórios Excel, importação de planilhas, portal do colaborador,
lembretes, resumo diário, backup e a integração com o Microsoft Graph (contra um servidor local que imita
o Entra ID e o Graph: modo aplicativo, conta conectada com PKCE, erros 401/403/429).

`testes/e2e_navegador.py` percorre as telas no Chrome (computador e celular) com Playwright e salva capturas.

## Dados e backup

Tudo fica na pasta `instance/` (ou na pasta definida em `PASTA_DADOS`):

| Arquivo | O que é |
|---|---|
| `celulares.db` | banco de dados |
| `documentos/termos/` | PDFs assinados |
| `uploads/chamados/` | fotos e PDFs anexados aos chamados (entram no backup) |
| `backups/` | backups diários (banco + PDFs + logotipo), com retenção configurável |
| `chave-mestra.key` | chave que protege sessões, segredos e o selo das assinaturas. **Guarde uma cópia em local seguro.** |
| `logs/aplicacao.log` | log do sistema |

Copie a pasta `backups/` para outro servidor regularmente.

Licenças de terceiros: [LICENCAS-TERCEIROS.md](LICENCAS-TERCEIROS.md).
