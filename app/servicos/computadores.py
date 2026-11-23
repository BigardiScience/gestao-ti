"""Inventário de computadores (desktops e notebooks) de cada unidade.

Regras de acesso (aplicadas aqui, no servidor, e não só na tela):
- administrador: vê e altera tudo;
- técnico de TI: vê os computadores de todas as unidades e altera só os da(s) unidade(s) dele;
- consulta: vê só as unidades liberadas, sem IP, MAC e acesso remoto.
"""
import ipaddress
import json
import re
from datetime import timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font

from ..db import condicao_busca, consultar, escalar, executar, normalizar, obter_db, transacao
from ..seguranca import (filtro_unidades_computadores, garantir_acesso_unidade, garantir_edicao_unidade,
                         pode_editar_unidade, unidades_editaveis, usuario_atual, ve_dados_de_rede)
from ..util import agora, agora_bd, agora_local, de_bd, para_bd, paginar
from ..validacao import limpar_texto, limpar_texto_longo
from . import ErroNegocio, ErroValidacao
from .registro import auditar, usuario_atual_id

TIPOS = {"desktop": "Desktop", "notebook": "Notebook"}
SITUACOES = {"em_uso": "Em uso", "reserva": "Reserva", "manutencao": "Manutenção", "desativado": "Desativado"}
SITUACOES_USUARIO = {"ativo": "Ativo", "ferias": "Férias ou afastado", "desligado": "Desligado"}
SEP = {"ok": "OK", "pendente": "Pendente", "nao_instalado": "Não instalado"}
FERRAMENTAS_REMOTO = ["AnyDesk", "RustDesk", "TeamViewer", "Outra"]
MOTIVOS_DESATIVACAO = ["Obsoleto", "Defeito sem conserto", "Furto ou perda", "Venda ou doação", "Outro"]
DESTINOS_DISCO = ["Apagado com segurança", "Destruído", "Retido pela TI"]
PRAZO_CONFERENCIA_DIAS = 180
SUGESTOES_SISTEMA = ["Windows 11 Pro", "Windows 10 Pro", "Windows 11", "Windows 10", "Ubuntu"]

CAMPOS_REDE = ("ip", "mac", "ip_wifi", "mac_wifi", "remoto_ferramenta", "remoto_id")
ROTULOS = {
    "nome": "Nome da máquina", "unidade_id": "Unidade", "tipo": "Tipo", "situacao": "Situação",
    "usuario_nome": "Nome do usuário", "usuario_login": "Login de rede", "setor": "Setor",
    "usuario_situacao": "Situação do usuário", "sistema": "Sistema operacional", "processador": "Processador",
    "memoria_gb": "Memória (GB)", "ip": "IP", "mac": "MAC", "ip_wifi": "IP Wi-Fi", "mac_wifi": "MAC Wi-Fi",
    "sep": "SEP", "remoto_ferramenta": "Acesso remoto", "remoto_id": "ID de acesso remoto", "nobreak": "Nobreak",
    "monitor": "Monitor", "webcam": "Webcam", "headset": "Headset", "outros_perifericos": "Outros periféricos",
    "softwares": "Softwares específicos", "marca": "Marca", "modelo": "Modelo", "numero_serie": "Nº de série",
    "patrimonio": "Patrimônio", "observacoes": "Observações", "desativacao_motivo": "Motivo da desativação",
    "destino_disco": "Destino do disco",
}
ROTULOS_REDE = {ROTULOS[c] for c in CAMPOS_REDE}
COLUNAS_GRAVADAS = [c for c in ROTULOS] + ["nome_ordem", "ip_ordem"]

SQL_BASE = """
    SELECT c.*, u.nome AS unidade_nome, uc.nome AS conferido_por_nome
      FROM computadores c
      JOIN unidades u ON u.id = c.unidade_id
      LEFT JOIN usuarios uc ON uc.id = c.conferido_por
"""
ORDENACOES = {
    "nome": "c.nome_ordem",
    "usuario": "c.usuario_nome IS NULL, c.usuario_nome COLLATE NOCASE, c.nome_ordem",
    "ip": "c.ip_ordem IS NULL, c.ip_ordem, c.nome_ordem",
    "situacao": "c.situacao, c.nome_ordem",
    "unidade": "u.nome COLLATE NOCASE, c.nome_ordem",
}
# precisa de atenção: SEP com problema, usuário desligado com a máquina ou conferência vencida (6 meses)
COND_ATENCAO = ("(c.situacao <> 'desativado' AND (c.sep IN ('pendente', 'nao_instalado') "
                "OR (c.usuario_situacao = 'desligado' AND c.situacao = 'em_uso') "
                "OR coalesce(c.conferido_em, c.criado_em) < ?))")


