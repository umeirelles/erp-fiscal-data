# Avaliação: como saber se uma mudança ajuda

Esta página explica como o mapeador é medido: qual é o gabarito e onde ele fica aquém, como o `mapeval` transforma um relatório em números, a linha de base de hoje, por que o limiar de aceite continua em 0,90 e os passos a seguir antes de chamar uma mudança de melhoria. Para como o mapeador decide, veja [ARCHITECTURE.md](ARCHITECTURE.md). Para as planilhas em si, veja [DATA.md](DATA.md). Defeitos e divergências entre código e comentário estão reunidos em [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

Todos os números desta página foram medidos em 2026-10-09 nos arquivos de `data/`, com os relatórios já migrados para o esquema em português. A migração não mudou nenhum número. Rode todos os comandos a partir da raiz do repositório. Nada aqui chama a OpenAI, a não ser onde a página indica.

O código usa o vocabulário interno em inglês (`status == "accepted"`, `Outcome.CORRECT`). Relatórios, planilhas e a saída das CLIs estão em português. Os nomes de aba, coluna e valor citados aqui são os que `report_schema.py` grava. O mapeval só lê o esquema em português (versão 3); um relatório no formato antigo, em inglês (versão 2), é recusado com a indicação de como convertê-lo. Nomes de arquivo e de pasta, flags das CLIs e identificadores continuam em inglês.

```bash
python -m mapeval                                   # pontua os quatro relatórios arquivados (cerca de 10 s)
python -m mapeval --show-losses                     # lista também cada linha retida, perdida ou com coluna errada
python -m mapeval --client oracle                   # um cliente; repita --client para mais
python -m mapeval --strict-key-only                 # pontua contra o gabarito exatamente como o cliente o escreveu
python -m pytest -q                                 # 343 testes, offline, cerca de 8 s
```

## O gabarito e seus limites

Há duas fontes de verdade, e nenhuma cobre muito.

### 1. Gabaritos (arquivos de-para)

Dois clientes mandaram um de-para: uma cópia do modelo base em que escreveram, acima de cada coluna do modelo que conseguem fornecer, a letra da coluna deles que a alimenta (`Coluna FY`).

| Cliente | Gabarito | Colunas do modelo no gabarito |
|---|---|---|
| datasul | `data/keys/de_para_datasul.xlsx` | 30 |
| oracle | `data/keys/de_para_oracle.xlsx` | 26 |
| **total** | | **56** |

`load_answer_key`, em `mapeval/truth.py`, lê só a linha 1. A posição da célula dá o número da coluna do modelo, e `parse_coluna_annotation`, em `mapeval/excel.py`, transforma `Coluna FY` (com espaços no fim, se houver) no número de uma coluna do cliente. A linha 2 repete os cabeçalhos do modelo e não é lida. Da linha 3 em diante vêm os dados do próprio cliente sob as colunas anotadas, e o mapeval também não os lê.

`data/keys/de_para_procosa.xlsx` não é gabarito. A linha 1 dele diz `ITEM`/`NOTA` (uma especificação da granularidade do registro), então não gera nenhum par de colunas. sap_terumo não tem gabarito.

Os gabaritos têm quatro limites.

- **São pequenos.** Uma coluna vale 1,8 ponto percentual no total de 56 colunas e de 3 a 4 pontos percentuais no número de um cliente só. **Trate todo recall como ±2 colunas.** Uma variação de uma ou duas colunas, sozinha, não é sinal. Veja quais linhas mudaram e por quê.
- **Estão parcialmente errados.** Em quatro linhas o gabarito está errado e o mapeador está certo. `KEY_CORRECTIONS`, em `mapeval/truth.py`, corrige essas linhas:

  | Cliente | Coluna do modelo | O gabarito diz | Corrigido para | Por quê |
  |---|---|---|---|---|
  | oracle | m213 Aliquota PIS | c37 | c36 | c37 é a alíquota da COFINS; c36 é a do PIS. |
  | oracle | m220 CST COFINS | c39 | c40 | O par de CST está trocado no gabarito. |
  | oracle | m221 CST PIS | c40 | c39 | A mesma troca. |
  | datasul | m184 Aliquota COFINS | c99 | c188 | c188 fecha o trio fiscal com o bloco m52–m55 do próprio gabarito. |

  `--strict-key-only` desliga as correções. Hoje elas valem exatamente +4 entregues e +4 encontrados (28 → 32 e 39 → 43), e `tests/test_mapeval.py` fixa a diferença dos encontrados.
- **Não dizem nada sobre as outras colunas do modelo.** Uma coluna do modelo que o cliente não anotou sai como *indeterminada*. O gabarito não consegue confirmar que um `sem_correspondencia` está certo, nem julgar um aceite fora das suas 56 colunas.
- **Às vezes atribuem uma coluna do cliente a duas colunas do modelo.** No datasul, c8 alimenta m39 Região e m147 Municipio Inicio. No oracle, c23 alimenta m100 Aliquota ICSP e m190 Aliquota ICMS. O mapeador tira uma coluna do cliente do conjunto disponível (`available`) assim que ela é aceita, e `apply_invoice_mapping.py` se recusa a entregar a mesma coluna do cliente duas vezes. Então, com as regras atuais, no máximo 54 das 56 colunas do gabarito podem ser entregues.

### 2. Pareceres do revisor

Um revisor pode dar parecer sobre uma linha da aba `Mapeamentos` de um relatório (`parecer_revisor` = `correto`, `incorreto` com `coluna_cliente_correta`, ou `inexistente`). Só um relatório tem pareceres: `data/reports/mapping_sap_procosa_full.xlsx` tem 26 (15 `correto`, 2 `incorreto` com a coluna corrigida, 9 `inexistente`), e as 26 observações do revisor (`observacao_revisor`) estão como foram escritas. `apply_invoice_mapping.py` respeita os pareceres, digitados em português; um parecer em inglês, como os do formato antigo, é recusado. O mapeval os ignora: só conta uma linha como entregue quando a `situacao` dada pelo próprio mapeador é `aceito`. A verificação de fabricação tem o mesmo ponto cego: nunca olha as linhas confirmadas pelo revisor (ver [KNOWN_ISSUES.md, item 2.6](KNOWN_ISSUES.md#26-o-mapeval-ignora-os-pareceres-do-revisor)).

## Como o mapeval pontua um relatório

`python -m mapeval` só lê. Nunca roda o mapeador e não grava arquivos. Para cada cliente de `CLIENTS`, em `mapeval/cli.py`, ele lê três planilhas sob `--root`: a exportação do cliente em `data/clients/`, o relatório de mapeamento arquivado `data/reports/mapping_<cliente>_full.xlsx` e, para datasul e oracle, o gabarito em `data/keys/`.

`read_mappings`, em `mapeval/score.py`, localiza a aba `Mapeamentos` com `report_schema.find_sheet`, confere o cabeçalho com `report_schema.internal_header_index` e devolve as linhas cruas com um índice pelos nomes internos dos campos. `score_report` lê cada linha com `_score_row`, que converte `situacao` e `metodo` para o vocabulário interno (`aceito` → `accepted`, `catalogo` → `catalog`) com `report_schema.internal_value` antes de qualquer comparação. Um relatório no formato antigo, em inglês (a aba `Mappings`, o cabeçalho `model_column_number`), é recusado com `ValueError` e a indicação de como convertê-lo; um relatório meio traduzido, por exemplo com um cabeçalho em inglês numa aba em português, também é recusado, em vez de ser lido pela metade.

`score_report` lê a aba pela **posição da linha**: a linha de dados *n* é a coluna do modelo *n*. A célula `numero_coluna_modelo` não é lida, então uma aba que alguém ordenou ou filtrou à mão é pontuada errado, sem nenhum erro ([KNOWN_ISSUES.md, item 2.8](KNOWN_ISSUES.md#28-o-mapeval-casa-as-colunas-do-modelo-pela-posição-nunca-pelo-nome)). De cada linha, `_score_row` tira `coluna_modelo` (o nome, só para imprimir), `numero_coluna_cliente`, `situacao`, `metodo`, `confianca_final` e `prova_aritmetica`.

### Classes de resultado

`classify`, em `mapeval/outcomes.py`, atribui um resultado a cada coluna do modelo. `score_report` passa a ela, para cada linha, a coluna do cliente proposta, a coluna que o gabarito indica (`expected_client_column`, `None` quando a coluna do modelo está fora do gabarito), se a coluna proposta está vazia e se a linha é entregue (`delivered`, verdadeiro quando a `situacao` é `aceito`). As checagens rodam nesta ordem, e vale a primeira que casar. Os valores de `Outcome` continuam em inglês no código; o rótulo em português vem de `report_schema.ENUMS["outcome"]`.

| # | Resultado (`Outcome`) | Rótulo | Condição |
|---|---|---|---|
| 1 | `FABRICATED` | `fabricado` | A `situacao` da linha é `aceito` e a coluna do cliente não tem nenhuma célula preenchida. É checado primeiro, então vale mesmo quando o gabarito concorda. |
| 2 | `UNDETERMINED` | `indeterminado` | A coluna do modelo não está no gabarito, faça o mapeador o que fizer. |
| 3 | `MISSED` | `perdido` | O gabarito indica uma coluna e o mapeador não propôs nenhuma. |
| 4 | `CORRECT` / `WRONG_COLUMN` | `correto` / `coluna_errada` | A proposta é igual à coluna do gabarito, ou não é. |

O resumo de cada cliente imprime `CORRETO`, `COLUNA_ERRADA`, `PERDIDO` e `FABRICADO` em maiúsculas; `--show-losses` usa `perdido` e `coluna_errada`. `indeterminado` não aparece na saída: os aceites indeterminados são contados na linha `sem_veredito`.

"Nenhuma célula preenchida" (coluna vazia) é calculado por `_vacuous_client_columns`, em `mapeval/score.py`: nenhum valor não vazio sob o cabeçalho, na primeira aba da exportação. Uma coluna toda zerada *não* é vazia, porque uma operação isenta tem base de cálculo zero legitimamente.

Não existe resultado de "ausência correta": o gabarito só anota as colunas que o cliente tem, então nunca afirma que uma coluna do modelo falta na exportação, e um `sem_correspondencia` fora dele não pode ser confirmado (ver [os limites do gabarito](#1-gabaritos-arquivos-de-para)). Uma coluna fora do gabarito só deixa de ser `UNDETERMINED` quando é um aceite em coluna vazia (checagem 1); hoje todas são `UNDETERMINED`: 197 no datasul e 201 no oracle.

As linhas em `revisao` também são classificadas. Uma proposta em `revisao` sobre uma coluna vazia não é `FABRICATED`, porque o apply não a entrega (a não ser com `--include-review`). Ela segue para as checagens normais. `Score.vacuous_proposals` conta essas propostas (datasul 6, oracle 0), mas a CLI não as imprime.

### O que cada número significa

A saída de `python -m mapeval` para o datasul, e o total, como saíram em 2026-10-09:

```text
datasul  presentes=30  (nunca 227)
  CORRETO           24      recall sobre presentes  80,0%
  COLUNA_ERRADA      1
  PERDIDO            5
  FABRICADO          0
  aceitos           19      precisão (limite inferior)  89,5%
  ENTREGUES         17      recall entregue  56,7%   <-- o que o cliente recebe
  retidos            7      coluna certa encontrada, retida pelo limiar de aceite
  sem_veredito       2      aceitos que o gabarito não consegue verificar
  aceites independentes de cabeçalho  36,8%   <-- métrica de generalização
  censo de métodos {'catalogo': 1, 'nome': 15, 'dados': 3}
  correções do gabarito aplicadas: [184]
...
TOTAL COM GABARITO  encontrados 43/56 (76,8%)   ENTREGUES 32/56 (57,1%)   fabricações 0
                    11 mapeamentos corretos encontrados, mas não entregues - rode com --show-losses para listá-los
```

| Linha impressa | Definição |
|---|---|
| `presentes=` | O denominador: as colunas do modelo que o gabarito do cliente anota (`AnswerKey.denominator`). |
| `ENTREGUES` / `recall entregue` | **Entregues.** Linhas `CORRETO` cuja `situacao` é `aceito`, divididas pelas colunas do gabarito. É o que o apply põe na planilha mapeada que o cliente recebe, e **é o número que descreve o produto.** |
| `CORRETO` / `recall sobre presentes` | **Encontrados.** Linhas cuja proposta (`aceito` *ou* `revisao`) bate com o gabarito, divididas pelas colunas do gabarito. Mede a recuperação. |
| `retidos` | Linhas `CORRETO` cuja `situacao` não é `aceito`: o mapeador achou a coluna certa e o limiar a reteve. `--show-losses` as lista, da maior confiança para a menor. |
| `aceitos` / `precisão (limite inferior)` | Aceites `CORRETO` divididos por todos os aceites. Aceites sem veredito contam como errados, então um aceite novo não verificado baixa o limite. |
| `sem_veredito` | Aceites em colunas do modelo fora do gabarito. O gabarito não consegue dizer se estão certos. |
| `aceites independentes de cabeçalho` | A métrica de generalização, na definição do mapeval (ver [abaixo](#a-métrica-de-generalização)). |
| `censo de métodos` | Aceites por `metodo`: `nome` é aceite de cabeçalho da primeira passagem, `dados` é decisão da segunda passagem, `catalogo` é decisão da segunda passagem por um catálogo oficial de códigos distintivo. |
| `correções do gabarito aplicadas` | Quais linhas de `KEY_CORRECTIONS` foram usadas. |
| `TOTAL COM GABARITO` | Encontrados e entregues somados nos clientes com gabarito, mais o total de fabricações. A linha seguinte diz quantas colunas corretas foram encontradas e não entregues. |

O denominador é sempre o número de colunas do gabarito. Nunca divida por 227. O modelo base é um superconjunto de propósito, e a maioria das colunas dele legitimamente não consta na exportação de um cliente qualquer.

### Fabricação e o código de saída

`main`, em `mapeval/cli.py`, devolve 1 se achou alguma fabricação e 0 caso contrário. Fabricações são as linhas `FABRICATED` dos clientes com gabarito mais os aceites em coluna vazia dos clientes sem gabarito. A fabricação é a falha que importa: uma `Base CBS` fabricada é muito pior que uma vazia.

É o único bloqueio automático. Código de saída 0 **não** quer dizer que a mudança é segura. Uma mudança que acrescenta aceites errados em colunas preenchidas fora do gabarito aumenta `sem_veredito` e baixa o limite de precisão, mas o código de saída continua 0. Leia essas duas linhas antes de confiar numa saída limpa.

O caminho de saída foi conferido em 2026-10-09 numa cópia do relatório do datasul em que uma linha `revisao` sobre coluna vazia (m32 ← c280) foi marcada `aceito`. A pontuação da cópia imprimiu `FABRICADO          1   <-- BLOQUEANTE` e a linha `FABRICAÇÃO m32 'Nº da Nota Fiscal' <- c280 (0 linhas preenchidas, final=0,9176)`, e saiu com 1.

### Clientes sem gabarito

Para sap_procosa e sap_terumo o mapeval não imprime nenhum número de precisão ou recall:

```text
sap_procosa  SEM GABARITO — nenhum número de precisão ou recall seria honesto aqui.
  aceitos  45   pendências de revisão  45   aceites em coluna vazia 0
```

`aceitos` conta as linhas com `situacao` = `aceito`. `aceites em coluna vazia` conta as linhas aceitas cuja coluna do cliente está vazia, e elas contam como fabricação para o código de saída. As `pendências de revisão` são a contagem de aceitos impressa de novo. Não contam linhas em `revisao` nem pareceres do revisor (ver [KNOWN_ISSUES.md, item 2.5](KNOWN_ISSUES.md#25-o-mapeval-imprime-a-contagem-de-aceitos-como-pendências-de-revisão)).

### Opções

| Opção | Efeito |
|---|---|
| `--client CLIENTE` | Pontua só este cliente. Repita a opção para mais de um. O nome precisa ser uma chave de `CLIENTS` (`datasul`, `oracle`, `sap_procosa`, `sap_terumo`). Qualquer outro nome para com um traceback de `KeyError`. |
| `--show-losses` | Para os clientes com gabarito, lista cada linha retida (`retido`, com `final=` e a situação, por exemplo `(revisao)`), depois cada linha `perdido` e `coluna_errada`, com a coluna `escolhida` e a `esperada`. |
| `--strict-key-only` | Não aplica `KEY_CORRECTIONS`. |
| `--root PASTA` | Resolve todo caminho de planilha de `CLIENTS` a partir de `PASTA` em vez de `.`. Use para pontuar relatórios candidatos sem tocar em `data/reports/` (ver [o protocolo](#protocolo-para-uma-mudança-proposta)). Se rodar de fora do repositório, aponte também o `PYTHONPATH` para o repositório. |

Quando o arquivo de relatório não existe, um cliente sem gabarito é pulado em silêncio, mas um cliente com gabarito para com `FileNotFoundError`. Quando você aponta `--root` para uma pasta com só alguns relatórios, nomeie esses clientes com `--client`.

Uma execução completa leva cerca de 10 s. Quase tudo é a varredura de colunas vazias das duas exportações grandes, datasul (8,9 MB) e sap_procosa (9,8 MB), cerca de 5 s cada. Cada varredura fica em cache dentro do processo.

## Linha de base (medida em 2026-10-09)

Produzida por `python -m mapeval` e `python -m mapeval --show-losses` sobre os relatórios arquivados, que foram gerados online e depois migrados para o esquema em português sem reexecutar o mapeador:

| Cliente | Colunas no gabarito | **Entregues** | Encontrados | Retidos | Coluna errada | Perdidos | Fabricados | Aceitos | Precisão (limite inferior) | Sem veredito |
|---|---|---|---|---|---|---|---|---|---|---|
| datasul | 30 | **17 (56,7%)** | 24 (80,0%) | 7 | 1 | 5 | 0 | 19 | 89,5% | 2 |
| oracle | 26 | **15 (57,7%)** | 19 (73,1%) | 4 | 1 | 6 | 0 | 18 | 83,3% | 3 |
| **total com gabarito** | 56 | **32 (57,1%)** | 43 (76,8%) | 11 | 2 | 11 | 0 | 37 | 86,5% | 5 |

Sem gabarito: sap_procosa 45 aceitos, sap_terumo 67 aceitos, nenhum aceite em coluna vazia nos dois. Código de saída 0.

Com `--strict-key-only`, o total com gabarito fica em 28/56 entregues (50,0%) e 39/56 encontrados (69,6%).

Nenhum dos 37 aceites com gabarito é `COLUNA_ERRADA`, então a precisão nos aceites que o gabarito *consegue* checar é 32/32. Os 5 que deixam o limite inferior abaixo de 100% são os sem veredito.

### As 11 linhas corretas retidas

Todas são linhas em `revisao` abaixo de 0,90. Cinco estão exatamente em 0,8999. Esse valor não é hesitação do modelo. Na segunda passagem, quando nem o cabeçalho nem a evidência objetiva dos dados são decisivos, `_local_final_confidence`, em `invoice_mapper.py`, limita a `confianca_final` ao teto `MapperConfig.clamp_ceiling` (`final_threshold - CEILING_GAP`, ou seja, `final_threshold - 0.0001`), então o limiar nunca aceita uma linha desse caminho.

| Cliente | Coluna do modelo | Proposta | `confianca_final` |
|---|---|---|---|
| datasul | m45 Outras ICMS | c16 | 0,8999 |
| datasul | m50 Isentas IPI | c19 | 0,8999 |
| datasul | m51 Outras IPI | c20 | 0,8999 |
| datasul | m52 Base PIS | c181 | 0,8999 |
| datasul | m210 Valor Total do Item | c90 | 0,891 |
| datasul | m44 Isentas ICMS | c15 | 0,8701 |
| datasul | m30 Data de lançamento | c3 | 0,8612 |
| oracle | m210 Valor Total do Item | c20 | 0,8999 |
| oracle | m195 Descrição | c13 | 0,8823 |
| oracle | m34 CFOP | c16 | 0,8547 |
| oracle | m31 Nº de documento | c8 | 0,8339 |

### Outras perdas

- **datasul.** Perdidos: m31 Nº de documento (c4), m39 Região (c8), m147 Municipio Inicio (c8), m194 Nº item do documento (c67), m211 Nome do Fornecedor do Local de Início (c6). Coluna errada: m213 Aliquota PIS, que propôs c96 onde o gabarito quer c187 (`revisao`, 0,8999).
- **oracle.** Perdidos: m30 Data de lançamento (c2), m37 Nome (c1), m99 Base ICSP (c28), m100 Aliquota ICSP (c23), m101 Valor ICSP (c24), m211 Nome do Fornecedor do Local de Início (c7). Coluna errada: m194 Nº item do documento, que propôs c15 onde o gabarito quer c12 (`revisao`, 0,8752).

### Os 5 aceites sem veredito

- datasul: m161 Despesas ← c76, m165 Código do Item ← c69.
- oracle: m41 Base ICMS ← c28, m42 Valor ICMS ← c24, m199 Quantidade ← c17.

O par do oracle precisa de uma pessoa. Os cabeçalhos do próprio cliente para c28 e c24 são `BASE ICMS` e `VALOR ICMS`, e o mapeador os aceitou nas colunas de ICMS. O gabarito põe essas mesmas colunas nas colunas de ICSP do modelo (m99, m101), que por isso saem como `PERDIDO`. O mapeval pontua os dois lados separadamente e não os relaciona ([KNOWN_ISSUES.md, item 2.7](KNOWN_ISSUES.md#27-aceites-fora-do-gabarito-não-são-conferidos-contra-o-gabarito-oracle-m41m42)). ICSP é uma família de tributo separada no modelo, e a trava de conflito de família do mapeador se recusa, de propósito, a casar um cabeçalho de ICMS com uma coluna de ICSP. Não "conserte" isso com uma regra que mande cabeçalhos de ICMS para ICSP. Isso ganharia pontos de gabarito num cliente codificando o vocabulário dele, que é exatamente o ajuste à mão que as regras do projeto proíbem (a docstring de `Score.undetermined_accepts`, em `mapeval/score.py`, dá este exemplo).

## A métrica de generalização

Os quatro clientes de amostra são material de referência. A produção vai ver ERPs que ninguém aqui viu. A pergunta que antecipa o próximo cliente é quantos aceites se apoiam em evidência que não seja o texto do cabeçalho do cliente. Esta página chama essa fração dos aceites de métrica de generalização. Aumentá-la é um objetivo em si. Há duas definições no código, e elas discordam:

- **mapeval** (`Row.header_independent`, em `mapeval/score.py`, impressa como `aceites independentes de cabeçalho`). Um aceite conta se a `prova_aritmetica` é decisiva, ou se o `metodo` é `catalogo`. Decisiva quer dizer não vazia e sem o marcador de prova não decisiva: `report_schema.proof_is_decisive` reconhece "não distingue esta coluna". Uma prova que dois blocos disjuntos do mesmo tributo satisfazem não é evidência para nenhum dos dois.
- **mapping_steps** (`mapping_steps.py`). Toda etapa de evidência tem o campo `transfers`. `perfil`, `identificador`, `catalogo` e `aritmetica` se transferem; `resolvedor_ia`, `lexico` e `semantico`, não. O `decidido_por` de uma linha é a etapa de apoio mais forte, na ordem `resolvedor_ia < lexico < semantico < perfil < identificador < catalogo < aritmetica` (`deciding_step`). No código, essas funções trabalham com os ids internos (`resolver`, `lexical`, `semantic`, `profile`, `identifier`, `catalog`, `arithmetic`); uma célula lida de um relatório em português passa antes por `report_schema.steps_to_internal`. A métrica é a fração de aceites cujo `decidido_por` está em `TRANSFERRING_STEP_IDS`. Uma função relacionada, `rests_only_on_headers`, pergunta se o `apoiado_por` cita ao menos uma etapa de evidência e nenhuma que se transfere.

As duas diferem quando um aceite de cabeçalho da primeira passagem é *corroborado* por um catálogo. mapping_steps o conta como transferível (`decidido_por` = `catalogo`). O mapeval, não (`metodo` = `nome`, sem prova aritmética). Medido nas linhas aceitas dos relatórios atuais:

| Cliente | Aceitos | Definição do mapeval | `decidido_por` se transfere |
|---|---|---|---|
| datasul | 19 | 7 (36,8%), impressa | 8 (42,1%). Difere em m204 Nº do NCM (`semantico + catalogo`) |
| oracle | 18 | 6 (33,3%), impressa | 6 (33,3%) |
| sap_procosa | 45 | não impressa; 0, porque o relatório não tem a coluna `prova_aritmetica` | 17 (37,8%) |
| sap_terumo | 67 | não impressa; 15 (22,4%) | 16 (23,9%). Difere em m34 CFOP |

Alguns aceites têm `decidido_por` = `nao_registrado` (datasul 2, sap_procosa 3, sap_terumo 1). Uma evidência objetiva os sustentou, mas o relatório completado pelo antigo preenchimento retroativo (backfill) não sabe dizer qual etapa foi. As duas definições os contam como não transferíveis. Enquanto as duas definições não forem unificadas, cite as duas e diga qual é qual. Ver [KNOWN_ISSUES.md, item 2.4](KNOWN_ISSUES.md#24-duas-definições-da-métrica-de-generalização).

Quando duas mudanças recuperam a mesma coluna, prefira a que se apoia em evidência que se transfere: a aritmética fiscal (base de cálculo × alíquota = valor do imposto), os catálogos oficiais de códigos e os dígitos verificadores de CPF/CNPJ e da chave de acesso da NF-e. Evite a que acrescenta vocabulário de cabeçalho.

## Por que o limiar de aceite fica em 0,90

As linhas retidas sugerem o caminho óbvio: baixar o limiar. A tabela abaixo mostra o que isso entregaria. É uma simulação sobre as pontuações arquivadas. No limiar *g*, uma linha é entregue se hoje é `aceito`, ou se é uma proposta em `revisao` com *g* ≤ `confianca_final` < 0,90. As linhas em `revisao` com 0,90 ou mais são retidas por uma trava, não pelo limiar, e ficam de fora. Hoje são 5 (4 no datasul, 1 no sap_terumo), todas apontando para colunas vazias do cliente. As linhas são então conferidas contra os gabaritos, com as correções aplicadas.

**Medido em 2026-10-09. Clientes com gabarito (datasul + oracle):**

| Limiar | Linhas que seriam entregues | Certas pelo gabarito | Erradas pelo gabarito | Sem veredito do gabarito | Coluna do cliente usada duas vezes |
|---|---|---|---|---|---|
| 0,90 (hoje) | 37 | 32 | 0 | 5 | 0 |
| 0,89 | 59 | 38 | 1 | 20 | 3 |
| 0,86 | 114 | 41 | 2 | 71 | 27 |
| 0,83 | 158 | 43 | 2 | 113 | 55 |

"Usada duas vezes" conta as linhas entregues que reaproveitam uma coluna do cliente que outra linha entregue do mesmo cliente já usa.

**Clientes sem gabarito (sap_procosa + sap_terumo)**, que ninguém consegue julgar. Linhas que seriam entregues: 112 / 180 / 223 / 253 nos mesmos quatro limiares, com 0 / 11 / 28 / 45 usadas duas vezes. Dessas linhas, 0 / 3 / 8 / 10 apontam para uma coluna do cliente sem dado, então seriam fabricações. No lado com gabarito, uma linha assim aparece em 0,83.

Leia assim. Descer a 0,83 recupera as 11 colunas corretas retidas (32 → 43 entregues). Também entrega mais 121 linhas com gabarito, das quais 2 sabidamente erradas e 108 que ninguém consegue conferir. Nos clientes sem gabarito, entrega mais 141 linhas não verificáveis, 10 delas em colunas vazias. A precisão nas linhas que o gabarito consegue checar cai de 32/32 para 43/45. Onde duas linhas entregues usam a mesma coluna do cliente, no máximo uma pode estar certa. Hoje o limiar não entrega nada que o gabarito mostre estar errado. Isso vale mais que 11 colunas.

Reexecutar o mapeador com `--final-threshold 0.83` é outro experimento. O teto de 0,8999 é `final_threshold - 0.0001`, e a evidência objetiva conta como decisiva a partir de `final_threshold`. Um valor menor na opção move o teto e a régua da evidência, não só o ponto que as linhas precisam ultrapassar. E exige uma execução online.

**O que destrava é o parecer do revisor, não uma constante mais frouxa.** sap_procosa é a prova medida. 26 pareceres renderam 17 colunas entregues a mais: 15 propostas em revisão confirmadas e 2 linhas corrigidas. O apply entrega hoje 62 colunas (45 aceitas + 17 confirmadas pelo revisor). Uma execução do apply para um arquivo de rascunho em 2026-10-09 imprimiu `colunas_mapeadas=62`. A aba `Fila de Revisão` ordena as propostas retidas justamente para esse trabalho. Os relatórios gravados pelo código atual têm essa aba; o relatório mais antigo de sap_procosa, não (a migração manteve o layout dele, só com `Mapeamentos` e `Metadados da Execução`).

O mapeval não tem opção para esta curva. Meça de novo com o trecho abaixo antes de alguém propor mudar o limiar. Ele lê os relatórios arquivados e leva cerca de 11 s.

```bash
python - <<'EOF'
from collections import Counter

import report_schema
from mapeval.cli import CLIENTS
from mapeval.score import _vacuous_client_columns, read_mappings
from mapeval.truth import load_answer_key

def mappings(report):
    rows, col = read_mappings(report)            # aba Mapeamentos
    def status(row):                             # aceito -> accepted, revisao -> review
        return report_schema.internal_value("mappings", "status", row[col["status"]])
    return [(m, r[col["client_column_number"]], status(r), r[col["final_confidence"]] or 0.0)
            for m, r in enumerate(rows[1:], start=1)]

for gate in (0.90, 0.89, 0.86, 0.83):
    keyed, unkeyed = Counter(), Counter()
    for name, (client, report, key_file) in CLIENTS.items():
        key = load_answer_key(key_file, name).present if key_file else None
        empty = _vacuous_client_columns(client)
        tally = keyed if key else unkeyed
        ships = [(m, int(c)) for m, c, status, final in mappings(report)
                 if status == "accepted" or (status == "review" and c is not None and gate <= final < 0.90)]
        tally["entregues"] += len(ships)
        if key:
            tally["certas"] += sum(key.get(m) == c for m, c in ships)
            tally["erradas"] += sum(m in key and key[m] != c for m, c in ships)
            tally["sem veredito"] += sum(m not in key for m, _ in ships)
        tally["usada duas vezes"] += len(ships) - len({c for _, c in ships})
        tally["coluna vazia"] += sum(c in empty for _, c in ships)
    print(f"{report_schema.format_decimal_br(gate, 2)}  com gabarito {dict(keyed)}")
    print(f"      sem gabarito {dict(unkeyed)}")
EOF
```

## Protocolo para uma mudança proposta

As [regras do projeto](../README.md#regras-do-projeto) valem aqui. Em resumo:

- O escopo é o algoritmo de correspondência.
- Os quatro clientes de amostra são exemplos de referência, nunca conjunto de treino. Nenhum código pode ramificar pelo nome de um cliente, nada se ajusta à mão contra eles, e toda mudança é validada deixando um cliente de fora. Os gabaritos são teste de regressão.
- Prefira evidência que se transfere para um ERP nunca visto.
- A fabricação é a falha que importa, e a precisão do aceite vale mais que o recall. O limiar de 0,90 fica; o caminho para entregar a faixa retida é o parecer do revisor.
- Cobertura parcial é o desenho. Nunca use 227 como denominador, e nunca chame de "faltante", "lacuna" ou "incompleta" uma coluna que o cliente não tem.
- Cite os ENTREGUES antes dos encontrados.
- O `data/model.xlsx` não muda por enquanto.
- O repositório tem dados reais de clientes e é só para uso interno.

### 1. Rode os testes e anote os números atuais

```bash
python -m pytest -q
python -m mapeval --show-losses > /tmp/mapeval-antes.txt
```

### 2. Gere relatórios de base e candidatos fora de `data/reports/`

Nunca grave relatórios candidatos por cima dos arquivados enquanto avalia. O mapeval lê nomes de arquivo fixos, então dê a cada execução uma raiz própria com a mesma estrutura:

```bash
run_all() {   # uso: run_all <pasta raiz> [opções do mapeador...]
  root=$1; shift
  mkdir -p "$root/data/reports"
  ln -sf "$PWD/data/clients" "$PWD/data/keys" "$root/data/"
  for pair in datasul:datasul_maio.xlsx oracle:oracle.xlsx \
              sap_procosa:sap_procosa.XLSX sap_terumo:sap_terumo.XLSX; do
    python invoice_mapper.py "data/clients/${pair#*:}" "$@" \
      --output "$root/data/reports/mapping_${pair%%:*}_full.xlsx"
  done
}

BASE=/tmp/mapeval-base   # qualquer pasta fora do repositório
CAND=/tmp/mapeval-cand

run_all "$BASE" --offline   # com o código ANTES da mudança (por exemplo, depois de `git stash`)
run_all "$CAND" --offline   # com a mudança aplicada
python -m mapeval --root "$BASE"
python -m mapeval --root "$CAND"
```

Para cada cliente, o mapeador imprime `Arquivo gravado: ...` e a contagem por situação, por exemplo `aceito=12 revisao=215 sem_correspondencia=0` no datasul offline. Os relatórios saem no esquema em português.

Esses relatórios contêm valores de amostra de dados reais de clientes. Mantenha as raízes de rascunho na sua máquina e nunca as inclua num commit (ver [dados confidenciais](../README.md#dados-confidenciais)).

Compare execuções equivalentes. Os relatórios arquivados foram gerados online: datasul, oracle e sap_terumo em 2026-08-25; sap_procosa em 2026-08-04, por um código mais antigo (sem a coluna `prova_aritmetica`), então os 45 aceites dele não servem de base para o código atual. Os quatro foram depois migrados para o esquema em português sem reexecutar o mapeador, então as decisões são as das execuções online originais. Sempre gere `BASE` com o código sem a mudança, no mesmo modo de `CAND`.

### 3. Compare as linhas, não só os totais

Uma mudança pode perder uma coluna correta e ganhar outra, deixando os totais iguais. Este trecho imprime o que mudou em cada cliente: pares entregues e encontrados nos clientes com gabarito, pares aceitos nos sem gabarito.

```bash
python - "$BASE" "$CAND" <<'EOF'
import sys
from pathlib import Path

import report_schema
from mapeval.cli import CLIENTS
from mapeval.outcomes import Outcome
from mapeval.score import read_mappings, score_report
from mapeval.truth import load_answer_key

base, cand = Path(sys.argv[1]), Path(sys.argv[2])

def accepted(root, report):
    rows, col = read_mappings(root / report)            # aba Mapeamentos
    return {(m, int(r[col["client_column_number"]]))
            for m, r in enumerate(rows[1:], start=1)
            if report_schema.internal_value("mappings", "status", r[col["status"]]) == "accepted"}

def correct(root, client, report, key):
    score = score_report(root / report, root / client, key)
    found = score.correct_pairs
    delivered = {(r.model_column, r.client_column) for r in score.rows
                 if r.outcome is Outcome.CORRECT and r.status == "accepted"}
    return delivered, found

for name, (client, report, key_file) in CLIENTS.items():
    if not (base / report).exists() or not (cand / report).exists():
        continue
    if key_file is None:
        pairs = {"aceitos": (accepted(base, report), accepted(cand, report))}
    else:
        key = load_answer_key(base / key_file, name)
        (d0, f0), (d1, f1) = correct(base, client, report, key), correct(cand, client, report, key)
        pairs = {"entregues": (d0, d1), "encontrados": (f0, f1)}
    for label, (old, new) in pairs.items():
        print(f"{name:12} {label:11} {len(old):3} -> {len(new):3}"
              f"  perdeu {sorted(old - new)}  ganhou {sorted(new - old)}")
EOF
```

Os pares são `(coluna do modelo, coluna do cliente)`. Passe `.` como primeiro argumento para comparar com os relatórios arquivados.

### 4. Critérios de aceitação

Uma mudança só é melhoria se todas estas condições forem atendidas:

- **As fabricações continuam em 0.** O mapeval sai com 0 em `CAND`.
- **Os entregues não caem** em nenhum dos dois clientes com gabarito. Todo par perdido tem explicação. Os encontrados vêm em segundo lugar.
- **Os aceites sem veredito e o limite inferior de precisão não pioram sem explicação.** Todo aceite novo fora do gabarito é listado e conferido por alguém que conhece os cabeçalhos fiscais.
- **Nenhuma queda de aceitos em sap_procosa ou sap_terumo.** Eles não têm gabarito, então uma contagem que sobe também não prova melhoria. Todo par aceito novo precisa de uma olhada, e `aceites em coluna vazia` tem de continuar em 0.
- **A fração de generalização não cai**, nas duas definições. Se um ganho vem só de vocabulário de cabeçalho, diga isso.
- **A variação é maior que o ruído.** Uma variação de ±1–2 colunas no total com gabarito não é evidência por si só.
- **Os testes passam**, menos as fixações que você atualiza de propósito (ver [abaixo](#regenerar-os-relatórios-arquivados)).

### 5. Validação deixando um cliente de fora

Para cada mudança, anote quais linhas de qual cliente a motivaram. Depois julgue a mudança só nos outros três. Uma regra encontrada olhando o datasul precisa mostrar seu valor no gabarito do oracle, ou ao menos não fazer mal no oracle, no sap_procosa e no sap_terumo. Se ela só ajuda o cliente que a inspirou, é um ajuste àquela amostra: descarte. Justifique uma regra por algo fora das amostras: a legislação tributária, uma tabela oficial de códigos, um padrão nacional como a definição de um campo do SPED. Não abra um arquivo de-para para desenhar uma regra.

Nenhum código executável pode citar um cliente. Hoje esta busca não encontra nada, nem em comentários (o `mapeval/` fica de fora, porque `CLIENTS` nomeia os clientes de propósito):

```bash
grep -nEi 'datasul|oracle|procosa|terumo' invoice_mapper.py reference_catalog.py mapping_steps.py apply_invoice_mapping.py \
  report_schema.py cli_ptbr.py
```

### 6. Offline versus online

| | Offline (`--offline`) | Online (padrão) |
|---|---|---|
| Pontuação dos cabeçalhos | léxica (`LexicalHeaderScorer`) | embeddings da OpenAI |
| Evidência da segunda passagem (aritmética, catálogos, identificadores, resolvedor de IA) | **não é usada**. Toda coluna do modelo que sobra depois da primeira passagem vira `revisao` sobre o melhor candidato de cabeçalho, então na prática não há `sem_correspondencia` (0 nos quatro clientes) | usada, com gpt-4o-mini como resolvedor de IA |
| Língua das justificativas | português: todas as frases são escritas pelo mapeador | as frases do mapeador em português; as justificativas do resolvedor de IA em inglês, porque o prompt é em inglês (502 nos quatro relatórios arquivados) |
| Reprodutível | sim. Três execuções dos quatro clientes em 2026-10-09 gravaram abas `Mapeamentos`, `Colunas do Cliente sem Uso` e `Fila de Revisão` idênticas | não é garantido: o resolvedor pode mudar linhas em `revisao` de uma execução para outra |
| Custo e exposição de dados | nenhum | chamadas pagas de API. Todo cabeçalho do modelo e do cliente vai para embeddings. Cada coluna do modelo que chega ao resolvedor manda valores de amostra do modelo e até 8 de cada coluna candidata do cliente (`--sample-size`). As execuções arquivadas registram de 167 a 211 respostas do resolvedor por cliente. Ver [dados confidenciais](../README.md#dados-confidenciais) |
| Tempo (medido em 2026-10-09 no Mac de desenvolvimento) | datasul 36 s, oracle 17 s, sap_procosa 82 s, sap_terumo 13 s | não medido |

O A/B offline só vale para mudanças na primeira passagem (ver [KNOWN_ISSUES.md, item 2.15](KNOWN_ISSUES.md#215-o-modo-offline-é-outro-pipeline)): normalização dos cabeçalhos e pontuação léxica, as travas da primeira passagem, e a auditoria por catálogo e o rebaixamento pelo trio fiscal que vêm depois dela. Uma mudança na segunda passagem precisa de execuções online de `BASE` e de `CAND`. Como as execuções online não são reprodutíveis, inspecione as linhas que mudaram em vez de confiar numa diferença de uma coluna.

Linha de base offline do código atual, medida em 2026-10-09 com o `run_all` acima, **só para A/B offline**: ENTREGUES 26/56 com gabarito, encontrados 39/56, 0 fabricações; sap_procosa 27 aceitos, sap_terumo 47 aceitos. Encontrados offline quer dizer pouco: toda coluna do modelo recebe um palpite só de cabeçalho, então `PERDIDO` é sempre 0 e as perdas aparecem como `COLUNA_ERRADA`. Compare entregues e aceitos, e **nunca cite número offline como número do produto.**

## Regenerar os relatórios arquivados

Quando uma mudança é aceita, regenerar `data/reports/` tem estas consequências.

**Os pareceres do revisor se perdem.** `write_results`, em `invoice_mapper.py`, grava todas as colunas do revisor em branco e sobrescreve o arquivo de saída sem perguntar. Não há ferramenta que leve os pareceres de um relatório antigo para um novo. Sem `--output`, o mapeador grava `mapping_<nome do arquivo do cliente>.xlsx` na pasta atual (por exemplo `mapping_datasul_maio.xlsx`), então você precisa passar `--output data/reports/mapping_<cliente>_full.xlsx` explicitamente para substituir um relatório arquivado. **Nunca regenere `mapping_sap_procosa_full.xlsx`:** ele guarda os 26 pareceres e as 26 observações do revisor. A correção proposta para os pareceres perdidos está em [KNOWN_ISSUES.md, item 3.3](KNOWN_ISSUES.md#33-regenerar-um-relatório-apaga-os-pareceres-do-revisor). O antigo `backfill_mapping_steps.py` (removido depois do commit `5fe5092`) foi o precedente de acrescentar colunas a um relatório existente sem reexecutar o mapeador, e deixou um defeito nos relatórios arquivados ([KNOWN_ISSUES.md, item 1.1](KNOWN_ISSUES.md#11-resolvedor_ia-não-aparece-em-apoiado_por-em-três-relatórios-arquivados)). Depois de regenerar, rode o apply de novo para a planilha correspondente de `data/mapped/` (ver [Como regenerar as saídas](DATA.md#como-regenerar-as-saídas) em DATA.md); as planilhas saem com os nomes de aba em português, uma mudança incompatível para quem lê os nomes antigos (ver [Planilhas mapeadas](DATA.md#planilhas-mapeadas-datamapped)).

**Um relatório antigo se converte, não se regenera.** Um relatório no formato antigo, em inglês (versão 2), como os gravados antes da tradução, passa para o esquema em português com o `migrate_reports.py` do commit `5fe5092`, sem reexecutar o mapeador: nenhuma decisão, pontuação ou número muda, e os pareceres e observações do revisor ficam como estão. Foi assim que os quatro arquivados foram convertidos. O apply e o mapeval recusam o formato antigo; os comandos da conversão estão no [README](../README.md#comandos-do-dia-a-dia), e o que ela fez nos quatro relatórios arquivados, em [DATA.md](DATA.md#migração-dos-relatórios-arquivados).

**Os testes fixados falham por desenho.** `tests/test_mapeval.py` afirma os totais absolutos de hoje nos relatórios arquivados:

| Teste | Asserção |
|---|---|
| `test_harness_reproduces_the_corrected_baseline` | `total == 43` (encontrados) |
| `test_the_four_key_corrections_are_worth_exactly_four_columns` | `corrected - strict == 4` (as correções valem exatamente 4), e `(strict, corrected) == (39, 43)` |
| `test_the_accept_gate_withholds_eleven_correct_mappings` | `found - delivered == 11`, e `(found, delivered) == (43, 32)` |
| `test_every_withheld_correct_mapping_sits_below_the_accept_threshold` | toda linha correta retida tem `final_confidence < 0.90` |

Uma melhoria legítima precisa atualizar esses números de propósito, no mesmo commit dos relatórios regenerados, com os números de antes e de depois e os pares que mudaram na mensagem do commit. Atualize também a linha de base desta página e a situação no README. Duas invariantes devem sobreviver a qualquer melhoria e pedem investigação, não edição, se falharem: as correções valem exatamente 4 (`corrected - strict == 4`; o `(39, 43)` ao lado é uma fixação absoluta, que você atualiza) e nada entregue aponta para coluna vazia (`test_no_report_delivers_a_column_with_no_data`). O teste das retidas abaixo de 0,90 (`test_every_withheld_correct_mapping_sits_below_the_accept_threshold`) falha se uma trava, e não o limiar, passar a segurar uma linha correta. Esse caso também pede investigação. Os comentários não citam esses números (só o nome de `test_the_accept_gate_withholds_eleven_correct_mappings` diz onze); valem as asserções e `python -m mapeval`.

**A prova da tradução também fixa o comportamento.** `tests/test_model_input_golden.py` existe para provar que a tradução não mudou o que os modelos de IA recebem e por isso pode falhar com uma mudança deliberada de comportamento. Ele compara o que os modelos de IA recebem (prompt, esquema `DataMappingDecision`, entradas de embedding, payloads do resolvedor) e as decisões de uma execução pequena com `tests/fixtures/model_input_golden.json`. Só regenere esse arquivo (`python tests/test_model_input_golden.py --write`) quando o que os modelos recebem, ou as decisões dessa execução, mudarem de propósito, no mesmo commit e com a explicação; se o prompt mudar, mude junto o `PROMPT_VERSION`. Nunca regenere só para fazer o teste passar.

## Suíte de testes

```bash
python -m pytest -q                              # os 343 testes, cerca de 8 s
python -m pytest -q tests/test_mapeval.py        # um arquivo
python -m pytest -q -k vacuous                   # pelo nome (os nomes dos testes estão em inglês)
```

`pyproject.toml` define `pythonpath = ["."]` e `testpaths = ["tests"]`. A suíte rodou em 7,8 s em 2026-10-09 (Python 3.13.1, pytest 8.4.1). Cerca de 5,4 s disso é a primeira varredura de colunas vazias do datasul, em `test_harness_reproduces_the_corrected_baseline`. Nenhum teste usa a rede: os testes do pontuador e do resolvedor da OpenAI injetam clientes falsos, e a suíte passa com `OPENAI_API_KEY` ausente.

| Arquivo | Testes | Cobre |
|---|---|---|
| `tests/test_report_schema.py` | 118 | O esquema em português e a CLI em português (`cli_ptbr`): nomes de aba válidos no Excel, cabeçalhos em snake_case ASCII, nenhum nome em português igual a um interno (que é também o do formato antigo), valores distintos em cada enum; cobertura por introspecção (todo campo que o código grava tem nome em português); ida e volta interno → português → interno; recusa do formato antigo pelo apply e pelo mapeval, com o caminho da conversão, e de nomes dele num relatório em português; parecer do revisor só em português, nome de exibição, etapas, marcador da prova e vírgula decimal. 4 deles leem os relatórios arquivados e são pulados se eles não existem. |
| `tests/test_invoice_mapper.py` | 109 | O mapeador em tabelas sintéticas: aceite e margens da primeira passagem, exclusividade de coluna, normalização de cabeçalho e detecção de conflito e de papel, aritmética fiscal e descoberta do trio fiscal, a trava de coluna vazia, dígitos verificadores da chave de acesso e perfis de valores, o pontuador e o resolvedor da OpenAI com clientes falsos, as colunas do revisor e os `Metadados da Execução`, as abas `Colunas do Cliente sem Uso` e `Fila de Revisão`, as colunas de etapas (inclusive que uma execução offline nunca alega `semantico`), as frases em português com vírgula decimal, a gravação no esquema em português (`.xlsx` e `.csv`) com ida e volta pelo `report_schema`, e a CLI em português. |
| `tests/test_apply_invoice_mapping.py` | 38 | O apply sobre planilhas temporárias, com relatórios gravados em português: só as linhas aceitas e confirmadas são preenchidas, `--include-review`, validação do parecer do revisor (tolerante a caixa, acentos e espaços, e recusando os pareceres em inglês), a verificação de hash contra entradas alteradas (inclusive a recusa de metadados com nomes do formato antigo), valores de hora preservados, colunas vazias ocultas, os estados da `Cobertura`, o banner em português e a CLI em português. |
| `tests/test_mapeval.py` | 33 | Leitura das letras de coluna, a precedência de resultados em `classify`, a regra da prova não decisiva (sintética e nos aceites reais de datasul), recusa de relatório meio traduzido, a saída da CLI com vírgula decimal e os rótulos em português. 12 deles pontuam os relatórios arquivados reais e fixam a linha de base; são pulados se `data/keys/de_para_oracle.xlsx` não existe. |
| `tests/test_mapping_steps.py` | 28 | O registro de etapas: ids únicos, ordem da etapa decisiva, o resolvedor como a mais fraca e não transferível, toda etapa documentada, e células de etapas lidas de um relatório em português dando a mesma decisão. |
| `tests/test_model_input_golden.py` | 10 | A entrada dos modelos de IA, byte a byte, contra `tests/fixtures/model_input_golden.json`: o prompt do resolvedor e o esquema `DataMappingDecision`, as entradas de embedding (com cabeçalho em branco e repetido), os payloads do resolvedor (conflitos de cabeçalho, `Unnamed column N`, `(occurrence N)`, evidência de catálogo e aritmética) e as decisões de uma execução pequena de `map_columns`. |
| `tests/test_reference_catalog.py` | 7 | Carga dos catálogos de referência de `reference_data/`, normalização dos códigos (zeros à esquerda, sufixos de CFOP de ERP), cobertura distinta, um catálogo distintivo resgatando um cabeçalho enganoso como linha `revisao`, e a regra de que domínios curtos de CST não decidem sozinhos. |
| **total** | **343** | |

`update_reference_data.py` não tem testes.
