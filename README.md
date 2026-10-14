# Mapeador de planilhas fiscais

## Comece aqui

O programa associa cada coluna de uma exportação de ERP do cliente a um dos 227 campos do modelo da consultoria, [`data/model.xlsx`](data/model.xlsx). Aceita sozinho só o que tem evidência decisiva e deixa o resto para revisão humana. Depois copia os valores para o layout do modelo, sem alterar nem inventar dados.

Tudo passa por um único entry point, [`run.py`](run.py), com quatro comandos:

| Comando | O que faz |
|---|---|
| `python run.py map EXPORTACAO` | Gera o relatório de mapeamento (`--offline` dispensa a OpenAI). |
| `python run.py apply EXPORTACAO RELATORIO --output ARQ` | Aplica o relatório revisado e grava a planilha no layout do modelo. |
| `python run.py evaluate` | Compara os relatórios gravados com os gabaritos de-para. |
| `python run.py update-catalogs` | Baixa as tabelas fiscais oficiais e regrava `reference_data/` (usa a rede). |

`python run.py COMANDO --help` mostra as opções de cada um. O código fica em [`mapper/`](mapper/), um módulo por etapa, na ordem do fluxo:

| Módulo | Etapa |
|---|---|
| [`spreadsheet.py`](mapper/spreadsheet.py) | 1. Ler as planilhas: `SheetTable`, `ColumnRef`, cabeçalhos repetidos. |
| [`schema.py`](mapper/schema.py) | Vocabulário dos arquivos em português e campos do modelo ([`data/target_fields.json`](data/target_fields.json)). |
| [`headers.py`](mapper/headers.py) | 2. Normalizar e pontuar cabeçalhos: sinônimos, tributos, papéis, conflitos, scorers léxico e de embeddings. |
| [`evidence.py`](mapper/evidence.py) | 3. Evidências nos valores: perfis, dígitos verificadores, aritmética fiscal e catálogos. |
| [`resolver.py`](mapper/resolver.py) | 4. Resolver de IA: prompt e payload enviados à OpenAI. |
| [`decide.py`](mapper/decide.py) | 5. `map_columns`: passagens, auditorias, regras locais, confiança e threshold. |
| [`report.py`](mapper/report.py) | 6. Gravar o relatório (comando `map`). |
| [`apply.py`](mapper/apply.py) | 7. Aplicar o relatório revisado (comando `apply`). |
| [`catalogs.py`](mapper/catalogs.py) | Tabelas fiscais oficiais e o comando `update-catalogs`. |
| [`evaluate.py`](mapper/evaluate.py) | Avaliação com os gabaritos (comando `evaluate`). |
| [`cli.py`](mapper/cli.py) | Ajuda e mensagens de erro em português. |

