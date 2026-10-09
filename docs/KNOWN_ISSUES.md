# Problemas conhecidos

Esta lista foi compilada em 2026-10-08 e revista em 2026-10-09, depois da tradução do repositório para o português, da migração dos relatórios arquivados, da remoção das ferramentas de uso único e do passe de legibilidade (que dividiu as funções longas em etapas com nome, reuniu os limiares num bloco de constantes nomeadas e tirou o código morto, sem mudar nenhuma decisão). Os números de linha se referem ao código do commit `fddc9c1` do `main` e vão se deslocar na primeira mudança; a referência estável é o nome do símbolo. As contagens foram medidas sobre os relatórios arquivados, e todos os comandos de "Como ver" foram rodados de novo nesse estado. O dono do projeto preferiu documentar estes problemas a corrigi-los antes da entrega. Cada item diz onde está o problema, o que acontece, como ver, o que afeta e uma correção sugerida.

O passe de legibilidade resolveu o que era só código morto ou comentário desatualizado. O item 4.1 (números antigos da linha de base em comentários) saiu da lista, e os itens 2.9 e 4.3 ficaram menores; cada um diz o que saiu. Os itens que continuam mantêm o número, então a numeração tem lacunas, e os comentários do código que citam um item (`KNOWN_ISSUES 2.1`, por exemplo) continuam apontando para o lugar certo.

Convenção de nomes: o código continua pensando em inglês (identificadores, ids de etapa, valores internos como `accepted`), e tudo o que uma pessoa lê nos arquivos está em português. A fronteira entre os dois é `report_schema.py`. Quando um item cita uma aba, coluna ou valor de um relatório de mapeamento ou de uma planilha mapeada, o nome entre crases é o que `report_schema` grava (`Mapeamentos`, `apoiado_por`, `aceito`). Quando cita o código, usa o nome interno (`supported_by`, `status == "accepted"`). Nomes de arquivo e de pasta, flags da linha de comando e identificadores continuam em inglês.