# ------------------------------------------------------------------ formatos

def chave_natural(texto: str) -> str:
    """PCSOR9 antes de PCSOR10: números viram texto com zeros à esquerda para ordenar."""
    return re.sub(r"\d+", lambda m: m.group().zfill(10), (texto or "").upper())


def normalizar_mac(valor) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    hexa = re.sub(r"[^0-9A-Fa-f]", "", texto).upper()
    if not re.fullmatch(r"[0-9A-Fa-f:.\- ]+", texto) or len(hexa) != 12:
        raise ValueError("MAC inválido: são 12 caracteres de 0-9 e A-F, ex.: 02:00:5E:10:64:D2.")
    return ":".join(hexa[i:i + 2] for i in range(0, 12, 2))


def normalizar_ip(valor) -> str | None:
    texto = str(valor or "").strip()
    if not texto:
        return None
    try:
        return str(ipaddress.ip_address(texto))
    except ValueError:
        raise ValueError("IP inválido. Ex.: 172.16.161.10.") from None


def ordem_ip(ip: str | None) -> int | None:
    if not ip:
        return None
    endereco = ipaddress.ip_address(ip)
    return int(endereco) if endereco.version == 4 else None


def ler_memoria(valor) -> int | None:
    texto = str(valor or "").strip().upper().replace(" ", "")
    if not texto:
        return None
    achado = re.fullmatch(r"(\d+(?:[.,]\d+)?)(GB|G|MB|M)?", texto)
    if not achado:
        raise ValueError("Informe a memória em GB, só o número. Ex.: 8.")
    numero = float(achado.group(1).replace(",", "."))
    if achado.group(2) in ("MB", "M") or numero > 256:
        numero /= 1024
    gb = round(numero)
    if not 1 <= gb <= 256:
        raise ValueError("Memória fora do esperado (1 a 256 GB).")
    return gb


def ler_sim_nao(valor) -> int | None:
    texto = normalizar(valor).strip(" .-")
    if texto in ("", "nao informado", "n/a", "na"):
        return None
    if texto in ("1", "sim", "s", "true", "on", "yes", "tem"):
        return 1
    if texto in ("0", "nao", "n", "false", "off", "no", "nao tem"):
        return 0
    raise ValueError("Use Sim ou Não.")


def processador_curto(texto) -> str:
    """"Intel® Core™ i3-13100 CPU @ 3.40GHz" vira "i3-13100" na lista (o nome completo fica na ficha)."""
    curto = re.sub(r"\(R\)|\(TM\)|[®™]|@.*$|\bCPU\b|\bprocessor\b|\bprocessador\b|\bIntel\b|\bCore\b|\bAMD\b",
                   " ", str(texto or ""), flags=re.I)
    curto = re.sub(r"Core(?=i\d)", "", curto, flags=re.I)
    curto = re.sub(r"\bI([3579])-", r"i\1-", " ".join(curto.replace(" - ", "-").split()))
    return curto or str(texto or "")


def _exibir(campo: str, valor) -> str | None:
    """Valor legível para o histórico de alterações."""
    if valor in (None, ""):
        return None
    if campo == "tipo":
        return TIPOS.get(valor, valor)
    if campo == "situacao":
        return SITUACOES.get(valor, valor)
    if campo == "usuario_situacao":
        return SITUACOES_USUARIO.get(valor, valor)
    if campo == "sep":
        return SEP.get(valor, valor)
    if campo in ("nobreak", "webcam", "headset"):
        return "Sim" if valor else "Não"
    if campo == "unidade_id":
        return escalar("SELECT nome FROM unidades WHERE id = ?", (valor,)) or str(valor)
    return str(valor)


def motivos_atencao(c: dict, limite: str | None = None) -> list[str]:
    if c["situacao"] == "desativado":
        return []
    motivos = []
    if c["sep"] == "pendente":
        motivos.append("SEP pendente")
    elif c["sep"] == "nao_instalado":
        motivos.append("Sem SEP")
    if c["usuario_situacao"] == "desligado" and c["situacao"] == "em_uso":
        motivos.append("Usuário desligado")
    if (c["conferido_em"] or c["criado_em"]) < (limite or _limite_conferencia()):
        motivos.append("Conferir")
    return motivos


def _limite_conferencia() -> str:
    return para_bd(agora() - timedelta(days=PRAZO_CONFERENCIA_DIAS))


# ------------------------------------------------------------------ consulta

def _visivel(unidade_id: int):
    """A consulta só abre computadores das unidades liberadas a ela (a equipe de TI vê todas)."""
    if not ve_dados_de_rede():
        garantir_acesso_unidade(unidade_id)


