"""Chamados de TI ("Pedidos para a TI"): abertura, atendimento, prazos e permissões.

Permissões (conferidas aqui, no servidor, em toda ação — a tela só esconde o que não pode):
- Administrador: tudo.
- Técnico: vê a fila de todas as unidades (só leitura nas que não atende) e escreve nota interna em
  qualquer chamado que pode ver; assume, responde, muda situação e prioridade, transfere e abre chamado
  por alguém só nas unidades que atende. Chamados de Segurança da informação aparecem apenas para quem
  atende a unidade (e para o administrador).
- Colaborador: só os próprios pedidos, sem as notas internas da TI.
- Usuário de consulta: não acessa chamados.
"""
import re
import secrets
from datetime import date, timedelta
from pathlib import Path

from flask import current_app

from ..db import consultar, escalar, executar, normalizar, transacao
from ..util import Pagina, agora, agora_bd, agora_local, de_bd, fmt_dia_hora, ler_datahora_local, local, para_bd
from ..validacao import email_valido, limpar_texto, limpar_texto_longo
from . import ErroNegocio, ErroValidacao
from . import expediente
from .configuracoes import cfg
from .registro import auditar


class SemPermissao(ErroNegocio):
    """A pessoa não pode fazer isto neste chamado (vira 403 na tela)."""


# chave: (nome na TI, texto para o colaborador, ícone)
CATEGORIAS = {
    "computador": ("Computador", "Computador", "computador"),
    "celular": ("Celular", "Celular", "celular"),
    "impressora": ("Impressora", "Impressora", "impressora"),
    "rede": ("Rede/Internet", "Internet", "wifi"),
    "acesso": ("Acesso e senha", "Senha ou acesso", "senha"),
    "sistemas": ("Sistemas", "Programa", "janela"),
    "seguranca": ("Segurança da informação", "E-mail ou site estranho", "escudo-alerta"),
    "outro": ("Outro", "Outra coisa", "balao"),
}
EXEMPLOS = {
    "computador": "Ex.: o computador está muito lento desde ontem e trava quando abro o navegador.",
    "celular": "Ex.: o celular não carrega mais, mesmo ligado na tomada.",
    "impressora": "Ex.: a impressora do setor está puxando duas folhas e amassando o papel.",
    "rede": "Ex.: a internet cai toda hora desde hoje cedo.",
    "acesso": "Ex.: não consigo entrar no programa do ponto; aparece “usuário bloqueado”.",
    "sistemas": "Ex.: o Excel fecha sozinho quando abro a planilha de custos.",
    "seguranca": "Ex.: recebi um e-mail pedindo para clicar num link e confirmar minha senha. Cliquei no link.",
    "outro": "Ex.: preciso de um teclado novo; o meu está com teclas falhando.",
}
PRIORIDADES = {"alta": "Alta", "media": "Média", "baixa": "Baixa"}
IMPACTOS = {"nao": ("Não", "baixa"), "pouco": ("Um pouco", "media"), "parou": ("Sim, parei", "alta")}
SITUACOES = {"novo": "Novo", "atendimento": "Em atendimento", "aguardando": "Aguardando usuário",
             "resolvido": "Resolvido", "fechado": "Fechado"}
SITUACOES_COLABORADOR = {"novo": "Recebido", "atendimento": "A TI está resolvendo",
                         "aguardando": "A TI precisa de uma resposta sua", "resolvido": "Resolvido",
                         "fechado": "Encerrado"}
CANAIS = {"telefone": "por telefone", "whatsapp": "pelo WhatsApp", "pessoalmente": "pessoalmente",
          "email": "por e-mail", "teams": "pelo Teams"}
ABERTAS = ("novo", "atendimento", "aguardando")
CORRENDO = ("novo", "atendimento")  # o prazo só corre nestas situações
FILTROS_SITUACAO = {
    "abertos": "Abertos", "novo": "Novos", "atendimento": "Em atendimento", "aguardando": "Aguardando usuário",
    "atrasados": "Atrasados", "concluidos": "Concluídos", "resolvido": "Resolvidos", "fechado": "Fechados",
    "todos": "Todos",
}
POR_PAGINA = 25
LIMITE_ANEXO = 10 * 1024 * 1024
TIPOS_ANEXO = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
               "image/heic": ".heic", "application/pdf": ".pdf"}
PADRAO_ARQUIVO = re.compile(r"[0-9a-f]{24}\.(jpg|png|gif|webp|heic|pdf)")


# ------------------------------------------------------------------ permissões

def eh_ti(usuario) -> bool:
    return bool(usuario) and usuario.get("perfil") in ("admin", "tecnico")


def unidades_atendidas(usuario):
    """None = todas (administrador); conjunto vazio = nenhuma."""
    if not usuario:
        return set()
    if usuario["perfil"] == "admin":
        return None
    if usuario["perfil"] == "tecnico":
        return set(usuario.get("unidades") or ())
    return set()


def atende_unidade(usuario, unidade_id) -> bool:
    unidades = unidades_atendidas(usuario)
    return eh_ti(usuario) and (unidades is None or unidade_id in unidades)


def pode_ver(usuario, chamado) -> bool:
    if not chamado or not eh_ti(usuario):
        return False
    if chamado["categoria"] == "seguranca":
        return atende_unidade(usuario, chamado["unidade_id"])
    return True


def pode_atender(usuario, chamado) -> bool:
    return bool(chamado) and atende_unidade(usuario, chamado["unidade_id"])


def _exigir_atender(usuario, chamado):
    if not pode_atender(usuario, chamado):
        raise SemPermissao("Este chamado é de uma unidade que você não atende. Você pode ler e escrever nota "
                           "interna, mas não alterar.")


def _visibilidade(usuario, alias: str = "c") -> tuple[str, list]:
    unidades = unidades_atendidas(usuario)
    if unidades is None:
        return "", []
    if not unidades:
        return f" AND {alias}.categoria <> 'seguranca'", []
    marcas = ",".join("?" * len(unidades))
    return f" AND ({alias}.categoria <> 'seguranca' OR {alias}.unidade_id IN ({marcas}))", sorted(unidades)


# ------------------------------------------------------------------ textos auxiliares

def resumo(texto: str, limite: int = 90) -> str:
    linha = " ".join((texto or "").split())
    if len(linha) <= limite:
        return linha
    corte = linha[:limite].rsplit(" ", 1)[0] or linha[:limite]
    return corte.rstrip(".,;:–- ") + "…"


def rotulo_computador(c: dict) -> str:
    tipo = {"notebook": "Notebook", "desktop": "Computador de mesa"}.get(c.get("tipo"), "Computador")
    modelo = " ".join(p for p in (c.get("marca"), c.get("modelo")) if p)
    return f"{tipo} {modelo}".strip()


def rotulo_para_pessoa(c: dict) -> str:
    """Como o colaborador reconhece o próprio computador: "Meu computador da mesa (Fiscal)", "Meu notebook"."""
    if c.get("tipo") == "notebook":
        return "Meu notebook"
    setor = (c.get("setor") or "").strip()
    return f"Meu computador da mesa ({setor})" if setor else "Meu computador da mesa"


def rotulo_aparelho(a: dict) -> str:
    return " ".join(p for p in (a.get("marca"), a.get("modelo")) if p) or "Celular"


def numero(chamado_id) -> str:
    return str(int(chamado_id))


# ------------------------------------------------------------------ equipamentos da pessoa

def computador_por_ip(ip: str):
    if not ip:
        return None
    return consultar("SELECT id, nome, tipo, marca, modelo, unidade_id FROM computadores "
                     "WHERE (ip = ? OR ip_wifi = ?) AND situacao <> 'desativado' ORDER BY id LIMIT 1",
                     (ip, ip), um=True)


