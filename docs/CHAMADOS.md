# Chamados de TI — implantação

O módulo de chamados substitui o GLPI. O colaborador entra com o usuário do Windows, escolhe o que está
com problema, conta o que aconteceu e acompanha em **Meus pedidos**. A TI recebe tudo preenchido (pessoa,
setor, unidade, máquina) e é avisada na hora pelo Teams.

Este guia segue a ordem da implantação. Antes de começar, o sistema precisa estar na VM Ubuntu com HTTPS
([INSTALACAO.md](INSTALACAO.md)): a senha do Windows passa pelo navegador, então **nunca** use o módulo por HTTP.

---

## 1. Pré-requisitos

- [ ] Sistema na VM com HTTPS e um nome no DNS do AD (ex.: `ti.empresa.local`).
- [ ] No `.env`, `CONFIAR_PROXY=1` (com o nginx na frente). Sem isso, todo pedido parece vir do próprio
      servidor e nenhuma máquina é reconhecida pelo IP.
- [ ] A VM alcança os controladores na porta **636** (LDAPS):
      `nc -zv DC01.empresa.local 636` e `nc -zv DC02.empresa.local 636`.
- [ ] Certificado LDAPS válido nos dois controladores (emitido pela CA do domínio).
- [ ] Outlook configurado no sistema ([INTEGRACAO-OUTLOOK.md](INTEGRACAO-OUTLOOK.md)).
- [ ] Fluxo do Teams criado no canal de cada unidade (item 6).

## 2. Testar antes de ligar o AD

Dá para testar tudo (tela do colaborador, fila, avisos) sem o AD, com **contas locais de teste**:

1. *Configurações › Usuários › Adicionar usuário*, **Conta local**.
2. Preencha Nome, **Usuário** (ex.: `teste.colaborador`), **Unidade da pessoa** e o perfil **Colaborador**.
   Atalho: na aba *Colaboradores*, botão **Colaborador de teste**.
3. Anote a senha temporária que aparece. Na tela de entrar, digite `teste.colaborador` e essa senha; o sistema
   pede para criar uma senha nova e abre a tela de pedidos.
4. Para testar como técnico, crie outra conta local (ex.: `teste.tecnico`) com o perfil **Técnico de TI**.

O pedido tem sempre **3 passos** (“Passo X de 3”):

| Escolha no passo 1 | Passo 2 | Passo 3 |
|---|---|---|
| Computador | “O problema é neste computador?” — **Sim, neste** ou **Não, é em outro** (lista) | Conte o que aconteceu |
| Celular | Lista dos celulares da pessoa (ou “O celular da empresa que está comigo”) e “Não sei qual é” | Conte o que aconteceu |
| As outras seis | — (vai direto) | Conte o que aconteceu (“Passo 3 de 3”) |

Para o teste mostrar a máquina **ligada ao inventário** (nome, configuração, SEP, acesso remoto no painel
*Equipamento* da TI), cadastre em *Computadores* o computador de onde você vai testar, com o **IP** dele e,
em *Usuário*, o login da conta de teste (ex.: `teste.colaborador`). Sem isso o “Sim, neste” também funciona:
o chamado chega como “Neste computador (não está no inventário)” com o IP de onde veio.

Se quiser receber os e-mails do teste, preencha o e-mail da conta de teste com uma caixa que você lê.
Depois de ligar o AD, **desative as contas de teste** (cada uso de conta local passa a gerar aviso aos
administradores). Uma conta local nunca vira conta do AD, mesmo com o mesmo nome de usuário.

## 3. Active Directory

*Configurações › Active Directory*

| Campo | Valor |
|---|---|
| Domínio | `empresa.local` |
| Controladores | `DC01.empresa.local` (1ª linha, principal) e `DC02.empresa.local` (2ª linha, reserva) |
| Porta LDAPS | `636` (o sistema recusa a 389) |
| Conferir o certificado | **ligado** |

**Certificado da CA.** Se o teste disser que o certificado “não é confiável”, a VM ainda não conhece a CA
interna. Exporte o certificado da CA raiz num controlador e cole no campo *Certificado da autoridade do domínio*:

```bat
certutil -ca.cert ca-raiz.cer
certutil -encode ca-raiz.cer ca-raiz.pem
```