def unidades_visiveis() -> list[dict]:
    restricao, parametros = filtro_unidades_computadores("u.id")
    return consultar(f"""SELECT u.id, u.nome, u.ativo, u.redes,
                                (SELECT COUNT(*) FROM computadores c WHERE c.unidade_id = u.id
                                    AND c.situacao <> 'desativado') AS total
                           FROM unidades u WHERE 1=1 {restricao}
                          ORDER BY u.nome COLLATE NOCASE""", parametros)


def unidades_para_cadastro() -> list[dict]:
    """Unidades ativas em que o usuário pode cadastrar (técnico: só as dele)."""
    editaveis = unidades_editaveis()
    unidades = consultar("SELECT id, nome, redes FROM unidades WHERE ativo = 1 ORDER BY nome COLLATE NOCASE")
    return [u for u in unidades if editaveis is None or u["id"] in editaveis]


def _expressao_busca() -> str:
    campos = ["c.nome", "c.usuario_nome", "c.usuario_login", "c.setor", "c.patrimonio", "c.numero_serie", "c.modelo"]
    if ve_dados_de_rede():
        campos += ["c.ip", "c.ip_wifi", "c.mac", "c.mac_wifi", "c.remoto_id"]
    return " || ' ' || ".join(f"coalesce({c}, '')" for c in campos)


def _texto_busca(texto: str) -> str:
    # MAC digitado com hífen ou ponto vira o formato guardado (02:00:5E...)
    return " ".join(re.sub(r"[-.]", ":", p) if re.fullmatch(r"[0-9A-Fa-f]{2}([-.][0-9A-Fa-f]{2})+", p) else p
                    for p in (texto or "").split())


def montar_filtros(filtros: dict, com_situacao: bool = True) -> tuple[str, list]:
    sql, parametros = "WHERE 1=1", []
    restricao, p = filtro_unidades_computadores("c.unidade_id")
    sql += restricao
    parametros += p
    busca, p = condicao_busca(_texto_busca(filtros.get("q")), _expressao_busca(), digitos=False)
    sql += busca
    parametros += p
    if filtros.get("unidade"):
        sql += " AND c.unidade_id = ?"
        parametros.append(int(filtros["unidade"]))
    if filtros.get("tipo") in TIPOS:
        sql += " AND c.tipo = ?"
        parametros.append(filtros["tipo"])
    if filtros.get("sep") in SEP:
        sql += " AND c.sep = ?"
        parametros.append(filtros["sep"])
    elif filtros.get("sep") == "sem_informacao":
        sql += " AND c.sep IS NULL"
    if filtros.get("setor"):
        sql += " AND c.setor = ? COLLATE NOCASE"
        parametros.append(filtros["setor"])
    if com_situacao:
        situacao = filtros.get("situacao")
        if situacao in SITUACOES:
            sql += " AND c.situacao = ?"
            parametros.append(situacao)
        elif situacao != "todos":
            sql += " AND c.situacao <> 'desativado'"
        if filtros.get("atencao"):
            sql += f" AND {COND_ATENCAO}"
            parametros.append(_limite_conferencia())
    return sql, parametros


def listar(filtros: dict, ordem: str = "nome", por_pagina: int = 25):
    where, parametros = montar_filtros(filtros)
    ordenacao = ORDENACOES.get(ordem, ORDENACOES["nome"])
    pagina = paginar(f"SELECT c.*, u.nome AS unidade_nome FROM computadores c JOIN unidades u ON u.id = c.unidade_id "
                     f"{where} ORDER BY {ordenacao}", parametros, por_pagina=por_pagina)
    limite = _limite_conferencia()
    for item in pagina.itens:
        item["atencao"] = motivos_atencao(item, limite)
    return pagina


def indicadores(filtros: dict) -> dict:
    """Números do topo da lista (respeitam unidade, busca e filtros, menos a situação)."""
    where, parametros = montar_filtros(filtros, com_situacao=False)
    linha = consultar(
        f"""SELECT coalesce(SUM(c.situacao <> 'desativado'), 0) AS total,
                   coalesce(SUM(c.situacao = 'em_uso'), 0) AS em_uso,
                   coalesce(SUM(c.situacao = 'reserva'), 0) AS reserva,
                   coalesce(SUM(c.situacao = 'manutencao'), 0) AS manutencao,
                   coalesce(SUM(c.situacao = 'desativado'), 0) AS desativado,
                   coalesce(SUM({COND_ATENCAO}), 0) AS atencao
              FROM computadores c {where}""",
        [_limite_conferencia()] + parametros, um=True)
    return linha


