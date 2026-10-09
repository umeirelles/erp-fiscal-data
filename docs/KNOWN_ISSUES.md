# Problemas conhecidos

Esta lista foi compilada em 2026-10-08 e revista em 2026-10-09 contra o commit `d97ee1a` da branch `traducao-pt-br`, depois da tradução do repositório para o português e da migração dos quatro relatórios arquivados para o esquema em português. Os números de linha se referem a esse estado e vão se deslocar na primeira mudança no código; a referência estável é o nome do símbolo. Toda contagem abaixo foi medida de novo nesse estado, sobre os relatórios migrados. O dono do projeto preferiu documentar estes problemas a corrigi-los antes da entrega. Cada item diz onde está o problema, o que acontece, como ver, o que afeta e uma correção sugerida.

Convenção de nomes: o código continua pensando em inglês (identificadores, ids de etapa, valores internos como `accepted`), e tudo o que uma pessoa lê nos arquivos está em português. A fronteira entre os dois é `report_schema.py`. Quando um item cita uma aba, coluna ou valor de um relatório de mapeamento ou de uma planilha mapeada, o nome entre crases é o que `report_schema` grava (`Mapeamentos`, `apoiado_por`, `aceito`). Quando cita o código, usa o nome interno (`supported_by`, `status == "accepted"`). Nomes de arquivo e de pasta, flags da linha de comando e identificadores continuam em inglês.

