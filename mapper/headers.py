"""Etapa 2: normalizar e pontuar os cabeçalhos.

Recebe os cabeçalhos do modelo e do cliente e devolve a matriz de scores (léxica ou
por embeddings), além das leituras usadas pelas regras: assinatura semântica,
tributo, papel fiscal e conflitos entre os dois lados.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import re
from typing import Protocol, Sequence

import numpy as np
from openai import OpenAI

from mapper import schema
from mapper.schema import target_field
from mapper.spreadsheet import normalize_header


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"

# Cabeçalhos
IDENTICAL_HEADER_SCORE = 1.0  # texto normalizado idêntico
SEMANTIC_EQUIVALENCE_SCORE = 0.98  # assinaturas iguais: passa do threshold de nome, abaixo do idêntico
LEXICAL_SEQUENCE_WEIGHT = 0.7  # avaliador léxico: 0,7 x SequenceMatcher + 0,3 x Jaccard
LEXICAL_JACCARD_WEIGHT = 0.3
_MIN_ROLE_PREFIX = 3  # abaixo deste tamanho, uma palavra é ruído, não truncamento

#: Conflito de cabeçalho, como aparece no relatório. Os lados chegam prontos: famílias em maiúsculas
#: (``ICMS``), qualificadores e papéis pelos rótulos de ``schema``.
HEADER_CONFLICT_FAMILY_PT = "tributos diferentes ({left} vs {right})"
HEADER_CONFLICT_QUALIFIER_PT = "tratamentos tributários diferentes ({left} vs {right})"
HEADER_CONFLICT_ROLE_PT = "tipos de campo incompatíveis ({left} vs {right})"


# ---------------------------------------------------------------------------
# Leitura dos cabeçalhos: normalização, sinônimos, família de tributo e papel
# ---------------------------------------------------------------------------

_HEADER_STOPWORDS = {
    "44", "a", "as", "da", "das", "de", "do", "dos", "digit", "for", "of", "o", "os", "para", "the",
}

_HEADER_TOKEN_ALIASES = {
    "access": "acesso",
    "aliq": "aliquota",
    "aliquota": "aliquota",
    "alq": "aliquota",
    "basis": "base",
    "business": "negocio",
    "cof": "cofins",
    "cofi": "cofins",
    "cod": "codigo",
    "ctg": "categoria",
    "date": "data",
    "desc": "descricao",
    "description": "descricao",
    "doc": "documento",
    "document": "documento",
    "electronic": "eletronico",
    "eletronica": "eletronico",
    "empresa": "empresa",
    "goods": "mercadoria",
    "invoice": "nota",
    "ibss": "ibs",
    "key": "chave",
    "lanc": "lancamento",
    "liq": "liquido",
    "liquido": "liquido",
    "measure": "medida",
    "mercadorias": "mercadoria",
    "name": "nome",
    "negocios": "negocio",
    "net": "liquido",
    "no": "numero",
    "nr": "numero",
    "num": "numero",
    "number": "numero",
    "partner": "parceiro",
    "place": "local",
    "posting": "lancamento",
    "price": "preco",
    "product": "produto",
    "qtd": "quantidade",
    "quantity": "quantidade",
    "rate": "aliquota",
    "region": "regiao",
    "series": "serie",
    "supplier": "fornecedor",
    "taxa": "aliquota",
    "type": "categoria",
    "unit": "unidade",
    "val": "valor",
    "value": "valor",
    "vendor": "fornecedor",
    "vlr": "valor",
}

_TAX_FAMILY_ALIASES = {"cof": "cofins", "cofi": "cofins", "fcp": "fecp", "ibss": "ibs"}

_TAX_FAMILIES = {
    "cbs", "cofins", "csll", "fecp", "icap", "icep", "icms", "icsp",
    "ibs", "ibsm", "inss", "ipi", "irpj", "iss", "iva", "pis",
}

#: As três posições do trio fiscal e as três do trio quantidade x preço.
_TRIO_ROLES = frozenset({"base", "rate", "value"})
_LINE_ROLES = frozenset({"quantity", "unit_price", "line_value"})

#: Palavras que nomeiam um papel no cabeçalho (usadas por header_semantic_role).
#: Não incluem "outros" nem "isentos", que _ROLE_CONCEPT_WORDS inclui.
_OTHER_WORDS = frozenset({"outra", "outras", "outro", "other"})
_EXEMPT_WORDS = frozenset({"isenta", "isentas", "isento", "exempt"})
_REDUCED_WORDS = frozenset({"reduzida", "reduzido", "reduced"})
_BASE_WORDS = frozenset({"base", "basis"})
_VALUE_WORDS = frozenset({"valor", "value", "amount"})
_RATE_WORDS = frozenset({"aliquota", "rate", "taxa", "tximp"})
_MONTANTE_WORDS = frozenset({"mont", "monta", "montante"})

#: Palavras de papel agrupadas pelo conceito. Os ERPs truncam os cabeçalhos
#: ("Vlr Isen ICM"), então um prefixo que alcança um único conceito é
#: expandido. Nenhum prefixo daqui alcança dois conceitos;
#: ``test_the_role_vocabulary_is_prefix_unambiguous`` garante isso.
_ROLE_CONCEPT_WORDS: dict[str, frozenset[str]] = {
    "other": frozenset({"outra", "outras", "outro", "outros", "other"}),
    "exempt": frozenset({"isenta", "isentas", "isento", "isentos", "exempt"}),
    "reduced": frozenset({"reduzida", "reduzido", "reduced"}),
}
_ROLE_CANONICAL = {"other": "outras", "exempt": "isentas", "reduced": "reduzida"}
_ROLE_WORDS = frozenset(word for words in _ROLE_CONCEPT_WORDS.values() for word in words)


def _role_prefix_index() -> dict[str, str]:
    """Associa cada prefixo abreviado ao conceito que ele identifica sem ambiguidade."""
    index: dict[str, list[str]] = {}
    for concept, words in _ROLE_CONCEPT_WORDS.items():
        for word in words:
            for size in range(_MIN_ROLE_PREFIX, len(word)):
                index.setdefault(word[:size], []).append(concept)
    return {
        prefix: concepts[0]
        for prefix, concepts in index.items()
        if len(set(concepts)) == 1
    }


_ROLE_PREFIXES = _role_prefix_index()


def semantic_header_signature(value: object) -> frozenset[str]:
    """Reúne os conceitos do cabeçalho após remover stopwords e normalizar sinônimos.

    Usa vocabulário fiscal em português e inglês, sem regras específicas de
    cliente. Assinaturas iguais e não vazias indicam cabeçalhos equivalentes;
    a ordem das palavras não interfere na comparação.
    """
    normalized = normalize_header(value)
    # CST é a sigla padrão de "código de situação tributária".
    normalized = re.sub(r"\btax\s+situation\b", "cst", normalized)
    normalized = re.sub(r"\bsituacao\s+tributaria\b", "cst", normalized)
    # Nos livros fiscais, "valor contábil" é o total do documento. Exigir a
    # palavra de valor deixa de fora "Conta Contábil" e "Data Contábil".
    normalized = re.sub(r"\b(?:valor|vlr|val|vl)\s+contab\w*", "valor total nf", normalized)
    tokens = [
        _HEADER_TOKEN_ALIASES.get(token, token)
        for token in normalized.split()
        if token not in _HEADER_STOPWORDS
    ]
    # Alguns ERPs truncam PIS para PI; só no contexto de CST o sentido é inequívoco.
    if "cst" in tokens:
        tokens = ["pis" if token == "pi" else token for token in tokens]
    return frozenset(tokens)


def underspecified_header_match(model_header: object, client_header: object) -> bool:
    """Verifica se o cabeçalho do cliente é genérico demais para uma escolha pelo nome.

    Isso ocorre quando ele contém apenas uma palavra da assinatura mais longa
    do modelo, como ``Centro`` para ``Centro custo``. A proposta ainda pode ser
    aceita se houver evidência suficiente nos dados.
    """
    model_signature = semantic_header_signature(model_header)
    client_signature = semantic_header_signature(client_header)
    return (
        len(model_signature) > 1
        and len(client_signature) == 1
        and client_signature < model_signature
    )


def header_tax_families(value: object) -> frozenset[str]:
    """Identifica os tributos mencionados explicitamente no cabeçalho."""
    families = {
        _TAX_FAMILY_ALIASES.get(token, token)
        for token in semantic_header_signature(value)
    }
    return frozenset(families & _TAX_FAMILIES)


def expand_truncated_role_words(tokens: set[str]) -> set[str]:
    """Expande os tokens abreviados que identificam um único papel fiscal.

    Mantém o token original e acrescenta a palavra completa ao conjunto.
    """
    expanded = set(tokens)
    for token in tokens:
        if token in _ROLE_WORDS or len(token) < _MIN_ROLE_PREFIX:
            continue
        concept = _ROLE_PREFIXES.get(token)
        if concept is not None:
            expanded.add(_ROLE_CANONICAL[concept])
    return expanded


def header_semantic_role(value: object) -> str | None:
    """Identifica o primeiro papel fiscal reconhecido no cabeçalho, sem consultar os valores."""
    normalized = normalize_header(value)
    padded = f" {normalized} "
    raw_tokens = set(normalized.split())
    tokens = expand_truncated_role_words(
        set(semantic_header_signature(value)) | raw_tokens
    )

    if (
        {"classification", "classificacao"} & tokens
        or ({"class", "classif"} & tokens and {"tax", "tributaria"} & tokens)
    ):
        return "tax_classification"
    if (
        " tax situation " in padded
        or "taxsituation" in tokens
        or " situacao " in padded
        or " sit tributaria " in padded
        or "cst" in tokens
        or " item estat imposto " in padded
        or re.search(r"\btipo\s+(?:de\s+|do\s+)?imposto\b", normalized)
    ):
        return "tax_status"
    # Um nome de família pode trazer um sinal de alíquota (DIF_ALIQ gera
    # "aliq"). Se o cabeçalho também nomeia uma posição e a alíquota vem só de
    # uma abreviação, a posição vence; "Tax rate (Tax/Base)" continua alíquota.
    names_a_slot = bool(
        tokens & (_OTHER_WORDS | _EXEMPT_WORDS | _REDUCED_WORDS | _BASE_WORDS | _VALUE_WORDS)
    )
    rate_word_spelled_out = bool(_RATE_WORDS & raw_tokens) or " tx imposto " in padded
    names_a_rate = bool(_RATE_WORDS & tokens) or " tx imposto " in padded
    if names_a_rate and not (names_a_slot and not rate_word_spelled_out):
        return "rate"
    if _OTHER_WORDS & tokens:
        return "other"
    if (
        _EXEMPT_WORDS & tokens
        or ({"exc", "excl", "excluded"} & tokens and _BASE_WORDS & tokens)
    ):
        return "exempt"
    if _REDUCED_WORDS & tokens and _BASE_WORDS & tokens:
        return "reduced_base"
    # "Mont." é a base de cálculo numa coluna de tributo (PIS Mont.) e o
    # montante (valor) fora dela.
    if header_tax_families(value) and _MONTANTE_WORDS & tokens:
        return "base"
    if _BASE_WORDS & tokens:
        return "base"
    if _VALUE_WORDS & tokens or ("total" in tokens and {"item", "linha"} & tokens):
        return "value"
    if _MONTANTE_WORDS & tokens:
        return "value"
    if {"grupo", "group"} & tokens and {"imposto", "tax"} & tokens:
        return "tax_group"
    if (
        {"grupo", "group"} & tokens
        and {"material", "mercadoria", "produto"} & tokens
    ):
        return "product_group"
    if {"lei", "law", "direito"} & tokens:
        return "tax_rule"
    if {"texto", "text", "descricao", "description"} & tokens:
        return "text"
    if "medida" in tokens and {"unidade", "codigo"} & tokens:
        return "unit_of_measure"
    if "preco" in tokens:
        return "unit_price"
    if ({"chave", "key"} & tokens and {"acesso", "nfe", "nf"} & tokens):
        return "nfe_key"
    if {"cnpj", "cpf"} & tokens or (
        {"fiscal", "tax"} & tokens
        and {"id", "identificador"} & tokens
        and not {"regional", "state", "estadual", "municipal"} & tokens
    ):
        return "party_tax_id"
    if (
        {"regional", "state", "estadual", "municipal"} & tokens
        and {"fiscal", "tax", "id", "inscricao", "registration"} & tokens
    ):
        return "regional_tax_id"
    if (
        "item" in tokens
        and not {"imposto", "tax", "total", "valor", "value", "amount"} & tokens
        and (
            len(tokens) == 1
            or bool({"numero", "codigo", "id", "identificador"} & tokens)
        )
    ):
        return "item_id"
    if (
        {"numero", "number", "documento", "document", "nf", "nfe", "invoice"}
        & tokens
        and not {"fiscal", "tax"} & tokens
    ):
        return "document_id"
    return None


def tax_calculation_scope(value: object) -> tuple[str, tuple[str, ...]] | None:
    """Identifica o tributo e seus qualificadores (ST, DIF etc.); retorna ``None`` se houver ambiguidade."""
    families = header_tax_families(value)
    if len(families) != 1:
        return None
    tokens = set(normalize_header(value).split())
    qualifiers = tuple(
        qualifier
        for qualifier in ("st", "icop", "dif", "diferencial")
        if qualifier in tokens
    )
    return next(iter(families)), qualifiers


def line_calculation_role(value: object) -> str | None:
    """Identifica pelo cabeçalho a posição na equação quantidade x preço unitário = valor do item."""
    tokens = semantic_header_signature(value)
    normalized = normalize_header(value)
    if {"quantidade", "quantity", "qtd"} & set(normalized.split()):
        return "quantity"
    if "preco" in tokens:
        return "unit_price"
    if (
        {"item", "linha"} & tokens
        and "valor" in tokens
        and "total" in tokens
    ):
        return "line_value"
    return None


# Papel e escopo de um campo do modelo: vêm de data/target_fields.json e, para
# cabeçalhos fora do registro, da leitura do próprio cabeçalho.
def _target_role(header: str) -> str | None:
    definition = target_field(header)
    return definition.role if definition is not None else header_semantic_role(header)


def _target_tax_scope(header: str) -> tuple[str, tuple[str, ...]] | None:
    definition = target_field(header)
    return definition.tax_scope if definition is not None else tax_calculation_scope(header)


def _same_explicit_role(model_header: object, client_header: object) -> bool:
    model_role = _target_role(model_header)
    return model_role is not None and model_role == header_semantic_role(client_header)


def _header_conflict_parts(
    left: object, right: object
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Identifica o conflito entre dois cabeçalhos, ou retorna ``None``.

    Retorna ``(tipo, lado esquerdo, lado direito)``, com tipo ``family``,
    ``qualifier`` ou ``role``. As descrições para a IA e para o usuário partem
    dessa mesma estrutura, garantindo que representem o mesmo conflito.
    """
    definition = target_field(left)
    left_families = (frozenset(definition.tax_families) if definition is not None
                     else header_tax_families(left))
    right_families = header_tax_families(right)
    if left_families and right_families and left_families.isdisjoint(right_families):
        return "family", tuple(sorted(left_families)), tuple(sorted(right_families))

    left_qualifiers = (set(definition.tax_qualifiers) if definition is not None
                       else set(normalize_header(left).split())) & {"st"}
    right_qualifiers = set(normalize_header(right).split()) & {"st"}
    if (
        left_families
        and right_families
        and not left_families.isdisjoint(right_families)
        and left_qualifiers != right_qualifiers
    ):
        # Sem qualificador, o tributo é o normal (ICMS normal x ICMS ST).
        return (
            "qualifier",
            tuple(sorted(left_qualifiers)) or ("regular",),
            tuple(sorted(right_qualifiers)) or ("regular",),
        )

    left_role = _target_role(left)
    right_role = header_semantic_role(right)
    if left_role is not None and right_role is not None and left_role != right_role:
        return "role", (left_role,), (right_role,)
    return None


