"""Acesso ao banco de dados SQLite.

Cada requisição usa uma conexão própria (guardada em flask.g). As alterações
que precisam ser atômicas usam o gerenciador de contexto `transacao()`.
O esquema evolui por migrações numeradas (PRAGMA user_version).
"""
import re
import sqlite3
import unicodedata
from contextlib import contextmanager

from flask import current_app, g


def normalizar(texto) -> str:
    """Minúsculas e sem acentos — usado nas buscas e comparações de nomes."""
    if texto is None:
        return ""
    texto = unicodedata.normalize("NFKD", str(texto))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return " ".join(texto.lower().split())


def conectar(caminho) -> sqlite3.Connection:
    conexao = sqlite3.connect(str(caminho), timeout=20, isolation_level=None, check_same_thread=False)
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    conexao.execute("PRAGMA busy_timeout = 20000")
    conexao.create_function("normalizar", 1, normalizar, deterministic=True)
    return conexao


def obter_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = conectar(current_app.config["BANCO"])
    return g.db


def fechar_db(_erro=None):
    conexao = g.pop("db", None)
    if conexao is not None:
        conexao.close()


def consultar(sql: str, parametros=(), um: bool = False):
    cursor = obter_db().execute(sql, parametros)
    if um:
        linha = cursor.fetchone()
        return dict(linha) if linha else None
    return [dict(linha) for linha in cursor.fetchall()]


def escalar(sql: str, parametros=()):
    linha = obter_db().execute(sql, parametros).fetchone()
    return linha[0] if linha else None


def executar(sql: str, parametros=()) -> sqlite3.Cursor:
    return obter_db().execute(sql, parametros)


def condicao_busca(texto: str, expressao: str, digitos: bool = True) -> tuple[str, list]:
    """Busca por palavras (todas precisam aparecer), sem diferenciar acentos e maiúsculas.

    Números digitados com pontuação — "(11) 98888-7777", "123.456.789-09" — viram só dígitos,
    pois IMEI, linha e CPF são guardados sem formatação.
    """
    partes, parametros = [], []
    for palavra in (texto or "").split():
        if digitos and re.fullmatch(r"[\d().\-/+]+", palavra):
            palavra = re.sub(r"\D", "", palavra)
        palavra = normalizar(palavra)
        if not palavra:
            continue
        palavra = palavra.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        partes.append(f"normalizar({expressao}) LIKE ? ESCAPE '\\'")
        parametros.append(f"%{palavra}%")
    if not partes:
        return "", []
    return " AND " + " AND ".join(partes), parametros


@contextmanager
def transacao():
    """Agrupa comandos numa transação. Transações aninhadas reaproveitam a externa."""
    conexao = obter_db()
    if conexao.in_transaction:
        yield conexao
        return
    conexao.execute("BEGIN IMMEDIATE")
    try:
        yield conexao
    except BaseException:
        conexao.execute("ROLLBACK")
        raise
    else:
        conexao.execute("COMMIT")


# --------------------------------------------------------------------------- esquema

