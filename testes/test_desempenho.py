"""Desempenho e tamanho: contagem barata na paginação, arquivos estáticos com cache e gzip,
busca de opções sob demanda e índices do banco."""
import gzip
import re

from conftest import criar_aparelho, criar_colaborador, criar_unidade

from app.util import sql_contagem


def _uma_linha(sql: str) -> str:
    return " ".join(sql.split())


def test_contagem_retira_juncoes_que_nao_mudam_o_total():
    sql = """SELECT m.*, a.marca, u.nome AS unidade, us.nome AS usuario
               FROM movimentacoes m
               LEFT JOIN aparelhos a ON a.id = m.aparelho_id
               LEFT JOIN unidades u ON u.id = a.unidade_id
               LEFT JOIN usuarios us ON us.id = m.usuario_id
              WHERE 1=1 ORDER BY m.ocorrido_em DESC"""
    assert _uma_linha(sql_contagem(sql)) == "SELECT COUNT(*) FROM movimentacoes m WHERE 1=1"
    # a junção usada no filtro fica, e também a que ela depende
    com_filtro = sql.replace("WHERE 1=1", "WHERE u.nome LIKE ?")
    contagem = _uma_linha(sql_contagem(com_filtro))
    assert "JOIN aparelhos a" in contagem and "JOIN unidades u" in contagem and "usuarios" not in contagem
    assert "ORDER BY" not in contagem
    # parâmetros na lista de colunas ou agrupamentos: conta pelo jeito seguro
    assert sql_contagem("SELECT a.*, (SELECT 1 WHERE ? = 1) FROM aparelhos a").startswith("SELECT COUNT(*) FROM (")
    assert sql_contagem("SELECT status, COUNT(*) FROM aparelhos GROUP BY status").startswith("SELECT COUNT(*) FROM (")


def test_busca_paginada_alem_da_ultima_pagina(app, admin):
    unidade = criar_unidade(admin)
    for _ in range(3):
        criar_aparelho(admin, unidade)
    pagina = admin.get("/aparelhos?q=Galaxy&pagina=99").get_data(as_text=True)
    assert pagina.count('class="titulo-celula"') == 3
    assert admin.get("/colaboradores?q=ninguem-com-esse-nome").status_code == 200


def test_arquivos_estaticos_com_cache_longo_e_gzip(app, admin):
    html = admin.get("/").get_data(as_text=True)
    url_css = re.search(r'href="(/static/css/app\.css\?v=[0-9a-f]+)"', html).group(1)
    assert re.search(r'/static/img/icones\.svg\?v=[0-9a-f]+#i-', html), "ícones devem vir do arquivo em cache"
    r = admin.get(url_css, headers={"Accept-Encoding": "gzip, deflate, br"})
    assert r.status_code == 200 and r.headers["Content-Encoding"] == "gzip"
    assert "immutable" in r.headers["Cache-Control"] and "max-age=31536000" in r.headers["Cache-Control"]
    with open(app.static_folder + "/css/app.css", "rb") as arquivo:
        assert gzip.decompress(r.data) == arquivo.read()
    sem_versao = admin.get("/static/css/app.css")
    assert "no-cache" in sem_versao.headers["Cache-Control"] and "Content-Encoding" not in sem_versao.headers
    # páginas continuam sem cache (dados pessoais)
    assert admin.get("/").headers["Cache-Control"] == "no-store"


def test_opcoes_de_colaborador_e_aparelho_sob_demanda(app, admin, anonimo):
    unidade = criar_unidade(admin)
    _, ana = criar_colaborador(admin, unidade, nome="Ana Busca Teste")
    criar_colaborador(admin, unidade, nome="Bruno Outro Nome")
    disponivel = criar_aparelho(admin, unidade, modelo="Moto Busca")
    criar_aparelho(admin, unidade, modelo="Moto Parado", status="manutencao")

    opcoes = admin.get("/opcoes/colaboradores?q=ana busca").get_json()
    assert [o["titulo"] for o in opcoes] == ["Ana Busca Teste"] and "Financeiro" in opcoes[0]["detalhe"]
    assert admin.get(f"/opcoes/colaboradores?q=ana&excluir={opcoes[0]['id']}").get_json() == []
    assert len(admin.get("/opcoes/colaboradores").get_json()) == 2

    aparelhos = admin.get("/opcoes/aparelhos?q=moto").get_json()
    assert [a["id"] for a in aparelhos] == [disponivel]  # só os disponíveis
    assert anonimo.get("/opcoes/aparelhos").status_code == 302

    # a tela de entrega não carrega mais todas as pessoas e aparelhos
    pagina = admin.get(f"/entregas/nova?aparelho={disponivel}").get_data(as_text=True)
    assert "Moto Busca" in pagina and "Ana Busca Teste" not in pagina and 'data-seletor="/opcoes/colaboradores"' in pagina


def test_historico_de_um_aparelho_e_indices(app, admin):
    unidade = criar_unidade(admin)
    a1 = criar_aparelho(admin, unidade, modelo="Galaxy Escopo")
    criar_aparelho(admin, unidade, modelo="Galaxy Fora")
    pagina = admin.get(f"/movimentacoes?aparelho_id={a1}").get_data(as_text=True)
    assert "Histórico de" in pagina and "Galaxy Escopo" in pagina and "Galaxy Fora" not in pagina
    assert admin.get("/movimentacoes?aparelho_id=abc").status_code == 200
    with app.app_context():
        from app.db import consultar, escalar
        indices = {l["name"] for l in consultar("SELECT name FROM sqlite_master WHERE type = 'index'")}
        assert {"ix_termos_vinculo", "ix_termos_emitido", "ix_vinculos_aparelho", "ix_mov_destino"} <= indices
        assert escalar("PRAGMA user_version") >= 2
