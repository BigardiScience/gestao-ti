"""Fluxos principais: instalação, cadastros, entrega, termo, envio, assinatura, devolução, transferência e troca."""
import io
import json

from conftest import (SENHA_ADMIN, TRACOS, Navegador, codigo_do_email, criar_aparelho, criar_colaborador,
                      criar_unidade, gerar_imei, token_do_email, ultimo_email)


def consulta(app, sql, parametros=(), um=True):
    with app.app_context():
        from app.db import consultar
        return consultar(sql, parametros, um=um)


# ------------------------------------------------------------------ instalação e acesso

def test_instalacao_exige_codigo_e_cria_admin(app, anonimo):
    assert anonimo.get("/").headers["Location"].endswith("/instalacao")
    r = anonimo.post("/instalacao", {"codigo": "ERRADO", "empresa_nome": "X", "url_base": "http://x", "nome": "Ana",
                                     "email": "a@b.com", "senha": "abc12345", "confirmar": "abc12345"})
    assert r.status_code == 200 and "Código incorreto" in r.get_data(as_text=True)
    r = anonimo.post("/instalacao", {"codigo": app.config["CODIGO_INSTALACAO"], "empresa_nome": "Grupo X",
                                     "url_base": "http://localhost", "nome": "Ana Admin", "email": "ana@x.com",
                                     "senha": "abc12345", "confirmar": "abc12345"})
    assert r.status_code == 302
    assert anonimo.get("/").status_code == 200
    assert consulta(app, "SELECT perfil FROM usuarios")["perfil"] == "admin"


def test_login_bloqueia_apos_tentativas(app, admin):
    admin.post("/sair")
    nav = Navegador(app.test_client())
    for _ in range(5):
        r = nav.post("/entrar", {"email": "ti@empresa.com.br", "senha": "errada"})
        assert "incorretos" in r.get_data(as_text=True)
    r = nav.post("/entrar", {"email": "ti@empresa.com.br", "senha": SENHA_ADMIN})
    assert "bloqueado" in r.get_data(as_text=True)
    with app.app_context():
        from app.db import executar
        executar("UPDATE usuarios SET bloqueado_ate = NULL")
    r = nav.post("/entrar", {"email": "TI@empresa.com.br", "senha": SENHA_ADMIN})
    assert r.status_code == 302


def test_csrf_obrigatorio(admin):
    r = admin.cliente.post("/unidades/nova", data={"nome": "Sem token"})
    assert r.status_code == 400


def test_cabecalhos_de_seguranca(admin):
    r = admin.get("/")
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "no-referrer"
    assert r.headers["Cache-Control"] == "no-store"


# ------------------------------------------------------------------ cadastros

def test_validacoes_de_cadastro(app, admin):
    unidade = criar_unidade(admin)
    r = admin.post("/aparelhos/novo", {"marca": "Apple", "modelo": "iPhone 13", "numero_serie": "X1",
                                       "imei": "356938035643808", "unidade_id": unidade,
                                       "estado_conservacao": "Novo", "status": "disponivel"})
    assert "IMEI inválido" in r.get_data(as_text=True)
    aparelho = criar_aparelho(admin, unidade)
    imei = consulta(app, "SELECT imei FROM aparelhos WHERE id = ?", (aparelho,))["imei"]
    r = admin.post("/aparelhos/novo", {"marca": "Apple", "modelo": "iPhone 13", "numero_serie": "X2", "imei": imei,
                                       "unidade_id": unidade, "estado_conservacao": "Novo", "status": "disponivel"})
    assert "já está cadastrado" in r.get_data(as_text=True)
    r = admin.post("/aparelhos/novo", {"marca": "Apple", "modelo": "iPhone 13", "numero_serie": "X3",
                                       "imei": gerar_imei("35000000000001"), "linha": "(11) 3333-444",
                                       "unidade_id": unidade, "estado_conservacao": "Novo", "status": "em_uso"})
    texto = r.get_data(as_text=True)
    assert "Linha inválida" in texto and "Entregar aparelho" in texto
    r = admin.post("/colaboradores/novo", {"nome": "Fulano", "cpf": "111.111.111-11", "email": "x@empresa.com",
                                           "cargo": "A", "departamento": "B", "unidade_id": unidade})
    assert "CPF inválido" in r.get_data(as_text=True)
    criar_colaborador(admin, unidade, cpf="529.982.247-25")
    r = admin.post("/colaboradores/novo", {"nome": "Outro", "cpf": "52998224725", "email": "y@empresa.com",
                                           "cargo": "A", "departamento": "B", "unidade_id": unidade})
    assert "já está cadastrado" in r.get_data(as_text=True)
    assert admin.get(f"/aparelhos/{aparelho}").status_code == 200
    assert admin.get("/aparelhos?q=galaxy").status_code == 200
    assert admin.get(f"/busca?q={imei}").headers["Location"].endswith(f"/aparelhos/{aparelho}")