Para continuar, leia [Mapear, revisar e aplicar](#mapear-revisar-e-aplicar) abaixo e, depois, [Arquitetura](docs/ARCHITECTURE.md), [Dados e formatos](docs/DATA.md), [Avaliação](docs/EVALUATION.md) e [Limitações conhecidas](docs/KNOWN_ISSUES.md). Os testes ficam em [`tests/`](tests/), um arquivo por módulo.

O modelo tem 227 campos e normalmente é maior que a exportação recebida. Cada associação automática informa **qual método decidiu** e **a confiança em porcentagem**. Toda coluna do cliente sem destino confirmado fica listada para revisão humana, inclusive as colunas vazias.

## Começar

Use Python 3.13, versão usada na validação do projeto. Na raiz do repositório:

```bash
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

O modo online precisa de `OPENAI_API_KEY` exportada no shell. A aplicação não carrega `.env` automaticamente. Os padrões do código são `text-embedding-3-large` para embeddings e `gpt-4o-mini` para o resolver; podem ser alterados por `--embedding-model` e `--llm-model`, ou por `OPENAI_EMBEDDING_MODEL` e `OPENAI_MODEL`.

As exportações contêm dados confidenciais. O modo online envia cabeçalhos, amostras e perfis das colunas à OpenAI. Mantenha as entradas e saídas em ambiente autorizado; não publique o repositório nem inclua credenciais em commits.

## Mapear, revisar e aplicar

Gere uma execução nova em `scratch/`, pasta ignorada pelo Git:

```bash
mkdir -p scratch
python run.py map data/clients/oracle.xlsx --output scratch/mapping_oracle.xlsx
```

Para experimentar somente a comparação léxica de cabeçalhos, sem API:

```bash
python run.py map data/clients/oracle.xlsx --offline --output scratch/mapping_oracle_offline.xlsx
```

O modo offline não executa a análise completa da segunda passagem. Seus resultados não substituem a validação online.

O relatório XLSX tem quatro abas:

| Aba | Para que serve |
|---|---|
| `Mapeamentos` | Uma linha por campo do modelo, com proposta, evidências, confiança e parecer humano. |
| `Metadados da Execução` | Parâmetros, modelos de IA, versões e hashes das entradas. |
| `Colunas do Cliente sem Uso` | Todas as origens sem aceite automático. |
| `Fila de Revisão` | Propostas pendentes organizadas para conferência. |

Edite os pareceres em **`Mapeamentos`**:

| `parecer_revisor` | Efeito |
|---|---|
| Em branco | Mantém a decisão automática. |
| `correto` | Confirma a origem proposta. |
| `incorreto` | Usa a origem indicada em `coluna_cliente_correta`, pelo número a partir de 1 ou pelo nome. |
| `inexistente` | Confirma que o cliente não fornece esse campo. |

Use `observacao_revisor` para registrar o motivo. Cada origem pode alimentar apenas um destino confirmado; propostas pendentes podem disputar a mesma origem.

Aplique o relatório revisado:

```bash
python run.py apply data/clients/oracle.xlsx scratch/mapping_oracle.xlsx --output scratch/oracle_mapped.xlsx
```

A saída tem `Dados Mapeados`, `Cobertura` e uma **nova lista de colunas sem uso**, calculada após os pareceres. Campos sem origem confirmada ficam vazios. `--include-review` gera uma saída de conferência que também copia propostas pendentes; ela não deve ser tratada como entrega confirmada.

O comando `map` sobrescreve o arquivo indicado e começa com os pareceres em branco. Preserve a revisão existente antes de substituir um relatório. O comando `apply` lê o relatório sem modificá-lo.

## Entender o resultado

- `decidido_por` identifica a evidência que levou ao aceite: `semantico`, `lexico`, `aritmetica`, `catalogo`, `perfil` ou `identificador`. Fica vazio nas propostas não aceitas.
- `apoiado_por` registra as etapas de apoio. Uma prova aritmética encontrada apenas para auditoria não recebe o crédito pela decisão.
- `metodo` indica o caminho do pipeline, como `nome`, `dados` ou `catalogo`.
- `confianca_final` é um score, não uma probabilidade calibrada. A formatação mostra duas casas decimais: 0,8999 aparece como **89,99%**.

O threshold padrão de aceite é 90%. O código combina cabeçalhos, amostras, identificadores, catálogos e relações aritméticas. A confiança declarada pela IA fica preservada, sem ajustes, nos campos de auditoria. Quando a IA indica uma candidata com confiança zero, o mapper ainda avalia essa candidata pelas regras locais; zero não é um veto por si só. Disputas sem vantagem suficiente seguem para revisão. Novas confirmações podem ajudar a resolver a aritmética de outros campos, em rodadas locais sem chamadas adicionais à API.

O modelo e [`data/target_fields.json`](data/target_fields.json) definem os campos controlados. A coluna 35 é `CFOP_descrição`; a 195 é `item/material_descrição`. Não há aliases para os nomes antigos. Uma revisão humana não cria um novo score automático para a origem corrigida.

## Arquivos atuais

| Cliente | Colunas confirmadas na saída | Origens sem uso após revisão |
|---|---:|---:|
| Datasul | 19 | 329 |
| Oracle | 18 | 30 |
| SAP Procosa | 62 | 56 |
| SAP Terumo | 64 | 301 |

Esses números pertencem à execução online de **2026-10-11**. Os relatórios estão em [`data/reports/`](data/reports/) e os entregáveis em [`data/mapped/`](data/mapped/). Procosa inclui os 26 pareceres anteriores, preservados também em [`data/reviews/`](data/reviews/).

A validação registrou 771 chamadas bem-sucedidas ao resolver, 1.436.882 células copiadas conferidas e 6.072.278 células sem origem verificadas como vazias, sem divergências. Os registros estão em [`data/validation/`](data/validation/).

Datasul e Oracle entregam corretamente 32 das 56 associações dos gabaritos. Procosa e Terumo não têm gabarito de colunas; integridade da cópia não comprova a precisão de cada associação.

A simplificação posterior foi validada nos quatro clientes com as mesmas respostas gravadas da API. A limpeza preservou os resultados. As correções de confiança acrescentaram propostas para revisão e fizeram um aceite do Terumo voltar à revisão por disputa de origem: são 63 aceites nessa nova execução. Os arquivos da tabela acima foram preservados. Veja a [comparação da simplificação](docs/EVALUATION.md#validação-da-simplificação).

## Testar e avaliar

```bash
python -m pytest -q
python run.py evaluate
python run.py evaluate --client oracle --show-losses
```

A suíte de testes roda offline. O comando `evaluate` avalia relatórios existentes, considera a revisão humana e sinaliza os dois conflitos de reutilização de origem nos gabaritos. Exit code 0 não significa que todas as associações estão corretas; veja [Avaliação](docs/EVALUATION.md).

## Manutenção

Mantenha o contrato de origem exclusiva, os campos sem origem vazios e o threshold de 90%. Uma mudança nas regras precisa de evidência reproduzível e comparação dos pares alterados. Evite regras por cliente.

Escreva documentação e comentários em português natural, mantendo termos de desenvolvimento como API, DataFrame, schema, resolver, payload, score e threshold. Preserve o texto das observações humanas. Mudanças nos inputs da IA exigem revisão do golden test e validação própria.

Use `scratch/` para rascunhos. Preserve exportações, gabaritos, catálogos, fixtures e registros de revisão. Os quatro entregáveis atuais são mantidos para consulta e validação; caches e arquivos temporários não pertencem ao repositório.

- [Arquitetura](docs/ARCHITECTURE.md): módulos e regras de decisão.
- [Dados e formatos](docs/DATA.md): arquivos, abas e campos do relatório.
- [Avaliação](docs/EVALUATION.md): métricas, resultados e como validar mudanças.
- [Limitações conhecidas](docs/KNOWN_ISSUES.md): pontos ainda abertos.
