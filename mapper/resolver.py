"""Etapa 4: resolvedor de IA.

Recebe a coluna do modelo, as candidatas do cliente e suas evidências; monta o
payload, consulta a OpenAI e devolve um ``DataMappingDecision``. O resolver só
propõe: as regras locais de ``decide.py`` decidem o aceite.
"""

from __future__ import annotations

import json
from typing import Protocol, Sequence

import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

from mapper.evidence import distinctive_sample_overlap, profile_similarity, summarize_values
from mapper.headers import semantic_header_conflict
from mapper.spreadsheet import ColumnRef

# Prompt, payload JSON, schema de saída, ``semantic_header_conflict`` e os nomes
# de coluna (``ColumnRef.name``, ``display_name``) ficam em inglês, byte a byte. O
# golden test em tests/test_model_input_golden.py verifica que não mudaram.


DEFAULT_LLM_MODEL = "gpt-4o-mini"
#: Muda junto com o prompt; fica registrada nos Metadados da Execução.
PROMPT_VERSION = "2026-08-04.2"


class DataMappingDecision(BaseModel):
    client_column_id: str | None = Field(
        description="One candidate client_column_id, or null when none is compatible."
    )
    data_confidence: float = Field(ge=0.0, le=1.0)
    final_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="Short explanation grounded in names and sample values.")


class DataResolver(Protocol):
    def resolve(
        self,
        model_column: ColumnRef,
        model_values: pd.Series,
        candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
        sample_size: int,
        relationship_evidence: dict[str, list[dict[str, object]]] | None = None,
        catalog_evidence: dict[str, list[dict[str, object]]] | None = None,
    ) -> DataMappingDecision:
        """Escolhe, entre os candidatos do cliente, a coluna para uma coluna do modelo (ou nenhuma)."""


class OpenAIDataResolver:
    """Consulta a OpenAI e recebe uma decisão no formato ``DataMappingDecision``.

    O prompt, o payload JSON e o schema de saída permanecem em inglês. O golden
    test verifica esses inputs; a justificativa retornada pelo modelo também
    é mantida no idioma original.
    """

    def __init__(self, client: OpenAI, model: str = DEFAULT_LLM_MODEL) -> None:
        self.client = client
        self.model = model

    def resolve(
        self,
        model_column: ColumnRef,
        model_values: pd.Series,
        candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
        sample_size: int,
        relationship_evidence: dict[str, list[dict[str, object]]] | None = None,
        catalog_evidence: dict[str, list[dict[str, object]]] | None = None,
        *,
        target_profile: dict[str, object] | None = None,
        candidate_profiles: dict[str, dict[str, object]] | None = None,
    ) -> DataMappingDecision:
        if target_profile is None:
            target_profile = summarize_values(model_values, sample_size)
        if candidate_profiles is None:
            candidate_profiles = {
                column.id: summarize_values(values, sample_size)
                for column, values, _ in candidates
            }
        payload = {
            "model_column": {
                "name": model_column.name,
                "profile": target_profile,
            },
            "client_candidates": [
                {
                    "client_column_id": column.id,
                    "name": column.name,
                    "column_number": column.index + 1,
                    "name_confidence": round(name_confidence, 4),
                    "semantic_header_conflict": semantic_header_conflict(
                        model_column.name, column.name
                    ),
                    "profile_similarity": profile_similarity(
                        target_profile, candidate_profiles[column.id]
                    ),
                    "distinctive_sample_overlap": distinctive_sample_overlap(
                        target_profile, candidate_profiles[column.id]
                    ),
                    "relationship_evidence": (relationship_evidence or {}).get(
                        column.id, []
                    ),
                    "catalog_evidence": (catalog_evidence or {}).get(column.id, []),
                    "profile": candidate_profiles[column.id],
                }
                for column, values, name_confidence in candidates
            ],
        }
        response = self.client.responses.parse(
            model=self.model,
            store=False,
            temperature=0,
            instructions=(
                "Map one client spreadsheet column to one canonical Brazilian invoice column. "
                "The column names and name_confidence remain the primary semantic evidence throughout "
                "the decision; compare how close the candidate name scores are and never forget them "
                "after inspecting values. A client header can be bogus or misleading, so distinctive "
                "content and format evidence may override a weak name; generic numeric similarity "
                "alone is not enough. profile_similarity is a generic value-shape retrieval signal, "
                "not proof of a mapping. Then use representative "
                "values, inferred "
                "types, formats, lengths, and numeric scales. Recognize dates, monetary values, "
                "tax bases, tax rates, tax amounts, CFOP, NCM, CNPJ/CPF, invoice numbers, series, "
                "and 44-digit NF-e keys. First identify the semantic role of each header: a tax "
                "situation/code is not a tax base, rate, or amount even when all contain numbers. "
                "semantic_header_conflict identifies explicit tax-family or role disagreement; "
                "treat it as strong contrary evidence. A conflicting candidate may remain viable "
                "only when distinctive row content indicates that its header is bogus or wrong. "
                "Most canonical fields may be absent from a client export. Do not choose the best "
                "of bad candidates: return null unless the evidence supports the same business "
                "meaning. Matching only a broad type, generic numbers, zeros, dates, or strings is "
                "not semantic evidence. distinctive_sample_overlap reports exact overlap after "
                "excluding zeros and trivial short numbers; a positive value is strong row "
                "evidence and should be examined together with the headers. "
                "relationship_evidence contains conservative row-wise fiscal checks such as tax "
                "value approximately equaling base times rate, or line value approximately "
                "equaling quantity times unit price. A high score across many rows is strong "
                "evidence; a low score is contrary evidence, but legitimate adjustments, "
                "rounding, exemptions, credits, and reversals mean it is not an absolute rule. "
                "catalog_evidence reports membership in versioned official Brazilian fiscal-code "
                "tables. coverage is the fraction of all nonblank values found in the catalog; "
                "score also discounts short, collision-prone domains such as two-digit CST. High "
                "coverage in CFOP, NCM, municipality, or cClassTrib is distinctive content evidence. "
                "CST values overlap across taxes, especially PIS and COFINS, so use the header and "
                "tax-family context to disambiguate them. all_zero_only is not useful evidence. "
                "State, municipal, and federal registrations are distinct and cannot be selected "
                "only because their values have similar shapes. CPF/CNPJ values may contain 11 or "
                "14 digits, may include punctuation, and spreadsheet numeric cells may lose a "
                "leading zero. Prefer a candidate that explicitly names the compatible identifier "
                "over a generic document-number field. When candidates are otherwise close, prefer "
                "the header with the same semantic role and no unrelated extra qualifier. CFOP may "
                "appear as four digits (5102) or with an ERP suffix "
                "(5102/AA); those formats are compatible. Do not choose a candidate merely because "
                "both are numeric. A profile whose evidence_quality is empty or all_zero provides "
                "little or no row evidence: zeros neither confirm a mapping nor contradict a strong "
                "name match. When row evidence is weak, keep final_confidence close to the relevant "
                "name_confidence. "
                "Return null when the data does not support a mapping. final_confidence must reflect "
                "both the name and data evidence. Keep the reason to one sentence."
            ),
            input=json.dumps(payload, ensure_ascii=False),
            text_format=DataMappingDecision,
        )
        if response.output_parsed is None:
            raise RuntimeError("A OpenAI não retornou uma decisão de mapeamento interpretável")
        # Mantém a resposta original para auditoria. O mapper calcula o score local.
        return response.output_parsed
