"""Cliente LDAPS mínimo para autenticar no Active Directory (sem biblioteca externa).

Faz só o necessário: conexão TLS (porta 636), "simple bind" com o usuário e a senha da pessoa,
uma busca pelo próprio usuário e o encerramento da conexão. As mensagens seguem o LDAP v3
(RFC 4511), codificadas em BER.

Segurança:
- a senha nunca é gravada nem registrada em log;
- senha vazia é recusada antes de conectar (no LDAP, bind com senha vazia "funciona" como anônimo);
- o certificado do controlador é verificado por padrão (nome e cadeia).
"""
import re
import socket
import ssl
from dataclasses import dataclass, field

# ------------------------------------------------------------------ erros


class ErroLDAP(Exception):
    def __init__(self, mensagem: str, detalhe: str = ""):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.detalhe = detalhe


class CredenciaisInvalidas(ErroLDAP):
    """Usuário ou senha recusados pelo AD (inclui conta desativada, bloqueada ou senha vencida)."""


class ServidorIndisponivel(ErroLDAP):
    """Não foi possível falar com o controlador (rede, TLS, tempo esgotado)."""


# códigos de "data XXX" que o AD devolve junto do erro 49 (invalidCredentials)
MOTIVOS_AD = {
    "52e": "Usuário ou senha incorretos.",
    "525": "Usuário ou senha incorretos.",
    "530": "Este usuário não pode entrar neste horário.",
    "531": "Este usuário não pode entrar a partir deste computador.",
    "532": "A senha do Windows venceu. Troque a senha no computador e tente de novo.",
    "533": "Este usuário está desativado no AD.",
    "701": "Este usuário está vencido no AD.",
    "773": "É preciso trocar a senha do Windows antes de entrar. Troque no computador e tente de novo.",
    "775": "Este usuário está bloqueado no AD por excesso de tentativas. Aguarde ou fale com a TI.",
}

# ------------------------------------------------------------------ BER (codificação)


def _comprimento(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    corpo = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(corpo)]) + corpo


def _tlv(tag: int, conteudo: bytes) -> bytes:
    return bytes([tag]) + _comprimento(len(conteudo)) + conteudo


