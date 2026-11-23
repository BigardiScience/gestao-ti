"""Geração de PDFs: termo de responsabilidade, comprovante de devolução e etiqueta do aparelho."""
import unicodedata
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from flask import current_app
from reportlab.graphics.barcode import code128
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.platypus import (BaseDocTemplate, Flowable, Frame, Image, KeepTogether, PageBreak, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle)

from ..util import fmt_data, fmt_datahora
from .configuracoes import cfg, nome_empresa

TINTA = colors.HexColor("#182230")
TEXTO_2 = colors.HexColor("#475467")
LINHA = colors.HexColor("#D0D5DD")
FUNDO = colors.HexColor("#F2F4F7")
MARCA_DAGUA = colors.HexColor("#E4E7EC")
VERMELHO_CLARO = colors.HexColor("#F4C7C3")
MARGEM = 20 * mm


def seguro(texto) -> str:
    """As fontes padrão do PDF cobrem o alfabeto latino (cp1252). Troca o que estiver fora por equivalentes."""
    texto = str(texto if texto is not None else "")
    try:
        texto.encode("cp1252")
        return texto
    except UnicodeEncodeError:
        saida = []
        for caractere in texto:
            try:
                caractere.encode("cp1252")
                saida.append(caractere)
            except UnicodeEncodeError:
                base = "".join(c for c in unicodedata.normalize("NFKD", caractere) if not unicodedata.combining(c))
                try:
                    base.encode("cp1252")
                    saida.append(base or "?")
                except UnicodeEncodeError:
                    saida.append("?")
        return "".join(saida)


def _txt(texto) -> str:
    return escape(seguro(texto))


def _rico(texto) -> str:
    """Escapa o texto e converte **negrito** em <b>negrito</b>."""
    partes = _txt(texto).split("**")
    return "".join(f"<b>{p}</b>" if i % 2 else p for i, p in enumerate(partes))


def _estilos():
    base = dict(fontName="Helvetica", textColor=TINTA)
    return {
        "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=13.5, leading=17.5,
                                 alignment=TA_CENTER, textColor=TINTA, spaceAfter=2),
        "subtitulo": ParagraphStyle("subtitulo", fontSize=8.5, leading=11, alignment=TA_CENTER, textColor=TEXTO_2,
                                    fontName="Helvetica"),
        "secao": ParagraphStyle("secao", fontName="Helvetica-Bold", fontSize=10, leading=13, spaceBefore=9,
                                spaceAfter=4, textColor=TINTA),
        "corpo": ParagraphStyle("corpo", fontSize=9.6, leading=14, alignment=TA_JUSTIFY, spaceAfter=5.5, **base),
        "rotulo": ParagraphStyle("rotulo", fontSize=8.2, leading=10.5, textColor=TEXTO_2, fontName="Helvetica"),
        "valor": ParagraphStyle("valor", fontSize=9.2, leading=11.5, **base),
        "pequeno": ParagraphStyle("pequeno", fontSize=7.8, leading=10, textColor=TEXTO_2, fontName="Helvetica"),
        "hash": ParagraphStyle("hash", fontName="Courier", fontSize=7.4, leading=9.5, textColor=TINTA),
        "nome": ParagraphStyle("nome", fontName="Helvetica-Bold", fontSize=9.6, leading=12.5, textColor=TINTA),
        "centro": ParagraphStyle("centro", fontSize=9, leading=12, alignment=TA_CENTER, **base),
        "esquerda": ParagraphStyle("esquerda", fontSize=9.6, leading=13.5, alignment=TA_LEFT, **base),
    }