O contexto está em outros documentos: o pipeline em [ARCHITECTURE.md](ARCHITECTURE.md), as planilhas em [DATA.md](DATA.md), a avaliação (`mapeval`) e os números em [EVALUATION.md](EVALUATION.md), e a instalação e as regras do projeto no [README](../README.md). Afirmações que estavam erradas em versões anteriores do README foram corrigidas lá e não se repetem aqui; este arquivo lista só as causas que continuam no código. Os termos fiscais seguem o [vocabulário de ARCHITECTURE.md](ARCHITECTURE.md#vocabulário).

## Antes de corrigir qualquer coisa

- O escopo é o algoritmo de correspondência. Uma mudança nele precisa se sustentar na validação deixando um cliente de fora. Nenhum código pode ramificar pelo nome de um cliente: os quatro clientes de amostra são exemplos de referência, nunca conjunto de treino. Prefira evidência que se transfere para um ERP nunca visto. Os gabaritos de-para são teste de regressão, não dado de treino. Ver o [protocolo de EVALUATION.md](EVALUATION.md#protocolo-para-uma-mudança-proposta).
- Fabricação é a falha que importa, e precisão vale mais que recall. O limiar de aceite de 0,90 fica; o caminho para entregar a faixa retida é o parecer do revisor. Cobertura parcial é o desenho: o denominador nunca é 227, e uma coluna que o cliente não tem não é defeito; nunca a chame de "faltante", "lacuna" ou "incompleta". Cite sempre os ENTREGUES antes dos encontrados.
- O `data/model.xlsx` é imutável por ora. O repositório guarda dados reais de clientes e é só para uso interno.
- Rode as duas verificações antes e depois de uma mudança. As duas são offline e não chamam a OpenAI:

  ```bash
  python -m pytest -q   # 343 testes passam em cerca de 8 s
  python -m mapeval     # ENTREGUES 32/56 (encontrados 43/56), fabricações 0
  ```

  Vários testes fixam totais absolutos (ver 3.18), então uma melhoria pode quebrar um teste. Quando isso acontecer, atualize o valor fixado de propósito.
- Não regenere `data/reports/mapping_sap_procosa_full.xlsx` com o mapeador. Ele guarda os únicos 26 pareceres do revisor do repositório, e regenerá-lo os apaga (ver 3.3).
- Qualquer mudança no prompt, no prefixo do embedding, em `DataMappingDecision` ou no payload do resolvedor de IA quebra `tests/test_model_input_golden.py` de propósito (ver 6.1).

## Índice

| ID | Problema | Onde fica |
|---|---|---|
| **1** | **Afeta entregáveis ou relatórios versionados** | |
| 1.1 | `resolvedor_ia` não aparece em `apoiado_por` em três relatórios arquivados | `data/reports/`, `data/mapped/` |
| 1.2 | A `Cobertura` mostra como `aguardando decisão` linhas que o revisor marcou `inexistente` | `apply_invoice_mapping.py` `_coverage_row` |
| 1.3 | A `Cobertura` deixa `numero_coluna_cliente` em branco nas linhas entregues | `apply_invoice_mapping.py` `_coverage_row` |
| **2** | **Pode enganar um revisor ou uma métrica** | |
| 2.1 | `vetado_por` só registra duas das quatro travas | `invoice_mapper.py` `_judge_proposal` |
| 2.2 | `decidido_por = resolvedor_ia` nunca acontece na prática | `invoice_mapper.py` `_supporting_step_ids` |
| 2.3 | Os relatórios arquivados e uma execução nova creditam a evidência de catálogo de jeitos diferentes | `data/reports/`, `invoice_mapper.py` |
| 2.4 | Duas definições da métrica de generalização | `mapeval/score.py`, `mapping_steps.py` |
| 2.5 | O mapeval imprime a contagem de aceitos como `pendências de revisão` | `mapeval/cli.py` `_report_unkeyed_client` |
| 2.6 | O mapeval ignora os pareceres do revisor | `mapeval/score.py` `Score.delivered_correct` |
| 2.7 | Aceites fora do gabarito não são conferidos contra o gabarito (oracle m41/m42) | `mapeval/outcomes.py` `classify` |
| 2.8 | O mapeval casa as colunas do modelo pela posição, nunca pelo nome | `mapeval/score.py` `score_report` |
| 2.9 | O mapeval conta as propostas sobre colunas vazias e nunca as informa | `mapeval/score.py` `Score.vacuous_proposals` |
| 2.10 | `metodo` diz o ramo do pipeline, não a evidência | `invoice_mapper.py` `_second_pass` |
| 2.11 | `cobertura_distinta_catalogo` fica vazia nas linhas da segunda passagem | `invoice_mapper.py` `_judge_proposal` |
| 2.12 | Várias linhas em revisão podem propor a mesma coluna do cliente | `invoice_mapper.py` `map_columns`, `_first_pass` |
| 2.13 | `confianca_final = 0,8999` é um teto, não uma medida | `invoice_mapper.py` `_local_final_confidence` |
| 2.14 | `margem_nome` pode ser negativa em linhas aceitas | `invoice_mapper.py` `_name_score_context` |
| 2.15 | O modo offline é outro pipeline | `invoice_mapper.py` `_start_run`, `_candidate_pool`, `_second_pass` |
| **3** | **Robustez e tratamento de dados** | |
| 3.1 | Falhas da API viram linhas em revisão e a execução sai com 0 | `invoice_mapper.py` `_second_pass` |
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
| 3.17 | A expansão de prefixos de palavras de papel casa palavras comuns | `invoice_mapper.py` `expand_truncated_role_words` |
| 3.18 | A CLI e os testes do mapeval são frágeis | `mapeval/cli.py`, `tests/test_mapeval.py` |
| 3.19 | Detalhes da instalação: `.env` e nenhum lockfile | raiz do repositório |
| 3.20 | A margem da primeira passagem vira com o ruído dos embeddings | `invoice_mapper.py` `_first_pass` |
| **4** | **Documentação e comentários desatualizados no código** | |
| 4.2 | `mapping_steps.py` cita funções que não existem | `mapping_steps.py` |
| 4.3 | O resumo da etapa de identificador promete mais do que ela faz | `mapping_steps.py` |
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
| 6.4 | O parecer do revisor tem leitura tolerante, mas `situacao` só no valor exato | `report_schema.py` `normalize_reviewer_status`, `internal_value` |
| 6.5 | `python -m mapeval --client <desconhecido>` ainda termina em traceback de `KeyError` | `mapeval/cli.py` `main` |
| 6.6 | As planilhas mapeadas mudaram de nome de aba e de rótulos | `apply_invoice_mapping.py`, `report_schema.py` |

---

## 1. Afeta entregáveis ou relatórios versionados

### 1.1 `resolvedor_ia` não aparece em `apoiado_por` em três relatórios arquivados

- **Onde:** nos relatórios de datasul, oracle e sap_terumo em `data/reports/` e no banner das planilhas mapeadas correspondentes em `data/mapped/`. A causa estava no `backfill_mapping_steps.py`, a ferramenta de uso único que acrescentou as colunas de etapas a esses relatórios (commit `88c503b`) e que saiu do repositório depois do commit `5fe5092`. No código daquele commit, o índice das colunas é montado na linha 147 de `backfill`, `sheet.insert_cols` roda na 156, `stored()` (165–166) continua usando o índice antigo e `llm_final_confidence` é lido na 184.
- **O que aconteceu:** no preenchimento retroativo (backfill), `backfill` inseria as três colunas de etapas numa âncora e depois lia os outros campos pelas posições que anotou antes da inserção. Todo campo à direita da âncora era lido três células à esquerda de onde estava. Os nomes abaixo são os do formato antigo, em inglês, em que o backfill rodou; nos relatórios migrados, as colunas correspondentes são `confianca_final_ia`, `apoiado_por`, `vetado_por` etc.
  - Nos relatórios de datasul, oracle e sap_terumo, a âncora era `arithmetic_proof`. A leitura de `llm_final_confidence` caiu na célula recém-inserida de `vetoed_by`, ainda vazia, então o resolvedor de IA nunca foi creditado.
  - Em sap_procosa, a âncora era `reviewer_status`, que vem depois das colunas `llm_*`, então esse relatório está correto.
  - Todos os outros campos que o backfill lia (`status`, `method`, as pontuações e as colunas de catálogo) ficavam à esquerda da âncora e foram lidos corretamente.
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

  O erro chega a `data/mapped/`, porque o apply copia `apoiado_por` para o banner da aba `Dados Mapeados`. Em 3 colunas entregues de datasul e 7 de sap_terumo, o resolvedor contribuiu para a escolha, mas o banner não diz isso. A migração para o português levou o erro adiante como estava: ela traduziu os ids de etapa, não refez o preenchimento.
- **Impacto:** nos três relatórios afetados, 185 linhas subestimam o papel do resolvedor. `decidido_por` e `vetado_por` não são afetados: o resolvedor tem a posição mais baixa, então nunca vence em `decidido_por`. Os números do mapeval também não, porque o mapeval não lê `apoiado_por`.
- **Correção:** consertar os arquivos versionados exige as ferramentas do commit `5fe5092`, numa cópia desse commit (`git worktree add /tmp/cbs-5fe5092 5fe5092`). Lá, depois do laço do cabeçalho de `backfill` (linhas 157–158), recalcule `index` a partir da linha 1. Isso foi testado em cópias dos relatórios anteriores ao backfill (`git show 88c503b^:mapping_<cliente>_full.xlsx`; naquele commit os relatórios ficavam na raiz): com essa linha a mais, o backfill credita o resolvedor em exatamente 75/32/60/78 linhas, e toda célula de `decided_by` e `vetoed_by` é idêntica à dos relatórios arquivados. A ordem importa, porque o backfill só aceita o formato antigo:
  1. Restaure de `88c503b^` os três relatórios afetados, numa pasta de rascunho.
  2. Rode neles o backfill corrigido, com a exportação correspondente de `data/clients/`.
  3. Migre cada um com o `migrate_reports.py` do mesmo commit.
  4. Ponha os três em `data/reports/` e rode de novo o `apply_invoice_mapping.py` para esses clientes.

  Numa cópia de oracle feita assim, a migração passou na conferência por regeneração e o resultado difere do relatório arquivado só em `apoiado_por`, nas 32 linhas esperadas. sap_procosa não precisa de conserto. Tudo isso precisa do histórico completo do git: um clone raso não tem esses commits.

### 1.2 A `Cobertura` mostra como `aguardando decisão` linhas que o revisor marcou `inexistente`

- **Onde:** `apply_invoice_mapping.py`, `_coverage_row`, linhas 549–554, que `_write_coverage_sheet` chama para cada coluna do modelo. A situação sai só do `status` do mapeador (`row["status"]`, na linha 553). O parecer do revisor nunca é consultado; a docstring de `_coverage_row` registra isso.
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
- **Correção:** em `_coverage_row`, leia também `row["reviewer_status"]` (já no vocabulário interno) e leve `no_mapping_exists` (o `inexistente` do relatório) a `STATE_ABSENT`. Uma ausência confirmada pelo revisor é uma resposta completa.

### 1.3 A `Cobertura` deixa `numero_coluna_cliente` em branco nas linhas entregues

- **Onde:** `apply_invoice_mapping.py`, `_coverage_row`, linha 551.
- **O que acontece:** numa linha entregue, a célula do número é gravada como `""`, enquanto `coluna_cliente` recebe o nome de exibição. As linhas que não foram entregues recebem o número proposto, quando há proposta.
- **Como ver:** toda linha `entregue` das quatro planilhas mapeadas tem o número em branco: datasul 19, oracle 18, sap_procosa 62 e sap_terumo 67. `coluna_cliente` está preenchida em todas.
- **Impacto:** justamente as linhas que um usuário a jusante mais quer rastrear até a exportação ficam sem o número de coluna usado em todo o resto, e quem lê precisa casar pelo nome de exibição.
- **Correção:** grave `column.index + 1` quando `column is not None`.

---

## 2. Pode enganar um revisor ou uma métrica

### 2.1 `vetado_por` só registra duas das quatro travas

- **Onde:** `invoice_mapper.py`, `_judge_proposal`, linhas 3057–3061, o único lugar que grava o campo; o antigo backfill gravou os mesmos dois rótulos nos relatórios arquivados. O registro das travas é `mapping_steps.py` `GUARDS` (142–187), cujo comentário já avisa que só duas chegam a `vetado_por`.
- **O que acontece:** só duas travas são gravadas, e só em linhas da segunda passagem: `vacuity` (`coluna_vazia` no relatório), que recusa uma coluna do cliente vazia, e `family_conflict` (`conflito_tributario`), que recusa um conflito de família de tributo ou de papel. `underspecified` (`cabecalho_generico`) e `exclusivity` (`coluna_ja_consumida`) estão registradas, mas nenhum código as grava.

  Vários outros caminhos de recusa agem como veto e não deixam rótulo nenhum:
  - as recusas da primeira passagem (`accepted_by_name`, em `_first_pass`, 2034–2040): conflito de cabeçalho, cabeçalho genérico, coluna vazia
  - a contradição aritmética forte na regra de aceite da segunda passagem (`_accept_gate`, 2998)
  - a auditoria de catálogo que reabre um aceite da primeira passagem (`_audit_accepted_catalog_matches`, 2073–2093, com `_catalog_contradicts_accept`)
  - o rebaixamento aritmético (`_demote_inconsistent_tax_header_matches`, 2096–2108, com `_inconsistent_trio_position`)
- **Como ver:** `grep -rn --include='*.py' -e '"underspecified"' -e '"exclusivity"' .` só encontra o registro (`mapping_steps.py` 166 e 177) e a tabela de tradução (`report_schema.py` `GUARD_IDS`, 256–257). Nos relatórios versionados, os valores são datasul `coluna_vazia` 6, sap_terumo `coluna_vazia` 11, sap_procosa `conflito_tributario` 3, e nenhum em oracle.
- **Impacto:** um `vetado_por` em branco é lido como "nenhuma trava disparou", o que muitas vezes é falso.
- **Correção:** dê um id de trava a cada caminho de recusa e grave-o na linha afetada. Nas recusas da primeira passagem, leve o id para a linha da segunda passagem. Um id novo precisa também de um nome em português em `report_schema.GUARD_IDS`; `tests/test_report_schema.py` acusa se faltar. Alternativamente, documente que a coluna cobre só essas duas travas.

### 2.2 `decidido_por = resolvedor_ia` nunca acontece na prática

- **Onde:** `invoice_mapper.py` `_supporting_step_ids`, linhas 1806–1809; `mapping_steps.py` `deciding_step` (204–211).
- **O que acontece:** `decidido_por` é a etapa de posição mais alta em `apoiado_por`, e o resolvedor tem a mais baixa. A etapa de cabeçalho (`semantico` ou `lexico`) entra sempre que `name_confidence > 0`.
  - Online, as pontuações de cabeçalho são similaridades de cosseno limitadas a [0, 1] (`OpenAIEmbeddingHeaderScorer.scores`, 1514), e nunca são 0 numa coluna proposta.
  - Offline, o resolvedor nem roda.

  Por isso, uma linha que a IA escolheu quando o cabeçalho não era decisivo aparece como decidida pelo cabeçalho.
- **Como ver:**
  - Nenhuma linha dos quatro relatórios tem `decidido_por = resolvedor_ia`.
  - A menor `confianca_nome` numa linha com proposta é 0,7688 (sap_procosa).
  - 222 linhas em revisão (datasul 68, oracle 32, sap_procosa 51, sap_terumo 71) têm uma escolha do resolvedor e um cabeçalho abaixo do limiar de 0,90 ou da margem de 0,05, e mesmo assim dizem `decidido_por = semantico`.
- **Impacto:** `decidido_por = resolvedor_ia` deveria dizer que nada objetivo sustentou o mapeamento e o julgamento da IA decidiu, mas esse valor nunca aparece, então o revisor não distingue as linhas decididas pelo cabeçalho das decididas pela IA. A parcela transferível não muda, porque nenhuma das duas etapas se transfere. (O comentário de `mapping_steps.py` que prometia esse significado foi encurtado no passe de legibilidade e não promete mais nada; o defeito continua.)
- **Correção:** conte a etapa de cabeçalho como decisiva só quando o cabeçalho decidiu sozinho (`_LocalEvidence.name_is_decisive`, calculado em `_local_evidence`, 2910–2914). Caso contrário, liste-a como corroboração, para que `resolver` possa vencer quando nada objetivo sustenta a linha.

### 2.3 Os relatórios arquivados e uma execução nova creditam a evidência de catálogo de jeitos diferentes

- **Onde:**
  - Relatórios arquivados: as colunas de etapas foram gravadas pelo antigo `backfill_mapping_steps.py`. No código do commit `5fe5092`, o último que o tem: `DISTINCTIVE_CATALOG_SCORE` (72), o bloco do catálogo (196–212) e o ramo `explained_by_catalog` (228–237).
  - Execução nova: `invoice_mapper.py` `catalog_evidence_is_distinctive` (1704–1731). Os rótulos dos aceites da primeira passagem são montados em `_first_pass` (`_evidence_attribution`, 2048–2050), antes de `_audit_accepted_catalog_matches` (chamada em `map_columns`, 1935) anexar a evidência de catálogo.
- **O que acontece:** os dois caminhos diferem em três pontos.
  1. **Limiar.** O backfill tratava um catálogo como decisivo a partir de 0,90, sem mais nada. A regra da execução nova usa um piso de 0,50 mais pelo menos 5 códigos distintos nos catálogos de especificidade 1,0, e 0,90 nos demais. Os catálogos de especificidade 1,0 são o CFOP, o NCM e o código de município do IBGE.
  2. **O atalho `explained_by_catalog`.** O backfill também creditava o catálogo como decisivo sempre que `data_confidence` é igual a `catalog_confidence`, qualquer que seja o catálogo. Isso inclui as tabelas de CST, que a regra de distintividade da execução nova (`catalog_evidence_is_distinctive`) nunca trata como decisivas, porque a especificidade de 0,75 ou menos mantém a pontuação abaixo de 0,90.
  3. **Aceites da primeira passagem.** Numa execução nova, um aceite da primeira passagem nunca lista `catalog`, porque os rótulos são calculados antes de a auditoria anexar a evidência. O backfill listava.
- **Como ver:**
  - m176 de sap_procosa, 'Código de Sit Tributária do CBS e IBD': está em `revisao` e mostra `decidido_por = catalogo`, vindo de `cst_ibs_cbs` com 0,75.
  - m204 de datasul, 'Nº do NCM': é um aceite da primeira passagem (`metodo = nome`) que mostra `decidido_por = catalogo`. Um relatório gerado agora diria `semantico`.
- **Impacto:** `decidido_por`, e qualquer métrica construída sobre ele (2.4), muda quando um relatório é regenerado, mesmo com evidência idêntica.
- **Correção:** na execução nova, recalcule `supported_by`/`decided_by` dos aceites da primeira passagem depois da auditoria (por exemplo, chamando `_evidence_attribution` de novo, com a pontuação de catálogo, em `_audit_accepted_catalog_matches`). As regras do backfill só vivem nos relatórios arquivados: a diferença some quando eles forem regenerados (online; ver 3.3 antes de regenerar sap_procosa). Até lá, ao comparar `decidido_por` de um relatório arquivado com o de um novo, lembre que as regras diferem.

### 2.4 Duas definições da métrica de generalização

- **Onde:**
  - mapeval: `mapeval/score.py` `HEADER_INDEPENDENT_METHODS` (24) e `Row.header_independent` (41–52), impresso por `mapeval/cli.py` `_report_keyed_client` (158–159) como "aceites independentes de cabeçalho … métrica de generalização".
  - Registro de etapas: `mapping_steps.py` `TRANSFERRING_STEP_IDS` (199–201) e `rests_only_on_headers` (220–227). Só os testes os usam.
- **O que acontece:** os dois arquivos decidem de modo diferente se um aceite se transferiria para um ERP nunca visto.
  - O mapeval conta um aceite como independente de cabeçalho só quando ele tem uma `prova_aritmetica` decisiva ou `metodo = catalogo`.
  - O registro conta um aceite como transferível quando ele se apoia em evidência de perfil, identificador, catálogo ou aritmética.

  Nenhum dos dois conta os aceites `nao_registrado`, que se apoiam em evidência objetiva que o antigo backfill não soube nomear.
- **Como ver:**
  - Datasul: o mapeval imprime 36,8% (7 de 19 aceitos). Contando os aceites cujo `decidido_por` é uma etapa transferível, dá 8 de 19 (42,1%). A diferença é m204, 'Nº do NCM' (ver 2.3).
  - Oracle: os dois dão 6 de 18 (33,3%).
  - Os aceites `nao_registrado` contam como presos ao cabeçalho nas duas definições: datasul 2, entre eles 'Chave de acesso' ← 'Chave Nfe' com `confianca_dados` 1,0; sap_procosa 3; sap_terumo 1.
- **Impacto:** o número que o projeto tenta aumentar depende de qual arquivo você lê, e as duas versões contam a menos.
- **Correção:** fique com uma definição só. Calcule a parcela no mapeval a partir de `apoiado_por`/`decidido_por` com `mapping_steps.TRANSFERRING_STEP_IDS`, depois de corrigir 2.3; a célula lida do relatório precisa passar antes por `report_schema.steps_to_internal`, porque `mapping_steps` só reconhece os ids internos. `nao_registrado` só existe nos relatórios preenchidos pelo antigo backfill: o mapeador atual já registra `perfil` e `identificador` separadamente e nunca grava esse valor.

### 2.5 O mapeval imprime a contagem de aceitos como `pendências de revisão`

- **Onde:** `mapeval/cli.py`, `_report_unkeyed_client`, linhas 128–130; o comentário da linha 128 aponta para este item.
- **O que acontece:** a linha dos clientes sem gabarito formata `len(accepted)` duas vezes.
- **Como ver:** `python -m mapeval` imprime `aceitos  45   pendências de revisão  45` para sap_procosa e `67 / 67` para sap_terumo. O número coincide hoje porque nenhuma linha aceita desses dois relatórios tem parecer. Confirmar um aceite não o reduziria.
- **Impacto:** as pendências nunca diminuem conforme os revisores trabalham.
- **Correção:** conte as linhas aceitas cujo parecer do revisor está em branco, e decida se as linhas em `revisao` ainda pendentes também entram na conta.

### 2.6 O mapeval ignora os pareceres do revisor

- **Onde:**
  - `mapeval/score.py` `Score.delivered_correct` (77–88). O código conta só `status == "accepted"`; a docstring reconhece que o apply também entrega as linhas confirmadas pelo revisor e aponta para este item.
  - O argumento `delivered=` em `_score_row` (251), chamado por `score_report`.
  - O ramo dos clientes sem gabarito, `mapeval/cli.py` `_report_unkeyed_client` (108–131).
  - A regra que o mapeval deveria seguir é `apply_invoice_mapping.py` `_effective_columns` (414–442), com a escolha por parecer em `_reviewed_column` (357–384). Ela entrega as linhas `correto` e as `incorreto` com correção, e descarta as `inexistente`, mesmo as aceitas.
- **O que acontece:** o mapeval trata a `situacao` do mapeador como "entregue", mas o que o apply de fato entrega depende também dos pareceres do revisor.
- **Como ver:**
  - Datasul e oracle não têm pareceres, então os números com gabarito não são afetados hoje.
  - sap_procosa tem 26 pareceres: 15 `correto`, 9 `inexistente` e 2 `incorreto`. A planilha mapeada dele traz 62 colunas (45 aceitas, 15 confirmadas e 2 corrigidas pelo revisor), enquanto a linha do mapeval mostra só os 45 aceitos.
  - A verificação de fabricação tem o mesmo ponto cego. Ela só olha as linhas aceitas (`score.py` `_score_row`, 247–252; `cli.py` `_report_unkeyed_client`, 117–127), e o apply não tem verificação própria de coluna vazia (`grep -n vacu apply_invoice_mapping.py` não encontra nada). Se um revisor confirmasse uma proposta sobre uma coluna do cliente vazia, o apply entregaria uma coluna em branco e o código de saída do mapeval não acusaria. Hoje não existe linha assim.
- **Impacto:** quando datasul ou oracle receberem pareceres, o número ENTREGUES estará errado. E o único bloqueio automático, a verificação de fabricação, não cobre as linhas confirmadas pelo revisor.
- **Correção:** leve a regra de seleção de linhas do apply (`_reviewed_column`) para uma função que o apply e o mapeval chamem, e calcule a entrega e a fabricação a partir dela. A função precisa receber o parecer já normalizado por `report_schema.normalize_reviewer_status`, como o apply faz hoje.

### 2.7 Aceites fora do gabarito não são conferidos contra o gabarito (oracle m41/m42)

- **Onde:** `mapeval/outcomes.py` `classify` (49–52) devolve `UNDETERMINED` para qualquer coluna do modelo fora do gabarito; também `mapeval/score.py` `Score.undetermined_accepts` (119–127).
- **O que acontece:** o gabarito diz qual coluna do cliente alimenta cada coluna do modelo que ele cobre. Suponha que um aceite fora do gabarito pegue uma coluna do cliente que o gabarito atribui a outra coluna do modelo. É uma contradição direta, mas o mapeval só confere o lado da coluna do modelo e não a vê.
- **Como ver:**
  - Oracle aceita m41 'Base ICMS' ← c28 'BASE ICMS' e m42 'Valor ICMS' ← c24 'VALOR ICMS'.
  - O gabarito de oracle atribui c28 a m99 'Base ICSP' e c24 a m101 'Valor ICSP'. `python -m mapeval --client oracle --show-losses` lista m99 e m101 como `perdido`, com `esperada=c28` e `esperada=c24`.
  - Os dois aceites só entram na contagem da linha `sem_veredito` (3 em oracle), nunca são listados, e o código de saída é 0.
- **Impacto:** uma de duas coisas é verdade:
  - a planilha mapeada põe nas colunas de ICMS do modelo as colunas que o gabarito chama de ICSP, ou seja, uma coluna de tributo errada (exatamente o que a regra de fabricação existe para impedir); ou
  - o gabarito está errado nessas duas colunas, e elas pertencem a `KEY_CORRECTIONS`.

  O mapeval tem os dados para forçar essa decisão e não força.
- **Correção:** em `score_report` (ou em `classify`, passando também as colunas do cliente que o gabarito atribui), dê a um aceite cuja coluna do cliente o gabarito atribui a outro lugar um resultado próprio (por exemplo `CONFLICTS_KEY`, com rótulo em português em `report_schema.ENUMS["outcome"]`), imprima-o e decida se ele deve bloquear. Depois resolva m41/m42 pelos dados do cliente. Não resolva com uma regra que mapeia cabeçalhos de ICMS para ICSP: seria ajustar o algoritmo ao vocabulário de um cliente (ver [EVALUATION.md](EVALUATION.md#os-5-aceites-sem-veredito)).

### 2.8 O mapeval casa as colunas do modelo pela posição, nunca pelo nome

- **Onde:** `mapeval/score.py` `score_report`, linha 212 (posição da linha na aba `Mapeamentos`, que `_score_row` usa como número da coluna do modelo; o comentário da linha 211 aponta para este item), e `mapeval/truth.py` `load_answer_key`, linha 65 (posição na linha 1 do de-para).
- **O que acontece:** nenhuma das duas lê `numero_coluna_modelo` nem compara nomes. O apply, ao contrário, reordena as linhas pelo número e confere cada nome (`_ordered_mapping`, 220–260).
- **Como ver:** hoje os quatro relatórios e os dois gabaritos batem com o model.xlsx: os números coincidem com as posições e nenhum nome difere.
- **Impacto:** uma aba `Mapeamentos` filtrada, ordenada ou editada à mão seria pontuada contra as entradas erradas do gabarito, sem erro nenhum.
- **Correção:** indexe as linhas por `numero_coluna_modelo` (o campo interno `model_column_number`, que o índice devolvido por `read_mappings` já traz) e confira que cada nome bate com o model.xlsx, como faz o apply.

### 2.9 O mapeval conta as propostas sobre colunas vazias e nunca as informa

- **Onde:** `mapeval/score.py` `Score.vacuous_proposals` (63), preenchido em `score_report` (217–218).
- **O que acontece:** `vacuous_proposals`, as propostas em revisão sobre colunas do cliente vazias, é calculado e nunca impresso. Dá 6 em datasul e 0 em oracle.
- **Resolvido no passe de legibilidade:** o resto deste item era código morto e saiu. `REGRESSION_GUARDED` foi apagado; `classify` perdeu o resultado `CORRECTLY_ABSENT`, os ramos que só os testes alcançavam e os parâmetros `in_key` e `model_column`; e `Score.correct_pairs`, que a CLI não usa, ficou documentado como o que é: a propriedade que o passo 3 do [protocolo de EVALUATION.md](EVALUATION.md#3-compare-as-linhas-não-só-os-totais) usa para comparar pares.
- **Impacto:** baixo, mas quem lê pode supor que o valor entra na pontuação.
- **Correção:** imprima `vacuous_proposals` na saída de cada cliente com gabarito (e calcule o mesmo para os clientes sem gabarito), ou apague o campo.

### 2.10 `metodo` diz o ramo do pipeline, não a evidência

- **Onde:** `invoice_mapper.py` `_second_pass`, linha 2582 (`method="data" if resolver_ran else "catalog"`), trocado para `"data"` pelas substituições 1 e 2 (`_unique_relationship_override`, 2679, e `_distinctive_overlap_override`, 2735) e gravado na linha por `_judge_proposal` (3043). O mapeval lê `metodo` por `HEADER_INDEPENDENT_METHODS` e `method_census`.
- **O que acontece:** toda linha da segunda passagem recebe `metodo = dados`, ou `catalogo` quando a decisão local por catálogo disparou, qualquer que tenha sido a evidência que de fato a sustentou. `metodo = catalogo` não implica que a linha foi aceita.
- **Como ver:**
  - sap_terumo aceita 'Utilização de material' ← 'Material usage' (`confianca_dados` 0,84) e 'Nº NF-e dos serviços' ← 'Service NF-e Number' (`confianca_dados` 0,00). As duas têm `metodo = dados` e `decidido_por = semantico`, porque o cabeçalho foi decisivo.
  - Oracle 'CFOP' ← 'CFO' tem `metodo = catalogo` e `situacao = revisao` (confiança final 0,8547, pontuação de catálogo 0,8824).
- **Impacto:** o censo de métodos do mapeval, e qualquer um que filtre por `metodo`, interpreta mal essas linhas.
- **Correção:** documente `metodo` como "o ramo seguido" e use `decidido_por` para responder "o que decidiu". Alternativamente, renomeie a coluna na próxima versão do esquema, em `report_schema.COLUMNS` e com aumento de versão (ver 5.1).

### 2.11 `cobertura_distinta_catalogo` fica vazia nas linhas da segunda passagem

- **Onde:** o campo `MappingResult.catalog_distinct_coverage` (1848) só é preenchido por `_audit_accepted_catalog_matches` (2088–2090), e só para aceites da primeira passagem. A chamada de `_mapping_row` em `_judge_proposal` (3039–3067), que grava as linhas da segunda passagem que propõem uma coluna, nunca o preenche (o comentário da linha 3062 aponta para este item), embora a regra que mantém uma linha da segunda passagem em revisão (`_review_proposal_is_supported`) receba `_LocalEvidence.catalog_distinct_coverage` (3027) e o compare com `REVIEW_MIN_CATALOG_COVERAGE` (3120–3123).
- **Como ver:** comparando as linhas com `nome_catalogo` e as linhas com `cobertura_distinta_catalogo` preenchida: datasul 4 contra 1, oracle 5 contra 4 e sap_terumo 7 contra 6. Toda linha preenchida é um aceite da primeira passagem (`metodo = nome`). As linhas de CFOP de datasul e oracle, decididas pelo catálogo, estão em branco. O relatório de sap_procosa é anterior à coluna.
- **Impacto:** nas linhas decididas pelo catálogo, o revisor não vê o número em que a pontuação de catálogo e a regra de revisão de fato se apoiam. A aba mostra só `cobertura_catalogo`, ponderada pelas linhas, que pode ser bem menor: no 'CFO' de oracle ela é 0,6673, contra uma cobertura de códigos distintos de 0,8824.
- **Correção:** em `_judge_proposal`, passe `catalog_distinct_coverage=round(local.catalog_distinct_coverage, 4)` sempre que `local.catalog` estiver preenchido.

### 2.12 Várias linhas em revisão podem propor a mesma coluna do cliente

- **Onde:** `invoice_mapper.py`. Só os aceites saem do conjunto disponível, `_MappingRun.available` (`available.remove` em `_first_pass`, 2052, e em `map_columns`, 1942–1943).
- **O que acontece:** uma proposta em revisão não consome a sua coluna do cliente. Uma coluna do cliente pode então ser proposta para muitas colunas do modelo, inclusive uma coluna que outra coluna do modelo, mais adiante, aceita.
- **Como ver:**
  - Colunas do cliente propostas por mais de uma linha em revisão: datasul 26 (até 6 vezes cada), oracle 7 (até 11), sap_procosa 14 (até 8) e sap_terumo 12 (até 7).
  - Linhas em revisão cuja coluna do cliente é aceita em outra linha: datasul 1, sap_terumo 2.
  - Exemplo: 'Vlr Isen ICM' de datasul (valor isento de ICMS) é proposta para 'Isentas IBS', 'Isentas ICMS', 'Isentas ICAP', 'Isentas ICSP', 'Isentas ICMS_FECP' e 'IVA' (ver também 3.9).
- **Impacto:** o revisor vê a mesma coluna oferecida várias vezes. Confirmar duas dessas linhas, ou uma cuja coluna já foi aceita, faz o apply parar com "A coluna do cliente … foi mapeada mais de uma vez após as correções do revisor" (`_effective_columns`, 434–440).
- **Correção:** na aba `Fila de Revisão`, marque as colunas do cliente compartilhadas ou já aceitas. Considere descartar as propostas em revisão cuja coluna uma linha posterior aceitou.

### 2.13 `confianca_final = 0,8999` é um teto, não uma medida

- **Onde:** `invoice_mapper.py` `_local_final_confidence`, linhas 2976–2987. Quando nem o cabeçalho nem os dados objetivos são decisivos, o valor é limitado ao teto `MapperConfig.clamp_ceiling` (`final_threshold - CEILING_GAP`, ou seja, `final_threshold - 0.0001`). O código reconhece isso em `summarize_adjudication_queue` (3380–3381).
- **Como ver:** linhas no teto: datasul 11, oracle 2, sap_procosa 14 e sap_terumo 33. Das 11 linhas com gabarito encontradas corretamente mas retidas, cinco estão em 0,8999: datasul m45, m50, m51, m52 e oracle m210.
- **Impacto:** na aba `Mapeamentos` e no `mapeval --show-losses`, que ordena por esse valor, "0,8999" parece "quase aceito". A aba `Fila de Revisão` já explica o teto em `motivo_retencao` e desempata pela margem; as outras vistas não.
- **Correção:** mantenha o teto na regra de aceite, mas grave também o valor sem teto numa coluna própria (por exemplo, um campo interno `unclamped_confidence`, com cabeçalho em português em `report_schema.COLUMNS`).

### 2.14 `margem_nome` pode ser negativa em linhas aceitas

- **Onde:** `invoice_mapper.py` `_name_score_context` (2439–2453), chamada com a coluna escolhida em `_local_evidence` (2883–2885).
- **O que acontece:** a margem é a pontuação do cabeçalho da coluna escolhida menos a da melhor alternativa. Quando a segunda passagem escolhe uma coluna que não é a melhor pelo cabeçalho, a margem fica negativa.
- **Como ver:** datasul 'CFOP' ← 'Cod. Fiscal' é aceita com `margem_nome` -0,0582 (decidida pelo catálogo). sap_procosa tem 3 linhas aceitas com margem negativa.
- **Impacto:** numa linha aceita, uma margem negativa parece um teste de margem reprovado.
- **Correção:** documente a coluna como "vantagem de cabeçalho da coluna escolhida sobre a melhor outra coluna; negativa quando os dados prevaleceram sobre o cabeçalho", ou renomeie-a.

### 2.15 O modo offline é outro pipeline

- **Onde:** em `invoice_mapper.py`:
  - Os perfis das colunas do cliente são pulados (`_start_run`, 1978–1985).
  - A evidência de relações aritméticas é pulada (`_candidate_pool`, 2486–2488).
  - A evidência de catálogo ainda é calculada (`_candidate_pool`, 2489–2490, com um comentário que aponta para este item) e depois descartada, quando `_second_pass` fecha a coluna offline com `method = name_only`, `status = review` (2548–2553).
  - A decisão local por catálogo (`_second_pass`, 2559–2561) nunca é alcançada.
- **O que acontece:** com `--offline`, só a primeira passagem aceita. Toda linha da segunda passagem vira `revisao` (`metodo = somente_nome`), sem `apoiado_por` nem `decidido_por`. As linhas `erro_dados` de 3.1 (`_second_pass`, 2562–2570) ficam com os mesmos rótulos em branco.
- **Impacto:** os resultados offline subestimam o que uma execução online entrega e nunca devem ser citados como o número do produto. O cálculo do catálogo é desperdiçado.
- **Correção:** em `_candidate_pool`, pule `_candidate_catalog_evidence` quando `online` é falso. Alternativamente, deixe o modo offline usar `_unique_distinctive_catalog_decision`, que não precisa da API. Em qualquer caso, preencha `supported_by` nessas linhas.

---

## 3. Robustez e tratamento de dados

### 3.1 Falhas da API viram linhas em revisão e a execução sai com 0

- **Onde:** `invoice_mapper.py` `_second_pass`, linhas 2562–2570 (`except Exception`, em volta da chamada a `_ask_resolver`). `_run` devolve 0 em 3792.
- **O que acontece:** o mapeador captura qualquer exceção da chamada ao resolvedor, coluna a coluna do modelo. Isso cobre uma cota ou um limite de taxa estourado no meio da execução, um erro de rede ou uma resposta que não passa na validação. A linha é gravada com `metodo = erro_dados`, `situacao = revisao` e a justificativa "A comparação de dados falhou: <tipo>: <mensagem>" (`REASON_DATA_ERROR`, 201; a mensagem da exceção chega em inglês, ver 6.2). A execução termina e sai com 0. Nenhum teste exercita esse caminho: os testes que citam `data_error` só gravam ou traduzem uma linha montada à mão.
- **Impacto:** uma cota que acaba ou uma conexão que cai no meio da execução produz um relatório de aparência normal, com toda coluna em dúvida marcada como `revisao`, e nada avisa o operador de que a execução saiu degradada. A chamada de embeddings no começo da execução (`_start_run`, 1957) não está protegida, então uma chave inválida desde o início para a execução ali.
- **Correção:** capture só os erros de uma decisão, como a análise ou a validação da resposta. Deixe os erros de autenticação, cota e conexão subirem. Saia com código diferente de zero, ou pelo menos imprima uma contagem, sempre que uma linha `erro_dados` for gravada.

### 3.2 Valores do cliente vão para a OpenAI sem mascaramento

- **Onde:** em `invoice_mapper.py`:
  - `OpenAIEmbeddingHeaderScorer._embeddings` (1492–1504) envia todos os cabeçalhos do modelo e do cliente.
  - `OpenAIDataResolver.resolve` (1568–1686), chamado por `_ask_resolver`, roda uma vez para cada coluna do modelo em dúvida. Envia os valores de amostra da própria coluna, tirados do model.xlsx, e um perfil de cada coluna candidata do cliente.
  - O conjunto de candidatos é a união de até `candidate_count` (10) colunas ranqueadas por cabeçalho, por perfil, por aritmética e por catálogo (`_rank_name_and_profile_candidates`, 2390–2436), ou seja, até 40 colunas.
  - Cada perfil inclui até `sample_size` (8) valores crus (`summarize_values`, chave `"samples"` em 966), além de mínimo, máximo e quartis.
  - `store=False` está ligado (1613), mas nada é mascarado.
- **Impacto:** no modo online, CNPJs, CPFs, chaves de acesso de NF-e e valores saem da consultoria. O repositório é compartilhado internamente de propósito, mas cada cliente precisa autorizar uma execução online antes que ela aconteça. O modo offline não envia nada.
- **Correção:** mascare os valores com cara de identificador antes de montar o payload. O perfil já traz `valid_cpf_fraction`, `valid_cnpj_fraction` e `valid_nfe_key_fraction`, então o modelo perde pouco. Alternativamente, omita `samples` nas colunas que validam como identificador. Qualquer das duas muda o payload: aumente `PROMPT_VERSION`, regenere o arquivo dourado e meça de novo online (ver 6.1).

### 3.3 Regenerar um relatório apaga os pareceres do revisor

- **Onde:** `invoice_mapper.py` `write_results` (3450–3489). As colunas do revisor são sempre gravadas em branco (3465–3466), e o arquivo de saída é sobrescrito sem checagem (3471).
- **O que acontece:**
  - Apontar `--output` para um relatório existente, por exemplo `data/reports/mapping_sap_procosa_full.xlsx`, apaga as colunas do revisor. O nome padrão (ver 3.4) não colide com os arquivos `*_full.xlsx`, então isso exige um caminho explícito.
  - Não existe caminho de mesclagem. Se a exportação do cliente muda, o apply recusa o relatório antigo pelo hash, e a única saída é um relatório novo com as colunas do revisor em branco.
- **Como ver:** `data/reports/mapping_sap_procosa_full.xlsx` guarda os únicos 26 pareceres do revisor do repositório.
- **Impacto:** um único comando apaga trabalho de revisão.
- **Correção:**
  - Recuse sobrescrever um relatório existente sem `--force`.
  - Crie um passo de mesclagem que leve `parecer_revisor`, `coluna_cliente_correta` e `observacao_revisor` para o relatório novo pelo `numero_coluna_modelo`, sempre que a coluna do cliente proposta não tiver mudado. O passo lê o relatório antigo por `report_schema`; um relatório no formato antigo, em inglês, precisa antes ser convertido (ver o [README](../README.md#comandos-do-dia-a-dia)).

### 3.4 O relatório vai, por padrão, para a pasta atual

- **Onde:** `invoice_mapper.py` `_run`, linha 3684.
- **O que acontece:** sem `--output`, o relatório é gravado como `mapping_<nome do arquivo do cliente>.xlsx` na pasta de trabalho, e não em `data/reports/`, e o arquivo não está no `.gitignore`. Até isso mudar, passe `--output data/reports/…`.
- **Correção:** use `data/reports/mapping_<nome>.xlsx` como padrão, junto com a proteção contra sobrescrita de 3.3.

### 3.5 A saída em CSV perde três abas

- **Onde:** `invoice_mapper.py` `write_results`, linhas 3468–3469.
- **O que acontece:** uma saída `.csv` grava só a tabela `Mapeamentos` (já em português), então `Metadados da Execução`, `Colunas do Cliente sem Uso` e `Fila de Revisão` se perdem. O apply lê relatórios com `pd.read_excel`, então um relatório em CSV não pode ser aplicado.
- **Correção:** elimine a saída CSV, ou grave um CSV por aba.

### 3.6 A verificação de relatório desatualizado do apply pode ser pulada

- **Onde:** em `apply_invoice_mapping.py`:
  - `_run_metadata` (190–202) devolve `{}` quando o relatório não tem a aba `Metadados da Execução`.
  - `_validate_input_hashes` (205–217) pula qualquer hash não gravado.
  - `_read_mapping_report` (180–187) lê a primeira aba, e não a aba `Mapeamentos` pelo nome.
- **O que acontece:**
  - Um relatório sem metadados, ou sem `sha256_cliente`/`sha256_modelo`, é aplicado sem verificação de atualidade. A conferência dos nomes das colunas propostas (`_refuse_proposal_from_another_export`, 401–411) ainda pega a exportação de outro cliente, mas não uma nova exportação do mesmo ERP com outras linhas.
  - `versao_esquema_mapeamento`, `versao_prompt` e os valores gravados de `aba_cliente`, `linha_cabecalho_cliente` e `linha_cabecalho_modelo` nunca são comparados com os argumentos do próprio apply.
  - Uma aba inserida antes de `Mapeamentos`, por exemplo à mão no Excel, faz o apply falhar com uma mensagem enganosa: "A aba Mapeamentos não tem a coluna 'numero_coluna_modelo'; confira se o arquivo é um relatório de mapeamento.", quando a aba lida nem é a `Mapeamentos`. O gravador avisa sobre isso num comentário em `invoice_mapper.py` `write_results` (3477–3479), e a docstring de `_read_mapping_report` também.
- **Correção:** exija os metadados e os dois hashes, compare as configurações de aba e de linha de cabeçalho, e leia a aba de mapeamentos pelo nome (`report_schema.find_sheet(nomes, "mappings")`, como o mapeval já faz).

### 3.7 Problemas menores na leitura das entradas do apply

Todos ficam em `apply_invoice_mapping.py`.

| Problema | Onde | O que acontece | Correção |
|---|---|---|---|
| `situacao` comparada sem normalizar | `_reviewed_column` 381 e `_effective_columns` 430; normalizada só por caixa e espaços, para a `Cobertura`, em `_coverage_row` 553 | `report_schema.internal_value` converte só o valor exato (`aceito`, `revisao`). Uma `situacao` editada à mão como `Aceito ` (maiúscula e espaço no fim) ou `Revisão` (com acento) passa crua: a linha some dos dados e a `Cobertura` a põe em `não consta na exportação do cliente`. Numa cópia do relatório de oracle com essas duas edições, o apply entregou 17 colunas em vez de 18 e pôs 174 linhas em `não consta na exportação do cliente` em vez de 172, com saída 0 | Normalize `situacao` uma vez, ignorando caixa, espaços e acentos, como já se faz com o parecer (ver 6.4). O teste `test_enum_values_are_converted_only_when_exact` fixa a regra atual |
| `--include-review` chama de `entregue` propostas não confirmadas | `_coverage_row` 549–550; colunas repetidas permitidas em `_effective_columns` 434–440 (só as aceitas ou confirmadas são conferidas) | Uma execução de oracle numa pasta de rascunho com `--include-review` rotulou 37 linhas em revisão como `entregue`, e 30 linhas entregues dividiam uma coluna do cliente com outra linha entregue | Crie um estado separado, como "entregue (não confirmada)" |
| Texto numérico em `coluna_cliente_correta` é lido como número de coluna | `_corrected_column` 311–316 | Um cabeçalho do cliente que seja literalmente um número, como '2024', não pode ser indicado pelo nome | Tente primeiro casar pelo nome, ou exija um prefixo como `#12` para números |

### 3.8 O DV de CPF/CNPJ falha em células numéricas

- **Onde:** `invoice_mapper.py` `_digits_only` (834–835), `_cpf_is_valid` (838–849) e `_cnpj_is_valid` (852–864). São usados por `_validation_fraction` (881–882), que alimenta `identifier_match_score`.
- **O que acontece:** os validadores do dígito verificador (DV) exigem exatamente 11 ou 14 dígitos e nunca devolvem os zeros à esquerda. Quando um ERP exporta o CNPJ como número, o zero à esquerda some. Uma célula float ainda ganha um `0` a mais, vindo do `.0`.
- **Como ver:**

  ```bash
  python -c "import invoice_mapper as im; v='01234567000195'; print(im._cnpj_is_valid(im._digits_only(v)), im._cnpj_is_valid(im._digits_only(int(v))))"
  # True False
  ```

  - Como float, o mesmo valor vira os dígitos `12345670001950`.
  - Uma coluna desses inteiros tem `valid_cnpj_fraction` 0,0 em vez de 1,0.
  - O prompt do resolvedor avisa a IA sobre zeros à esquerda perdidos (1649–1651), mas o código local não aplica essa regra.
- **Impacto:** a evidência de identificador, uma das mais transferíveis, some sem aviso nos ERPs que guardam cadastros como número.
- **Correção:** converta células int e float numa string de inteiro e tente `zfill(11)` e `zfill(14)`. Deixe as células de texto como estão. Uma chave de acesso de 44 dígitos guardada como float já perdeu precisão e não tem conserto.

### 3.9 'ICM', 'IS' e 'DIF_ALIQ' não são famílias de tributo

- **Onde:** `invoice_mapper.py` `_TAX_FAMILIES` (360–363) e `_TAX_FAMILY_ALIASES` (358). São lidos por `header_tax_families` (461–467), que alimenta `semantic_header_conflict` (625, pela análise de `_header_conflict_parts` em 598–601) e `tax_calculation_scope` (1201–1212).
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

- **Onde:** `invoice_mapper.py` `identifier_match_score` (1061–1078). Para cada tipo de identificador, exige pelo menos 80% de valores válidos dos dois lados: na coluna do cliente e nas linhas de amostra da própria coluna do modelo no model.xlsx.
- **O que acontece:** uma coluna do cliente só ganha evidência de identificador para o tipo que domina as linhas de amostra do modelo.
  - 'Nº ID fiscal 1' tem 209 valores de amostra: 91,4% são CPFs válidos e 8,6% CNPJs válidos. Só uma coluna do cliente feita principalmente de CPFs casa com ela.
  - As amostras de 'Chave de acesso' são 100% chaves de acesso válidas.
  - Nenhuma outra coluna do modelo chega à barra de 80%. A seguinte é 'Nome do Fornecedor do Local de Início', com 1,9% de CPFs válidos.
- **Como ver:** contra 'Nº ID fiscal 1', uma coluna de rascunho com 50 CNPJs válidos tem `identifier_match_score` 0,0, e uma com 50 CPFs válidos tem 0,914.
- **Impacto:** se a coluna de CNPJ/CPF de um cliente ganha essa evidência transferível depende da mistura de pessoas e empresas nas linhas de amostra do modelo, e não dos dados do cliente. `mapping_steps.py` 104–107 promete mais ("qualquer que seja o nome dela"; ver 4.3).
- **Correção:** o model.xlsx fica como está. Trate CPF e CNPJ como um tipo só, "cadastro de contribuinte", ou declare no código o tipo de identificador de cada coluna do modelo, pelo cabeçalho do modelo, e pontue só o lado do cliente.

### 3.11 As colunas 'Tipo de Imposto …' do modelo não recebem evidência de catálogo

- **Onde:** `reference_catalog.py` `expected_catalogs` (37–74), em especial a cláusula `"tipo imposto" in normalized` na linha 60, que o comentário da linha 59 liga a este item.
- **O que acontece:** os catálogos a testar são escolhidos pelo cabeçalho do modelo. As 14 colunas do modelo chamadas 'Tipo de Imposto <tributo>' normalizam para 'tipo de imposto …', então a cláusula não casa com nenhum cabeçalho do modelo, e nenhuma dessas colunas ganha catálogo. O próprio mapeador trata essas colunas como de situação tributária (`header_semantic_role` devolve `tax_status`; ver `test_a_family_name_does_not_smuggle_a_rate_role` em `tests/test_invoice_mapper.py`).
- **Como ver:** `expected_catalogs('Tipo Imposto ICMS')` devolve os três catálogos do ICMS (`cst_icms`, `cst_icms_taxation` e `csosn`), enquanto `expected_catalogs('Tipo de Imposto ICMS')` devolve `()`. Só 15 colunas do modelo recebem algum catálogo.
- **Impacto:** ou a cláusula é código morto, ou 14 colunas perdem um sinal.
- **Correção:** decida qual dos dois.
  - Se essas colunas guardam códigos CST, case pelos tokens `{"tipo", "imposto"}`. Catálogos de CST não aceitam sozinhos (a pontuação fica abaixo de 0,90), então a mudança só acrescentaria evidência para o resolvedor.
  - Se não guardam, apague a cláusula.

### 3.12 A pontuação de catálogo ignora quantos valores estão bem formados

- **Onde:** `reference_catalog.py` `ReferenceCatalogs.analyze`. A pontuação é `distinct_coverage * spec.specificity * sample_factor` (176), e `sample_factor` conta os valores não vazios, e não os bem formados (175).
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

- **Impacto:** o aceite automático está protegido, porque `catalog_evidence_is_distinctive` exige `format_fraction >= CATALOG_MIN_FORMAT_FRACTION` (0,90; `invoice_mapper.py` 1723). A pontuação crua ainda é usada sem checagem em três lugares:
  - `_catalog_score` (1694–1696), para trazer candidatos para o conjunto (`_rank_name_and_profile_candidates`, 2423–2432)
  - a checagem que preserva o melhor cabeçalho (`_preserve_best_name_override`, 2832–2836), onde uma pontuação lixo de 0,90 ou mais impede que o favorito pelo cabeçalho seja restaurado
  - o `catalog_evidence` enviado ao resolvedor
- **Correção:** multiplique a pontuação por `format_fraction`. Como `catalog_evidence` vai no payload do resolvedor, a mudança pode alterar a entrada do modelo: rode o teste dourado e, se ele falhar, trate como em 6.1.

### 3.13 Códigos cClassTrib vencidos continuam valendo

- **Onde:** `reference_catalog.py` `ReferenceCatalogs.from_directory` (142–150) carrega cada CSV com `_load_catalog` (196–207), que guarda só a coluna `code` (206). `valid_from` e `valid_to` são ignorados em todo lugar.
- **Como ver:** `reference_data/ibs_cbs_classification.csv` guarda os códigos cClassTrib do IBS/CBS. Três deles, 220001 a 220003, têm `valid_to` 2026-01-01, e os três ainda carregam como válidos.
- **Correção:** descarte os códigos cujo `valid_to` é anterior à data da execução, ou mantenha-os e informe à parte os casamentos com códigos vencidos.

### 3.14 A atualização dos catálogos de referência é frágil

- **Onde:** `update_reference_data.py`. Não rode sem motivo: ele baixa dos sites do governo e reescreve `reference_data/` (e, com isso, o hash do manifesto que cada relatório registra).
- **O que acontece:**
  - **Nomes de aba** fixos numa publicação: 'CST 2026-06-01 Pub' (`_ibs_cbs_cst_frame`, 157) e 'cClass 2026-06-01 Pub' (`_ibs_cbs_classification_frame`, 171). Uma nova publicação derruba o script ou precisa ser editada à mão.
  - **Rótulos de versão:** os textos de `source_version` do CFOP, do IBGE e das duas tabelas do IBS/CBS são literais em `_official_metadata` (207, 221, 228, 239). Só a versão do NCM é lida do download (`official_frames`, 113), então as outras tabelas rotulariam dados novos com a versão antiga.
  - **Sem filtro de tamanho no CST do IBS/CBS** (`_ibs_cbs_cst_frame`, 155–167), ao contrário do CFOP, da tabela de municípios e do cClassTrib, que passam por `_full_width_unique` (124–126; em 139, 152 e 180). Por isso a linha 2 de `reference_data/ibs_cbs_cst.csv` tem o código em branco. Hoje é inofensivo, porque o carregador descarta códigos em branco (`reference_catalog.py` `_load_catalog`, 206).
  - **Manifesto:** o carregador nunca confere `schema_version` nem `generated_at` do `manifest.json`, e o manifesto não tem checksum próprio. O mapeador grava o sha256 do manifesto em `sha256_manifesto_referencia`, nos metadados (`invoice_mapper.py` `_run`, 3757–3761).
- **Correção:** procure as abas por prefixo, leia as versões dos arquivos de origem, acrescente o filtro de 3 dígitos e valide o esquema do manifesto ao carregar.

### 3.15 Uma chave desconhecida no manifesto termina em traceback cru

- **Onde:** `reference_catalog.py` `from_directory`, linha 147 (`CatalogSpec(name=name, **raw_spec)`). `invoice_mapper.py` `_run` (3698–3705) captura só `OSError`, `ValueError`, `KeyError` e `json.JSONDecodeError`, e `main` (3674–3677) só `ValueError` e `OSError`.
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

- **Onde:** `reference_catalog.py` `_load_catalog`, linhas 198–202, levanta erro quando o checksum de um catálogo não confere com o manifesto; nada em `tests/` exercita esse caminho.
- **Como ver:** `grep -rn checksum tests/` não encontra nada.
- **Correção:** acrescente um teste que adultere um CSV numa cópia em `tmp_path` e espere o `ValueError`.

### 3.17 A expansão de prefixos de palavras de papel casa palavras comuns

- **Onde:** `invoice_mapper.py` `expand_truncated_role_words` (470–482), que usa `_ROLE_PREFIXES`: 21 prefixos de 3 letras ou mais, montados por `_role_prefix_index` (392–406).
- **O que acontece:** qualquer token de cabeçalho igual a um prefixo de uma palavra de papel é expandido: 'out' vira outras, 'red' vira reduzida, 'exe' vira isentas. A docstring da função reconhece o risco.
- **Como ver:** `python -c "from invoice_mapper import header_semantic_role as r; print(r('Valor Out'))"` imprime `other` (o papel de outras).
- **Impacto:** baixo. Um cabeçalho em inglês que use "Out" como palavra ganha o papel de outras.
- **Correção:** expanda prefixos só em cabeçalhos que também nomeiam uma família de tributo.

### 3.18 A CLI e os testes do mapeval são frágeis

- **Nomes de cliente desconhecidos.** `--client` não é validado, então um nome desconhecido termina num `KeyError` cru, com traceback em inglês (`mapeval/cli.py` `main`, 85). Ver também 6.5.
- **Pulo silencioso.** Para os clientes sem gabarito, um relatório que não existe é pulado sem nenhuma palavra (`_report_unkeyed_client`, 113–114). Por exemplo, `python -m mapeval --root "$(mktemp -d)" --client sap_terumo` não imprime nada e sai com 0. Uma exportação do cliente que não existe, ao contrário, gera erro.
- **Pasta de trabalho.** `--root` tem `.` como padrão (67), então o mapeval precisa rodar a partir da raiz do repositório.
- **Marcador de pulo.** Os testes com planilhas só pulam quando `data/keys/de_para_oracle.xlsx` não existe (`tests/test_mapeval.py` 35–38). Se qualquer outra planilha não estiver lá, eles falham em vez de pular.
- **Totais fixados.** Vários testes fixam totais absolutos em `tests/test_mapeval.py`: os denominadores 30/26 em 113, `43` em 126, `(39, 43)` em 142 e `(43, 32)` em 181. Regenerar qualquer relatório com gabarito os quebra por construção, seja a mudança uma melhoria ou não. Atualize-os de propósito, junto com o relatório.
- **Dica repetida.** A linha final "rode com --show-losses para listá-los" sai mesmo quando `--show-losses` já foi passado (`_report_totals`, 191–195).
- **Correção:**
  - Use `choices=list(CLIENTS)` em `--client`; o `PortugueseArgumentParser` já traduz o erro de escolha inválida.
  - Imprima uma linha quando um relatório for pulado.
  - Resolva `--root` em relação ao pacote.
  - Confira todas as planilhas no marcador de pulo.
  - Omita a dica quando `--show-losses` foi passado.

### 3.19 Detalhes da instalação: `.env` e nenhum lockfile

- **O `.env` nunca é carregado.** Uma cópia de trabalho pode ter um `.env` na raiz. Ele está no `.gitignore` e nenhum código o lê (nada importa `dotenv`), então as execuções online precisam de `OPENAI_API_KEY` exportada no shell. Ao compartilhar a pasta como arquivo compactado, e não como clone do git, deixe o `.env` de fora.
- **Nenhum lockfile.** `requirements.txt` só dá faixas de versão. A suíte de testes foi verificada com Python 3.13.
- **Correção:** apague o `.env` ou carregue-o explicitamente, e fixe as versões num arquivo de restrições ou de lock.

### 3.20 A margem da primeira passagem vira com o ruído dos embeddings

- **Onde:** `invoice_mapper.py` `_first_pass`, na regra `name_margin >= config.name_margin_threshold` (linha 2036), aplicada às pontuações de `OpenAIEmbeddingHeaderScorer`.
- **O que acontece:** a API de embeddings da OpenAI não devolve exatamente os mesmos vetores de uma execução para outra. Para o mesmo par de cabeçalhos, a similaridade variou até 0,005 entre as execuções online de 2026-10-09 (na maioria das linhas, menos de 0,0001). Um aceite da primeira passagem que está a um fio da margem mínima de 0,05 pode virar `revisao`, e o contrário também.
- **Como ver:** sap_terumo, m42 `Valor ICMS` ← `ICMS Valor`. No relatório arquivado: `confianca_nome` 0,9832, segunda melhor 0,9331, `margem_nome` 0,0501, `aceito` pela primeira passagem. Em todas as execuções online de 2026-10-09 (com o código antigo, o traduzido e o simplificado): segunda melhor 0,9332, margem 0,0500, abaixo do mínimo. A linha cai para a segunda passagem e termina em `revisao` com 0,8999, o teto do item 2.13. Por isso sap_terumo tem 67 aceitos no relatório arquivado e 65 nas execuções novas (a outra diferença vem do resolvedor de IA). A coluna proposta continua a certa.
- **Impacto:** o número de aceitos não se repete entre execuções online nem fora do resolvedor de IA, e comparar o relatório arquivado com uma execução nova mistura esse ruído com o efeito de uma mudança no código. É por isso que o protocolo pede `BASE` e `CAND` online no mesmo dia ([EVALUATION.md](EVALUATION.md#o-pipeline-online-de-novo-em-2026-10-09)). Nenhuma fabricação nasce disso: a linha só muda entre `aceito` e `revisao`, sempre na mesma coluna.
- **Correção:** guardar em cache, num arquivo local por modelo de embedding, o vetor de cada texto de cabeçalho, para que execuções repetidas usem vetores idênticos; isso também reduz o custo. Arredondar as pontuações antes de comparar com o limiar e a margem é mais simples, mas muda decisões e precisa passar pela validação de [EVALUATION.md](EVALUATION.md#protocolo-para-uma-mudança-proposta).

---

## 4. Documentação e comentários desatualizados no código

### 4.2 `mapping_steps.py` cita funções que não existem

- Linha 62: o `implemented_by="_openai_data_decision"` do resolvedor. A função real é `OpenAIDataResolver.resolve` (`invoice_mapper.py` 1568), chamada por `_ask_resolver`.
- Linha 185: o `implemented_by="build_mapping (o conjunto available)"` da exclusividade. A tradução só mudou a prosa ("(the available set)" virou "(o conjunto available)"); o conjunto é `_MappingRun.available` (`invoice_mapper.py` 1905), de onde `_first_pass` e `map_columns` tiram as colunas aceitas.

Os outros 20 nomes citados em `implemented_by` existem.

**Correção:** corrija as duas strings. Um teste que resolva cada nome de `implemented_by` contra os módulos impediria que o problema voltasse; hoje `tests/test_mapping_steps.py` só confere que o campo não está vazio.

### 4.3 O resumo da etapa de identificador promete mais do que ela faz

- `mapping_steps.py` 104–107, no resumo da etapa de identificador, diz que uma coluna que passa na validação "é aquele identificador, qualquer que seja o nome dela", mas a etapa também depende das amostras do modelo (3.10). O passe de legibilidade não mudou os resumos do registro de etapas, de propósito.
- **Resolvido no passe de legibilidade:** os outros três comentários deste item foram corrigidos onde moram. O comentário do resolvedor em `mapping_steps.py` (57–60) não promete mais o significado de "decidido pelo resolvedor" (o defeito continua em 2.2); o comentário de `GUARDS` (141) diz que só duas travas chegam a `vetado_por` (2.1); e a docstring de `Score.delivered_correct` diz que a conta ignora as linhas confirmadas pelo revisor (2.6).

**Correção:** corrija o resumo junto com 3.10, ou agora, se 3.10 ficar para depois.

---

## 5. Peculiaridades dos dados

### 5.1 Os layouts dos relatórios diferem, e a versão do esquema não diz qual é qual

- **O relatório de sap_procosa é o mais antigo.** `data/reports/mapping_sap_procosa_full.xlsx` foi gerado em 2026-08-04; os outros três, em 2026-08-25. Ele tem só as abas `Mapeamentos` e `Metadados da Execução`: nenhuma `Colunas do Cliente sem Uso`, nenhuma `Fila de Revisão`, nenhuma `prova_aritmetica` e nenhuma `cobertura_distinta_catalogo` (23 colunas, contra 25 dos outros). A migração manteve esse layout de propósito.
- **A versão do esquema não separa os layouts.** Antes da migração, os quatro diziam `mapping_schema_version = 2`, porque `MAPPING_SCHEMA_VERSION` não foi aumentado quando aquelas colunas e abas entraram. Agora os quatro dizem `versao_esquema_mapeamento = 3` e `migrado_da_versao_esquema = 2`. O aumento para 3 (`invoice_mapper.py` 45) marca o esquema em português, não o layout: um relatório novo e o de sap_procosa dizem a mesma versão com layouts diferentes.
- **As colunas de etapas ficam em três lugares diferentes:**
  - Um relatório novo as põe depois de `prova_aritmetica`, na ordem `apoiado_por`, `decidido_por`, `vetado_por` (`MappingResult`, 1851–1855).
  - Os relatórios de datasul, oracle e sap_terumo, preenchidos pelo antigo backfill, põem `decidido_por`, `apoiado_por`, `vetado_por` antes de `prova_aritmetica`, como colunas 17–19.
  - O de sap_procosa as põe depois de `confianca_final_ia` e antes de `parecer_revisor`, como colunas 18–20.
- **Uma linha de banner em branco.** O banner de `data/mapped/sap_procosa_mapped.xlsx` tem a linha `prova_aritmetica` inteiramente em branco, porque o apply acrescenta como vazias as colunas opcionais que o relatório não tem (`OPTIONAL_MAPPING_COLUMNS`, completadas por `_ordered_mapping` em 256–258).
- **Impacto:** leia as colunas dos relatórios pelo nome, nunca pela posição, e não deduza o layout pela versão do esquema. Não regenere sap_procosa só para atualizar o layout (3.3).
- **Correção:** aumente `MAPPING_SCHEMA_VERSION` sempre que colunas ou abas mudarem, e não só quando os nomes mudam.

### 5.2 Contagem de linhas dos gabaritos

- Nos arquivos de-para, a linha 1 tem as anotações "Coluna X", a linha 2 os cabeçalhos do modelo, e os dados começam na linha 3.
- `data/keys/de_para_oracle.xlsx` tem 7.338 linhas de dados. `data/clients/oracle.xlsx` carrega como 7.340 linhas de dados, mas a linha 7340 do Excel está inteiramente em branco, então ele tem 7.339 linhas preenchidas.
- `de_para_datasul.xlsx` tem 6.804 linhas de dados preenchidas, como `datasul_maio.xlsx`, mas a aba declara 7.340 linhas, então o `max_row` do openpyxl exagera. `de_para_procosa.xlsx` declara 209 linhas, das quais 101 estão preenchidas (as duas linhas de cabeçalho e 99 de dados).
- **Impacto:** nenhum na pontuação hoje, porque o mapeval só lê a linha 1. Importa se alguém construir um canal de dados do de-para alinhado linha a linha com a exportação (por exemplo, para derivar de novo as correções de `KEY_CORRECTIONS` a partir dos dados, ideia que um comentário antigo de `mapeval/truth.py` registrava): as linhas de oracle não podem ser alinhadas pela posição.
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

- 206 dos 348 cabeçalhos de `data/clients/datasul_maio.xlsx` têm espaços no fim, por exemplo 'Filial      '. `_header_text` (`invoice_mapper.py` 670–675) os remove na leitura.
- Uma anotação na linha 1 de `de_para_datasul.xlsx` tem um espaço sobrando, que `parse_coluna_annotation` (`mapeval/excel.py` 38–50) tolera.
- Qualquer código novo que leia esses arquivos precisa limpar cabeçalhos e anotações do mesmo jeito.

---

## 6. Introduzidos ou expostos pela tradução

A tradução (commits `2909fa5` a `d97ee1a`) não mudou nenhuma decisão: as execuções offline batem célula a célula com a linha de base em inglês de `2ae4b6d`, o mapeval imprime os mesmos números e o teste dourado da entrada dos modelos continua passando. Os itens abaixo são o que ela deixou em inglês de propósito, o que ela criou e o que ela tornou visível.

### 6.1 As justificativas escritas pela IA continuam em inglês

- **Onde:** `invoice_mapper.py` `OpenAIDataResolver.resolve`. O prompt (`instructions`, 1615–1662) termina em "Keep the reason to one sentence.", e a descrição do campo `DataMappingDecision.reason` (1540) é "Short explanation grounded in names and sample values.". A justificativa do modelo entra na linha por `_proposal_reason`, que devolve a `reason` da decisão quando nenhuma trava a reescreve (3090), ou, numa linha `sem_correspondencia` em que a IA não escolheu nada, por `_second_pass` (2589), e passa intacta por `report_schema`.
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
  1. aumentar `PROMPT_VERSION` (hoje `"2026-08-04.2"`, `invoice_mapper.py` 46), que vai para `versao_prompt` nos metadados;
  2. regenerar o arquivo dourado com `python tests/test_model_input_golden.py --write`, de propósito e só por causa dessa mudança;
  3. medir de novo online os quatro clientes e comparar com o mapeval, porque uma instrução a mais pode mexer em confianças e escolhas. Cada execução online manda dados do cliente para a OpenAI (3.2) e precisa de autorização, e regenerar sap_procosa apaga os pareceres (3.3).

  As 502 justificativas arquivadas continuam em inglês de qualquer jeito: a migração só traduziu as frases escritas pelo próprio mapeador.

### 6.2 Texto de exceção do Python, em inglês, dentro de mensagens em português

- **Onde:**
  - `invoice_mapper.py` `REASON_DATA_ERROR` (201), "A comparação de dados falhou: {exc_type}: {exc}", gravado na justificativa das linhas `erro_dados` (3.1).
  - `invoice_mapper.py` `_error_message` (3645–3654), usado pelo mapeador e pelo apply (`main`, 745–746). Ele acrescenta `exc.strerror` aos erros de sistema e o texto cru do `ValueError` a "Não foi possível gerar o relatório de mapeamento".
  - `apply_invoice_mapping.py` `main` (747–750) imprime `str(exc)` de qualquer `ValueError`, inclusive os do pandas; o comentário ali aponta para este item.
  - A mensagem de catálogos em `_run` (3700–3705) embute `{exc}`.
- **O que acontece:** as mensagens montadas pelo projeto estão em português, mas o pedaço que vem da biblioteca ou do sistema operacional chega em inglês, e às vezes é a mensagem inteira.
- **Como ver** (nenhum destes casos grava arquivo):
  - `python apply_invoice_mapping.py data/clients/oracle.xlsx data/reports/mapping_oracle_full.xlsx --client-sheet Inexistente --output /tmp/x.xlsx` imprime só `Worksheet named 'Inexistente' not found`.
  - O mapeador com `--client-sheet Inexistente` imprime "Não foi possível gerar o relatório de mapeamento: Worksheet named 'Inexistente' not found".
  - Uma pasta no lugar de um arquivo dá "Não foi possível ler ou gravar o arquivo …: Is a directory" no apply (como EXPORTACAO ou RELATORIO).
  - Um `manifest.json` sem a chave `catalogs` dá "Não foi possível carregar os catálogos de referência de …: 'catalogs'. …", em que `'catalogs'` é todo o texto do `KeyError`.
  - Uma linha `erro_dados` gravaria, por exemplo, a mensagem de um `RateLimitError` da biblioteca `openai`, em inglês. A única exceção que o próprio resolvedor levanta (`RuntimeError`, 1667) já está em português.
- **Impacto:** baixo para as decisões, mas o desenvolvedor recebe mensagens meio em inglês justamente nos erros, e a justificativa `erro_dados` mistura as duas línguas numa célula que o revisor lê.
- **Correção:** traduza na fronteira as exceções mais comuns (aba inexistente em `SheetTable.from_excel`, `JSONDecodeError` e `KeyError` do manifesto, os erros da `openai` em `REASON_DATA_ERROR`), guardando o texto original como detalhe técnico no fim da mensagem. Leve `_error_message` para um lugar neutro (por exemplo, `cli_ptbr.py`), em vez de o apply importá-la de `invoice_mapper.py`.

### 6.3 Nomes de exibição ambíguos: cabeçalho literal 'Coluna sem nome N' ou '… (ocorrência N)'

- **Onde:** `report_schema.py` `display_name_to_internal` (559–574), aplicado na leitura a `coluna_cliente`, `coluna_cliente_proposta` e `coluna_cliente_correta` (os campos `display` de `FIELD_KIND`, 433–467).
- **O que acontece:** em memória, o nome de exibição continua em inglês (`Unnamed column N`, `Nome (occurrence N)`), porque `ColumnRef.name` é entrada da IA. Na gravação ele vira `Coluna sem nome N` e `Nome (ocorrência N)`, e na leitura volta. A volta não distingue um cabeçalho do cliente que já seja literalmente `Coluna sem nome 3` ou `Valor ICMS (ocorrência 2)`: lê os dois como a forma interna. A docstring reconhece só o primeiro caso.
- **Como ver:**

  ```bash
  python -c "import report_schema as rs; print(rs.display_name_to_internal('Coluna sem nome 3'), '|', rs.display_name_to_internal('Valor ICMS (ocorrência 2)'))"
  # Unnamed column 3 | Valor ICMS (occurrence 2)
  ```

  Numa exportação de rascunho com esses dois cabeçalhos e um relatório que propõe as duas colunas, o apply recusa o relatório com uma mensagem que se contradiz: "O relatório de mapeamento não confere com a planilha do cliente na coluna 3: 'Coluna sem nome 3' != 'Coluna sem nome 3'". Um revisor que digite qualquer um desses cabeçalhos em `coluna_cliente_correta` recebe "não foi encontrada"; só o número da coluna funciona. No sentido inverso, um cabeçalho literal `Unnamed column 7` aparece nos arquivos como `Coluna sem nome 7`. Nenhuma das quatro exportações de amostra tem cabeçalho assim.
- **Impacto:** baixo hoje, mas um ERP brasileiro pode exportar cabeçalhos que terminam com "(ocorrência N)" ou que já vêm como "Coluna sem nome N" de outra ferramenta, e o relatório fica inaplicável sem que a mensagem explique por quê.
- **Correção:** no apply, compare os dois lados pela mesma ida e volta: passe o `display_name` da exportação por `display_name_to_external` e depois por `display_name_to_internal` antes de comparar com a célula lida, e faça o mesmo ao procurar a coluna de `coluna_cliente_correta`. A comparação já é feita por número de coluna, então a ida e volta não confunde duas colunas diferentes.

### 6.4 O parecer do revisor tem leitura tolerante, mas `situacao` só no valor exato

- **Onde:** `report_schema.py` `normalize_reviewer_status` (675–687) e `internal_value` (638–655); o apply valida o parecer em `_reviewer_status` (263–273), que `_ordered_mapping` chama (259).
- **O que acontece:** o parecer é digitado por pessoas, então a leitura é tolerante de propósito: ignora espaços nas pontas, caixa e acentos, e reconhece `correto`, `incorreto` e `inexistente`. Os pareceres em inglês do formato antigo (`correct`, `incorrect`, `no_mapping_exists`) são recusados como qualquer valor desconhecido, com uma mensagem que mostra o que foi digitado. No banner da planilha mapeada sai sempre a forma canônica (`report_schema.external_value`), mas o relatório guarda o que foi digitado.

  Os valores de máquina seguem outra regra. Só o valor exato em português volta ao interno (3.7), e um valor que não é o português exato passa como está. Como os valores internos são os nomes do formato antigo, em inglês, um `accepted` digitado num relatório em português é lido como aceito sem aviso. O mesmo vale para um id de etapa em inglês e para o sufixo `(occurrence N)` num nome de exibição: eles coincidem com a forma interna e passam calados (o sufixo só consegue achar a mesma coluna que nomeia).
- **Como ver:**

  ```bash
  python -c "import report_schema as rs; print([rs.normalize_reviewer_status(v) for v in ('Correto ', 'correct')], [rs.internal_value('mappings', 'status', v) for v in ('aceito', 'Revisão', 'accepted')])"
  # ['correct', None] ['accepted', 'Revisão', 'accepted']
  ```

- **Impacto:** nada se perde hoje: os 26 pareceres de sap_procosa estão na grafia canônica, e os relatórios arquivados não têm valores em inglês. Mas há duas regras de leitura (o parecer tolerante, com recusa explícita do inglês; os valores de máquina exatos, com o valor interno aceito em silêncio), e quem edita um relatório à mão não tem como saber qual vale para cada coluna. Um parecer fora da lista, como `Correta`, é recusado pelo apply com a lista dos valores aceitos, o que está certo.
- **Correção:** escolha uma regra só para os valores que pessoas editam: a tolerância do parecer também para `situacao` (3.7) e, para os valores de máquina em inglês, recusa explícita em vez de aceitação silenciosa, como já se faz com o parecer. Os testes `test_reviewer_status_is_read_only_in_portuguese` e `test_enum_values_are_converted_only_when_exact` (`tests/test_report_schema.py`) fixam o comportamento atual.

### 6.5 `python -m mapeval --client <desconhecido>` ainda termina em traceback de `KeyError`

- **Onde:** `mapeval/cli.py` `main`, linha 85 (`CLIENTS[name]`); o comentário da linha 84 aponta para este item.
- **O que acontece:** a ajuda, os erros do argparse e toda a saída do mapeval estão em português (`PortugueseArgumentParser`), mas um nome que não está em `CLIENTS` passa pelo parser e termina num traceback do Python com `KeyError: 'foo'`, em inglês. É o mesmo problema de 3.18; a tradução só o deixou mais visível.
- **Como ver:** `python -m mapeval --client foo` (traceback, saída 1).
- **Correção:** `choices=list(CLIENTS)` em `--client`. O `PortugueseArgumentParser` já traduz o erro de escolha inválida ("argumento --client: valor não permitido: 'foo' (valores aceitos: …)", saída 2).

### 6.6 As planilhas mapeadas mudaram de nome de aba e de rótulos

- **Onde:** `apply_invoice_mapping.py` `build_mapped_workbook` e `_write_coverage_sheet`, com os nomes de `report_schema.py` (`SHEETS`, 53–60; `COLUMNS["banner"]`, `COLUMNS["coverage"]` e `COLUMNS["mapped_data"]`).
- **O que acontece:** o entregável agora sai sempre em português. A aba `Mapped Data` virou `Dados Mapeados`, `Coverage` virou `Cobertura`, a célula A1 `__mapping_info__` virou `__info_mapeamento__`, os 21 rótulos do banner (linhas 2 a 22 da coluna A) usam os cabeçalhos em português (`situacao`, `confianca_final`, …), e os valores também mudaram (`aceito`, `semantico`, `entregue`, `aguardando decisão`, `não consta na exportação do cliente`). Os cabeçalhos do modelo na linha 1, os dados a partir da linha 23 do Excel, as colunas ocultas e as células de dados não mudaram. É uma quebra para qualquer leitor a jusante que procure os nomes antigos. Os relatórios de mapeamento também mudaram (`Mappings` virou `Mapeamentos` etc.); ali o apply e o mapeval só leem o esquema novo e recusam um relatório antigo com a indicação de como convertê-lo. Nada no repositório lê as planilhas mapeadas, e elas não registram versão de esquema: o único jeito de um leitor saber o formato é pelos nomes.
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
