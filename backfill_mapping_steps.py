"""Acrescenta as etapas nomeadas da análise a um relatório gravado antes de elas existirem.

Os relatórios produzidos antes de ``mapping_steps.py`` trazem a evidência, mas
não os nomes: registram o catálogo que casou, a prova aritmética, a pontuação
do cabeçalho e se o resolvedor de IA rodou, mas nunca dizem qual delas decidiu.
Esta ferramenta reconstrói ``supported_by`` / ``decided_by`` / ``vetoed_by`` a
partir do que já está gravado, sem rodar o mapeador de novo.

Isso importa por mais do que o custo. Rodar de novo não é inofensivo: o
resolvedor não é determinístico, então uma execução nova mexe nas linhas em
*revisão* que um analista pode estar no meio de conferir, e, em sap_procosa,
destruiria de uma vez os 26 pareceres de revisor já registrados.

Duas das sete etapas nem sempre podem ser recuperadas. ``profile`` e
``identifier`` se fundem numa única ``data_confidence`` gravada; então, quando
essa pontuação passa do teto do perfil genérico sem catálogo nem prova que a
explique, alguma etapa objetiva rodou e o relatório não diz qual. Essas linhas
são marcadas como ``unrecorded`` (não registrada) em vez de adivinhadas: um
rótulo de procedência errado é pior do que uma lacuna assumida, porque a razão
de ser destas colunas é poder confiar no que a ferramenta diz que fez.

A aritmética é recalculada, não lida: os trios fiscais (base, alíquota, valor)
são descobertos nas próprias colunas do cliente, então a prova pode ser refeita
offline e sem custo. É isso que permite que sap_procosa, cujo relatório é
anterior à própria coluna ``arithmetic_proof``, ainda tenha o apoio aritmético
nomeado.

A ferramenta só se aplica a relatórios legados em inglês, anteriores às colunas
de etapas, e grava valores legados (``unrecorded``, ``catalog (agrees)``),
porque a saída continua sendo um relatório legado. Ela recusa, sem gravar
nada, um relatório no esquema em português e um que já tenha as colunas de
etapas. A ordem é preencher retroativamente primeiro e só depois migrar o
relatório para o esquema em português (``migrate_reports.py``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl

import report_schema
from cli_ptbr import PortugueseArgumentParser
from invoice_mapper import (
    SheetTable,
    client_column_is_vacuous,
    discover_client_tax_trios,
    semantic_header_conflict,
    trio_corroboration,
    _trio_proof_text,
)
from mapping_steps import deciding_step, format_steps

#: Uma etapa objetiva rodou e o relatório é antigo demais para dizer qual.
#: Valor legado, gravado em inglês como nos relatórios que esta ferramenta
#: completa; a migração o converte depois para ``nao_registrado``.
UNRECORDED = "unrecorded"

#: Acima disto, há algo além da semelhança genérica de formato em jogo: é o
#: teto que o invoice_mapper impõe a generic_profile_score.
GENERIC_PROFILE_CEILING = 0.84

#: Pontuação que a evidência de catálogo precisa atingir para contar como
#: decisiva, e não só como concordância. A intenção é aplicar a mesma barra
#: que invoice_mapper.catalog_evidence_is_distinctive aplica numa execução
#: nova; a confiança gravada já traz o desconto de especificidade que deixa
#: abaixo dela os domínios sujeitos a colisão, como o CST. Na prática as duas
#: regras não coincidem (docs/KNOWN_ISSUES.md, item 2.3).
DISTINCTIVE_CATALOG_SCORE = 0.90

#: Colunas de etapas, com os nomes legados em inglês, na ordem em que são inseridas.
NEW_COLUMNS = ("decided_by", "supported_by", "vetoed_by")


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _refuse_unless_legacy(book: openpyxl.Workbook, report_path: Path) -> None:
    """Encerra com ``SystemExit``, sem gravar nada, se o relatório não está no esquema legado.

    A referência é o cabeçalho da primeira aba, a mesma que o ``apply`` lê pelo
    índice 0. Sem esta checagem, um relatório no esquema em português, que não
    tem nenhuma das colunas legadas ``decided_by`` / ``supported_by`` /
    ``vetoed_by``, passaria pela recusa de relatório já preenchido e ganharia
    três colunas legadas vazias, gravadas no próprio arquivo, com saída 0.

    Também recusa um relatório de esquema misto ou não identificado, inclusive
    pelos nomes das abas: um cabeçalho legado ao lado de uma aba "Metadados da
    Execução" faria ``_header_step`` não achar "Run Metadata" e rotular tudo
    como ``lexical`` sem aviso.
    """
    header = [cell.value for cell in book.worksheets[0][1]]
    try:
        schema = report_schema.detect_schema(header)
        if schema == report_schema.LEGACY_EN:
            for kind in ("mappings", "run_metadata"):
                report_schema.find_sheet(book.sheetnames, kind, schema)
    except ValueError as error:
        raise SystemExit(f"{report_path.name}: {error} Nada foi gravado.") from None
    if schema == report_schema.PT_BR:
        raise SystemExit(
            f"{report_path.name} está no esquema em português; o preenchimento "
            "retroativo só se aplica a relatórios legados em inglês, anteriores às "
            "colunas de etapas. Nada foi gravado."
        )


def _header_step(book: openpyxl.Workbook) -> str:
    """Qual comparação de cabeçalhos (léxica ou semântica) gerou o relatório, segundo os metadados da execução."""
    # Aba, chave e sentinela com os nomes legados em inglês. O mapeador grava
    # "offline", e não "disabled", numa execução sem embeddings, então um
    # relatório offline sai rotulado como semantic (docs/KNOWN_ISSUES.md, item
    # 3.17). Mantido de propósito: corrigir mudaria a saída.
    if "Run Metadata" not in book.sheetnames:
        return "lexical"
    for row in book["Run Metadata"].values:
        if row and str(row[0]) == "embedding_model":
            return "lexical" if str(row[1]).strip().lower() == "disabled" else "semantic"
    return "lexical"


def backfill(report_path: Path, client_path: Path, *, dry_run: bool = False) -> dict[str, int]:
    """Preenche as colunas de etapas de um relatório legado e devolve a contagem.

    Grava no próprio arquivo, a menos que ``dry_run`` seja verdadeiro. Antes de
    ler a exportação do cliente ou de alterar qualquer célula, recusa com
    ``SystemExit`` um relatório no esquema em português (ou de esquema misto)
    e um relatório legado que já tenha as colunas de etapas.
    """
    book = openpyxl.load_workbook(report_path)
    _refuse_unless_legacy(book, report_path)
    sheet = book["Mappings"]
    header = [cell.value for cell in sheet[1]]
    if any(name in header for name in NEW_COLUMNS):
        raise SystemExit(f"{report_path.name} já tem as colunas de etapas. Nada foi gravado.")

    client = SheetTable.from_excel(client_path, 0, 0)
    trios = discover_client_tax_trios(client)
    header_step = _header_step(book)
    index = {name: position for position, name in enumerate(header) if name}

    # Segue o layout de um relatório novo, para que arquivos antigos e novos
    # sejam lidos do mesmo jeito. Na prática o layout resultante é outro
    # (docs/KNOWN_ISSUES.md, item 5.1).
    anchor = next(
        (index[name] + 1 for name in ("arithmetic_proof", "reviewer_status") if name in index),
        len(header) + 1,
    )
    sheet.insert_cols(anchor, amount=len(NEW_COLUMNS))
    for offset, name in enumerate(NEW_COLUMNS):
        sheet.cell(1, anchor + offset).value = name

    # ``index`` não é recalculado depois da inserção: todo campo à direita da
    # âncora passa a ser lido três células à esquerda de onde está, e por isso
    # o resolvedor não é creditado quando a âncora é arithmetic_proof
    # (docs/KNOWN_ISSUES.md, item 1.1). Mantido de propósito: corrigir mudaria
    # a saída.
    def stored(row: int, name: str) -> object:
        return sheet.cell(row, index[name] + 1).value if name in index else None

    tally = {"rows": 0, UNRECORDED: 0, "arithmetic": 0, "vetoed": 0}
    for excel_row in range(2, sheet.max_row + 1):
        model_column = stored(excel_row, "model_column")
        if model_column in (None, ""):
            continue
        raw_client = stored(excel_row, "client_column_number")
        if raw_client in (None, ""):
            continue
        client_index = int(_number(raw_client)) - 1
        if client_index < 0 or client_index >= len(client.columns):
            continue
        tally["rows"] += 1

        steps: list[str] = []
        #: Evidência que concorda, mas é fraca demais para decidir.
        corroborating: list[str] = []
        if _number(stored(excel_row, "llm_final_confidence")) > 0:
            steps.append("resolver")
        if _number(stored(excel_row, "name_confidence")) > 0:
            steps.append(header_step)
        # Segue o caminho de uma execução nova, que pontua a evidência de
        # catálogo com catalog_evidence_is_distinctive e zera tudo o que não
        # passa nessa barra. Um CST de dois dígitos cobre a tabela inteira e
        # ainda assim diz pouco, porque os mesmos códigos aparecem no PIS, na
        # COFINS e no IPI; é isso que o desconto de especificidade já embute na
        # confiança gravada. Creditar a cobertura bruta aqui faria um relatório
        # preenchido retroativamente discordar de um gerado agora sobre a mesma
        # evidência.
        catalog_name = stored(excel_row, "catalog_name")
        catalog_score = _number(stored(excel_row, "catalog_confidence"))
        # O campo antigo 'method' registra o ramo que o mapeador de fato
        # seguiu, o que é evidência melhor do que deduzi-lo de novo. Quando ele
        # diz catalog, o catálogo decidiu e nenhum limiar nosso deve
        # contrariá-lo. Caso contrário, o catálogo é só uma concordância
        # incidental e precisa passar na mesma barra de distinção que uma
        # execução nova aplica: um CST de dois dígitos cobre a tabela inteira e
        # ainda assim diz pouco, porque os mesmos códigos aparecem no PIS, na
        # COFINS e no IPI.
        if catalog_name not in (None, ""):
            if str(stored(excel_row, "method")).strip() == "catalog" or (
                catalog_score >= DISTINCTIVE_CATALOG_SCORE
            ):
                steps.append("catalog")
            elif catalog_score > 0:
                corroborating.append("catalog")

        # Recalculada, não lida: os trios vêm das próprias colunas do cliente,
        # então isto funciona mesmo quando o relatório não tem a coluna
        # arithmetic_proof. A frase da prova pode estar em inglês ou em
        # português; proof_is_decisive reconhece o marcador de prova não
        # decisiva nas duas línguas.
        proof = _trio_proof_text(trio_corroboration(trios, str(model_column), client_index))
        if report_schema.proof_is_decisive(proof):
            steps.append("arithmetic")
            tally["arithmetic"] += 1

        # Alguma evidência objetiva superou a semelhança genérica de formato.
        # Se a pontuação do catálogo explica isso exatamente, essa é a
        # explicação e não há nada sem registro: os dois números são iguais
        # porque foi o catálogo que produziu a pontuação.
        data_score = _number(stored(excel_row, "data_confidence"))
        explained_by_catalog = catalog_score > 0 and abs(data_score - catalog_score) < 1e-6
        objective_ran = data_score > GENERIC_PROFILE_CEILING
        if objective_ran and not explained_by_catalog and "catalog" not in steps and "arithmetic" not in steps:
            steps.append(UNRECORDED)
            tally[UNRECORDED] += 1
        elif explained_by_catalog and "catalog" not in steps:
            steps.append("catalog")
            if "catalog" in corroborating:
                corroborating.remove("catalog")

        column = client.columns[client_index]
        veto = ""
        if str(stored(excel_row, "status")).strip() != "accepted":
            if client_column_is_vacuous(client, column):
                veto = "vacuity"
            elif semantic_header_conflict(str(model_column), column.name) is not None:
                veto = "family_conflict"
        if veto:
            tally["vetoed"] += 1

        ranked = [step for step in steps if step != UNRECORDED]
        decided = deciding_step(ranked)
        if UNRECORDED in steps and decided in ("", header_step, "resolver"):
            # Alguma evidência objetiva superou o casamento de cabeçalho; não
            # dá para dizer qual.
            decided = UNRECORDED
        rendered = format_steps(ranked)
        for weak in corroborating:
            rendered = f"{rendered} + {weak} (agrees)" if rendered else f"{weak} (agrees)"
        if UNRECORDED in steps:
            rendered = f"{rendered} + {UNRECORDED}" if rendered else UNRECORDED

        sheet.cell(excel_row, anchor).value = decided
        sheet.cell(excel_row, anchor + 1).value = rendered
        sheet.cell(excel_row, anchor + 2).value = veto

    if not dry_run:
        book.save(report_path)
    book.close()
    return tally


def main(argv: list[str] | None = None) -> int:
    # A docstring do módulo é a ajuda; o formatador cru mantém os parágrafos.
    parser = PortugueseArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "report",
        metavar="RELATORIO",
        help="relatório de mapeamento legado, em inglês, a completar no próprio arquivo",
    )
    parser.add_argument(
        "client",
        metavar="EXPORTACAO",
        help="a exportação do cliente que gerou o relatório",
    )
    parser.add_argument("--dry-run", action="store_true", help="mostra a contagem sem gravar nada")
    args = parser.parse_args(argv)

    tally = backfill(Path(args.report), Path(args.client), dry_run=args.dry_run)
    print(
        f"{args.report}: {tally['rows']} linhas com proposta, "
        f"{tally['arithmetic']} com prova aritmética, "
        f"{tally[UNRECORDED]} com etapa não registrada ({UNRECORDED}), "
        f"{tally['vetoed']} com veto"
        + ("  (simulação, nada foi gravado)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
