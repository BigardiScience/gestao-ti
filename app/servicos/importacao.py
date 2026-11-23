"""Importação de aparelhos, colaboradores e computadores a partir de planilhas (.xlsx).

A importação é "tudo ou nada": cada linha é validada dentro de uma transação e, se
qualquer linha tiver erro, nada é gravado — o sistema devolve a lista de erros com o
número da linha para corrigir na planilha.
"""
from datetime import date, datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from ..db import normalizar, obter_db
from . import ErroNegocio, ErroValidacao
from . import aparelhos as svc_aparelhos
from . import colaboradores as svc_colaboradores
from . import unidades as svc_unidades
from . import vinculos as svc_vinculos
from .registro import auditar

MODELOS = {
    "aparelhos": {
        "titulo": "Aparelhos",
        "colunas": [
            ("marca", "Marca", True, "Samsung"),
            ("modelo", "Modelo", True, "Galaxy A54"),
            ("numero_serie", "Número de série", True, "R58T12ABCDE"),
            ("imei", "IMEI", True, "356938035643809"),
            ("imei2", "IMEI 2", False, ""),
            ("linha", "Linha", False, "(11) 98765-4321"),
            ("operadora", "Operadora", False, "Vivo"),
            ("unidade", "Unidade", True, "Matriz"),
            ("data_aquisicao", "Data de aquisição", False, "15/03/2025"),
            ("valor", "Valor", False, "1.899,00"),
            ("estado_conservacao", "Estado de conservação", True, "Bom"),
            ("status", "Status", False, "Disponível"),
            ("acessorios", "Acessórios", False, "Carregador e capa"),
            ("observacoes", "Observações", False, ""),
            ("responsavel", "CPF ou e-mail do responsável", False, "maria.silva@empresa.com.br"),
            ("data_entrega", "Data de entrega", False, "10/01/2026"),
        ],
        "apelidos": {
            "marca": ["marca", "fabricante"],
            "modelo": ["modelo", "aparelho"],
            "numero_serie": ["numero de serie", "no de serie", "n de serie", "serie", "serial", "numero serie",
                             "s/n", "sn"],
            "imei": ["imei", "imei 1", "imei1"],
            "imei2": ["imei 2", "imei2"],
            "linha": ["linha", "numero da linha", "linha telefonica", "telefone", "numero", "chip"],
            "operadora": ["operadora"],
            "unidade": ["unidade", "filial", "unidade/filial"],
            "data_aquisicao": ["data de aquisicao", "aquisicao", "data aquisicao", "data da compra"],
            "valor": ["valor", "valor (r$)", "preco", "valor r$"],
            "estado_conservacao": ["estado de conservacao", "estado", "conservacao"],
            "status": ["status", "situacao"],
            "acessorios": ["acessorios"],
            "observacoes": ["observacoes", "observacao", "obs"],
            "responsavel": ["cpf ou e-mail do responsavel", "responsavel", "cpf do responsavel",
                            "e-mail do responsavel", "email do responsavel", "usuario"],
            "data_entrega": ["data de entrega", "entregue em", "data entrega"],
        },
    },
    "colaboradores": {
        "titulo": "Colaboradores",
        "colunas": [
            ("nome", "Nome completo", True, "Maria da Silva"),
            ("cpf", "CPF", True, "123.456.789-09"),
            ("email", "E-mail corporativo", True, "maria.silva@empresa.com.br"),
            ("matricula", "Matrícula", False, "1234"),
            ("cargo", "Cargo", True, "Analista financeiro"),
            ("departamento", "Departamento", True, "Financeiro"),
            ("unidade", "Unidade", True, "Matriz"),
            ("telefone", "Telefone corporativo", False, ""),
        ],
        "apelidos": {
            "nome": ["nome completo", "nome", "colaborador", "funcionario"],
            "cpf": ["cpf"],
            "email": ["e-mail corporativo", "email corporativo", "e-mail", "email"],
            "matricula": ["matricula", "registro", "re"],
            "cargo": ["cargo", "funcao", "cargo/funcao"],
            "departamento": ["departamento", "setor", "area"],
            "unidade": ["unidade", "filial"],
            "telefone": ["telefone corporativo", "telefone", "celular", "ramal"],
        },
    },
}