def _inteiro(n: int, tag: int = 0x02) -> bytes:
    corpo = n.to_bytes(max(1, (n.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(tag, corpo)


def _texto(valor, tag: int = 0x04) -> bytes:
    return _tlv(tag, valor.encode("utf-8") if isinstance(valor, str) else valor)


def _sequencia(*partes: bytes, tag: int = 0x30) -> bytes:
    return _tlv(tag, b"".join(partes))


def _mensagem(id_mensagem: int, operacao: bytes) -> bytes:
    return _sequencia(_inteiro(id_mensagem), operacao)


def pedido_bind(id_mensagem: int, nome: str, senha: str) -> bytes:
    # BindRequest ::= [APPLICATION 0] { version 3, name, authentication simple [0] }
    return _mensagem(id_mensagem, _sequencia(_inteiro(3), _texto(nome), _texto(senha, 0x80), tag=0x60))


def filtro_igual(atributo: str, valor: str) -> bytes:
    return _sequencia(_texto(atributo), _texto(valor), tag=0xA3)  # equalityMatch [3]


def filtro_e(*filtros: bytes) -> bytes:
    return _tlv(0xA0, b"".join(filtros))  # and [0] SET OF Filter


def pedido_busca(id_mensagem: int, base: str, filtro: bytes, atributos: list[str], limite: int = 2) -> bytes:
    # SearchRequest ::= [APPLICATION 3] { base, scope wholeSubtree(2), derefAliases never(0), sizeLimit,
    #                                     timeLimit, typesOnly FALSE, filter, attributes }
    corpo = (_texto(base) + _inteiro(2, 0x0A) + _inteiro(0, 0x0A) + _inteiro(limite) + _inteiro(10)
             + _tlv(0x01, b"\x00") + filtro + _sequencia(*(_texto(a) for a in atributos)))
    return _mensagem(id_mensagem, _tlv(0x63, corpo))


def pedido_unbind(id_mensagem: int) -> bytes:
    return _mensagem(id_mensagem, b"\x42\x00")

# ------------------------------------------------------------------ BER (leitura)


def ler_tlv(dados: bytes, pos: int = 0) -> tuple[int, bytes, int]:
    """Lê um elemento a partir de pos. Devolve (tag, conteúdo, posição seguinte)."""
    if pos + 2 > len(dados):
        raise ValueError("BER incompleto")
    tag = dados[pos]
    tamanho = dados[pos + 1]
    pos += 2
    if tamanho & 0x80:
        quantos = tamanho & 0x7F
        if quantos == 0 or quantos > 4 or pos + quantos > len(dados):
            raise ValueError("comprimento BER inválido")
        tamanho = int.from_bytes(dados[pos:pos + quantos], "big")
        pos += quantos
    if pos + tamanho > len(dados):
        raise ValueError("BER incompleto")
    return tag, dados[pos:pos + tamanho], pos + tamanho


def elementos(conteudo: bytes) -> list[tuple[int, bytes]]:
    itens, pos = [], 0
    while pos < len(conteudo):
        tag, valor, pos = ler_tlv(conteudo, pos)
        itens.append((tag, valor))
    return itens


def _ler_resultado(conteudo: bytes) -> tuple[int, str]:
    partes = elementos(conteudo)
    codigo = int.from_bytes(partes[0][1], "big", signed=True)
    diagnostico = partes[2][1].decode("utf-8", "replace") if len(partes) > 2 else ""
    return codigo, diagnostico


# ------------------------------------------------------------------ conexão


@dataclass
class EntradaAD:
    dn: str
    atributos: dict = field(default_factory=dict)

    def valor(self, nome: str) -> str:
        for chave, valores in self.atributos.items():
            if chave.lower() == nome.lower() and valores:
                return valores[0]
        return ""


class ConexaoLDAP:
    def __init__(self, servidor: str, porta: int = 636, *, timeout: float = 5.0, contexto: ssl.SSLContext = None):
        self.servidor, self.porta, self.timeout = servidor, porta, timeout
        self.contexto = contexto or ssl.create_default_context()
        self._sock = None
        self._buffer = b""
        self._id = 0

    def __enter__(self):
        try:
            bruto = socket.create_connection((self.servidor, self.porta), timeout=self.timeout)
        except OSError as erro:
            raise ServidorIndisponivel(f"Não foi possível conectar em {self.servidor}:{self.porta}.", str(erro)) from None
        try:
            self._sock = self.contexto.wrap_socket(bruto, server_hostname=self.servidor)
        except ssl.SSLCertVerificationError as erro:
            bruto.close()
            raise ServidorIndisponivel(
                f"O certificado de {self.servidor} não é confiável para este servidor. Cadastre o certificado da "
                "autoridade (CA) do domínio em Configurações > Active Directory.", str(erro)) from None
        except (ssl.SSLError, OSError) as erro:
            bruto.close()
            raise ServidorIndisponivel(f"Falha na conexão segura (TLS) com {self.servidor}.", str(erro)) from None
        self._sock.settimeout(self.timeout)
        return self

    def __exit__(self, *_):
        try:
            self._enviar(pedido_unbind(self._proximo_id()))
        except Exception:
            pass
        try:
            self._sock.close()
        except Exception:
            pass

    def _proximo_id(self) -> int:
        self._id += 1
        return self._id

    def _enviar(self, dados: bytes):
        try:
            self._sock.sendall(dados)
        except OSError as erro:
            raise ServidorIndisponivel(f"A conexão com {self.servidor} caiu.", str(erro)) from None

    def _receber_mensagem(self) -> tuple[int, int, bytes]:
        """Lê uma LDAPMessage inteira. Devolve (id, tag da operação, conteúdo da operação)."""
        while True:
            try:
                _, conteudo, fim = ler_tlv(self._buffer)
            except ValueError:
                try:
                    pedaco = self._sock.recv(65536)
                except OSError as erro:
                    raise ServidorIndisponivel(f"{self.servidor} não respondeu a tempo.", str(erro)) from None
                if not pedaco:
                    raise ServidorIndisponivel(f"{self.servidor} encerrou a conexão.")
                self._buffer += pedaco
                if len(self._buffer) > 4 * 1024 * 1024:
                    raise ServidorIndisponivel("Resposta do AD grande demais.")
                continue
            self._buffer = self._buffer[fim:]
            partes = elementos(conteudo)
            id_mensagem = int.from_bytes(partes[0][1], "big")
            tag, operacao = partes[1]
            return id_mensagem, tag, operacao

    def bind(self, nome: str, senha: str):
        if not senha:
            raise CredenciaisInvalidas("Informe a senha.")
        self._enviar(pedido_bind(self._proximo_id(), nome, senha))
        _, tag, operacao = self._receber_mensagem()
        if tag != 0x61:
            raise ServidorIndisponivel("Resposta inesperada do AD ao entrar.")
        codigo, diagnostico = _ler_resultado(operacao)
        if codigo == 0:
            return
        if codigo == 49:
            achado = re.search(r"data ([0-9a-f]{3})", diagnostico, re.I)
            motivo = MOTIVOS_AD.get(achado.group(1).lower()) if achado else None
            raise CredenciaisInvalidas(motivo or "Usuário ou senha incorretos.", diagnostico)
        raise ErroLDAP(f"O AD recusou a entrada (código {codigo}).", diagnostico)

    def buscar(self, base: str, filtro: bytes, atributos: list[str]) -> list[EntradaAD]:
        self._enviar(pedido_busca(self._proximo_id(), base, filtro, atributos))
        entradas = []
        while True:
            _, tag, operacao = self._receber_mensagem()
            if tag == 0x64:  # SearchResultEntry
                partes = elementos(operacao)
                dn = partes[0][1].decode("utf-8", "replace")
                valores = {}
                for _, atributo in elementos(partes[1][1]):
                    nome, conjunto = elementos(atributo)[:2]
                    valores[nome[1].decode("utf-8", "replace")] = [
                        v.decode("utf-8", "replace") for _, v in elementos(conjunto[1])]
                entradas.append(EntradaAD(dn, valores))
            elif tag == 0x73:  # SearchResultReference (outro domínio): ignorado
                continue
            elif tag == 0x65:  # SearchResultDone
                codigo, diagnostico = _ler_resultado(operacao)
                if codigo not in (0, 4):  # 4 = sizeLimitExceeded
                    raise ErroLDAP(f"A busca no AD falhou (código {codigo}).", diagnostico)
                return entradas
            else:
                raise ServidorIndisponivel("Resposta inesperada do AD na busca.")


# ------------------------------------------------------------------ nomes distintos (DN)


def partes_dn(dn: str) -> list[tuple[str, str]]:
    """'CN=Ana Souza,OU=Fiscal,OU=Setores' -> [('CN','Ana Souza'), ('OU','Fiscal'), ('OU','Setores')]"""
    partes, atual, escapado = [], "", False
    for caractere in dn or "":
        if escapado:
            atual += caractere
            escapado = False
        elif caractere == "\\":
            escapado = True
        elif caractere == ",":
            partes.append(atual)
            atual = ""
        else:
            atual += caractere
    if atual:
        partes.append(atual)
    resultado = []
    for parte in partes:
        tipo, _, valor = parte.partition("=")
        resultado.append((tipo.strip().upper(), valor.strip()))
    return resultado


def unidade_e_setor(dn: str) -> tuple[str | None, str | None]:
    """Padrão esperado: ...,OU=<setor>,OU=Setores,OU=<unidade>,OU=<empresa>,OU=Empresa Exemplo,DC=..."""
    partes = partes_dn(dn)
    for indice, (tipo, valor) in enumerate(partes):
        if tipo == "OU" and valor.lower() == "setores":
            setor = partes[indice - 1][1] if indice > 0 and partes[indice - 1][0] == "OU" else None
            unidade = partes[indice + 1][1] if indice + 1 < len(partes) and partes[indice + 1][0] == "OU" else None
            return unidade, setor
    return None, None


def base_do_dominio(dominio: str) -> str:
    return ",".join(f"DC={parte}" for parte in dominio.strip().strip(".").split(".") if parte)
