# Limitações conhecidas

Esta página mantém os pontos ainda abertos no código atual. Diagnósticos resolvidos, transcrições de execuções antigas e referências a números de linha foram retirados; seu histórico continua no Git. O contrato do produto está no [README](../README.md), e os resultados medidos estão em [Avaliação](EVALUATION.md).

## Evidência e interpretação

- **Os gabaritos são incompletos e contêm dois conflitos de origem.** Datasul e Oracle cobrem 56 campos; Procosa e Terumo não têm gabarito de colunas. Os conflitos de reutilização são sinalizados, sem mudar a regra de origem exclusiva. Um aceite fora do gabarito pode estar errado sem que a avaliação o detecte.
- **Confiança não é probabilidade.** O teto de 89,99% significa que faltou evidência para aceitar. Não indica uma chance de acerto medida. Uma margem de nome negativa também pode coexistir com um aceite sustentado pelos dados.
- **Execuções online podem variar.** Mudanças nos scores e nas escolhas do resolver afetam pares próximos dos thresholds. Compare as origens e os motivos alterados, não apenas totais de aceites. Os testes com scores fixos isolam a regra local dessa variação.
- **O reconhecimento fiscal é parcial.** O vocabulário de `header_tax_families` não reconhece todos os nomes de ERP. `ICM`, `IS` e `DIF_ALIQ` ainda apresentam lacunas de família ou escopo. O registro dos campos controlados explicita a interpretação existente, mas não torna a inferência do cliente completa.
- **Prefixos podem atribuir papéis indevidos.** As expansões em `semantic_header_signature` podem interpretar palavras comuns como abreviações fiscais. Novos aliases precisam de exemplos positivos e negativos que não dependam de um cliente específico.
- **Identificadores numéricos podem perder evidência.** `_digits_only` e os validadores de CPF/CNPJ não recuperam zeros à esquerda de células numéricas; floats podem acrescentar dígitos vindos de `.0`. Uma chave NF-e convertida em número também pode perder precisão na origem. A declaração dos tipos permitidos pelo alvo não corrige esse problema de representação.
- **Catálogo não valida tratamento fiscal.** A aplicação verifica correspondência de códigos; não conclui que o código é adequado à operação nem que o tratamento tributário está correto.

## Relatórios e revisão

- **Regenerar apaga os pareceres no arquivo sobrescrito.** `write_results` começa com as colunas do revisor em branco. A transferência dos 26 pareceres de Procosa foi conferida para a execução atual, mas não existe uma rotina geral de migração de revisões. Gere novas execuções em `scratch/` e preserve a decisão efetiva de cada campo antes de publicar.
- **As abas auxiliares do relatório são snapshots.** `Fila de Revisão` e `Colunas do Cliente sem Uso` não se atualizam quando alguém edita `Mapeamentos`. O apply gera uma lista sem uso atualizada no entregável. Propostas pendentes podem compartilhar origens; isso não autoriza dois aceites para a mesma origem.
- **O banner mistura origem efetiva e auditoria automática.** A origem reflete a revisão, enquanto scores, método e situação automática continuam descrevendo a proposta original. Use `Cobertura` para a situação efetiva. Uma correção humana não ganha automaticamente 100% de confiança.
- **A prévia pode parecer uma entrega.** Com `--include-review`, a cobertura chama de `entregue` uma coluna copiada mesmo que sua proposta ainda esteja pendente. A exclusividade é exigida apenas para confirmações. A lista sem uso continua indicando as origens sem confirmação; trate essa saída como conferência.
- **O registro de vetos é parcial.** `vetado_por` registra coluna vazia, conflito semântico e disputa de origem. Cabeçalho genérico, contradição aritmética e reabertura por auditoria não têm um histórico completo nesse campo.
- **A cobertura distinta não é exportada em todos os caminhos.** `_judge_proposal` ainda deixa `catalog_distinct_coverage` em branco, embora use essa informação no cálculo local. A cobertura total não substitui a cobertura de códigos distintos.
- **Relatórios antigos podem não registrar o vencedor.** A avaliação não tenta reconstruí-lo pela justificativa ou pelo método genérico. Casos sem `decidido_por` ficam como `nao_registrado`; correções humanas também não herdam o vencedor da proposta anterior.
- **Texto livre pode estar em inglês.** O prompt da IA está em inglês e o resolver pode escrever a justificativa nessa língua. Observações humanas são preservadas; mensagens de exceptions também podem vir das bibliotecas em inglês. Alterar o idioma pedido à IA muda o prompt e requer validação própria.