# ------------------------------------------------------------------ fluxo completo do termo

def test_fluxo_completo_entrega_termo_assinatura(app, admin):
    unidade = criar_unidade(admin)
    aparelho = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, nome="Maria da Silva Souza", aparelho_id=aparelho)
    assert "/termos/" in r.headers["Location"]
    termo_id = int(r.headers["Location"].rstrip("/").split("/")[-1])
    termo = consulta(app, "SELECT * FROM termos WHERE id = ?", (termo_id,))
    assert termo["status"] == "gerado" and termo["codigo"].startswith("TR-")
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (aparelho,))["status"] == "em_uso"
    pagina = admin.get(f"/termos/{termo_id}").get_data(as_text=True)
    assert "Maria da Silva Souza" in pagina and "Enviar para assinatura" in pagina
    previa = admin.get(f"/termos/{termo_id}/pdf")
    assert previa.status_code == 200 and previa.data[:4] == b"%PDF"

    r = admin.post(f"/termos/{termo_id}/enviar")
    assert r.status_code == 302
    email = ultimo_email(app, "termo")
    assert email["destinatario"] == dados["email"] and email["status"] == "simulado"
    token = token_do_email(email)
    assert consulta(app, "SELECT status FROM termos WHERE id = ?", (termo_id,))["status"] == "enviado"

    colaborador = Navegador(app.test_client())
    pagina = colaborador.get(f"/assinar/{token}")
    assert pagina.status_code == 200 and "Leia o termo" in pagina.get_data(as_text=True)
    assert colaborador.get("/assinar/token-que-nao-existe").status_code == 404

    r = colaborador.post(f"/assinar/{token}", {"acao": "enviar_codigo", "cpf": "000.000.000-00"})
    assert r.status_code == 422 and "não confere" in r.get_data(as_text=True)
    r = colaborador.post(f"/assinar/{token}", {"acao": "enviar_codigo", "cpf": dados["cpf"]})
    assert r.status_code == 200 and "Enviado para" in r.get_data(as_text=True)
    codigo = codigo_do_email(ultimo_email(app, "codigo_assinatura"))

    base = {"acao": "assinar", "cpf": dados["cpf"], "codigo": codigo, "tipo_assinatura": "desenho", "tracos": TRACOS,
            "aceite": "1"}
    r = colaborador.post(f"/assinar/{token}", dict(base, nome="Maria Souza"))
    assert r.status_code == 422 and "não confere com o cadastro" in r.get_data(as_text=True)
    r = colaborador.post(f"/assinar/{token}", dict(base, nome="Maria da Silva Souza", codigo="000000"))
    assert r.status_code == 422 and "Código incorreto" in r.get_data(as_text=True)
    r = colaborador.post(f"/assinar/{token}", dict(base, nome="maria da silva souza", tracos=""))
    assert r.status_code == 422 and "Desenhe a sua assinatura" in r.get_data(as_text=True)
    r = colaborador.post(f"/assinar/{token}", dict(base, nome="  MARIA DA SILVA SOUZA "))
    assert r.status_code == 302 and r.headers["Location"].endswith("/assinar/concluido")

    termo = consulta(app, "SELECT * FROM termos WHERE id = ?", (termo_id,))
    assert termo["status"] == "assinado" and termo["pdf_hash"] and termo["codigo_verificacao"]
    evidencias = json.loads(termo["evidencias_json"])
    assert evidencias["autenticacao"]["codigo_confirmado"] is True
    assert evidencias["signatario"]["cpf"].replace(".", "").replace("-", "") == dados["cpf"]
    concluido = colaborador.get("/assinar/concluido").get_data(as_text=True)
    assert termo["codigo_verificacao"] in concluido
    pdf = colaborador.get("/assinar/concluido/pdf").data
    assert pdf[:4] == b"%PDF"
    from app.util import sha256
    assert sha256(pdf) == termo["pdf_hash"]

    # o link não serve mais para assinar
    assert colaborador.get(f"/assinar/{token}").status_code == 410

    # verificação de autenticidade: pelo arquivo e pelo código
    r = Navegador(app.test_client()).post("/verificar", {"arquivo": (io.BytesIO(pdf), "termo.pdf")},
                                          content_type="multipart/form-data")
    assert "Documento autêntico e íntegro" in r.get_data(as_text=True)
    meio = len(pdf) // 2
    adulterado = pdf[:meio] + bytes([pdf[meio] ^ 0x01]) + pdf[meio + 1:]  # um único bit alterado
    r = Navegador(app.test_client()).post("/verificar", {"arquivo": (io.BytesIO(adulterado), "termo.pdf")},
                                          content_type="multipart/form-data")
    assert "Não confirmado" in r.get_data(as_text=True)
    r = Navegador(app.test_client()).get(f"/verificar?codigo={termo['codigo_verificacao']}")
    assert "Código válido" in r.get_data(as_text=True) and "Maria S. S." in r.get_data(as_text=True)

    # confirmações por e-mail: colaborador (com PDF anexo) e TI
    confirmacao = ultimo_email(app, "confirmacao")
    assert confirmacao["destinatario"] == dados["email"] and confirmacao["anexos"].endswith(".pdf")
    assert ultimo_email(app, "notificacao")["destinatario"] == "ti@empresa.com.br"

    # painel e histórico
    painel = admin.get("/").get_data(as_text=True)
    assert "aparelhos em uso têm termo assinado" in painel and "100%" in painel and "Últimas movimentações" in painel
    historico = admin.get(f"/aparelhos/{aparelho}").get_data(as_text=True)
    for evento in ("Cadastro do aparelho", "Entrega", "Termo emitido", "Termo enviado", "Termo assinado"):
        assert evento in historico
    detalhe = admin.get(f"/termos/{termo_id}").get_data(as_text=True)
    assert "Integridade" in detalhe and "Conferida" in detalhe
    assert admin.get(f"/termos/{termo_id}/pdf").data == pdf


