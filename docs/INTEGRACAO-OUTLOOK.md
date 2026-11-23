# Integração com o Outlook / Microsoft 365

O sistema envia os e-mails (termos, lembretes, códigos, confirmações e avisos) pelo **Microsoft Graph**,
autenticando no **Microsoft Entra ID**. Tudo é configurado em *Configurações > Outlook*,
sem mexer no código. O segredo do aplicativo e os tokens ficam **cifrados** no banco.

Há dois modos:

| | Aplicativo + caixa compartilhada (recomendado) | Conta Microsoft conectada |
|---|---|---|
| Como envia | Como `celulares@empresa.com.br`, sem ninguém logado | Em nome de um usuário que entrou uma vez |
| Depende de um funcionário | Não | Sim (se a conta for desativada ou mudar a senha, é preciso reconectar) |
| Permissão no Graph | `Mail.Send` **de aplicativo**, ou RBAC do Exchange limitado à caixa | `Mail.Send`, `Mail.Send.Shared`, `User.Read`, `offline_access` **delegadas** |
| Endereço de retorno (redirect URI) | Não precisa | Precisa (HTTPS) |

---

## 1. Criar a caixa compartilhada

No **Exchange admin center** (<https://admin.exchange.microsoft.com>): *Destinatários > Caixas de correio >
Adicionar uma caixa de correio compartilhada*. Exemplo: **Celulares Corporativos**,
`celulares@empresa.com.br`. Caixas compartilhadas não precisam de licença.

## 2. Registrar o aplicativo no Entra ID

1. Acesse <https://entra.microsoft.com> > *Aplicativos > Registros de aplicativo > Novo registro*.
2. Nome: `Gestão de Celulares`. Tipos de conta: *Contas somente neste diretório organizacional*.
3. (Só no modo conta conectada) *URI de redirecionamento*: plataforma **Web**, endereço mostrado na tela
   de integração do sistema, por exemplo `https://celulares.empresa.com.br/configuracoes/integracao/retorno`.
4. Na *Visão geral*, copie o **ID do aplicativo (cliente)** e o **ID do diretório (locatário)**.
5. Em *Certificados e segredos > Novo segredo do cliente*, escolha a validade (até 24 meses) e copie a
   coluna **Valor** (ela só aparece uma vez). Anote a data de vencimento: antes dela, crie um segredo novo e
   atualize no sistema.

## 3. Dar a permissão de envio

### Modo aplicativo, jeito simples

*Permissões de API > Adicionar uma permissão > Microsoft Graph > Permissões de aplicativo* > `Mail.Send`
> *Adicionar*. Depois clique em **Conceder consentimento do administrador**.

Atenção: assim o aplicativo pode enviar como **qualquer** caixa da empresa. Funciona, mas o ideal é restringir.

### Modo aplicativo, restrito à caixa compartilhada (recomendado)

Use o **RBAC para aplicativos** do Exchange Online, que substituiu as antigas *Application Access Policies*.
Nesse caso **não** conceda `Mail.Send` no Entra ID (as duas permissões se somam e a do Entra liberaria
todas as caixas). Se já concedeu, remova a permissão e revogue o consentimento.

Em *Aplicativos empresariais* (não em Registros de aplicativo), abra o aplicativo e copie o **ID do objeto**.
No PowerShell, com o módulo ExchangeOnlineManagement e um usuário com a função *Organization Management*:

```powershell
Connect-ExchangeOnline

# 1. Registra o aplicativo no Exchange
New-ServicePrincipal -AppId "<ID do aplicativo>" -ObjectId "<ID do objeto em Aplicativos empresariais>" `
    -DisplayName "Gestão de Celulares"

# 2. Escopo com apenas a caixa compartilhada
New-ManagementScope -Name "Celulares - caixa de envio" `
    -RecipientRestrictionFilter "PrimarySmtpAddress -eq 'celulares@empresa.com.br'"

# 3. Permissão de envio só nesse escopo
New-ManagementRoleAssignment -App "<ID do aplicativo>" -Role "Application Mail.Send" `
    -CustomResourceScope "Celulares - caixa de envio"

# 4. Conferência: InScope deve ser True para a caixa compartilhada e False para outra caixa qualquer
Test-ServicePrincipalAuthorization -Identity "Gestão de Celulares" -Resource celulares@empresa.com.br | Format-Table
```

As mudanças podem levar de 30 minutos a 2 horas para valer. Neste modo, o teste de conexão avisa que o
token não traz `Mail.Send`: é esperado, porque a permissão vem do Exchange, não do Entra ID. O envio do
e-mail de teste é o que confirma.

### Modo conta conectada

*Permissões de API > Microsoft Graph > Permissões delegadas*: `Mail.Send`, `Mail.Send.Shared`, `User.Read`
e `offline_access`. Para enviar como a caixa compartilhada, a conta precisa da permissão **Enviar como** na
caixa (Exchange admin center > a caixa > *Delegação*).

## 4. Configurar no sistema

1. *Configurações > Outlook*.
2. Escolha **Microsoft 365 (Outlook)** e o tipo de autenticação.
3. Preencha o ID do diretório, o ID do aplicativo, o segredo e a caixa remetente. Salve.
4. Modo conta conectada: clique em **Conectar conta Microsoft** e entre com a conta que vai enviar.
5. Em **Testar conexão**, informe o seu e-mail. O sistema autentica, confere as permissões e envia uma
   mensagem de teste.

Para voltar ao modo de simulação ou trocar de aplicativo, use **Desconectar integração** (apaga o segredo e
a conta conectada).

## Erros comuns

| Mensagem no sistema | Causa provável | O que fazer |
|---|---|---|
| O segredo do cliente está incorreto (AADSTS7000215) | Copiou o *ID do segredo* em vez do *Valor* | Crie um segredo novo e copie a coluna Valor |
| O segredo do cliente expirou (AADSTS7000222) | Venceu a validade | Crie um segredo novo e atualize |
| O ID do aplicativo não foi encontrado (AADSTS700016) | Client ID ou tenant errados | Confira os dois na Visão geral |
| Acesso negado ao enviar pela caixa… | Falta consentimento, ou a caixa está fora do escopo do RBAC | Conceda o consentimento ou ajuste o escopo; aguarde até 2 h |
| A caixa de e-mail… não foi encontrada | Endereço errado ou caixa sem Exchange Online | Confira o remetente |
| A conexão com a conta Microsoft expirou (AADSTS700082) | Conta conectada sem uso por muito tempo | Clique em Conectar conta Microsoft de novo |
| Bloqueado por Acesso Condicional (AADSTS53003) | Política do Entra ID | Libere o aplicativo/servidor na política |

Todos os envios ficam em *Configurações > E-mails enviados* (destinatário, assunto, data, finalidade,
situação e erro). Falhas temporárias (limite de envio 429 e instabilidades 5xx) são repetidas
automaticamente; envios em lote respeitam o limite do Exchange Online (30 mensagens por minuto por caixa).

Se o servidor acessa a internet por proxy, defina `HTTPS_PROXY` no `.env`.