MIGRACOES = [
    # 1 — esquema inicial
    """
    CREATE TABLE configuracoes (
        chave TEXT PRIMARY KEY,
        valor TEXT,
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE unidades (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        sigla TEXT,
        cnpj TEXT,
        cidade TEXT,
        uf TEXT,
        endereco TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_unidades_nome ON unidades(nome COLLATE NOCASE);

    CREATE TABLE usuarios (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        email TEXT NOT NULL,
        senha_hash TEXT NOT NULL,
        perfil TEXT NOT NULL CHECK (perfil IN ('admin', 'consulta')),
        ativo INTEGER NOT NULL DEFAULT 1,
        deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
        tentativas_falhas INTEGER NOT NULL DEFAULT 0,
        bloqueado_ate TEXT,
        versao_sessao INTEGER NOT NULL DEFAULT 1,
        ultimo_acesso_em TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_usuarios_email ON usuarios(email COLLATE NOCASE);

    CREATE TABLE usuarios_unidades (
        usuario_id INTEGER NOT NULL REFERENCES usuarios(id) ON DELETE CASCADE,
        unidade_id INTEGER NOT NULL REFERENCES unidades(id) ON DELETE CASCADE,
        PRIMARY KEY (usuario_id, unidade_id)
    );

    CREATE TABLE colaboradores (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        cpf TEXT NOT NULL,
        email TEXT NOT NULL,
        matricula TEXT,
        cargo TEXT NOT NULL,
        departamento TEXT NOT NULL,
        unidade_id INTEGER NOT NULL REFERENCES unidades(id),
        telefone TEXT,
        situacao TEXT NOT NULL DEFAULT 'ativo' CHECK (situacao IN ('ativo', 'desligado')),
        desligado_em TEXT,
        observacoes TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_colaboradores_cpf ON colaboradores(cpf);
    CREATE UNIQUE INDEX ux_colaboradores_email_ativo ON colaboradores(email COLLATE NOCASE)
        WHERE situacao = 'ativo';
    CREATE INDEX ix_colaboradores_unidade ON colaboradores(unidade_id);

    CREATE TABLE aparelhos (
        id INTEGER PRIMARY KEY,
        marca TEXT NOT NULL,
        modelo TEXT NOT NULL,
        numero_serie TEXT NOT NULL,
        imei TEXT NOT NULL,
        imei2 TEXT,
        linha TEXT,
        operadora TEXT,
        unidade_id INTEGER NOT NULL REFERENCES unidades(id),
        data_aquisicao TEXT,
        valor_centavos INTEGER,
        estado_conservacao TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('disponivel', 'em_uso', 'manutencao', 'devolvido', 'baixado')),
        status_desde TEXT NOT NULL DEFAULT (datetime('now')),
        motivo_baixa TEXT,
        acessorios TEXT,
        observacoes TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_aparelhos_imei ON aparelhos(imei);
    CREATE UNIQUE INDEX ux_aparelhos_serie ON aparelhos(numero_serie COLLATE NOCASE);
    CREATE UNIQUE INDEX ux_aparelhos_linha_ativa ON aparelhos(linha)
        WHERE linha IS NOT NULL AND status <> 'baixado';
    CREATE INDEX ix_aparelhos_unidade_status ON aparelhos(unidade_id, status);

    CREATE TABLE vinculos (
        id INTEGER PRIMARY KEY,
        aparelho_id INTEGER NOT NULL REFERENCES aparelhos(id),
        colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
        vinculo_origem_id INTEGER REFERENCES vinculos(id),
        entregue_em TEXT NOT NULL,
        entregue_por INTEGER REFERENCES usuarios(id),
        estado_entrega TEXT,
        acessorios_entrega TEXT,
        observacoes_entrega TEXT,
        devolucao_prevista TEXT,
        devolvido_em TEXT,
        recebido_por INTEGER REFERENCES usuarios(id),
        estado_devolucao TEXT,
        acessorios_devolucao TEXT,
        observacoes_devolucao TEXT,
        motivo_encerramento TEXT CHECK (motivo_encerramento IN
            ('devolucao', 'transferencia', 'substituicao', 'desligamento', 'baixa')),
        criado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE UNIQUE INDEX ux_vinculos_aparelho_ativo ON vinculos(aparelho_id) WHERE devolvido_em IS NULL;
    CREATE INDEX ix_vinculos_colaborador ON vinculos(colaborador_id, devolvido_em);

    CREATE TABLE contadores (
        nome TEXT PRIMARY KEY,
        valor INTEGER NOT NULL
    );

    CREATE TABLE termos (
        id INTEGER PRIMARY KEY,
        codigo TEXT NOT NULL UNIQUE,
        vinculo_id INTEGER NOT NULL REFERENCES vinculos(id),
        colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
        aparelho_id INTEGER NOT NULL REFERENCES aparelhos(id),
        status TEXT NOT NULL CHECK (status IN ('gerado', 'enviado', 'assinado', 'cancelado')),
        conteudo_json TEXT NOT NULL,
        hash_conteudo TEXT NOT NULL,
        modelo_versao INTEGER,
        emitido_em TEXT NOT NULL,
        emitido_por INTEGER REFERENCES usuarios(id),
        token_hash TEXT,
        token_cifrado TEXT,
        token_expira_em TEXT,
        enviado_em TEXT,
        ultimo_envio_em TEXT,
        envios INTEGER NOT NULL DEFAULT 0,
        lembretes INTEGER NOT NULL DEFAULT 0,
        ultimo_lembrete_em TEXT,
        visualizado_em TEXT,
        assinado_em TEXT,
        evidencias_json TEXT,
        selo TEXT,
        codigo_verificacao TEXT UNIQUE,
        pdf_caminho TEXT,
        pdf_hash TEXT,
        cancelado_em TEXT,
        cancelado_por INTEGER REFERENCES usuarios(id),
        motivo_cancelamento TEXT
    );
    CREATE UNIQUE INDEX ux_termos_token ON termos(token_hash) WHERE token_hash IS NOT NULL;
    CREATE UNIQUE INDEX ux_termos_vinculo_vigente ON termos(vinculo_id) WHERE status <> 'cancelado';
    CREATE INDEX ix_termos_status ON termos(status);
    CREATE INDEX ix_termos_colaborador ON termos(colaborador_id);
    CREATE INDEX ix_termos_pdf_hash ON termos(pdf_hash);

    CREATE TABLE termo_eventos (
        id INTEGER PRIMARY KEY,
        termo_id INTEGER NOT NULL REFERENCES termos(id),
        evento TEXT NOT NULL,
        ocorrido_em TEXT NOT NULL DEFAULT (datetime('now')),
        usuario_id INTEGER REFERENCES usuarios(id),
        ip TEXT,
        user_agent TEXT,
        detalhes TEXT
    );
    CREATE INDEX ix_termo_eventos ON termo_eventos(termo_id, ocorrido_em);

    CREATE TABLE codigos_verificacao (
        id INTEGER PRIMARY KEY,
        finalidade TEXT NOT NULL CHECK (finalidade IN ('assinatura', 'portal')),
        colaborador_id INTEGER NOT NULL REFERENCES colaboradores(id),
        termo_id INTEGER REFERENCES termos(id),
        codigo_hash TEXT NOT NULL,
        expira_em TEXT NOT NULL,
        tentativas INTEGER NOT NULL DEFAULT 0,
        usado_em TEXT,
        ip TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_codigos_colaborador ON codigos_verificacao(colaborador_id, finalidade, criado_em);

    CREATE TABLE movimentacoes (
        id INTEGER PRIMARY KEY,
        tipo TEXT NOT NULL,
        ocorrido_em TEXT NOT NULL DEFAULT (datetime('now')),
        aparelho_id INTEGER REFERENCES aparelhos(id),
        colaborador_id INTEGER REFERENCES colaboradores(id),
        colaborador_destino_id INTEGER REFERENCES colaboradores(id),
        vinculo_id INTEGER REFERENCES vinculos(id),
        termo_id INTEGER REFERENCES termos(id),
        unidade_id INTEGER REFERENCES unidades(id),
        usuario_id INTEGER REFERENCES usuarios(id),
        descricao TEXT NOT NULL,
        detalhes TEXT
    );
    CREATE INDEX ix_mov_aparelho ON movimentacoes(aparelho_id, ocorrido_em);
    CREATE INDEX ix_mov_colaborador ON movimentacoes(colaborador_id, ocorrido_em);
    CREATE INDEX ix_mov_data ON movimentacoes(ocorrido_em);

    CREATE TABLE emails (
        id INTEGER PRIMARY KEY,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        modo TEXT NOT NULL,
        remetente TEXT,
        destinatario TEXT NOT NULL,
        assunto TEXT NOT NULL,
        finalidade TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('enviado', 'simulado', 'falha')),
        erro TEXT,
        tentativas INTEGER NOT NULL DEFAULT 1,
        termo_id INTEGER REFERENCES termos(id),
        colaborador_id INTEGER REFERENCES colaboradores(id),
        usuario_id INTEGER REFERENCES usuarios(id),
        corpo_html TEXT,
        anexos TEXT
    );
    CREATE INDEX ix_emails_data ON emails(criado_em);
    CREATE INDEX ix_emails_termo ON emails(termo_id);

    CREATE TABLE auditoria (
        id INTEGER PRIMARY KEY,
        ocorrido_em TEXT NOT NULL DEFAULT (datetime('now')),
        usuario_id INTEGER REFERENCES usuarios(id),
        usuario_nome TEXT,
        acao TEXT NOT NULL,
        entidade TEXT,
        entidade_id INTEGER,
        detalhes TEXT,
        ip TEXT
    );
    CREATE INDEX ix_auditoria_data ON auditoria(ocorrido_em);

    CREATE TABLE tarefas (
        nome TEXT PRIMARY KEY,
        proxima_execucao TEXT,
        ultima_execucao TEXT,
        ultimo_resultado TEXT,
        bloqueada_ate TEXT
    );
    """,
    # 2 — índices para manter as telas rápidas com milhares de aparelhos e registros
    """
    CREATE INDEX IF NOT EXISTS ix_termos_vinculo ON termos(vinculo_id, status);
    CREATE INDEX IF NOT EXISTS ix_termos_aparelho ON termos(aparelho_id);
    CREATE INDEX IF NOT EXISTS ix_termos_emitido ON termos(emitido_em);
    CREATE INDEX IF NOT EXISTS ix_vinculos_aparelho ON vinculos(aparelho_id, entregue_em);
    CREATE INDEX IF NOT EXISTS ix_mov_vinculo ON movimentacoes(vinculo_id);
    CREATE INDEX IF NOT EXISTS ix_mov_termo ON movimentacoes(termo_id);
    CREATE INDEX IF NOT EXISTS ix_mov_destino ON movimentacoes(colaborador_destino_id);
    CREATE INDEX IF NOT EXISTS ix_emails_colaborador ON emails(colaborador_id, criado_em);
    ANALYZE;
    """,
    # 3 — perfil "técnico de TI", computadores (desktops e notebooks) por unidade e faixa de IP de cada unidade
    """
    CREATE TABLE usuarios_novo (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        email TEXT NOT NULL,
        senha_hash TEXT NOT NULL,
        perfil TEXT NOT NULL CHECK (perfil IN ('admin', 'tecnico', 'consulta')),
        ativo INTEGER NOT NULL DEFAULT 1,
        deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
        tentativas_falhas INTEGER NOT NULL DEFAULT 0,
        bloqueado_ate TEXT,
        versao_sessao INTEGER NOT NULL DEFAULT 1,
        ultimo_acesso_em TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO usuarios_novo (id, nome, email, senha_hash, perfil, ativo, deve_trocar_senha, tentativas_falhas,
                               bloqueado_ate, versao_sessao, ultimo_acesso_em, criado_em, atualizado_em)
        SELECT id, nome, email, senha_hash, perfil, ativo, deve_trocar_senha, tentativas_falhas, bloqueado_ate,
               versao_sessao, ultimo_acesso_em, criado_em, atualizado_em FROM usuarios;
    DROP TABLE usuarios;
    ALTER TABLE usuarios_novo RENAME TO usuarios;
    CREATE UNIQUE INDEX ux_usuarios_email ON usuarios(email COLLATE NOCASE);

    ALTER TABLE unidades ADD COLUMN redes TEXT;

    CREATE TABLE computadores (
        id INTEGER PRIMARY KEY,
        unidade_id INTEGER NOT NULL REFERENCES unidades(id),
        nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
        nome_ordem TEXT NOT NULL,
        tipo TEXT NOT NULL CHECK (tipo IN ('desktop', 'notebook')),
        situacao TEXT NOT NULL CHECK (situacao IN ('em_uso', 'reserva', 'manutencao', 'desativado')),
        usuario_nome TEXT,
        usuario_login TEXT,
        setor TEXT,
        usuario_situacao TEXT CHECK (usuario_situacao IN ('ativo', 'ferias', 'desligado')),
        sistema TEXT,
        processador TEXT,
        memoria_gb INTEGER,
        ip TEXT,
        ip_ordem INTEGER,
        mac TEXT,
        ip_wifi TEXT,
        mac_wifi TEXT,
        sep TEXT CHECK (sep IN ('ok', 'pendente', 'nao_instalado')),
        remoto_ferramenta TEXT,
        remoto_id TEXT,
        nobreak INTEGER,
        monitor TEXT,
        webcam INTEGER NOT NULL DEFAULT 0,
        headset INTEGER NOT NULL DEFAULT 0,
        outros_perifericos TEXT,
        softwares TEXT,
        marca TEXT,
        modelo TEXT,
        numero_serie TEXT,
        patrimonio TEXT,
        observacoes TEXT,
        desativacao_motivo TEXT,
        destino_disco TEXT,
        desativado_em TEXT,
        conferido_em TEXT,
        conferido_por INTEGER REFERENCES usuarios(id),
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_comp_unidade ON computadores(unidade_id, nome_ordem);
    CREATE INDEX ix_comp_ip ON computadores(ip);
    CREATE INDEX ix_comp_ip_wifi ON computadores(ip_wifi);
    CREATE INDEX ix_comp_mac ON computadores(mac);
    CREATE INDEX ix_comp_mac_wifi ON computadores(mac_wifi);

    CREATE TABLE computadores_historico (
        id INTEGER PRIMARY KEY,
        computador_id INTEGER NOT NULL REFERENCES computadores(id) ON DELETE CASCADE,
        ocorrido_em TEXT NOT NULL DEFAULT (datetime('now')),
        usuario_id INTEGER REFERENCES usuarios(id),
        tipo TEXT NOT NULL,
        descricao TEXT NOT NULL,
        detalhes TEXT
    );
    CREATE INDEX ix_comp_historico ON computadores_historico(computador_id, ocorrido_em);
    """,
    # 4 — login pelo AD (perfil colaborador), cobertura temporária de unidade e módulo de chamados
    """
    CREATE TABLE usuarios_novo (
        id INTEGER PRIMARY KEY,
        nome TEXT NOT NULL,
        email TEXT,
        senha_hash TEXT,
        perfil TEXT NOT NULL CHECK (perfil IN ('admin', 'tecnico', 'consulta', 'colaborador')),
        origem TEXT NOT NULL DEFAULT 'local' CHECK (origem IN ('local', 'ad')),
        login_ad TEXT COLLATE NOCASE,
        unidade_id INTEGER REFERENCES unidades(id),
        setor TEXT,
        ad_dn TEXT,
        ad_sincronizado_em TEXT,
        ativo INTEGER NOT NULL DEFAULT 1,
        deve_trocar_senha INTEGER NOT NULL DEFAULT 0,
        tentativas_falhas INTEGER NOT NULL DEFAULT 0,
        bloqueado_ate TEXT,
        versao_sessao INTEGER NOT NULL DEFAULT 1,
        ultimo_acesso_em TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    INSERT INTO usuarios_novo (id, nome, email, senha_hash, perfil, ativo, deve_trocar_senha, tentativas_falhas,
                               bloqueado_ate, versao_sessao, ultimo_acesso_em, criado_em, atualizado_em)
        SELECT id, nome, email, senha_hash, perfil, ativo, deve_trocar_senha, tentativas_falhas, bloqueado_ate,
               versao_sessao, ultimo_acesso_em, criado_em, atualizado_em FROM usuarios;
    DROP TABLE usuarios;
    ALTER TABLE usuarios_novo RENAME TO usuarios;
    -- e-mail único só entre contas locais (quem entra pelo AD é identificado pelo login)
    CREATE UNIQUE INDEX ux_usuarios_email ON usuarios(email COLLATE NOCASE) WHERE origem = 'local';
    CREATE UNIQUE INDEX ux_usuarios_login_ad ON usuarios(login_ad) WHERE login_ad IS NOT NULL;
    CREATE INDEX ix_usuarios_perfil ON usuarios(perfil, ativo);

    ALTER TABLE usuarios_unidades ADD COLUMN criado_em TEXT;
    ALTER TABLE usuarios_unidades ADD COLUMN criado_por INTEGER REFERENCES usuarios(id);
    ALTER TABLE usuarios_unidades ADD COLUMN expira_em TEXT;
    UPDATE usuarios_unidades SET criado_em = datetime('now');

    ALTER TABLE unidades ADD COLUMN ou_ad TEXT;
    ALTER TABLE unidades ADD COLUMN chamados_ativo INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE unidades ADD COLUMN teams_webhook TEXT;   -- link do fluxo do Teams (criptografado)
    ALTER TABLE unidades ADD COLUMN teams_tambem TEXT;    -- ids das unidades cujos canais também recebem os avisos

    CREATE TABLE login_tentativas (
        chave TEXT PRIMARY KEY,
        falhas INTEGER NOT NULL DEFAULT 0,
        bloqueado_ate TEXT,
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE chamados (
        id INTEGER PRIMARY KEY,
        unidade_id INTEGER NOT NULL REFERENCES unidades(id),
        solicitante_id INTEGER NOT NULL REFERENCES usuarios(id),
        aberto_por_id INTEGER REFERENCES usuarios(id),
        categoria TEXT NOT NULL CHECK (categoria IN ('computador', 'celular', 'impressora', 'rede', 'acesso',
                                                     'sistemas', 'seguranca', 'outro')),
        impacto TEXT CHECK (impacto IN ('nao', 'pouco', 'parou')),
        prioridade TEXT NOT NULL CHECK (prioridade IN ('alta', 'media', 'baixa')),
        situacao TEXT NOT NULL CHECK (situacao IN ('novo', 'atendimento', 'aguardando', 'resolvido', 'fechado')),
        assunto TEXT NOT NULL,
        computador_id INTEGER REFERENCES computadores(id),
        aparelho_id INTEGER REFERENCES aparelhos(id),
        equipamento_texto TEXT,
        origem_ip TEXT,
        origem_computador_id INTEGER REFERENCES computadores(id),
        responsavel_id INTEGER REFERENCES usuarios(id),
        prazo_base_em TEXT NOT NULL,
        pausa_total_min INTEGER NOT NULL DEFAULT 0,
        pausado_em TEXT,
        prazo_em TEXT NOT NULL,
        assumido_em TEXT,
        resolvido_em TEXT,
        fechado_em TEXT,
        avisos_enviados INTEGER NOT NULL DEFAULT 0,
        ultimo_aviso_em TEXT,
        aviso_prazo TEXT,
        seg_conhecido_em TEXT,
        seg_acoes TEXT,
        seg_dados_pessoais TEXT CHECK (seg_dados_pessoais IN ('sim', 'nao', 'nao_sei')),
        ultima_interacao_em TEXT NOT NULL,
        busca TEXT,   -- assunto, solicitante, setor e máquina já sem acentos (busca rápida na fila)
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_chamados_unidade ON chamados(unidade_id, situacao);
    CREATE INDEX ix_chamados_situacao ON chamados(situacao, prazo_em);
    CREATE INDEX ix_chamados_solicitante ON chamados(solicitante_id, criado_em);
    CREATE INDEX ix_chamados_responsavel ON chamados(responsavel_id, situacao);
    CREATE INDEX ix_chamados_computador ON chamados(computador_id);
    CREATE INDEX ix_chamados_aparelho ON chamados(aparelho_id);

    CREATE TABLE chamados_mensagens (
        id INTEGER PRIMARY KEY,
        chamado_id INTEGER NOT NULL REFERENCES chamados(id),
        autor_id INTEGER REFERENCES usuarios(id),
        tipo TEXT NOT NULL CHECK (tipo IN ('mensagem', 'resposta', 'nota', 'evento')),
        texto TEXT NOT NULL,
        publico INTEGER NOT NULL DEFAULT 0,   -- 1 = o colaborador vê (mensagens dele, respostas e alguns eventos)
        criado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_chamados_mensagens ON chamados_mensagens(chamado_id, id);

    CREATE TABLE chamados_anexos (
        id INTEGER PRIMARY KEY,
        chamado_id INTEGER NOT NULL REFERENCES chamados(id),
        mensagem_id INTEGER REFERENCES chamados_mensagens(id),
        nome TEXT NOT NULL,
        arquivo TEXT NOT NULL,
        tipo TEXT NOT NULL,
        tamanho INTEGER NOT NULL,
        criado_por INTEGER REFERENCES usuarios(id),
        criado_em TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX ix_chamados_anexos ON chamados_anexos(chamado_id);

    CREATE TABLE avisos_teams (
        id INTEGER PRIMARY KEY,
        chamado_id INTEGER REFERENCES chamados(id),
        destino TEXT NOT NULL,
        conteudo TEXT NOT NULL,
        situacao TEXT NOT NULL DEFAULT 'pendente' CHECK (situacao IN ('pendente', 'enviando', 'enviado', 'falha')),
        tentativas INTEGER NOT NULL DEFAULT 0,
        proxima_em TEXT,
        erro TEXT,
        criado_em TEXT NOT NULL DEFAULT (datetime('now')),
        atualizado_em TEXT NOT NULL DEFAULT (datetime('now')),
        enviado_em TEXT
    );
    CREATE INDEX ix_avisos_teams ON avisos_teams(situacao, proxima_em);
    """,
    # 5 — resposta do colaborador ainda não vista pela TI (aviso no menu, no sino e no Windows)
    """
    ALTER TABLE chamados ADD COLUMN resposta_em TEXT;
    CREATE INDEX ix_chamados_resposta ON chamados(resposta_em) WHERE resposta_em IS NOT NULL;
    """,
    # 6 — até qual mensagem o colaborador já viu (número de respostas novas da TI em "Meus pedidos")
    """
    ALTER TABLE chamados ADD COLUMN colaborador_viu_ate INTEGER NOT NULL DEFAULT 0;
    UPDATE chamados SET colaborador_viu_ate = COALESCE(
        (SELECT MAX(m.id) FROM chamados_mensagens m WHERE m.chamado_id = chamados.id), 0);
    """,
    # 7 — resolvido dentro ou fora do prazo (relatório mensal e auditoria), gravado na hora em que resolve
    """
    ALTER TABLE chamados ADD COLUMN resolvido_no_prazo INTEGER;
    UPDATE chamados SET resolvido_no_prazo = CASE WHEN resolvido_em <= prazo_em THEN 1 ELSE 0 END
        WHERE resolvido_em IS NOT NULL;
    CREATE INDEX ix_chamados_resolvido ON chamados(resolvido_em) WHERE resolvido_em IS NOT NULL;
    """,
]


def migrar(conexao: sqlite3.Connection) -> int:
    """Aplica as migrações pendentes. Devolve a versão final do esquema.

    As chaves estrangeiras ficam desligadas durante as migrações (procedimento recomendado pelo SQLite para
    recriar tabelas) e são conferidas antes de cada COMMIT.
    """
    conexao.execute("PRAGMA journal_mode = WAL")
    versao = conexao.execute("PRAGMA user_version").fetchone()[0]
    if versao >= len(MIGRACOES):
        return versao
    conexao.execute("PRAGMA foreign_keys = OFF")
    try:
        for numero, script in enumerate(MIGRACOES, start=1):
            if numero <= versao:
                continue
            conexao.executescript(f"BEGIN IMMEDIATE;\n{script}\nPRAGMA user_version = {numero};")
            problemas = conexao.execute("PRAGMA foreign_key_check").fetchall()
            if problemas:
                conexao.execute("ROLLBACK")
                raise RuntimeError(f"Migração {numero} deixaria referências inválidas: {problemas[:5]}")
            conexao.execute("COMMIT")
            versao = numero
    finally:
        conexao.execute("PRAGMA foreign_keys = ON")
    return versao