def test_devolucao_transferencia_substituicao(app, admin):
    unidade = criar_unidade(admin)
    a1 = criar_aparelho(admin, unidade)
    a2 = criar_aparelho(admin, unidade)
    criar_colaborador(admin, unidade, nome="Carlos Primeiro")
    criar_colaborador(admin, unidade, nome="Beatriz Segunda")
    c1 = consulta(app, "SELECT id FROM colaboradores WHERE nome = 'Carlos Primeiro'")["id"]
    c2 = consulta(app, "SELECT id FROM colaboradores WHERE nome = 'Beatriz Segunda'")["id"]

    r = admin.post("/entregas/nova", {"aparelho_id": a1, "colaborador_id": c1, "gerar_termo": "1",
                                      "devolucao_prevista": "2020-01-01"})
    assert r.status_code == 302
    v1 = consulta(app, "SELECT id FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (a1,))["id"]
    t1 = consulta(app, "SELECT id FROM termos WHERE vinculo_id = ?", (v1,))["id"]
    assert "devolução em atraso" in admin.get("/alertas").get_data(as_text=True)

    # aparelho em uso não pode ser entregue de novo
    r = admin.post("/entregas/nova", {"aparelho_id": a1, "colaborador_id": c2})
    assert "não está disponível" in r.get_data(as_text=True)

    # transferência: encerra o vínculo, cancela o termo pendente e gera novo termo
    r = admin.post(f"/vinculos/{v1}/transferir", {"colaborador_id": c2, "estado": "Ótimo", "gerar_termo": "1"})
    assert r.status_code == 302
    antigo = consulta(app, "SELECT * FROM vinculos WHERE id = ?", (v1,))
    assert antigo["motivo_encerramento"] == "transferencia" and antigo["devolvido_em"]
    assert consulta(app, "SELECT status FROM termos WHERE id = ?", (t1,))["status"] == "cancelado"
    v2 = consulta(app, "SELECT * FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (a1,))
    assert v2["colaborador_id"] == c2 and v2["vinculo_origem_id"] == v1
    assert consulta(app, "SELECT status FROM termos WHERE vinculo_id = ?", (v2["id"],))["status"] == "gerado"

    # substituição: a1 vai para manutenção, a2 fica com Beatriz
    r = admin.post(f"/vinculos/{v2['id']}/substituir", {"aparelho_id": a2, "estado_devolucao": "Danificado",
                                                       "destino": "manutencao", "motivo": "Tela quebrada",
                                                       "gerar_termo": "1"})
    assert r.status_code == 302
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (a1,))["status"] == "manutencao"
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (a2,))["status"] == "em_uso"
    v3 = consulta(app, "SELECT * FROM vinculos WHERE aparelho_id = ? AND devolvido_em IS NULL", (a2,))
    assert v3["colaborador_id"] == c2

    # devolução com baixa exige motivo
    r = admin.post(f"/vinculos/{v3['id']}/devolver", {"estado": "Bom", "destino": "baixado"})
    assert "motivo da baixa" in r.get_data(as_text=True)
    r = admin.post(f"/vinculos/{v3['id']}/devolver", {"estado": "Bom", "destino": "devolvido", "acessorios": "Carregador"})
    assert r.status_code == 302
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (a2,))["status"] == "devolvido"
    comprovante = admin.get(f"/vinculos/{v3['id']}/comprovante.pdf")
    assert comprovante.status_code == 200 and comprovante.data[:4] == b"%PDF"

    # mudança de status manual e baixa
    r = admin.post(f"/aparelhos/{a2}/status", {"status": "disponivel"})
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (a2,))["status"] == "disponivel"
    r = admin.post(f"/aparelhos/{a1}/status", {"status": "baixado", "motivo": "Dano sem conserto"})
    assert consulta(app, "SELECT status FROM aparelhos WHERE id = ?", (a1,))["status"] == "baixado"

    historico = admin.get(f"/aparelhos/{a1}").get_data(as_text=True)
    for texto in ("Transferido de Carlos Primeiro para Beatriz Segunda", "Recolhido de Beatriz Segunda", "Baixa"):
        assert texto in historico
    movimentacoes = admin.get("/movimentacoes?tipo=transferencia").get_data(as_text=True)
    assert "Transferido de Carlos Primeiro" in movimentacoes
    assert admin.get(f"/aparelhos/{a1}/etiqueta.pdf").data[:4] == b"%PDF"


