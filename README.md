# cbs-base-fiscal: mapeador de colunas de notas fiscais

Ferramenta interna de uma consultoria tributária. Os clientes mandam planilhas de notas fiscais exportadas do ERP (SAP, Datasul, Oracle...), e cada ERP e cada cliente organiza as colunas de um jeito. A ferramenta liga as colunas da exportação do cliente às colunas do modelo base fixo da consultoria, `data/model.xlsx`. Ela nunca altera um valor. A aplicação seguinte na cadeia lê só o modelo base.

O modelo base é um superconjunto grande de propósito: 227 colunas, que cobrem qualquer campo que algum cliente possa mandar, a começar pelos tributos da reforma, CBS e IBS. Cada cliente preenche só uma parte dele, então uma coluna do modelo vazia muitas vezes é a resposta certa.

## Visão geral do fluxo

```text
data/clients/<exportação>.xlsx
      │  python invoice_mapper.py
      ▼
data/reports/mapping_<cliente>.xlsx    uma linha por coluna do modelo: aceito / revisao / sem_correspondencia
      │  revisão humana: colunas do revisor na aba Mapeamentos
      │  python apply_invoice_mapping.py
      ▼
data/mapped/<cliente>_mapped.xlsx      abas: Dados Mapeados, Cobertura

python -m mapeval                      pontua os relatórios arquivados contra os gabaritos (só leitura)
```

1. **Mapear** (`invoice_mapper.py`, `map_columns`). A primeira passagem aceita uma coluna do cliente só pelo cabeçalho. Isso acontece quando a similaridade dos cabeçalhos (embeddings da OpenAI no modo online, comparação léxica no offline) é de pelo menos 0,90, supera a segunda melhor por uma margem de pelo menos 0,05 e nenhuma trava se opõe. A segunda passagem pega as colunas do modelo que sobraram e pesa os valores: aritmética fiscal (base de cálculo × alíquota = valor do imposto), catálogos oficiais de códigos, sobreposição com os valores de amostra do modelo e dígitos verificadores dos identificadores. Em geral o resolvedor de IA (`gpt-4o-mini`) propõe a coluna; depois o código local recalcula a confiança e aplica o limiar de aceite de 0,90. Cada coluna do modelo termina como `aceito`, `revisao` ou `sem_correspondencia`, com uma `justificativa`. As colunas `apoiado_por` / `decidido_por` / `vetado_por` nomeiam as etapas de evidência e as travas por trás do resultado; `mapping_steps.py` define essas etapas. As confianças são sinais de decisão, não probabilidades calibradas.
2. **Revisar.** O revisor registra o parecer nas linhas da aba `Mapeamentos` do relatório: em `parecer_revisor`, `correto`, `incorreto` (com a coluna certa em `coluna_cliente_correta`) ou `inexistente` (o cliente não tem esse campo). A aba `Fila de Revisão` ordena as propostas que o limiar reteve. A aba `Colunas do Cliente sem Uso` lista as colunas do cliente cujos dados não chegaram a nenhuma coluna do modelo. O relatório arquivado de sap_procosa é anterior a essas duas abas e tem só `Mapeamentos` e `Metadados da Execução`.
3. **Aplicar** (`apply_invoice_mapping.py`). Copia os valores do cliente sem alteração, na ordem das colunas do modelo. Preenche só as colunas aceitas e as confirmadas pelo revisor. A aba `Cobertura` marca cada coluna do modelo como `entregue`, `aguardando decisão` ou `não consta na exportação do cliente`.
4. **Medir** (`mapeval/`). Pontuação, só de leitura, dos relatórios arquivados contra os gabaritos que os próprios clientes escreveram. Sai com código 1 se houver qualquer fabricação.

## Estrutura do repositório

