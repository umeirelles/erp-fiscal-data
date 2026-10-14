# Arquitetura

O fluxo principal é **exportação do cliente → relatório de mapeamento → revisão humana → planilha no layout padrão**. O comando `map` propõe associações; o `apply` copia valores; o `evaluate` mede os resultados disponíveis. Nenhum deles precisa encontrar uma origem para todos os campos do modelo.

## Módulos

O único ponto de entrada é [`run.py`](../run.py). Cada comando chama uma função de um módulo de [`mapper/`](../mapper/), listado abaixo na ordem do fluxo:

| Arquivo | Responsabilidade |
|---|---|
| [`run.py`](../run.py) | Comandos `map`, `apply`, `evaluate` e `update-catalogs`. |
| [`mapper/spreadsheet.py`](../mapper/spreadsheet.py) | Leitura do Excel, `SheetTable`, `ColumnRef`, cabeçalhos e números de coluna. |
| [`mapper/schema.py`](../mapper/schema.py) e [`data/target_fields.json`](../data/target_fields.json) | Conversão entre identificadores internos em inglês e o schema dos arquivos em português; identidade e semântica dos 227 campos controlados (`target_field`). |
| [`mapper/headers.py`](../mapper/headers.py) | Normalização, sinônimos, famílias de tributo, papéis, conflitos e os scorers léxico e de embeddings. |
| [`mapper/evidence.py`](../mapper/evidence.py) | Perfis, dígitos verificadores, aritmética fiscal, trios do cliente e leitura das análises de catálogo. |
| [`mapper/resolver.py`](../mapper/resolver.py) | Prompt, payload e chamada ao resolver de IA. |
| [`mapper/decide.py`](../mapper/decide.py) | `map_columns`: passagens, auditorias, regras locais, confiança, aceite e etapas de evidência (`EVIDENCE_STEPS`, `GUARDS`). |
| [`mapper/report.py`](../mapper/report.py) | Colunas sem uso, fila de revisão, gravação do relatório e comando `map`. |
| [`mapper/apply.py`](../mapper/apply.py) | Combinação da proposta automática com o parecer humano, validação do relatório, planilha mapeada e comando `apply`. |
| [`mapper/catalogs.py`](../mapper/catalogs.py) | Carregamento, checksum, normalização e análise dos catálogos; comando `update-catalogs`, que usa a rede. |
| [`mapper/evaluate.py`](../mapper/evaluate.py) | Gabaritos, classificação das associações e comando `evaluate`. |
| [`mapper/cli.py`](../mapper/cli.py) | Ajuda e mensagens do parser em português. |

As dependências seguem o fluxo: `spreadsheet` → `schema` → `catalogs` e `headers` → `evidence` → `resolver` → `decide` → `report`; `apply` e `evaluate` ficam no topo, e `cli` não depende de nenhum. Os caminhos relativos do modelo e das exportações partem do diretório atual; os defaults de `reference_data/` e `data/target_fields.json` são resolvidos a partir da raiz do repositório.

## Identidade e semântica

`SheetTable.from_excel` lê a aba com `dtype=object`. `ColumnRef` guarda ID, índice, nome e ocorrência. Os índices internos começam em 0; os números nos relatórios começam em 1. Cabeçalhos equivalentes após normalização recebem ocorrências distintas. Identifique uma coluna pelo número, sem depender apenas do nome.

`target_fields.json` declara número, nome, papel, famílias de tributo, qualificadores, identificadores permitidos e catálogos. `target_field` pode consultar por número e nome, nome exato ou nome normalizado sem ambiguidade. Os IDs 35 e 195 correspondem a `CFOP_descrição` e `item/material_descrição`. Os campos `Domicílio fiscal` e `Domicílio Fiscal`, nas posições 76 e 154, também mantêm identidades próprias.

As definições controladas têm precedência sobre a inferência. Por exemplo, `Nº ID fiscal 1` aceita CPF e CNPJ independentemente da proporção nas amostras. Cabeçalhos de clientes e modelos alternativos sem definição continuam usando o fallback de inferência. O registro não resolve todas as limitações de vocabulário; elas estão em [Limitações conhecidas](KNOWN_ISSUES.md).