def obter(computador_id: int):
    computador = consultar(f"{SQL_BASE} WHERE c.id = ?", (computador_id,), um=True)
    if computador:
        _visivel(computador["unidade_id"])
        computador["atencao"] = motivos_atencao(computador)
        base = de_bd(computador["conferido_em"] or computador["criado_em"])
        computador["conferir_ate"] = para_bd(base + timedelta(days=PRAZO_CONFERENCIA_DIAS))
    return computador


def historico(computador_id: int, limite: int = 60):
    return consultar(
        """SELECT h.*, us.nome AS usuario_nome FROM computadores_historico h
             LEFT JOIN usuarios us ON us.id = h.usuario_id
            WHERE h.computador_id = ? ORDER BY h.ocorrido_em DESC, h.id DESC LIMIT ?""",
        (computador_id, limite))


def valores_distintos(coluna: str, limite: int = 300) -> list[str]:
    if coluna not in ("setor", "sistema", "processador", "marca", "monitor"):
        raise ValueError(coluna)
    return [l["v"] for l in consultar(
        f"SELECT DISTINCT {coluna} AS v FROM computadores WHERE {coluna} IS NOT NULL AND {coluna} <> '' "
        f"ORDER BY {coluna} COLLATE NOCASE LIMIT ?", (limite,))]


def buscar(texto: str, por_pagina: int = 12):
    """Para a busca geral do topo."""
    return listar({"q": texto, "situacao": "todos"}, por_pagina=por_pagina)


# ------------------------------------------------------------------ cadastro

def _texto(form, campo, limite, longo=False):
    return (limpar_texto_longo(form.get(campo), limite) if longo else limpar_texto(form.get(campo), limite)) or None


