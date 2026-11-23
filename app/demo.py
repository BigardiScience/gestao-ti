"""Dados de demonstração para conhecer a plataforma (comando: flask --app wsgi dados-exemplo).

Só funciona com o banco vazio (sem aparelhos e sem colaboradores), para nunca misturar
dados fictícios com dados reais.
"""
import random
from datetime import timedelta

from flask import g

from .db import consultar, escalar, executar
from .util import agora, para_bd, valor_datetime_local

UNIDADES = [
    {"nome": "Matriz Sorocaba", "sigla": "MTZ", "cidade": "Sorocaba", "uf": "SP"},
    {"nome": "Fábrica Indaiatuba", "sigla": "FJD", "cidade": "Indaiatuba", "uf": "SP"},
    {"nome": "CD Campinas", "sigla": "CDC", "cidade": "Campinas", "uf": "SP"},
]
PESSOAS = [
    ("Mariana Albuquerque Costa", "Analista financeira", "Financeiro", 0),
    ("Rafael Nogueira Lima", "Coordenador de logística", "Logística", 2),
    ("Juliana Ferreira Santos", "Supervisora de produção", "Produção", 1),
    ("Thiago Martins Rocha", "Vendedor externo", "Comercial", 0),
    ("Camila Ribeiro Duarte", "Gerente comercial", "Comercial", 0),
    ("Bruno Henrique Teixeira", "Técnico de manutenção", "Manutenção", 1),
    ("Larissa Gomes Pereira", "Analista de RH", "Recursos Humanos", 0),
    ("Diego Carvalho Mendes", "Motorista", "Logística", 2),
    ("Patrícia Moreira Lopes", "Compradora", "Suprimentos", 0),
    ("Gustavo Almeida Barros", "Líder de expedição", "Logística", 2),
    ("Fernanda Castro Vieira", "Engenheira de processos", "Produção", 1),
    ("Lucas Fernandes Araújo", "Vendedor externo", "Comercial", 0),
    ("Aline Souza Cardoso", "Analista de qualidade", "Qualidade", 1),
    ("Rodrigo Pinto Monteiro", "Supervisor de segurança", "Segurança do Trabalho", 1),
    ("Beatriz Lima Correia", "Assistente administrativa", "Administrativo", 0),
    ("Eduardo Ramos Freitas", "Encarregado de turno", "Produção", 1),
    ("Vanessa Rocha Batista", "Analista de PCP", "Planejamento", 1),
    ("Marcelo Dias Azevedo", "Conferente", "Logística", 2),
]
MODELOS = [("Samsung", "Galaxy A54", 189990), ("Samsung", "Galaxy A35", 159900), ("Motorola", "Moto G84", 129900),
           ("Apple", "iPhone 13", 379900), ("Samsung", "Galaxy S23 FE", 299900), ("Motorola", "Edge 40", 219900),
           ("Xiaomi", "Redmi Note 13", 119900)]
TRACOS = {"w": 600, "h": 190, "tracos": [[[42, 128], [64, 92], [86, 70], [103, 88], [110, 126], [124, 96], [150, 72],
                                         [168, 104], [176, 131], [205, 86], [232, 76], [251, 109], [281, 98]],
                                        [[300, 120], [330, 92], [352, 112], [381, 84], [418, 102], [451, 95]]]}


def _imei(indice: int) -> str:
    base = f"35{random.Random(indice).randint(10 ** 11, 10 ** 12 - 1)}"[:14]
    soma = 0
    for i, c in enumerate(reversed(base)):
        d = int(c) * (2 if i % 2 == 0 else 1)
        soma += d - 9 if d > 9 else d
    return base + str((10 - soma % 10) % 10)


def _cpf(indice: int) -> str:
    numeros = [int(c) for c in f"{random.Random(1000 + indice).randint(10 ** 8, 10 ** 9 - 1)}"]
    for tamanho in (9, 10):
        soma = sum(numeros[i] * (tamanho + 1 - i) for i in range(tamanho))
        numeros.append((soma * 10) % 11 % 10)
    return "".join(map(str, numeros))


def _email(nome: str) -> str:
    from .db import normalizar
    partes = normalizar(nome).split()
    return f"{partes[0]}.{partes[-1]}@empresa.com.br"


def _quando(dias: int, hora: int = 10) -> str:
    momento = (agora() - timedelta(days=dias)).replace(hour=hora + 3, minute=random.randint(0, 59))
    return valor_datetime_local(para_bd(momento))