def computadores_da_pessoa(usuario) -> list:
    login = (usuario.get("login_ad") or "").lower()
    nome = normalizar(usuario.get("nome"))
    if not login and not nome:
        return []
    candidatos = consultar(
        "SELECT id, nome, tipo, marca, modelo, setor, usuario_login, usuario_nome FROM computadores "
        "WHERE situacao <> 'desativado' AND (instr(lower(COALESCE(usuario_login, '')), ?) > 0 "
        "OR normalizar(usuario_nome) = ?) ORDER BY nome LIMIT 30", (login or "\x00", nome or "\x00"))
    escolhidos = []
    for c in candidatos:
        login_inv = (c["usuario_login"] or "").lower().rsplit("\\", 1)[-1].split("@", 1)[0].strip()
        if (login and login_inv == login) or (nome and normalizar(c["usuario_nome"]) == nome):
            escolhidos.append(c)
    return escolhidos[:10]


def celulares_da_pessoa(usuario) -> list:
    email = (usuario.get("email") or "").lower()
    sql = ("SELECT a.id, a.marca, a.modelo FROM vinculos v JOIN colaboradores co ON co.id = v.colaborador_id "
           "JOIN aparelhos a ON a.id = v.aparelho_id WHERE v.devolvido_em IS NULL AND {} ORDER BY a.marca, a.modelo")
    if email:
        lista = consultar(sql.format("lower(co.email) = ?"), (email,))
        if lista:
            return lista
    nome = normalizar(usuario.get("nome"))
    return consultar(sql.format("co.situacao = 'ativo' AND normalizar(co.nome) = ?"), (nome,)) if nome else []


def _rotulos_unicos(itens: list, rotulo, extra) -> list:
    contagem = {}
    for item in itens:
        contagem[rotulo(item)] = contagem.get(rotulo(item), 0) + 1
    return [{"id": i["id"], "rotulo": rotulo(i) + (f" – {extra(i)}" if contagem[rotulo(i)] > 1 and extra(i) else "")}
            for i in itens]


# O passo 2 ("qual equipamento?") aparece sempre para estas categorias; as outras vão direto ao passo 3.
CATEGORIAS_COM_EQUIPAMENTO = ("computador", "celular")
# Quando a pessoa não tem computador no inventário, a lista mostra estas opções (vão como texto para a TI).
COMPUTADORES_SEM_CADASTRO = {
    "pc:mesa": ("Meu computador da mesa", "O computador de mesa da pessoa (não encontrado no inventário)."),
    "pc:notebook": ("Meu notebook", "O notebook da pessoa (não encontrado no inventário)."),
}


def opcoes_equipamento(usuario, ip: str) -> dict:
    origem = computador_por_ip(ip)
    meus = [c for c in computadores_da_pessoa(usuario) if not origem or c["id"] != origem["id"]]
    return {
        "origem": origem,
        "computadores": _rotulos_unicos(meus, rotulo_para_pessoa,
                                        lambda c: " ".join(p for p in (c.get("marca"), c.get("modelo")) if p) or c["nome"]),
        "celulares": _rotulos_unicos(celulares_da_pessoa(usuario), rotulo_aparelho, lambda a: None),
    }


# ------------------------------------------------------------------ prazos

def sla_minutos(prioridade: str, exp=None) -> int:
    exp = exp or expediente.atual()
    return exp.duracao_em_minutos(cfg(f"chamados_prazo_{prioridade}")) or 240


def _prazos(criado_em, prioridade: str, pausa_total: int, exp) -> tuple[str, str]:
    inicio = de_bd(criado_em)
    sla = sla_minutos(prioridade, exp)
    return para_bd(exp.somar_minutos(inicio, sla)), para_bd(exp.somar_minutos(inicio, sla + pausa_total))


def _fim_da_pausa(chamado, momento, exp) -> dict:
    """Ao sair de "Aguardando usuário" ou de "Resolvido": o tempo parado não conta no prazo."""
    pausa = chamado["pausa_total_min"] or 0
    if chamado["pausado_em"]:
        pausa += max(0, exp.minutos_entre(de_bd(chamado["pausado_em"]), momento))
    base, prazo = _prazos(chamado["criado_em"], chamado["prioridade"], pausa, exp)
    return {"pausa_total_min": pausa, "prazo_base_em": base, "prazo_em": prazo, "pausado_em": None,
            "aviso_prazo": None}


