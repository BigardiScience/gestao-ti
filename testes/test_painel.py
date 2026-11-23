"""Painel inicial: guia de primeiros passos com o sistema vazio e a frota em ícones depois."""
import re

from conftest import criar_aparelho, criar_unidade


def test_painel_vazio_mostra_primeiros_passos(app, admin):
    pagina = admin.get("/").get_data(as_text=True)
    assert "Vamos montar a sua frota" in pagina and "0 de 5" in pagina
    assert "Termos de responsabilidade" not in pagina  # sem aparelhos, o cartão de termos não aparece
    assert "Últimas movimentações" in pagina


def test_painel_com_aparelhos_mostra_a_frota_em_icones(app, admin):
    unidade = criar_unidade(admin)
    for _ in range(3):
        criar_aparelho(admin, unidade)
    criar_aparelho(admin, unidade, status="manutencao")
    pagina = admin.get("/").get_data(as_text=True)
    assert len(re.findall(r'class="frota__icone frota__icone--disponivel"', pagina)) == 3
    assert len(re.findall(r'class="frota__icone frota__icone--manutencao"', pagina)) == 1
    assert "aparelhos na frota" in pagina and "Cada ícone é um aparelho" in pagina
    assert "Primeiros passos" in pagina and "2 de 5" in pagina  # unidades e aparelhos feitos
    assert "Nenhum aparelho em uso" in pagina


def test_frota_grande_agrupa_icones():
    from app.servicos.painel import frota_em_icones
    frota = frota_em_icones({"em_uso": 900, "disponivel": 50, "manutencao": 1, "devolvido": 0, "baixado": 5})
    assert frota["escala"] == 3 and frota["tamanho"] == "p"
    assert [g["icones"] for g in frota["grupos"]] == [300, 17, 1]  # todo status presente tem ao menos 1 ícone