MODELOS["computadores"] = {
    "titulo": "Computadores",
    "colunas": [
        ("nome", "Nome da máquina", True, "PCSOR10"),
        ("tipo", "Tipo", True, "Desktop"),
        ("situacao", "Situação", False, "Em uso"),
        ("usuario_nome", "Nome do usuário", False, "Nicolas Vital"),
        ("usuario_login", "Login de rede", False, "nicolas.vital"),
        ("setor", "Setor", False, "Fiscal"),
        ("usuario_situacao", "Situação do usuário", False, "Ativo"),
        ("sistema", "Sistema operacional", False, "Windows 11 Pro"),
        ("processador", "Processador", False, "Intel Core i3-13100"),
        ("memoria_gb", "Memória (GB)", False, "8"),
        ("ip", "IP", False, "172.16.161.10"),
        ("mac", "MAC", False, "02:00:5E:10:77:50"),
        ("ip_wifi", "IP Wi-Fi", False, ""),
        ("mac_wifi", "MAC Wi-Fi", False, ""),
        ("sep", "SEP", False, "OK"),
        ("remoto_ferramenta", "Acesso remoto", False, "AnyDesk"),
        ("remoto_id", "ID de acesso remoto", False, "1 160 774 978"),
        ("nobreak", "Nobreak", False, "Não"),
        ("monitor", "Monitor", False, '17"'),
        ("perifericos", "Periféricos", False, "Webcam, Headset"),
        ("softwares", "Softwares específicos", False, ""),
        ("marca", "Marca", False, ""),
        ("modelo", "Modelo", False, ""),
        ("numero_serie", "Nº de série", False, ""),
        ("patrimonio", "Patrimônio", False, ""),
        ("observacoes", "Observações", False, ""),
    ],
    "apelidos": {
        "nome": ["nome da maquina", "maquina", "computador", "hostname", "nome do computador", "nome"],
        "tipo": ["tipo"],
        "situacao": ["situacao", "status", "situacao da maquina"],
        "usuario_nome": ["nome do usuario", "colaborador", "responsavel"],
        "usuario_login": ["login de rede", "login", "usuario", "usuarios", "usuario de rede"],
        "setor": ["setor", "departamento", "area"],
        "usuario_situacao": ["situacao do usuario"],
        "sistema": ["sistema operacional", "so", "sistema"],
        "processador": ["processador", "cpu"],
        "memoria_gb": ["memoria (gb)", "memoria", "memoria gb", "memoria mb", "ram"],
        "ip": ["ip", "ip cabo", "endereco ip"],
        "mac": ["mac", "mac cabo", "endereco mac"],
        "ip_wifi": ["ip wi-fi", "ip wifi"],
        "mac_wifi": ["mac wi-fi", "mac wifi"],
        "sep": ["sep", "antivirus"],
        "remoto_ferramenta": ["acesso remoto", "ferramenta de acesso remoto"],
        "remoto_id": ["id de acesso remoto", "id acesso remoto", "id remoto"],
        "anydesk": ["anydesk", "id anydesk"],
        "nobreak": ["nobreak"],
        "monitor": ["monitor", "monitores"],
        "perifericos": ["perifericos", "acessorios", "utilitarios"],
        "softwares": ["softwares especificos", "softwaresespecificos", "softwares", "software"],
        "marca": ["marca", "fabricante"],
        "modelo": ["modelo"],
        "numero_serie": ["no de serie", "numero de serie", "n de serie", "serie", "serial", "serialnumber",
                         "service tag"],
        "patrimonio": ["patrimonio", "no patrimonio"],
        "observacoes": ["observacoes", "observacao", "obs"],
    },
    "listas": {
        "tipo": (["Desktop", "Notebook"], "Escolha Desktop ou Notebook."),
        "situacao": (["Em uso", "Reserva", "Manutenção"], "Escolha Em uso, Reserva ou Manutenção."),
        "usuario_situacao": (["Ativo", "Férias", "Desligado"], "Escolha Ativo, Férias ou Desligado."),
        "sep": (["OK", "Pendente", "Não instalado"], "Escolha OK, Pendente ou Não instalado."),
        "remoto_ferramenta": (["AnyDesk", "RustDesk", "TeamViewer", "Outra"], "Escolha uma ferramenta da lista."),
        "nobreak": (["Sim", "Não"], "Escolha Sim ou Não."),
    },
    "texto": ("ip", "ip_wifi", "mac", "mac_wifi", "remoto_id", "patrimonio", "numero_serie"),
    "notas": {
        "nome": "Como aparece no Windows (hostname). Não pode repetir.",
        "tipo": "Desktop ou Notebook.",
        "situacao": "Em uso, Reserva ou Manutenção. Em branco: Em uso se tiver usuário, senão Reserva.",
        "usuario_situacao": "Ativo, Férias ou Desligado. Em branco: Ativo.",
        "memoria_gb": "Só o número, em GB.",
        "ip": "IP fixo da máquina. Não pode repetir e deve ser da faixa de IP da unidade (se cadastrada).",
        "mac": "Qualquer formato (02:00:5E..., 02-00-5E...). Não pode repetir.",
        "ip_wifi": "Só para notebook.",
        "mac_wifi": "Só para notebook.",
        "remoto_id": "Nunca coloque senhas na planilha.",
        "perifericos": "Webcam e Headset são reconhecidos; o resto vai para Outros periféricos.",
    },
}