def situacao_do_prazo(chamado, exp=None) -> dict:
    """Para as telas. "curto" vai na fila ("atrasado 30 min", "vence em 1 h", "hoje, 17:12", "pausado");
    "quando" + "detalhe" vão no painel do atendimento ("hoje, 17:12 · faltam 3 h")."""
    if chamado["situacao"] in ("resolvido", "fechado"):
        no_prazo = chamado.get("resolvido_no_prazo")
        if no_prazo is None:  # encerrado sem solução (a pessoa não respondeu)
            return {"estado": "encerrado", "curto": "—", "quando": "—", "detalhe": ""}
        texto = "no prazo" if no_prazo else "fora do prazo"
        return {"estado": "no-prazo" if no_prazo else "fora-do-prazo", "curto": texto,
                "quando": fmt_dia_hora(chamado["resolvido_em"]), "detalhe": f"resolvido {texto}"}
    if chamado["situacao"] == "aguardando":
        return {"estado": "pausado", "curto": "pausado", "quando": "pausado", "detalhe": "aguardando o usuário"}
    exp = exp or expediente.atual()
    prazo = de_bd(chamado["prazo_em"])
    quando = fmt_dia_hora(chamado["prazo_em"])
    restante = exp.minutos_entre(agora(), prazo)
    if prazo <= agora():
        atraso = -restante
        texto = f"atrasado {expediente.texto_duracao(atraso)}" if atraso >= 1 else f"venceu {quando}"
        return {"estado": "atrasado", "curto": texto, "quando": quando, "detalhe": texto}
    limite = max(30, sla_minutos(chamado["prioridade"], exp) // 4)
    return {"estado": "perto" if restante <= limite else "ok",
            "curto": f"vence em {expediente.texto_duracao(restante)}" if restante <= 120 else quando,
            "quando": quando, "detalhe": f"faltam {expediente.texto_duracao(restante)}"}


def prazo_anpd(chamado) -> date:
    """3 dias úteis a partir de quando a TI soube (Resolução CD/ANPD nº 15/2024)."""
    base = local(chamado["seg_conhecido_em"] or chamado["criado_em"]).date()
    dia, contados = base, 0
    while contados < 3:
        dia += timedelta(days=1)
        if dia.isoweekday() <= 5 and dia not in expediente.feriados_do_ano(dia.year, carnaval=False,
                                                                          corpus_christi=False):
            contados += 1
    return dia


# ------------------------------------------------------------------ anexos

def _tipo_por_conteudo(cabeca: bytes) -> str | None:
    if cabeca.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if cabeca.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if cabeca[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if cabeca[:4] == b"RIFF" and cabeca[8:12] == b"WEBP":
        return "image/webp"
    if cabeca[4:8] == b"ftyp" and cabeca[8:12] in (b"heic", b"heix", b"mif1", b"msf1", b"heis", b"hevc"):
        return "image/heic"
    if cabeca.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def ler_anexo(arquivo) -> dict | None:
    """Confere o arquivo enviado (só imagens e PDF, até 10 MB) pelo conteúdo, não pelo nome."""
    if not arquivo or not getattr(arquivo, "filename", ""):
        return None
    dados = arquivo.read(LIMITE_ANEXO + 1)
    if not dados:
        return None
    if len(dados) > LIMITE_ANEXO:
        raise ErroValidacao({"anexo": "O arquivo passa de 10 MB. Envie uma foto menor ou um print da tela."})
    tipo = _tipo_por_conteudo(dados[:16])
    if not tipo:
        raise ErroValidacao({"anexo": "Envie só foto, print (JPG ou PNG) ou PDF."})
    nome = re.sub(r"[\x00-\x1f\\/:*?\"<>|]+", "_", Path(arquivo.filename).name).strip(" ._") or "anexo"
    if not nome.lower().endswith(TIPOS_ANEXO[tipo]) and not (tipo == "image/jpeg" and nome.lower().endswith(".jpeg")):
        nome = Path(nome).stem + TIPOS_ANEXO[tipo]
    return {"nome": nome[:120], "tipo": tipo, "dados": dados}


def _pasta_anexos(chamado_id: int) -> Path:
    return Path(current_app.config["PASTA_UPLOADS"]) / "chamados" / str(int(chamado_id))


def _gravar_anexo(chamado_id: int, mensagem_id: int, anexo: dict, usuario_id):
    pasta = _pasta_anexos(chamado_id)
    pasta.mkdir(parents=True, exist_ok=True)
    arquivo = secrets.token_hex(12) + TIPOS_ANEXO[anexo["tipo"]]
    (pasta / arquivo).write_bytes(anexo["dados"])
    executar("INSERT INTO chamados_anexos (chamado_id, mensagem_id, nome, arquivo, tipo, tamanho, criado_por) "
             "VALUES (?, ?, ?, ?, ?, ?, ?)",
             (chamado_id, mensagem_id, anexo["nome"], arquivo, anexo["tipo"], len(anexo["dados"]), usuario_id))


def obter_anexo(anexo_id: int):
    return consultar("SELECT an.*, m.tipo AS mensagem_tipo, m.publico AS mensagem_publico "
                     "FROM chamados_anexos an LEFT JOIN chamados_mensagens m ON m.id = an.mensagem_id "
                     "WHERE an.id = ?", (anexo_id,), um=True)


def caminho_anexo(anexo: dict) -> Path | None:
    if not anexo or not PADRAO_ARQUIVO.fullmatch(anexo["arquivo"] or ""):
        return None
    caminho = _pasta_anexos(anexo["chamado_id"]) / anexo["arquivo"]
    return caminho if caminho.is_file() else None


# ------------------------------------------------------------------ gravação

def _mensagem(chamado_id: int, autor_id, tipo: str, texto: str, publico: bool) -> int:
    return executar("INSERT INTO chamados_mensagens (chamado_id, autor_id, tipo, texto, publico) VALUES (?, ?, ?, ?, ?)",
                    (chamado_id, autor_id, tipo, texto, 1 if publico else 0)).lastrowid


def _evento(chamado_id: int, texto: str, autor_id=None, publico: bool = False):
    _mensagem(chamado_id, autor_id, "evento", texto, publico)


def _atualizar(chamado_id: int, campos: dict):
    campos = dict(campos, atualizado_em=agora_bd())
    executar(f"UPDATE chamados SET {', '.join(f'{c} = ?' for c in campos)} WHERE id = ?",
             (*campos.values(), chamado_id))


def _inserir(**dados) -> int:
    exp = expediente.atual()
    criado = agora_bd()
    base, prazo = _prazos(criado, dados["prioridade"], 0, exp)
    dados.update(prazo_base_em=base, prazo_em=prazo, criado_em=criado, atualizado_em=criado,
                 ultima_interacao_em=criado, avisos_enviados=0)
    colunas = ", ".join(dados)
    chamado_id = executar(f"INSERT INTO chamados ({colunas}) VALUES ({', '.join('?' * len(dados))})",
                          tuple(dados.values())).lastrowid
    recalcular_busca(chamado_id=chamado_id)
    return chamado_id


def recalcular_busca(*, chamado_id=None, solicitante_id=None):
    """Mantém a coluna de busca da fila (sem acentos, para não precisar normalizar milhares de linhas a cada busca)."""
    coluna, valor = ("id", chamado_id) if chamado_id else ("solicitante_id", solicitante_id)
    executar(f"""UPDATE chamados SET busca = (
                   SELECT normalizar(chamados.assunto || ' ' || s.nome || ' ' || COALESCE(s.login_ad, '') || ' ' ||
                          COALESCE(s.setor, '') || ' ' ||
                          COALESCE((SELECT nome FROM computadores WHERE id = chamados.computador_id), '') || ' ' ||
                          COALESCE((SELECT nome FROM computadores WHERE id = chamados.origem_computador_id), ''))
                   FROM usuarios s WHERE s.id = chamados.solicitante_id)
                 WHERE {coluna} = ?""", (valor,))


def _ler_texto(form, campo: str = "texto", minimo: int = 2, anexo=None) -> str:
    texto = limpar_texto_longo(form.get(campo), 6000)
    if len(texto) < minimo and not (anexo and not texto):
        raise ErroValidacao({campo: "Escreva a mensagem."})
    return texto or "(anexo)"


# ------------------------------------------------------------------ abertura

def criar_pedido(usuario, form, anexo, ip: str) -> int:
    """Pedido aberto pelo próprio colaborador (3 passos)."""
    erros = {}
    categoria = form.get("categoria") or ""
    if categoria not in CATEGORIAS:
        erros["categoria"] = "Escolha o que está com problema."
    descricao = limpar_texto_longo(form.get("descricao"), 4000)
    if len(descricao) < 5:
        erros["descricao"] = "Conte com algumas palavras o que aconteceu."
    impacto = form.get("impacto") or ""
    if categoria != "seguranca" and impacto not in IMPACTOS:
        erros["impacto"] = "Responda se isso está impedindo o seu trabalho."
    if erros:
        raise ErroValidacao(erros, "Falta responder: " + " ".join(erros.values()))
    if not usuario.get("unidade_id"):
        raise ErroNegocio("Sua unidade ainda não usa este sistema. Fale com a TI.")
    prioridade = "alta" if categoria == "seguranca" else IMPACTOS[impacto][1]
    origem = computador_por_ip(ip)
    computador_id = aparelho_id = equipamento_texto = None
    escolha = form.get("equipamento") or ""
    if categoria == "computador":
        validos = {c["id"] for c in computadores_da_pessoa(usuario)} | ({origem["id"]} if origem else set())
        if escolha in ("este", "origem"):
            if origem:
                computador_id = origem["id"]
            else:
                equipamento_texto = "Neste computador (o que a pessoa estava usando; não está no inventário)."
        elif _id_de(escolha, "comp:") in validos:
            computador_id = _id_de(escolha, "comp:")
        elif escolha in COMPUTADORES_SEM_CADASTRO:
            equipamento_texto = COMPUTADORES_SEM_CADASTRO[escolha][1]
        elif escolha:
            equipamento_texto = "A pessoa não sabe qual é o computador."
    elif categoria == "celular":
        alvo = _id_de(escolha, "cel:")
        if alvo in {a["id"] for a in celulares_da_pessoa(usuario)}:
            aparelho_id = alvo
        elif escolha == "cel:empresa":
            equipamento_texto = "O celular da empresa que está com a pessoa (não encontrado no cadastro)."
        elif escolha:
            equipamento_texto = "A pessoa não sabe qual é o celular."
    with transacao():
        chamado_id = _inserir(
            unidade_id=usuario["unidade_id"], solicitante_id=usuario["id"], aberto_por_id=usuario["id"],
            categoria=categoria, impacto=impacto if impacto in IMPACTOS else None, prioridade=prioridade,
            situacao="novo", assunto=resumo(descricao), computador_id=computador_id, aparelho_id=aparelho_id,
            equipamento_texto=equipamento_texto, origem_ip=(ip or "")[:45] or None,
            origem_computador_id=origem["id"] if origem else None)
        mensagem_id = _mensagem(chamado_id, usuario["id"], "mensagem", descricao, True)
        if anexo:
            _gravar_anexo(chamado_id, mensagem_id, anexo, usuario["id"])
        auditar("chamado_aberto", "chamado", chamado_id, {"categoria": categoria, "prioridade": prioridade})
    from . import avisos_chamados
    avisos_chamados.ao_abrir(chamado_id)
    return chamado_id


def _id_de(valor: str, prefixo: str) -> int | None:
    if valor and valor.startswith(prefixo) and valor[len(prefixo):].isdigit():
        return int(valor[len(prefixo):])
    return None


def pessoas_conhecidas(unidades=None, limite: int = 800) -> list:
    """Para o campo "Quem pediu?" da TI (lista de sugestões)."""
    sql = ("SELECT u.id, u.nome, u.login_ad, u.setor, un.nome AS unidade_nome FROM usuarios u "
           "LEFT JOIN unidades un ON un.id = u.unidade_id WHERE u.ativo = 1 AND u.login_ad IS NOT NULL")
    parametros = []
    if unidades is not None:
        if not unidades:
            return []
        sql += f" AND u.unidade_id IN ({','.join('?' * len(unidades))})"
        parametros += sorted(unidades)
    return consultar(sql + " ORDER BY u.nome COLLATE NOCASE LIMIT ?", (*parametros, limite))


def criar_por_ti(tecnico, form, anexo) -> int:
    """Chamado aberto pela TI em nome de alguém (pedido por telefone, WhatsApp, pessoalmente...)."""
    from .ad import normalizar_login
    erros = {}
    try:
        unidade_id = int(form.get("unidade_id") or 0)
    except ValueError:
        unidade_id = 0
    unidade = consultar("SELECT id, nome, ativo FROM unidades WHERE id = ?", (unidade_id,), um=True)
    if not unidade or not unidade["ativo"]:
        erros["unidade_id"] = "Selecione a unidade."
    elif not atende_unidade(tecnico, unidade_id):
        raise SemPermissao("Você só abre chamados das unidades que atende.")
    digitado = (form.get("login") or "").strip()
    login = normalizar_login(digitado) if digitado else None
    if digitado and not login:
        erros["login"] = "Login inválido. Use o formato nome.sobrenome."
    nome = limpar_texto(form.get("nome"), 120)
    email = (form.get("email") or "").strip().lower()
    if email and not email_valido(email):
        erros["email"] = "E-mail inválido."
    pessoa = consultar("SELECT * FROM usuarios WHERE login_ad = ?", (login,), um=True) if login else None
    if not pessoa and len(nome) < 3:
        erros["nome"] = "Informe o nome de quem pediu."
    categoria = form.get("categoria") or ""
    if categoria not in CATEGORIAS:
        erros["categoria"] = "Selecione a categoria."
    prioridade = "alta" if categoria == "seguranca" else (form.get("prioridade") or "")
    if prioridade not in PRIORIDADES:
        erros["prioridade"] = "Selecione a prioridade."
    descricao = limpar_texto_longo(form.get("descricao"), 4000)
    if len(descricao) < 5:
        erros["descricao"] = "Descreva o pedido."
    canal = form.get("canal") if form.get("canal") in CANAIS else "telefone"
    computador_id = _id_existente("computadores", form.get("computador_id"))
    aparelho_id = _id_existente("aparelhos", form.get("aparelho_id"))
    if form.get("computador_id") and not computador_id:
        erros["computador_id"] = "Computador não encontrado."
    if erros:
        raise ErroValidacao(erros)
    assumir = form.get("assumir") in ("1", "on")
    with transacao():
        if not pessoa and not login:  # sem usuário no AD: procura pelo nome na mesma unidade
            pessoa = consultar("SELECT * FROM usuarios WHERE login_ad IS NULL AND perfil = 'colaborador' "
                               "AND unidade_id = ? AND normalizar(nome) = ?", (unidade_id, normalizar(nome)), um=True)
        if not pessoa:
            pessoa_id = executar(
                "INSERT INTO usuarios (nome, email, perfil, origem, login_ad, unidade_id, ativo) "
                "VALUES (?, ?, 'colaborador', 'ad', ?, ?, ?)",
                (nome, email or None, login, unidade_id, 1 if login else 0)).lastrowid
            auditar("usuario_pre_cadastrado", "usuario", pessoa_id, {"login": login, "nome": nome})
        else:
            pessoa_id = pessoa["id"]
            if email and not pessoa["email"]:
                executar("UPDATE usuarios SET email = ? WHERE id = ?", (email, pessoa_id))
        chamado_id = _inserir(
            unidade_id=unidade_id, solicitante_id=pessoa_id, aberto_por_id=tecnico["id"], categoria=categoria,
            impacto=None, prioridade=prioridade, situacao="atendimento" if assumir else "novo",
            responsavel_id=tecnico["id"] if assumir else None, assumido_em=agora_bd() if assumir else None,
            assunto=resumo(descricao), computador_id=computador_id, aparelho_id=aparelho_id)
        mensagem_id = _mensagem(chamado_id, pessoa_id, "mensagem", descricao, True)
        if anexo:
            _gravar_anexo(chamado_id, mensagem_id, anexo, tecnico["id"])
        _evento(chamado_id, f"Pedido registrado pela TI ({tecnico['nome']}), {CANAIS[canal]}.", tecnico["id"], True)
        if assumir:
            _evento(chamado_id, f"{tecnico['nome']} (TI) começou a atender.", tecnico["id"], True)
        auditar("chamado_aberto", "chamado", chamado_id,
                {"categoria": categoria, "prioridade": prioridade, "em_nome_de": pessoa_id, "canal": canal})
    from . import avisos_chamados
    avisos_chamados.ao_abrir(chamado_id, aberto_pela_ti=True)
    return chamado_id


def _id_existente(tabela: str, valor) -> int | None:
    try:
        numero_id = int(valor or 0)
    except ValueError:
        return None
    return numero_id if numero_id and escalar(f"SELECT id FROM {tabela} WHERE id = ?", (numero_id,)) else None


# ------------------------------------------------------------------ atendimento (TI)

def assumir(chamado, usuario):
    _exigir_atender(usuario, chamado)
    if chamado["situacao"] in ("resolvido", "fechado"):
        raise ErroNegocio("Este chamado já foi resolvido. Reabra antes de assumir.")
    if chamado["responsavel_id"] == usuario["id"]:
        return
    with transacao():
        _atualizar(chamado["id"], {"responsavel_id": usuario["id"],
                                   "situacao": "atendimento" if chamado["situacao"] == "novo" else chamado["situacao"],
                                   "assumido_em": chamado["assumido_em"] or agora_bd()})
        _evento(chamado["id"], f"{usuario['nome']} (TI) começou a atender.", usuario["id"], True)
        auditar("chamado_assumido", "chamado", chamado["id"], {"anterior": chamado["responsavel_id"]})
    from . import avisos_chamados
    avisos_chamados.ao_assumir(chamado["id"], usuario["nome"])


def responder(chamado, usuario, form, anexo) -> int:
    _exigir_atender(usuario, chamado)
    if chamado["situacao"] == "fechado":
        raise ErroNegocio("Este chamado está fechado.")
    texto = _ler_texto(form, anexo=anexo)
    aguardar = form.get("aguardar") in ("1", "on")
    momento = agora()
    exp = expediente.atual()
    campos = {"ultima_interacao_em": para_bd(momento), "resposta_em": None}
    if not chamado["responsavel_id"]:
        campos.update(responsavel_id=usuario["id"], assumido_em=chamado["assumido_em"] or para_bd(momento))
    if chamado["situacao"] == "aguardando" and not aguardar:
        campos.update(_fim_da_pausa(chamado, momento, exp), situacao="atendimento")
    elif aguardar and chamado["situacao"] in ("novo", "atendimento"):
        campos.update(situacao="aguardando", pausado_em=para_bd(momento))
    elif chamado["situacao"] == "novo":
        campos["situacao"] = "atendimento"
    with transacao():
        mensagem_id = _mensagem(chamado["id"], usuario["id"], "resposta", texto, True)
        if anexo:
            _gravar_anexo(chamado["id"], mensagem_id, anexo, usuario["id"])
        _atualizar(chamado["id"], campos)
        if campos.get("situacao") and campos["situacao"] != chamado["situacao"]:
            auditar("chamado_situacao", "chamado", chamado["id"], {"de": chamado["situacao"], "para": campos["situacao"]})
        auditar("chamado_respondido", "chamado", chamado["id"], {"aguardar": aguardar})
    from . import avisos_chamados
    if not chamado["responsavel_id"]:  # responder sem assumir antes também conta como assumir
        avisos_chamados.ao_assumir(chamado["id"], usuario["nome"])
    avisos_chamados.ao_responder_ti(chamado["id"], mensagem_id)
    return mensagem_id


def nota(chamado, usuario, form, anexo) -> int:
    if not pode_ver(usuario, chamado):
        raise SemPermissao("Você não tem acesso a este chamado.")
    texto = _ler_texto(form, anexo=anexo)
    with transacao():
        mensagem_id = _mensagem(chamado["id"], usuario["id"], "nota", texto, False)
        if anexo:
            _gravar_anexo(chamado["id"], mensagem_id, anexo, usuario["id"])
        _atualizar(chamado["id"], {})
        auditar("chamado_nota", "chamado", chamado["id"])
    return mensagem_id


def resolver(chamado, usuario):
    _exigir_atender(usuario, chamado)
    if chamado["situacao"] in ("resolvido", "fechado"):
        raise ErroNegocio("Este chamado já está resolvido.")
    momento = agora()
    campos = {}
    if chamado["situacao"] == "aguardando":
        campos.update(_fim_da_pausa(chamado, momento, expediente.atual()))
    # dentro do prazo? (o prazo já descontou as pausas, inclusive a que termina agora)
    prazo_final = de_bd(campos.get("prazo_em") or chamado["prazo_em"])
    campos["resolvido_no_prazo"] = 1 if prazo_final and momento <= prazo_final else 0
    campos.update(situacao="resolvido", resolvido_em=para_bd(momento), pausado_em=para_bd(momento),
                  responsavel_id=chamado["responsavel_id"] or usuario["id"],
                  assumido_em=chamado["assumido_em"] or para_bd(momento), ultima_interacao_em=para_bd(momento))
    with transacao():
        _atualizar(chamado["id"], campos)
        _evento(chamado["id"], f"{usuario['nome']} (TI) marcou o pedido como resolvido.", usuario["id"], True)
        auditar("chamado_situacao", "chamado", chamado["id"], {"de": chamado["situacao"], "para": "resolvido"})
    from . import avisos_chamados
    avisos_chamados.ao_resolver(chamado["id"])


def reabrir(chamado, usuario, texto: str | None = None):
    """Volta um chamado resolvido para "Em atendimento" (TI a qualquer momento; colaborador no prazo)."""
    if chamado["situacao"] != "resolvido":
        raise ErroNegocio("Só é possível reabrir um pedido resolvido.")
    pela_ti = eh_ti(usuario) and chamado["solicitante_id"] != usuario["id"]
    if pela_ti:
        _exigir_atender(usuario, chamado)
    elif chamado["solicitante_id"] != usuario["id"]:
        raise SemPermissao("Este pedido não é seu.")
    elif not pode_reabrir(chamado):
        raise ErroNegocio("O prazo para reabrir este pedido terminou. Faça um novo pedido.")
    momento = agora()
    campos = _fim_da_pausa(chamado, momento, expediente.atual())
    campos.update(situacao="atendimento", resolvido_em=None, resolvido_no_prazo=None, ultima_interacao_em=para_bd(momento))
    campos["resposta_em"] = None if pela_ti else para_bd(momento)
    with transacao():
        if texto:
            _mensagem(chamado["id"], usuario["id"], "resposta" if pela_ti else "mensagem", texto, True)
        _atualizar(chamado["id"], campos)
        _evento(chamado["id"], "Pedido reaberto pela TI." if pela_ti else "Ainda com problema: pedido reaberto.",
                usuario["id"], True)
        auditar("chamado_reaberto", "chamado", chamado["id"], {"pela_ti": pela_ti})
    if not pela_ti:
        from . import avisos_chamados
        avisos_chamados.ao_responder_colaborador(chamado["id"], reaberto=True)


def pode_reabrir(chamado) -> bool:
    if chamado["situacao"] != "resolvido" or not chamado["resolvido_em"]:
        return False
    exp = expediente.atual()
    limite = exp.somar_dias(de_bd(chamado["resolvido_em"]), int(cfg("chamados_fechar_resolvido_dias") or 3))
    return agora() < limite


def tecnicos_da_unidade(unidade_id: int) -> list:
    return consultar(
        "SELECT DISTINCT u.id, u.nome, u.perfil FROM usuarios u LEFT JOIN usuarios_unidades uu ON uu.usuario_id = u.id "
        "WHERE u.ativo = 1 AND (u.perfil = 'admin' OR (u.perfil = 'tecnico' AND uu.unidade_id = ? "
        "AND (uu.expira_em IS NULL OR uu.expira_em > ?))) ORDER BY u.perfil = 'admin', u.nome COLLATE NOCASE",
        (unidade_id, agora_bd()))


def situacoes_possiveis(chamado) -> list:
    """O que o técnico pode escolher no campo "Situação" do atendimento."""
    atual = chamado["situacao"]
    if atual == "resolvido":
        return ["resolvido", "atendimento"]
    if atual == "fechado":
        return ["fechado"]
    return (["novo"] if atual == "novo" else []) + ["atendimento", "aguardando", "resolvido"]


def alterar(chamado, usuario, form):
    """Situação, prioridade, categoria, assunto e responsável (painel "Atendimento")."""
    _exigir_atender(usuario, chamado)
    if chamado["situacao"] == "fechado":
        raise ErroNegocio("Este chamado está fechado.")
    situacao = form.get("situacao") or chamado["situacao"]
    if situacao not in situacoes_possiveis(chamado):
        raise ErroValidacao({"situacao": "Situação inválida."})
    if chamado["situacao"] == "resolvido":
        if situacao == "atendimento":
            reabrir(chamado, usuario)
        return
    _alterar_campos(chamado, usuario, form, situacao)
    if situacao == "resolvido":
        resolver(obter(chamado["id"]), usuario)


def _alterar_campos(chamado, usuario, form, situacao):
    erros, campos, eventos = {}, {}, []
    categoria = form.get("categoria") or chamado["categoria"]
    if categoria not in CATEGORIAS:
        erros["categoria"] = "Categoria inválida."
    prioridade = form.get("prioridade") or chamado["prioridade"]
    if prioridade not in PRIORIDADES:
        erros["prioridade"] = "Prioridade inválida."
    if categoria == "seguranca":
        prioridade = "alta"
    assunto = limpar_texto(form.get("assunto"), 140) if "assunto" in form else chamado["assunto"]
    if len(assunto) < 3:
        erros["assunto"] = "Informe o assunto."
    responsavel_id = chamado["responsavel_id"]
    if "responsavel_id" in form:
        try:
            responsavel_id = int(form.get("responsavel_id") or 0) or None
        except ValueError:
            responsavel_id = None
        if responsavel_id and responsavel_id not in {t["id"] for t in tecnicos_da_unidade(chamado["unidade_id"])}:
            erros["responsavel_id"] = "Escolha alguém da TI que atende esta unidade."
    if situacao == "aguardando" and chamado["situacao"] != "aguardando" and not _ti_perguntou_por_ultimo(chamado["id"]):
        erros["situacao"] = ("Para esperar a pessoa, escreva a pergunta no campo de resposta e marque "
                             "“Mudar para Aguardando usuário”.")
    if erros:
        raise ErroValidacao(erros)
    if categoria != chamado["categoria"]:
        campos["categoria"] = categoria
        eventos.append(f"Categoria: {CATEGORIAS[chamado['categoria']][0]} → {CATEGORIAS[categoria][0]}.")
    if prioridade != chamado["prioridade"]:
        exp = expediente.atual()
        base, prazo = _prazos(chamado["criado_em"], prioridade, chamado["pausa_total_min"] or 0, exp)
        campos.update(prioridade=prioridade, prazo_base_em=base, prazo_em=prazo)
        if de_bd(prazo) > agora():
            campos["aviso_prazo"] = None
        eventos.append(f"Prioridade: {PRIORIDADES[chamado['prioridade']]} → {PRIORIDADES[prioridade]}.")
    if assunto != chamado["assunto"]:
        campos["assunto"] = assunto
        eventos.append("Assunto alterado.")
    novo_responsavel = None
    if responsavel_id != chamado["responsavel_id"]:
        campos["responsavel_id"] = responsavel_id
        if responsavel_id:
            novo_responsavel = consultar("SELECT id, nome, email FROM usuarios WHERE id = ?", (responsavel_id,), um=True)
            campos["assumido_em"] = chamado["assumido_em"] or agora_bd()
            if chamado["situacao"] == "novo":
                campos["situacao"] = "atendimento"
            eventos.append(f"Responsável: {novo_responsavel['nome']}.")
        else:
            eventos.append("Chamado sem responsável.")
    assumiu = False
    if situacao in ("atendimento", "aguardando") and situacao != chamado["situacao"]:
        momento = agora()
        if chamado["situacao"] == "aguardando":  # volta a correr o prazo (com a prioridade já ajustada)
            campos.update(_fim_da_pausa(dict(chamado, prioridade=prioridade), momento, expediente.atual()))
        if situacao == "aguardando":
            campos["pausado_em"] = para_bd(momento)
        campos.update(situacao=situacao, ultima_interacao_em=para_bd(momento))
        if not responsavel_id:
            campos.update(responsavel_id=usuario["id"], assumido_em=chamado["assumido_em"] or para_bd(momento))
            assumiu = True
        eventos.append(f"Situação: {SITUACOES[chamado['situacao']]} → {SITUACOES[situacao]}.")
    if not campos:
        return
    with transacao():
        _atualizar(chamado["id"], campos)
        if "assunto" in campos:
            recalcular_busca(chamado_id=chamado["id"])
        if assumiu:
            _evento(chamado["id"], f"{usuario['nome']} (TI) começou a atender.", usuario["id"], True)
        for texto in eventos:
            _evento(chamado["id"], texto, usuario["id"])
        auditar("chamado_alterado", "chamado", chamado["id"],
                {c: [chamado.get(c), v] for c, v in campos.items() if c in ("categoria", "prioridade", "assunto",
                                                                             "responsavel_id", "situacao")})
    from . import avisos_chamados
    if novo_responsavel and novo_responsavel["id"] != usuario["id"]:
        avisos_chamados.ao_atribuir(chamado["id"], novo_responsavel, usuario["nome"])
    elif assumiu or (novo_responsavel and novo_responsavel["id"] == usuario["id"] and not chamado["responsavel_id"]):
        avisos_chamados.ao_assumir(chamado["id"], usuario["nome"])


def _ti_perguntou_por_ultimo(chamado_id: int) -> bool:
    """A última mensagem que a pessoa vê é da TI (é a pergunta que ela precisa responder)."""
    ultima = consultar("SELECT m.tipo FROM chamados_mensagens m WHERE m.chamado_id = ? AND m.publico = 1 "
                       "AND m.tipo IN ('mensagem', 'resposta') ORDER BY m.id DESC LIMIT 1", (chamado_id,), um=True)
    return bool(ultima and ultima["tipo"] == "resposta")


def transferir(chamado, usuario, form):
    _exigir_atender(usuario, chamado)
    if chamado["situacao"] not in ABERTAS:
        raise ErroNegocio("Só é possível transferir chamados abertos.")
    try:
        destino_id = int(form.get("unidade_id") or 0)
    except ValueError:
        destino_id = 0
    destino = consultar("SELECT id, nome FROM unidades WHERE id = ? AND ativo = 1", (destino_id,), um=True)
    motivo = limpar_texto_longo(form.get("motivo"), 1000)
    erros = {}
    if not destino or destino_id == chamado["unidade_id"]:
        erros["unidade_id"] = "Escolha a unidade que vai atender."
    if len(motivo) < 5:
        erros["motivo"] = "Explique o motivo para a outra equipe."
    if erros:
        raise ErroValidacao(erros)
    origem = escalar("SELECT nome FROM unidades WHERE id = ?", (chamado["unidade_id"],))
    with transacao():
        _atualizar(chamado["id"], {"unidade_id": destino_id, "responsavel_id": None,
                                   "situacao": "aguardando" if chamado["situacao"] == "aguardando" else "novo",
                                   "avisos_enviados": 0, "ultimo_aviso_em": None})
        _evento(chamado["id"], f"Transferido de {origem} para {destino['nome']} por {usuario['nome']}. Motivo: {motivo}",
                usuario["id"])
        auditar("chamado_transferido", "chamado", chamado["id"],
                {"de": chamado["unidade_id"], "para": destino_id, "motivo": motivo})
    from . import avisos_chamados
    avisos_chamados.ao_transferir(chamado["id"], origem, usuario["nome"])


def salvar_seguranca(chamado, usuario, form):
    _exigir_atender(usuario, chamado)
    if chamado["categoria"] != "seguranca":
        raise ErroNegocio("Este registro é só para chamados de Segurança da informação.")
    erros = {}
    try:
        conhecido = ler_datahora_local(form.get("seg_conhecido_em"))
    except ValueError:
        conhecido, erros["seg_conhecido_em"] = None, "Data e hora inválidas."
    acoes = limpar_texto_longo(form.get("seg_acoes"), 4000) or None
    dados = form.get("seg_dados_pessoais") if form.get("seg_dados_pessoais") in ("sim", "nao", "nao_sei") else None
    if erros:
        raise ErroValidacao(erros)
    campos = {"seg_conhecido_em": conhecido, "seg_acoes": acoes, "seg_dados_pessoais": dados}
    mudou = {c: [chamado[c], v] for c, v in campos.items() if (chamado[c] or None) != (v or None)}
    if not mudou:
        return
    with transacao():
        _atualizar(chamado["id"], campos)
        _evento(chamado["id"], f"{usuario['nome']} atualizou o registro do incidente.", usuario["id"])
        auditar("chamado_seguranca_registro", "chamado", chamado["id"], mudou)
    if "seg_dados_pessoais" in mudou and dados == "sim":
        from . import avisos_chamados
        avisos_chamados.ao_dados_pessoais(chamado["id"], usuario["nome"])


def enviar_ao_encarregado(chamado, usuario):
    _exigir_atender(usuario, chamado)
    if chamado["categoria"] != "seguranca":
        raise ErroNegocio("Só chamados de Segurança da informação vão para o encarregado (DPO).")
    destino = (cfg("chamados_encarregado_email") or "").strip()
    if not email_valido(destino):
        raise ErroNegocio("Cadastre o e-mail do encarregado (DPO) em Configurações > Chamados.")
    from . import avisos_chamados
    resultado = avisos_chamados.registro_ao_encarregado(chamado["id"], destino)
    with transacao():
        _evento(chamado["id"], f"Registro completo enviado ao encarregado (DPO) por {usuario['nome']}.", usuario["id"])
        auditar("chamado_enviado_dpo", "chamado", chamado["id"], {"destino": destino, "status": resultado.status})
    if not resultado.ok:
        raise ErroNegocio(f"Não foi possível enviar o e-mail: {resultado.erro}")


# ------------------------------------------------------------------ colaborador

def mensagem_colaborador(chamado, usuario, form, anexo) -> int:
    if chamado["solicitante_id"] != usuario["id"]:
        raise SemPermissao("Este pedido não é seu.")
    if chamado["situacao"] == "fechado":
        raise ErroNegocio("Este pedido foi encerrado. Se ainda precisar de ajuda, faça um novo pedido.")
    if chamado["situacao"] == "resolvido":
        texto = limpar_texto_longo(form.get("texto"), 6000)
        reabrir(chamado, usuario, texto or None)
        return 0
    texto = _ler_texto(form, anexo=anexo)
    momento = agora()
    campos = {"ultima_interacao_em": para_bd(momento), "resposta_em": para_bd(momento)}
    if chamado["situacao"] == "aguardando":
        campos.update(_fim_da_pausa(chamado, momento, expediente.atual()), situacao="atendimento")
    with transacao():
        mensagem_id = _mensagem(chamado["id"], usuario["id"], "mensagem", texto, True)
        if anexo:
            _gravar_anexo(chamado["id"], mensagem_id, anexo, usuario["id"])
        _atualizar(chamado["id"], campos)
        auditar("chamado_mensagem", "chamado", chamado["id"])
    from . import avisos_chamados
    avisos_chamados.ao_responder_colaborador(chamado["id"], mensagem_id=mensagem_id)
    return mensagem_id


def meus_pedidos(usuario, limite: int = 50) -> list:
    pedidos = consultar(
        "SELECT c.id, c.categoria, c.situacao, c.assunto, c.criado_em, c.resolvido_em, c.fechado_em, "
        "c.atualizado_em, c.ultima_interacao_em FROM chamados c WHERE c.solicitante_id = ? "
        "ORDER BY CASE c.situacao WHEN 'aguardando' THEN 0 WHEN 'novo' THEN 1 WHEN 'atendimento' THEN 1 "
        "WHEN 'resolvido' THEN 2 ELSE 3 END, c.id DESC LIMIT ?", (usuario["id"], limite))
    novas = nao_lidas_do_colaborador(usuario)
    for p in pedidos:
        p["pode_reabrir"] = p["situacao"] == "resolvido" and pode_reabrir(dict(p, resolvido_em=p["resolvido_em"]))
        p["nao_lidas"] = novas.get(p["id"], 0)
    return pedidos


def nao_lidas_do_colaborador(usuario) -> dict:
    """{pedido: quantas respostas da TI a pessoa ainda não viu}. Notas internas e eventos não contam."""
    return {l["chamado_id"]: l["n"] for l in consultar(
        "SELECT m.chamado_id, COUNT(*) AS n FROM chamados_mensagens m JOIN chamados c ON c.id = m.chamado_id "
        "WHERE c.solicitante_id = ? AND m.publico = 1 AND m.tipo = 'resposta' AND m.id > c.colaborador_viu_ate "
        "GROUP BY m.chamado_id", (usuario["id"],))}


def marcar_lido_pelo_colaborador(chamado_id: int, ate_mensagem: int | None = None):
    """O colaborador abriu o pedido (ou a resposta apareceu na tela dele)."""
    if ate_mensagem is None:
        ate_mensagem = escalar("SELECT COALESCE(MAX(id), 0) FROM chamados_mensagens WHERE chamado_id = ?", (chamado_id,))
    executar("UPDATE chamados SET colaborador_viu_ate = MAX(colaborador_viu_ate, ?) WHERE id = ?",
             (int(ate_mensagem or 0), chamado_id))


# ------------------------------------------------------------------ consultas

COLUNAS = """c.*, s.nome AS solicitante_nome, s.setor AS solicitante_setor, s.login_ad AS solicitante_login,
    s.email AS solicitante_email, s.unidade_id AS solicitante_unidade_id, r.nome AS responsavel_nome,
    u.nome AS unidade_nome, cp.nome AS computador_nome, a.marca AS aparelho_marca, a.modelo AS aparelho_modelo,
    org.nome AS origem_nome"""
JUNCOES = """FROM chamados c JOIN usuarios s ON s.id = c.solicitante_id JOIN unidades u ON u.id = c.unidade_id
    LEFT JOIN usuarios r ON r.id = c.responsavel_id LEFT JOIN computadores cp ON cp.id = c.computador_id
    LEFT JOIN aparelhos a ON a.id = c.aparelho_id LEFT JOIN computadores org ON org.id = c.origem_computador_id"""


def obter(chamado_id) -> dict | None:
    try:
        chamado_id = int(chamado_id)
    except (TypeError, ValueError):
        return None
    return consultar(f"SELECT {COLUNAS}, ab.nome AS aberto_por_nome, su.nome AS solicitante_unidade_nome "
                     f"{JUNCOES} LEFT JOIN usuarios ab ON ab.id = c.aberto_por_id "
                     f"LEFT JOIN unidades su ON su.id = s.unidade_id WHERE c.id = ?", (chamado_id,), um=True)


def conversa(chamado_id: int, para_colaborador: bool = False, depois: int = 0) -> list:
    """Mensagens em ordem. Com "depois", só as mais novas que aquela (atualização da tela em tempo real)."""
    sql = ("SELECT m.*, u.nome AS autor_nome, u.perfil AS autor_perfil, u.setor AS autor_setor FROM chamados_mensagens m "
           "LEFT JOIN usuarios u ON u.id = m.autor_id WHERE m.chamado_id = ? AND m.id > ?")
    if para_colaborador:
        sql += " AND m.publico = 1"
    mensagens = consultar(sql + " ORDER BY m.id", (chamado_id, max(0, depois)))
    if not mensagens:
        return []
    anexos = {}
    for anexo in consultar("SELECT id, mensagem_id, nome, tipo, tamanho FROM chamados_anexos WHERE chamado_id = ? "
                           "AND mensagem_id >= ? ORDER BY id", (chamado_id, mensagens[0]["id"])):
        anexos.setdefault(anexo["mensagem_id"], []).append(anexo)
    for m in mensagens:
        m["anexos"] = anexos.get(m["id"], [])
    return mensagens


def _filtros_sql(usuario, filtros: dict) -> tuple[list, list]:
    condicoes, parametros = ["1=1"], []
    visibilidade, extra = _visibilidade(usuario)
    if visibilidade:
        condicoes.append(visibilidade[5:])
        parametros += extra
    if filtros.get("unidade"):
        condicoes.append("c.unidade_id = ?")
        parametros.append(filtros["unidade"])
    if filtros.get("unidades") is not None:  # só as unidades que a pessoa atende (resumo diário)
        unidades = sorted(filtros["unidades"])
        condicoes.append(f"c.unidade_id IN ({','.join('?' * len(unidades))})" if unidades else "1=0")
        parametros += unidades
    situacao = filtros.get("situacao") or "abertos"
    if situacao == "abertos":
        condicoes.append("c.situacao IN ('novo', 'atendimento', 'aguardando')")
    elif situacao == "concluidos":
        condicoes.append("c.situacao IN ('resolvido', 'fechado')")
    elif situacao == "atrasados":
        condicoes.append("c.situacao IN ('novo', 'atendimento') AND c.prazo_em < ?")
        parametros.append(agora_bd())
    elif situacao in SITUACOES:
        condicoes.append("c.situacao = ?")
        parametros.append(situacao)
    if filtros.get("meus"):
        condicoes.append("c.responsavel_id = ?")
        parametros.append(usuario["id"])
    if filtros.get("sem_responsavel"):
        condicoes.append("c.responsavel_id IS NULL")
    if filtros.get("respostas"):
        condicoes.append("c.resposta_em IS NOT NULL AND (c.responsavel_id = ? OR c.responsavel_id IS NULL)")
        parametros.append(usuario["id"])
    if filtros.get("categoria") in CATEGORIAS:
        condicoes.append("c.categoria = ?")
        parametros.append(filtros["categoria"])
    if filtros.get("prioridade") in PRIORIDADES:
        condicoes.append("c.prioridade = ?")
        parametros.append(filtros["prioridade"])
    if filtros.get("solicitante"):
        condicoes.append("c.solicitante_id = ?")
        parametros.append(filtros["solicitante"])
    texto = (filtros.get("q") or "").strip()
    if texto:
        numero_buscado = texto.lstrip("#").lstrip("0") if re.fullmatch(r"#?\d{1,9}", texto) else None
        palavras = [normalizar(p).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    for p in texto.split() if normalizar(p)]
        busca = " AND ".join("c.busca LIKE ? ESCAPE '\\'" for _ in palavras) or "1=1"
        extra = [f"%{p}%" for p in palavras]
        if numero_buscado:
            condicoes.append(f"(c.id = ? OR ({busca}))")
            parametros += [int(numero_buscado or 0), *extra]
        else:
            condicoes.append(f"({busca})")
            parametros += extra
    return condicoes, parametros


def listar_fila(usuario, filtros: dict, pagina: int = 1, por_pagina: int = POR_PAGINA) -> Pagina:
    condicoes, parametros = _filtros_sql(usuario, filtros)
    onde = " AND ".join(condicoes)
    total = escalar(f"SELECT COUNT(*) {JUNCOES} WHERE {onde}", parametros)
    if (filtros.get("situacao") or "abertos") in ("concluidos", "resolvido", "fechado", "todos"):
        ordem = "COALESCE(c.fechado_em, c.resolvido_em, c.atualizado_em) DESC, c.id DESC"
        extra_ordem = []
    else:
        ordem = ("CASE WHEN c.situacao IN ('novo', 'atendimento') AND c.prazo_em < ? THEN 0 ELSE 1 END, "
                 "CASE c.prioridade WHEN 'alta' THEN 0 WHEN 'media' THEN 1 ELSE 2 END, "
                 "CASE WHEN c.situacao = 'aguardando' THEN 1 ELSE 0 END, c.prazo_em, c.id")
        extra_ordem = [agora_bd()]
    pagina = max(1, min(pagina, max(1, -(-total // por_pagina))))
    itens = consultar(f"SELECT {COLUNAS} {JUNCOES} WHERE {onde} ORDER BY {ordem} LIMIT ? OFFSET ?",
                      (*parametros, *extra_ordem, por_pagina, (pagina - 1) * por_pagina))
    exp = expediente.atual()
    primeiros = {}
    for item in itens:
        item["prazo"] = situacao_do_prazo(item, exp)
        if item["responsavel_nome"]:
            primeiros.setdefault(item["responsavel_nome"].split()[0], set()).add(item["responsavel_id"])
    for item in itens:
        nome = item["responsavel_nome"]
        item["responsavel_curto"] = (nome.split()[0] if nome and len(primeiros[nome.split()[0]]) == 1 else nome)
    return Pagina(itens, total, pagina, por_pagina)


def indicadores(usuario, unidade_id=None) -> dict:
    visibilidade, parametros = _visibilidade(usuario)
    aberto = "c.situacao IN ('novo', 'atendimento', 'aguardando')"
    resolvido_no_mes = "(c.resolvido_em >= ? AND c.situacao IN ('resolvido', 'fechado'))"
    inicio_mes = para_bd(agora_local().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    sql = (f"SELECT SUM({aberto}) AS abertos, "
           "SUM(c.situacao = 'novo') AS novos, SUM(c.situacao = 'atendimento') AS atendimento, "
           f"SUM(c.situacao = 'aguardando') AS aguardando, SUM({aberto} AND c.responsavel_id IS NULL) AS sem_responsavel, "
           "SUM(c.situacao IN ('novo', 'atendimento') AND c.prazo_em < ?) AS atrasados, "
           f"SUM({resolvido_no_mes}) AS resolvidos_mes, "
           f"SUM({resolvido_no_mes} AND c.resolvido_no_prazo = 1) AS no_prazo_mes "
           f"FROM chamados c WHERE ({aberto} OR {resolvido_no_mes}){visibilidade}")
    parametros = [agora_bd(), inicio_mes, inicio_mes, inicio_mes, *parametros]
    if unidade_id:
        sql += " AND c.unidade_id = ?"
        parametros.append(unidade_id)
    linha = consultar(sql, parametros, um=True) or {}
    return {chave: int(linha.get(chave) or 0) for chave in ("abertos", "novos", "atendimento", "aguardando",
                                                           "atrasados", "sem_responsavel", "resolvidos_mes",
                                                           "no_prazo_mes")}


def abertos_por_unidade(usuario) -> dict:
    visibilidade, parametros = _visibilidade(usuario)
    return {l["unidade_id"]: l["total"] for l in consultar(
        "SELECT c.unidade_id, COUNT(*) AS total FROM chamados c WHERE c.situacao IN ('novo', 'atendimento', "
        f"'aguardando'){visibilidade} GROUP BY c.unidade_id", parametros)}


def contador(usuario) -> dict:
    """Para o menu e o aviso do navegador: chamados novos (sem ninguém) nas unidades que a pessoa atende e
    respostas de colaborador que ela ainda não viu (nos chamados dela, ou sem responsável nas suas unidades)."""
    unidades = unidades_atendidas(usuario)
    vazio = {"novos": 0, "ultimo": 0, "respostas": 0, "ultima_resposta": "", "resposta": None}
    filtro, parametros = "", []
    if unidades is not None:
        if not unidades:
            return vazio
        filtro = f" AND c.unidade_id IN ({','.join('?' * len(unidades))})"
        parametros = sorted(unidades)
    linha = consultar(f"SELECT COUNT(*) AS novos, MAX(c.id) AS ultimo FROM chamados c WHERE c.situacao = 'novo'{filtro}",
                      parametros, um=True)
    respostas = consultar(
        "SELECT c.id, c.resposta_em, s.nome FROM chamados c JOIN usuarios s ON s.id = c.solicitante_id "
        "WHERE c.resposta_em IS NOT NULL AND c.situacao IN ('novo', 'atendimento', 'aguardando') "
        f"AND (c.responsavel_id = ? OR (c.responsavel_id IS NULL{filtro})) ORDER BY c.resposta_em DESC",
        (usuario["id"], *parametros))
    ultima = respostas[0] if respostas else None
    return {"novos": int(linha["novos"] or 0), "ultimo": int(linha["ultimo"] or 0), "respostas": len(respostas),
            "ultima_resposta": ultima["resposta_em"] if ultima else "",
            "resposta": {"id": ultima["id"], "nome": ultima["nome"], "numero": numero(ultima["id"])} if ultima else None}


def marcar_resposta_vista(chamado, usuario):
    """O responsável (ou, sem responsável, quem atende a unidade) abriu o chamado: a resposta foi vista."""
    if not chamado.get("resposta_em"):
        return
    if chamado["responsavel_id"] == usuario["id"] or (not chamado["responsavel_id"] and pode_atender(usuario, chamado)):
        executar("UPDATE chamados SET resposta_em = NULL WHERE id = ?", (chamado["id"],))


def pedidos_recentes(solicitante_id: int, dias: int = 90) -> int:
    return escalar("SELECT COUNT(*) FROM chamados WHERE solicitante_id = ? AND criado_em >= ?",
                   (solicitante_id, para_bd(agora() - timedelta(days=dias))))


def equipamento(chamado) -> dict:
    dados = {"computador": None, "aparelho": None, "origem": None}
    if chamado["computador_id"]:
        dados["computador"] = consultar(
            "SELECT id, nome, tipo, marca, modelo, processador, memoria_gb, sistema, ip, ip_wifi, sep, "
            "remoto_ferramenta, remoto_id, unidade_id, (SELECT COUNT(*) FROM chamados x WHERE x.computador_id = "
            "computadores.id) AS chamados FROM computadores WHERE id = ?", (chamado["computador_id"],), um=True)
    if chamado["aparelho_id"]:
        dados["aparelho"] = consultar(
            "SELECT id, marca, modelo, linha, operadora, (SELECT COUNT(*) FROM chamados x WHERE x.aparelho_id = "
            "aparelhos.id) AS chamados FROM aparelhos WHERE id = ?", (chamado["aparelho_id"],), um=True)
    if chamado["origem_computador_id"] and chamado["origem_computador_id"] != chamado["computador_id"]:
        dados["origem"] = consultar("SELECT id, nome, tipo, marca, modelo FROM computadores WHERE id = ?",
                                    (chamado["origem_computador_id"],), um=True)
    return dados


def do_equipamento(usuario, *, computador_id=None, aparelho_id=None, limite: int = 15) -> list:
    if not eh_ti(usuario):
        return []
    visibilidade, parametros = _visibilidade(usuario)
    coluna, valor = ("c.computador_id", computador_id) if computador_id else ("c.aparelho_id", aparelho_id)
    return consultar(f"SELECT c.id, c.assunto, c.situacao, c.categoria, c.prioridade, c.criado_em, s.nome AS "
                     f"solicitante_nome FROM chamados c JOIN usuarios s ON s.id = c.solicitante_id "
                     f"WHERE {coluna} = ?{visibilidade} ORDER BY c.id DESC LIMIT ?", (valor, *parametros, limite))


def registrar_leitura(chamado, usuario):
    """Leitura de chamado de segurança fica na auditoria (no máximo uma vez a cada 30 min por pessoa)."""
    if chamado["categoria"] != "seguranca":
        return
    recente = escalar("SELECT 1 FROM auditoria WHERE acao = 'chamado_lido' AND entidade = 'chamado' AND entidade_id = ? "
                      "AND usuario_id = ? AND ocorrido_em > ?",
                      (chamado["id"], usuario["id"], para_bd(agora() - timedelta(minutes=30))))
    if not recente:
        auditar("chamado_lido", "chamado", chamado["id"])
