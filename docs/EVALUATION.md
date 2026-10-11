# Avaliação

O `mapeval` compara relatórios já gerados com os gabaritos disponíveis. Ele não executa o mapper nem chama a API. A avaliação usa a associação efetiva após a revisão humana e identifica cada campo por `numero_coluna_modelo`; reordenar as linhas do relatório não muda a pontuação.

## Executar

Na raiz do repositório, com as dependências instaladas:

```bash
python -m pytest -q
python -m mapeval
python -m mapeval --client oracle --show-losses
python -m mapeval --strict-key-only
```

`--client` pode ser repetido. Os nomes configurados em `mapeval.cli.CLIENTS` são `datasul`, `oracle`, `sap_procosa` e `sap_terumo`. `--root` permite avaliar outro diretório com a mesma estrutura de arquivos.

## Gabaritos e contrato

Datasul tem 30 associações anotadas e Oracle tem 26. O denominador é **56 campos fornecidos pelos clientes**, não os 227 campos do modelo. Procosa e Terumo não têm gabarito de colunas.

Por padrão, `load_answer_key` aplica quatro correções documentadas em `KEY_CORRECTIONS`, sem regravar as planilhas:

| Cliente | Campo do modelo | Origem anotada | Origem corrigida |
|---|---:|---:|---:|
| Datasul | 184 | 99 | 188 |
| Oracle | 213 | 37 | 36 |
| Oracle | 220 | 39 | 40 |
| Oracle | 221 | 40 | 39 |

`--strict-key-only` desliga essas correções. Duas entradas dos gabaritos continuam incompatíveis com a origem exclusiva:

| Cliente | Origem | Destinos indicados |
|---|---:|---|
| Datasul | 8 | 39 e 147 |
| Oracle | 23 | 100 e 190 |

O evaluator sinaliza esses conflitos para revisão. A aplicação mantém a regra de uma origem por destino confirmado; não usa as duplicidades como autorização para reutilizar colunas. São 56 associações esperadas, mas apenas 54 origens distintas entre os dois clientes.

## Métricas

| Medida | Significado |
|---|---|
| Encontrados | A origem escolhida coincide com o gabarito, mesmo que ainda esteja em revisão. |
| Entregues corretamente | A origem coincide com o gabarito e está aceita automaticamente ou confirmada pelo revisor. |
| Retidos | Associações corretas encontradas, mas ainda não entregues. |
| Coluna errada | A origem proposta difere da anotada para um campo coberto pelo gabarito. Pode ser uma proposta pendente. |
| Perdido | Um campo do gabarito ficou sem origem. |
| Fabricação | Um aceite aponta para uma coluna sem dados. Zero é dado, não célula vazia. |
| Sem veredito | Aceites fora dos campos cobertos pelo gabarito. Não podem ser considerados acertos comprovados. |

O recall entregue é `entregues corretamente / campos presentes no gabarito`. A precisão apresentada como limite inferior usa os acertos comprovados sobre todos os aceites; origens fora do gabarito entram no denominador.

O exit code é 1 quando há fabricação. Um exit code 0 não garante qualidade: propostas erradas, perdas e aceites sem veredito precisam ser examinados nas métricas e nos pares individuais. Uma proposta pendente sobre coluna vazia não é uma fabricação entregue.

A métrica `aceites independentes de cabeçalho` usa `Row.header_independent`: prova aritmética decisiva ou um método de catálogo. Não é uma contagem de `decidido_por` e não cobre exatamente os mesmos casos que `mapping_steps.transfers`. Cite qual definição está usando.

## Resultado dos arquivos atuais

A execução online de **2026-10-11** realizou 771 chamadas ao resolver, todas bem-sucedidas. Os relatórios passaram pela conferência de IDs, origem exclusiva, atribuição do método, formato de confiança, lista completa de origens sem uso e avaliação após inverter as linhas.

| Cliente | Aceites automáticos | Confirmações após revisão | Sem uso após revisão | Entregues corretamente no gabarito |
|---|---:|---:|---:|---:|
| Datasul | 19 | 19 | 329 | 17/30 |
| Oracle | 18 | 18 | 30 | 15/26 |
| SAP Procosa | 45 | 62 | 56 | Sem gabarito |
| SAP Terumo | 64 | 64 | 301 | Sem gabarito |

Datasul e Oracle somam **32/56 entregues corretamente e 43/56 encontrados**, sem aceite incorreto nos campos cobertos pelo gabarito e sem fabricação. Onze associações corretas continuam retidas para revisão. O aceite de Frete do Datasul, origem 74, voltou a ser entregue a 94,14% após a correção do veto de competição já descartada.

Procosa preserva os 26 pareceres e todas as observações anteriores. A conferência final comparou 1.436.882 células copiadas e 6.072.278 células vazias de campos sem origem, sem divergências. Isso comprova a cópia e os contratos das saídas; não comprova a precisão de cada associação nos SAP.