def popular() -> str:
    from .servicos import aparelhos, colaboradores, termos, unidades, vinculos
    from .servicos.configuracoes import salvar
    if escalar("SELECT COUNT(*) FROM aparelhos") or escalar("SELECT COUNT(*) FROM colaboradores"):
        raise RuntimeError("O banco já tem aparelhos ou colaboradores. Os dados de exemplo só podem ser criados "
                           "num banco vazio.")
    admin = consultar("SELECT * FROM usuarios WHERE perfil = 'admin' ORDER BY id LIMIT 1", um=True)
    if not admin:
        raise RuntimeError("Faça a instalação (crie o administrador) antes de gerar os dados de exemplo.")
    g.usuario = admin
    salvar({"empresa_cidade": "Sorocaba", "ti_email_contato": admin["email"]})
    ids_unidades = []
    for u in UNIDADES:
        existente = unidades.localizar_por_nome(u["nome"])
        ids_unidades.append(existente["id"] if existente else unidades.criar(dict(u, ativo="1")))

    pessoas = []
    for indice, (nome, cargo, departamento, unidade) in enumerate(PESSOAS):
        pessoas.append(colaboradores.criar({
            "nome": nome, "cpf": _cpf(indice), "email": _email(nome), "cargo": cargo, "departamento": departamento,
            "unidade_id": ids_unidades[unidade], "matricula": f"{2100 + indice}"}))

    lista = []
    for indice in range(24):
        marca, modelo, valor = MODELOS[indice % len(MODELOS)]
        unidade = ids_unidades[[0, 0, 1, 1, 2][indice % 5]]
        lista.append(aparelhos.criar({
            "marca": marca, "modelo": modelo, "numero_serie": f"R58T{4100 + indice}X", "imei": _imei(indice),
            "linha": f"119{87650000 + indice * 37:08d}" if indice < 20 else "", "operadora": "Vivo" if indice % 3 else "Claro",
            "unidade_id": unidade, "estado_conservacao": "Novo" if indice > 12 else "Bom", "status": "disponivel",
            "valor": f"{valor / 100:.2f}", "acessorios": "Carregador, cabo e capa",
            "data_aquisicao": (agora() - timedelta(days=400 - indice * 9)).date().isoformat()}))

    def entregar(aparelho, pessoa, dias):
        _, termo_id = vinculos.entregar({"aparelho_id": aparelho, "colaborador_id": pessoa, "entregue_em": _quando(dias),
                                         "gerar_termo": "1"})
        return termo_id

    def assinar(termo_id):
        termos.enviar(termo_id)
        termo = termos.obter(termo_id, verificar_acesso=False)
        termos.assinar(termo_id, {"cpf": termo["colaborador_cpf"], "nome": termo["colaborador_nome"],
                                  "tipo_assinatura": "desenho", "tracos": _json(TRACOS), "aceite": "1"},
                       metodo="portal", ip="10.0.0.25", user_agent="Demonstração")

    for posicao in range(10):  # assinados
        assinar(entregar(lista[posicao], pessoas[posicao], 120 - posicao * 9))
    for posicao in range(10, 13):  # aguardando assinatura
        termos.enviar(entregar(lista[posicao], pessoas[posicao], 4 + posicao - 10))
    termo_expirado = entregar(lista[13], pessoas[13], 16)
    termos.enviar(termo_expirado)
    executar("UPDATE termos SET token_expira_em = ? WHERE id = ?", (para_bd(agora() - timedelta(days=2)), termo_expirado))
    entregar(lista[14], pessoas[14], 1)  # gerado, aguardando envio
    vinculos.entregar({"aparelho_id": lista[15], "colaborador_id": pessoas[15], "entregue_em": _quando(200),
                       "gerar_termo": "0"})  # entrega antiga, sem termo (como na planilha)

    vinculo = escalar("SELECT id FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (lista[2],))
    _, novo = vinculos.transferir(vinculo, {"colaborador_id": pessoas[16], "entregue_em": _quando(20),
                                            "gerar_termo": "1"})
    assinar(novo)
    vinculo = escalar("SELECT id FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (lista[5],))
    _, novo = vinculos.substituir(vinculo, {"aparelho_id": lista[16], "entregue_em": _quando(12),
                                            "estado_devolucao": "Danificado", "destino": "manutencao",
                                            "motivo": "Tela quebrada", "gerar_termo": "1"})
    assinar(novo)
    aparelhos.alterar_status(lista[17], "manutencao", observacao="Bateria estufada, enviado à assistência.")
    vinculo = escalar("SELECT id FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (lista[7],))
    vinculos.devolver(vinculo, {"devolvido_em": _quando(3), "estado": "Bom", "destino": "devolvido",
                                "acessorios": "Carregador"})
    aparelhos.alterar_status(lista[18], "baixado", motivo="Furto ou roubo", observacao="Boletim de ocorrência 1234/2026.")
    colaboradores.desligar(pessoas[9], "Desligamento a pedido.")
    executar("UPDATE vinculos SET devolucao_prevista = ? WHERE aparelho_id = ? AND devolvido_em IS NULL",
             ((agora() - timedelta(days=5)).date().isoformat(), lista[12]))
    # os cadastros "aconteceram" antes das entregas retroativas
    inicio = para_bd(agora() - timedelta(days=240))
    executar("UPDATE movimentacoes SET ocorrido_em = ? WHERE tipo IN ('cadastro', 'colaborador_cadastro')", (inicio,))
    executar("UPDATE aparelhos SET criado_em = ?", (inicio,))
    executar("UPDATE colaboradores SET criado_em = ?", (inicio,))
    return (f"{len(UNIDADES)} unidades, {len(pessoas)} colaboradores e {len(lista)} aparelhos de exemplo criados.")


def _json(dados):
    import json
    return json.dumps(dados)