Abra o `ca-raiz.pem` no Bloco de Notas e cole tudo, de `-----BEGIN CERTIFICATE-----` até `-----END CERTIFICATE-----`.
(Outra opção: instalar a CA no Ubuntu com `sudo cp ca-raiz.pem /usr/local/share/ca-certificates/ca-raiz.crt && sudo update-ca-certificates`.)

Use **Testar com um usuário** com a sua conta: o resultado mostra nome, e-mail, a OU da unidade, a unidade do
sistema correspondente e o setor. A senha não é guardada nem registrada.

O que o sistema faz a cada login: confere a senha no primeiro controlador que responder (5 s de limite),
relê nome, e-mail, unidade e setor, e recusa conta desativada, vencida ou bloqueada no AD. Depois de 5 senhas
erradas seguidas, o sistema bloqueia aquele usuário por 15 minutos. Confira se a política de bloqueio do AD
tem limite maior que 5: assim uma senha esquecida no sistema não trava a conta do Windows.

## 4. Unidades

*Unidades › editar*

| Unidade | OU no AD | Pedidos liberados | Avisos no Teams |
|---|---|---|---|
| Sorocaba | `SP - Sorocaba` | sim (piloto) | link do canal de Sorocaba |
| Uberaba | `MG - Uberaba` | depois do piloto | link do canal de Uberaba |
| Santos | `SP - Santos` | depois do piloto | sem canal próprio; marque *Avisar também nos canais de* Sorocaba e Uberaba |
| Nordeste | `PE - Recife` | depois do piloto | link do canal da Nordeste |

- A OU é a que fica logo acima de `Setores` no caminho do usuário. O setor é a OU logo abaixo de `Setores`.
- Quem estiver numa OU sem unidade (ex.: `BA - Camaçari`, `SP - São Bernardo do Campo`) ou numa unidade com os
  pedidos desligados recebe “Sua unidade ainda não usa este sistema”. A equipe de TI entra normalmente.

## 5. Equipe de TI

*Configurações › Usuários › Adicionar à equipe*, com **Usuário do Windows (AD)**:

| Pessoa | Perfil | Unidades que atende |
|---|---|---|
| Técnicos de Sorocaba | Técnico de TI | Sorocaba e Santos |
| Técnicos de Uberaba | Técnico de TI | Uberaba e Santos |
| Técnicos da Nordeste | Técnico de TI | Nordeste |
| Superior / administrador do dia a dia | Administrador de TI | todas |

- Pode cadastrar antes do primeiro acesso: a pessoa já entra com o perfil certo.
- Quem entra pelo AD sem estar na lista é **colaborador** e só vê os próprios pedidos.
- Nenhum técnico para pessoas de Santos.
- **Conta local de emergência:** é a conta criada na instalação. Use uma senha longa, guarde no cofre de
  senhas e use só se o AD falhar. Cada uso gera um e-mail para os administradores e para *Configurações ›
  Notificações › Quem recebe*.
- **Cobertura de férias:** no cadastro do técnico, *Cobertura de férias › Liberar cobertura*, com a unidade e
  a data final. O acesso termina sozinho e fica na revisão de acessos.

## 6. Teams (um fluxo por canal)

Em cada canal de unidade (canal **padrão**, não privado):

1. No canal, clique em **⋯ › Workflows**.
2. Escolha **“Enviar alertas de webhook para um canal”** (em inglês: *Send webhook alerts to a channel*).
3. Dê um nome (ex.: `Chamados Sorocaba`), confira equipe e canal e clique em **Adicionar fluxo de trabalho**.
4. Copie o link que aparece no fim.
5. No sistema, *Unidades › editar › Link do fluxo do canal desta unidade*, cole e salve. Clique em
   **Enviar mensagem de teste** e confira no canal.
6. No Power Automate (*Meus fluxos › o fluxo › Editar › Proprietários*), adicione um **coproprietário** da TI:
   se a conta de quem criou o fluxo for desativada, o fluxo para.

O link é um segredo: fica criptografado no banco e não aparece de novo na tela. Para trocar, cole um novo.

O que chega no canal: cartão com número, assunto, solicitante, unidade, prioridade, prazo e o botão *Abrir
chamado*. Chamados de Segurança da informação chegam sem o texto do pedido. Quando alguém assume, sai uma
mensagem curta com o nome (o webhook do Teams não permite editar o cartão).

