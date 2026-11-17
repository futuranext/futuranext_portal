from functools import lru_cache
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.worksheet.datavalidation import DataValidation

TEMPLATE_VERSION = "1.0"
TEMPLATE_FILENAME = f"modelo-nfse-emissao-v{TEMPLATE_VERSION}.xlsx"
CCP_TEMPLATE_FILENAME = "Base_Emissao_Nota_Fiscal.xlsx"
TEMPLATE_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TEMPLATE_LAST_ROW = 1001

COLUMNS = (
    ("referencia", "Sua referência única para o serviço.", 23),
    ("tomador_documento", "CPF ou CNPJ do tomador. Preserve zeros iniciais.", 26),
    ("tomador_nome", "Nome ou razão social do tomador.", 34),
    ("competencia", "Data em que o serviço foi prestado.", 19),
    ("cep", "CEP do endereço do tomador, quando aplicável.", 15),
    ("numero_endereco", "Número do endereço, quando aplicável.", 21),
    ("valor_servico", "Valor do serviço em reais, sem símbolo monetário.", 19),
    ("descricao_servico", "Descrição da prestação do serviço.", 44),
    ("codigo_nbs", "Código NBS, se aplicável à atividade.", 17),
)

CCP_COLUMNS = (
    ("Aluno", "Referência do aluno no cadastro local. Preserve zeros iniciais.", 20),
    ("Nome Aluno", "Nome do aluno.", 32),
    ("Nome Responsável", "Nome do responsável pela prestação.", 34),
    ("Data Vencimento", "Data no Excel ou texto no formato dd/mm/aaaa.", 20),
    ("CPF Responsável", "CPF com 11 dígitos. Preserve zeros iniciais.", 20),
    ("Numero Endereco", "Número do endereço do responsável.", 21),
    ("CEP", "CEP com 8 dígitos. Preserve zeros iniciais.", 15),
    ("Vlr Recebido", "Valor recebido, maior que zero.", 19),
    ("NF", "Deixe em branco. Preenchido pelo fluxo legado.", 20),
    ("Status", "Deixe em branco nas linhas que o fluxo legado deve considerar.", 22),
    ("NBS", "Código NBS, conforme a configuração do serviço.", 17),
)


@lru_cache(maxsize=1)
def emission_template() -> bytes:
    return build_template("Emissão", COLUMNS, 4, 7, "Modelo de emissão NFS-e")


@lru_cache(maxsize=1)
def legacy_ccp_template() -> bytes:
    return build_template("Base", CCP_COLUMNS, 4, 8, "Modelo de emissão para o robô CCP")


def build_template(
    sheet_name: str,
    columns: tuple[tuple[str, str, int], ...],
    date_column: int,
    amount_column: int,
    title: str,
) -> bytes:
    workbook = Workbook()
    instructions = workbook.active
    instructions.title = "Instruções"
    instructions.append((title, f"Versão {TEMPLATE_VERSION}"))
    instructions.append(("Preenchimento", f"Preencha uma linha por serviço na aba {sheet_name}."))
    instructions.append(
        ("Conferência", "Valide os campos fiscais e o enquadramento com o responsável.")
    )
    instructions.append(("Uso", "Modelo de coleta para conferência dos dados de emissão."))
    instructions.append(("Coluna", "Orientação"))
    for name, description, _ in columns:
        instructions.append((name, description))

    instructions.column_dimensions["A"].width = 31
    instructions.column_dimensions["B"].width = 74
    instructions.freeze_panes = "A6"

    sheet = workbook.create_sheet(sheet_name)
    sheet.append(tuple(column[0] for column in columns))
    sheet.freeze_panes = "A2"
    last_col = sheet[1][-1].column_letter
    sheet.auto_filter.ref = f"A1:{last_col}{TEMPLATE_LAST_ROW}"
    for header in (*instructions[1], *instructions[5], *sheet[1]):
        header.font = Font(bold=True, color="FFFFFF")
        header.fill = PatternFill("solid", fgColor="164E63")

    for cell in sheet[1]:
        _, _, width = columns[cell.column - 1]
        sheet.column_dimensions[cell.column_letter].width = width
        if cell.column not in {date_column, amount_column}:
            sheet.column_dimensions[cell.column_letter].number_format = "@"
    for row in range(2, TEMPLATE_LAST_ROW + 1):
        sheet.cell(row=row, column=date_column).number_format = "dd/mm/yyyy"
        sheet.cell(row=row, column=amount_column).number_format = "0.00"

    amount = DataValidation(type="decimal", operator="greaterThan", formula1="0", allow_blank=True)
    amount.showErrorMessage = True
    amount.error = "Informe um valor de serviço maior que zero."
    sheet.add_data_validation(amount)
    amount_letter = sheet.cell(row=1, column=amount_column).column_letter
    amount.add(f"{amount_letter}2:{amount_letter}{TEMPLATE_LAST_ROW}")
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
