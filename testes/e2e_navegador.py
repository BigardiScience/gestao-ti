"""Teste de ponta a ponta no navegador (Chrome) com capturas de tela.

Uso (com o sistema rodando e o banco vazio):
    python testes/e2e_navegador.py http://127.0.0.1:8765 /caminho/da/pasta_dados /pasta/das/capturas
Requer: pip install playwright && playwright install chromium (ou Google Chrome instalado).
"""
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE, PASTA_DADOS, SAIDA = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
SAIDA.mkdir(parents=True, exist_ok=True)
RAIZ = Path(__file__).resolve().parent.parent
erros_console = []


def banco(sql, parametros=()):
    conexao = sqlite3.connect(PASTA_DADOS / "celulares.db")
    conexao.row_factory = sqlite3.Row
    try:
        return [dict(l) for l in conexao.execute(sql, parametros).fetchall()]
    finally:
        conexao.close()


def observar(pagina, nome):
    pagina.on("console", lambda m: erros_console.append(f"[{nome}] {m.type}: {m.text}") if m.type in ("error", "warning") else None)
    pagina.on("pageerror", lambda e: erros_console.append(f"[{nome}] pageerror: {e}"))


def foto(pagina, nome, inteira=True):
    pagina.screenshot(path=str(SAIDA / f"{nome}.png"), full_page=inteira)
    print("captura:", nome)


