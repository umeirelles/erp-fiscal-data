"""Verifica que revisões de texto preservam os inputs enviados aos modelos.

O teste captura as chamadas com um fake client da OpenAI e as compara com
``tests/fixtures/model_input_golden.json``, gerado antes da tradução, no
commit ``2ae4b6d``. A captura cobre:

* prompt e schema de saída do resolver;
* inputs de embeddings, incluindo cabeçalhos vazios e repetidos;
* argumentos de ``responses.parse``, com os três tipos de conflito de
  cabeçalho e evidências de catálogo e aritmética;
* chamadas e decisões de uma execução completa de ``map_columns``.

As mensagens destinadas ao usuário não entram na comparação de decisões.
Elas podem ser reescritas sem alterar o input dos modelos nem a escolha
das colunas.

Uma diferença na captura indica mudança no input ou no comportamento
registrado. Atualize a fixture apenas em uma alteração deliberada desses
inputs, junto com ``PROMPT_VERSION`` e a validação prevista no projeto::

    python tests/test_model_input_golden.py --write

Não regenere a fixture para acomodar uma revisão de linguagem.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

if __name__ == "__main__":
    # Execução direta (regeneração): o pytest já põe a raiz no caminho.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from invoice_mapper import (  # noqa: E402
    ColumnRef,
    DataMappingDecision,
    MapperConfig,
    OpenAIDataResolver,
    OpenAIEmbeddingHeaderScorer,
    SheetTable,
    _header_text,
    line_calculation_evidence,
    map_columns,
    tax_calculation_evidence,
)
from reference_catalog import CatalogSpec, LoadedCatalog, ReferenceCatalogs  # noqa: E402

GOLDEN_PATH = Path(__file__).resolve().parent / "fixtures" / "model_input_golden.json"

#: Campos de MappingResult que são mensagens para o usuário. Ficam fora da
#: comparação de decisões porque passam a ser escritos em português.
TEXT_FIELDS = ("reason", "arithmetic_proof")

RESOLVER_CASES = (
    "header_conflicts_unnamed_and_occurrence",
    "catalog_evidence_and_identifier_profiles",
    "weak_rows_and_line_arithmetic",
)


# ---------------------------------------------------------------------------
# Fake client da OpenAI
# ---------------------------------------------------------------------------


def _fake_embedding(text: str) -> list[float]:
    """Vetor determinístico: contagem das palavras do texto em 64 posições."""
    vector = [0.0] * 64
    for token in re.findall(r"\w+", text.casefold()):
        vector[int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % 64] += 1.0
    return vector


class RecordingOpenAI:
    """Fake client que registra os argumentos das chamadas e retorna respostas fixas.

    ``choices`` associa o nome da coluna do modelo ao ``client_column_id`` da
    resposta. Quando não há configuração para a coluna, escolhe a primeira candidata.
    """

    def __init__(self, choices: dict[str, str | None] | None = None) -> None:
        self.embedding_calls: list[dict[str, object]] = []
        self.parse_calls: list[dict[str, object]] = []
        self.choices = choices
        self.embeddings = SimpleNamespace(create=self._create_embeddings)
        self.responses = SimpleNamespace(parse=self._parse)

    def _create_embeddings(self, **kwargs):
        self.embedding_calls.append(kwargs)
        return SimpleNamespace(
            data=[SimpleNamespace(embedding=_fake_embedding(text)) for text in kwargs["input"]]
        )

    def _parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        payload = json.loads(kwargs["input"])
        candidate_ids = [item["client_column_id"] for item in payload["client_candidates"]]
        if self.choices is None:
            chosen = candidate_ids[0] if candidate_ids else None
        else:
            chosen = self.choices.get(payload["model_column"]["name"])
            if chosen not in candidate_ids:
                chosen = None
        return SimpleNamespace(
            output_parsed=DataMappingDecision(
                client_column_id=chosen,
                data_confidence=0.9 if chosen else 0.1,
                final_confidence=0.92 if chosen else 0.1,
                reason="Golden fake decision.",
            )
        )


# ---------------------------------------------------------------------------
# Dados determinísticos
# ---------------------------------------------------------------------------


def _cnpj(seed: int) -> str:
    """CNPJ válido (com dígitos verificadores) a partir de uma semente."""
    digits = f"{seed:08d}0001"
    for weights in (
        [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
        [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2],
    ):
        remainder = sum(int(d) * w for d, w in zip(digits, weights)) % 11
        digits += "0" if remainder < 2 else str(11 - remainder)
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _nfe_key(seed: int) -> str:
    """Chave de acesso de NF-e com 44 dígitos e dígito verificador válido."""
    body = f"35260511222333000181550010000{seed:05d}1{seed:08d}"[:43]
    total, weight = 0, 2
    for digit in reversed(body):
        total += int(digit) * weight
        weight = 2 if weight == 9 else weight + 1
    remainder = total % 11
    return body + ("0" if remainder in {0, 1} else str(11 - remainder))


def _tax_block(rows: int) -> tuple[list[float], list[int], list[float]]:
    """Trio fiscal coerente: valor = base x alíquota em todas as linhas."""
    base = [round(100 + index * 37.5, 2) for index in range(rows)]
    rate = [(18, 12, 7, 4)[index % 4] for index in range(rows)]
    value = [round(b * r / 100, 2) for b, r in zip(base, rate)]
    return base, rate, value


def _synthetic_catalogs(codes: dict[str, frozenset[str]]) -> ReferenceCatalogs:
    """Catálogos em memória, para não depender do conteúdo de reference_data/."""
    loaded = {}
    for name, catalog_codes in codes.items():
        spec = CatalogSpec(
            name=name,
            file=f"{name}.csv",
            width=len(next(iter(catalog_codes))),
            normalizer="cfop" if name == "cfop" else "punctuated_digits",
            specificity=1.0,
            source_version="golden-fixture-1",
            source_url=f"https://example.invalid/{name}",
            sha256="0" * 64,
        )
        loaded[name] = LoadedCatalog(spec=spec, codes=catalog_codes)
    return ReferenceCatalogs(Path("golden-fixture"), loaded)


def _tax_rule(base, rate, value, *, role: str) -> dict[str, object]:
    """Evidência no formato que _candidate_relationship_evidence monta para um trio."""
    rule = tax_calculation_evidence(pd.Series(base), pd.Series(rate), pd.Series(value))
    rule.update(
        {
            "rule": "tax_value_equals_base_times_rate",
            "target_role": role,
            "tax_scope": "icms",
            "tax_qualifiers": [],
        }
    )
    return rule


# ---------------------------------------------------------------------------
# Capturas
# ---------------------------------------------------------------------------


def _plain(value: object) -> object:
    """Converte a captura para valores serializáveis em JSON, usando o nome qualificado das classes."""
    if isinstance(value, type):
        return f"{value.__module__}.{value.__qualname__}"
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def _columns(table: SheetTable) -> list[dict[str, object]]:
    return [
        {
            "id": column.id,
            "index": column.index,
            "name": column.name,
            "occurrence": column.occurrence,
            "display_name": column.display_name,
        }
        for column in table.columns
    ]


def _capture_embedding_header_set(workdir: Path) -> dict[str, object]:
    """Captura os inputs de cabeçalhos vazios, com espaços e repetidos, após a leitura do Excel."""
    path = workdir / "embedding_headers.xlsx"
    headers = ["CFOP", None, "Valor ICMS", "valor icms", "   ", "Nº da Nota Fiscal", "Valor ICMS"]
    pd.DataFrame([headers, [5102, 1, 2, 3, 4, 5, 6]]).to_excel(path, header=False, index=False)
    client_table = SheetTable.from_excel(path)
    model_headers = ["CFOP", "Valor ICMS", "Nº da Nota Fiscal"]

    client = RecordingOpenAI()
    OpenAIEmbeddingHeaderScorer(client).scores(
        model_headers, [column.name for column in client_table.columns]
    )
    return {
        "model_headers": model_headers,
        "client_columns": _columns(client_table),
        "embedding_calls": client.embedding_calls,
    }


def _resolve(case: str, **arguments) -> dict[str, object]:
    client = RecordingOpenAI()
    OpenAIDataResolver(client).resolve(**arguments)
    assert len(client.parse_calls) == 1, case
    return {
        "candidate_display_names": [column.display_name for column, _, _ in arguments["candidates"]],
        "parse_kwargs": client.parse_calls[0],
    }


def _capture_resolver_cases() -> dict[str, dict[str, object]]:
    rows = 40
    base, rate, value = _tax_block(rows)
    ipi = [round(b * 0.05, 2) for b in base]
    icms_st = [round(b * 0.031 + index, 2) for index, b in enumerate(base)]
    other_value = [round(b * 1.1, 2) for b in base]
    unnamed = ColumnRef("column_004", 3, _header_text(None, 3), 1)
    candidates = [
        (ColumnRef("column_005", 4, "Valor IPI", 1), pd.Series(ipi), 0.8333),
        (ColumnRef("column_006", 5, "Valor ICMS ST", 1), pd.Series(icms_st), 0.9258),
        (ColumnRef("column_002", 1, "Base ICMS", 1), pd.Series(base), 0.7071),
        (unnamed, pd.Series(value), 0.6804),
        (ColumnRef("column_012", 11, "Valor ICMS", 2), pd.Series(other_value), 1.0),
    ]
    header_conflicts = _resolve(
        "header_conflicts_unnamed_and_occurrence",
        model_column=ColumnRef("column_004", 3, "Valor ICMS", 1),
        model_values=pd.Series([0.0, 0.0, 0.0, None]),
        candidates=candidates,
        sample_size=8,
        relationship_evidence={
            column.id: [_tax_rule(base, rate, list(values), role="value")]
            for column, values, _ in candidates
            if column.name != "Base ICMS"
        },
    )

    cfop_codes = ["5102", "6102/AA", "5405", "1102", "2102", "5949", "5102", "6108"]
    cfop_values = [cfop_codes[index % len(cfop_codes)] for index in range(rows)]
    catalogs = _synthetic_catalogs(
        {"cfop": frozenset({"5102", "6102", "5405", "1102", "2102", "6108"})}
    )
    identifier_candidates = [
        (ColumnRef("column_003", 2, "Cod. Fiscal", 1), pd.Series(cfop_values), 0.42),
        (
            ColumnRef("column_007", 6, "Natureza da Operacao", 1),
            pd.Series([("Venda", "Devolucao", "Remessa")[index % 3] for index in range(rows)]),
            0.31,
        ),
        (
            ColumnRef("column_008", 7, "Data Emissao", 1),
            pd.Series([datetime(2026, 5, 1) + timedelta(days=index) for index in range(rows)]),
            0.12,
        ),
        (
            ColumnRef("column_009", 8, "CNPJ Emitente", 1),
            pd.Series([_cnpj(11222333 + index) for index in range(rows)]),
            0.1,
        ),
        (
            ColumnRef("column_010", 9, "Chave NF-e", 1),
            pd.Series([_nfe_key(index) for index in range(rows)]),
            0.05,
        ),
    ]
    catalog_evidence = _resolve(
        "catalog_evidence_and_identifier_profiles",
        model_column=ColumnRef("column_001", 0, "CFOP", 1),
        model_values=pd.Series(["5102", "6102", None]),
        candidates=identifier_candidates,
        sample_size=8,
        catalog_evidence={
            column.id: [catalogs.analyze(values, "cfop")]
            for column, values, _ in identifier_candidates
        },
    )

    quantity = [float(1 + index % 5) for index in range(rows)]
    unit_price = [round(9.9 + index * 1.25, 2) for index in range(rows)]
    line_value = [round(q * p, 2) for q, p in zip(quantity, unit_price)]
    line_rule = line_calculation_evidence(
        pd.Series(quantity), pd.Series(unit_price), pd.Series(line_value)
    )
    line_rule.update(
        {"rule": "line_value_equals_quantity_times_unit_price", "target_role": "line_value"}
    )
    weak_rows = _resolve(
        "weak_rows_and_line_arithmetic",
        model_column=ColumnRef("column_002", 1, "Valor CBS", 1),
        model_values=pd.Series([0, 0, 0]),
        candidates=[
            (ColumnRef("column_001", 0, "CBS Value", 1), pd.Series([0, 0, 0]), 0.88),
            (ColumnRef("column_002", 1, _header_text("  ", 1), 1), pd.Series([None, None, None]), 0.41),
            (ColumnRef("column_003", 2, "Valor Total Item", 1), pd.Series(line_value), 0.37),
        ],
        sample_size=3,
        relationship_evidence={"column_003": [line_rule]},
    )
    return {
        "header_conflicts_unnamed_and_occurrence": header_conflicts,
        "catalog_evidence_and_identifier_profiles": catalog_evidence,
        "weak_rows_and_line_arithmetic": weak_rows,
    }


def _capture_end_to_end(workdir: Path) -> dict[str, object]:
    """Uma execução completa de map_columns com embeddings e fake resolvers.

    O cliente tem um cabeçalho em branco (a coluna que fecha valor = base x
    alíquota), um cabeçalho repetido e candidatos com os três tipos de
    conflito; o catálogo de NCM em memória não é decisivo, para que a
    evidência de catálogo chegue ao resolver. Tem também um trio de COFINS
    (prova aritmética decisiva) e dois blocos disjuntos de PIS (prova que não
    distingue a coluna), para que as decisões fixem as duas formas da prova.
    """
    rows = 40
    base, rate, value = _tax_block(rows)
    cofins_base = [round(80 + index * 12.25, 2) for index in range(rows)]
    cofins_value = [round(b * 7.6 / 100, 2) for b in cofins_base]
    pis_base = [round(200 + index * 11.0, 2) for index in range(rows)]
    pis_value = [round(b * 1.65 / 100, 2) for b in pis_base]
    pis_ap_base = [round(500 + index * 13.0, 2) for index in range(rows)]
    pis_ap_value = [round(b * 0.65 / 100, 2) for b in pis_ap_base]
    cfops = ["5102", "6102", "5405", "1102", "2102", "5949"]
    ncms = ["84714900", "85167910", "39269090"]
    client_rows = [
        [
            cfops[index % len(cfops)],
            base[index],
            rate[index],
            value[index],
            round(base[index] * 0.05, 2),
            round(base[index] * 0.031 + index, 2),
            round(base[index] * 1.1, 2),
            ncms[index % 3] if index % 5 else "99999999",
            datetime(2026, 5, 1) + timedelta(days=index),
            _cnpj(11222333 + index),
            f"Pedido {1000 + index}",
            f"Lote {index % 7}",
            round(base[index] * 1.3, 2),
            cofins_base[index],
            7.6,
            cofins_value[index],
            pis_base[index],
            1.65,
            pis_value[index],
            pis_ap_base[index],
            0.65,
            pis_ap_value[index],
        ]
        for index in range(rows)
    ]
    client_headers = [
        "CFOP", "Base ICMS", "Aliquota ICMS", None, "Valor IPI", "Valor ICMS ST",
        "Base Calculo Total", "Classificacao", "Data Emissao", "CNPJ Emitente",
        "Observacao", "Observacao", "Valor Total", "Base COFINS", "Aliquota COFINS",
        "Valor COFINS", "Base PIS", "Aliq PIS", "Valor PIS", "Base PIS Ap", "Aliq PIS Ap",
        "Vlr PIS Ap",
    ]
    model_headers = [
        "CFOP", "Base ICMS", "Aliquota ICMS", "Valor ICMS", "NCM", "Observacao",
        "Data de Emissao", "CNPJ do Emitente", "Campo Inexistente", "Base COFINS", "Valor PIS",
    ]
    model_rows = [[None, 0, 0, 0, None, None, None, None, None, 0, 0] for _ in range(3)]
    client_path = workdir / "end_to_end_client.xlsx"
    model_path = workdir / "end_to_end_model.xlsx"
    pd.DataFrame([client_headers, *client_rows]).to_excel(client_path, header=False, index=False)
    pd.DataFrame([model_headers, *model_rows]).to_excel(model_path, header=False, index=False)
    client_table = SheetTable.from_excel(client_path)
    model_table = SheetTable.from_excel(model_path)

    client = RecordingOpenAI(
        choices={
            "Valor ICMS": "column_004",
            "NCM": "column_008",
            "Observacao": "column_012",
        }
    )
    results = map_columns(
        model_table,
        client_table,
        OpenAIEmbeddingHeaderScorer(client),
        OpenAIDataResolver(client),
        MapperConfig(),
        reference_catalogs=_synthetic_catalogs({"ncm": frozenset(ncms)}),
    )
    decisions = []
    for result in results:
        row = {key: item for key, item in asdict(result).items() if key not in TEXT_FIELDS}
        row["arithmetic_proof_present"] = result.arithmetic_proof is not None
        decisions.append(row)
    return {
        "client_columns": _columns(client_table),
        "embedding_calls": client.embedding_calls,
        "parse_calls": client.parse_calls,
        "decisions": decisions,
    }


def build_capture(workdir: Path) -> dict[str, object]:
    """Reúne os dados da golden fixture, sem timestamps nem caminhos de arquivo."""
    embedding_header_set = _capture_embedding_header_set(workdir)
    resolver_cases = _capture_resolver_cases()
    end_to_end = _capture_end_to_end(workdir)
    instructions = resolver_cases[RESOLVER_CASES[0]]["parse_kwargs"]["instructions"]
    schema = DataMappingDecision.model_json_schema()
    first_call = embedding_header_set["embedding_calls"][0]
    first_header = embedding_header_set["model_headers"][0]
    capture = {
        "_leia_me": (
            "Entrada dos modelos de IA capturada no código anterior à tradução "
            "(commit 2ae4b6d). O cenário end-to-end foi atualizado para resolver aritmética "
            "antes da API; prompt, schema e payloads restantes foram preservados. "
            "Gerado por tests/test_model_input_golden.py; não edite à mão."
        ),
        "resolver_instructions": instructions,
        "data_mapping_decision_json_schema": schema,
        "embedding_prefix": first_call["input"][0].removesuffix(first_header),
        "fingerprints": {
            "resolver_instructions_sha256": hashlib.sha256(instructions.encode("utf-8")).hexdigest(),
            # Serializado na ordem dos campos, que é a ordem enviada à API.
            "data_mapping_decision_json_schema_sha256": hashlib.sha256(
                json.dumps(schema, ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
        },
        "embedding_header_set": embedding_header_set,
        "resolver_cases": resolver_cases,
        "end_to_end": end_to_end,
    }
    return json.loads(json.dumps(_plain(capture), ensure_ascii=False))


def serialize(capture: dict[str, object]) -> str:
    return json.dumps(capture, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def captured(tmp_path_factory) -> dict[str, object]:
    return build_capture(tmp_path_factory.mktemp("golden"))


@pytest.fixture(scope="module")
def golden() -> dict[str, object]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def _assert_same_call(actual: dict[str, object], expected: dict[str, object]) -> None:
    # O payload decodificado primeiro, para que uma diferença apareça legível;
    # depois a chamada inteira, com o JSON enviado comparado como texto.
    if "input" in expected and isinstance(expected["input"], str):
        assert json.loads(actual["input"]) == json.loads(expected["input"])
    assert actual == expected


def test_the_resolver_prompt_is_unchanged(captured, golden) -> None:
    assert captured["resolver_instructions"] == golden["resolver_instructions"]
    assert captured["fingerprints"] == golden["fingerprints"]


def test_the_structured_output_schema_is_unchanged(captured, golden) -> None:
    assert (
        captured["data_mapping_decision_json_schema"]
        == golden["data_mapping_decision_json_schema"]
    )


def test_the_embedding_prefix_and_inputs_are_unchanged(captured, golden) -> None:
    assert captured["embedding_prefix"] == golden["embedding_prefix"]
    assert captured["embedding_header_set"] == golden["embedding_header_set"]


@pytest.mark.parametrize("case", RESOLVER_CASES)
def test_each_resolver_payload_is_unchanged(case, captured, golden) -> None:
    actual = captured["resolver_cases"][case]
    expected = golden["resolver_cases"][case]
    assert actual["candidate_display_names"] == expected["candidate_display_names"]
    _assert_same_call(actual["parse_kwargs"], expected["parse_kwargs"])


def test_an_end_to_end_run_sends_the_same_requests(captured, golden) -> None:
    actual = captured["end_to_end"]
    expected = golden["end_to_end"]
    assert actual["client_columns"] == expected["client_columns"]
    assert actual["embedding_calls"] == expected["embedding_calls"]
    assert len(actual["parse_calls"]) == len(expected["parse_calls"])
    for actual_call, expected_call in zip(actual["parse_calls"], expected["parse_calls"]):
        _assert_same_call(actual_call, expected_call)


def test_an_end_to_end_run_makes_the_same_decisions(captured, golden) -> None:
    assert captured["end_to_end"]["decisions"] == golden["end_to_end"]["decisions"]


def test_the_scenarios_still_cover_what_they_claim(golden) -> None:
    # Protege a própria golden fixture: se um cenário deixar de exercitar
    # um caso, a garantia acima fica mais fraca sem ninguém perceber.
    payloads = [
        json.loads(case["parse_kwargs"]["input"]) for case in golden["resolver_cases"].values()
    ] + [json.loads(call["input"]) for call in golden["end_to_end"]["parse_calls"]]
    candidates = [item for payload in payloads for item in payload["client_candidates"]]
    conflicts = {item["semantic_header_conflict"] for item in candidates}
    assert any(text and text.startswith("tax-family conflict") for text in conflicts)
    assert any(text and text.startswith("tax-qualifier conflict") for text in conflicts)
    assert any(text and text.startswith("semantic-role conflict") for text in conflicts)
    assert any(item["name"].startswith("Unnamed column ") for item in candidates)
    assert any(item["catalog_evidence"] for item in candidates)
    assert any(item["relationship_evidence"] for item in candidates)
    display_names = [
        name
        for case in golden["resolver_cases"].values()
        for name in case["candidate_display_names"]
    ]
    assert any(name.endswith("(occurrence 2)") for name in display_names)
    embedded = [
        text
        for call in golden["embedding_header_set"]["embedding_calls"]
        for text in call["input"]
    ]
    assert any(text.endswith("Unnamed column 2") for text in embedded)
    names = [column["name"] for column in golden["embedding_header_set"]["client_columns"]]
    assert len(names) > len(set(names))


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        raise SystemExit(
            "Uso: python tests/test_model_input_golden.py --write\n"
            "Regrava a golden fixture. Revise as diferenças esperadas antes de usar; "
            "se o prompt mudar, atualize também PROMPT_VERSION."
        )
    with tempfile.TemporaryDirectory() as directory:
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(serialize(build_capture(Path(directory))), encoding="utf-8")
    print(f"Golden fixture gravada em {GOLDEN_PATH}")