## Decisões do mapper

`map_columns` prepara um `_MappingRun` e chama `_map_run`. CLI e testes usam esse mesmo fluxo; scorer e resolver são injetáveis.

### Preparação

`_start_run` calcula a matriz de scores dos cabeçalhos, descobre trios fiscais do cliente e prepara séries numéricas. No modo online, prepara também os perfis das origens. Os perfis dos alvos e as análises de catálogo são reaproveitados durante a execução. O resolver OpenAI, a lista de colunas sem uso e a fila de revisão recebem os perfis já calculados. Chamadas isoladas às funções de relatório continuam podendo preparar seus próprios perfis.

O scorer online usa embeddings; `LexicalHeaderScorer` compara texto no modo offline. A assinatura semântica expande abreviações e sinônimos e ajuda a reconhecer papéis e famílias de tributo. Essa interpretação pode bloquear pares incompatíveis, como base versus alíquota ou ICMS versus ICMS-ST.

### Primeira passagem

`_first_pass` calcula propostas antes de reservar origens. Um cabeçalho pode decidir quando:

- atinge `name_threshold`, padrão 0,90;
- supera a segunda candidata pela margem configurada, padrão 0,05;
- não apresenta conflito semântico nem correspondência genérica demais;
- a origem contém dados.

`_resolve_competing_claims` compara os aceites que disputam a mesma origem. Com vantagem suficiente, mantém o mais forte; sem ela, encaminha a disputa para revisão. Uma proposta descartada não deixa um veto permanente sobre a origem.

As auditorias de catálogo e de consistência aritmética podem reabrir um aceite e devolver a origem ao conjunto disponível. Os aceites que permanecem alimentam as referências da segunda passagem.

### Segunda passagem

Cada alvo pendente recebe um `_CandidatePool` com candidatas por cabeçalho, perfil, relação aritmética e catálogo. O default seleciona até 10 por critério, com sobreposição entre as listas.

Todas as propostas desta passagem veem as mesmas origens disponíveis e referências confirmadas. A escolha segue estas regras:

1. Um catálogo distintivo ou uma relação aritmética com vantagem suficiente pode resolver a escolha localmente.
2. Caso contrário, `_ask_resolver` envia as candidatas e evidências à IA, que escolhe uma origem ou nenhuma.
3. `_apply_local_overrides` confere relações, amostras em comum e apoio do cabeçalho.
4. `_judge_proposal` calcula a confiança local e aplica as condições de aceite.
5. As propostas concluídas são comparadas quanto à exclusividade antes de confirmar as origens.

A aritmética decisiva é consultada antes da API. Uma falha do resolver deixa a proposta em revisão com `metodo=erro_dados`; o relatório continua sendo gerado.

Após novos aceites, `_propagate_confirmed_relationships` reavalia a aritmética dos campos pendentes em rodadas locais. Todos os alvos de uma rodada usam as mesmas referências. O loop termina quando não há novo aceite e não faz chamadas adicionais à API.

No modo offline, a segunda passagem encaminha a melhor candidata de cabeçalho para revisão. A análise completa acima depende do modo online.

### Evidências e confiança

| Evidência | O que sustenta |
|---|---|
| Cabeçalho | Similaridade de nomes, margem e compatibilidade semântica. |
| Aritmética | Relação base × alíquota = valor, ou quantidade × preço = valor do item, usando referências confirmadas. |
| Catálogo | Códigos válidos, cobertura distinta e especificidade suficiente para distinguir a candidata. |
| Amostras | Valores distintivos compartilhados com o modelo; semelhança genérica de formato não basta. |
| Identificadores | Dígitos verificadores de CPF, CNPJ ou chave NF-e, conforme os tipos permitidos pelo campo. |

Os trios descobertos somente nos dados do cliente também alimentam `prova_aritmetica`. Essa prova serve de apoio e auditoria; não substitui a relação ancorada usada no score de aceite. Dois blocos disjuntos do mesmo tributo podem satisfazer a mesma equação sem distinguir uma coluna.