```text
invoice_mapper.py          CLI e algoritmo do mapeador: pontuação de cabeçalhos, evidências, limiar de aceite, gravação do relatório
mapping_steps.py           registro das etapas de evidência e das travas citadas em apoiado_por/decidido_por/vetado_por
reference_catalog.py       carrega reference_data/, confere os checksums e pontua uma coluna contra cada catálogo
apply_invoice_mapping.py   CLI do apply: exportação do cliente + relatório de mapeamento -> planilha mapeada
report_schema.py           fronteira entre o vocabulário interno (em inglês) e o esquema em português dos arquivos:
                           abas, cabeçalhos, chaves de metadados e valores; recusa um relatório no formato antigo, em inglês
cli_ptbr.py                PortugueseArgumentParser: ajuda e erros do argparse em português, usado por todas as CLIs
update_reference_data.py   reconstrói reference_data/ a partir das fontes oficiais (rede)
mapeval/                   avaliação, rodada com `python -m mapeval`
docs/                      os quatro documentos detalhados listados em "Onde ler a seguir"
tests/                     suíte do pytest; roda offline
  test_report_schema.py            vocabulário do esquema em português, ida e volta interno -> português -> interno e o cli_ptbr
  test_model_input_golden.py       garante que a entrada dos modelos de IA não mudou (ver "Idioma")
  fixtures/model_input_golden.json a entrada dos modelos capturada antes da tradução
  test_invoice_mapper.py, test_apply_invoice_mapping.py, test_mapeval.py,
  test_mapping_steps.py, test_reference_catalog.py   um arquivo por módulo
reference_data/            10 catálogos de códigos fiscais (CSV) + manifest.json com os checksums
data/model.xlsx            o modelo base: 227 cabeçalhos e 209 linhas de amostra (imutável por enquanto)
data/clients/              quatro exportações reais de clientes: datasul_maio, oracle, sap_procosa, sap_terumo
data/keys/                 arquivos de-para: os de datasul e oracle são gabaritos de colunas; o de procosa é uma especificação no nível de registro, não um gabarito
data/reports/              relatórios arquivados do mapeador, mapping_<cliente>_full.xlsx, no esquema em português (o de sap_procosa tem 26 pareceres do revisor)
data/mapped/               saídas do apply, <cliente>_mapped.xlsx
requirements.txt           faixas de versão das dependências, sem lockfile
pyproject.toml             só a configuração do pytest
```

Aqui o de-para é uma planilha em que o cliente anotou, nas colunas do modelo que a exportação dele abastece, a letra da coluna correspondente.

## Instalação

Desenvolvido e testado no Python 3.13.1 (pandas 2.3.3, openpyxl 3.1.5, openai 2.30.0).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='...'      # só no modo online
```

Nenhum código carrega o `.env`, então a chave precisa estar exportada no shell. `.env` e `.venv/` estão no `.gitignore`; nunca faça commit de uma chave. Os modelos padrão são `gpt-4o-mini` e `text-embedding-3-large`. Para trocá-los, use `--llm-model` / `--embedding-model` ou as variáveis `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL`.

Rode todos os comandos a partir da raiz do repositório. O padrão de `--model` (`data/model.xlsx`), os caminhos das planilhas no mapeval e a atualização dos dados de referência são resolvidos a partir do diretório atual.

## Comandos do dia a dia

**Relatório de mapeamento, online** (chama a OpenAI, tem custo e manda dados do cliente para fora; veja [Dados confidenciais](#dados-confidenciais)):

```bash
python invoice_mapper.py data/clients/oracle.xlsx --output data/reports/mapping_oracle.xlsx
```

No fim, o mapeador imprime o arquivo gravado e a contagem por situação (`aceito=… revisao=… sem_correspondencia=…`). Sem `--output`, o relatório vai para o diretório atual como `mapping_<nome do arquivo do cliente>.xlsx`. A gravação sobrescreve arquivos existentes sem perguntar e sempre começa com as colunas do revisor em branco. Nunca aponte `--output` para um relatório que tenha pareceres, como `data/reports/mapping_sap_procosa_full.xlsx`. Se os cabeçalhos não estiverem na linha 1, passe `--client-header-row N` / `--model-header-row N` (contadas a partir de 1), e passe as mesmas flags ao apply. `python invoice_mapper.py --help` lista todas as opções.

**Relatório de mapeamento, offline** (sem rede e sem custo):

```bash
python invoice_mapper.py data/clients/oracle.xlsx --offline --output /tmp/mapping_oracle_offline.xlsx
```

O modo offline pontua os cabeçalhos pela comparação léxica e dispensa o resolvedor de IA. A primeira passagem continua aceitando pelo cabeçalho, mas a segunda nunca aceita nem rejeita: toda coluna do modelo que sobra para ela vira `revisao` com o melhor cabeçalho que encontrou. Use-o em testes rápidos e no trabalho com a lógica de cabeçalhos. Trate esses relatórios como rascunho: não faça commit deles nem cite as contagens como número do produto.

**Planilha mapeada:**

```bash
python apply_invoice_mapping.py data/clients/sap_terumo.XLSX data/reports/mapping_sap_terumo_full.xlsx \
  --output data/mapped/sap_terumo_mapped.xlsx