def validar(form: dict, atual: dict | None = None) -> dict:
    erros = {}
    proprio = atual["id"] if atual else 0
    d = {}

    nome = limpar_texto(form.get("nome"), 63).upper()
    if not nome:
        erros["nome"] = "Informe o nome da máquina."
    elif not re.fullmatch(r"[A-Z0-9][A-Z0-9._-]*", nome):
        erros["nome"] = "Use só letras, números, ponto, hífen ou sublinhado, sem espaços."
    elif escalar("SELECT 1 FROM computadores WHERE nome = ? AND id <> ?", (nome, proprio)):
        erros["nome"] = f"Já existe um computador chamado {nome}."
    d["nome"] = nome

    unidade = None
    try:
        unidade_id = int(form.get("unidade_id") or 0)
    except (TypeError, ValueError):
        unidade_id = 0
    if unidade_id:
        unidade = consultar("SELECT id, nome, ativo, redes FROM unidades WHERE id = ?", (unidade_id,), um=True)
    if not unidade:
        erros["unidade_id"] = "Selecione a unidade."
    elif not pode_editar_unidade(unidade["id"]):
        erros["unidade_id"] = "Você só pode cadastrar computadores na sua unidade."
    elif not unidade["ativo"] and not (atual and atual["unidade_id"] == unidade["id"]):
        erros["unidade_id"] = "Esta unidade está inativa."
    d["unidade_id"] = unidade["id"] if unidade else None

    d["tipo"] = form.get("tipo") or ""
    if d["tipo"] not in TIPOS:
        erros["tipo"] = "Escolha Desktop ou Notebook."
    d["situacao"] = form.get("situacao") or ""
    if d["situacao"] not in SITUACOES:
        erros["situacao"] = "Selecione a situação."
    d["desativacao_motivo"] = d["destino_disco"] = None
    if d["situacao"] == "desativado":
        d["desativacao_motivo"] = form.get("desativacao_motivo") or None
        d["destino_disco"] = form.get("destino_disco") or None
        if d["desativacao_motivo"] not in MOTIVOS_DESATIVACAO:
            erros["desativacao_motivo"] = "Informe o motivo."
        if d["destino_disco"] not in DESTINOS_DISCO:
            erros["destino_disco"] = "Informe o que foi feito com o disco (ISO 27001: descarte seguro)."

    d["usuario_nome"] = _texto(form, "usuario_nome", 120)
    d["usuario_login"] = _texto(form, "usuario_login", 60)
    d["setor"] = _texto(form, "setor", 60)
    tem_usuario = bool(d["usuario_nome"] or d["usuario_login"])
    d["usuario_situacao"] = None
    if tem_usuario:
        d["usuario_situacao"] = form.get("usuario_situacao") or "ativo"
        if d["usuario_situacao"] not in SITUACOES_USUARIO:
            erros["usuario_situacao"] = "Selecione a situação do usuário."
    if d["situacao"] == "em_uso" and not tem_usuario:
        erros["usuario_nome"] = "Informe quem usa a máquina ou mude a situação para Reserva."

    d["sistema"] = _texto(form, "sistema", 60)
    d["processador"] = _texto(form, "processador", 80)
    try:
        d["memoria_gb"] = ler_memoria(form.get("memoria_gb"))
    except ValueError as erro:
        erros["memoria_gb"] = str(erro)

    notebook = d["tipo"] == "notebook"
    for campo, funcao in (("ip", normalizar_ip), ("mac", normalizar_mac), ("ip_wifi", normalizar_ip),
                          ("mac_wifi", normalizar_mac)):
        d[campo] = None
        if campo.endswith("_wifi") and not notebook:
            continue
        try:
            d[campo] = funcao(form.get(campo))
        except ValueError as erro:
            erros[campo] = str(erro)
    if d["ip"] and d["ip"] == d["ip_wifi"]:
        erros["ip_wifi"] = "O IP do Wi-Fi deve ser diferente do IP do cabo."
    if d["mac"] and d["mac"] == d["mac_wifi"]:
        erros["mac_wifi"] = "O MAC do Wi-Fi deve ser diferente do MAC do cabo."
    if unidade and unidade["redes"]:
        from .unidades import ler_redes
        redes = ler_redes(unidade["redes"])
        for campo in ("ip", "ip_wifi"):
            if d[campo] and campo not in erros and not any(ipaddress.ip_address(d[campo]) in r for r in redes):
                erros[campo] = f"Este IP não é da rede de {unidade['nome']} ({unidade['redes']})."
    if d["situacao"] != "desativado":  # IP e MAC não se repetem entre computadores ativos
        for campo in ("ip", "ip_wifi"):
            if d[campo] and campo not in erros:
                outro = escalar("SELECT nome FROM computadores WHERE (ip = ? OR ip_wifi = ?) AND situacao <> 'desativado' "
                                "AND id <> ?", (d[campo], d[campo], proprio))
                if outro:
                    erros[campo] = f"Este IP já está no {outro}."
        for campo in ("mac", "mac_wifi"):
            if d[campo] and campo not in erros:
                outro = escalar("SELECT nome FROM computadores WHERE (mac = ? OR mac_wifi = ?) AND situacao <> 'desativado' "
                                "AND id <> ?", (d[campo], d[campo], proprio))
                if outro:
                    erros[campo] = f"Este MAC já está no {outro}."

    d["sep"] = form.get("sep") or None
    if d["sep"] and d["sep"] not in SEP:
        erros["sep"] = "Selecione a situação do SEP."
    ferramenta = (form.get("remoto_ferramenta") or "").strip()
    d["remoto_ferramenta"] = next((f for f in FERRAMENTAS_REMOTO if f.lower() == ferramenta.lower()), None)
    if ferramenta and not d["remoto_ferramenta"]:
        erros["remoto_ferramenta"] = "Escolha AnyDesk, RustDesk, TeamViewer ou Outra."
    d["remoto_id"] = _texto(form, "remoto_id", 40)
    if d["remoto_id"] and not d["remoto_ferramenta"] and "remoto_ferramenta" not in erros:
        erros["remoto_ferramenta"] = "Informe a ferramenta de acesso remoto."

    try:
        d["nobreak"] = ler_sim_nao(form.get("nobreak"))
    except ValueError as erro:
        erros["nobreak"] = str(erro)
    d["monitor"] = _texto(form, "monitor", 40)
    d["webcam"] = 1 if form.get("webcam") in ("1", "on", 1, True) else 0
    d["headset"] = 1 if form.get("headset") in ("1", "on", 1, True) else 0
    d["outros_perifericos"] = _texto(form, "outros_perifericos", 120)
    d["softwares"] = _texto(form, "softwares", 1000, longo=True)
    d["observacoes"] = _texto(form, "observacoes", 2000, longo=True)
    d["marca"] = _texto(form, "marca", 60)
    d["modelo"] = _texto(form, "modelo", 80)
    d["numero_serie"] = (_texto(form, "numero_serie", 60) or "").upper() or None
    d["patrimonio"] = _texto(form, "patrimonio", 40)
    if d["numero_serie"] and escalar("SELECT 1 FROM computadores WHERE numero_serie = ? COLLATE NOCASE AND id <> ?",
                                     (d["numero_serie"], proprio)):
        erros["numero_serie"] = "Já existe um computador com este número de série."
    if d["patrimonio"] and escalar("SELECT 1 FROM computadores WHERE patrimonio = ? COLLATE NOCASE AND id <> ?",
                                   (d["patrimonio"], proprio)):
        erros["patrimonio"] = "Já existe um computador com este patrimônio."

    if erros:
        raise ErroValidacao(erros)
    d["nome_ordem"] = chave_natural(d["nome"])
    d["ip_ordem"] = ordem_ip(d["ip"] or d["ip_wifi"])
    return d