STATUS_PLANILHA = {
    "disponivel": "disponivel", "em utilizacao": "em_uso", "em uso": "em_uso", "em manutencao": "manutencao",
    "manutencao": "manutencao", "devolvido": "devolvido",
}


# ------------------------------------------------------------------ modelo para download

def gerar_modelo(tipo: str) -> bytes:
    modelo = MODELOS[tipo]
    livro = Workbook()
    aba = livro.active
    aba.title = modelo["titulo"]
    aba.append([rotulo + (" *" if obrigatorio else "") for _, rotulo, obrigatorio, _ in modelo["colunas"]])
    for indice, celula in enumerate(aba[1], start=1):
        celula.font = Font(bold=True, color="FFFFFF")
        celula.fill = PatternFill("solid", fgColor="182230")
        celula.alignment = Alignment(vertical="center", wrap_text=True)
        aba.column_dimensions[get_column_letter(indice)].width = max(14, len(celula.value) + 4)
    aba.row_dimensions[1].height = 30
    aba.freeze_panes = "A2"
    como_texto = modelo.get("texto", ("imei", "imei2", "cpf", "linha", "numero_serie", "telefone", "matricula"))
    for indice, (campo, *_resto) in enumerate(modelo["colunas"], start=1):
        letra = get_column_letter(indice)
        if campo in como_texto:
            for linha in range(2, 1002):
                aba[f"{letra}{linha}"].number_format = "@"

    listas_campos = modelo.get("listas") or {
        "unidade": ([u["nome"] for u in svc_unidades.opcoes()] or ["Matriz"], "Escolha uma unidade cadastrada."),
        "estado_conservacao": (svc_aparelhos.ESTADOS, "Escolha um estado da lista."),
        "status": (["Disponível", "Em utilização", "Em manutenção", "Devolvido"], "Escolha um status da lista."),
    }
    listas = livro.create_sheet("Listas")
    listas.sheet_state = "hidden"
    campos = [c[0] for c in modelo["colunas"]]
    for numero_lista, (campo, (itens, mensagem)) in enumerate(listas_campos.items(), start=1):
        coluna_lista = get_column_letter(numero_lista)
        for numero, item in enumerate(itens, start=1):
            listas[f"{coluna_lista}{numero}"] = item
        if campo not in campos:
            continue
        letra = get_column_letter(campos.index(campo) + 1)
        regra = DataValidation(type="list", formula1=f"=Listas!${coluna_lista}$1:${coluna_lista}${len(itens)}",
                               allow_blank=True, showErrorMessage=campo != "unidade")
        regra.error = mensagem
        regra.errorTitle = "Valor não aceito"
        aba.add_data_validation(regra)
        regra.add(f"{letra}2:{letra}1001")

    instrucoes = livro.create_sheet("Instruções")
    instrucoes.column_dimensions["A"].width = 30
    instrucoes.column_dimensions["B"].width = 16
    instrucoes.column_dimensions["C"].width = 80
    instrucoes.append(["Como preencher"])
    instrucoes["A1"].font = Font(bold=True, size=13)
    instrucoes.append([f"Preencha a aba “{modelo['titulo']}” a partir da linha 2. Campos com * são obrigatórios."])
    if tipo == "computadores":
        instrucoes.append(["A unidade é escolhida na tela de importação: use uma planilha por unidade."])
    instrucoes.append([])
    instrucoes.append(["Coluna", "Obrigatória", "Exemplo e observações"])
    for celula in instrucoes[4]:
        celula.font = Font(bold=True)
    notas = {
        "imei": "15 dígitos. Disque *#06# no aparelho para ver o IMEI.",
        "linha": "Com DDD. Deixe em branco se o aparelho estiver sem chip.",
        "operadora": "Obrigatória quando houver linha.",
        "unidade": "Nome da unidade como está cadastrado no sistema.",
        "estado_conservacao": "Novo, Ótimo, Bom, Regular ou Danificado.",
        "status": "Disponível, Em manutenção ou Devolvido. Se informar o responsável, o aparelho entra como "
                  "Em utilização.",
        "responsavel": "Para aparelhos que já estão com alguém: CPF ou e-mail de um colaborador já cadastrado. "
                       "Importe os colaboradores primeiro.",
        "data_entrega": "Data em que o colaborador recebeu o aparelho (se não informar, usa a data de hoje).",
        "cpf": "Com ou sem pontuação.",
        "email": "O termo de responsabilidade é enviado para este e-mail.",
        "valor": "Valor de referência do aparelho, em reais.",
    }
    if modelo.get("notas"):
        notas = modelo["notas"]
    for campo, rotulo, obrigatorio, exemplo in modelo["colunas"]:
        texto = exemplo
        if campo in notas:
            texto = f"{exemplo}  —  {notas[campo]}" if exemplo else notas[campo]
        instrucoes.append([rotulo, "Sim" if obrigatorio else "Não", texto])
        instrucoes.cell(row=instrucoes.max_row, column=3).alignment = Alignment(wrap_text=True)
    buffer = BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


