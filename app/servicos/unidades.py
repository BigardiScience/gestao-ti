"""Unidades (filiais) da empresa."""
import ipaddress
import re

from ..db import consultar, escalar, executar, normalizar, transacao
from ..seguranca import filtro_unidades
from ..validacao import cnpj_valido, limpar_texto
from . import ErroNegocio, ErroValidacao
from .registro import auditar

UFS = ["AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB", "PR",
       "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"]


def listar(incluir_inativas: bool = True, com_contagens: bool = False):
    restricao, parametros = filtro_unidades("u.id")
    where = "WHERE 1=1" + restricao + ("" if incluir_inativas else " AND u.ativo = 1")
    if not com_contagens:
        return consultar(f"SELECT u.* FROM unidades u {where} ORDER BY u.nome COLLATE NOCASE", parametros)
    return consultar(
        f"""SELECT u.*,
               (SELECT COUNT(*) FROM aparelhos a WHERE a.unidade_id = u.id AND a.status <> 'baixado') AS aparelhos,
               (SELECT COUNT(*) FROM aparelhos a WHERE a.unidade_id = u.id AND a.status = 'em_uso') AS em_uso,
               (SELECT COUNT(*) FROM colaboradores c WHERE c.unidade_id = u.id AND c.situacao = 'ativo') AS colaboradores,
               (SELECT COUNT(*) FROM computadores pc WHERE pc.unidade_id = u.id AND pc.situacao <> 'desativado')
                   AS computadores
            FROM unidades u {where} ORDER BY u.nome COLLATE NOCASE""",
        parametros,
    )


def opcoes():
    """Unidades ativas para os campos de seleção."""
    return listar(incluir_inativas=False)


def obter(unidade_id: int):
    return consultar("SELECT * FROM unidades WHERE id = ?", (unidade_id,), um=True)


def localizar_por_nome(texto: str):
    """Procura por nome ou sigla, ignorando maiúsculas e acentos (usado na importação)."""
    alvo = normalizar(texto)
    if not alvo:
        return None
    for unidade in consultar("SELECT * FROM unidades"):
        if normalizar(unidade["nome"]) == alvo or (unidade["sigla"] and normalizar(unidade["sigla"]) == alvo):
            return unidade
    return None


def validar(form: dict, unidade_id: int | None = None) -> dict:
    erros = {}
    dados = {
        "nome": limpar_texto(form.get("nome"), 120),
        "sigla": limpar_texto(form.get("sigla"), 20).upper() or None,
        "cnpj": None,
        "cidade": limpar_texto(form.get("cidade"), 80) or None,
        "uf": (form.get("uf") or "").strip().upper() or None,
        "endereco": limpar_texto(form.get("endereco"), 200) or None,
        "ativo": 1 if form.get("ativo") in ("1", "on", True, 1) else 0,
        "redes": None,
        "ou_ad": limpar_texto(form.get("ou_ad"), 80) or None,
        "chamados_ativo": 1 if form.get("chamados_ativo") in ("1", "on", True, 1) else 0,
        "teams_tambem": None,
        "teams_link": (form.get("teams_link") or "").strip(),
        "teams_remover": form.get("teams_remover") in ("1", "on"),
    }
    valores = form.getlist("teams_tambem") if hasattr(form, "getlist") else form.get("teams_tambem") or []
    outras = sorted({int(v) for v in valores if str(v).isdigit() and int(v) != (unidade_id or 0)})
    dados["teams_tambem"] = ",".join(str(v) for v in outras) or None
    if dados["teams_link"]:
        from .teams import link_valido
        if not link_valido(dados["teams_link"]):
            erros["teams_link"] = "Cole o endereço completo do fluxo (começa com https://)."
    if dados["ou_ad"] and escalar("SELECT id FROM unidades WHERE ou_ad = ? COLLATE NOCASE AND id <> ?",
                                  (dados["ou_ad"], unidade_id or 0)):
        erros["ou_ad"] = "Outra unidade já usa esta OU do AD."
    try:
        redes = ler_redes(form.get("redes"))
        dados["redes"] = ", ".join(str(r) for r in redes) or None
    except ValueError as erro:
        erros["redes"] = str(erro)
    if not dados["nome"]:
        erros["nome"] = "Informe o nome da unidade."
    elif escalar("SELECT id FROM unidades WHERE nome = ? COLLATE NOCASE AND id <> ?",
                 (dados["nome"], unidade_id or 0)):
        erros["nome"] = "Já existe uma unidade com este nome."
    cnpj = (form.get("cnpj") or "").strip()
    if cnpj:
        if not cnpj_valido(cnpj):
            erros["cnpj"] = "CNPJ inválido. Confira os dígitos."
        else:
            dados["cnpj"] = "".join(c for c in cnpj.upper() if c.isalnum())
    if dados["uf"] and dados["uf"] not in UFS:
        erros["uf"] = "Selecione uma UF válida."
    if erros:
        raise ErroValidacao(erros)
    return dados