## Leitura e validação

- **Metadados ausentes reduzem a proteção.** `_validate_input_hashes` compara SHA-256 do cliente e modelo quando as chaves existem. Se faltarem, essa comparação é pulada. O apply também não confere os parâmetros de aba e linha do cabeçalho contra os metadados. Cabeçalhos e números ainda são validados, mas não substituem a identidade completa dos inputs.
- **Edite somente os campos do revisor.** O parecer tolera caixa, acento e espaços; valores automáticos como `situacao` usam conversão exata. Alterar `aceito` para `Aceito ` pode mudar a interpretação da linha. Alguns valores internos em inglês também passam pela leitura; isso não significa suporte ao schema antigo.
- **Nomes de exibição têm casos ambíguos.** Um cabeçalho literal como `Coluna sem nome 3` ou com sufixo `(ocorrência 2)` pode ser confundido com a apresentação de uma coluna sem nome ou repetida. Um cabeçalho numérico em `coluna_cliente_correta` é interpretado como número de coluna. Prefira números de origem nas correções.
- **O schema não descreve todos os detalhes do layout.** O relatório informa a versão 3, mas a planilha mapeada não tem uma versão própria. Consumidores devem usar os nomes atuais e `FIRST_DATA_EXCEL_ROW`. CSV perde metadados e as três abas auxiliares do relatório.

## Catálogos

- **O score bruto pode exagerar o apoio.** `ReferenceCatalogs.analyze` calcula cobertura distinta entre os valores que conseguiu formatar. Uma coluna com muito texto e poucos códigos pode ter score alto. A regra de catálogo distintivo exige também boa `format_fraction`, protegendo o aceite por essa evidência; seleção de candidatas, payload e alguns overrides ainda consultam o score bruto.
- **Vigência não entra no matching.** `_load_catalog` carrega os códigos sem filtrar `valid_from` ou `valid_to`. O fato de um código constar no CSV não demonstra validade na data do documento.
- **O updater depende do formato das publicações.** `python run.py update-catalogs` usa nomes fixos de abas e alguns rótulos fixos de versão. A extração de CST IBS/CBS não aplica o mesmo filtro de largura das demais tabelas. Uma publicação nova precisa de conferência antes de substituir os CSVs.
- **A validação do manifesto é limitada.** Os checksums dos CSVs são conferidos, mas `schema_version` e `generated_at` não determinam sua aceitação. Chaves desconhecidas passadas para `CatalogSpec` podem provocar `TypeError` sem a mensagem amigável da CLI.

## Operação e testes

- **Falhas do resolver não tornam o exit code um indicador de saúde da API.** A exceção vira `revisao`, com `metodo=erro_dados`, e o mapper pode terminar com 0. Examine essas linhas e a contagem de falhas da execução. Decisões aritméticas locais resolvidas antes da API não dependem dessa chamada.
- **O modo offline cobre outro percurso.** Usa comparação léxica e deixa a segunda passagem para revisão. Não mede a qualidade do resolver nem de todo o processo online.
- **A CLI de avaliação tem arestas.** Um `--client` desconhecido termina em `KeyError`. A ausência de um relatório de cliente sem gabarito é ignorada sem aviso. A dica de `--show-losses` pode aparecer mesmo quando a opção já foi usada. Somente fabricações alteram o exit code de avaliação.
- **O cache de avaliação pressupõe inputs fixos.** `_client_structure` mantém a estrutura da exportação no processo. Reinicie a avaliação se substituir o arquivo no mesmo caminho.
- **As dependências não têm lockfile.** `requirements.txt` fixa faixas de versão. Registre o ambiente ao comparar resultados e use uma virtualenv. O `.env` não é carregado automaticamente.

As correções de identidade, precedência da revisão, competição, propagação aritmética, atribuição do método e formatação de confiança já estão no código e nos testes. Os arquivos atuais têm as quatro abas do relatório e as três abas do entregável; Procosa conserva a revisão humana. Esses itens não são pendências desta lista.
