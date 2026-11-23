"""Controlador de domínio de mentira para os testes: LDAPS de verdade (TLS com certificado próprio),
entendendo só bind, busca pelo usuário e unbind — o suficiente para exercitar o cliente do sistema."""
import datetime
import socket
import ssl
import tempfile
import threading
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from app.servicos.ldap import _inteiro, _sequencia, _texto, elementos, ler_tlv

DOMINIO = "empresa.local"
BASE_OU = "OU=Empresa Exemplo,DC=empresa,DC=local"


def dn(nome: str, setor: str, unidade: str, empresa: str = "Empresa") -> str:
    return f"CN={nome},OU=Usuários,OU={setor},OU=Setores,OU={unidade},OU={empresa},{BASE_OU}"


def _certificados(pasta: Path):
    agora = datetime.datetime.now(datetime.timezone.utc)
    chave_ca = ec.generate_private_key(ec.SECP256R1())
    nome_ca = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "CA de teste do domínio")])
    ca = (x509.CertificateBuilder().subject_name(nome_ca).issuer_name(nome_ca).public_key(chave_ca.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(agora - datetime.timedelta(days=1))
          .not_valid_after(agora + datetime.timedelta(days=30))
          .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(chave_ca.public_key()), critical=False)
          .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False, key_agreement=False,
                                       encipher_only=False, decipher_only=False), critical=True)
          .sign(chave_ca, hashes.SHA256()))
    chave = ec.generate_private_key(ec.SECP256R1())
    servidor = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
                .issuer_name(nome_ca).public_key(chave.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(agora - datetime.timedelta(days=1)).not_valid_after(agora + datetime.timedelta(days=30))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
                .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
                .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(chave_ca.public_key()), critical=False)
                .sign(chave_ca, hashes.SHA256()))
    (pasta / "servidor.pem").write_bytes(servidor.public_bytes(serialization.Encoding.PEM)
                                         + ca.public_bytes(serialization.Encoding.PEM))
    (pasta / "servidor.key").write_bytes(chave.private_bytes(serialization.Encoding.PEM,
                                                             serialization.PrivateFormat.PKCS8,
                                                             serialization.NoEncryption()))
    return ca.public_bytes(serialization.Encoding.PEM).decode()


def _resultado(tag: int, codigo: int, diagnostico: str = "") -> bytes:
    return _sequencia(_inteiro(codigo, 0x0A), _texto(""), _texto(diagnostico), tag=tag)


class ControladorFalso:
    """Uso: with ControladorFalso(usuarios) as dc: dc.porta, dc.ca_pem"""

    def __init__(self, usuarios: dict):
        self.usuarios = usuarios  # login -> {"senha", "dn", "nome", "mail", "uac"}
        self.pasta = Path(tempfile.mkdtemp(prefix="dc-falso-"))
        self.ca_pem = _certificados(self.pasta)
        self.binds = []  # nomes usados no bind (para conferir que a senha nunca passa sem TLS)
        self._parar = threading.Event()

    def __enter__(self):
        contexto = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        contexto.load_cert_chain(self.pasta / "servidor.pem", self.pasta / "servidor.key")
        self._escuta = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._escuta.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._escuta.bind(("127.0.0.1", 0))
        self._escuta.listen(8)
        self._escuta.settimeout(0.2)
        self.porta = self._escuta.getsockname()[1]
        self._contexto = contexto
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._parar.set()
        self._thread.join(2)
        self._escuta.close()

    def _laco(self):
        while not self._parar.is_set():
            try:
                bruto, _ = self._escuta.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._atender, args=(bruto,), daemon=True).start()

    def _atender(self, bruto):
        try:
            conexao = self._contexto.wrap_socket(bruto, server_side=True)
        except (ssl.SSLError, OSError):
            bruto.close()
            return
        conexao.settimeout(5)
        buffer, logado = b"", None
        try:
            while True:
                try:
                    _, conteudo, fim = ler_tlv(buffer)
                except ValueError:
                    pedaco = conexao.recv(65536)
                    if not pedaco:
                        return
                    buffer += pedaco
                    continue
                buffer = buffer[fim:]
                partes = elementos(conteudo)
                id_mensagem = int.from_bytes(partes[0][1], "big")
                tag, operacao = partes[1]
                if tag == 0x60:  # bind
                    campos = elementos(operacao)
                    nome = campos[1][1].decode()
                    senha = campos[2][1].decode()
                    self.binds.append(nome)
                    login = nome.split("@")[0].lower()
                    usuario = self.usuarios.get(login)
                    if nome.lower().endswith("@" + DOMINIO) and usuario and senha == usuario["senha"]:
                        if usuario.get("bloqueado_ad"):
                            resposta = _resultado(0x61, 49, "80090308: LdapErr: DSID-0C09044E, comment: "
                                                            "AcceptSecurityContext error, data 775, v4563")
                        else:
                            logado = login
                            resposta = _resultado(0x61, 0)
                    else:
                        resposta = _resultado(0x61, 49, "80090308: LdapErr: DSID-0C09044E, comment: "
                                                        "AcceptSecurityContext error, data 52e, v4563")
                    conexao.sendall(_sequencia(_inteiro(id_mensagem), resposta))
                elif tag == 0x63:  # busca
                    if logado:
                        u = self.usuarios[logado]
                        atributos = {"sAMAccountName": [logado], "displayName": [u["nome"]],
                                     "userAccountControl": [u.get("uac", "512")]}
                        if u.get("mail"):
                            atributos["mail"] = [u["mail"]]
                        corpo = _sequencia(*(_sequencia(_texto(chave), _sequencia(*(_texto(v) for v in valores), tag=0x31))
                                             for chave, valores in atributos.items()))
                        conexao.sendall(_sequencia(_inteiro(id_mensagem), _sequencia(_texto(u["dn"]), corpo, tag=0x64)))
                    conexao.sendall(_sequencia(_inteiro(id_mensagem), _resultado(0x65, 0 if logado else 1)))
                elif tag == 0x42:  # unbind
                    return
        except (OSError, ssl.SSLError):
            return
        finally:
            try:
                conexao.close()
            except OSError:
                pass