# ------------------------------------------------------------------ leitura

def _texto_celula(valor, campo: str) -> str:
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M") if campo == "data_entrega" and (valor.hour or valor.minute) \
            else valor.strftime("%d/%m/%Y")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    if isinstance(valor, float):
        if campo == "valor":
            return f"{valor:.2f}"
        if valor.is_integer():
            valor = int(valor)
    if isinstance(valor, int):
        texto = str(valor)
        if campo == "cpf":
            texto = texto.zfill(11)
        return texto
    return str(valor).strip()


def _mapear_cabecalho(linha, apelidos: dict) -> dict:
    mapa = {}
    for indice, valor in enumerate(linha):
        if valor is None:
            continue
        nome = normalizar(str(valor)).replace("*", "").strip()
        for campo, opcoes in apelidos.items():
            if nome in opcoes and campo not in mapa.values():
                mapa[indice] = campo
                break
    return mapa


def ler_planilha(tipo: str, conteudo: bytes) -> tuple[list[tuple[int, dict]], list[str]]:
    """Devolve ([(número da linha, campos)], erros de estrutura)."""
    try:
        livro = load_workbook(BytesIO(conteudo), read_only=True, data_only=True)
    except Exception:
        raise ErroNegocio("Não foi possível ler o arquivo. Envie uma planilha do Excel (.xlsx).") from None
    modelo = MODELOS[tipo]
    nomes = [n for n in livro.sheetnames if normalizar(n) not in ("instrucoes", "listas", "sobre este relatorio")]
    preferida = next((n for n in nomes if normalizar(n) == normalizar(modelo["titulo"])), nomes[0] if nomes else None)
    if not preferida:
        raise ErroNegocio("A planilha está vazia.")
    linhas = list(livro[preferida].iter_rows(values_only=True))
    livro.close()
    mapa, inicio = {}, 0
    for numero, linha in enumerate(linhas[:15]):
        candidato = _mapear_cabecalho(linha, modelo["apelidos"])
        if len(candidato) >= 2:
            mapa, inicio = candidato, numero + 1
            break
    if not mapa:
        raise ErroNegocio("Não encontramos o cabeçalho da planilha. Use o modelo disponível nesta página.")
    obrigatorios = [rotulo for campo, rotulo, obrigatorio, _ in modelo["colunas"] if obrigatorio
                    and campo not in mapa.values()]
    if obrigatorios:
        return [], ["Faltam colunas obrigatórias: " + ", ".join(obrigatorios) + "."]
    registros = []
    for deslocamento, linha in enumerate(linhas[inicio:], start=inicio + 1):
        campos = {campo: _texto_celula(linha[i] if i < len(linha) else None, campo) for i, campo in mapa.items()}
        if any(campos.values()):
            registros.append((deslocamento, campos))
    if not registros:
        return [], ["A planilha não tem linhas preenchidas abaixo do cabeçalho."]
    if len(registros) > 5000:
        return [], ["Importe no máximo 5.000 linhas por vez."]
    return registros, []