| Tipo de chamado | Primeiro aviso | Ninguém assumiu |
|---|---|---|
| Comum | na hora | lembrete em 30 min; mais 30 min, e-mail ao administrador |
| Prioridade Alta | na hora | lembrete em 15 min; mais 15 min, e-mail ao administrador |
| Segurança da informação | na hora, canal e e-mail ao administrador | lembrete a cada 15 min até alguém assumir |

Os minutos contam só no horário de atendimento. Prazo: aviso quando faltar ¼ do prazo e outro (também ao
administrador) se vencer.

## 7. Configurações › Chamados

- **Horário:** segunda a sexta, 8h às 18h (A CONFIRMAR). Feriados nacionais entram sozinhos, inclusive
  Sexta-feira Santa, Carnaval e Corpus Christi (dá para desmarcar os dois últimos). Cadastre os municipais de
  Sorocaba, Uberaba, Santos e Recife e as pontes em *Outros feriados*.
- **Prazos:** Alta 4 horas úteis, Média 1 dia útil, Baixa 3 dias úteis (A CONFIRMAR). Valem para os chamados
  novos. “Aguardando usuário” pausa o prazo.
- **Encerramento automático:** resolvido fecha em 3 dias úteis (o colaborador pode reabrir até lá); aguardando
  usuário sem resposta fecha em 5 dias úteis, com e-mail para a pessoa.
- **Resumo diário** às 8h para cada técnico (só das unidades dele) e para os administradores.
- **Encarregado (DPO):** e-mail que recebe os avisos de incidente com dado pessoal.
- **Contato da TI:** aparece em “Esqueceu a senha?” na tela de entrar (ex.: ramal).

## 8. Avisos no navegador

Na fila, clique em **Ativar avisos do Windows** e permita. Com o sistema aberto em qualquer tela da TI
(o navegador confere a cada 10 segundos, mesmo com a aba em segundo plano; com o chamado aberto, a cada 4 segundos, e as mensagens novas entram na conversa sozinhas, dos dois lados):

- **Chamado novo** nas unidades que você atende: número no menu e no título da aba, som e aviso do Windows.
- **Resposta do colaborador** num chamado seu (ou sem responsável na sua unidade): o mesmo número, som e aviso
  do Windows “Fulano respondeu o chamado #N”. Se você estiver com o chamado aberto, aparece a faixa
  “respondeu agora · Atualizar a conversa”; na fila, a linha ganha “Fulano respondeu” e o filtro
  **Respostas novas**. Também aparece no sino. Some quando o responsável abre o chamado.

No rodapé da fila aparece se os avisos estão **ligados** ou **bloqueados** neste navegador, com o botão
**Testar aviso**. Se o teste não aparecer no canto da tela, o bloqueio é no Windows (Configurações › Sistema ›
Notificações: navegador ligado e “Não perturbe” desligado).

Os avisos dependem de uma aba do sistema aberta **com o login da TI**. Se a sessão expirar, se alguém clicar
em **Sair** ou se outra pessoa entrar no mesmo navegador, a aba mostra “Os avisos de chamados pararam nesta
aba”. Para testar como colaborador e como técnico ao mesmo tempo, use uma **janela anônima**
(Ctrl+Shift+N) ou outro navegador para o colaborador.

Para liberar sem perguntar em todas as máquinas da TI, use a política do Edge/Chrome
`NotificationsAllowedForUrls` com o endereço do sistema.

O contador não mantém a sessão viva: depois de 30 minutos sem uso, o sistema pede o login de novo (o Teams
continua avisando).

## 9. Piloto e liberação

1. **Piloto de 1 a 2 semanas só em Sorocaba** (só Sorocaba com *pedidos liberados*). O GLPI continua funcionando.
2. Ajustes com o retorno dos colaboradores.
3. **Comunicado** aos funcionários (modelo abaixo) e **atalho na área de trabalho** por GPO:
   *Configuração do Usuário › Preferências › Configurações do Windows › Atalhos › Novo atalho*:
   Ação *Atualizar*, Nome `Pedidos para a TI`, Tipo de destino *Objeto URL*, Local *Área de trabalho*,
   URL `https://ti.empresa.local/`, ícone à escolha. Vincule a GPO às OUs `Setores` das unidades liberadas.
