"""Testes de ``mapper/resolver.py``: chamada à OpenAI e preservação da resposta original."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd

from mapper import decide, resolver
from mapper.decide import map_columns
from mapper.evidence import summarize_values
from mapper.resolver import DataMappingDecision, OpenAIDataResolver
from mapper.spreadsheet import ColumnRef

from support import Scores, table_from_headers


def test_openai_data_resolver_uses_structured_response() -> None:
    expected = DataMappingDecision(
        client_column_id="column_001",
        data_confidence=0.97,
        final_confidence=0.93,
        reason="The values are four-digit CFOP codes.",
    )

    class Responses:
        def __init__(self):
            self.arguments = None

        def parse(self, **kwargs):
            self.arguments = kwargs
            return SimpleNamespace(output_parsed=expected)

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    resolver = OpenAIDataResolver(client, model="llm-test")
    model_column = ColumnRef("column_001", 0, "CFOP", 1)
    client_column = ColumnRef("column_001", 0, "Cod. Fiscal", 1)

    decision = resolver.resolve(
        model_column,
        pd.Series(["5102", "6102"]),
        [(client_column, pd.Series(["5102", "6102"]), 0.61)],
        sample_size=2,
    )

    assert decision == expected
    assert responses.arguments["model"] == "llm-test"
    assert responses.arguments["store"] is False
    assert responses.arguments["temperature"] == 0
    assert "reasoning" not in responses.arguments
    assert responses.arguments["text_format"] is DataMappingDecision
    assert '"samples": ["5102", "6102"]' in responses.arguments["input"]


def test_data_resolver_preserves_the_raw_response_when_rows_are_all_zero() -> None:
    model_decision = DataMappingDecision(
        client_column_id="column_001",
        data_confidence=0.2,
        final_confidence=0.3,
        reason="The zero values do not add evidence.",
    )

    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=model_decision)

    resolver = OpenAIDataResolver(SimpleNamespace(responses=Responses()), model="llm-test")
    model_column = ColumnRef("model_001", 0, "Valor CBS", 1)
    client_column = ColumnRef("column_001", 0, "CBS Value", 1)

    decision = resolver.resolve(
        model_column,
        pd.Series([0, 0, 0]),
        [(client_column, pd.Series([0, 0, 0]), 0.88)],
        sample_size=3,
    )

    assert decision.model_dump() == model_decision.model_dump()


def test_resolver_reuses_the_six_prepared_profiles():
    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=DataMappingDecision(
                client_column_id=None, data_confidence=0, final_confidence=0, reason="No choice."))
    # decide.py (perfis preparados) e resolver.py (perfis ausentes) usam a função pelo
    # nome importado; o mesmo mock nos dois módulos conta todas as chamadas.
    summarize = Mock(wraps=summarize_values)
    with patch.object(decide, "summarize_values", summarize), \
         patch.object(resolver, "summarize_values", summarize):
        map_columns(
            table_from_headers(["Target A", "Target B", "Target C"], [["one", "two", "three"]]),
            table_from_headers(["Source X", "Source Y", "Source Z"], [["red", "green", "blue"]]),
            Scores({"Target A": [.85, .84, .83], "Target B": [.84, .85, .83],
                    "Target C": [.83, .84, .85]}),
            OpenAIDataResolver(SimpleNamespace(responses=Responses())),
        )
    assert summarize.call_count == 6
