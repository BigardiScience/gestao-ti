"""Validação e formatação de documentos e campos brasileiros."""
import re
from datetime import date, datetime

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def somente_digitos(valor) -> str:
    return re.sub(r"\D", "", str(valor or ""))


# ------------------------------------------------------------------ CPF

def cpf_valido(cpf) -> bool:
    numeros = somente_digitos(cpf)
    if len(numeros) != 11 or numeros == numeros[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(numeros[i]) * (tamanho + 1 - i) for i in range(tamanho))
        digito = (soma * 10) % 11 % 10
        if digito != int(numeros[tamanho]):
            return False
    return True


def formatar_cpf(cpf) -> str:
    n = somente_digitos(cpf)
    if len(n) != 11:
        return cpf or ""
    return f"{n[:3]}.{n[3:6]}.{n[6:9]}-{n[9:]}"


def mascarar_cpf(cpf) -> str:
    """Mostra só os dígitos do meio, como recomenda a LGPD para exibições gerais."""
    n = somente_digitos(cpf)
    if len(n) != 11:
        return "—"
    return f"***.{n[3:6]}.{n[6:9]}-**"


# ------------------------------------------------------------------ CNPJ (numérico e alfanumérico)

def cnpj_valido(cnpj) -> bool:
    """Aceita o CNPJ numérico e o novo formato alfanumérico (IN RFB 2.229/2024)."""
    valor = re.sub(r"[^0-9A-Za-z]", "", str(cnpj or "")).upper()
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", valor) or valor == valor[0] * 14:
        return False

    def calcular(base: str) -> int:
        pesos = list(range(len(base) - 7, 1, -1)) + list(range(9, 1, -1))
        soma = sum((ord(c) - 48) * p for c, p in zip(base, pesos))
        resto = soma % 11
        return 0 if resto < 2 else 11 - resto

    d1 = calcular(valor[:12])
    d2 = calcular(valor[:12] + str(d1))
    return valor[12:] == f"{d1}{d2}"


def formatar_cnpj(cnpj) -> str:
    v = re.sub(r"[^0-9A-Za-z]", "", str(cnpj or "")).upper()
    if len(v) != 14:
        return cnpj or ""
    return f"{v[:2]}.{v[2:5]}.{v[5:8]}/{v[8:12]}-{v[12:]}"


# ------------------------------------------------------------------ IMEI

def imei_valido(imei) -> bool:
    """IMEI tem 15 dígitos e o último é verificador (algoritmo de Luhn)."""
    texto = str(imei or "").strip()
    if not re.fullmatch(r"[\d\s./-]+", texto):
        return False
    numeros = somente_digitos(texto)
    if len(numeros) != 15:
        return False
    soma = 0
    for i, c in enumerate(reversed(numeros)):
        d = int(c)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        soma += d
    return soma % 10 == 0


# ------------------------------------------------------------------ telefone

def telefone_valido(telefone) -> bool:
    n = somente_digitos(telefone)
    if n.startswith("55") and len(n) in (12, 13):
        n = n[2:]
    return len(n) in (10, 11) and n[0] != "0"


def normalizar_telefone(telefone) -> str:
    n = somente_digitos(telefone)
    if n.startswith("55") and len(n) in (12, 13):
        n = n[2:]
    return n


def formatar_telefone(telefone) -> str:
    n = somente_digitos(telefone)
    if len(n) == 11:
        return f"({n[:2]}) {n[2:7]}-{n[7:]}"
    if len(n) == 10:
        return f"({n[:2]}) {n[2:6]}-{n[6:]}"
    return telefone or ""


# ------------------------------------------------------------------ e-mail

def email_valido(email) -> bool:
    email = (email or "").strip()
    return len(email) <= 254 and bool(EMAIL_RE.match(email))


def mascarar_email(email) -> str:
    email = email or ""
    if "@" not in email:
        return email
    usuario, dominio = email.split("@", 1)
    visivel = usuario[:1]
    return f"{visivel}{'*' * max(2, len(usuario) - 1)}@{dominio}"


# ------------------------------------------------------------------ datas e valores

def ler_data(valor) -> date | None:
    """Aceita date/datetime, 'AAAA-MM-DD' (campo de data do navegador) e 'DD/MM/AAAA'."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor).strip()
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(texto, formato).date()
        except ValueError:
            continue
    raise ValueError("Data inválida. Use o formato DD/MM/AAAA.")


def ler_valor_centavos(valor) -> int | None:
    """Converte '1.234,56', '1234.56' ou 1234.56 em centavos (inteiro)."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        return int(round(float(valor) * 100))
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        numero = float(texto)
    except ValueError:
        raise ValueError("Valor inválido. Exemplo: 1.299,90") from None
    if numero < 0:
        raise ValueError("O valor não pode ser negativo.")
    return int(round(numero * 100))


def formatar_moeda(centavos) -> str:
    if centavos is None or centavos == "":
        return ""
    reais = int(centavos) / 100
    texto = f"{reais:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def limpar_texto(valor, limite: int = 200) -> str:
    texto = " ".join(str(valor or "").split())
    return texto[:limite]


def limpar_texto_longo(valor, limite: int = 4000) -> str:
    linhas = [" ".join(l.split()) for l in str(valor or "").replace("\r\n", "\n").split("\n")]
    return "\n".join(linhas).strip()[:limite]