Evidências:

- [`automatic_mapping_verification.json`](../data/validation/automatic_mapping_verification.json): execução automática, chamadas, pares alterados e verificações por arquivo.
- [`workbook_verification.json`](../data/validation/workbook_verification.json): dados efetivamente entregues após a revisão, inventário sem uso e hashes dos arquivos finais.
- [`procosa_review_transfer.json`](../data/reviews/procosa_review_transfer.json): preservação das decisões e observações humanas.

Esses registros descrevem uma execução específica. Se os relatórios forem alterados, seus hashes e resultados deixam de representar os arquivos novos.

## Validar uma mudança

1. Rode a suíte offline e o evaluator antes da alteração. Preserve os relatórios revisados e anote os pares encontrados e entregues, além dos totais.
2. Reproduza o problema com inputs pequenos, scores fixos e resolver simulado. Inclua ordem de campos, disputa de origem, falha de API ou parecer humano quando relevantes.
3. Faça a alteração e rode os testes apropriados. Verifique o golden test se houver mudança nos inputs ou decisões da IA.
4. Quando a mudança afetar o mapeamento online, gere relatórios novos para os quatro arquivos, em caminhos separados, e compare com uma execução de referência sob as mesmas condições. Não use o modo offline para concluir que a segunda passagem manteve qualidade.
5. Examine os pares que mudaram, os aceites sem gabarito e as origens sem uso. Compare também fabricação, erros de API e decisões próximas ao threshold. Uma variação pequena no total pode esconder a troca de um acerto por um erro.
6. Aplique os relatórios e confira as células copiadas, os campos vazios, a cobertura e a lista sem uso. Ao transferir pareceres, compare a decisão efetiva por ID e origem; copiar apenas o texto `correto` pode confirmar uma proposta diferente.
7. Substitua os entregáveis apenas depois de validar o conjunto e preservar a revisão anterior. Atualize os registros de validação junto com os resultados publicados.

Use `scratch/` para saídas temporárias. Não crie regras por nome de cliente. Quando explorar uma heurística com os exemplos disponíveis, reserve um cliente para validação e registre separadamente seu resultado; resultados sobre os mesmos quatro exemplos não provam generalização para outros ERPs.

Para comparar pares em Python:

```python
from mapeval.score import score_report
from mapeval.truth import load_answer_key

key = load_answer_key("data/keys/de_para_oracle.xlsx", "oracle")
a = score_report("data/reports/mapping_oracle_full.xlsx", "data/clients/oracle.xlsx", key)
b = score_report("scratch/mapping_oracle.xlsx", "data/clients/oracle.xlsx", key)
print("Acertos perdidos:", a.correct_pairs - b.correct_pairs)
print("Acertos encontrados:", b.correct_pairs - a.correct_pairs)
print("Entregues corretamente:", a.delivered_correct, b.delivered_correct)
```

`correct_pairs` inclui propostas corretas ainda pendentes. Para comparar a entrega, filtre também `score.accepted`. Mantenha os arquivos de entrada fixos durante a avaliação: a estrutura do cliente é lida com cache no processo.

## Testes e fixtures

A suíte roda offline, com fake clients para a OpenAI. Ela cobre o mapper, aplicação, revisão, catálogos, schema, CLIs e avaliação. O pytest conta cada entrada de um teste parametrizado como um caso; essa quantidade, por si só, não mede a cobertura ou a qualidade do mapeamento.

Mantenha variantes de conversão e normalização nos unit tests e casos representativos nos testes de integração com Excel. Ao consolidar testes, preserve as regras de aceitação, a identificação dos campos, a revisão humana, o método vencedor, a confiança, as colunas sem correspondência e a integridade dos dados. Descrições e mensagens podem mudar sem exigir uma atualização geral dos testes; os erros ainda precisam identificar o problema e os valores envolvidos.

[`tests/fixtures/model_input_golden.json`](../tests/fixtures/model_input_golden.json) fixa prompt, schema, inputs de embeddings, payloads e decisões de cenários controlados. Uma diferença exige análise; não regenere a fixture apenas para fazer o teste passar. Ao alterar o prompt, atualize `PROMPT_VERSION`, revise o diff e valide o resultado online. Mudanças somente na atribuição de uma decisão não exigem inventar uma nova versão de prompt.

[`tests/fixtures/evaluation_baseline.json`](../tests/fixtures/evaluation_baseline.json) conserva somente os campos necessários para reproduzir a baseline histórica do evaluator. Os testes reconstroem relatórios temporários a partir dela. Os entregáveis atuais podem ser regenerados sem mover essa baseline; não apague a fixture como se fosse uma saída antiga sem uso.

Leia também [Dados e formatos](DATA.md) e [Limitações conhecidas](KNOWN_ISSUES.md) antes de interpretar os resultados.