```

O apply imprime o arquivo gravado e `colunas_modelo=227 linhas_cliente=727 colunas_mapeadas=67`. Ele recusa um relatório cujo SHA-256 gravado da exportação ou do modelo (`sha256_cliente` / `sha256_modelo` em `Metadados da Execução`) não confere com os arquivos passados. `--include-review` preenche também as propostas não confirmadas; o resultado é uma planilha de revisão explicitamente insegura, nunca um entregável. O apply só lê relatórios no esquema em português.

**Relatório antigo, em inglês.** Os relatórios gravados antes da tradução estão no formato antigo, em inglês (versão 2 do esquema: aba `Mappings`, cabeçalho `model_column_number`...). O apply e o mapeval os recusam com uma mensagem que diz como convertê-los. Os quatro de `data/reports/` já foram convertidos (`migrado_da_versao_esquema` = 2 em `Metadados da Execução`). O conversor, `migrate_reports.py`, saiu do repositório depois do commit `5fe5092`; para usá-lo, rode-o a partir de uma cópia desse commit:

```bash
git worktree add /tmp/cbs-5fe5092 5fe5092
cd /tmp/cbs-5fe5092
python migrate_reports.py /caminho/do/relatorio.xlsx /caminho/da/exportacao.xlsx --dry-run
python migrate_reports.py /caminho/do/relatorio.xlsx /caminho/da/exportacao.xlsx
cd - && git worktree remove /tmp/cbs-5fe5092
```

Ele reescreve o relatório no próprio arquivo, sem reexecutar o mapeador: nenhuma decisão, confiança ou ordem de linhas muda, e os pareceres e as observações do revisor são preservados. O segundo argumento é a exportação que gerou o relatório; `--dry-run` faz tudo num arquivo temporário. Um relatório tão antigo que não tenha as colunas de etapas (`apoiado_por`, `decidido_por`, `vetado_por`) passa antes, no mesmo commit, pelo `backfill_mapping_steps.py`. O README e o `docs/ARCHITECTURE.md` daquele commit descrevem a ferramenta. O que a conversão fez nos quatro relatórios arquivados está em [DATA.md](docs/DATA.md#migração-dos-relatórios-arquivados).

**Avaliação e testes:**

```bash
python -m mapeval                     # os quatro clientes
python -m mapeval --show-losses       # lista cada linha retida, perdida ou com coluna errada
python -m mapeval --client datasul    # um cliente (a opção pode ser repetida)
python -m pytest -q                   # 343 testes, offline, cerca de 8 s
```

O mapeval pontua os relatórios arquivados `data/reports/mapping_<cliente>_full.xlsx` listados em `CLIENTS`, em `mapeval/cli.py`; ele nunca roda o mapeador. A saída é em português; a linha de totais é `TOTAL COM GABARITO  encontrados 43/56 (76,8%)   ENTREGUES 32/56 (57,1%)   fabricações 0`, seguida de uma linha que conta os 11 mapeamentos corretos encontrados mas não entregues. Alguns testes fixam esses números dos relatórios arquivados. Por isso um relatório `_full` regenerado faz esses testes falharem de propósito, até que alguém atualize os valores fixados conscientemente.

A suíte está descrita, arquivo por arquivo, em [EVALUATION.md](docs/EVALUATION.md#suíte-de-testes).

**Atualização dos dados de referência** (rede; baixa dos portais do governo):

```bash
python update_reference_data.py
```

Regrava `reference_data/` no próprio lugar: CFOP, NCM, códigos de município do IBGE, CST e cClassTrib do IBS/CBS, e as tabelas de CST/CSOSN que ficam fixas no código. Revise o diff e faça commit dos CSV junto com o `manifest.json`. O mapeador se recusa a começar se um catálogo não confere com o checksum do manifesto.

## Idioma

Tudo o que uma pessoa lê está em português: nomes de abas, cabeçalhos, chaves de metadados e valores dos relatórios e das planilhas mapeadas; as frases que o mapeador escreve (`justificativa`, `prova_aritmetica`, `motivo_retencao`...); a saída, a ajuda e os erros das CLIs; comentários, docstrings e estes documentos. Ficam em inglês, de propósito:

- **Identificadores.** Nomes de módulos, funções, variáveis e campos internos; nomes de arquivos e pastas (`data/clients`, `mapping_<cliente>_full.xlsx`, `<cliente>_mapped.xlsx`); flags das CLIs (`--offline`, `--output`); ids e arquivos dos catálogos em `reference_data/`; nomes das funções de teste. Em memória o código usa o vocabulário interno (`status == "accepted"`, etapa `semantic`). `report_schema.py` é a fronteira: traduz ao gravar e volta ao vocabulário interno ao ler.
- **Entrada dos modelos de IA, byte a byte.** O prompt do resolvedor, o prefixo do embedding, o esquema `DataMappingDecision`, o payload enviado à IA, o texto de `semantic_header_conflict` e os nomes `Unnamed column N` e `(occurrence N)` de `ColumnRef`. Mudar qualquer um deles mudaria as pontuações e as decisões online sem que os demais testes offline percebessem. Por isso `tests/test_model_input_golden.py` compara tudo isso com `tests/fixtures/model_input_golden.json`, capturado antes da tradução; nunca regenere esse arquivo para fazer o teste passar. Nos arquivos, essas formas aparecem em português (`Coluna sem nome N`, `(ocorrência N)` e a descrição do conflito de cabeçalho feita por `describe_header_conflict_pt`).
- **Justificativas escritas pela IA.** Como o prompt é em inglês, o resolvedor responde em inglês. As 502 justificativas da IA nos relatórios arquivados ficaram como estavam, e as próximas também virão em inglês.
- **Texto que o código não escreve.** As 26 observações do revisor de sap_procosa estão intactas, byte a byte. Dados, cabeçalhos e valores de amostra do cliente passam sem alteração (por exemplo, `Net value` em sap_terumo).

O apply e o mapeval só leem o esquema em português (versão 3). Um relatório no formato antigo, em inglês (versão 2), é recusado com a indicação de como convertê-lo (ver [Relatório antigo, em inglês](#comandos-do-dia-a-dia)), e um nome do formato antigo num relatório em português é recusado com o que renomear. Como a fronteira funciona está em [ARCHITECTURE.md](docs/ARCHITECTURE.md#língua-e-esquema-dos-arquivos).

**Mudança incompatível para quem lê as planilhas mapeadas:** as abas agora se chamam `Dados Mapeados` e `Cobertura` (antes `Mapped Data` e `Coverage`), a célula A1 de `Dados Mapeados` é `__info_mapeamento__` (antes `__mapping_info__`) e os rótulos e valores do bloco de decisão no topo de `Dados Mapeados` e da `Cobertura` estão em português. Qualquer leitor que procure os nomes antigos precisa ser atualizado. As colunas de dados, com os cabeçalhos do `model.xlsx`, e os valores das células não mudaram. O layout da planilha mapeada está em [DATA.md](docs/DATA.md#planilhas-mapeadas-datamapped), e a correspondência entre os nomes antigos e os novos, em [Nomes legados em inglês](docs/DATA.md#nomes-legados-em-inglês).

## Regras do projeto

Vêm do dono do projeto e valem para qualquer mudança.

1. **O escopo é o algoritmo de correspondência**: interpretação dos cabeçalhos, fusão das evidências e atribuição das colunas. Interface, reaproveitamento do mapeamento de um cliente entre entregas e escala/custo ficam de fora por enquanto.
2. **Os quatro clientes de amostra são exemplos de referência, não o produto.** Em produção vão aparecer ERPs e layouts que ninguém aqui viu. Nenhum código pode ramificar pelo nome de um cliente, e nada é ajustado à mão para esses quatro. Valide cada mudança deixando um cliente de fora. Os gabaritos são teste de regressão, nunca conjunto de treino.
3. **Prefira evidência que se transfere para um ERP nunca visto.** Aritmética fiscal, catálogos oficiais de códigos e dígitos verificadores de CNPJ/CPF e da chave de acesso da NF-e valem mais que vocabulário de cabeçalho aprendido. A métrica de generalização a melhorar é a fração dos aceites apoiados em evidência independente de cabeçalho.
4. **Fabricação é a falha que importa**: preencher uma coluna do modelo com dados que o cliente não tem. Uma `Base CBS` fabricada é muito pior que uma vazia, então a precisão do aceite vale mais que o recall. O limiar de 0,90 fica. O caminho para entregar mais da faixa retida é o parecer do revisor, não um limiar mais baixo.
5. **Cobertura parcial é o desenho.** `sem_correspondencia` muitas vezes é a resposta certa. Nunca use 227 como denominador nem escreva "N de 227 preenchidas". Descreva uma coluna do modelo como *entregue*, *aguardando decisão* ou *não consta na exportação do cliente*; nunca chame de "faltante", "lacuna" ou "incompleta" uma coluna que o cliente não tem.
6. **Cite os ENTREGUES antes dos encontrados** ao descrever o produto. Cite o recall de encontrados só ao discutir a recuperação, e nunca apresente números do modo offline como número do produto.
7. **`data/model.xlsx` é imutável por enquanto.** Não proponha enxugá-lo nem reestruturá-lo.

## Dados confidenciais

O repositório contém dados reais de clientes: CNPJs, CPFs, chaves de acesso de NF-e e valores de notas. Ele é compartilhado com desenvolvedores internos de propósito e é só para uso interno. Mantenha-o em máquinas aprovadas e nunca o envie para um repositório remoto público. O modo online manda todos os cabeçalhos do modelo e do cliente para a API de embeddings da OpenAI. Para cada coluna do modelo da segunda passagem sobre a qual consulta o resolvedor de IA, ele manda também até 8 valores de amostra (`--sample-size`) e estatísticas resumidas (contagens, mínimo, quartis, máximo) de cada coluna candidata do cliente e do modelo, sem anonimização. Confirme que o cliente permite isso antes de uma execução online; caso contrário, use `--offline`.

## Situação atual

*Encontrado* quer dizer que o mapeador escolheu a coluna do cliente que o gabarito indica. *Entregue* quer dizer que essa escolha também passou pelo limiar de aceite, então o apply a preenche.

- Nos dois clientes com gabarito (datasul e oracle, 56 colunas do modelo com resposta no gabarito): **ENTREGUES 32/56 (57,1%)**, encontrados 43/56 (76,8%), 0 fabricações. As 11 colunas encontradas mas retidas são todas linhas `revisao` abaixo do limiar de 0,90 (confiança final de 0,8339 a 0,8999).
- sap_procosa mostra o que o parecer do revisor destrava: 26 pareceres levaram o cliente de 45 colunas aceitas a 62 entregues (15 propostas confirmadas + 2 correções).
- O repositório foi traduzido para o português sem mudar nenhuma decisão. Os quatro relatórios arquivados foram migrados para o esquema em português sem reexecutar o mapeador, e as quatro planilhas mapeadas foram regeneradas a partir deles. O mapeval dá os mesmos números de antes da tradução, e o apply entrega 19, 18, 62 e 67 colunas (datasul, oracle, sap_procosa, sap_terumo), como antes.
- Em 2026-10-09 o pipeline online completo rodou de novo nos quatro clientes, antes e depois da tradução e depois da simplificação, e reproduziu os mesmos números com gabarito. Entre execuções variam só as escolhas do resolvedor de IA e uma coluna na margem da primeira passagem ([detalhes](docs/EVALUATION.md#o-pipeline-online-de-novo-em-2026-10-09)).
- 343 testes passam offline em cerca de 8 s.

## Onde ler a seguir

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): como o mapeador decide (passagens, etapas de evidência, travas, limiar de aceite) e como o apply monta a planilha mapeada.
- [docs/DATA.md](docs/DATA.md): cada planilha (modelo base, exportações dos clientes, gabaritos, abas e colunas do relatório de mapeamento, planilha mapeada) e os catálogos de referência.
- [docs/EVALUATION.md](docs/EVALUATION.md): resultados do mapeval, encontrados e entregues, correções do gabarito e como validar uma mudança.
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md): defeitos conhecidos e pontos em que comentários e código discordam.