# ------------------------------------------------------------------ processamento

def processar(tipo: str, conteudo: bytes, gravar: bool, criar_unidades: bool = False) -> dict:
    registros, erros_estrutura = ler_planilha(tipo, conteudo)
    resultado = {"linhas": len(registros), "erros": [], "estrutura": erros_estrutura, "gravado": False,
                 "unidades_criadas": [], "entregas": 0}
    if erros_estrutura:
        return resultado
    rotulos = {campo: rotulo for campo, rotulo, *_ in MODELOS[tipo]["colunas"]}
    rotulos.update({"unidade_id": "Unidade", "colaborador_id": "Responsável", "entregue_em": "Data de entrega",
                    "estado": "Estado de conservação", "aparelho_id": "Aparelho"})
    conexao = obter_db()
    conexao.execute("BEGIN IMMEDIATE")
    try:
        for numero, campos in registros:
            conexao.execute("SAVEPOINT linha")
            try:
                entregou = _importar_linha(tipo, campos, criar_unidades, resultado)
                resultado["entregas"] += entregou
                conexao.execute("RELEASE linha")
            except ErroValidacao as erro:
                conexao.execute("ROLLBACK TO linha")
                conexao.execute("RELEASE linha")
                for campo, mensagem in erro.erros.items():
                    resultado["erros"].append({"linha": numero, "coluna": rotulos.get(campo, campo), "mensagem": mensagem})
            except ErroNegocio as erro:
                conexao.execute("ROLLBACK TO linha")
                conexao.execute("RELEASE linha")
                resultado["erros"].append({"linha": numero, "coluna": "", "mensagem": str(erro)})
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
        auditar("importacao", tipo, None, {"linhas": len(registros), "entregas": resultado["entregas"],
                                           "unidades_criadas": resultado["unidades_criadas"]})
    return resultado


def _unidade(campos: dict, criar: bool, resultado: dict):
    nome = campos.get("unidade", "")
    if not nome:
        raise ErroValidacao({"unidade": "Informe a unidade."})
    unidade = svc_unidades.localizar_por_nome(nome)
    if unidade:
        return unidade["id"]
    if not criar:
        raise ErroValidacao({"unidade": f"A unidade “{nome}” não está cadastrada. Cadastre-a ou marque a opção "
                                        "de criar unidades automaticamente."})
    unidade_id = svc_unidades.criar_rapida(nome)
    resultado["unidades_criadas"].append(nome)
    return unidade_id


def _importar_linha(tipo: str, campos: dict, criar_unidades: bool, resultado: dict) -> int:
    form = dict(campos)
    form["unidade_id"] = _unidade(campos, criar_unidades, resultado)
    if tipo == "colaboradores":
        svc_colaboradores.criar(form, origem="importacao")
        return 0

    status = STATUS_PLANILHA.get(normalizar(campos.get("status")) or "disponivel")
    if campos.get("status") and not status:
        raise ErroValidacao({"status": f"Status “{campos['status']}” não aceito. Use Disponível, Em manutenção "
                                       "ou Devolvido."})
    responsavel = campos.get("responsavel")
    if status == "em_uso" and not responsavel:
        raise ErroValidacao({"responsavel": "Para “Em utilização”, informe o CPF ou o e-mail do responsável."})
    form["status"] = "disponivel" if responsavel or status == "em_uso" else status
    colaborador = None
    if responsavel:
        colaborador = svc_colaboradores.localizar(responsavel)
        if not colaborador:
            raise ErroValidacao({"responsavel": f"Colaborador “{responsavel}” não encontrado. Importe os "
                                                "colaboradores antes dos aparelhos."})
    aparelho_id = svc_aparelhos.criar(form, origem="importacao")
    if colaborador:
        svc_vinculos.entregar({
            "aparelho_id": aparelho_id, "colaborador_id": colaborador["id"],
            "entregue_em": campos.get("data_entrega") or "", "estado": form.get("estado_conservacao"),
            "acessorios": campos.get("acessorios") or "", "gerar_termo": "0",
            "observacoes": "Entrega registrada pela importação de planilha.",
        })
        return 1
    return 0