def semantic_header_conflict(left: object, right: object) -> str | None:
    """Descreve o conflito de tributo, qualificador ou papel entre dois cabeçalhos.

    Só compara atributos reconhecidos nos dois lados; a ausência de um atributo
    não constitui conflito. O texto integra o payload da IA e deve permanecer
    em inglês. Para o relatório, use ``describe_header_conflict_pt``.
    """
    parts = _header_conflict_parts(left, right)
    if parts is None:
        return None
    kind, left_side, right_side = parts
    if kind == "family":
        return f"tax-family conflict ({', '.join(left_side)} vs {', '.join(right_side)})"
    if kind == "qualifier":
        return f"tax-qualifier conflict ({', '.join(left_side)} vs {', '.join(right_side)})"
    return f"semantic-role conflict ({left_side[0]} vs {right_side[0]})"


def describe_header_conflict_pt(left: object, right: object) -> str | None:
    """Descreve em português o mesmo conflito identificado por ``semantic_header_conflict``."""
    parts = _header_conflict_parts(left, right)
    if parts is None:
        return None
    kind, left_side, right_side = parts
    if kind == "family":
        template, labels = HEADER_CONFLICT_FAMILY_PT, None
    elif kind == "qualifier":
        template, labels = HEADER_CONFLICT_QUALIFIER_PT, schema.TEXT_LABELS["tax_qualifier"]
    else:
        template, labels = HEADER_CONFLICT_ROLE_PT, schema.TEXT_LABELS["header_role"]

    def side(values: tuple[str, ...]) -> str:
        # Siglas de tributo em maiúsculas; qualificadores e papéis pelo rótulo.
        return ", ".join(
            value.upper() if labels is None else labels.get(value, value) for value in values
        )

    return template.format(left=side(left_side), right=side(right_side))