`_local_final_confidence` usa o score do cabeçalho quando ele é decisivo. Com evidência objetiva suficiente, combina 40% do nome e 60% da melhor evidência, com os pisos definidos no código. Sem apoio suficiente, aplica o teto padrão de 0,8999. A confiança declarada pelo LLM fica em campos separados, preservada antes de qualquer regra local. Zero não é um veto implícito: uma candidata selecionada passa pelos mesmos critérios locais de aceite. Uma resposta sem candidata continua sujeita aos fallbacks existentes; a alteração não transforma ausência de proposta em aceite.

`_accept_gate` exige confiança final suficiente, nome ou evidência objetiva decisivos, ausência de conflito semântico e de contradição aritmética, e dados na origem. `sem_correspondencia` significa falta de proposta sustentada, não uma prova de que o campo inexiste.

### Atribuição do método

`_evidence_attribution` reúne apoio e prova. A primeira passagem atribui o aceite ao cabeçalho; `_acceptance_step` identifica o ramo usado no cálculo da segunda passagem. A prioridade de `EVIDENCE_STEPS` só desempata evidências objetivas de mesmo score. A explicação da concordância entre cabeçalho e aritmética não calcula outra confiança; o score final vem de `_local_final_confidence`.

`decidido_por` fica vazio nas linhas não aceitas. `apoiado_por` pode incluir o resolver e uma prova de auditoria sem que essas etapas sejam decisivas. `metodo` identifica o caminho do pipeline. O apoio aritmético usa os scores e a quantidade de blocos alternativos em memória; a redação de `prova_aritmetica` não é interpretada como regra. `vetado_por` registra alguns bloqueios, mas ainda não é um histórico completo de todas as recusas.

## Revisão compartilhada

`apply.normalize_review` valida o parecer e `resolve_mapping` retorna uma `EffectiveMapping`: origem e situação após a revisão. `correto` confirma a proposta; `incorreto` exige uma origem corrigida; `inexistente` retira a associação. Uma correção preenchida sem o parecer `incorreto` é recusada.

Apply e avaliação consomem essa decisão. Ambos rejeitam duas associações confirmadas para a mesma origem. Propostas pendentes não reservam origens. A cobertura usa a mesma precedência do parecer; uma ausência confirmada não fica pendente.

## Aplicação e avaliação

O apply confere números e nomes dos campos, identidade das origens e hashes das entradas quando presentes. Confere também os conflitos semânticos de aceites automáticos sem parecer humano. Depois monta os dados na ordem do modelo, mantendo vazios os campos sem origem confirmada.

`_banner_rows` registra a origem efetivamente escolhida e os metadados da proposta. Os demais scores continuam históricos: uma correção humana não recalcula a confiança automática. `_write_coverage_sheet` descreve a entrega e `_write_unused_sheet` lista as origens sem confirmação após a revisão. Veja o layout em [Dados e formatos](DATA.md).

O evaluator exige `numero_coluna_modelo` inteiro, positivo, sem repetição, cobrindo 1 a N. Ele pontua pelo ID, não pela posição da linha, e resolve o parecer antes de comparar com o gabarito. A contagem de vencedores usa `decidido_por`. Uma correção humana não herda o método da proposta substituída. Métricas e limites estão em [Avaliação](EVALUATION.md).

## Alterar o projeto

Mantenha IDs internos em inglês e use `schema` para apresentar nomes e valores nos arquivos. Não converta frases de apresentação de volta em regras de decisão. Textos do cliente e do revisor são preservados.

Use testes com scores e resolvers controlados para reproduzir uma mudança. Não ajuste thresholds ou sinônimos para um cliente específico. O golden test cobre prompt, schema e payloads da IA; atualize a fixture somente após examinar a diferença esperada. Alterar o prompt requer também atualizar `PROMPT_VERSION` e validar as decisões online.

Novas colunas ou mudanças de significado no relatório precisam de entradas em `schema`, revisão da versão do schema e verificação dos consumidores. Os defaults completos e a ajuda ficam nas CLIs (`--help`), evitando manter outra lista de parâmetros desatualizada.