def _registrar(computador_id: int, tipo: str, descricao: str, detalhes=None):
    executar("INSERT INTO computadores_historico (computador_id, ocorrido_em, usuario_id, tipo, descricao, detalhes) "
             "VALUES (?, ?, ?, ?, ?, ?)",
             (computador_id, agora_bd(), usuario_atual_id(), tipo, descricao,
              json.dumps(detalhes, ensure_ascii=False) if detalhes else None))


def criar(form: dict, origem: str = "cadastro") -> int:
    if not usuario_atual():
        raise ErroNegocio("Entre no sistema para cadastrar.")
    d = validar(form)
    garantir_edicao_unidade(d["unidade_id"])
    colunas = COLUNAS_GRAVADAS + ["desativado_em"]
    valores = [d[c] for c in COLUNAS_GRAVADAS] + [agora_bd() if d["situacao"] == "desativado" else None]
    with transacao():
        cursor = executar(f"INSERT INTO computadores ({', '.join(colunas)}) VALUES ({', '.join('?' * len(colunas))})",
                          valores)
        texto = "Computador cadastrado" + (" pela importação de planilha" if origem == "importacao" else "")
        _registrar(cursor.lastrowid, "cadastro", f"{texto}, situação “{SITUACOES[d['situacao']]}”.")
    return cursor.lastrowid


def atualizar(computador_id: int, form: dict) -> bool:
    atual = obter(computador_id)
    if not atual:
        raise ErroNegocio("Computador não encontrado.")
    garantir_edicao_unidade(atual["unidade_id"])
    d = validar(form, atual)
    mudancas = {}
    for campo, rotulo in ROTULOS.items():
        antes, depois = atual.get(campo), d.get(campo)
        if (None if antes == "" else antes) != (None if depois == "" else depois):
            mudancas[rotulo] = [_exibir(campo, antes), _exibir(campo, depois)]
    if not mudancas:
        return False
    desativado_em = atual["desativado_em"]
    if d["situacao"] == "desativado" and atual["situacao"] != "desativado":
        desativado_em, tipo = agora_bd(), "desativacao"
        texto = f"Computador desativado. Motivo: {d['desativacao_motivo']}. Disco: {d['destino_disco']}."
    elif atual["situacao"] == "desativado" and d["situacao"] != "desativado":
        desativado_em, tipo = None, "reativacao"
        texto = f"Computador reativado, situação “{SITUACOES[d['situacao']]}”."
    else:
        tipo, texto = "alteracao", "Dados alterados: " + ", ".join(mudancas) + "."
    with transacao():
        atribuicoes = ", ".join(f"{c} = ?" for c in COLUNAS_GRAVADAS)
        executar(f"UPDATE computadores SET {atribuicoes}, desativado_em = ?, atualizado_em = datetime('now') "
                 "WHERE id = ?", [d[c] for c in COLUNAS_GRAVADAS] + [desativado_em, computador_id])
        _registrar(computador_id, tipo, texto, mudancas)
    return True


def conferir(computador_id: int):
    atual = obter(computador_id)
    if not atual:
        raise ErroNegocio("Computador não encontrado.")
    garantir_edicao_unidade(atual["unidade_id"])
    with transacao():
        executar("UPDATE computadores SET conferido_em = ?, conferido_por = ? WHERE id = ?",
                 (agora_bd(), usuario_atual_id(), computador_id))
        _registrar(computador_id, "conferencia", "Conferência registrada: dados confirmados na máquina.")


def excluir(computador_id: int):
    atual = obter(computador_id)
    if not atual:
        raise ErroNegocio("Computador não encontrado.")
    garantir_edicao_unidade(atual["unidade_id"])
    with transacao():
        executar("DELETE FROM computadores WHERE id = ?", (computador_id,))
        auditar("computador_excluido", "computador", computador_id,
                {"nome": atual["nome"], "unidade": atual["unidade_nome"], "usuario": atual["usuario_nome"]})


# ------------------------------------------------------------------ exportação para Excel