# ---------------------------------------------------------------------------
# Score dos cabeçalhos
# ---------------------------------------------------------------------------


class HeaderScorer(Protocol):
    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        """Matriz com formato (cabeçalhos do modelo, cabeçalhos do cliente)."""


class LexicalHeaderScorer:
    """Avaliador de cabeçalhos offline, usado nos testes e nas execuções locais (--offline)."""

    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(model_headers), len(client_headers)), dtype=float)
        client_features = [
            (normalize_header(header), semantic_header_signature(header))
            for header in client_headers
        ]
        for i, target in enumerate(model_headers):
            left = normalize_header(target)
            left_tokens = semantic_header_signature(target)
            for j, (right, right_tokens) in enumerate(client_features):
                if left and left == right:
                    matrix[i, j] = IDENTICAL_HEADER_SCORE
                    continue
                union = left_tokens | right_tokens
                jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
                sequence = SequenceMatcher(None, left, right).ratio()
                matrix[i, j] = LEXICAL_SEQUENCE_WEIGHT * sequence + LEXICAL_JACCARD_WEIGHT * jaccard
                if left and left_tokens and left_tokens == right_tokens:
                    matrix[i, j] = max(matrix[i, j], SEMANTIC_EQUIVALENCE_SCORE)
        return matrix