4. Liberação para Uberaba, Nordeste e Santos (*Unidades › Pedidos liberados*).
5. **Data de corte:** o GLPI para de aceitar chamados novos e fica só para consulta por 6 meses (A CONFIRMAR).

Modelo de comunicado:

> **Assunto: Agora os pedidos para a TI são feitos pelo atalho “Pedidos para a TI”**
>
> A partir de **[data]**, quando algo não funcionar (computador, celular, impressora, internet, senha, programa
> ou um e-mail estranho), use o atalho **Pedidos para a TI** na área de trabalho.
>
> 1. Entre com o mesmo usuário e senha que você usa para ligar o computador.
> 2. Toque no que está com problema.
> 3. Conte o que aconteceu e envie.
>
> A TI responde no seu e-mail e na tela **Meus pedidos**. A TI nunca pede a sua senha.
> Dúvidas: ramal [ramal da TI].

## 10. Segurança, TISAX, ISO 27001 e LGPD

- **A senha do AD não é guardada.** O sistema só confere no controlador, sempre por LDAPS com o certificado
  conferido.
- **Permissões no servidor:** técnico de outra unidade lê e escreve nota interna, mas não assume, não responde,
  não muda situação/prioridade e não transfere, nem por requisição direta (há testes automáticos para isso).
- **Segurança da informação:** chamados visíveis só à TI da unidade e aos administradores; toda leitura e todo
  anexo aberto ficam na auditoria; não podem ser apagados.
- **Conta desativada no AD** não entra mais. Uma sessão já aberta termina por inatividade (10 min para
  colaboradores, 30 min para a TI). Para cortar na hora, desative a pessoa em *Configurações › Usuários*.
- **Anexos:** só imagens e PDF, até 10 MB, conferidos pelo conteúdo e entregues só a quem pode ver o chamado.
- **Revisão de acessos a cada 6 meses:** *Configurações › Usuários › Revisão de acessos* (quem atende o quê,
  desde quando, quem liberou, mudanças e usos da conta local). Imprima ou salve em PDF pelo navegador.
- **Política de acesso (TISAX)**, para incluir na política da empresa:
  > A equipe de TI atua como equipe única: os técnicos consultam os chamados de todas as unidades para
  > cobertura e troca de conhecimento, e atendem apenas os da sua unidade. Chamados de segurança da
  > informação ficam restritos à unidade e ao administrador.
- **LGPD:** em chamado de Segurança da informação, preencha *Registro do incidente* (quando a TI soube, o que
  foi feito, se houve dado pessoal). Marcando “Sim” em dado pessoal, o encarregado (DPO) e os administradores
  recebem um aviso com o prazo de 3 dias úteis (Resolução CD/ANPD nº 15/2024). O botão *Enviar registro ao
  encarregado* manda o histórico completo. Quem decide comunicar a ANPD é o encarregado.
- **Guarda:** o sistema não apaga chamados. Guarda mínima de 5 anos (A CONFIRMAR com jurídico/DPO). Os anexos
  ficam em `instance/uploads/chamados/` e entram no backup.

## 11. Problemas comuns

| O que aparece | O que fazer |
|---|---|
| “O certificado de … não é confiável” | Cadastre a CA do domínio (item 3). |
| “Não foi possível falar com o AD agora” | Teste a porta 636 a partir da VM; confira firewall e se o controlador está no ar. O sistema tenta o segundo controlador sozinho. |
| “Sua unidade ainda não usa este sistema” | Confira a *OU no AD* da unidade (igual ao AD, sem acento faltando) e se *pedidos liberados* está marcado. |
| O pedido não reconhece a máquina | Confira `CONFIAR_PROXY=1` e se o IP da máquina está no inventário. Fora do inventário (máquina não cadastrada, Wi-Fi de visitante, VPN), o “Sim, neste” registra o IP de onde veio para a TI localizar. No navegador do celular, a pergunta vira “Qual computador está com problema?”. |
| Avisos do Teams não chegam | *Configurações › Chamados › Canais do Teams* mostra as falhas. Teste o link na unidade; o fluxo pode ter sido desligado ou o dono perdeu o acesso (por isso o coproprietário). Se a VM sai pela internet por proxy, defina `HTTPS_PROXY` no ambiente do serviço. |
| E-mails não saem | *Configurações › E-mails enviados* mostra o motivo; confira o Outlook ([guia](INTEGRACAO-OUTLOOK.md)). |