def exportar(filtros: dict, ordem: str, usuario_nome: str) -> tuple[bytes, str, int]:
    from .configuracoes import nome_empresa
    from .relatorios import Coluna, _preencher_aba

    where, parametros = montar_filtros(filtros)
    linhas = consultar(f"{SQL_BASE} {where} ORDER BY {ORDENACOES.get(ordem, 'u.nome COLLATE NOCASE, c.nome_ordem')}",
                       parametros)
    rede = ve_dados_de_rede()
    colunas = [Coluna("unidade_nome", "Unidade", 16), Coluna("nome", "Nome da máquina", 16), Coluna("tipo_t", "Tipo", 11),
               Coluna("situacao_t", "Situação", 13), Coluna("usuario_nome", "Usuário", 24),
               Coluna("usuario_login", "Login", 18), Coluna("setor", "Setor", 16),
               Coluna("usuario_situacao_t", "Situação do usuário", 14), Coluna("sistema", "Sistema operacional", 18),
               Coluna("processador", "Processador", 22), Coluna("memoria_gb", "Memória (GB)", 10, "inteiro")]
    if rede:
        colunas += [Coluna("ip", "IP", 15), Coluna("mac", "MAC", 19), Coluna("ip_wifi", "IP Wi-Fi", 15),
                    Coluna("mac_wifi", "MAC Wi-Fi", 19), Coluna("remoto_ferramenta", "Acesso remoto", 13),
                    Coluna("remoto_id", "ID de acesso remoto", 16)]
    colunas += [Coluna("sep_t", "SEP", 12), Coluna("nobreak_t", "Nobreak", 9), Coluna("monitor", "Monitor", 10),
                Coluna("webcam_t", "Webcam", 8), Coluna("headset_t", "Headset", 8),
                Coluna("outros_perifericos", "Outros periféricos", 20), Coluna("softwares", "Softwares específicos", 26),
                Coluna("marca", "Marca", 12), Coluna("modelo", "Modelo", 18), Coluna("numero_serie", "Nº de série", 16),
                Coluna("patrimonio", "Patrimônio", 12), Coluna("conferido_em", "Conferido em", 16, "datahora"),
                Coluna("observacoes", "Observações", 30), Coluna("desativacao_motivo", "Motivo da desativação", 18),
                Coluna("destino_disco", "Destino do disco", 18), Coluna("desativado_em", "Desativado em", 16, "datahora")]
    for linha in linhas:
        linha["tipo_t"] = TIPOS.get(linha["tipo"])
        linha["situacao_t"] = SITUACOES.get(linha["situacao"])
        linha["usuario_situacao_t"] = SITUACOES_USUARIO.get(linha["usuario_situacao"])
        linha["sep_t"] = SEP.get(linha["sep"])
        linha["nobreak_t"] = None if linha["nobreak"] is None else ("Sim" if linha["nobreak"] else "Não")
        linha["webcam_t"] = "Sim" if linha["webcam"] else "Não"
        linha["headset_t"] = "Sim" if linha["headset"] else "Não"

    livro = Workbook(write_only=True)
    livro.properties.title = "Computadores"
    _preencher_aba(livro.create_sheet("Computadores"), colunas, linhas)
    sobre = livro.create_sheet("Sobre este relatório")
    sobre.column_dimensions["A"].width = 24
    sobre.column_dimensions["B"].width = 60
    unidade = escalar("SELECT nome FROM unidades WHERE id = ?", (filtros["unidade"],)) if filtros.get("unidade") else "Todas"
    info = [("Relatório", "Computadores"), ("Empresa", nome_empresa()), ("Unidade", unidade),
            ("Gerado em", agora_local().strftime("%d/%m/%Y %H:%M")), ("Gerado por", usuario_nome),
            ("Registros", len(linhas))]
    if filtros.get("q"):
        info.append(("Busca", filtros["q"]))
    if not rede:
        info.append(("Observação", "IP, MAC e acesso remoto não são exportados para o perfil de consulta."))
    for rotulo, valor in info:
        celula = WriteOnlyCell(sobre, rotulo)
        celula.font = Font(bold=True)
        sobre.append([celula, valor])
    buffer = BytesIO()
    livro.save(buffer)
    sufixo = re.sub(r"[^a-z0-9]+", "-", normalizar(unidade)).strip("-")
    nome = f"computadores-{sufixo}-{agora_local().strftime('%Y%m%d-%H%M')}.xlsx"
    return buffer.getvalue(), nome, len(linhas)


# ------------------------------------------------------------------ importação de planilha

MAPA_TIPO = {"desktop": "desktop", "pc": "desktop", "computador": "desktop", "notebook": "notebook",
             "note": "notebook", "laptop": "notebook"}
MAPA_SITUACAO = {"em uso": "em_uso", "uso": "em_uso", "reserva": "reserva", "estoque": "reserva",
                 "disponivel": "reserva", "desocupado": "reserva", "manutencao": "manutencao",
                 "em manutencao": "manutencao", "conserto": "manutencao", "em conserto": "manutencao"}