O contexto está em outros documentos: o pipeline em [ARCHITECTURE.md](ARCHITECTURE.md), as planilhas em [DATA.md](DATA.md), a avaliação (`mapeval`) e os números em [EVALUATION.md](EVALUATION.md), e a instalação e as regras do projeto no [README](../README.md). Afirmações que estavam erradas em versões anteriores do README foram corrigidas lá e não se repetem aqui; este arquivo lista só as causas que continuam no código. Os termos fiscais seguem o [vocabulário de ARCHITECTURE.md](ARCHITECTURE.md#vocabulário).

## Antes de corrigir qualquer coisa

- O escopo é o algoritmo de correspondência. Uma mudança nele precisa se sustentar na validação deixando um cliente de fora. Nenhum código pode ramificar pelo nome de um cliente: os quatro clientes de amostra são exemplos de referência, nunca conjunto de treino. Prefira evidência que se transfere para um ERP nunca visto. Os gabaritos de-para são teste de regressão, não dado de treino. Ver o [protocolo de EVALUATION.md](EVALUATION.md#protocolo-para-uma-mudança-proposta).
- Fabricação é a falha que importa, e precisão vale mais que recall. O limiar de aceite de 0,90 fica; o caminho para entregar a faixa retida é o parecer do revisor. Cobertura parcial é o desenho: o denominador nunca é 227, e uma coluna que o cliente não tem não é defeito; nunca a chame de "faltante", "lacuna" ou "incompleta". Cite sempre os ENTREGUES antes dos encontrados.
- O `data/model.xlsx` é imutável por ora. O repositório guarda dados reais de clientes e é só para uso interno.
- Rode as duas verificações antes e depois de uma mudança. As duas são offline e não chamam a OpenAI:

  ```bash
  python -m pytest -q   # 451 testes passam em cerca de 10 s; os 10 marcados como lento ficam de fora
  python -m mapeval     # ENTREGUES 32/56 (encontrados 43/56), fabricações 0
  ```

  As 10 provas de ponta a ponta sobre os dados reais rodam com `python -m pytest -m lento` (cerca de 4 minutos) e precisam do commit `2ae4b6d` no git (ver 6.8). Vários testes fixam totais absolutos (ver 3.19), então uma melhoria pode quebrar um teste. Quando isso acontecer, atualize o valor fixado de propósito.
- Não regenere `data/reports/mapping_sap_procosa_full.xlsx` com o mapeador. Ele guarda os únicos 26 pareceres do revisor do repositório, e regenerá-lo os apaga (ver 3.3).
- Qualquer mudança no prompt, no prefixo do embedding, em `DataMappingDecision` ou no payload do resolvedor de IA quebra `tests/test_model_input_golden.py` de propósito (ver 6.1).

## Índice

| ID | Problema | Onde fica |
|---|---|---|
| **1** | **Afeta entregáveis ou relatórios versionados** | |
| 1.1 | O backfill lê células deslocadas, e `resolvedor_ia` não aparece em `apoiado_por` | `backfill_mapping_steps.py` `backfill` |
| 1.2 | A `Cobertura` mostra como `aguardando decisão` linhas que o revisor marcou `inexistente` | `apply_invoice_mapping.py` `_write_coverage_sheet` |
| 1.3 | A `Cobertura` deixa `numero_coluna_cliente` em branco nas linhas entregues | `apply_invoice_mapping.py` `_write_coverage_sheet` |
| **2** | **Pode enganar um revisor ou uma métrica** | |
| 2.1 | `vetado_por` só registra duas das quatro travas | `invoice_mapper.py` `map_columns` |
| 2.2 | `decidido_por = resolvedor_ia` nunca acontece na prática | `invoice_mapper.py` `_supporting_step_ids` |
| 2.3 | O backfill e a execução nova creditam a evidência de catálogo de jeitos diferentes | `backfill_mapping_steps.py`, `invoice_mapper.py` |
| 2.4 | Duas definições da métrica de generalização | `mapeval/score.py`, `mapping_steps.py` |
| 2.5 | O mapeval imprime a contagem de aceitos como `pendências de revisão` | `mapeval/cli.py` `main` |
| 2.6 | O mapeval ignora os pareceres do revisor | `mapeval/score.py` `Score.delivered_correct` |
| 2.7 | Aceites fora do gabarito não são conferidos contra o gabarito (oracle m41/m42) | `mapeval/outcomes.py` `classify` |
| 2.8 | O mapeval casa as colunas do modelo pela posição, nunca pelo nome | `mapeval/score.py` `score_report` |
| 2.9 | O mapeval calcula valores que nunca informa | `mapeval/score.py`, `mapeval/outcomes.py` |
| 2.10 | `metodo` diz o ramo do pipeline, não a evidência | `invoice_mapper.py` `map_columns` |
| 2.11 | `cobertura_distinta_catalogo` fica vazia nas linhas da segunda passagem | `invoice_mapper.py` `map_columns` |
| 2.12 | Várias linhas em revisão podem propor a mesma coluna do cliente | `invoice_mapper.py` `map_columns` |
| 2.13 | `confianca_final = 0,8999` é um teto, não uma medida | `invoice_mapper.py` `map_columns` |
| 2.14 | `margem_nome` pode ser negativa em linhas aceitas | `invoice_mapper.py` `_name_score_context` |
| 2.15 | O modo offline é outro pipeline | `invoice_mapper.py` `map_columns` |
| **3** | **Robustez e tratamento de dados** | |
| 3.1 | Falhas da API viram linhas em revisão e a execução sai com 0 | `invoice_mapper.py` `map_columns` |
| 3.2 | Valores do cliente vão para a OpenAI sem mascaramento | `invoice_mapper.py` `OpenAIDataResolver.resolve` |
| 3.3 | Regenerar um relatório apaga os pareceres do revisor | `invoice_mapper.py` `write_results` |
| 3.4 | O relatório vai, por padrão, para a pasta atual | `invoice_mapper.py` `_run` |
| 3.5 | A saída em CSV perde três abas | `invoice_mapper.py` `write_results` |
| 3.6 | A verificação de relatório desatualizado do apply pode ser pulada | `apply_invoice_mapping.py` `_run_metadata` |
| 3.7 | Problemas menores na leitura das entradas do apply | `apply_invoice_mapping.py` |
| 3.8 | O DV de CPF/CNPJ falha em células numéricas | `invoice_mapper.py` `_cnpj_is_valid` |
| 3.9 | 'ICM', 'IS' e 'DIF_ALIQ' não são famílias de tributo | `invoice_mapper.py` `_TAX_FAMILIES` |
| 3.10 | A evidência de identificador depende da mistura de CPF e CNPJ nas amostras do model.xlsx | `invoice_mapper.py` `identifier_match_score` |
| 3.11 | As colunas 'Tipo de Imposto …' do modelo não recebem evidência de catálogo | `reference_catalog.py` `expected_catalogs` |
| 3.12 | A pontuação de catálogo ignora quantos valores estão bem formados | `reference_catalog.py` `ReferenceCatalogs.analyze` |
| 3.13 | Códigos cClassTrib vencidos continuam valendo | `reference_catalog.py` `from_directory` |
| 3.14 | A atualização dos catálogos de referência é frágil | `update_reference_data.py` |
| 3.15 | Uma chave desconhecida no manifesto termina em traceback cru | `reference_catalog.py` `from_directory` |
| 3.16 | Nenhum teste cobre um checksum que não confere | `tests/` |
| 3.17 | `backfill_mapping_steps.py` tem poucas verificações de segurança (resolvido em parte) | `backfill_mapping_steps.py` |
| 3.18 | A expansão de prefixos de palavras de papel casa palavras comuns | `invoice_mapper.py` `expand_truncated_role_words` |
| 3.19 | A CLI e os testes do mapeval são frágeis | `mapeval/cli.py`, `tests/test_mapeval.py` |
| 3.20 | Detalhes da instalação: `.env` e nenhum lockfile | raiz do repositório |
| **4** | **Documentação e comentários desatualizados no código** | |
| 4.1 | Números de linha de base desatualizados em comentários | `mapeval/truth.py`, `tests/test_mapeval.py`, `invoice_mapper.py` |
| 4.2 | `mapping_steps.py` cita funções que não existem | `mapping_steps.py` |
| 4.3 | Comentários descrevem comportamento que o código não tem | vários |
| **5** | **Peculiaridades dos dados** | |
| 5.1 | Os layouts dos relatórios diferem, e a versão do esquema não diz qual é qual | `data/reports/` |
| 5.2 | Contagem de linhas dos gabaritos | `data/keys/` |
| 5.3 | `de_para_procosa.xlsx` não é um gabarito de colunas e está deslocado em uma coluna | `data/keys/` |
| 5.4 | O model.xlsx tem cabeçalhos repetidos | `data/model.xlsx` |
| 5.5 | Os cabeçalhos da exportação datasul têm espaços sobrando | `data/clients/datasul_maio.xlsx` |
| **6** | **Introduzidos ou expostos pela tradução** | |
| 6.1 | As justificativas escritas pela IA continuam em inglês | `invoice_mapper.py` `OpenAIDataResolver.resolve` |
| 6.2 | Texto de exceção do Python, em inglês, dentro de mensagens em português | `invoice_mapper.py` `REASON_DATA_ERROR`, `_error_message` |
| 6.3 | Nomes de exibição ambíguos: cabeçalho literal 'Coluna sem nome N' ou '… (ocorrência N)' | `report_schema.py` `display_name_to_internal` |
| 6.4 | O parecer do revisor é aceito nas duas línguas, mas `situacao` só no valor exato | `report_schema.py` `normalize_reviewer_status` |
| 6.5 | `python -m mapeval --client <desconhecido>` ainda termina em traceback de `KeyError` | `mapeval/cli.py` `main` |
| 6.6 | `legacy_reports.py` depende de auxiliares privados do mapeador e espelha a rotulagem do conflito | `legacy_reports.py` |
| 6.7 | Uma migração interrompida deixa um arquivo temporário oculto | `migrate_reports.py` `migrate_report` |
| 6.8 | Os testes `lento` precisam do commit `2ae4b6d` no git | `tests/test_migrate_reports.py` |
| 6.9 | As planilhas mapeadas mudaram de nome de aba e de rótulos | `apply_invoice_mapping.py`, `report_schema.py` |

---

## 1. Afeta entregáveis ou relatórios versionados

### 1.1 O backfill lê células deslocadas, e `resolvedor_ia` não aparece em `apoiado_por`

- **Onde:** `backfill_mapping_steps.py`, `backfill`. O índice das colunas é montado na linha 147, `sheet.insert_cols` roda na 156, `stored()` (165–166) continua usando o índice antigo e `llm_final_confidence` é lido na 184. O comentário em 160–164 aponta este item.
- **O que acontece:** no preenchimento retroativo (backfill), `backfill` insere as três colunas de etapas numa âncora e depois lê os outros campos pelas posições que anotou antes da inserção. Todo campo à direita da âncora passa a ser lido três células à esquerda de onde está.
  - Nos relatórios de datasul, oracle e sap_terumo, a âncora é `arithmetic_proof`. A leitura de `llm_final_confidence` cai na célula recém-inserida de `vetoed_by`, ainda vazia, então o resolvedor de IA nunca é creditado.
  - Em sap_procosa, a âncora é `reviewer_status`, que vem depois das colunas `llm_*`, então esse relatório está correto.
  - Todos os outros campos que o backfill lê (`status`, `method`, as pontuações e as colunas de catálogo) ficam à esquerda da âncora e são lidos corretamente.

  Esses nomes são os do esquema legado em inglês, porque o backfill só roda em relatórios legados (ver 3.17). Nos relatórios migrados, as colunas correspondentes são `confianca_final_ia`, `apoiado_por`, `vetado_por` etc.
- **Como ver:** compare as linhas com proposta e `confianca_final_ia > 0` com as linhas cujo `apoiado_por` contém `resolvedor_ia`. As contagens são datasul 75 contra 0, oracle 32 contra 0, sap_terumo 78 contra 0 e sap_procosa 60 contra 60.

  ```bash
  python - <<'EOF'
  import pandas as pd
  for c in ["datasul", "oracle", "sap_procosa", "sap_terumo"]:
      m = pd.read_excel(f"data/reports/mapping_{c}_full.xlsx", sheet_name="Mapeamentos")
      ia = pd.to_numeric(m["confianca_final_ia"], errors="coerce").fillna(0) > 0
      proposta = m["numero_coluna_cliente"].notna()
      creditada = m["apoiado_por"].fillna("").str.contains("resolvedor_ia")
      print(c, int((ia & proposta).sum()), int(creditada.sum()))
  EOF
  ```

  O erro chega a `data/mapped/`, porque o apply copia `apoiado_por` para o banner da aba `Dados Mapeados`. Em 3 colunas entregues de datasul e 7 de sap_terumo, o resolvedor contribuiu para a escolha, mas o banner não diz isso. A migração para o português levou o erro adiante como estava: ela traduz os ids de etapa, não refaz o preenchimento.
- **Impacto:** nos três relatórios afetados, 185 linhas subestimam o papel do resolvedor. `decidido_por` e `vetado_por` não são afetados: o resolvedor tem a posição mais baixa, então nunca vence em `decidido_por`. Os números do mapeval também não, porque o mapeval não lê `apoiado_por`.
- **Correção:** depois do laço do cabeçalho em 157–158, recalcule `index` a partir da linha 1. Isso foi testado em cópias dos relatórios anteriores ao backfill (`git show 88c503b^:mapping_<cliente>_full.xlsx`; naquele commit os relatórios ficavam na raiz). Com essa linha a mais, o backfill credita o resolvedor em exatamente 75/32/60/78 linhas, e toda célula de `decided_by` e `vetoed_by` é idêntica à dos relatórios arquivados. Para consertar os arquivos versionados, a ordem importa, porque o backfill recusa o esquema em português:
  1. Restaure de `88c503b^` para `data/reports/` os três relatórios afetados.
  2. Rode o backfill corrigido com a exportação correspondente de `data/clients/`.
  3. Migre cada um com `python migrate_reports.py data/reports/mapping_<cliente>_full.xlsx data/clients/<exportação>`.
  4. Rode de novo o `apply_invoice_mapping.py` para esses três clientes.

  Numa cópia de oracle feita assim, a migração passou na conferência por regeneração e o resultado difere do relatório arquivado só em `apoiado_por`, nas 32 linhas esperadas. sap_procosa não precisa de conserto. O passo 1 precisa do histórico completo do git (ver 6.8).

### 1.2 A `Cobertura` mostra como `aguardando decisão` linhas que o revisor marcou `inexistente`

- **Onde:** `apply_invoice_mapping.py`, `_write_coverage_sheet`, linhas 508–513. A situação sai só do `status` do mapeador, que `build_mapped_workbook` junta de `mapping["status"]` em 667–669. O parecer do revisor nunca é consultado.
- **O que acontece:** suponha que o mapeador deixou uma linha em `revisao` e o revisor depois a marcou como `inexistente`. É uma decisão encerrada: a exportação do cliente não tem esse campo. A aba `Cobertura` ainda põe a linha em `aguardando decisão`.
- **Como ver:** a aba `Cobertura` de `data/mapped/sap_procosa_mapped.xlsx` lista 51 linhas em `aguardando decisão`. Nove delas têm `inexistente` no relatório, então só 42 estão de fato pendentes.

  ```bash
  python - <<'EOF'
  import pandas as pd
  cob = pd.read_excel("data/mapped/sap_procosa_mapped.xlsx", sheet_name="Cobertura")
  rel = pd.read_excel("data/reports/mapping_sap_procosa_full.xlsx", sheet_name="Mapeamentos")
  m = cob.merge(rel[["numero_coluna_modelo", "parecer_revisor"]], on="numero_coluna_modelo")
  print(pd.crosstab(m["situacao_cobertura"], m["parecer_revisor"].fillna("(nenhum)")))
  EOF
  ```

- **Impacto:** o revisor é mandado de volta a linhas já decididas, e em sap_procosa a contagem de `não consta na exportação do cliente` fica 9 abaixo do que o revisor estabeleceu. As outras três planilhas mapeadas não têm pareceres, então hoje não são afetadas.
- **Correção:** passe o parecer do revisor para `_write_coverage_sheet` e leve `no_mapping_exists` (o `inexistente` do relatório) a `STATE_ABSENT`. Uma ausência confirmada pelo revisor é uma resposta completa.

### 1.3 A `Cobertura` deixa `numero_coluna_cliente` em branco nas linhas entregues

- **Onde:** `apply_invoice_mapping.py`, `_write_coverage_sheet`, linha 522.
- **O que acontece:** numa linha entregue, a célula do número é gravada como `""`, enquanto `coluna_cliente` recebe o nome de exibição. As linhas que não foram entregues recebem o número proposto, quando há proposta.
- **Como ver:** toda linha `entregue` das quatro planilhas mapeadas tem o número em branco: datasul 19, oracle 18, sap_procosa 62 e sap_terumo 67. `coluna_cliente` está preenchida em todas.
- **Impacto:** justamente as linhas que um usuário a jusante mais quer rastrear até a exportação ficam sem o número de coluna usado em todo o resto, e quem lê precisa casar pelo nome de exibição.
- **Correção:** grave `column.index + 1` quando `column is not None`.

---

## 2. Pode enganar um revisor ou uma métrica

### 2.1 `vetado_por` só registra duas das quatro travas

- **Onde:** `invoice_mapper.py`, `map_columns`, linhas 3271–3275, o único lugar que grava o campo. `backfill_mapping_steps.py` `backfill` (240–245) grava os mesmos dois rótulos. O registro das travas é `mapping_steps.py` `GUARDS` (161–206).
- **O que acontece:** só duas travas são gravadas, e só em linhas da segunda passagem: `vacuity` (`coluna_vazia` no relatório), que recusa uma coluna do cliente vazia, e `family_conflict` (`conflito_tributario`), que recusa um conflito de família de tributo ou de papel. `underspecified` (`cabecalho_generico`) e `exclusivity` (`coluna_ja_consumida`) estão registradas, mas nenhum código as grava.

  Vários outros caminhos de recusa agem como veto e não deixam rótulo nenhum:
  - as recusas da primeira passagem (2585–2592): conflito de cabeçalho, cabeçalho genérico, coluna vazia
  - a contradição aritmética forte na regra de aceite da segunda passagem (3175–3185)
  - a auditoria de catálogo que reabre um aceite da primeira passagem (`_audit_accepted_catalog_matches`, 1642–1654)
  - o rebaixamento aritmético (`_demote_inconsistent_tax_header_matches`, a partir de 1762)
- **Como ver:** `grep -rn --include='*.py' -e '"underspecified"' -e '"exclusivity"' .` só encontra o registro (`mapping_steps.py` 185 e 196) e a tabela de tradução (`report_schema.py` `GUARD_IDS`, 289–290). Nos relatórios versionados, os valores são datasul `coluna_vazia` 6, sap_terumo `coluna_vazia` 11, sap_procosa `conflito_tributario` 3, e nenhum em oracle.
- **Impacto:** um `vetado_por` em branco é lido como "nenhuma trava disparou", o que muitas vezes é falso.
- **Correção:** dê um id de trava a cada caminho de recusa e grave-o na linha afetada. Nas recusas da primeira passagem, leve o id para a linha da segunda passagem. Um id novo precisa também de um nome em português em `report_schema.GUARD_IDS`; `tests/test_report_schema.py` acusa se faltar. Alternativamente, documente que a coluna cobre só essas duas travas.

### 2.2 `decidido_por = resolvedor_ia` nunca acontece na prática

- **Onde:** `invoice_mapper.py` `_supporting_step_ids`, linhas 1350–1351; `mapping_steps.py` `deciding_step` (223–239) e o comentário em 74–80.
- **O que acontece:** `decidido_por` é a etapa de posição mais alta em `apoiado_por`, e o resolvedor tem a mais baixa. A etapa de cabeçalho (`semantico` ou `lexico`) entra sempre que `name_confidence > 0`.
  - Online, as pontuações de cabeçalho são similaridades de cosseno limitadas a [0, 1] (1157), e nunca são 0 numa coluna proposta.
  - Offline, o resolvedor nem roda.

  Por isso, uma linha que a IA escolheu quando o cabeçalho não era decisivo aparece como decidida pelo cabeçalho.
- **Como ver:**
  - Nenhuma linha dos quatro relatórios tem `decidido_por = resolvedor_ia`.
  - A menor `confianca_nome` numa linha com proposta é 0,7688 (sap_procosa).
  - 222 linhas em revisão (datasul 68, oracle 32, sap_procosa 51, sap_terumo 71) têm uma escolha do resolvedor e um cabeçalho abaixo do limiar de 0,90 ou da margem de 0,05, e mesmo assim dizem `decidido_por = semantico`.
- **Impacto:** o significado prometido em `mapping_steps.py`, "nada objetivo sustentou o mapeamento e o julgamento da IA decidiu", nunca aparece, então o revisor não distingue as linhas decididas pelo cabeçalho das decididas pela IA. A parcela transferível não muda, porque nenhuma das duas etapas se transfere.
- **Correção:** conte a etapa de cabeçalho como decisiva só quando o cabeçalho decidiu sozinho (`name_is_decisive`, 3107–3111). Caso contrário, liste-a como corroboração, para que `resolver` possa vencer quando nada objetivo sustenta a linha.

### 2.3 O backfill e a execução nova creditam a evidência de catálogo de jeitos diferentes

- **Onde:**
  - Backfill: `backfill_mapping_steps.py` `DISTINCTIVE_CATALOG_SCORE` (72), o bloco do catálogo (196–212) e o ramo `explained_by_catalog` (228–237).
  - Execução nova: `invoice_mapper.py` `catalog_evidence_is_distinctive` (1683–1702). Os rótulos dos aceites da primeira passagem são montados em 2609–2617, antes de `_audit_accepted_catalog_matches` (chamada em 2623) anexar a evidência de catálogo.
- **O que acontece:** os dois caminhos diferem em três pontos.
  1. **Limiar.** O backfill trata um catálogo como decisivo a partir de 0,90, sem mais nada. A regra da execução nova usa um piso de 0,50 mais pelo menos 5 códigos distintos nos catálogos de especificidade 1,0, e 0,90 nos demais. Os catálogos de especificidade 1,0 são o CFOP, o NCM e o código de município do IBGE.
  2. **O atalho `explained_by_catalog`.** O backfill também credita o catálogo como decisivo sempre que `data_confidence` é igual a `catalog_confidence`, qualquer que seja o catálogo. Isso inclui as tabelas de CST, que a regra de distintividade da execução nova (`catalog_evidence_is_distinctive`) nunca trata como decisivas, porque a especificidade de 0,75 ou menos mantém a pontuação abaixo de 0,90.
  3. **Aceites da primeira passagem.** Numa execução nova, um aceite da primeira passagem nunca lista `catalog`, porque os rótulos são calculados antes de a auditoria anexar a evidência. O backfill lista.
- **Como ver:**
  - m176 de sap_procosa, 'Código de Sit Tributária do CBS e IBD': está em `revisao` e mostra `decidido_por = catalogo`, vindo de `cst_ibs_cbs` com 0,75.
  - m204 de datasul, 'Nº do NCM': é um aceite da primeira passagem (`metodo = nome`) que mostra `decidido_por = catalogo`. Um relatório gerado agora diria `semantico`.
- **Impacto:** `decidido_por`, e qualquer métrica construída sobre ele (2.4), muda quando um relatório é regenerado, mesmo com evidência idêntica.
- **Correção:** faça os dois caminhos chegarem ao mesmo veredito:
  - No backfill, recalcule a evidência de catálogo a partir da exportação do cliente, como ele já faz com a aritmética (`ReferenceCatalogs.analyze` é offline e barato), e aplique `catalog_evidence_is_distinctive`.
  - Elimine o atalho `explained_by_catalog`.
  - Na primeira passagem da execução nova, recalcule `supported_by`/`decided_by` depois da auditoria.

### 2.4 Duas definições da métrica de generalização

- **Onde:**
  - mapeval: `mapeval/score.py` `HEADER_INDEPENDENT_METHODS` (25) e `Row.header_independent` (43–64), impresso por `mapeval/cli.py` `main` (134–135) como "aceites independentes de cabeçalho … métrica de generalização".
  - Registro de etapas: `mapping_steps.py` `TRANSFERRING_STEP_IDS` (218–220) e `rests_only_on_headers` (251–259). Só os testes os usam.
- **O que acontece:** os dois arquivos decidem de modo diferente se um aceite se transferiria para um ERP nunca visto.
  - O mapeval conta um aceite como independente de cabeçalho só quando ele tem uma `prova_aritmetica` decisiva ou `metodo = catalogo`.
  - O registro conta um aceite como transferível quando ele se apoia em evidência de perfil, identificador, catálogo ou aritmética.

  Nenhum dos dois conta os aceites `nao_registrado`, que se apoiam em evidência objetiva que o backfill não soube nomear.
- **Como ver:**
  - Datasul: o mapeval imprime 36,8% (7 de 19 aceitos). Contando os aceites cujo `decidido_por` é uma etapa transferível, dá 8 de 19 (42,1%). A diferença é m204, 'Nº do NCM' (ver 2.3).
  - Oracle: os dois dão 6 de 18 (33,3%).
  - Os aceites `nao_registrado` contam como presos ao cabeçalho nas duas definições: datasul 2, entre eles 'Chave de acesso' ← 'Chave Nfe' com `confianca_dados` 1,0; sap_procosa 3; sap_terumo 1.
- **Impacto:** o número que o projeto tenta aumentar depende de qual arquivo você lê, e as duas versões contam a menos.
- **Correção:** fique com uma definição só. Calcule a parcela no mapeval a partir de `apoiado_por`/`decidido_por` com `mapping_steps.TRANSFERRING_STEP_IDS`, depois de corrigir 2.3; a célula lida do relatório precisa passar antes por `report_schema.steps_to_internal`, porque `mapping_steps` só reconhece os ids internos. `nao_registrado` só existe nos relatórios preenchidos pelo backfill: o mapeador atual já registra `perfil` e `identificador` separadamente e nunca grava esse valor.

### 2.5 O mapeval imprime a contagem de aceitos como `pendências de revisão`

- **Onde:** `mapeval/cli.py`, `main`, linhas 97–98.
- **O que acontece:** a linha dos clientes sem gabarito formata `len(accepted)` duas vezes.
- **Como ver:** `python -m mapeval` imprime `aceitos  45   pendências de revisão  45` para sap_procosa e `67 / 67` para sap_terumo. O número coincide hoje porque nenhuma linha aceita desses dois relatórios tem parecer. Confirmar um aceite não o reduziria.
- **Impacto:** as pendências nunca diminuem conforme os revisores trabalham.
- **Correção:** conte as linhas aceitas cujo parecer do revisor está em branco, e decida se as linhas em `revisao` ainda pendentes também entram na conta.

### 2.6 O mapeval ignora os pareceres do revisor

- **Onde:**
  - `mapeval/score.py` `Score.delivered_correct` (90–103). A docstring diz que o apply entrega as linhas aceitas e as confirmadas pelo revisor, mas o código conta só `status == "accepted"`.
  - O argumento `delivered=` em `score_report` (275).
  - O ramo dos clientes sem gabarito em `mapeval/cli.py` `main` (81–100).
  - A regra que o mapeval deveria seguir é `apply_invoice_mapping.py` `_effective_columns` (338–453). Ela entrega as linhas `correto` e as `incorreto` com correção, e descarta as `inexistente`, mesmo as aceitas.
- **O que acontece:** o mapeval trata a `situacao` do mapeador como "entregue", mas o que o apply de fato entrega depende também dos pareceres do revisor.
- **Como ver:**
  - Datasul e oracle não têm pareceres, então os números com gabarito não são afetados hoje.
  - sap_procosa tem 26 pareceres: 15 `correto`, 9 `inexistente` e 2 `incorreto`. A planilha mapeada dele traz 62 colunas (45 aceitas, 15 confirmadas e 2 corrigidas pelo revisor), enquanto a linha do mapeval mostra só os 45 aceitos.
  - A verificação de fabricação tem o mesmo ponto cego. Ela só olha as linhas aceitas (`score.py` 269–276, `cli.py` 88–96), e o apply não tem verificação própria de coluna vazia (`grep -n vacu apply_invoice_mapping.py` não encontra nada). Se um revisor confirmasse uma proposta sobre uma coluna do cliente vazia, o apply entregaria uma coluna em branco e o código de saída do mapeval não acusaria. Hoje não existe linha assim.
- **Impacto:** quando datasul ou oracle receberem pareceres, o número ENTREGUES estará errado. E o único bloqueio automático, a verificação de fabricação, não cobre as linhas confirmadas pelo revisor.
- **Correção:** leve a regra de seleção de linhas do apply para uma função que o apply e o mapeval chamem, e calcule a entrega e a fabricação a partir dela. A função precisa receber o parecer já normalizado por `report_schema.normalize_reviewer_status`, como o apply faz hoje.

### 2.7 Aceites fora do gabarito não são conferidos contra o gabarito (oracle m41/m42)

- **Onde:** `mapeval/outcomes.py` `classify` (74–80) devolve `UNDETERMINED` para qualquer coluna do modelo fora do gabarito; também `mapeval/score.py` `Score.undetermined_accepts` (136–151).
- **O que acontece:** o gabarito diz qual coluna do cliente alimenta cada coluna do modelo que ele cobre. Suponha que um aceite fora do gabarito pegue uma coluna do cliente que o gabarito atribui a outra coluna do modelo. É uma contradição direta, mas o mapeval só confere o lado da coluna do modelo e não a vê.
- **Como ver:**
  - Oracle aceita m41 'Base ICMS' ← c28 'BASE ICMS' e m42 'Valor ICMS' ← c24 'VALOR ICMS'.
  - O gabarito de oracle atribui c28 a m99 'Base ICSP' e c24 a m101 'Valor ICSP'. `python -m mapeval --client oracle --show-losses` lista m99 e m101 como `perdido`, com `esperada=c28` e `esperada=c24`.
  - Os dois aceites só entram na contagem da linha `sem_veredito` (3 em oracle), nunca são listados, e o código de saída é 0.
- **Impacto:** uma de duas coisas é verdade:
  - a planilha mapeada põe nas colunas de ICMS do modelo as colunas que o gabarito chama de ICSP, ou seja, uma coluna de tributo errada (exatamente o que a regra de fabricação existe para impedir); ou
  - o gabarito está errado nessas duas colunas, e elas pertencem a `KEY_CORRECTIONS`.

  O mapeval tem os dados para forçar essa decisão e não força.
- **Correção:** em `score_report`, dê a um aceite cuja coluna do cliente o gabarito atribui a outro lugar um resultado próprio (por exemplo `CONFLICTS_KEY`, com rótulo em português em `report_schema.ENUMS["outcome"]`), imprima-o e decida se ele deve bloquear. Depois resolva m41/m42 pelos dados do cliente. Não resolva com uma regra que mapeia cabeçalhos de ICMS para ICSP: seria ajustar o algoritmo ao vocabulário de um cliente (ver [EVALUATION.md](EVALUATION.md#os-5-aceites-sem-veredito)).

### 2.8 O mapeval casa as colunas do modelo pela posição, nunca pelo nome

- **Onde:** `mapeval/score.py` `score_report`, linha 242 (posição da linha na aba `Mapeamentos`), e `mapeval/truth.py` `load_answer_key`, linha 75 (posição na linha 1 do de-para).
- **O que acontece:** nenhuma das duas lê `numero_coluna_modelo` nem compara nomes. O apply, ao contrário, reordena as linhas pelo número e confere cada nome (`_ordered_mapping`, 249–300).
- **Como ver:** hoje os quatro relatórios e os dois gabaritos batem com o model.xlsx: os números coincidem com as posições e nenhum nome difere.
- **Impacto:** uma aba `Mapeamentos` filtrada, ordenada ou editada à mão seria pontuada contra as entradas erradas do gabarito, sem erro nenhum.
- **Correção:** indexe as linhas por `numero_coluna_modelo` (o campo interno `model_column_number`, que o índice devolvido por `read_mappings` já traz) e confira que cada nome bate com o model.xlsx, como faz o apply.

### 2.9 O mapeval calcula valores que nunca informa

- **Onde:** `mapeval/score.py` `Score.vacuous_proposals` (76, 281–282) e `Score.correct_pairs` (161–169); `mapeval/outcomes.py` `REGRESSION_GUARDED` (39) e `classify` (42–86).
- **O que acontece:**
  - `vacuous_proposals`, as propostas em revisão sobre colunas do cliente vazias, é calculado e nunca impresso. Dá 6 em datasul e 0 em oracle.
  - A CLI não usa `correct_pairs` nem `REGRESSION_GUARDED`.
  - Em `classify`, o resultado `CORRECTLY_ABSENT` e o ramo que leva `expected is None` a `FABRICATED` (82–85) só são alcançáveis pelos testes unitários. `score_report` passa `in_key = position in key.present`, o que já implica `expected` preenchido. Os dois braços do bloco `if not in_key` (74–80) devolvem `UNDETERMINED`.
- **Impacto:** baixo, mas quem lê pode supor que esses valores entram na pontuação. `correct_pairs` foi escrito para pegar regressões do tipo "perdeu uma, ganhou outra", que uma contagem igual esconde, e nada o executa.
- **Correção:** imprima `vacuous_proposals`. Ligue `correct_pairs` a uma comparação com uma linha de base guardada, ou apague-o junto com `REGRESSION_GUARDED`. Junte os ramos mortos.

### 2.10 `metodo` diz o ramo do pipeline, não a evidência

- **Onde:** `invoice_mapper.py` `map_columns`, linha 2763 (`decision_method = "catalog" if catalog_decision is not None else "data"`), reatribuída a `"data"` em 2840 e 2906 e gravada na linha em 3252. O mapeval lê `metodo` por `HEADER_INDEPENDENT_METHODS` e `method_census`.
- **O que acontece:** toda linha da segunda passagem recebe `metodo = dados`, ou `catalogo` quando a decisão local por catálogo disparou, qualquer que tenha sido a evidência que de fato a sustentou. `metodo = catalogo` não implica que a linha foi aceita.
- **Como ver:**
  - sap_terumo aceita 'Utilização de material' ← 'Material usage' (`confianca_dados` 0,84) e 'Nº NF-e dos serviços' ← 'Service NF-e Number' (`confianca_dados` 0,00). As duas têm `metodo = dados` e `decidido_por = semantico`, porque o cabeçalho foi decisivo.
  - Oracle 'CFOP' ← 'CFO' tem `metodo = catalogo` e `situacao = revisao` (confiança final 0,8547, pontuação de catálogo 0,8824).
- **Impacto:** o censo de métodos do mapeval, e qualquer um que filtre por `metodo`, interpreta mal essas linhas.
- **Correção:** documente `metodo` como "o ramo seguido" e use `decidido_por` para responder "o que decidiu". Alternativamente, renomeie a coluna na próxima versão do esquema, em `report_schema.COLUMNS` e com aumento de versão (ver 5.1).

### 2.11 `cobertura_distinta_catalogo` fica vazia nas linhas da segunda passagem

- **Onde:** o campo `MappingResult.catalog_distinct_coverage` (1396) só é preenchido por `_audit_accepted_catalog_matches` (1638–1640), e só para aceites da primeira passagem. A chamada `MappingResult(...)` da segunda passagem (3242–3293) nunca o preenche, embora a regra que mantém uma linha da segunda passagem em revisão (`_review_proposal_is_supported`) use `selected_catalog_distinct_coverage` (3099–3103 e 3196).
- **Como ver:** comparando as linhas com `nome_catalogo` e as linhas com `cobertura_distinta_catalogo` preenchida: datasul 4 contra 1, oracle 5 contra 4 e sap_terumo 7 contra 6. Toda linha preenchida é um aceite da primeira passagem (`metodo = nome`). As linhas de CFOP de datasul e oracle, decididas pelo catálogo, estão em branco. O relatório de sap_procosa é anterior à coluna.
- **Impacto:** nas linhas decididas pelo catálogo, o revisor não vê o número em que a pontuação de catálogo e a regra de revisão de fato se apoiam. A aba mostra só `cobertura_catalogo`, ponderada pelas linhas, que pode ser bem menor: no 'CFO' de oracle ela é 0,6673, contra uma cobertura de códigos distintos de 0,8824.
- **Correção:** passe `catalog_distinct_coverage=round(selected_catalog_distinct_coverage, 4)` sempre que `selected_catalog` estiver preenchido.

### 2.12 Várias linhas em revisão podem propor a mesma coluna do cliente

- **Onde:** `invoice_mapper.py` `map_columns`. Só os aceites saem do conjunto disponível (`available.remove` em 2619 e 3294–3295).
- **O que acontece:** uma proposta em revisão não consome a sua coluna do cliente. Uma coluna do cliente pode então ser proposta para muitas colunas do modelo, inclusive uma coluna que outra coluna do modelo, mais adiante, aceita.
- **Como ver:**
  - Colunas do cliente propostas por mais de uma linha em revisão: datasul 26 (até 6 vezes cada), oracle 7 (até 11), sap_procosa 14 (até 8) e sap_terumo 12 (até 7).
  - Linhas em revisão cuja coluna do cliente é aceita em outra linha: datasul 1, sap_terumo 2.
  - Exemplo: 'Vlr Isen ICM' de datasul (valor isento de ICMS) é proposta para 'Isentas IBS', 'Isentas ICMS', 'Isentas ICAP', 'Isentas ICSP', 'Isentas ICMS_FECP' e 'IVA' (ver também 3.9).
- **Impacto:** o revisor vê a mesma coluna oferecida várias vezes. Confirmar duas dessas linhas, ou uma cuja coluna já foi aceita, faz o apply parar com "A coluna do cliente … foi mapeada mais de uma vez após as correções do revisor" (`_effective_columns`, 445–451).
- **Correção:** na aba `Fila de Revisão`, marque as colunas do cliente compartilhadas ou já aceitas. Considere descartar as propostas em revisão cuja coluna uma linha posterior aceitou.

### 2.13 `confianca_final = 0,8999` é um teto, não uma medida

- **Onde:** `invoice_mapper.py` `map_columns`, linhas 3163–3174. Quando nem o cabeçalho nem os dados objetivos são decisivos, o valor é limitado ao teto `final_threshold - 0.0001`. O código reconhece isso em `summarize_adjudication_queue` (3399–3403).
- **Como ver:** linhas no teto: datasul 11, oracle 2, sap_procosa 14 e sap_terumo 33. Das 11 linhas com gabarito encontradas corretamente mas retidas, cinco estão em 0,8999: datasul m45, m50, m51, m52 e oracle m210.
- **Impacto:** na aba `Mapeamentos` e no `mapeval --show-losses`, que ordena por esse valor, "0,8999" parece "quase aceito". A aba `Fila de Revisão` já explica o teto em `motivo_retencao` e desempata pela margem; as outras vistas não.
- **Correção:** mantenha o teto na regra de aceite, mas grave também o valor sem teto numa coluna própria (por exemplo, um campo interno `unclamped_confidence`, com cabeçalho em português em `report_schema.COLUMNS`).

### 2.14 `margem_nome` pode ser negativa em linhas aceitas

- **Onde:** `invoice_mapper.py` `_name_score_context` (1963–1977), chamada com a coluna escolhida em 3077–3079.
- **O que acontece:** a margem é a pontuação do cabeçalho da coluna escolhida menos a da melhor alternativa. Quando a segunda passagem escolhe uma coluna que não é a melhor pelo cabeçalho, a margem fica negativa.
- **Como ver:** datasul 'CFOP' ← 'Cod. Fiscal' é aceita com `margem_nome` -0,0582 (decidida pelo catálogo). sap_procosa tem 3 linhas aceitas com margem negativa.
- **Impacto:** numa linha aceita, uma margem negativa parece um teste de margem reprovado.
- **Correção:** documente a coluna como "vantagem de cabeçalho da coluna escolhida sobre a melhor outra coluna; negativa quando os dados prevaleceram sobre o cabeçalho", ou renomeie-a.

### 2.15 O modo offline é outro pipeline

- **Onde:** em `invoice_mapper.py` `map_columns`:
  - Os perfis das colunas do cliente são pulados (2648–2657).
  - A evidência de relações aritméticas é pulada (2665–2676).
  - A evidência de catálogo ainda é calculada (2677–2684) e depois descartada, quando o ramo offline fecha a coluna com `method = name_only`, `status = review` (2734–2749).
  - A decisão local por catálogo (2759) nunca é alcançada.
- **O que acontece:** com `--offline`, só a primeira passagem aceita. Toda linha da segunda passagem vira `revisao` (`metodo = somente_nome`), sem `apoiado_por` nem `decidido_por`. As linhas `erro_dados` de 3.1 (2781–2796) ficam com os mesmos rótulos em branco.
- **Impacto:** os resultados offline subestimam o que uma execução online entrega e nunca devem ser citados como o número do produto. O cálculo do catálogo é desperdiçado.
- **Correção:** pule `_candidate_catalog_evidence` quando `data_resolver is None`. Alternativamente, deixe o modo offline usar `_unique_distinctive_catalog_decision`, que não precisa da API. Em qualquer caso, preencha `supported_by` nessas linhas.

---

## 3. Robustez e tratamento de dados

### 3.1 Falhas da API viram linhas em revisão e a execução sai com 0

- **Onde:** `invoice_mapper.py` `map_columns`, linhas 2781–2796 (`except Exception`). `_run` devolve 0 em 3814.
- **O que acontece:** o mapeador captura qualquer exceção da chamada ao resolvedor, coluna a coluna do modelo. Isso cobre uma cota ou um limite de taxa estourado no meio da execução, um erro de rede ou uma resposta que não passa na validação. A linha é gravada com `metodo = erro_dados`, `situacao = revisao` e a justificativa "A comparação de dados falhou: <tipo>: <mensagem>" (`REASON_DATA_ERROR`, 2461; a mensagem da exceção chega em inglês, ver 6.2). A execução termina e sai com 0. Nenhum teste exercita esse caminho: os testes que citam `data_error` só gravam ou traduzem uma linha montada à mão.
- **Impacto:** uma cota que acaba ou uma conexão que cai no meio da execução produz um relatório de aparência normal, com toda coluna em dúvida marcada como `revisao`, e nada avisa o operador de que a execução saiu degradada. A chamada de embeddings no começo de `map_columns` (2531) não está protegida, então uma chave inválida desde o início para a execução ali.
- **Correção:** capture só os erros de uma decisão, como a análise ou a validação da resposta. Deixe os erros de autenticação, cota e conexão subirem. Saia com código diferente de zero, ou pelo menos imprima uma contagem, sempre que uma linha `erro_dados` for gravada.

### 3.2 Valores do cliente vão para a OpenAI sem mascaramento

- **Onde:** em `invoice_mapper.py`:
  - `OpenAIEmbeddingHeaderScorer._embeddings` (1135–1147) envia todos os cabeçalhos do modelo e do cliente.
  - `OpenAIDataResolver.resolve` (1209–1326) roda uma vez para cada coluna do modelo em dúvida. Envia os valores de amostra da própria coluna, tirados do model.xlsx, e um perfil de cada coluna candidata do cliente.
  - O conjunto de candidatos é a união de até `candidate_count` (10) colunas ranqueadas por cabeçalho, por perfil, por aritmética e por catálogo (`_rank_name_and_profile_candidates`, 1910–1960), ou seja, até 40 colunas.
  - Cada perfil inclui até `sample_size` (8) valores crus (`summarize_values`, chave `"samples"` em 793), além de mínimo, máximo e quartis.
  - `store=False` está ligado (1254), mas nada é mascarado.
- **Impacto:** no modo online, CNPJs, CPFs, chaves de acesso de NF-e e valores saem da consultoria. O repositório é compartilhado internamente de propósito, mas cada cliente precisa autorizar uma execução online antes que ela aconteça. O modo offline não envia nada.
- **Correção:** mascare os valores com cara de identificador antes de montar o payload. O perfil já traz `valid_cpf_fraction`, `valid_cnpj_fraction` e `valid_nfe_key_fraction`, então o modelo perde pouco. Alternativamente, omita `samples` nas colunas que validam como identificador. Qualquer das duas muda o payload: aumente `PROMPT_VERSION`, regenere o arquivo dourado e meça de novo online (ver 6.1).

### 3.3 Regenerar um relatório apaga os pareceres do revisor

- **Onde:** `invoice_mapper.py` `write_results` (3464–3510). As colunas do revisor são sempre gravadas em branco (3480–3481), e o arquivo de saída é sobrescrito sem checagem (3486).
- **O que acontece:**
  - Apontar `--output` para um relatório existente, por exemplo `data/reports/mapping_sap_procosa_full.xlsx`, apaga as colunas do revisor. O nome padrão (ver 3.4) não colide com os arquivos `*_full.xlsx`, então isso exige um caminho explícito.
  - Não existe caminho de mesclagem. Se a exportação do cliente muda, o apply recusa o relatório antigo pelo hash, e a única saída é um relatório novo com as colunas do revisor em branco.
- **Como ver:** `data/reports/mapping_sap_procosa_full.xlsx` guarda os únicos 26 pareceres do revisor do repositório.
- **Impacto:** um único comando apaga trabalho de revisão.
- **Correção:**
  - Recuse sobrescrever um relatório existente sem `--force`.
  - Crie um passo de mesclagem que leve `parecer_revisor`, `coluna_cliente_correta` e `observacao_revisor` para o relatório novo pelo `numero_coluna_modelo`, sempre que a coluna do cliente proposta não tiver mudado. O passo precisa ler o relatório antigo nos dois esquemas, por `report_schema`, porque pareceres podem estar num relatório legado.

### 3.4 O relatório vai, por padrão, para a pasta atual

- **Onde:** `invoice_mapper.py` `_run`, linha 3703.
- **O que acontece:** sem `--output`, o relatório é gravado como `mapping_<nome do arquivo do cliente>.xlsx` na pasta de trabalho, e não em `data/reports/`, e o arquivo não está no `.gitignore`. Até isso mudar, passe `--output data/reports/…`.
- **Correção:** use `data/reports/mapping_<nome>.xlsx` como padrão, junto com a proteção contra sobrescrita de 3.3.

### 3.5 A saída em CSV perde três abas

- **Onde:** `invoice_mapper.py` `write_results`, linhas 3483–3484.
- **O que acontece:** uma saída `.csv` grava só a tabela `Mapeamentos` (já em português), então `Metadados da Execução`, `Colunas do Cliente sem Uso` e `Fila de Revisão` se perdem. O apply lê relatórios com `pd.read_excel`, então um relatório em CSV não pode ser aplicado.
- **Correção:** elimine a saída CSV, ou grave um CSV por aba.

### 3.6 A verificação de relatório desatualizado do apply pode ser pulada

- **Onde:** em `apply_invoice_mapping.py`:
  - `_run_metadata` (303–317) devolve `{}` quando o relatório não tem a aba `Metadados da Execução` (nem a legada `Run Metadata`).
  - `_validate_input_hashes` (320–335) pula qualquer hash não gravado.
  - `_read_mapping_report` (238–246) lê a primeira aba, e não a aba `Mapeamentos` pelo nome.
- **O que acontece:**
  - Um relatório sem metadados, ou sem `sha256_cliente`/`sha256_modelo`, é aplicado sem verificação de atualidade. A conferência dos nomes das colunas propostas (`_effective_columns`, 433–440) ainda pega a exportação de outro cliente, mas não uma nova exportação do mesmo ERP com outras linhas.
  - `versao_esquema_mapeamento`, `versao_prompt` e os valores gravados de `aba_cliente`, `linha_cabecalho_cliente` e `linha_cabecalho_modelo` nunca são comparados com os argumentos do próprio apply.
  - Uma aba inserida antes de `Mapeamentos`, por exemplo à mão no Excel, faz o apply falhar com uma mensagem enganosa: "Não foi possível identificar o esquema da aba Mapeamentos: o cabeçalho não tem 'numero_coluna_modelo' …", quando a aba lida nem é a `Mapeamentos`. O gravador avisa sobre isso num comentário em `invoice_mapper.py` 3492–3495.
- **Correção:** exija os metadados e os dois hashes, compare as configurações de aba e de linha de cabeçalho, e leia a aba de mapeamentos pelo nome (`report_schema.find_sheet(nomes, "mappings", None)`, como o mapeval já faz).

### 3.7 Problemas menores na leitura das entradas do apply

Todos ficam em `apply_invoice_mapping.py`.

| Problema | Onde | O que acontece | Correção |
|---|---|---|---|
| `situacao` comparada sem normalizar | `_effective_columns` 410, 414 e 418; normalizada só por caixa e espaços, para a `Cobertura`, em 667–669 | `report_schema.internal_value` converte só o valor exato (`aceito`, `revisao`). Uma `situacao` editada à mão como `Aceito ` (maiúscula e espaço no fim) ou `Revisão` (com acento) passa crua: a linha some dos dados e a `Cobertura` a põe em `não consta na exportação do cliente`. Numa cópia do relatório de oracle com essas duas edições, o apply entregou 17 colunas em vez de 18 e pôs 174 linhas em `não consta na exportação do cliente` em vez de 172, com saída 0 | Normalize `situacao` uma vez, ignorando caixa, espaços e acentos, como já se faz com o parecer (ver 6.4). O teste `test_enum_values_are_converted_only_when_exact_and_only_in_portuguese` fixa a regra atual |
| `--include-review` chama de `entregue` propostas não confirmadas | `_write_coverage_sheet` 508–509; colunas repetidas permitidas em 442–451 | Uma execução de oracle numa pasta de rascunho com `--include-review` rotulou 37 linhas em revisão como `entregue`, e 30 linhas entregues dividiam uma coluna do cliente com outra linha entregue | Crie um estado separado, como "entregue (não confirmada)" |
| Texto numérico em `coluna_cliente_correta` é lido como número de coluna | `_corrected_column` 203–211 | Um cabeçalho do cliente que seja literalmente um número, como '2024', não pode ser indicado pelo nome | Tente primeiro casar pelo nome, ou exija um prefixo como `#12` para números |

### 3.8 O DV de CPF/CNPJ falha em células numéricas

- **Onde:** `invoice_mapper.py` `_digits_only` (660–661), `_cpf_is_valid` (664–675) e `_cnpj_is_valid` (678–690). São usados por `_validation_fraction` (707–708), que alimenta `identifier_match_score`.
- **O que acontece:** os validadores do dígito verificador (DV) exigem exatamente 11 ou 14 dígitos e nunca devolvem os zeros à esquerda. Quando um ERP exporta o CNPJ como número, o zero à esquerda some. Uma célula float ainda ganha um `0` a mais, vindo do `.0`.
- **Como ver:**

  ```bash
  python -c "import invoice_mapper as im; v='01234567000195'; print(im._cnpj_is_valid(im._digits_only(v)), im._cnpj_is_valid(im._digits_only(int(v))))"
  # True False
  ```

  - Como float, o mesmo valor vira os dígitos `12345670001950`.
  - Uma coluna desses inteiros tem `valid_cnpj_fraction` 0,0 em vez de 1,0.
  - O prompt do resolvedor avisa a IA sobre zeros à esquerda perdidos (1290–1292), mas o código local não aplica essa regra.
- **Impacto:** a evidência de identificador, uma das mais transferíveis, some sem aviso nos ERPs que guardam cadastros como número.
- **Correção:** converta células int e float numa string de inteiro e tente `zfill(11)` e `zfill(14)`. Deixe as células de texto como estão. Uma chave de acesso de 44 dígitos guardada como float já perdeu precisão e não tem conserto.

### 3.9 'ICM', 'IS' e 'DIF_ALIQ' não são famílias de tributo

- **Onde:** `invoice_mapper.py` `_TAX_FAMILIES` (191–208) e `_TAX_FAMILY_ALIASES` (184–189). São lidos por `header_tax_families` (211–217), que alimenta `semantic_header_conflict` (443, pela análise de `_header_conflict_parts` em 416–417) e `tax_calculation_scope` (997–1008).
- **O que acontece:** três cabeçalhos não produzem família de tributo:
  - 'ICM', o truncamento do Datasul para ICMS
  - 'IS', o Imposto Seletivo da reforma
  - 'DIF_ALIQ', o diferencial de alíquota do ICMS

  Sem família, `family_conflict` não dispara, e a coluna nunca entra no escopo de um trio fiscal (base de cálculo × alíquota = valor do imposto).
- **Como ver:**

  ```bash
  python -c "from invoice_mapper import header_tax_families as f; print([sorted(f(h)) for h in ('Vlr Isen ICM', 'Valor IS', 'Base DIF_ALIQ', 'Isentas ICMS')])"
  # [[], [], [], ['icms']]
  ```

  - `semantic_header_conflict` devolve `None` entre 'Vlr Isen ICM' e cada uma das 17 colunas 'Isentas …' do modelo, e é por isso que datasul a propõe para cinco delas e para 'IVA' (2.12).
  - As 14 colunas do modelo nos blocos IS e DIF_ALIQ ficam sem família e sem escopo de trio.
- **Impacto:** a aritmética, a evidência transferível mais forte, e a principal trava contra a troca de tributo ficam indisponíveis para essas colunas.
- **Correção:**
  - Faça de `icm` um sinônimo de `icms`.
  - Leve o bloco DIF_ALIQ para `icms` com o qualificador `dif`, que `tax_calculation_scope` já conhece.
  - Acrescente `is` como família, protegida contra a palavra inglesa `is`.
  - Valide a mudança deixando um cliente de fora.

### 3.10 A evidência de identificador depende da mistura de CPF e CNPJ nas amostras do model.xlsx

- **Onde:** `invoice_mapper.py` `identifier_match_score` (880–894). Para cada tipo de identificador, exige pelo menos 80% de valores válidos dos dois lados: na coluna do cliente e nas linhas de amostra da própria coluna do modelo no model.xlsx.
- **O que acontece:** uma coluna do cliente só ganha evidência de identificador para o tipo que domina as linhas de amostra do modelo.
  - 'Nº ID fiscal 1' tem 209 valores de amostra: 91,4% são CPFs válidos e 8,6% CNPJs válidos. Só uma coluna do cliente feita principalmente de CPFs casa com ela.
  - As amostras de 'Chave de acesso' são 100% chaves de acesso válidas.
  - Nenhuma outra coluna do modelo chega à barra de 80%. A seguinte é 'Nome do Fornecedor do Local de Início', com 1,9% de CPFs válidos.
- **Como ver:** contra 'Nº ID fiscal 1', uma coluna de rascunho com 50 CNPJs válidos tem `identifier_match_score` 0,0, e uma com 50 CPFs válidos tem 0,914.
- **Impacto:** se a coluna de CNPJ/CPF de um cliente ganha essa evidência transferível depende da mistura de pessoas e empresas nas linhas de amostra do modelo, e não dos dados do cliente. `mapping_steps.py` 124–127 promete mais ("qualquer que seja o nome dela").
- **Correção:** o model.xlsx fica como está. Trate CPF e CNPJ como um tipo só, "cadastro de contribuinte", ou declare no código o tipo de identificador de cada coluna do modelo, pelo cabeçalho do modelo, e pontue só o lado do cliente.

### 3.11 As colunas 'Tipo de Imposto …' do modelo não recebem evidência de catálogo

- **Onde:** `reference_catalog.py` `expected_catalogs` (33–71), em especial a cláusula `"tipo imposto" in normalized` na linha 57.
- **O que acontece:** os catálogos a testar são escolhidos pelo cabeçalho do modelo. As 14 colunas do modelo chamadas 'Tipo de Imposto <tributo>' normalizam para 'tipo de imposto …', então a cláusula não casa com nenhum cabeçalho do modelo, e nenhuma dessas colunas ganha catálogo. O próprio mapeador trata essas colunas como de situação tributária (`header_semantic_role` devolve `tax_status`; ver `test_a_family_name_does_not_smuggle_a_rate_role` em `tests/test_invoice_mapper.py`).
- **Como ver:** `expected_catalogs('Tipo Imposto ICMS')` devolve os três catálogos do ICMS (`cst_icms`, `cst_icms_taxation` e `csosn`), enquanto `expected_catalogs('Tipo de Imposto ICMS')` devolve `()`. Só 15 colunas do modelo recebem algum catálogo.
- **Impacto:** ou a cláusula é código morto, ou 14 colunas perdem um sinal.
- **Correção:** decida qual dos dois.
  - Se essas colunas guardam códigos CST, case pelos tokens `{"tipo", "imposto"}`. Catálogos de CST não aceitam sozinhos (a pontuação fica abaixo de 0,90), então a mudança só acrescentaria evidência para o resolvedor.
  - Se não guardam, apague a cláusula.

### 3.12 A pontuação de catálogo ignora quantos valores estão bem formados

- **Onde:** `reference_catalog.py` `ReferenceCatalogs.analyze`. A pontuação é `distinct_coverage * specificity * sample_factor` (204), e `sample_factor` conta os valores não vazios, e não os bem formados (200).
- **Como ver:** uma coluna de texto livre com 1.000 valores que contém um único '5102' vira um CFOP perfeito:

  ```bash
  python - <<'EOF'
  import pandas as pd
  from reference_catalog import ReferenceCatalogs
  rc = ReferenceCatalogs.from_directory("reference_data")
  coluna = pd.Series([f"texto livre {i}" for i in range(999)] + ["5102"])
  e = rc.analyze(coluna, "cfop")
  print(e["score"], e["coverage"], e["format_fraction"])   # 1.0 0.001 0.001
  EOF
  ```

- **Impacto:** o aceite automático está protegido, porque `catalog_evidence_is_distinctive` exige `format_fraction >= 0.90` (`invoice_mapper.py` 1695). A pontuação crua ainda é usada sem checagem em três lugares:
  - `_catalog_score` (1570–1572), para trazer candidatos para o conjunto (1943–1953)
  - a checagem que preserva o melhor cabeçalho (3025–3034), onde uma pontuação lixo de 0,90 ou mais impede que o favorito pelo cabeçalho seja restaurado
  - o `catalog_evidence` enviado ao resolvedor
- **Correção:** multiplique a pontuação por `format_fraction`. Como `catalog_evidence` vai no payload do resolvedor, a mudança pode alterar a entrada do modelo: rode o teste dourado e, se ele falhar, trate como em 6.1.

### 3.13 Códigos cClassTrib vencidos continuam valendo

- **Onde:** `reference_catalog.py` `ReferenceCatalogs.from_directory` (140–161) guarda só a coluna `code` (159). `valid_from` e `valid_to` são ignorados em todo lugar.
- **Como ver:** `reference_data/ibs_cbs_classification.csv` guarda os códigos cClassTrib do IBS/CBS. Três deles, 220001 a 220003, têm `valid_to` 2026-01-01, e os três ainda carregam como válidos.
- **Correção:** descarte os códigos cujo `valid_to` é anterior à data da execução, ou mantenha-os e informe à parte os casamentos com códigos vencidos.

### 3.14 A atualização dos catálogos de referência é frágil

- **Onde:** `update_reference_data.py`. Não rode sem motivo: ele baixa dos sites do governo e reescreve `reference_data/` (e, com isso, o hash do manifesto que cada relatório registra).
- **O que acontece:**
  - **Nomes de aba** fixos numa publicação: 'CST 2026-06-01 Pub' (122) e 'cClass 2026-06-01 Pub' (136). Uma nova publicação derruba o script ou precisa ser editada à mão.
  - **Rótulos de versão:** os textos de `source_version` do CFOP, do IBGE e das duas tabelas do IBS/CBS são literais (178, 192, 199, 211). Só a versão do NCM é lida do download (185), então as outras tabelas rotulariam dados novos com a versão antiga.
  - **Sem filtro de tamanho no CST do IBS/CBS** (121–133), ao contrário do CFOP (102), da tabela de municípios (117–119) e do cClassTrib (148). Por isso a linha 2 de `reference_data/ibs_cbs_cst.csv` tem o código em branco. Hoje é inofensivo, porque o carregador descarta códigos em branco (`reference_catalog.py` 159).
  - **Manifesto:** o carregador nunca confere `schema_version` nem `generated_at` do `manifest.json`, e o manifesto não tem checksum próprio. O mapeador grava o sha256 do manifesto em `sha256_manifesto_referencia`, nos metadados (`invoice_mapper.py` 3777–3781).
- **Correção:** procure as abas por prefixo, leia as versões dos arquivos de origem, acrescente o filtro de 3 dígitos e valide o esquema do manifesto ao carregar.

### 3.15 Uma chave desconhecida no manifesto termina em traceback cru

- **Onde:** `reference_catalog.py` `from_directory`, linha 146 (`CatalogSpec(name=name, **raw_spec)`). `invoice_mapper.py` `_run` (3716–3724) captura só `OSError`, `ValueError`, `KeyError` e `json.JSONDecodeError`, e `main` (3692–3694) só `ValueError` e `OSError`.
- **Como ver:** com uma cópia de `reference_data/` cujo manifesto tem uma chave a mais, `valid_until`, o mapeador sai com 1 e um traceback terminado em `TypeError: CatalogSpec.__init__() got an unexpected keyword argument 'valid_until'`, em vez da mensagem amigável "Não foi possível carregar os catálogos de referência de … Execute update_reference_data.py ou use --no-reference-data.". O trecho abaixo trabalha numa pasta temporária e falha antes de mapear qualquer coluna:

  ```bash
  tmp=$(mktemp -d)
  cp -R reference_data "$tmp/ref"
  python - "$tmp/ref/manifest.json" <<'EOF'
  import json, sys
  caminho = sys.argv[1]
  manifesto = json.load(open(caminho, encoding="utf-8"))
  manifesto["catalogs"]["cfop"]["valid_until"] = "2027-01-01"
  json.dump(manifesto, open(caminho, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
  EOF
  python invoice_mapper.py data/clients/oracle.xlsx --offline --reference-data "$tmp/ref" --output "$tmp/x.xlsx"
  ```

- **Correção:** valide as chaves do manifesto e levante `ValueError`, ou acrescente `TypeError` ao `except`.

### 3.16 Nenhum teste cobre um checksum que não confere

- **Onde:** `reference_catalog.py`, linhas 149–153, levanta erro quando o checksum de um catálogo não confere com o manifesto; nada em `tests/` exercita esse caminho.
- **Como ver:** `grep -rn checksum tests/` não encontra nada.
- **Correção:** acrescente um teste que adultere um CSV numa cópia em `tmp_path` e espere o `ValueError`.

### 3.17 `backfill_mapping_steps.py` tem poucas verificações de segurança (resolvido em parte)

- **Onde:** `backfill_mapping_steps.py` `backfill` (129–268), `_header_step` (115–126) e `_refuse_unless_legacy` (85–112).
- **O que já foi resolvido** (commits `653da8b` e `d97ee1a`):
  - **Esquema em português.** Antes de ler a exportação ou de mexer em qualquer célula, `_refuse_unless_legacy` detecta o esquema pelo cabeçalho da primeira aba e recusa, com saída 1 e sem gravar nada, um relatório em português ("… está no esquema em português; o preenchimento retroativo só se aplica a relatórios legados em inglês, anteriores às colunas de etapas. Nada foi gravado."), um de esquema misto ou um que não dá para identificar. Sem isso, um relatório novo ganharia três colunas legadas vazias no próprio arquivo, com saída 0.
  - **Testes.** `tests/test_backfill_mapping_steps.py` tem 11 testes: a recusa byte a byte dos esquemas em português e misto, um relatório legado mínimo preenchido exatamente como pela versão de `2ae4b6d`, o `--dry-run`, a recusa de um relatório já preenchido, a prova não decisiva nas duas línguas e a ajuda em português.
  - **Arquivo inexistente.** O backfill imprime "Arquivo não encontrado: …" com saída 1, em vez de um traceback.
- **O que continua valendo:**
  - **Exportação errada passa despercebida.** Ele lê a exportação do cliente na aba 0, com o cabeçalho na linha 0 (144), seja o que for que os metadados do relatório digam, e nunca confere `client_sha256`. Passar a exportação errada produz em silêncio rótulos de aritmética e de veto errados.
  - **Sem cópia de segurança.** Ele grava por cima do relatório (265–266).
  - **Rótulo offline errado.** `_header_step` trata `embedding_model == "disabled"` como léxico (125), mas o mapeador grava `"offline"` (`invoice_mapper.py` 3770), então um relatório offline preenchido diria `semantic`. O comentário em 117–120 registra isso e mantém o comportamento de propósito. Os quatro relatórios arquivados vêm de execuções online, e, como o backfill só aceita relatórios legados, nenhum relatório que o mapeador grave hoje passa por aqui.
  - **Roda uma vez só.** Ele recusa um relatório que já tem as colunas de etapas (141–142), então um backfill corrigido precisa partir do relatório anterior ao backfill no histórico do git (ver 1.1). E, como recusa o esquema em português, precisa rodar antes da migração.
  - **Os testes não alcançam o defeito 1.1.** O relatório de teste usa o layout de sap_procosa, sem `arithmetic_proof`: as colunas entram antes de `reviewer_status` e todo campo lido fica à esquerda da âncora, como o próprio teste avisa. Um relatório no layout de datasul, com `arithmetic_proof` e `llm_final_confidence > 0`, mostraria o resolvedor sem crédito.
- **Correção:**
  - Leia a aba e a linha de cabeçalho dos metadados e confira o hash da exportação.
  - Grave num arquivo novo em vez de sobrescrever.
  - Compare com `"offline"`.
  - Acrescente um caso no layout de datasul que confira cada campo lido, junto com a correção de 1.1.

### 3.18 A expansão de prefixos de palavras de papel casa palavras comuns

- **Onde:** `invoice_mapper.py` `expand_truncated_role_words` (260–277), que usa `_ROLE_PREFIXES`: 21 prefixos de 3 letras ou mais, montados em 239–252.
- **O que acontece:** qualquer token de cabeçalho igual a um prefixo de uma palavra de papel é expandido: 'out' vira outras, 'red' vira reduzida, 'exe' vira isentas. A docstring da função reconhece o risco.
- **Como ver:** `python -c "from invoice_mapper import header_semantic_role as r; print(r('Valor Out'))"` imprime `other` (o papel de outras).
- **Impacto:** baixo. Um cabeçalho em inglês que use "Out" como palavra ganha o papel de outras.
- **Correção:** expanda prefixos só em cabeçalhos que também nomeiam uma família de tributo.

### 3.19 A CLI e os testes do mapeval são frágeis

- **Nomes de cliente desconhecidos.** `--client` não é validado, então um nome desconhecido termina num `KeyError` cru, com traceback em inglês (`mapeval/cli.py` 80). Ver também 6.5.
- **Pulo silencioso.** Para os clientes sem gabarito, um relatório que não existe é pulado sem nenhuma palavra (81–84). Por exemplo, `python -m mapeval --root "$(mktemp -d)" --client sap_terumo` não imprime nada e sai com 0. Uma exportação do cliente que não existe, ao contrário, gera erro.
- **Pasta de trabalho.** `--root` tem `.` como padrão (61), então o mapeval precisa rodar a partir da raiz do repositório.
- **Marcador de pulo.** Os testes com planilhas só pulam quando `data/keys/de_para_oracle.xlsx` não existe (`tests/test_mapeval.py` 42–45). Se qualquer outra planilha não estiver lá, eles falham em vez de pular.
- **Totais fixados.** Vários testes fixam totais absolutos em `tests/test_mapeval.py`: os denominadores 30/26 em 209, `43` em 222, `(39, 43)` em 241 e `(43, 32)` em 291. Regenerar qualquer relatório com gabarito os quebra por construção, seja a mudança uma melhoria ou não. Atualize-os de propósito, junto com o relatório.
- **Dica repetida.** A linha final "rode com --show-losses para listá-los" sai mesmo quando `--show-losses` já foi passado (161–164).
- **Correção:**
  - Use `choices=list(CLIENTS)` em `--client`; o `PortugueseArgumentParser` já traduz o erro de escolha inválida.
  - Imprima uma linha quando um relatório for pulado.
  - Resolva `--root` em relação ao pacote.
  - Confira todas as planilhas no marcador de pulo.
  - Omita a dica quando `--show-losses` foi passado.

### 3.20 Detalhes da instalação: `.env` e nenhum lockfile

- **O `.env` nunca é carregado.** Uma cópia de trabalho pode ter um `.env` na raiz. Ele está no `.gitignore` e nenhum código o lê (nada importa `dotenv`), então as execuções online precisam de `OPENAI_API_KEY` exportada no shell. Ao compartilhar a pasta como arquivo compactado, e não como clone do git, deixe o `.env` de fora (e lembre que, sem a pasta `.git`, os testes `lento` não provam nada; ver 6.8).
- **Nenhum lockfile.** `requirements.txt` só dá faixas de versão. A suíte de testes foi verificada com Python 3.13.
- **Correção:** apague o `.env` ou carregue-o explicitamente, e fixe as versões num arquivo de restrições ou de lock.

---

## 4. Documentação e comentários desatualizados no código

### 4.1 Números de linha de base desatualizados em comentários

| Onde | Diz | Atual |
|---|---|---|
| `mapeval/truth.py:22` | Aplicar as correções do gabarito "é a diferença entre 33/56 e 37/56" | 39/56 encontrados com `--strict-key-only`, 43/56 com as correções |
| `tests/test_mapeval.py:278` | "O recall da escolha é 42/56, mas o cliente recebe 31" | O teste confere `(43, 32)` na linha 291 |
| `invoice_mapper.py:3317` (docstring de `AdjudicationRow`) | "o mapeador encontra 42 de 56 e entrega 31" | Entrega 32 e encontra 43. "as 11 de diferença" continua certo |

Os três sobreviveram à tradução: os comentários foram vertidos para o português com os números antigos.

**Correção:** atualize os números, ou aponte para `python -m mapeval` em vez de citar números em comentários.

### 4.2 `mapping_steps.py` cita funções que não existem

- Linha 82: o `implemented_by="_openai_data_decision"` do resolvedor. A função real é `OpenAIDataResolver.resolve` (`invoice_mapper.py` 1209).
- Linha 204: o `implemented_by="build_mapping (o conjunto available)"` da exclusividade. A tradução só mudou a prosa ("(the available set)" virou "(o conjunto available)"); a função real continua sendo `map_columns` (`invoice_mapper.py` 2509), pelo conjunto `available`.

Os outros 20 nomes citados em `implemented_by` existem.

**Correção:** corrija as duas strings. Um teste que resolva cada nome de `implemented_by` contra os módulos impediria que o problema voltasse; hoje `tests/test_mapping_steps.py` só confere que o campo não está vazio.

### 4.3 Comentários descrevem comportamento que o código não tem

- `mapping_steps.py` 74–80 diz que "decidido pelo resolvedor" mantém "o seu significado honesto", mas esse valor nunca é produzido (2.2).
- `mapping_steps.py` 124–127, no resumo da etapa de identificador, diz que uma coluna que passa na validação "é aquele identificador, qualquer que seja o nome dela", mas a etapa também depende das amostras do modelo (3.10).
- `mapping_steps.py` `GUARDS` (161–206) dá a entender que as quatro travas são informadas, mas duas nunca são gravadas (2.1).
- `mapeval/score.py` 92–97, a docstring de `delivered_correct`, diz que o apply entrega as linhas aceitas e as confirmadas pelo revisor, mas o código conta só as aceitas (2.6).
- `backfill_mapping_steps.py` 188–195 ("Segue o caminho de uma execução nova…") e 198–205 ("precisa passar na mesma barra de distinção que uma execução nova aplica") afirmam uma paridade com a execução nova que não existe (2.3).

Dois comentários que esta seção citava foram acertados na tradução e agora apontam para os itens: a docstring de `DISTINCTIVE_CATALOG_SCORE` (66–71) diz que as duas regras não coincidem (2.3), e o comentário sobre o layout (149–151) diz que o layout resultante é outro (5.1). Esses comentários, os de 117–120 e 160–164 do backfill e o de `tests/test_backfill_mapping_steps.py` citam este arquivo pelo número do item, então mantenha a numeração estável.

**Correção:** corrija cada comentário junto com o item ligado a ele, ou agora, se a correção do item ficar para depois.

---

## 5. Peculiaridades dos dados

### 5.1 Os layouts dos relatórios diferem, e a versão do esquema não diz qual é qual

- **O relatório de sap_procosa é o mais antigo.** `data/reports/mapping_sap_procosa_full.xlsx` foi gerado em 2026-08-04; os outros três, em 2026-08-25. Ele tem só as abas `Mapeamentos` e `Metadados da Execução`: nenhuma `Colunas do Cliente sem Uso`, nenhuma `Fila de Revisão`, nenhuma `prova_aritmetica` e nenhuma `cobertura_distinta_catalogo` (23 colunas, contra 25 dos outros). A migração manteve esse layout de propósito.
- **A versão do esquema não separa os layouts.** Antes da migração, os quatro diziam `mapping_schema_version = 2`, porque `MAPPING_SCHEMA_VERSION` não foi aumentado quando aquelas colunas e abas entraram. Agora os quatro dizem `versao_esquema_mapeamento = 3` e `migrado_da_versao_esquema = 2`. O aumento para 3 (`invoice_mapper.py` 48) marca o esquema em português, não o layout: um relatório novo e o de sap_procosa dizem a mesma versão com layouts diferentes.
- **As colunas de etapas ficam em três lugares diferentes:**
  - Um relatório novo as põe depois de `prova_aritmetica`, na ordem `apoiado_por`, `decidido_por`, `vetado_por` (`MappingResult`, 1402–1406).
  - Os relatórios de datasul, oracle e sap_terumo, preenchidos pelo backfill, põem `decidido_por`, `apoiado_por`, `vetado_por` antes de `prova_aritmetica`, como colunas 17–19.
  - O de sap_procosa as põe depois de `confianca_final_ia` e antes de `parecer_revisor`, como colunas 18–20.
- **Uma linha de banner em branco.** O banner de `data/mapped/sap_procosa_mapped.xlsx` tem a linha `prova_aritmetica` inteiramente em branco, porque o apply acrescenta como vazias as colunas opcionais que o relatório não tem (`_ordered_mapping`, 283–299).
- **Impacto:** leia as colunas dos relatórios pelo nome, nunca pela posição, e não deduza o layout pela versão do esquema. Não regenere sap_procosa só para atualizar o layout (3.3).
- **Correção:** aumente `MAPPING_SCHEMA_VERSION` sempre que colunas ou abas mudarem, e não só quando os nomes mudam.

### 5.2 Contagem de linhas dos gabaritos

- Nos arquivos de-para, a linha 1 tem as anotações "Coluna X", a linha 2 os cabeçalhos do modelo, e os dados começam na linha 3.
- `data/keys/de_para_oracle.xlsx` tem 7.338 linhas de dados. `data/clients/oracle.xlsx` carrega como 7.340 linhas de dados, mas a linha 7340 do Excel está inteiramente em branco, então ele tem 7.339 linhas preenchidas.
- `de_para_datasul.xlsx` tem 6.804 linhas de dados preenchidas, como `datasul_maio.xlsx`, mas a aba declara 7.340 linhas, então o `max_row` do openpyxl exagera. `de_para_procosa.xlsx` declara 209 linhas, das quais 101 estão preenchidas (as duas linhas de cabeçalho e 99 de dados).
- **Impacto:** nenhum na pontuação hoje, porque o mapeval só lê a linha 1. Importa se alguém construir o "canal de dados do de-para, alinhado linha a linha" que `mapeval/truth.py` 18–20 menciona: as linhas de oracle não podem ser alinhadas pela posição.
- **Correção:** alinhe por uma chave do documento, como número da nota mais item, e não pela posição, e conte as linhas preenchidas em vez de confiar no `max_row`.

### 5.3 `de_para_procosa.xlsx` não é um gabarito de colunas e está deslocado em uma coluna

- É uma especificação no nível de registro (a linha 1 marca cada coluna como 'ITEM' ou 'NOTA'), escrita sobre outra exportação, então o mapeval trata sap_procosa como sem gabarito.
- Tem 226 colunas. Como 'Tipo de Imposto CBS' não está nela, a coluna N do arquivo corresponde à coluna N+1 do modelo.
- Não a carregue com `load_answer_key`.

### 5.4 O model.xlsx tem cabeçalhos repetidos

- 'Descrição' é o cabeçalho das colunas 35 e 195.
- `pandas.read_excel` renomeia a segunda para 'Descrição.1', enquanto `SheetTable` mantém os dois `name` iguais e dá à segunda o nome de exibição 'Descrição (occurrence 2)' em memória, que os arquivos mostram como 'Descrição (ocorrência 2)'. Enderece as colunas do modelo pelo número.
- `SheetTable` numera as ocorrências pelo cabeçalho normalizado (`normalize_header`, sem caixa nem acento), então 'Domicílio fiscal' (coluna 76) e 'Domicílio Fiscal' (coluna 154) também contam como repetição: em memória, a 154 tem o nome de exibição 'Domicílio Fiscal (occurrence 2)', embora os dois cabeçalhos sejam diferentes e o pandas os mantenha distintos.
- A linha retida de oracle em m195 se refere à segunda 'Descrição'.

### 5.5 Os cabeçalhos da exportação datasul têm espaços sobrando

- 206 dos 348 cabeçalhos de `data/clients/datasul_maio.xlsx` têm espaços no fim, por exemplo 'Filial      '. `_header_text` (`invoice_mapper.py` 503–508) os remove na leitura.
- Uma anotação na linha 1 de `de_para_datasul.xlsx` tem um espaço sobrando, que `parse_coluna_annotation` (`mapeval/excel.py` 43–55) tolera.
- Qualquer código novo que leia esses arquivos precisa limpar cabeçalhos e anotações do mesmo jeito.

---

## 6. Introduzidos ou expostos pela tradução

A tradução (commits `2909fa5` a `d97ee1a`) não mudou nenhuma decisão: as execuções offline batem célula a célula com a linha de base em inglês de `2ae4b6d`, o mapeval imprime os mesmos números e o teste dourado da entrada dos modelos continua passando. Os itens abaixo são o que ela deixou em inglês de propósito, o que ela criou e o que ela tornou visível.

### 6.1 As justificativas escritas pela IA continuam em inglês

- **Onde:** `invoice_mapper.py` `OpenAIDataResolver.resolve`. O prompt (`instructions`, 1256–1302) termina em "Keep the reason to one sentence.", e a descrição do campo `DataMappingDecision.reason` (1179) é "Short explanation grounded in names and sample values.". A justificativa do modelo entra na linha em `map_columns` (`reason = decision.reason`, 3186) e passa intacta por `report_schema` e por `legacy_reports`.
- **O que acontece:** o prompt continua em inglês de propósito, porque é entrada do modelo e precisa ficar idêntico byte a byte (`tests/test_model_input_golden.py`). Por isso a IA continua respondendo em inglês, e a `justificativa` das linhas que ela escreveu é a única frase do mapeador em inglês num relatório em português. (As 26 observações do revisor de sap_procosa também estão em inglês, mas são texto de pessoas e ficam como foram escritas.)
- **Como ver:** os relatórios arquivados têm 502 justificativas da IA em inglês (datasul 119, oracle 160, sap_procosa 113, sap_terumo 110), como 'No viable candidates match the semantic role of the tax situation code.'. O apply as copia como estão para o banner de `Dados Mapeados` e para a `Cobertura` das planilhas mapeadas, e toda execução online futura grava mais. O trecho abaixo conta as justificativas que a migração deixou idênticas às do relatório legado de `2ae4b6d`:

  ```bash
  python - <<'EOF'
  import io
  import subprocess

  import pandas as pd

  total = 0
  for c in ["datasul", "oracle", "sap_procosa", "sap_terumo"]:
      caminho = f"data/reports/mapping_{c}_full.xlsx"
      legado = subprocess.run(["git", "show", f"2ae4b6d:{caminho}"], capture_output=True, check=True).stdout
      antes = pd.read_excel(io.BytesIO(legado), sheet_name="Mappings")
      depois = pd.read_excel(caminho, sheet_name="Mapeamentos")
      intactas = int((antes["reason"] == depois["justificativa"]).sum())
      total += intactas
      print(c, intactas)
  print("total", total)   # 119, 160, 113, 110; total 502
  EOF
  ```

- **Impacto:** o revisor lê as duas línguas na mesma coluna, e a planilha mapeada que vai para o cliente também. Nada no código depende da língua da justificativa.
- **Correção possível:** acrescentar ao prompt em inglês uma instrução para escrever a justificativa em português do Brasil (e, se for o caso, ajustar a descrição de `DataMappingDecision.reason`). Isso muda a entrada do modelo, então exige, na ordem:
  1. aumentar `PROMPT_VERSION` (hoje `"2026-08-04.2"`, `invoice_mapper.py` 49), que vai para `versao_prompt` nos metadados;
  2. regenerar o arquivo dourado com `python tests/test_model_input_golden.py --write`, de propósito e só por causa dessa mudança;
  3. medir de novo online os quatro clientes e comparar com o mapeval, porque uma instrução a mais pode mexer em confianças e escolhas. Cada execução online manda dados do cliente para a OpenAI (3.2) e precisa de autorização, e regenerar sap_procosa apaga os pareceres (3.3).

  As 502 justificativas arquivadas continuam em inglês de qualquer jeito: `legacy_reports` só traduz frases escritas pelo próprio mapeador, reconhecidas pelo modelo de frase.

### 6.2 Texto de exceção do Python, em inglês, dentro de mensagens em português

- **Onde:**
  - `invoice_mapper.py` `REASON_DATA_ERROR` (2461), "A comparação de dados falhou: {exc_type}: {exc}", gravado na justificativa das linhas `erro_dados` (3.1).
  - `invoice_mapper.py` `_error_message` (3661–3670), usado pelo mapeador, pelo apply (783–785) e pelo backfill (291–293). Ele acrescenta `exc.strerror` aos erros de sistema e o texto cru do `ValueError` a "Não foi possível gerar o relatório de mapeamento".
  - `apply_invoice_mapping.py` `main` (786–788) imprime `str(exc)` de qualquer `ValueError`, inclusive os do pandas.
  - A mensagem de catálogos em `_run` (3719–3724) embute `{exc}`.
  - `migrate_reports.py` tem a sua própria `_error_message` (1167–1178), quase igual, com um ramo a mais para `IsADirectoryError`.
- **O que acontece:** as mensagens montadas pelo projeto estão em português, mas o pedaço que vem da biblioteca ou do sistema operacional chega em inglês, e às vezes é a mensagem inteira.
- **Como ver** (nenhum destes casos grava arquivo):
  - `python apply_invoice_mapping.py data/clients/oracle.xlsx data/reports/mapping_oracle_full.xlsx --client-sheet Inexistente --output /tmp/x.xlsx` imprime só `Worksheet named 'Inexistente' not found`.
  - O mapeador com `--client-sheet Inexistente` imprime "Não foi possível gerar o relatório de mapeamento: Worksheet named 'Inexistente' not found".
  - Uma pasta no lugar de um arquivo dá "Não foi possível ler ou gravar o arquivo …: Is a directory" no apply (como EXPORTACAO ou RELATORIO), mas "Esperava um arquivo, mas … é uma pasta." no `migrate_reports.py` (como RELATORIO): as duas funções já divergem.
  - Um `manifest.json` sem a chave `catalogs` dá "Não foi possível carregar os catálogos de referência de …: 'catalogs'. …", em que `'catalogs'` é todo o texto do `KeyError`.
  - Uma linha `erro_dados` gravaria, por exemplo, a mensagem de um `RateLimitError` da biblioteca `openai`, em inglês. A única exceção que o próprio resolvedor levanta (`RuntimeError`, 1308) já está em português.
- **Impacto:** baixo para as decisões, mas o desenvolvedor recebe mensagens meio em inglês justamente nos erros, e a justificativa `erro_dados` mistura as duas línguas numa célula que o revisor lê.
- **Correção:** traduza na fronteira as exceções mais comuns (aba inexistente em `SheetTable.from_excel`, `JSONDecodeError` e `KeyError` do manifesto, os erros da `openai` em `REASON_DATA_ERROR`), guardando o texto original como detalhe técnico no fim da mensagem. Junte as duas `_error_message` num lugar só (por exemplo, `cli_ptbr.py`), para que mapeador, apply, backfill e migração digam a mesma coisa.

### 6.3 Nomes de exibição ambíguos: cabeçalho literal 'Coluna sem nome N' ou '… (ocorrência N)'

- **Onde:** `report_schema.py` `display_name_to_internal` (601–621), aplicado na leitura a `coluna_cliente`, `coluna_cliente_proposta` e `coluna_cliente_correta` (`FIELD_KIND`, 746–772), nos dois esquemas.
- **O que acontece:** em memória, o nome de exibição continua em inglês (`Unnamed column N`, `Nome (occurrence N)`), porque `ColumnRef.name` é entrada da IA. Na gravação ele vira `Coluna sem nome N` e `Nome (ocorrência N)`, e na leitura volta. A volta não distingue um cabeçalho do cliente que já seja literalmente `Coluna sem nome 3` ou `Valor ICMS (ocorrência 2)`: lê os dois como a forma interna. A docstring reconhece só o primeiro caso.
- **Como ver:**

  ```bash
  python -c "import report_schema as rs; print(rs.display_name_to_internal('Coluna sem nome 3'), '|', rs.display_name_to_internal('Valor ICMS (ocorrência 2)'))"
  # Unnamed column 3 | Valor ICMS (occurrence 2)
  ```

  Numa exportação de rascunho com esses dois cabeçalhos e um relatório que propõe as duas colunas, o apply recusa o relatório com uma mensagem que se contradiz: "O relatório de mapeamento não confere com a planilha do cliente na coluna 3: 'Coluna sem nome 3' != 'Coluna sem nome 3'". Um revisor que digite qualquer um desses cabeçalhos em `coluna_cliente_correta` recebe "não foi encontrada"; só o número da coluna funciona. No sentido inverso, um cabeçalho literal `Unnamed column 7` aparece nos arquivos como `Coluna sem nome 7`. Nenhuma das quatro exportações de amostra tem cabeçalho assim.
- **Impacto:** baixo hoje, mas um ERP brasileiro pode exportar cabeçalhos que terminam com "(ocorrência N)" ou que já vêm como "Coluna sem nome N" de outra ferramenta, e o relatório fica inaplicável sem que a mensagem explique por quê.
- **Correção:** no apply, compare os dois lados pela mesma ida e volta: passe o `display_name` da exportação por `display_name_to_external` e depois por `display_name_to_internal` antes de comparar com a célula lida, e faça o mesmo ao procurar a coluna de `coluna_cliente_correta`. A comparação já é feita por número de coluna, então a ida e volta não confunde duas colunas diferentes.

### 6.4 O parecer do revisor é aceito nas duas línguas, mas `situacao` só no valor exato

- **Onde:** `report_schema.py` `normalize_reviewer_status` (710–723) e `_REVIEWER_STATUS_INPUTS` (687–695); `internal_value` (828–859).
- **O que acontece:** o parecer é digitado por pessoas, então a leitura é tolerante de propósito: ignora espaços nas pontas, caixa e acentos, e aceita `correct`/`correto`, `incorrect`/`incorreto` e `no_mapping_exists`/`inexistente` **nos dois esquemas**. Um relatório legado com `correto` e um em português com `correct` são aplicados normalmente, e a mesma coluna pode misturar as duas línguas. No banner da planilha mapeada sai sempre a forma canônica em português (`report_schema.external_value`), mas o relatório guarda o que foi digitado.

  Os valores de máquina seguem outra regra. Só o valor exato em português volta ao interno (3.7), e o valor interno em inglês (`accepted`) passa sem aviso num relatório em português, porque um valor desconhecido volta como está. A migração, por sua vez, recusa como esquema misto um valor de máquina em português num relatório legado.
- **Como ver:**

  ```bash
  python -c "import report_schema as rs; print([rs.internal_value('mappings', 'reviewer_status', v, rs.LEGACY_EN) for v in ('correto', 'Inexistente')], [rs.internal_value('mappings', 'status', v, rs.PT_BR) for v in ('aceito', 'Revisão', 'accepted')])"
  # ['correct', 'no_mapping_exists'] ['accepted', 'Revisão', 'accepted']
  ```

- **Impacto:** nada se perde hoje: os 26 pareceres de sap_procosa estão na grafia canônica. Mas há três regras de leitura (parecer tolerante, situação exata, valor interno aceito em silêncio), e quem edita um relatório à mão não tem como saber qual vale para cada coluna. Um parecer fora da lista, como `Correta`, é recusado pelo apply com a lista dos valores aceitos, o que está certo.
- **Correção:** escolha uma regra só para os valores que pessoas editam: a tolerância do parecer também para `situacao` (3.7), e recusa explícita, em vez de aceitação silenciosa, para valores de máquina do outro esquema. Os testes `test_reviewer_status_is_normalised_in_both_languages` e `test_enum_values_are_converted_only_when_exact_and_only_in_portuguese` (`tests/test_report_schema.py`) fixam o comportamento atual.

### 6.5 `python -m mapeval --client <desconhecido>` ainda termina em traceback de `KeyError`

- **Onde:** `mapeval/cli.py` `main`, linha 80 (`CLIENTS[name]`).
- **O que acontece:** a ajuda, os erros do argparse e toda a saída do mapeval estão em português (`PortugueseArgumentParser`), mas um nome que não está em `CLIENTS` passa pelo parser e termina num traceback do Python com `KeyError: 'foo'`, em inglês. É o mesmo problema de 3.19; a tradução só o deixou mais visível.
- **Como ver:** `python -m mapeval --client foo` (traceback, saída 1).
- **Correção:** `choices=list(CLIENTS)` em `--client`. O `PortugueseArgumentParser` já traduz o erro de escolha inválida ("argumento --client: valor não permitido: 'foo' (valores aceitos: …)", saída 2).

### 6.6 `legacy_reports.py` depende de auxiliares privados do mapeador e espelha a rotulagem do conflito

- **Onde:** `legacy_reports.py` usa `im._catalog_label` (436 e 490), e `translate_header_conflict` (133–168) repete a preparação dos lados do conflito que `invoice_mapper.describe_header_conflict_pt` (475–500) faz: siglas de tributo em maiúsculas, qualificadores e papéis pelos rótulos de `report_schema.TEXT_LABELS`. Além disso, `migrate_reports.py` usa `im._sheet_argument` e `im._trio_proof_text`, o backfill importa `_trio_proof_text` e `_error_message`, e o apply, `_error_message`.
- **O que acontece:** a tradução do legado depende de nomes privados de `invoice_mapper.py` e de uma segunda cópia de uma regra de formatação. Um renome ou uma mudança de rótulo no mapeador quebra a migração, ou a faz gravar uma frase diferente da que o mapeador grava.
- **Como ver:** os testes de antidesvio fixam a igualdade: `test_legacy_sentence_translates_to_what_the_current_producer_writes` (`tests/test_migrate_reports.py` 1032) e `test_the_header_conflict_twin_covers_the_three_kinds` (1053). Eles fazem parte da execução padrão, então uma divergência aparece no `python -m pytest`.
- **Impacto:** baixo enquanto os testes rodam; a dívida é de acoplamento.
- **Correção:** torne públicos os auxiliares que outros módulos usam (ou leia `report_schema.CATALOG_LABELS` direto, que é o que `_catalog_label` faz), e faça `translate_header_conflict` chamar uma função do mapeador que recebe o tipo e os lados já analisados, em vez de reimplementar a formatação.

### 6.7 Uma migração interrompida deixa um arquivo temporário oculto

- **Onde:** `migrate_reports.py` `migrate_report` (1070–1084). O resultado é gravado num temporário criado com `tempfile.mkstemp(prefix=f".{report.stem}.", suffix=".migrando.xlsx", dir=report.parent)`, verificado e só então posto no lugar do original com `os.replace`; o `finally` apaga o temporário.
- **O que acontece:** se o processo morre de um jeito que impede o `finally` de rodar (`kill -9`, queda de energia, fim abrupto da sessão), sobra ao lado do relatório um arquivo oculto como `.mapping_datasul_full.jjq9gd7j.migrando.xlsx`, com permissão 600. O original fica intacto. O padrão não está no `.gitignore`, então o arquivo aparece como não rastreado no `git status` de `data/reports/`.
- **Como ver:** numa cópia em pasta de rascunho, um `kill -9` durante a gravação deixou exatamente esse arquivo e o original sem alteração. `git check-ignore data/reports/.mapping_oracle_full.x.migrando.xlsx` não imprime nada (saída 1).
- **Impacto:** baixo: lixo num diretório versionado, que alguém pode acabar commitando.
- **Correção:** acrescente `.*.migrando.xlsx` ao `.gitignore` e faça a migração avisar sobre temporários antigos do mesmo relatório (ou apagá-los) ao começar.

### 6.8 Os testes `lento` precisam do commit `2ae4b6d` no git

- **Onde:** `pyproject.toml` (`addopts = "-m 'not lento'"`) e `tests/test_migrate_reports.py`: `LEGACY_COMMIT = "2ae4b6d"` (54), `_legacy_code` (1103–1112), `offline_runs` (1116–1146) e `_legacy_archived_report` (1235–1257).
- **O que acontece:** as 10 provas de ponta a ponta tiram o código e os relatórios legados do histórico. Oito rodam o mapeador de `2ae4b6d`, extraído com `git archive`, e o comparam com o atual; duas migram os relatórios arquivados obtidos com `git show 2ae4b6d:…`. Num clone raso (`git clone --depth 1`) ou numa cópia sem a pasta `.git`, o commit não existe e os testes são pulados ("código de 2ae4b6d indisponível no git", "relatório legado de … indisponível"), e `python -m pytest -m lento` termina com saída 0. As oito do mapeador ainda rodam as quatro execuções offline do código atual antes de pular. `MIGRACAO_BASELINE_EN` substitui o git para essas oito, se apontar para os quatro `mapping_<cliente>_offline.xlsx` gerados por `2ae4b6d`; as duas dos relatórios arquivados não têm alternativa.
- **Como ver:** num clone raso de rascunho, `python -m pytest -m lento -k archived -rs` pulou os dois testes dos relatórios arquivados, e `python -m pytest` passou nos 451 testes padrão, que não dependem do histórico. `git rev-parse --is-shallow-repository` diz se o clone é raso.
- **Impacto:** quem recebe o repositório por um clone raso ou um arquivo compactado vê a suíte lenta "passar" sem provar nada. O conserto de 1.1 também precisa do histórico (`88c503b^`).
- **Correção:** entregue o repositório com o histórico completo, ou faça os testes `lento` falharem (em vez de pular) quando o commit não existe e `MIGRACAO_BASELINE_EN` não está definida, ou guarde a linha de base em inglês fora do git, num lugar documentado.

### 6.9 As planilhas mapeadas mudaram de nome de aba e de rótulos

- **Onde:** `apply_invoice_mapping.py` `build_mapped_workbook` e `_write_coverage_sheet`, com os nomes de `report_schema.py` (`SHEETS`, 76–83; `COLUMNS["banner"]`, `COLUMNS["coverage"]` e `COLUMNS["mapped_data"]`).
- **O que acontece:** o entregável agora sai sempre em português. A aba `Mapped Data` virou `Dados Mapeados`, `Coverage` virou `Cobertura`, a célula A1 `__mapping_info__` virou `__info_mapeamento__`, os 21 rótulos do banner (linhas 2 a 22 da coluna A) usam os cabeçalhos em português (`situacao`, `confianca_final`, …), e os valores também mudaram (`aceito`, `semantico`, `entregue`, `aguardando decisão`, `não consta na exportação do cliente`). Os cabeçalhos do modelo na linha 1, os dados a partir da linha 23 do Excel, as colunas ocultas e as células de dados não mudaram. É uma quebra para qualquer leitor a jusante que procure os nomes antigos. Os relatórios de mapeamento também mudaram (`Mappings` virou `Mapeamentos` etc.), mas ali o apply e o mapeval leem os dois esquemas. Nada no repositório lê as planilhas mapeadas, e elas não registram versão de esquema: o único jeito de um leitor saber o formato é pelos nomes.
- **Como ver:**

  ```bash
  python - <<'EOF'
  import openpyxl
  for c in ["datasul", "oracle", "sap_procosa", "sap_terumo"]:
      wb = openpyxl.load_workbook(f"data/mapped/{c}_mapped.xlsx", read_only=True)
      print(c, wb.sheetnames, wb["Dados Mapeados"]["A1"].value)
      wb.close()
  EOF
  ```

- **Impacto:** um sistema que lia `Mapped Data` ou filtrava a `Coverage` por `delivered` falha (aba inexistente) ou não encontra nenhuma linha, conforme o código dele.
- **Correção:** avise os consumidores antes de entregar. Se algum não puder mudar, ele pode ler pela posição (cabeçalhos do modelo na linha 1, dados a partir da linha 23) ou aceitar os dois nomes. Se o formato voltar a mudar, grave uma versão no próprio entregável.
