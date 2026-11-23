from app.validacao import (cnpj_valido, cpf_valido, formatar_cpf, formatar_telefone, imei_valido, ler_valor_centavos,
                           mascarar_cpf, telefone_valido)
from conftest import gerar_cpf, gerar_imei


def test_cpf():
    assert cpf_valido("529.982.247-25")
    assert cpf_valido(gerar_cpf("123456789"))
    assert not cpf_valido("529.982.247-24")
    assert not cpf_valido("111.111.111-11")
    assert not cpf_valido("123")
    assert formatar_cpf("52998224725") == "529.982.247-25"
    assert mascarar_cpf("52998224725") == "***.982.247-**"


def test_cnpj_numerico_e_alfanumerico():
    assert cnpj_valido("11.222.333/0001-81")
    assert not cnpj_valido("11.222.333/0001-82")
    assert cnpj_valido("12.ABC.345/01DE-35")  # exemplo oficial do novo formato
    assert not cnpj_valido("12.ABC.345/01DE-36")


def test_imei():
    assert imei_valido("356938035643809")
    assert imei_valido(gerar_imei("35693803564380"))
    assert not imei_valido("356938035643808")
    assert not imei_valido("35693803564380")
    assert not imei_valido("35693803564380A")


def test_telefone_e_valor():
    assert telefone_valido("(11) 98765-4321")
    assert telefone_valido("+55 11 98765-4321")
    assert not telefone_valido("98765-4321")
    assert formatar_telefone("11987654321") == "(11) 98765-4321"
    assert ler_valor_centavos("1.899,90") == 189990
    assert ler_valor_centavos("1899.9") == 189990
    assert ler_valor_centavos(1899.9) == 189990