MAPA_USUARIO = {"ativo": "ativo", "ferias": "ferias", "afastado": "ferias", "ferias ou afastado": "ferias",
                "desligado": "desligado"}
MAPA_SEP = {"ok": "ok", "sim": "ok", "instalado": "ok", "pendente": "pendente", "nao": "nao_instalado",
            "nao instalado": "nao_instalado", "sem": "nao_instalado"}


def _form_da_planilha(campos: dict, unidade_id: int) -> dict:
    form = dict(campos)
    form["unidade_id"] = unidade_id
    erros = {}
    tipo = normalizar(campos.get("tipo"))
    form["tipo"] = MAPA_TIPO.get(tipo, tipo)
    situacao = normalizar(campos.get("situacao")).strip(" -")
    if situacao in ("desativado", "baixado", "descartado"):
        erros["situacao"] = "Para desativar, importe como Reserva e desative pela ficha (pede motivo e destino do disco)."
    elif situacao and situacao not in MAPA_SITUACAO:
        erros["situacao"] = "Use Em uso, Reserva ou Manutenção."
    form["situacao"] = MAPA_SITUACAO.get(situacao) or (
        "em_uso" if (campos.get("usuario_nome") or campos.get("usuario_login")) else "reserva")
    usuario = normalizar(campos.get("usuario_situacao"))
    if usuario and usuario not in MAPA_USUARIO:
        erros["usuario_situacao"] = "Use Ativo, Férias ou Desligado."
    form["usuario_situacao"] = MAPA_USUARIO.get(usuario, "")
    sep = normalizar(campos.get("sep")).strip(" -")
    if sep and sep not in MAPA_SEP:
        erros["sep"] = "Use OK, Pendente ou Não instalado."
    form["sep"] = MAPA_SEP.get(sep, "")
    if campos.get("anydesk") and not campos.get("remoto_id"):
        form["remoto_id"] = campos["anydesk"]
        form["remoto_ferramenta"] = campos.get("remoto_ferramenta") or "AnyDesk"
    perifericos = [p.strip() for p in re.split(r"[/,;+]", campos.get("perifericos") or "") if p.strip()]
    outros = []
    for item in perifericos:
        chave = normalizar(item).replace(" ", "").replace("-", "")
        if chave == "webcam":
            form["webcam"] = "1"
        elif chave == "headset":
            form["headset"] = "1"
        elif chave not in ("n", "n/", "na", "nao", "nenhum"):
            outros.append(item)
    if outros and not campos.get("outros_perifericos"):
        form["outros_perifericos"] = ", ".join(outros)
    monitor = (campos.get("monitor") or "").strip()
    if re.fullmatch(r"\d+([.,]\d+)?", monitor):
        form["monitor"] = monitor.replace(".", ",") + '"'
    elif monitor in ("-", "—"):
        form["monitor"] = ""
    for campo in ("mac", "ip", "nobreak", "remoto_id", "memoria_gb"):
        if (form.get(campo) or "").strip() in ("-", "—"):
            form[campo] = ""
    if erros:
        raise ErroValidacao(erros)
    return form


def importar_planilha(conteudo: bytes, unidade_id: int, gravar: bool) -> dict:
    from .importacao import MODELOS, ler_planilha
    garantir_edicao_unidade(unidade_id)
    registros, estrutura = ler_planilha("computadores", conteudo)
    resultado = {"linhas": len(registros), "erros": [], "estrutura": estrutura, "gravado": False}
    if estrutura:
        return resultado
    rotulos = {campo: rotulo for campo, rotulo, *_ in MODELOS["computadores"]["colunas"]}
    rotulos.update(ROTULOS)
    conexao = obter_db()
    conexao.execute("BEGIN IMMEDIATE")
    try:
        for numero, campos in registros:
            conexao.execute("SAVEPOINT linha")
            try:
                criar(_form_da_planilha(campos, unidade_id), origem="importacao")
                conexao.execute("RELEASE linha")
            except ErroValidacao as erro:
                conexao.execute("ROLLBACK TO linha")
                conexao.execute("RELEASE linha")
                for campo, mensagem in erro.erros.items():
                    resultado["erros"].append({"linha": numero, "coluna": rotulos.get(campo, campo),
                                               "mensagem": mensagem})
        if gravar and not resultado["erros"]:
            conexao.execute("COMMIT")
            resultado["gravado"] = True
        else:
            conexao.execute("ROLLBACK")
    except BaseException:
        if conexao.in_transaction:
            conexao.execute("ROLLBACK")
        raise
    if resultado["gravado"]:
        auditar("importacao", "computadores", None, {"linhas": len(registros), "unidade_id": unidade_id})
    return resultado