class CanvasNumerado(rl_canvas.Canvas):
    """Canvas que escreve "Página X de Y" depois de saber o total de páginas."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._estados = []

    def showPage(self):
        self._estados.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._estados)
        for estado in self._estados:
            self.__dict__.update(estado)
            self.setFont("Helvetica", 7.5)
            self.setFillColor(TEXTO_2)
            self.drawRightString(A4[0] - MARGEM, 11 * mm, f"Página {self._pageNumber} de {total}")
            super().showPage()
        super().save()


class Assinatura(Flowable):
    """Desenha a assinatura feita na tela (traços) ou a assinatura digitada."""

    def __init__(self, desenho=None, texto=None, largura=72 * mm, altura=22 * mm):
        super().__init__()
        self.desenho, self.texto = desenho, texto
        self.largura, self.altura = largura, altura

    def wrap(self, *_):
        return self.largura, self.altura

    def draw(self):
        tela = self.canv
        tela.setStrokeColor(TINTA)
        tela.setFillColor(TINTA)
        if self.desenho and self.desenho.get("tracos"):
            pontos = [p for t in self.desenho["tracos"] for p in t]
            xs, ys = [p[0] for p in pontos], [p[1] for p in pontos]
            folga = 4
            minx, maxx = min(xs) - folga, max(xs) + folga
            miny, maxy = min(ys) - folga, max(ys) + folga
            escala = min(self.largura / max(1, maxx - minx), self.altura / max(1, maxy - miny))
            tela.setLineWidth(1.15)
            tela.setLineCap(1)
            tela.setLineJoin(1)
            for traco in self.desenho["tracos"]:
                convertidos = [((x - minx) * escala, self.altura - (y - miny) * escala) for x, y in traco]
                if len(convertidos) == 1:
                    tela.circle(convertidos[0][0], convertidos[0][1], 0.7, stroke=0, fill=1)
                    continue
                caminho = tela.beginPath()
                caminho.moveTo(*convertidos[0])
                for ponto in convertidos[1:]:
                    caminho.lineTo(*ponto)
                tela.drawPath(caminho, stroke=1, fill=0)
        elif self.texto:
            tamanho = 22
            texto = seguro(self.texto)
            while tamanho > 10 and tela.stringWidth(texto, "Times-Italic", tamanho) > self.largura:
                tamanho -= 1
            tela.setFont("Times-Italic", tamanho)
            tela.drawString(0, 5, texto)


def _logo():
    arquivo = cfg("logo_arquivo")
    if not arquivo:
        return None
    caminho = Path(current_app.config["PASTA_UPLOADS"]) / arquivo
    if not caminho.exists():
        return None
    try:
        leitor = ImageReader(str(caminho))
        largura, altura = leitor.getSize()
    except Exception:
        return None
    altura_final = 13 * mm
    largura_final = min(55 * mm, altura_final * largura / max(1, altura))
    altura_final = largura_final * altura / max(1, largura)
    imagem = Image(str(caminho), width=largura_final, height=altura_final)
    imagem.hAlign = "LEFT"
    return imagem


def _tabela_campos(campos, estilos, largura_total, largura_rotulo=46 * mm):
    linhas = [[Paragraph(_txt(rotulo), estilos["rotulo"]), Paragraph(_txt(valor), estilos["valor"])]
              for rotulo, valor in campos]
    tabela = Table(linhas, colWidths=[largura_rotulo, largura_total - largura_rotulo], hAlign="LEFT")
    tabela.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), FUNDO),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINHA),
        ("LINEABOVE", (0, 0), (-1, 0), 0.4, LINHA),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3.2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return tabela


def _decoracao(cabecalho_esquerda: str, cabecalho_direita: str, rodape: str, marca_dagua: str | None,
               cor_marca=MARCA_DAGUA):
    def desenhar(tela, documento):
        tela.saveState()
        largura, altura = A4
        if marca_dagua:
            tela.setFillColor(cor_marca)
            tela.setFont("Helvetica-Bold", 64)
            tela.translate(largura / 2, altura / 2)
            tela.rotate(35)
            tela.drawCentredString(0, 0, seguro(marca_dagua))
            tela.rotate(-35)
            tela.translate(-largura / 2, -altura / 2)
        tela.setFont("Helvetica-Bold", 7.8)
        tela.setFillColor(TEXTO_2)
        tela.drawString(MARGEM, altura - 12 * mm, seguro(cabecalho_esquerda)[:90])
        tela.setFont("Helvetica", 7.8)
        tela.drawRightString(largura - MARGEM, altura - 12 * mm, seguro(cabecalho_direita))
        tela.setStrokeColor(LINHA)
        tela.setLineWidth(0.5)
        tela.line(MARGEM, altura - 13.8 * mm, largura - MARGEM, altura - 13.8 * mm)
        tela.line(MARGEM, 15 * mm, largura - MARGEM, 15 * mm)
        tela.setFont("Helvetica", 7.2)
        tela.drawString(MARGEM, 11 * mm, seguro(rodape)[:150])
        tela.restoreState()
    return desenhar


def _documento(buffer, titulo: str, assunto: str):
    doc = BaseDocTemplate(buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM, topMargin=19 * mm,
                          bottomMargin=20 * mm, title=seguro(titulo), author=seguro(nome_empresa()),
                          subject=seguro(assunto), creator="Plataforma de Gestão de Celulares Corporativos")
    return doc


# ------------------------------------------------------------------ termo de responsabilidade

def gerar_termo(conteudo: dict, *, evidencias: dict | None = None, selo: str | None = None, previa: bool = False,
                cancelado: bool = False) -> bytes:
    estilos = _estilos()
    buffer = BytesIO()
    doc = _documento(buffer, f"{conteudo['titulo']} - {conteudo['codigo']}", "Termo de responsabilidade")
    largura = doc.width

    if evidencias:
        rodape = (f"Assinado eletronicamente. Verifique em {evidencias['verificacao']['url']} "
                  f"com o código {evidencias['verificacao']['codigo']}.")
        marca = None
    elif cancelado:
        rodape, marca = "Termo cancelado: documento sem validade.", "CANCELADO"
    else:
        rodape, marca = "Prévia: documento ainda não assinado.", "PRÉVIA" if previa else None
    decoracao = _decoracao(conteudo["empresa"]["nome"], f"Termo nº {conteudo['codigo']}", rodape, marca,
                           VERMELHO_CLARO if cancelado else MARCA_DAGUA)
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width,
                                                                  doc.height, id="corpo")], onPage=decoracao)])

    historia = []
    logo = _logo()
    if logo:
        historia += [logo, Spacer(1, 3 * mm)]
    historia.append(Paragraph(_txt(conteudo["titulo"]), estilos["titulo"]))
    detalhe_empresa = conteudo["empresa"]["nome"]
    if conteudo["empresa"].get("cnpj"):
        detalhe_empresa += f", CNPJ {conteudo['empresa']['cnpj']}"
    historia.append(Paragraph(_txt(f"{detalhe_empresa}. Termo nº {conteudo['codigo']}, emitido em "
                                   f"{fmt_data(conteudo['emitido_em'])}."), estilos["subtitulo"]))
    historia.append(Spacer(1, 4 * mm))

    numero = 0
    for secao in conteudo["secoes"]:
        numero += 1
        historia.append(Paragraph(_txt(f"{numero}. {secao['titulo']}"), estilos["secao"]))
        historia.append(_tabela_campos(secao["campos"], estilos, largura))
    numero += 1
    historia.append(Paragraph(_txt(f"{numero}. Declaração e condições de uso"), estilos["secao"]))
    from .termos import paragrafos
    for paragrafo in paragrafos(conteudo["texto"]):
        historia.append(Paragraph(_rico(paragrafo), estilos["corpo"]))
    if conteudo.get("observacoes"):
        historia.append(Paragraph("<b>Observações da entrega:</b> " + _txt(conteudo["observacoes"]), estilos["corpo"]))
    historia.append(Spacer(1, 2 * mm))
    historia.append(Paragraph(_txt(conteudo["local_data"] + "."), estilos["esquerda"]))

    signatario = conteudo["signatario"]
    bloco = [Spacer(1, 7 * mm)]
    if evidencias:
        assinatura = evidencias["assinatura"]
        if assinatura["tipo"] == "desenho":
            bloco.append(Assinatura(desenho=assinatura["desenho"]))
        else:
            bloco.append(Assinatura(texto=evidencias["signatario"]["nome_digitado"]))
    else:
        bloco.append(Spacer(1, 22 * mm))
    linha = Table([[""]], colWidths=[80 * mm], rowHeights=[1], hAlign="LEFT")
    linha.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, -1), 0.7, TINTA)]))
    bloco += [linha, Paragraph(_txt(signatario["nome"]), estilos["nome"]),
              Paragraph(_txt(f"CPF {signatario['cpf']}"), estilos["pequeno"])]
    if evidencias:
        registro = evidencias["registro"]
        bloco.append(Paragraph(_txt(f"Assinado eletronicamente em {registro['assinado_em_local']} "
                                    f"({registro['fuso_horario']}). Código de verificação: "
                                    f"{evidencias['verificacao']['codigo']}."), estilos["pequeno"]))
    else:
        bloco.append(Paragraph("Assinatura do colaborador", estilos["pequeno"]))
    historia.append(KeepTogether(bloco))

    if evidencias:
        historia += _pagina_evidencias(evidencias, selo, estilos, largura)

    doc.build(historia, canvasmaker=CanvasNumerado)
    return buffer.getvalue()


def _pagina_evidencias(ev: dict, selo: str | None, estilos: dict, largura) -> list:
    historia = [PageBreak(), Paragraph("Registro de assinatura eletrônica", estilos["titulo"]),
                Paragraph(_txt(f"Termo nº {ev['documento']['codigo']}"), estilos["subtitulo"]), Spacer(1, 4 * mm),
                Paragraph("Este registro reúne as evidências de autoria, data e integridade coletadas no momento da "
                          "assinatura. Ele é parte inseparável do termo.", estilos["corpo"])]
    tipo = "Desenhada na tela pelo signatário" if ev["assinatura"]["tipo"] == "desenho" else "Nome digitado pelo signatário"
    reg = ev["registro"]
    campos = [
        ("Documento", f"{ev['documento']['titulo']} (termo nº {ev['documento']['codigo']})"),
        ("Emitido em", fmt_datahora(ev["documento"]["emitido_em"], segundos=True)),
        ("Signatário", ev["signatario"]["nome"]),
        ("CPF", ev["signatario"]["cpf"]),
        ("E-mail corporativo", ev["signatario"]["email"]),
        ("Nome digitado na assinatura", ev["signatario"]["nome_digitado"]),
        ("Autenticação", ev["autenticacao"]["metodo"] + ". CPF conferido com o cadastro."),
        ("Forma da assinatura", tipo),
        ("Aceite", "Declarou ter lido e concordado com o termo e com a assinatura eletrônica."),
        ("Data e hora", f"{reg['assinado_em_local']} ({reg['fuso_horario']}); UTC {reg['assinado_em_utc']}"),
        ("Endereço IP", reg["ip"] or "não identificado"),
        ("Navegador", reg["navegador"] or "não identificado"),
        ("Código de verificação", ev["verificacao"]["codigo"]),
    ]
    historia += [Paragraph("Evidências", estilos["secao"]), _tabela_campos(campos, estilos, largura, 50 * mm)]
    historia.append(Paragraph("Integridade", estilos["secao"]))
    integridade = Table([
        [Paragraph("Hash SHA-256 do conteúdo do termo", estilos["rotulo"]),
         Paragraph(_txt(ev["documento"]["hash_conteudo"]), estilos["hash"])],
        [Paragraph("Selo do registro (HMAC-SHA256)", estilos["rotulo"]), Paragraph(_txt(selo or ""), estilos["hash"])],
    ], colWidths=[50 * mm, largura - 50 * mm], hAlign="LEFT")
    integridade.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, -1), FUNDO), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINHA),
                                     ("LINEABOVE", (0, 0), (-1, 0), 0.4, LINHA), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                     ("TOPPADDING", (0, 0), (-1, -1), 3.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 3.2)]))
    historia.append(integridade)

    historia.append(Paragraph("Trilha de eventos", estilos["secao"]))
    linhas = [[Paragraph("<b>Data e hora</b>", estilos["rotulo"]), Paragraph("<b>Evento</b>", estilos["rotulo"]),
               Paragraph("<b>Responsável</b>", estilos["rotulo"]), Paragraph("<b>IP</b>", estilos["rotulo"])]]
    for item in ev["trilha"]:
        linhas.append([Paragraph(_txt(fmt_datahora(item["quando"], segundos=True)), estilos["valor"]),
                       Paragraph(_txt(item["evento"]), estilos["valor"]),
                       Paragraph(_txt(item.get("por", "")), estilos["valor"]),
                       Paragraph(_txt(item.get("ip", "")), estilos["valor"])])
    trilha = Table(linhas, colWidths=[34 * mm, 58 * mm, largura - 34 * mm - 58 * mm - 30 * mm, 30 * mm],
                   hAlign="LEFT", repeatRows=1)
    trilha.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), FUNDO), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINHA),
                                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 2.8),
                                ("BOTTOMPADDING", (0, 0), (-1, -1), 2.8)]))
    historia.append(trilha)
    historia += [Spacer(1, 5 * mm), Paragraph(_txt(
        f"Para conferir a autenticidade, acesse {ev['verificacao']['url']} e envie este arquivo PDF ou informe o "
        f"código {ev['verificacao']['codigo']}. Qualquer alteração no arquivo muda o seu hash e a verificação "
        "deixa de confirmar o documento."), estilos["corpo"])]
    return historia


# ------------------------------------------------------------------ comprovante de devolução

def gerar_comprovante_devolucao(vinculo: dict, aparelho: dict, colaborador: dict, status_destino: str) -> bytes:
    from ..validacao import formatar_cpf, formatar_telefone
    estilos = _estilos()
    buffer = BytesIO()
    doc = _documento(buffer, "Comprovante de devolução de aparelho celular", "Comprovante de devolução")
    doc.addPageTemplates([PageTemplate(id="pagina", frames=[Frame(doc.leftMargin, doc.bottomMargin, doc.width,
                                                                  doc.height)],
                                       onPage=_decoracao(nome_empresa(), f"Devolução nº {vinculo['id']}",
                                                         "Comprovante de devolução emitido pela plataforma.", None))])
    largura = doc.width
    historia = []
    logo = _logo()
    if logo:
        historia += [logo, Spacer(1, 3 * mm)]
    historia += [Paragraph("Comprovante de devolução de aparelho celular corporativo", estilos["titulo"]),
                 Spacer(1, 5 * mm), Paragraph("1. Colaborador", estilos["secao"]),
                 _tabela_campos([("Nome", colaborador["nome"]), ("CPF", formatar_cpf(colaborador["cpf"])),
                                 ("Cargo", colaborador["cargo"]), ("Departamento", colaborador["departamento"])],
                                estilos, largura),
                 Paragraph("2. Aparelho", estilos["secao"]),
                 _tabela_campos([("Marca e modelo", f"{aparelho['marca']} {aparelho['modelo']}"),
                                 ("Número de série", aparelho["numero_serie"]), ("IMEI", aparelho["imei"]),
                                 ("Linha", formatar_telefone(aparelho["linha"]) if aparelho["linha"] else "Sem linha"),
                                 ("Operadora", aparelho["operadora"] or "—")], estilos, largura),
                 Paragraph("3. Devolução", estilos["secao"]),
                 _tabela_campos([("Entregue em", fmt_datahora(vinculo["entregue_em"])),
                                 ("Devolvido em", fmt_datahora(vinculo["devolvido_em"])),
                                 ("Estado na devolução", vinculo["estado_devolucao"] or "—"),
                                 ("Acessórios devolvidos", vinculo["acessorios_devolucao"] or "Nenhum informado"),
                                 ("Situação do aparelho", status_destino),
                                 ("Recebido por", vinculo.get("recebido_por_nome") or "—"),
                                 ("Observações", vinculo["observacoes_devolucao"] or "—")], estilos, largura),
                 Spacer(1, 4 * mm),
                 Paragraph(_txt("Declaramos que o aparelho acima foi devolvido ao departamento de TI nas condições "
                                "descritas, encerrando a responsabilidade do colaborador sobre ele a partir desta "
                                "data."), estilos["corpo"]),
                 Spacer(1, 16 * mm)]
    assinaturas = Table([["", "", ""], [Paragraph(_txt(colaborador["nome"]), estilos["centro"]), "",
                                        Paragraph(_txt(vinculo.get("recebido_por_nome") or "Departamento de TI"),
                                                  estilos["centro"])],
                         [Paragraph("Colaborador", estilos["pequeno"]), "",
                          Paragraph("Departamento de TI", estilos["pequeno"])]],
                        colWidths=[(largura - 12 * mm) / 2, 12 * mm, (largura - 12 * mm) / 2])
    assinaturas.setStyle(TableStyle([("LINEABOVE", (0, 1), (0, 1), 0.7, TINTA), ("LINEABOVE", (2, 1), (2, 1), 0.7, TINTA),
                                     ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    historia.append(assinaturas)
    doc.build(historia, canvasmaker=CanvasNumerado)
    return buffer.getvalue()


# ------------------------------------------------------------------ etiqueta de patrimônio

def gerar_etiqueta(aparelho: dict) -> bytes:
    """Etiqueta 90 x 50 mm com código de barras do IMEI, para colar na caixa ou na capa do aparelho."""
    from ..validacao import formatar_telefone
    largura, altura = 90 * mm, 50 * mm
    buffer = BytesIO()
    tela = rl_canvas.Canvas(buffer, pagesize=(largura, altura))
    tela.setTitle(seguro(f"Etiqueta {aparelho['marca']} {aparelho['modelo']}"))
    tela.setStrokeColor(TINTA)
    tela.setLineWidth(0.6)
    tela.roundRect(2.5 * mm, 2.5 * mm, largura - 5 * mm, altura - 5 * mm, 2.5 * mm)
    tela.setFillColor(TEXTO_2)
    tela.setFont("Helvetica", 6.8)
    tela.drawString(6 * mm, altura - 8 * mm, seguro(f"{nome_empresa()}  |  Patrimônio de telefonia")[:70])
    tela.setFillColor(TINTA)
    tela.setFont("Helvetica-Bold", 10.5)
    tela.drawString(6 * mm, altura - 13.5 * mm, seguro(f"{aparelho['marca']} {aparelho['modelo']}")[:42])
    codigo = code128.Code128(aparelho["imei"], barHeight=13 * mm, barWidth=0.38 * mm, quiet=False)
    codigo.drawOn(tela, 6 * mm, altura - 30 * mm)
    tela.setFont("Helvetica", 8.5)
    tela.drawString(6 * mm, altura - 34.5 * mm, f"IMEI {aparelho['imei']}")
    tela.setFont("Helvetica", 7)
    tela.setFillColor(TEXTO_2)
    linha = formatar_telefone(aparelho["linha"]) if aparelho.get("linha") else "sem linha"
    tela.drawString(6 * mm, altura - 39.5 * mm, seguro(f"Série {aparelho['numero_serie']}  |  Linha {linha}")[:70])
    tela.drawString(6 * mm, altura - 43.5 * mm, seguro(f"Unidade {aparelho.get('unidade_nome', '')}")[:70])
    tela.showPage()
    tela.save()
    return buffer.getvalue()


def assinatura_svg(desenho: dict | None) -> str:
    """SVG da assinatura desenhada, recortado nos traços (para exibir na tela)."""
    if not desenho or not desenho.get("tracos"):
        return ""
    pontos = [p for t in desenho["tracos"] for p in t]
    xs, ys = [p[0] for p in pontos], [p[1] for p in pontos]
    folga = 6
    minx, miny = min(xs) - folga, min(ys) - folga
    largura, altura = max(xs) - minx + folga, max(ys) - miny + folga
    linhas = []
    for traco in desenho["tracos"]:
        if len(traco) == 1:
            x, y = traco[0]
            linhas.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.6"/>')
        else:
            pontos_txt = " ".join(f"{x:.1f},{y:.1f}" for x, y in traco)
            linhas.append(f'<polyline points="{pontos_txt}"/>')
    return (f'<svg viewBox="{minx:.1f} {miny:.1f} {largura:.1f} {altura:.1f}" preserveAspectRatio="xMinYMid meet" '
            f'role="img" aria-label="Assinatura desenhada" xmlns="http://www.w3.org/2000/svg">{"".join(linhas)}</svg>')