def ler_redes(texto) -> list:
    """Faixas de IP da unidade, ex.: "172.16.161.0/24, 10.10.5.0/24" (vírgula, espaço ou quebra de linha)."""
    redes = []
    for parte in re.split(r"[\s,;]+", str(texto or "").strip()):
        if not parte:
            continue
        try:
            rede = ipaddress.ip_network(parte, strict=False)
        except ValueError:
            raise ValueError(f"“{parte}” não é uma faixa de IP válida. Use o formato 172.16.161.0/24.") from None
        if rede.num_addresses == 1:
            raise ValueError(f"Informe a faixa com a máscara, por exemplo {parte}/24.")
        if rede not in redes:
            redes.append(rede)
    return redes


def _gravar_teams(unidade_id: int, dados: dict, atual: dict | None):
    from .teams import guardar_link
    if dados["teams_link"]:
        guardar_link(unidade_id, dados["teams_link"])
        auditar("unidade_teams_alterado", "unidade", unidade_id, {"acao": "link do Teams cadastrado"})
    elif dados["teams_remover"] and atual and atual.get("teams_webhook"):
        guardar_link(unidade_id, None)
        auditar("unidade_teams_alterado", "unidade", unidade_id, {"acao": "link do Teams removido"})


def criar(form: dict) -> int:
    dados = validar(form)
    with transacao():
        cursor = executar(
            "INSERT INTO unidades (nome, sigla, cnpj, cidade, uf, endereco, ativo, redes, ou_ad, chamados_ativo, "
            "teams_tambem) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (dados["nome"], dados["sigla"], dados["cnpj"], dados["cidade"], dados["uf"], dados["endereco"],
             dados["ativo"], dados["redes"], dados["ou_ad"], dados["chamados_ativo"], dados["teams_tambem"]),
        )
        _gravar_teams(cursor.lastrowid, dados, None)
        auditar("unidade_criada", "unidade", cursor.lastrowid, {"nome": dados["nome"]})
    return cursor.lastrowid


def criar_rapida(nome: str) -> int:
    """Cria uma unidade só com o nome (opção da importação de planilhas)."""
    return criar({"nome": nome, "ativo": "1"})


def atualizar(unidade_id: int, form: dict):
    atual = obter(unidade_id)
    if not atual:
        raise ErroNegocio("Unidade não encontrada.")
    dados = validar(form, unidade_id)
    if not dados["ativo"] and atual["ativo"]:
        em_uso = escalar("SELECT COUNT(*) FROM aparelhos WHERE unidade_id = ? AND status = 'em_uso'", (unidade_id,))
        if em_uso:
            raise ErroNegocio(f"A unidade tem {em_uso} aparelho(s) em utilização. "
                              "Transfira ou registre a devolução antes de inativá-la.")
    with transacao():
        executar(
            """UPDATE unidades SET nome = ?, sigla = ?, cnpj = ?, cidade = ?, uf = ?, endereco = ?, ativo = ?,
                   redes = ?, ou_ad = ?, chamados_ativo = ?, teams_tambem = ?, atualizado_em = datetime('now')
               WHERE id = ?""",
            (dados["nome"], dados["sigla"], dados["cnpj"], dados["cidade"], dados["uf"], dados["endereco"],
             dados["ativo"], dados["redes"], dados["ou_ad"], dados["chamados_ativo"], dados["teams_tambem"],
             unidade_id),
        )
        _gravar_teams(unidade_id, dados, atual)
        auditar("unidade_alterada", "unidade", unidade_id,
                {"nome": dados["nome"], "ou_ad": dados["ou_ad"], "chamados_ativo": dados["chamados_ativo"]})


def excluir(unidade_id: int):
    unidade = obter(unidade_id)
    if not unidade:
        raise ErroNegocio("Unidade não encontrada.")
    vinculada = (escalar("SELECT COUNT(*) FROM aparelhos WHERE unidade_id = ?", (unidade_id,))
                 + escalar("SELECT COUNT(*) FROM colaboradores WHERE unidade_id = ?", (unidade_id,))
                 + escalar("SELECT COUNT(*) FROM computadores WHERE unidade_id = ?", (unidade_id,))
                 + escalar("SELECT COUNT(*) FROM chamados WHERE unidade_id = ?", (unidade_id,))
                 + escalar("SELECT COUNT(*) FROM usuarios WHERE unidade_id = ?", (unidade_id,)))
    if vinculada:
        raise ErroNegocio("Esta unidade tem aparelhos, colaboradores, computadores, chamados ou usuários. "
                          "Para preservar o histórico, inative-a em vez de excluir.")
    with transacao():
        executar("DELETE FROM usuarios_unidades WHERE unidade_id = ?", (unidade_id,))
        executar("DELETE FROM unidades WHERE id = ?", (unidade_id,))
        auditar("unidade_excluida", "unidade", unidade_id, {"nome": unidade["nome"]})