with sync_playwright() as p:
    navegador = p.chromium.launch(executable_path="/usr/bin/google-chrome" if Path("/usr/bin/google-chrome").exists() else None,
                                  args=["--no-proxy-server"])
    contexto = navegador.new_context(viewport={"width": 1440, "height": 900}, locale="pt-BR",
                                     timezone_id="America/Sao_Paulo")
    pagina = contexto.new_page()
    observar(pagina, "admin")

    pagina.goto(BASE + "/")
    assert "/instalacao" in pagina.url
    foto(pagina, "01-instalacao")
    codigo = (PASTA_DADOS / "codigo-instalacao.txt").read_text().strip()
    pagina.fill("#c-codigo", codigo)
    pagina.fill("#c-empresa_nome", "Empresa Exemplo")
    pagina.fill("#c-nome", "Gustavo Martins")
    pagina.fill("#c-email", "ti@empresa.com.br")
    pagina.fill("#c-senha", "Senha-forte-2026")
    pagina.fill("#c-confirmar", "Senha-forte-2026")
    pagina.click("button[type=submit]")
    pagina.wait_for_url("**/unidades")

    ambiente = dict(os.environ, PASTA_DADOS=str(PASTA_DADOS), AGENDADOR_ATIVO="0")
    saida = subprocess.run([sys.executable, "-m", "flask", "--app", "wsgi", "dados-exemplo"], cwd=RAIZ, env=ambiente,
                           capture_output=True, text=True)
    print(saida.stdout, saida.stderr[-2000:])
    assert saida.returncode == 0

    pagina.goto(BASE + "/")
    foto(pagina, "02-painel")
    pagina.goto(BASE + "/aparelhos")
    foto(pagina, "03-aparelhos")
    em_uso = banco("SELECT a.id FROM aparelhos a JOIN vinculos v ON v.aparelho_id = a.id AND v.devolvido_em IS NULL "
                   "JOIN termos t ON t.vinculo_id = v.id AND t.status = 'assinado' LIMIT 1")[0]["id"]
    pagina.goto(f"{BASE}/aparelhos/{em_uso}")
    foto(pagina, "04-aparelho-detalhe")
    transferido = banco("SELECT aparelho_id FROM movimentacoes WHERE tipo = 'transferencia' LIMIT 1")[0]["aparelho_id"]
    pagina.goto(f"{BASE}/aparelhos/{transferido}")
    foto(pagina, "05-aparelho-historico")
    colaborador = banco("SELECT colaborador_id FROM vinculos WHERE devolvido_em IS NULL LIMIT 1")[0]["colaborador_id"]
    pagina.goto(f"{BASE}/colaboradores/{colaborador}")
    foto(pagina, "06-colaborador")
    assinado = banco("SELECT id FROM termos WHERE status = 'assinado' LIMIT 1")[0]["id"]
    pagina.goto(f"{BASE}/termos/{assinado}")
    foto(pagina, "07-termo-assinado")
    gerado = banco("SELECT id FROM termos WHERE status = 'gerado' LIMIT 1")[0]["id"]
    pagina.goto(f"{BASE}/termos/{gerado}")
    foto(pagina, "08-termo-revisao", inteira=False)
    pagina.goto(BASE + "/termos?status=pendente")
    foto(pagina, "09-termos")
    # entrega com os campos de escolha que buscam no servidor
    pagina.goto(BASE + "/entregas/nova")
    pagina.click("#c-colaborador_id")
    pagina.keyboard.type("camila")
    foto(pagina, "10-entrega", inteira=False)
    pagina.keyboard.press("Enter")  # antes da resposta da busca chegar: precisa escolher a Camila, não o 1º da lista antiga
    pagina.wait_for_selector("[data-seletor-texto]:has-text('Camila')")
    assert pagina.input_value("input[name=colaborador_id]"), "o colaborador não foi escolhido"
    pagina.click("#c-aparelho_id")
    pagina.wait_for_selector("#lista-aparelho_id .seletor__opcao")
    pagina.click("#lista-aparelho_id .seletor__opcao >> nth=0")
    assert pagina.input_value("input[name=aparelho_id]"), "o aparelho não foi escolhido"
    pagina.click("button[type=submit]:has-text('Registrar entrega')")
    pagina.wait_for_url("**/termos/*")
    assert "Aguardando envio" in pagina.content()
    pagina.goto(BASE + "/relatorios?tipo=em_uso&previa=1")
    foto(pagina, "11-relatorios")
    with pagina.expect_download() as download:
        pagina.click("button[formaction]")
    download.value.save_as(SAIDA / "relatorio-em-uso.xlsx")
    pagina.goto(BASE + "/configuracoes/integracao")
    foto(pagina, "12-integracao")
    pagina.goto(BASE + "/alertas")
    foto(pagina, "13-pendencias")
    pagina.goto(BASE + "/colaboradores")
    foto(pagina, "14-colaboradores")

    # computadores: os campos do Wi-Fi só aparecem para notebook e os da desativação só para "Desativado"
    unidade = banco("SELECT id FROM unidades ORDER BY id LIMIT 1")[0]["id"]
    pagina.goto(BASE + "/computadores/novo")
    assert not pagina.is_visible("#c-ip_wifi") and not pagina.is_visible("#c-desativacao_motivo")
    pagina.fill("#c-nome", "pcsor10")
    pagina.select_option("#c-unidade_id", str(unidade))
    pagina.click(".segmentado label:has-text('Notebook')")
    assert pagina.is_visible("#c-ip_wifi"), "campos do Wi-Fi deveriam aparecer para notebook"
    pagina.select_option("#c-situacao", "desativado")
    assert pagina.is_visible("#c-desativacao_motivo")
    pagina.select_option("#c-situacao", "em_uso")
    pagina.fill("#c-usuario_nome", "Nicolas Vital")
    pagina.fill("#c-usuario_login", "nicolas.vital")
    pagina.fill("#c-ip", "172.16.161.10")
    pagina.fill("#c-mac", "02-00-5e-10-77-50")
    pagina.fill("#c-ip_wifi", "172.16.161.11")
    pagina.click("button[type=submit]:has-text('Cadastrar computador')")
    pagina.wait_for_url("**/computadores/*")
    assert "02:00:5E:10:77:50" in pagina.content() and "PCSOR10" in pagina.content()
    foto(pagina, "14a-computador")
    pagina.fill("#busca-geral", "172.16.161.11")
    pagina.keyboard.press("Enter")
    pagina.wait_for_url("**/computadores/*")
    pagina.click("button:has-text('Marcar como conferido')")
    pagina.wait_for_load_state("networkidle")
    assert "Conferência registrada" in pagina.content()
    pagina.goto(BASE + "/computadores")
    foto(pagina, "14b-computadores")

    # confirmação em diálogo antes de cancelar um termo
    pendente = banco("SELECT id FROM termos WHERE status = 'enviado' LIMIT 1")[0]["id"]
    pagina.goto(f"{BASE}/termos/{pendente}")
    pagina.click(".cabecalho__acoes .acoes-mais > summary")
    pagina.click("button:has-text('Cancelar termo')")
    pagina.wait_for_selector("#dialogo-confirmar[open]")
    pagina.wait_for_timeout(400)
    foto(pagina, "15-dialogo-cancelar", inteira=False)
    pagina.click("[data-dialogo-cancelar]")

    # ------------------------------------------------------------ colaborador assinando pelo celular
    celular = navegador.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                    has_touch=True, locale="pt-BR", timezone_id="America/Sao_Paulo")
    tela = celular.new_page()
    observar(tela, "celular")
    email = banco("SELECT e.corpo_html, t.id AS termo_id FROM emails e JOIN termos t ON t.id = e.termo_id "
                  "WHERE e.finalidade = 'termo' AND t.status = 'enviado' AND t.token_expira_em > datetime('now') "
                  "ORDER BY e.id DESC LIMIT 1")[0]
    link = re.search(r'href="([^"]+/assinar/[^"]+)"', email["corpo_html"]).group(1).replace("http://127.0.0.1:8765", BASE)
    termo = banco("SELECT c.cpf, c.nome FROM termos t JOIN colaboradores c ON c.id = t.colaborador_id WHERE t.id = ?",
                  (email["termo_id"],))[0]
    tela.goto(link)
    foto(tela, "20-celular-assinar-inicio", inteira=False)
    tela.fill("#c-cpf", termo["cpf"])
    tela.click("text=Enviar código para o meu e-mail")
    codigo_email = banco("SELECT corpo_html FROM emails WHERE finalidade = 'codigo_assinatura' ORDER BY id DESC LIMIT 1")[0]
    codigo = re.search(r">(\d{6})<", codigo_email["corpo_html"]).group(1)
    tela.fill("#c-codigo", codigo)
    tela.locator("canvas").scroll_into_view_if_needed()
    quadro = tela.locator("canvas").bounding_box()
    x0, y0 = quadro["x"] + 30, quadro["y"] + quadro["height"] * 0.6
    tela.mouse.move(x0, y0)
    tela.mouse.down()
    for passo in range(1, 30):
        tela.mouse.move(x0 + passo * 9, y0 - 40 * ((passo % 6) / 6) + (passo % 3) * 6)
    tela.mouse.up()
    tela.fill("#c-nome", termo["nome"])
    tela.check("input[name=aceite]")
    tela.locator(".quadro-assinatura").scroll_into_view_if_needed()
    assert tela.locator("input[name=tracos]").input_value(), "a assinatura desenhada não foi registrada"
    foto(tela, "21-celular-assinatura-desenhada", inteira=False)
    tela.click("button[value=assinar]")
    tela.wait_for_url("**/assinar/concluido")
    foto(tela, "22-celular-concluido")

    tela.goto(BASE + "/portal/entrar")
    foto(tela, "23-celular-portal-entrar", inteira=False)

    painel_cel = celular.new_page()
    observar(painel_cel, "celular-admin")
    painel_cel.goto(BASE + "/entrar")
    painel_cel.fill("#usuario", "ti@empresa.com.br")
    painel_cel.fill("#senha", "Senha-forte-2026")
    painel_cel.click("button[type=submit]")
    painel_cel.wait_for_url(BASE + "/")
    foto(painel_cel, "24-celular-painel")
    painel_cel.click("[data-abrir-menu]")
    painel_cel.wait_for_timeout(400)  # o menu desliza em 0,2 s
    foto(painel_cel, "25-celular-menu", inteira=False)
    painel_cel.goto(f"{BASE}/aparelhos/{em_uso}")
    foto(painel_cel, "26-celular-aparelho")
    painel_cel.goto(BASE + "/aparelhos")
    foto(painel_cel, "27-celular-lista")

    navegador.close()

print("\n".join(erros_console) or "Sem erros no console.")
assert not [e for e in erros_console if "error" in e.lower()], "Há erros no console do navegador"