class OpenAIEmbeddingHeaderScorer:
    """Avaliador de cabeçalhos por embeddings da OpenAI (similaridade de cosseno).

    O prefixo enviado com cada cabeçalho fica em inglês, byte a byte: mudá-lo
    mudaria os vetores e os scores de nome.
    """

    def __init__(
        self,
        client: OpenAI,
        model: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 512,
    ) -> None:
        self.client = client
        self.model = model
        self.batch_size = batch_size

    def _embeddings(self, headers: Sequence[str]) -> np.ndarray:
        unique_headers = list(dict.fromkeys(headers))
        vectors: dict[str, list[float]] = {}
        for start in range(0, len(unique_headers), self.batch_size):
            batch = unique_headers[start : start + self.batch_size]
            response = self.client.embeddings.create(
                model=self.model,
                input=[f"Brazilian invoice spreadsheet column: {header}" for header in batch],
                encoding_format="float",
            )
            for header, item in zip(batch, response.data, strict=True):
                vectors[header] = item.embedding
        return np.asarray([vectors[header] for header in headers], dtype=float)

    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        model_vectors = self._embeddings(model_headers)
        client_vectors = self._embeddings(client_headers)
        model_norms = np.linalg.norm(model_vectors, axis=1, keepdims=True)
        client_norms = np.linalg.norm(client_vectors, axis=1, keepdims=True)
        if np.any(model_norms == 0) or np.any(client_norms == 0):
            raise ValueError("A OpenAI retornou um embedding de comprimento zero")
        similarities = (model_vectors / model_norms) @ (client_vectors / client_norms).T
        similarities = np.clip(similarities, 0.0, 1.0)

        # Cabeçalhos idênticos recebem 1,0. Assinaturas equivalentes elevam
        # o score até 0,98, preservando a diferença entre os dois casos.
        client_features = [
            (normalize_header(header), semantic_header_signature(header))
            for header in client_headers
        ]
        for i, target in enumerate(model_headers):
            left = normalize_header(target)
            left_tokens = semantic_header_signature(target)
            for j, (right, right_tokens) in enumerate(client_features):
                if left == right:
                    similarities[i, j] = IDENTICAL_HEADER_SCORE
                elif left_tokens and left_tokens == right_tokens:
                    similarities[i, j] = max(
                        similarities[i, j], SEMANTIC_EQUIVALENCE_SCORE
                    )
        return similarities
