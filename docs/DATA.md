# Dados e formatos

Os arquivos de cliente são confidenciais. Os exemplos e entregáveis atuais pertencem à execução de 2026-10-11. O [README](../README.md) mostra os comandos de uso; as regras de decisão estão em [Arquitetura](ARCHITECTURE.md).

## O que manter

| Caminho | Finalidade |
|---|---|
| [`data/model.xlsx`](../data/model.xlsx) | Modelo padrão da consultoria. |
| [`data/target_fields.json`](../data/target_fields.json) | Identidade e semântica de cada campo do modelo. |
| [`data/clients/`](../data/clients/) | Quatro exportações originais dos clientes. |
| [`data/keys/`](../data/keys/) | Gabaritos de Datasul e Oracle e especificação de registros de Procosa. |
| [`data/reports/`](../data/reports/) | Quatro relatórios atuais; o de Procosa inclui os pareceres humanos. |
| [`data/mapped/`](../data/mapped/) | Quatro entregáveis correspondentes aos relatórios atuais. |
| [`data/reviews/`](../data/reviews/) | Relatório original revisado de Procosa e registro de transferência dos pareceres. |
| [`data/validation/`](../data/validation/) | Evidências da execução e da verificação dos entregáveis. |
| [`reference_data/`](../reference_data/) | Dez catálogos CSV e manifesto com proveniência e checksums. |
| [`tests/fixtures/`](../tests/fixtures/) | Inputs fixos dos testes de IA e da regressão histórica do evaluator. |

Esses arquivos têm funções diferentes; não são cópias descartáveis. Rascunhos e execuções experimentais devem ficar em `scratch/`, ignorado pelo Git. Caches, bytecode e arquivos temporários do Excel também são ignorados.

## Modelo e entradas

`data/model.xlsx` tem uma aba, `Sheet1`, com 227 cabeçalhos na linha 1 e 209 linhas de amostra. Os campos 35 e 195 se chamam `CFOP_descrição` e `item/material_descrição`. A renomeação preservou as demais células, estilos e ordem. Não há aliases para os nomes anteriores.

O número é a identidade do campo nos relatórios e gabaritos. `Domicílio fiscal` (76) e `Domicílio Fiscal` (154) continuam distintos. Cabeçalhos desconhecidos ou repetidos do cliente recebem uma identificação de ocorrência, mas o número da coluna continua sendo a referência mais segura.

| Cliente | Exportação | Linhas lidas | Colunas |
|---|---|---:|---:|
| Datasul | `data/clients/datasul_maio.xlsx` | 6.804 | 348 |
| Oracle | `data/clients/oracle.xlsx` | 7.340 | 48 |
| SAP Procosa | `data/clients/sap_procosa.XLSX` | 18.209 | 118 |
| SAP Terumo | `data/clients/sap_terumo.XLSX` | 727 | 365 |

A leitura padrão usa a primeira aba e o cabeçalho na linha 1. `--client-sheet`, `--model-sheet` e as opções `--*-header-row` permitem outros layouts. Na CLI, números de abas começam em 0 e linhas de cabeçalho em 1.

Nos gabaritos de Datasul e Oracle, a linha 1 tem anotações como `Coluna BV`; a posição indica o campo do modelo. O evaluator não alinha as linhas de dados do gabarito com as da exportação. `de_para_procosa.xlsx` descreve a granularidade ITEM/NOTA de outro layout e não é um gabarito de colunas.

## Relatório de mapeamento

O XLSX atual usa o schema em português, versão 3, e tem quatro abas. Consulte [`mapper/schema.py`](../mapper/schema.py) para a lista completa e a ordem dos campos. CSV conserva apenas `Mapeamentos` e perde metadados e listas auxiliares.

### Mapeamentos

Cada linha representa um campo do modelo:

| Grupo | Campos principais e leitura |
|---|---|
| Destino | `numero_coluna_modelo`, `coluna_modelo`. O apply exige correspondência com o modelo informado. |
| Proposta | `numero_coluna_cliente`, `coluna_cliente`. A origem proposta pode ainda depender de revisão. |
| Decisão automática | `situacao`: `aceito`, `revisao` ou `sem_correspondencia`; `metodo` e `justificativa` explicam o caminho. |
| Evidências | `confianca_nome`, `margem_nome`, `confianca_dados`, campos de catálogo e `prova_aritmetica`. |
| Confiança | `confianca_final`, número de 0 a 1 exibido com duas casas decimais em porcentagem. Os campos com sufixo `_ia` são apenas auditoria do resolver. |
| Atribuição | `decidido_por` identifica o método de aceite; `apoiado_por` lista apoio; `vetado_por` registra alguns bloqueios. |
| Parecer | `parecer_revisor`, `coluna_cliente_correta`, `observacao_revisor`. São os campos destinados à edição humana. |

O parecer aceita `correto`, `incorreto` e `inexistente`, tolerando diferenças de caixa, acento e espaços. `incorreto` exige a origem corrigida, por número ou nome. Um cabeçalho numérico digitado como correção é interpretado como número de coluna. Para nomes repetidos, use o número ou a ocorrência explícita.

