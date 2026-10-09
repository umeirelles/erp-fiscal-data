# Arquivos de dados e formatos

Esta página descreve cada arquivo de dados do repositório e cada arquivo que as ferramentas gravam: o modelo base, as exportações dos clientes, os de-para, os relatórios de mapeamento, as planilhas mapeadas e os catálogos de referência fiscais. Descreve também o esquema em português desses arquivos e o que fazer com um relatório antigo, gravado no formato antigo, em inglês.

Documentos relacionados:

- [README](../README.md): instalação e os comandos principais.
- [ARCHITECTURE.md](ARCHITECTURE.md): como o mapeador chega a cada decisão.
- [EVALUATION.md](EVALUATION.md): como os relatórios são pontuados contra os gabaritos.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md): defeitos que afetam os arquivos descritos aqui.

Todas as contagens abaixo foram medidas em 2026-10-09 nos arquivos de `data/`, já no esquema em português. A simplificação do código feita no mesmo dia não mudou nenhum arquivo de `data/` nem de `reference_data/`.

Os nomes de aba, coluna, chave e valor aparecem entre crases exatamente como `report_schema.py` os grava. Nomes de arquivo e de pasta, flags da CLI e identificadores do código continuam em inglês.

## Sumário

- [Termos](#termos)
- [Onde fica cada arquivo](#onde-fica-cada-arquivo)
- [Confidencialidade](#confidencialidade)
- [O modelo base: `data/model.xlsx`](#o-modelo-base-datamodelxlsx)
- [Exportações dos clientes: `data/clients/`](#exportações-dos-clientes-dataclients)
- [Arquivos de-para: `data/keys/`](#arquivos-de-para-datakeys)
- [Relatórios de mapeamento: `data/reports/`](#relatórios-de-mapeamento-datareports)
- [Planilhas mapeadas: `data/mapped/`](#planilhas-mapeadas-datamapped)
- [Esquema em português e esquema legado](#esquema-em-português-e-esquema-legado)
- [Catálogos de referência: `reference_data/`](#catálogos-de-referência-reference_data)
- [Como regenerar as saídas](#como-regenerar-as-saídas)

## Termos

| Termo | Significado |
|---|---|
| NF-e, chave de acesso | A chave de acesso da NF-e tem 44 dígitos e termina num dígito verificador (DV). |
| CNPJ / CPF | 14 e 11 dígitos, os dois com DV. |
| CFOP, NCM | Natureza da operação (4 dígitos) e classificação da mercadoria (8 dígitos). |
| CST / CSOSN | Código de situação tributária, com uma tabela por tributo; CSOSN é a tabela do Simples Nacional. |
| ICMS, IPI, PIS, COFINS, ISS | Os tributos atuais. |
| CBS, IBS, IS | Os tributos da reforma (LC 214/2025): a CBS federal, que substitui PIS e COFINS, o IBS de estados e municípios e o Imposto Seletivo. |
| de-para | Tabela que diz qual coluna do cliente alimenta qual coluna do modelo. |
| gabarito | De-para escrito pelo cliente e usado como resposta certa pelo `mapeval`. |
| trio fiscal | Base de cálculo × alíquota = valor do imposto, conferido linha a linha na exportação do cliente. |

## Onde fica cada arquivo

| Caminho | O que é | Quem grava | Quem lê |
|---|---|---|---|
| `data/model.xlsx` | O modelo base da consultoria: 227 colunas do modelo e 209 linhas de amostra | a consultoria (fixo) | `invoice_mapper.py`, `apply_invoice_mapping.py` |
| `data/clients/` | Quatro exportações reais de ERP | os clientes | o mapeador, o apply, `mapeval` |
| `data/keys/` | Três de-para escritos pelos clientes. Dois deles são gabaritos. | os clientes | `mapeval`, `tests/test_mapeval.py` |
| `data/reports/mapping_<cliente>_full.xlsx` | Relatórios de mapeamento, um por cliente | `invoice_mapper.py`, depois os revisores, o antigo `backfill_mapping_steps.py` e o antigo `migrate_reports.py` | `apply_invoice_mapping.py`, `mapeval`, `tests/test_mapeval.py` |
| `data/mapped/<cliente>_mapped.xlsx` | Entregáveis no layout de colunas do modelo | `apply_invoice_mapping.py` | a aplicação a jusante, os revisores |
| `reference_data/` | Retrato das tabelas fiscais oficiais | `update_reference_data.py` | `reference_catalog.py`, pelo mapeador |

Todo arquivo em `data/` e em `reference_data/` é versionado no git. As duas exportações SAP têm a extensão `.XLSX` em maiúsculas; digite-a exatamente assim nos comandos.

`mapeval/cli.py` (`CLIENTS`) e `tests/test_mapeval.py` (`KEYED`) fixam esses caminhos no código, e o mapeador e o apply usam `--model data/model.xlsx` por padrão. Se renomear ou mover uma planilha, atualize-os.

## Confidencialidade

As planilhas têm dados reais de clientes, inclusive CNPJ e CPF, chaves de acesso de NF-e, nomes de parceiros e números de documento. Isso vale para:

- as quatro exportações;
- os de-para;
- as linhas de amostra do `model.xlsx` (91% dos valores de `Nº ID fiscal 1` são CPFs válidos, e todos os valores de `Chave de acesso` são chaves de NF-e válidas);
- tudo o que deriva deles, inclusive as planilhas mapeadas e as abas dos relatórios que citam valores de amostra.

O repositório foi compartilhado com você de propósito, só para uso interno:

- Mantenha-o em máquinas e remotos git controlados pela consultoria. Não o envie a um serviço público ou pessoal, não cole valores em ferramentas externas e não anexe planilhas a chamados fora da consultoria.
- Tudo em `data/` está commitado, portanto também está no histórico do git. Apagar um arquivo da árvore de trabalho não o remove de um clone.
- **O modo online envia dados à OpenAI.** Sem `--offline`, o `invoice_mapper.py` envia todos os cabeçalhos do modelo e do cliente à API de embeddings. Para cada coluna do modelo da segunda passagem que a decisão por catálogo não resolve, envia também perfis de valores ao resolvedor de IA. Cada perfil inclui até `--sample-size` valores de amostra (padrão 8), tirados da coluna do modelo e de cada coluna do cliente do conjunto de candidatos (até 40). Nada é mascarado; as chamadas ao resolvedor usam `store=False`. Confirme que o cliente permite isso antes de rodar online.
- O mapeamento offline, o apply, o `mapeval` e os testes nunca acessam a OpenAI. O `update_reference_data.py` baixa arquivos de sites do governo, mas não envia nenhum dado de cliente.
- O `.env` na raiz do repositório está no `.gitignore`, e nenhum código o lê. Execuções online precisam de `OPENAI_API_KEY` exportada no shell.

## O modelo base: `data/model.xlsx`

O modelo tem uma aba, `Sheet1`:

- **A linha 1** é o cabeçalho: 227 colunas, de A a HS.
- **As linhas 2 a 210** são 209 linhas de amostra.
- Um autofiltro cobre `A1:HS210`. Não há painéis congelados nem células mescladas.

As linhas de amostra importam. No modo online, o resolvedor de IA compara os valores de amostra de uma coluna do modelo com os de cada coluna candidata do cliente. 180 colunas do modelo têm pelo menos um valor de amostra. As outras 47 não têm nenhum, então o resolvedor não recebe valores de exemplo para elas.

### Blocos dos tributos da reforma, colunas 1 a 28

O modelo começa com um bloco de sete campos para cada tributo da reforma:

| Colunas | Tributo |
|---|---|
| 1-7 | CBS |
| 8-14 | IBSM (pelo nome, a parcela municipal do IBS) |
| 15-21 | IBS |
| 22-28 | IS |

Todo bloco tem os mesmos campos na mesma ordem, com o tributo como sufixo (`Base CBS`, `Aliquota IBSM`, `Valor IS`):

| Posição no bloco | Campo | Significado |
|---|---|---|
| 1 | `Tipo de Imposto` | tipo de imposto |
| 2 | `Base` | base de cálculo |
| 3 | `Aliquota` | alíquota |
| 4 | `Valor` | valor do imposto (base × alíquota) |
| 5 | `Isentas` | isentas |
| 6 | `Outras` | outras (não tributadas) |
| 7 | `Base Reduzida` | base reduzida |

Nas linhas de amostra, as quatro colunas `Tipo de Imposto` (1, 8, 15 e 22) estão vazias, e os outros 24 campos dos tributos da reforma valem 0 em todas as linhas.

### Colunas 29 a 227

São cabeçalhos em português no estilo do SAP, em quatro grupos:

- **Campos do documento:** `Data de lançamento`, `Nº de documento`, `Nº da Nota Fiscal`, `Série`, `CFOP`, o `Nome` e o `Nº ID fiscal 1` do parceiro, `Chave de acesso`.
- **Tributos atuais:** ICMS, ICMS-ST, IPI, PIS, COFINS, ISS, FECP e DIF_ALIQ, além de outros tipos de imposto do SAP ligados ao ICMS (ICAP, ICEP, ICSP, ICOP). A maioria usa os mesmos campos Tipo/Base/Aliquota/Valor/Isentas/Outras/Base Reduzida, mas os campos de um tributo nem sempre ficam lado a lado. O ICMS, por exemplo, tem `Base ICMS` na coluna 41 e `Aliquota ICMS` na 190.
- **Campos do item e da importação:** `Material`, `Nº do NCM`, `Quantidade`, `Preço líq.`, datas e locais de desembaraço aduaneiro.
- **Códigos e textos legais:**
  - `CST IPI`, `CST COFINS`, `CST PIS` e `CST ICMS` são as colunas 219 a 222.
  - O CST e o código de classificação do IBS/CBS são as colunas 176 e 177.
  - `Texto ICMS`, `Texto IPI`, `Lei COFINS` e `Lei tributária PIS` guardam referências à legislação no SAP.

### Cabeçalhos repetidos

Dois cabeçalhos se repetem:

- `Descrição` nas colunas 35 e 195, com o mesmo texto.
- `Domicílio fiscal` (76) e `Domicílio Fiscal` (154), que só diferem na caixa.

`SheetTable.from_excel` (invoice_mapper.py) compara cabeçalhos normalizados e por isso chama a segunda coluna de cada par de `... (occurrence 2)` em memória. Esse sufixo nunca é gravado para colunas do modelo: relatórios e gabaritos endereçam as colunas do modelo pelo número, e `coluna_modelo` (como `coluna_modelo_mais_proxima` e os cabeçalhos de `Dados Mapeados`) traz o cabeçalho sem sufixo, então ali as repetições nunca são ambíguas. Lendo `Dados Mapeados` com o pandas, o segundo `Descrição` vem como `Descrição.1`.

### Não mexa no modelo

O `model.xlsx` está fixo por enquanto. Não corte, reordene, renomeie nem reestruture o arquivo, e não proponha mudanças que dependam de ele mudar.

Ele é um superconjunto grande de propósito, que cobre todo campo que algum cliente possa enviar. Uma execução que entrega só parte do modelo é o resultado esperado, não uma deficiência.

Há também razões mecânicas para não mexer nele:

- Todo relatório registra `sha256_modelo`, e o `apply_invoice_mapping.py` recusa um relatório cujo hash do modelo é diferente.
- Relatórios, gabaritos e `mapeval` endereçam as colunas do modelo pela posição.

## Exportações dos clientes: `data/clients/`

São quatro exportações reais de ERP, cada uma com uma aba e o cabeçalho na linha 1. Elas são exemplos de referência para aprender estratégias de mapeamento que se transfiram para outros clientes, nunca um conjunto de treino. Não são o produto: em produção virão muitos clientes que ninguém viu ainda.

- Não ajuste limiares, vocabulário ou regras contra estes quatro arquivos.
- Nenhum código pode ramificar pelo nome de um cliente.
- Valide as mudanças deixando um cliente de fora (ver [EVALUATION.md](EVALUATION.md#5-validação-deixando-um-cliente-de-fora)), e prefira evidência que se transfira para um ERP nunca visto.

| Arquivo | ERP | Aba | Linhas de dados | Colunas | Observações |
|---|---|---|---|---|---|
| `datasul_maio.xlsx` | Datasul | `datasul` | 6.804 | 348 | Todo cabeçalho é um campo fixo de 12 caracteres, e 206 deles terminam com espaços de preenchimento (`'Filial      '`). 3 cabeçalhos se repetem exatamente, 4 depois de `normalize_header` (`Aliq. COFINS` e `Aliq. Cofins`). 36 colunas não têm valor nenhum. |
| `oracle.xlsx` | Oracle | `oracle` | 7.340 | 48 | Cabeçalhos em maiúsculas (`ORGANIZACAO`, `DATA GL`, `CFO`). 1 coluna sem valor nenhum. |
| `sap_procosa.XLSX` | SAP | `Sheet1` | 18.209 | 118 | `Direito` aparece 3 vezes. 1 coluna sem valor nenhum. |
| `sap_terumo.XLSX` | SAP | `Sheet1` | 727 | 365 | Rótulos de campo do SAP em inglês, alguns em português. 23 cabeçalhos se repetem exatamente (`Reserved` 5 vezes), 25 depois de `normalize_header`. 89 colunas sem valor nenhum. |

Como as ferramentas leem uma exportação: `SheetTable.from_excel` carrega a aba com `header=None, dtype=object`, então nenhum valor é convertido.

- Os cabeçalhos perdem os espaços das pontas.
- Um cabeçalho em branco vira `Unnamed column N` em memória e `Coluna sem nome N` nos arquivos.
- Um cabeçalho repetido ganha o sufixo ` (occurrence N)` em memória e ` (ocorrência N)` nos arquivos, por exemplo `Name 1 (ocorrência 2)` em sap_terumo. As repetições são detectadas depois de `normalize_header`, então cabeçalhos que só diferem na caixa, nos acentos ou na pontuação contam como repetidos.

A forma em memória é entrada do embedding e do resolvedor de IA e por isso fica em inglês; a forma em português é a que relatórios e revisores usam. O cabeçalho em si é dado do cliente e nunca é traduzido: `Net value` continua `Net value`, só o sufixo muda. Para uma exportação cujo cabeçalho não está na linha 1, passe `--client-header-row` (contada a partir de 1) ao mapeador e ao apply.

Todo relatório guarda o SHA-256 da sua exportação (`sha256_cliente`), e o apply recusa uma exportação diferente. Salvar a exportação de novo no Excel muda o hash e invalida o relatório. As quatro exportações conferem com os hashes gravados nos seus relatórios.

## Arquivos de-para: `data/keys/`

Os clientes escreveram três de-para. Só dois deles são gabaritos.

### Layout dos gabaritos (datasul, oracle)

| Linha | Conteúdo |
|---|---|
| 1 | Para cada coluna do modelo que o cliente tem, o texto livre `Coluna X`, com a letra da coluna na exportação (`Coluna I` é a coluna 9). A célula fica em branco nas demais colunas do modelo. Algumas células têm espaços no fim (`'Coluna BV '`). |
| 2 | Os 227 cabeçalhos do modelo, idênticos à linha 1 de `data/model.xlsx`. |
| 3 em diante | Os dados do cliente rearranjados no layout do modelo, linha a linha, em paralelo com a exportação: a linha 3 do gabarito corresponde à linha 2 da exportação, e assim por diante. |

| Arquivo | Aba | Colunas do modelo anotadas | Linhas de dados | Linhas de dados da exportação |
|---|---|---|---|---|
| `de_para_datasul.xlsx` | `Data Sul` | 30 | 6.804 | 6.804 |
| `de_para_oracle.xlsx` | `Oracle` | 26 | 7.338 | 7.340 |

As duas abas declaram o intervalo `A1:HS7340`, maior do que os dados. Conte as linhas preenchidas em vez de confiar em `max_row`.

O `mapeval` lê só a linha 1, por `mapeval/truth.py` (`load_answer_key`) e `mapeval/excel.py` (`parse_coluna_annotation`). Os dois gabaritos juntos anotam 56 colunas do modelo (30 + 26). Essas 56 são o único denominador do recall; 227 nunca é. Nenhum código lê as linhas de dados. Foi contra elas que as correções abaixo foram conferidas.

Os gabaritos são um teste de regressão, não dado de treino. Nunca derive regras, vocabulário ou limiares deles.

### Quatro defeitos conhecidos nos gabaritos

`KEY_CORRECTIONS`, em `mapeval/truth.py`, substitui quatro anotações em que o gabarito está errado e o mapeador está certo:

| Cliente | Coluna do modelo | O gabarito diz | Corrigido para | Por quê |
|---|---|---|---|---|
| oracle | 213 `Aliquota PIS` | c37 `ALIQ COFINS` | c36 `ALIQ PIS` | o gabarito dá a alíquota da COFINS no lugar da do PIS |
| oracle | 220 `CST COFINS` | c39 `CST-PIS` | c40 `CST-COFINS` | o par PIS/COFINS está trocado no gabarito |
| oracle | 221 `CST PIS` | c40 `CST-COFINS` | c39 `CST-PIS` | a mesma troca |
| datasul | 184 `Aliquota COFINS` | c99 `Aliq. COFINS` | c188 `Aliq. Cofins` | c99 pertence a outro bloco da exportação e não combina com o resto do gabarito; c188 fecha base × alíquota = valor com as colunas de base e de valor do PIS/COFINS do próprio gabarito (modelo 52 a 55) |

O `mapeval` aplica as correções por padrão. `python -m mapeval --strict-key-only` pontua contra os gabaritos crus. As correções valem exatamente quatro colunas, tanto nas entregues quanto nas encontradas. `tests/test_mapeval.py` (`test_the_four_key_corrections_are_worth_exactly_four_columns`) só confere isso nas encontradas. Os números estão em [EVALUATION.md](EVALUATION.md#1-gabaritos-arquivos-de-para).

### `de_para_procosa.xlsx` não é gabarito

- A aba dele, `SAP`, tem 226 colunas. A linha 1 marca cada coluna como `ITEM` (155 colunas) ou `NOTA` (71): se o campo pertence à linha da nota ou à nota inteira. Não há anotações `Coluna X`.
- A linha 2 é o cabeçalho do modelo sem a primeira coluna (`Tipo de Imposto CBS`), então a coluna N do arquivo é a coluna N+1 do modelo.
- A coluna 31 do arquivo (modelo 32, `Nº da Nota Fiscal`) traz uma observação no lugar do cabeçalho. Ela diz que o número da NF-e foi posto na coluna GZ, que é a coluna 208 do arquivo e a 209 do modelo (`Número de nota fiscal eletrônica`).
- Tem 99 linhas de dados (linhas 3 a 101), embora a aba declare 209.
- Nenhum dos seus 97 números de documento aparece em `sap_procosa.XLSX`, então ele foi escrito contra outra extração. A coluna `Chave de acesso` dele tem 52 chaves de acesso de NF-e válidas, e a exportação não tem chave de acesso nenhuma.

Ele documenta a granularidade do registro, não qual coluna da exportação alimenta qual coluna do modelo. Por isso `CLIENTS`, em `mapeval/cli.py`, não dá gabarito a sap_procosa. sap_terumo não tem de-para nenhum.

Para os dois clientes sem gabarito, o `mapeval` imprime `SEM GABARITO`, só a contagem de aceitos, e confere se há aceites em colunas do cliente sem valor nenhum (`aceites em coluna vazia`).

## Relatórios de mapeamento: `data/reports/`

O `invoice_mapper.py` grava um relatório por execução, por `write_results`, no esquema em português: `versao_esquema_mapeamento` igual a 3 (`MAPPING_SCHEMA_VERSION`). Os relatórios arquivados se chamam `mapping_<cliente>_full.xlsx`, em que `<cliente>` é `datasul`, `oracle`, `sap_procosa` ou `sap_terumo`. Os quatro foram gravados no formato antigo, em inglês, e migrados para o português sem reexecutar o mapeador (ver [Migração dos relatórios arquivados](#migração-dos-relatórios-arquivados)).

A saída padrão do mapeador é `mapping_<nome do arquivo do cliente>.xlsx` no diretório atual, então passe `--output` explicitamente. Uma saída terminada em `.csv` grava só a tabela `Mapeamentos`; o apply não consegue lê-la.

> **Os pareceres do revisor se perdem com facilidade.** O `write_results` grava as três colunas do revisor sempre em branco e sobrescreve o arquivo de saída sem perguntar. Apontar `--output` para um relatório existente apaga os pareceres dele; hoje isso apagaria os 26 de sap_procosa. Grave cada execução nova com um nome de arquivo novo.

### Abas

As abas vêm nesta ordem:

1. **`Mapeamentos`** precisa continuar em primeiro. O `apply_invoice_mapping.py` (`_read_mapping_report`) lê a aba de índice 0 sem procurá-la pelo nome, então uma aba inserida antes dela quebra o apply com um erro enganoso: `A aba Mapeamentos não tem a coluna 'numero_coluna_modelo'; confira se o arquivo é um relatório de mapeamento.` Se acrescentar uma aba no Excel, ponha no fim.
2. **`Metadados da Execução`**.
3. **`Colunas do Cliente sem Uso`**.
4. **`Fila de Revisão`**.

As abas 3 e 4 não existem no relatório antigo de sap_procosa (ver [O relatório antigo de sap_procosa](#o-relatório-antigo-de-sap_procosa)). Uma execução atual grava as duas mesmo sem nada a listar, então uma aba ausente sempre indica um relatório antigo.

O apply procura `Metadados da Execução` pelo nome, e o `mapeval` faz o mesmo com `Mapeamentos` (é a única aba que ele lê); a comparação ignora maiúsculas, acentos e espaços nas pontas. Uma aba com o nome do formato antigo, em inglês (por exemplo, `Run Metadata`), é recusada.

### Contagem por situação

| Relatório | `aceito` | `revisao` | `sem_correspondencia` | Pareceres do revisor |
|---|---|---|---|---|
| datasul | 19 | 95 | 113 | 0 |
| oracle | 18 | 37 | 172 | 0 |
| sap_procosa | 45 | 66 | 116 | 26 |
| sap_terumo | 67 | 92 | 68 | 0 |

As três situações significam:

- **`aceito`**: a linha passou pelo limiar de aceite e é entregue.
- **`revisao`**: existe uma candidata, mas o limiar a reteve.
- **`sem_correspondencia`**: o mapeador não achou contrapartida. Como o modelo é um superconjunto, muitas vezes essa é a resposta certa: o cliente não envia aquele campo.

Toda linha `revisao` traz uma coluna do cliente proposta, e nenhuma linha `sem_correspondencia` traz.

### `Mapeamentos`

`Mapeamentos` tem uma linha por coluna do modelo, na ordem do modelo. Estas são as colunas como um relatório novo as grava (`MappingResult` seguido de `REVIEWER_COLUMNS`, em invoice_mapper.py, com os cabeçalhos de `report_schema.COLUMNS["mappings"]`):

| Coluna | Significado |
|---|---|
| `numero_coluna_modelo` | Posição em `data/model.xlsx`, a partir de 1. |
| `coluna_modelo` | Cabeçalho do modelo. O apply o confere com o modelo, posição a posição. |
| `numero_coluna_cliente` | Coluna da exportação proposta, a partir de 1. Vazia nas linhas `sem_correspondencia`. |
| `coluna_cliente` | O nome de exibição dela. O apply o confere com a exportação, o que pega um relatório feito de outro layout. |
| `confianca_nome` | Semelhança de cabeçalho da coluna escolhida: embeddings online, léxica offline. |
| `confianca_nome_segunda_melhor` | A melhor semelhança de cabeçalho entre as demais colunas do cliente. |
| `margem_nome` | A vantagem da coluna escolhida sobre a segunda melhor. |
| `confianca_dados` | Pontuação local tirada dos valores. Vazia nos aceites da primeira passagem (só cabeçalho). |
| `confianca_final` | A pontuação que o limiar de aceite testa (`--final-threshold`, padrão 0,90). |
| `metodo` | O ramo que produziu a linha: `nome`, `dados`, `catalogo`, `somente_nome` (revisão offline) ou `erro_dados` (a chamada ao resolvedor de IA falhou). Ver [Valores de `metodo`](ARCHITECTURE.md#valores-de-metodo) em ARCHITECTURE.md. |
| `situacao` | `aceito`, `revisao` ou `sem_correspondencia`. |
| `justificativa` | Uma frase que explica a linha (ver [Frases em português e prosa da IA](#frases-em-português-e-prosa-da-ia)). |
| `nome_catalogo` | O catálogo de referência que os valores da coluna casaram, se houver. |
| `cobertura_catalogo` | Fração das linhas não vazias cujo valor é um código válido. |
| `cobertura_distinta_catalogo` | Fração dos códigos distintos bem formados que são válidos. Só a auditoria da primeira passagem a preenche. |
| `confianca_catalogo` | Pontuação do catálogo: cobertura distinta × especificidade do catálogo × um fator para amostras pequenas. |
| `prova_aritmetica` | Texto, preenchido quando um trio fiscal da exportação (base de cálculo × alíquota = valor do imposto) põe esta coluna aqui. |
| `apoiado_por` | As etapas de evidência que sustentam a coluna escolhida, da mais fraca para a mais forte, unidas por ` + `. |
| `decidido_por` | A mais forte delas. |
| `vetado_por` | A trava que recusou uma correspondência que de outro modo seria viável: `coluna_vazia` ou `conflito_tributario`. |
| `confianca_dados_ia`, `confianca_final_ia` | As pontuações do próprio resolvedor de IA, guardadas para auditoria. O limiar não as usa. |
| `parecer_revisor`, `coluna_cliente_correta`, `observacao_revisor` | Gravadas em branco. Os revisores as preenchem (ver [Colunas do revisor](#colunas-do-revisor)). |

As confianças são sinais de decisão, não probabilidades calibradas.

Os ids de etapa vêm de `mapping_steps.py` e são gravados em português (`report_schema.STEP_IDS`). Estas são as etapas de evidência, da mais fraca para a mais forte:

1. `resolvedor_ia`
2. `lexico`
3. `semantico`
4. `perfil`
5. `identificador`
6. `catalogo`
7. `aritmetica`

As quatro últimas se transferem para um ERP desconhecido, porque se apoiam na lei tributária, nas tabelas oficiais de códigos e em dígitos verificadores, e não nas palavras do cabeçalho deste cliente. A fração dos aceites apoiados em evidência independente de cabeçalho é a métrica de generalização do projeto. Para os clientes com gabarito, o `mapeval` imprime uma versão mais estreita como `aceites independentes de cabeçalho`. Ela só conta um aceite quando a linha traz uma `prova_aritmetica` decisiva ou o `metodo` é `catalogo`, e não lê as colunas de etapas (ver [A métrica de generalização](EVALUATION.md#a-métrica-de-generalização) em EVALUATION.md e o item [2.4 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#24-duas-definições-da-métrica-de-generalização)). [Atribuição de etapas](ARCHITECTURE.md#atribuição-de-etapas), em ARCHITECTURE.md, explica o resto.

#### Frases em português e prosa da IA

As frases que o mapeador escreve (`justificativa`, `prova_aritmetica`, `detalhe_sinais`, `motivo_retencao` e `divergencia_ia`) são escritas em português na origem, com vírgula decimal, por exemplo `valor = base x alíquota se confirma para ICMS em 453 linhas, com aderência 1,0`.

A justificativa escrita pelo resolvedor de IA continua em inglês. O prompt é em inglês, então a IA responde em inglês: isso vale para as 502 justificativas da IA nos relatórios arquivados e para as de execuções futuras. Ninguém as traduz, nem a migração.

Quando o cliente tem mais de um bloco do mesmo tributo, a prova confirma o tributo mas não aponta uma coluna só, e o texto contém o trecho `não distingue esta coluna`. `report_schema.proof_is_decisive` reconhece esse marcador, e uma prova assim não conta como evidência aritmética.

#### As colunas de etapas dos relatórios arquivados

**Os quatro relatórios arquivados receberam as colunas de etapas por preenchimento retroativo (backfill).** O `backfill_mapping_steps.py`, uma ferramenta de uso único removida depois do commit `5fe5092`, as acrescentou depois das execuções (commit 88c503b), ainda no formato antigo, reconstruindo-as a partir das pontuações gravadas. Três consequências:

- Ele gravou dois valores que uma execução nova nunca grava (aqui com os nomes já migrados):
  - `nao_registrado`: uma etapa objetiva rodou, mas as pontuações gravadas não dizem qual.
  - `catalogo (concorda)`: a evidência de catálogo concordou, mas era fraca demais para decidir.
- Ele leu `llm_final_confidence` (hoje `confianca_final_ia`) de uma posição desatualizada, três células ao lado. Por isso `resolvedor_ia` nunca aparece em `apoiado_por` nos relatórios de datasul, oracle e sap_terumo. Isso inclui as 75, 32 e 78 linhas (respectivamente) que têm coluna proposta e pontuação positiva do resolvedor. Em sap_procosa ele aparece em 60 linhas. Ver o item [1.1 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#11-resolvedor_ia-não-aparece-em-apoiado_por-em-três-relatórios-arquivados).
- As colunas de etapas ficam em lugares diferentes em cada relatório:

| Relatório | Posição das colunas de etapas |
|---|---|
| execução nova | depois de `confianca_catalogo`: `prova_aritmetica`, `apoiado_por`, `decidido_por`, `vetado_por` |
| datasul, oracle, sap_terumo | antes de `prova_aritmetica`: `decidido_por`, `apoiado_por`, `vetado_por`, `prova_aritmetica` |
| sap_procosa | depois de `confianca_final_ia`, logo antes de `parecer_revisor` (este relatório não tem `prova_aritmetica`) |

Todas as ferramentas procuram as colunas pelo nome; faça o mesmo.

### Colunas do revisor

Os revisores registram os pareceres em `Mapeamentos`, e o `apply_invoice_mapping.py` (`_effective_columns`) os aplica.

| `parecer_revisor` | `coluna_cliente_correta` | Efeito na planilha mapeada |
|---|---|---|
| vazio | vazio | A decisão do mapeador vale: as linhas `aceito` são entregues, as outras não. |
| `correto` | vazio | O `numero_coluna_cliente` proposto é entregue. Recusado se a linha não tem proposta. |
| `incorreto` | obrigatória | A coluna indicada aqui é entregue. Dê o número da coluna (a partir de 1) ou o cabeçalho da exportação. Para um cabeçalho repetido, use o nome de exibição com o sufixo ` (ocorrência N)`; o nome sozinho seleciona a primeira ocorrência sem avisar. Texto que pode ser lido como número é sempre lido como número de coluna. |
| `inexistente` | vazio | Nada é entregue. Confirma que o cliente não envia esse campo. |

`inexistente` não é `sem_correspondencia`: um é o parecer humano de que o cliente não tem o campo, o outro é a situação que o mapeador gravou.

A leitura do parecer (`report_schema.normalize_reviewer_status`) ignora espaços nas pontas, maiúsculas e acentos: `Correto` e ` INCORRETO ` valem. Um valor desconhecido é recusado e a mensagem mostra o que foi digitado e a coluna do modelo; isso vale também para os pareceres em inglês do formato antigo (`correct`, `incorrect`, `no_mapping_exists`). Em `coluna_cliente_correta`, a comparação com os cabeçalhos ignora maiúsculas, e o sufixo pode vir como `(ocorrência N)` ou `(ocorrencia N)`; um cabeçalho em branco é `Coluna sem nome N`.

`observacao_revisor` é texto livre. É copiada sem tradução para o banner da planilha mapeada.

O apply se recusa a gravar a planilha em qualquer um destes casos:

- o parecer não é um dos valores acima;
- `coluna_cliente_correta` está preenchida com qualquer parecer que não seja `incorreto`;
- o parecer é `incorreto` e a correção está em branco;
- o parecer é `correto` e a linha não tem proposta;
- uma correção cita uma coluna que a exportação não tem;
- a mesma coluna do cliente seria entregue duas vezes.

A `Fila de Revisão` é um índice ordenado dessas linhas, não um segundo lugar para escrever. Registre os pareceres aqui, em `Mapeamentos`.

### `Metadados da Execução`

`Metadados da Execução` tem duas colunas, `chave` e `valor`. Uma execução nova grava 19 chaves; os quatro relatórios arquivados têm 20, porque a migração acrescentou `migrado_da_versao_esquema`.

| Chave | Significado | Valor nos relatórios arquivados |
|---|---|---|
| `versao_esquema_mapeamento` | Esquema do relatório (`MAPPING_SCHEMA_VERSION`): 3 é o esquema em português, 2 o formato antigo em inglês, que não é mais lido. | 3 (era 2 antes da migração) |
| `gerado_em_utc` | Quando o mapeador rodou. A migração não o altera. | 2026-08-25 para datasul, oracle e sap_terumo; 2026-08-04 para sap_procosa |
| `versao_prompt` | Versão do prompt do resolvedor de IA. | 2026-08-04.2 |
| `arquivo_cliente`, `arquivo_modelo` | Só o nome do arquivo, sem a pasta. | por exemplo `oracle.xlsx`, `model.xlsx` |
| `sha256_cliente`, `sha256_modelo` | SHA-256 das entradas. O apply recusa arquivos diferentes. | conferem com os arquivos atuais em `data/` |
| `aba_cliente`, `aba_modelo` | Índice ou nome da aba usada. | 0 |
| `linha_cabecalho_cliente`, `linha_cabecalho_modelo` | Linha do cabeçalho, a partir de 1. | 1 |
| `modelo_embedding` | Modelo de embedding dos cabeçalhos, ou `offline`. | `text-embedding-3-large` |
| `modelo_ia` | Modelo do resolvedor de IA, ou `offline`. | `gpt-4o-mini` |
| `limiar_nome`, `margem_minima_nome`, `limiar_final` | Ajustes dos limiares (`--name-threshold`, `--name-margin`, `--final-threshold`). `margem_minima_nome` é a margem mínima exigida, não a `margem_nome` medida em cada linha. | 0,9, 0,05, 0,9 |
| `quantidade_candidatos` | Tamanho de cada lista de candidatos da segunda passagem por coluna do modelo (`--candidates`). Online há uma lista por pontuação de cabeçalho, uma por perfil de valores, uma por evidência aritmética e uma por evidência de catálogo, e o resolvedor de IA vê a união delas. | 10 |
| `tamanho_amostra` | Valores de amostra por coluna em cada perfil (`--sample-size`). | 8 |
| `sha256_manifesto_referencia` | SHA-256 de `reference_data/manifest.json`, ou `desativado`. | `c4e2106d...`, que confere com o manifesto atual |
| `migrado_da_versao_esquema` | Só em relatório migrado: a versão de onde ele veio. | 2 |

Os quatro relatórios arquivados vieram de execuções online. O apply confere só os dois hashes de entrada, e pula a conferência em silêncio quando a aba ou uma das chaves não existe. Não compara aba, linha de cabeçalho nem manifesto. Uma aba, um cabeçalho ou uma chave com o nome do formato antigo, em inglês, é recusado, justamente para a conferência de hash não ser pulada.

### `Colunas do Cliente sem Uso`

Esta aba lista toda coluna da exportação que nenhum mapeamento aceito consumiu. Uma lista longa é normal, porque o cliente envia muitos campos que o modelo não tem. A lista serve para o revisor achar uma perda de verdade.

| Coluna | Significado |
|---|---|
| `numero_coluna_cliente`, `coluna_cliente` | A coluna da exportação. |
| `disposicao` | `proposta_nao_aceita`: alguma linha do modelo a propôs em revisão. `nunca_referenciada`: nenhuma linha a propôs. |
| `prioridade_revisao` | `prioritaria` quando a coluna tem valores informativos (não está vazia nem é só zeros) e traz um sinal ou foi proposta. `baixa` nos demais casos. |
| `linhas_com_valor`, `valores_distintos` | Quanto dado a coluna tem. |
| `sinais` | Pistas independentes de cabeçalho, separadas por vírgula: `aritmetica`, `catalogo`, `dv_chave_nfe`, `dv_cnpj`, `dv_cpf`, `data`. |
| `detalhe_sinais` | Uma frase por sinal, ou o motivo de a coluna ter sido deixada de lado. |
| `coluna_modelo_mais_proxima`, `confianca_nome_mais_proxima` | O cabeçalho do modelo mais próximo. É sempre calculado de forma léxica, mesmo em execução online. |

As linhas vêm com as prioritárias primeiro, depois pelo número da coluna do cliente.

| Relatório | Linhas | `prioritaria` |
|---|---|---|
| datasul | 329 | 37 |
| oracle | 30 | 14 |
| sap_terumo | 298 | 40 |

A aba é gravada quando o mapeador roda, então pareceres posteriores não a alteram.

### `Fila de Revisão`

Esta aba lista toda coluna do modelo que tem coluna do cliente proposta e `confianca_final` acima de 0, mas não foi aceita. Nos relatórios arquivados, são exatamente as linhas `revisao`.

| Coluna | Significado |
|---|---|
| `posicao` | Posição na fila. |
| `numero_coluna_modelo`, `coluna_modelo` | A coluna do modelo. |
| `numero_coluna_cliente_proposta`, `coluna_cliente_proposta` | A proposta retida. |
| `confianca_final` | A pontuação que o limiar testou. |
| `distancia_limiar` | `limiar_final` menos `confianca_final`. |
| `margem_nome` | A vantagem de cabeçalho sobre a segunda melhor. |
| `linhas_com_valor` | Linhas preenchidas na coluna proposta. |
| `divergencia_ia` | `o resolvedor de IA atribuiu confiança X` quando a pontuação do resolvedor fica mais de 0,10 abaixo de `confianca_final`. |
| `prova_aritmetica` | Copiada de `Mapeamentos`. |
| `amostra_valores_cliente` | Até 6 valores da coluna proposta, unidos por ` \| `. É dado real do cliente. |
| `motivo_retencao` | Um dos três motivos abaixo. |

Os três valores de `motivo_retencao`:

- `abaixo do limiar de aceite`
- `só há evidência de nome - este caminho não aceita automaticamente, por mais que os cabeçalhos coincidam`: a linha fica no teto de 0,8999, logo abaixo do limiar (ver [O ramo do teto](ARCHITECTURE.md#a-confiança-final-local) em ARCHITECTURE.md).
- `a coluna proposta não tem linhas preenchidas - confirmá-la entregaria uma coluna em branco`

A ordem vem de `summarize_adjudication_queue`, em invoice_mapper.py:

1. Propostas para colunas com dados vêm antes das propostas para colunas vazias.
2. A `confianca_final` maior vem primeiro, comparada em degraus de 0,0002 (`_CLAMPED_BAND`).
3. Dentro do mesmo degrau, vem primeiro a `margem_nome` maior, depois mais `linhas_com_valor`.

O degrau de 0,0002 existe porque muitas linhas compartilham o teto de 0,8999. Dentro dessa faixa, quem ordena é a `margem_nome`.

| Relatório | Linhas | abaixo do limiar | só evidência de nome | sem linhas preenchidas |
|---|---|---|---|---|
| datasul | 95 | 78 | 11 | 6 |
| oracle | 37 | 35 | 2 | 0 |
| sap_terumo | 92 | 50 | 31 | 11 |

O limiar de aceite fica em 0,90. O parecer do revisor é o caminho para essas linhas virarem colunas entregues; [EVALUATION.md](EVALUATION.md#por-que-o-limiar-de-aceite-fica-em-090) explica por que baixar o limiar não é a resposta.

### O relatório antigo de sap_procosa

`mapping_sap_procosa_full.xlsx` foi gerado em 2026-08-04 com um layout mais antigo, e a migração manteve esse layout:

- Tem só `Mapeamentos` e `Metadados da Execução`.
- `Mapeamentos` tem 23 colunas. Não há `cobertura_distinta_catalogo` nem `prova_aritmetica`.
- As colunas de etapas foram preenchidas depois, após `confianca_final_ia`.
- `versao_esquema_mapeamento` diz 3 desde a migração. A versão descreve os nomes (esquema em português), não o layout; antes ela dizia 2, como nos outros relatórios (item [5.1 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#51-os-layouts-dos-relatórios-diferem-e-a-versão-do-esquema-não-diz-qual-é-qual)).

É também o único relatório com pareceres do revisor:

| Situação do mapeador | Parecer do revisor | Linhas | Resultado |
|---|---|---|---|
| `revisao` | `correto` | 15 | entregue |
| `sem_correspondencia` | `incorreto` | 2 | entregue a partir da coluna corrigida: `Nome` (modelo 37) da coluna 6 (`NF Parceiro descrição`), `Nº ID fiscal 1` (modelo 38) da coluna 32 (`CNPJ`) |
| `revisao` | `inexistente` | 9 | confirmado que não consta na exportação |

Todos os 26 pareceres têm `observacao_revisor`. As observações foram escritas em inglês pelo revisor e continuam exatamente como foram escritas; a migração só traduziu o parecer (`correct` para `correto` e assim por diante). Em `coluna_cliente_correta`, 6 e 32 continuam números inteiros.

Uma sessão de revisão transformou 17 linhas em colunas entregues: 45 aceitas mais 17 confirmadas (15 `correto` e 2 `incorreto`) dão 62 entregues. Esse resultado é a razão de o parecer do revisor sobre a faixa retida ser o caminho planejado para aumentar as colunas entregues.

Não regenere este relatório; isso apagaria os 26 pareceres. O apply continua aceitando o arquivo, porque preenche com vazio as colunas opcionais ausentes.

## Planilhas mapeadas: `data/mapped/`

O `apply_invoice_mapping.py` (`build_mapped_workbook`) transforma uma exportação e o seu relatório numa planilha no layout de colunas do modelo. Ela tem duas abas, `Dados Mapeados` e `Cobertura`, em português.

> **Contrato com quem lê a planilha: mudança incompatível.** Até a tradução, as abas se chamavam `Mapped Data` e `Coverage`, a célula A1 era `__mapping_info__`, os rótulos do banner e os cabeçalhos da cobertura eram os nomes em inglês, e os valores também (`accepted`, `delivered` ...). As quatro planilhas de `data/mapped/` foram regeneradas no esquema novo. Um leitor a jusante que procure a aba, a célula A1 ou um rótulo pelo nome antigo quebra; atualize-o com a [tabela de nomes legados](#nomes-legados-em-inglês). Não mudaram: a posição das colunas (A, depois as 227 colunas do modelo de B a HT), os cabeçalhos do modelo, a linha 23 como primeira linha de dados, os valores das células de dados (valor, tipo e formato), as colunas ocultas e o painel congelado.

Por padrão, uma coluna do modelo só é preenchida a partir de uma linha `aceito` sem parecer ou de uma linha confirmada pelo revisor (`correto` ou `incorreto`). `--include-review` preenche também as propostas não confirmadas. Essa saída é explicitamente insegura; nunca a entregue.

### `Dados Mapeados`

| Linhas do Excel | Conteúdo |
|---|---|
| 1 | Cabeçalho. `A1` é `__info_mapeamento__`, e as colunas B a HT são os 227 cabeçalhos do modelo, na ordem do modelo. Cabeçalhos repetidos ficam como estão. |
| 2-22 | Um banner de metadados com 21 linhas. A coluna A traz o rótulo, e cada coluna do modelo traz o valor daquela linha no relatório. |
| 23 em diante | Dados do cliente, uma linha por linha de dados da exportação, na ordem da exportação. A coluna A fica vazia. |

**Os dados do cliente começam na linha 23 do Excel.** Notas antigas dizem linha 20; isso está desatualizado. A constante é `FIRST_DATA_EXCEL_ROW = len(MAPPING_METADATA) + 2`, em `apply_invoice_mapping.py`. Ela já mudou antes, então importe-a em vez de fixar um número. Os painéis estão congelados em `B23`.

As linhas do banner, na ordem de `MAPPING_METADATA`:

| Linhas do Excel | Rótulos |
|---|---|
| 2-3 | `numero_coluna_cliente`, `coluna_cliente`: a coluna de fato entregue, depois dos pareceres do revisor. `coluna_cliente_correta` não aparece. |
| 4-5 | `situacao`, `metodo`: os valores do mapeador, não os do revisor. |
| 6-15 | `confianca_nome`, `confianca_nome_segunda_melhor`, `margem_nome`, `confianca_dados`, `confianca_dados_ia`, `confianca_final_ia`, `nome_catalogo`, `cobertura_catalogo`, `confianca_catalogo`, `confianca_final` |
| 16-19 | `decidido_por`, `apoiado_por`, `vetado_por`, `prova_aritmetica` |
| 20-22 | `justificativa`, `parecer_revisor`, `observacao_revisor` |

Os rótulos são os mesmos cabeçalhos da aba `Mapeamentos`. As confianças são gravadas como número e exibidas como porcentagem. Os valores de máquina (situação, método, etapas, trava, catálogo, parecer) saem em português, e o parecer sai na grafia canônica (`correto`, `incorreto`, `inexistente`) mesmo que o revisor tenha digitado ` Correto`. A justificativa, a prova aritmética e a observação do revisor são copiadas como estão no relatório. Em sap_procosa a linha `prova_aritmetica` fica em branco, porque o relatório não tem essa coluna, e as duas linhas `incorreto` mostram a situação `sem_correspondencia`, embora sejam entregues.

Os valores do cliente são copiados sem alteração. A exportação é lida com `dtype=object`, e a única conversão de tipo é que valores `datetime.time` voltam a ser células de hora do Excel (`hh:mm:ss`).

Formatação:

- O banner tem preenchimento azul-claro.
- Os cabeçalhos entregues têm preenchimento verde.
- Toda coluna do modelo sem coluna do cliente entregue fica oculta, não apagada, para as colunas entregues ficarem lado a lado. Um clique as reexibe, e quem lê por programa vê as 228 colunas.

| Planilha | Linhas de dados (linhas do Excel) |
|---|---|
| `datasul_mapped.xlsx` | 6.804 (23 a 6.826) |
| `oracle_mapped.xlsx` | 7.340 (23 a 7.362) |
| `sap_procosa_mapped.xlsx` | 18.209 (23 a 18.231) |
| `sap_terumo_mapped.xlsx` | 727 (23 a 749) |

Para ler os dados e a cobertura com o pandas, rode da raiz do repositório (no interpretador interativo ou com `python -`), porque o trecho importa `apply_invoice_mapping`:

```python
import pandas as pd
from apply_invoice_mapping import FIRST_DATA_EXCEL_ROW

arquivo = "data/mapped/sap_terumo_mapped.xlsx"

dados = pd.read_excel(
    arquivo,
    sheet_name="Dados Mapeados",
    skiprows=range(1, FIRST_DATA_EXCEL_ROW - 1),  # mantém o cabeçalho, descarta o banner
)
# 727 linhas x 228 colunas; a primeira é '__info_mapeamento__',
# e o segundo 'Descrição' chega como 'Descrição.1'

banner = pd.read_excel(
    arquivo,
    sheet_name="Dados Mapeados",
    nrows=FIRST_DATA_EXCEL_ROW - 2,  # as 21 linhas do banner
    index_col=0,
)
# banner.loc["situacao", "CFOP"] == "aceito"

cobertura = pd.read_excel(arquivo, sheet_name="Cobertura")
entregues = cobertura[cobertura["situacao_cobertura"] == "entregue"]
# 67 linhas
```

### `Cobertura`

`Cobertura` tem uma linha por coluna do modelo e diz em que pé cada uma está. Leia esta aba primeiro, porque `Dados Mapeados` não consegue mostrar o que não está lá. As colunas:

| Coluna | Significado |
|---|---|
| `situacao_cobertura` | Um dos três estados abaixo. |
| `numero_coluna_modelo`, `coluna_modelo` | A coluna do modelo. |
| `coluna_excel` | A letra dela em `Dados Mapeados` (a coluna 1 do modelo é B). |
| `numero_coluna_cliente` | A proposta, nas linhas não entregues. Vazia nas linhas entregues (item [1.3 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#13-a-cobertura-deixa-numero_coluna_cliente-em-branco-nas-linhas-entregues)). |
| `coluna_cliente` | A coluna entregue ou, se não houver, a proposta. |
| `confianca_final` | Do relatório, exibida como porcentagem. |
| `justificativa` | Do relatório, copiada como está. |

Os três estados:

- **`entregue`**: o apply pôs uma coluna do cliente aqui.
- **`aguardando decisão`**: nada foi entregue e a situação do mapeador é `revisao`. São as linhas da `Fila de Revisão`, e as únicas que são trabalho de alguém.
- **`não consta na exportação do cliente`**: todo o resto (`sem_correspondencia`). É uma resposta completa e correta, não um defeito.

As linhas vêm ordenadas por estado, nessa ordem, e depois pelo número da coluna do modelo. O cabeçalho fica congelado e tem autofiltro.

| Cliente | `entregue` | `aguardando decisão` | `não consta na exportação do cliente` |
|---|---|---|---|
| datasul | 19 | 95 | 113 |
| oracle | 18 | 37 | 172 |
| sap_procosa | 62 | 51 | 114 |
| sap_terumo | 67 | 92 | 68 |

Duas ressalvas:

- O estado vem da situação do mapeador, não de `parecer_revisor`. Por isso as 9 linhas `inexistente` de sap_procosa aparecem como `aguardando decisão`, e só 42 das 51 estão de fato pendentes (item [1.2 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#12-a-cobertura-mostra-como-aguardando-decisão-linhas-que-o-revisor-marcou-inexistente)).
- Com `--include-review`, as propostas não confirmadas aparecem como `entregue` (item [3.7](KNOWN_ISSUES.md#37-problemas-menores-na-leitura-das-entradas-do-apply)).

Trate `data/mapped/` como saída de build: regenere em vez de editar à mão.

## Esquema em português e esquema legado

### O que está em português e o que continua em inglês

`report_schema.py` é a fronteira. Em memória o código usa o vocabulário interno em inglês (a situação `accepted`, ids de etapa como `semantic`, os ids de catálogo, o nome de exibição `Nome (occurrence 2)`); esses valores viram português só na gravação e voltam ao vocabulário interno na leitura. Tudo o que uma pessoa lê num arquivo está em português: nomes de aba, cabeçalhos, chaves de metadados, valores enumerados, ids de etapa e de trava, o sufixo de ocorrência e as frases do mapeador.

Continuam em inglês, de propósito:

- nomes de arquivo e de pasta (`mapping_<cliente>_full.xlsx`, `<cliente>_mapped.xlsx`, `data/reports/`), flags da CLI e identificadores do código;
- os ids de catálogo dentro de `reference_data/` (nomes dos CSV, `manifest.json` e checksums não mudaram);
- a entrada dos modelos de IA, que não aparece nos arquivos (ver [Entrada dos modelos](ARCHITECTURE.md#entrada-dos-modelos-inglês-byte-a-byte) em ARCHITECTURE.md);
- a justificativa escrita pelo resolvedor de IA: as 502 dos relatórios arquivados e as futuras, porque o prompt é em inglês;
- as 26 observações do revisor de sap_procosa, como foram escritas;
- os dados do cliente: cabeçalhos da exportação (`Net value`, `Document Number`), valores de amostra e células de dados. Os cabeçalhos do modelo já são em português e não mudam.

O apply e o `mapeval` só leem o esquema em português (versão 3). O cabeçalho da primeira aba precisa ter `numero_coluna_modelo` (`report_schema.check_header`), e os nomes e valores são convertidos para o vocabulário interno antes de qualquer comparação.

Um relatório no formato antigo, em inglês (versão 2: aba `Mappings`, cabeçalho `model_column_number`...), é recusado com uma mensagem que diz como convertê-lo; os comandos estão no [README](../README.md#comandos-do-dia-a-dia). Um nome do formato antigo num relatório em português é recusado com o que renomear: por exemplo, uma aba `Run Metadata` ao lado de `Mapeamentos`, um cabeçalho `reviewer_status` ou uma chave `client_sha256`. Lido pela metade, um arquivo assim faria a conferência de hash ser pulada ou os pareceres serem ignorados em silêncio.

### Migração dos relatórios arquivados

Os quatro relatórios de `data/reports/` foram gravados no formato antigo, em inglês (versão 2), e migrados para o esquema em português (versão 3) pelo `migrate_reports.py`, sem reexecutar o mapeador (commit 09ebda9). As planilhas de `data/mapped/` foram então regeneradas pelo apply a partir dos relatórios migrados. O `migrate_reports.py` e o `legacy_reports.py` saíram do repositório depois do commit `5fe5092`; o registro abaixo fica como referência.

O que a migração fez, célula a célula, com o openpyxl:

- Renomeou as abas, os cabeçalhos e as chaves de metadados, e traduziu os valores enumerados, as etapas (inclusive os legados `unrecorded`, que virou `nao_registrado`, e o sufixo ` (agrees)`, que virou ` (concorda)`), o sentinela `disabled` (nenhum dos quatro o tinha) e o sufixo de ocorrência dos nomes de exibição (38 células com `(ocorrência N)` nos quatro relatórios).
- Reescreveu as frases do mapeador (justificativa, prova aritmética, detalhe dos sinais, motivo de retenção e divergência da IA) com o `legacy_reports.py`. Ele reconhecia cada frase pelo modelo em inglês que a produziu, copiado de `invoice_mapper.py` no commit 2ae4b6d, e a reescrevia com a constante em português que o mapeador usa. A frase migrada é exatamente a que o mapeador atual gravaria para os mesmos valores. O que não casava com nenhum modelo passou intacto.
- Mudou `versao_esquema_mapeamento` de 2 para 3 e acrescentou `migrado_da_versao_esquema` = 2. `gerado_em_utc` não mudou.

Passou intacto: a prosa escrita pela IA, as observações do revisor, `coluna_modelo`, os valores de amostra do cliente, os nomes de coluna do cliente (fora o sufixo) e todos os números, com o tipo (os inteiros 6 e 32 de `coluna_cliente_correta` continuam inteiros, e as células de texto vazio continuam texto vazio). A ordem das colunas e a formatação também não mudaram, e nenhuma aba ou coluna foi acrescentada: sap_procosa manteve o layout antigo.

Antes de traduzir, a ferramenta regenerou as partes derivadas (as provas aritméticas pelos trios fiscais, `Colunas do Cliente sem Uso` e `Fila de Revisão`) e as comparou com o arquivado, quando os hashes gravados permitiam; depois de traduzir, verificou o resultado antes de substituir o original. Nos quatro relatórios arquivados:

| Relatório | Conferência por regeneração | Frases do mapeador traduzidas | Justificativas da IA mantidas em inglês |
|---|---|---|---|
| datasul | sem divergência | 569 | 119 |
| oracle | sem divergência | 146 | 160 |
| sap_procosa | não feita: o relatório não tem prova aritmética nem as abas derivadas | 114 | 113 |
| sap_terumo | sem divergência | 545 | 110 |
| total | | 1.374 | 502 |

A contagem por situação, os 26 pareceres e as 26 observações de sap_procosa ficaram iguais. O `mapeval` imprime a mesma saída, byte a byte, de antes da migração (ENTREGUES 32/56, encontrados 43/56, fabricações 0), e o apply entrega as mesmas 19, 18, 62 e 67 colunas, com as células de dados idênticas às da planilha em inglês.

Para converter um relatório antigo que você tenha, use o `migrate_reports.py` do commit `5fe5092`, como mostra o [README](../README.md#comandos-do-dia-a-dia).

### Nomes legados em inglês

Referência histórica, para quem lê uma cópia antiga de um relatório ou de uma planilha mapeada, ou mantém código que procura os nomes antigos. Nada no repositório lê esses nomes; um relatório com eles é recusado (ver [acima](#esquema-em-português-e-esquema-legado)). A fonte é `report_schema.py` (`SHEETS` e `LEGACY_SHEETS`, `COLUMNS`, `METADATA_KEYS`, `METADATA_SENTINELS`, `STEP_IDS`, `GUARD_IDS`, `LEGACY_STEP_IDS`, `ENUMS` e as constantes `AGREES_SUFFIX_*`, `OCCURRENCE_SUFFIX_*` e `UNNAMED_COLUMN_*`); se esta tabela e o código divergirem, vale o código. Duas partes só constam desta tabela, e não do código: os nomes antigos das abas da planilha mapeada (`Mapped Data` e `Coverage`) e as frases antigas, em inglês, de `motivo_retencao` e `situacao_cobertura`, que o mapeador e o apply escrevem em português na origem (`report_schema` não guarda mais a versão em inglês delas). Os demais nomes de coluna, chaves e valores enumerados que o código usa em memória são os próprios nomes legados.

**Abas e célula A1**

| Legado | Português | Arquivo |
|---|---|---|
| `Mappings` | `Mapeamentos` | relatório |
| `Run Metadata` | `Metadados da Execução` | relatório |
| `Unmatched Client Columns` | `Colunas do Cliente sem Uso` | relatório |
| `Adjudication Queue` | `Fila de Revisão` | relatório |
| `Mapped Data` | `Dados Mapeados` | planilha mapeada |
| `Coverage` | `Cobertura` | planilha mapeada |
| `__mapping_info__` (A1) | `__info_mapeamento__` (A1) | planilha mapeada, `Dados Mapeados` |

**Colunas e rótulos do banner.** Legenda da última coluna: M = `Mapeamentos`, B = banner de `Dados Mapeados`, U = `Colunas do Cliente sem Uso`, F = `Fila de Revisão`, C = `Cobertura`.

| Legado | Português | Onde |
|---|---|---|
| `model_column_number` | `numero_coluna_modelo` | M, F, C |
| `model_column` | `coluna_modelo` | M, F, C |
| `client_column_number` | `numero_coluna_cliente` | M, B, U, C |
| `client_column` | `coluna_cliente` | M, B, U, C |
| `name_confidence` | `confianca_nome` | M, B |
| `name_runner_up_confidence` | `confianca_nome_segunda_melhor` | M, B |
| `name_margin` | `margem_nome` | M, B, F |
| `data_confidence` | `confianca_dados` | M, B |
| `final_confidence` | `confianca_final` | M, B, F, C |
| `method` | `metodo` | M, B |
| `status` | `situacao` | M, B |
| `reason` | `justificativa` | M, B, C |
| `catalog_name` | `nome_catalogo` | M, B |
| `catalog_coverage` | `cobertura_catalogo` | M, B |
| `catalog_distinct_coverage` | `cobertura_distinta_catalogo` | M |
| `catalog_confidence` | `confianca_catalogo` | M, B |
| `arithmetic_proof` | `prova_aritmetica` | M, B, F |
| `supported_by` | `apoiado_por` | M, B |
| `decided_by` | `decidido_por` | M, B |
| `vetoed_by` | `vetado_por` | M, B |
| `llm_data_confidence` | `confianca_dados_ia` | M, B |
| `llm_final_confidence` | `confianca_final_ia` | M, B |
| `reviewer_status` | `parecer_revisor` | M, B |
| `correct_client_column` | `coluna_cliente_correta` | M |
| `reviewer_note` | `observacao_revisor` | M, B |
| `key`, `value` | `chave`, `valor` | `Metadados da Execução` |
| `disposition` | `disposicao` | U |
| `review_priority` | `prioridade_revisao` | U |
| `rows_with_value` | `linhas_com_valor` | U, F |
| `distinct_values` | `valores_distintos` | U |
| `signals` | `sinais` | U |
| `signal_detail` | `detalhe_sinais` | U |
| `closest_model_column` | `coluna_modelo_mais_proxima` | U |
| `closest_name_confidence` | `confianca_nome_mais_proxima` | U |
| `rank` | `posicao` | F |
| `proposed_client_column_number` | `numero_coluna_cliente_proposta` | F |
| `proposed_client_column` | `coluna_cliente_proposta` | F |
| `gate_distance` | `distancia_limiar` | F |
| `resolver_disagrees` | `divergencia_ia` | F |
| `client_sample_values` | `amostra_valores_cliente` | F |
| `why_held` | `motivo_retencao` | F |
| `state` | `situacao_cobertura` | C |
| `excel_column` | `coluna_excel` | C |

**Chaves de `Metadados da Execução`**

| Legado | Português |
|---|---|
| `mapping_schema_version` | `versao_esquema_mapeamento` |
| `generated_at_utc` | `gerado_em_utc` |
| `prompt_version` | `versao_prompt` |
| `client_file` | `arquivo_cliente` |
| `client_sha256` | `sha256_cliente` |
| `model_file` | `arquivo_modelo` |
| `model_sha256` | `sha256_modelo` |
| `client_sheet` | `aba_cliente` |
| `model_sheet` | `aba_modelo` |
| `client_header_row` | `linha_cabecalho_cliente` |
| `model_header_row` | `linha_cabecalho_modelo` |
| `embedding_model` | `modelo_embedding` |
| `llm_model` | `modelo_ia` |
| `name_threshold` | `limiar_nome` |
| `name_margin` | `margem_minima_nome` |
| `final_threshold` | `limiar_final` |
| `candidate_count` | `quantidade_candidatos` |
| `sample_size` | `tamanho_amostra` |
| `reference_manifest_sha256` | `sha256_manifesto_referencia` |
| (não existe no legado) | `migrado_da_versao_esquema` |

A chave legada `name_margin` tem a mesma grafia da coluna `name_margin`, mas são coisas diferentes; em português elas se separam em `margem_minima_nome` (o limiar) e `margem_nome` (a margem medida).

**Valores enumerados**

| Campo | Legado → português |
|---|---|
| `situacao` | `accepted` → `aceito`; `review` → `revisao`; `no_match` → `sem_correspondencia` |
| `metodo` | `name` → `nome`; `data` → `dados`; `catalog` → `catalogo`; `name_only` → `somente_nome`; `data_error` → `erro_dados` |
| `parecer_revisor` | `correct` → `correto`; `incorrect` → `incorreto`; `no_mapping_exists` → `inexistente` (na leitura, os valores em inglês são recusados) |
| `vetado_por` | `vacuity` → `coluna_vazia`; `family_conflict` → `conflito_tributario` |
| `apoiado_por`, `decidido_por` | `resolver` → `resolvedor_ia`; `lexical` → `lexico`; `semantic` → `semantico`; `profile` → `perfil`; `identifier` → `identificador`; `catalog` → `catalogo`; `arithmetic` → `aritmetica`; `unrecorded` → `nao_registrado`; sufixo ` (agrees)` → ` (concorda)` |
| `nome_catalogo` | `ibge_municipality` → `municipio_ibge`; `ibs_cbs_classification` → `cclasstrib`; `ibs_cbs_cst` → `cst_ibs_cbs`; `cst_icms_taxation` → `cst_icms_tributacao`. Os outros seis (`cfop`, `ncm`, `cst_icms`, `csosn`, `cst_ipi`, `cst_pis_cofins`) não mudam. |
| `disposicao` | `proposed_not_accepted` → `proposta_nao_aceita`; `never_referenced` → `nunca_referenciada` |
| `prioridade_revisao` | `shortlist` → `prioritaria`; `low` → `baixa` |
| `sinais` | `arithmetic` → `aritmetica`; `catalog` → `catalogo`; `nfe_key_checkdigit` → `dv_chave_nfe`; `cnpj_checkdigit` → `dv_cnpj`; `cpf_checkdigit` → `dv_cpf`; `date` → `data` |
| `motivo_retencao` | `below the accept threshold` → `abaixo do limiar de aceite`; `name evidence only - this branch cannot auto-accept however well the headers match` → `só há evidência de nome - este caminho não aceita automaticamente, por mais que os cabeçalhos coincidam`; `the proposed column has no populated rows - confirming this would ship a blank column` → `a coluna proposta não tem linhas preenchidas - confirmá-la entregaria uma coluna em branco` |
| `situacao_cobertura` | `delivered` → `entregue`; `awaiting decision` → `aguardando decisão`; `not in this client's export` → `não consta na exportação do cliente` |
| `sha256_manifesto_referencia` | `disabled` → `desativado` |
| `modelo_embedding`, `modelo_ia` | `offline` continua `offline` |
| nomes de exibição | `X (occurrence N)` → `X (ocorrência N)`; `Unnamed column N` → `Coluna sem nome N` |

As outras duas travas de `mapping_steps.py`, `underspecified` (`cabecalho_generico`) e `exclusivity` (`coluna_ja_consumida`), também têm id em português, mas nunca são gravadas em `vetado_por` (item [2.1 de KNOWN_ISSUES.md](KNOWN_ISSUES.md#21-vetado_por-só-registra-duas-das-quatro-travas)).

## Catálogos de referência: `reference_data/`

Esta pasta guarda um retrato das tabelas oficiais de códigos. O mapeador as usa como evidência independente de cabeçalho: uma coluna cujos valores são CFOPs válidos é uma coluna de CFOP, diga o cabeçalho o que disser.

| Arquivo | Catálogo | Valor em `nome_catalogo` | Códigos | Largura | Normalizador | Especificidade | Fonte (`source_version`) |
|---|---|---|---|---|---|---|---|
| `cfop.csv` | CFOP | `cfop` | 619 | 4 | `cfop` | 1,0 | Portal da NF-e, IT 2023.002 v1.00; vigência 2023-04-24 |
| `ncm.csv` | NCM | `ncm` | 10.515 | 8 | `punctuated_digits` | 1,0 | Portal Único Siscomex, "Vigente em 04/08/2026" |
| `ibge_municipality.csv` | código de município do IBGE | `municipio_ibge` | 5.571 | 7 | `digits` | 1,0 | tabela de municípios do Portal da NF-e publicada em 2025-02-18 |
| `ibs_cbs_classification.csv` | cClassTrib do IBS/CBS | `cclasstrib` | 164 | 6 | `digits` | 0,85 | Portal da NF-e, IT 2025.002 v1.60; publicada em 2026-06-23 |
| `ibs_cbs_cst.csv` | CST do IBS/CBS | `cst_ibs_cbs` | 18 | 3 | `digits` | 0,75 | mesma publicação |
| `cst_icms.csv` | CST do ICMS (dígito de origem + tributação) | `cst_icms` | 99 | 3 | `digits` | 0,75 | Guia Prático da EFD ICMS/IPI 3.2.2, tabela 4.3.1 |
| `csosn.csv` | CSOSN | `csosn` | 10 | 3 | `digits` | 0,75 | idem, tabela 4.3.1 |
| `cst_icms_taxation.csv` | CST do ICMS, só os dígitos de tributação | `cst_icms_tributacao` | 11 | 2 | `digits` | 0,50 | idem, tabela 4.3.1 |
| `cst_ipi.csv` | CST do IPI | `cst_ipi` | 14 | 2 | `digits` | 0,55 | idem, tabela 4.3.2 |
| `cst_pis_cofins.csv` | CST do PIS/COFINS | `cst_pis_cofins` | 33 | 2 | `digits` | 0,55 | tabelas II e III de CST da EFD-Contribuições |

Os ids de catálogo, os nomes dos arquivos e o `manifest.json` continuam em inglês; só o valor gravado em `nome_catalogo` muda, e só nos ids com palavras em inglês.

Todo CSV tem as colunas `code,description,valid_from,valid_to`. Só `code` é lida (`reference_catalog.py`: `ReferenceCatalogs.from_directory` carrega cada CSV com `_load_catalog`). Antes da consulta, a célula é normalizada para a largura do catálogo:

- Os zeros à esquerda que o Excel tirou são restaurados.
- `cfop` também aceita a forma `5102/XX`.
- `punctuated_digits` remove `.`, `-`, `/` e espaços (então `8471.30.12` funciona).

A especificidade mede o quanto um casamento prova. CFOP, NCM e códigos do IBGE vêm de domínios grandes e únicos no país (1,0). Os domínios curtos de CST colidem entre tributos e por isso são descontados. Com o limiar padrão (`--final-threshold 0.90`), só os catálogos de especificidade 1,0 conseguem decidir um mapeamento sozinhos. A seção [Catálogos de referência](ARCHITECTURE.md#catálogos-de-referência) de ARCHITECTURE.md cobre as regras.

O `manifest.json` guarda:

- `schema_version`: 1.
- `generated_at`: `2026-08-04T22:14:31.359847+00:00`.
- `catalogs`: uma entrada por catálogo, com `file`, `width`, `normalizer`, `specificity`, `source_version`, `source_url` e `sha256`.

Na carga, o SHA-256 de cada CSV precisa conferir com a sua entrada no manifesto, ou o mapeador para e sugere `update_reference_data.py` ou `--no-reference-data`. Todo relatório registra o hash do próprio manifesto em `sha256_manifesto_referencia`. `--reference-data` usa por padrão a pasta `reference_data` ao lado de `invoice_mapper.py`, então o mapeador a encontra de qualquer diretório de trabalho.

Duas peculiaridades:

- `ibs_cbs_cst.csv` tem 19 linhas, mas 18 códigos. Uma linha tem o código em branco, e o carregador a descarta.
- Em `ibs_cbs_classification.csv`, os códigos 220001 a 220003 têm `valid_to` 2026-01-01, mas continuam contando como válidos, porque as datas não são lidas.

**Atualização.** O `update_reference_data.py` precisa de acesso à rede e sobrescreve arquivos:

- Baixa o CFOP, o NCM, a tabela de municípios e a planilha do IBS/CBS (que gera dois CSV).
- Grava as cinco tabelas de CST/CSOSN a partir de listas no próprio script.
- Regrava todos os CSV e o `manifest.json`. `--output` usa `reference_data` relativo ao diretório atual por padrão, então uma execução a partir da raiz do repositório substitui o retrato no lugar.

Os nomes das abas do IBS/CBS estão fixos no código para a publicação de 2026-06-01 (`CST 2026-06-01 Pub` e `cClass 2026-06-01 Pub`), então uma publicação mais nova provavelmente vai exigir mudança no código. Para atualizar, grave primeiro numa pasta temporária, compare, depois copie e commite os CSV e o manifesto juntos:

```bash
python update_reference_data.py --output /tmp/reference_data_new
diff -r reference_data /tmp/reference_data_new
```

Um retrato novo muda `sha256_manifesto_referencia` nos relatórios futuros. O apply não confere esse hash, então os relatórios existentes continuam aplicáveis.

## Como regenerar as saídas

Rode os comandos da raiz do repositório. O mapeamento offline, o apply e o `mapeval` nunca acessam a OpenAI.

Para reconstruir os quatro entregáveis a partir dos relatórios arquivados, rode os comandos abaixo. Eles imprimem `colunas_mapeadas=19`, `18`, `62` e `67`, respectivamente (cada um numa linha `colunas_modelo=227 linhas_cliente=... colunas_mapeadas=...`), o que confere com as planilhas commitadas. Para conferir sem tocar em `data/mapped/`, troque o `--output` por um caminho em `/tmp`.

```bash
python apply_invoice_mapping.py data/clients/datasul_maio.xlsx data/reports/mapping_datasul_full.xlsx --output data/mapped/datasul_mapped.xlsx
python apply_invoice_mapping.py data/clients/oracle.xlsx data/reports/mapping_oracle_full.xlsx --output data/mapped/oracle_mapped.xlsx
python apply_invoice_mapping.py data/clients/sap_procosa.XLSX data/reports/mapping_sap_procosa_full.xlsx --output data/mapped/sap_procosa_mapped.xlsx
python apply_invoice_mapping.py data/clients/sap_terumo.XLSX data/reports/mapping_sap_terumo_full.xlsx --output data/mapped/sap_terumo_mapped.xlsx
```

Para produzir um relatório só de cabeçalhos, sem a OpenAI (imprime `aceito=47 revisao=180 sem_correspondencia=0`):

```bash
python invoice_mapper.py data/clients/sap_terumo.XLSX --offline --output /tmp/mapping_sap_terumo_offline.xlsx
```

Relatórios offline são um regime degenerado. Toda coluna do modelo não resolvida vira `revisao` com `metodo` `somente_nome`, então não há linhas `sem_correspondencia` enquanto sobrar coluna do cliente sem uso. Eles não são comparáveis com os relatórios online arquivados, e os números deles nunca são o número do produto.

Uma execução online envia dados à OpenAI (ver [Confidencialidade](#confidencialidade)) e precisa de `OPENAI_API_KEY`:

```bash
python invoice_mapper.py data/clients/oracle.xlsx --output data/reports/mapping_oracle_rerun.xlsx
```

Grave as execuções online com um nome novo. Sobrescrever um relatório arquivado tem três efeitos:

- Apaga os pareceres do revisor daquele relatório.
- Move a linha de base do `mapeval` que o `tests/test_mapeval.py` fixa, porque o resolvedor de IA não é determinístico.
- Exige uma planilha regenerada correspondente em `data/mapped/`.

Os testes de `tests/test_mapeval.py` que precisam das planilhas são pulados quando `data/keys/de_para_oracle.xlsx` não existe, então o resto da suíte roda sem os dados. A suíte está descrita em [EVALUATION.md](EVALUATION.md#suíte-de-testes).