def test_limite_e_desligamento(app, admin):
    unidade = criar_unidade(admin)
    a1, a2 = criar_aparelho(admin, unidade), criar_aparelho(admin, unidade)
    criar_colaborador(admin, unidade, nome="Diego Limite")
    c = consulta(app, "SELECT id FROM colaboradores WHERE nome = 'Diego Limite'")["id"]
    with app.app_context():
        from app.servicos.configuracoes import salvar
        salvar({"limite_aparelhos_colaborador": "1"})
    assert admin.post("/entregas/nova", {"aparelho_id": a1, "colaborador_id": c}).status_code == 302
    r = admin.post("/entregas/nova", {"aparelho_id": a2, "colaborador_id": c})
    assert "limite da empresa" in r.get_data(as_text=True)
    admin.post(f"/colaboradores/{c}/desligar")
    assert "desligado com aparelho" in admin.get("/alertas").get_data(as_text=True)
    r = admin.post("/entregas/nova", {"aparelho_id": a2, "colaborador_id": c})
    assert "desligado" in r.get_data(as_text=True)


def test_emissao_em_lote_para_entregas_sem_termo(app, admin):
    unidade = criar_unidade(admin)
    for _ in range(3):
        a = criar_aparelho(admin, unidade)
        r, dados = criar_colaborador(admin, unidade)
        c = consulta(app, "SELECT id FROM colaboradores WHERE cpf = ?", (dados["cpf"],))["id"]
        admin.post("/entregas/nova", {"aparelho_id": a, "colaborador_id": c, "gerar_termo": "0"})
    assert "3 entregas sem termo" in admin.get("/alertas").get_data(as_text=True)
    assert "Emitir e enviar todos" in admin.get("/termos?status=sem_termo").get_data(as_text=True)
    r = admin.post("/termos/lote", {"acao": "emitir_pendentes", "enviar_agora": "1"})
    assert r.status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM termos WHERE status = 'enviado'")["n"] == 3
    assert consulta(app, "SELECT COUNT(*) AS n FROM emails WHERE finalidade = 'termo'")["n"] == 3


def test_cancelamento_e_reenvio(app, admin):
    unidade = criar_unidade(admin)
    a = criar_aparelho(admin, unidade)
    r, dados = criar_colaborador(admin, unidade, aparelho_id=a)
    termo_id = int(r.headers["Location"].split("/")[-1])
    admin.post(f"/termos/{termo_id}/enviar")
    token1 = token_do_email(ultimo_email(app, "termo"))
    admin.post(f"/termos/{termo_id}/enviar")  # reenvio reaproveita o link ainda válido
    assert token_do_email(ultimo_email(app, "termo")) == token1
    with app.app_context():
        from app.db import executar
        executar("UPDATE termos SET token_expira_em = '2000-01-01 00:00:00' WHERE id = ?", (termo_id,))
    assert Navegador(app.test_client()).get(f"/assinar/{token1}").status_code == 410
    admin.post(f"/termos/{termo_id}/enviar")
    token2 = token_do_email(ultimo_email(app, "termo"))
    assert token2 != token1
    assert Navegador(app.test_client()).get(f"/assinar/{token2}").status_code == 200
    r = admin.post(f"/termos/{termo_id}/cancelar", {"motivo": "Dados errados"})
    assert consulta(app, "SELECT status FROM termos WHERE id = ?", (termo_id,))["status"] == "cancelado"
    assert Navegador(app.test_client()).get(f"/assinar/{token2}").status_code == 404
    vinculo = consulta(app, "SELECT vinculo_id FROM termos WHERE id = ?", (termo_id,))["vinculo_id"]
    r = admin.post(f"/termos/emitir/{vinculo}")
    assert r.status_code == 302
    assert consulta(app, "SELECT COUNT(*) AS n FROM termos WHERE vinculo_id = ?", (vinculo,))["n"] == 2