Edite os campos do revisor, mantendo `situacao` e os IDs automáticos. Uma associação confirmada pode aparecer com situação automática `revisao` ou `sem_correspondencia`; apply e evaluator usam a decisão efetiva após o parecer.

### Metadados da Execução

Registra versões do schema e prompt, horário, entradas e seus SHA-256, abas e linhas de cabeçalho, modelos de IA, parâmetros e hash do manifesto de referência. O apply verifica os hashes do cliente e modelo quando presentes. Ausência de metadados reduz essa proteção; veja [Limitações conhecidas](KNOWN_ISSUES.md).

### Colunas do Cliente sem Uso

No relatório, é o retrato automático da execução. Inclui todas as origens não consumidas por um aceite, inclusive as vazias. `proposta_nao_aceita` identifica uma origem proposta mas não confirmada; `nunca_referenciada` indica que nenhuma proposta a usou.

A aba também informa contagens de valores, prioridade, sinais como catálogo ou DV, e o campo do modelo mais próximo por comparação léxica. Esses sinais ajudam o revisor, mas não confirmam uma associação.

### Fila de Revisão

Reúne propostas não aceitas com confiança positiva. Prioriza colunas preenchidas, faixas de confiança e margem de cabeçalho. É outra fotografia da execução automática: editar um parecer em `Mapeamentos` não recalcula esta aba.

## Planilha mapeada

A saída do apply contém três abas:

| Aba | Conteúdo |
|---|---|
| `Dados Mapeados` | Cabeçalhos do modelo e valores copiados, com um banner de metadados. |
| `Cobertura` | Situação efetiva de cada campo: `entregue`, `aguardando decisão` ou `não consta na exportação do cliente`. |
| `Colunas do Cliente sem Uso` | Inventário recalculado após os pareceres, com origem, disposição e contagens de valores. |

Em `Dados Mapeados`, A1 é `__info_mapeamento__`. As colunas B em diante seguem o modelo. A linha 1 contém cabeçalhos, as linhas 2 a 22 contêm o banner e os dados começam na linha 23. Importe `FIRST_DATA_EXCEL_ROW` em vez de fixar esse número em integrações.

As origens do banner refletem as correções; os scores e a situação automática descrevem a proposta original. `Cobertura` informa a situação efetiva. Colunas sem dados entregues ficam ocultas, sem sair do arquivo. Valores `datetime.time` recebem formato de hora do Excel.

```python
import pandas as pd
from mapper.apply import FIRST_DATA_EXCEL_ROW

arquivo = "data/mapped/oracle_mapped.xlsx"
with pd.ExcelFile(arquivo, engine="openpyxl") as excel:
    # O pandas omite linhas finais vazias; preserve a altura da saída do apply.
    linhas = excel.book["Dados Mapeados"].max_row - FIRST_DATA_EXCEL_ROW + 1
    dados = pd.read_excel(
        excel, sheet_name="Dados Mapeados", header=0,
        skiprows=range(1, FIRST_DATA_EXCEL_ROW - 1), dtype=object,
    ).reindex(range(linhas))
    cobertura = pd.read_excel(excel, sheet_name="Cobertura")
    sem_uso = pd.read_excel(excel, sheet_name="Colunas do Cliente sem Uso")
```

A saída com `--include-review` é uma prévia: copia também propostas pendentes. A lista de origens sem confirmação continua incluindo essas propostas.

## Revisão preservada e validação

O relatório em `data/reviews/mapping_sap_procosa_reviewed.xlsx` é a evidência original dos 26 pareceres: 15 confirmações, duas correções e nove ausências. Não é o relatório operacional atual nem deve ser aplicado ao modelo renomeado. `procosa_review_transfer.json` registra a transferência das mesmas decisões e observações para `data/reports/mapping_sap_procosa_full.xlsx`.

`automatic_mapping_verification.json` registra as quatro execuções antes de reaplicar a revisão. `workbook_verification.json` registra a conferência dos entregáveis finais e seus hashes. Por isso Procosa tem 45 aceites no registro automático e 62 confirmações na saída final; a lista sem uso passa de 73 para 56 origens.

## Catálogos e formatos anteriores

Cada CSV em `reference_data/` contém códigos, descrições e datas de vigência quando disponíveis. O manifesto informa arquivo, origem, versão, largura, normalização, especificidade e SHA-256. O loader confere cada CSV antes de usá-lo. A vigência ainda não participa da comparação, e a correspondência com um catálogo não é uma validação do tratamento fiscal da nota.

`python run.py update-catalogs` acessa as fontes configuradas e regrava os catálogos. Atualize-os separadamente de mudanças no mapper, conferindo formatos e proveniência; uma nova publicação pode exigir ajustes no parser.

Relatórios antigos no schema em inglês são recusados. A conversão histórica pertence ao Git; ela não faz parte do fluxo atual. Não renomeie colunas ou remova hashes manualmente para forçar um relatório antigo a passar pelas verificações.
