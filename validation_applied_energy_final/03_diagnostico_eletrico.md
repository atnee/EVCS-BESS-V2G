# Diagnóstico elétrico e validação controlada

## O que está demonstrado

O modelo histórico é uma redução MT resolvida em sequência pelo pandapower, não o IEEE 8500 completo. O carregador cria todas as linhas com `create_lines_from_parameters`, incluindo 1.866 monofásicas, três bifásicas e 614 trifásicas. Há 3.714 conexões linha-fase físicas e 7.449 posições trifásicas na representação: a diferença de 3.735 é contagem estrutural, não prova de contribuição individual ao erro.

As matrizes trifásicas são substituídas por médias dos termos próprios e mútuos. Nos casos mono/bifásicos, o código usa acoplamento mútuo zero. Chaves também são representadas no modelo de barras trifásicas. O transformador principal delta–estrela vira YNyn; bancos reguladores passam a ter tap conjunto, sem LDC/atrasos; capacitores permanecem ligados e secundários são removidos. O solver atualiza cargas com expoentes dos modelos 1/2/5, enquanto a transição de carga fora de Vminpu/Vmaxpu do OpenDSS não é reproduzida integralmente.

A [documentação do pandapower](https://pandapower.readthedocs.io/en/v2.11.0/powerflow/ac_3ph.html) descreve a solução em sequência. A [documentação de cargas do EPRI](https://opendss.epri.com/Properties7.html) explica a transição fora de Vminpu/Vmaxpu. As conclusões específicas desta auditoria vêm do código local e dos resultados, não de supor que qualquer solver desequilibrado represente automaticamente uma topologia de fases arbitrária.

## Causa: contribuição confirmada versus hipótese

| Experimento histórico, 18 h | Vmin DSS pu | MAE de tensão versus pandapower pu | Interpretação |
|---|---:|---:|---|
| MT, matrizes originais, delta | 0,840680424 | 0,056017518 | divergência substancial |
| MT, matrizes trifásicas em sequência, delta | 0,876210497 | 0,040005481 | aproximação das matrizes explica parte da diferença neste ponto |
| MT, matrizes originais, wye | 0,838269240 | 0,056874167 | mudar só conexão HV não resolve |
| MT, sequência, wye | 0,874696260 | 0,040631763 | permanece diferença substancial |

Fonte: [ablacoes_historicas.csv](ablacoes_historicas.csv), com caminhos de origem. Esses experimentos mantêm fonte em 1,0 pu, taps fixos, capacitores ligados e limites amplos do modelo PQ; não representam a operação automática nativa nem o dia regulado do artigo.

O teste histórico que substitui apenas as tripletas CAP por um elemento trifásico em cada grupo muda a tensão em no máximo **0,000207928 pu**, MAE **0,000161420 pu**, e mantém uma linha sobrecarregada. Fonte: `../EVCS-BESS-V2G/results/diagnostico_v8_cap_tripletas/resumo.json` (caminho relativo ao Projeto B). Assim, **CAP isoladamente não é causa suficiente** da divergência. Isso não inocenta todos os outros caminhos de fases não físicas. Ainda falta uma ablação global de conectividade de fases com as demais características igualadas; não atribuímos o erro residual inteiro às fases.

## Validação B/C: pares históricos recalculados

[04_validacao_s0.csv](04_validacao_s0.csv) recalcula MAE, RMSE e erro máximo global e por fase a partir das tensões salvas, e erros de corrente das linhas comuns. São dados históricos reaproveitados, não novas execuções pareadas.

| Hora | Modelo DSS | Pares de tensão | Pares linha-fase | RMSE pu | MAE pu | Erro máximo pu |
|---|---|---:|---:|---:|---:|---:|
| 3 | MT equivalente | 3.820 | 3.714 | 0,022700369 | 0,017176229 | 0,042177470 |
| 18 | MT equivalente | 3.820 | 3.714 | 0,072769260 | 0,056017518 | 0,137512736 |

Às 18 h: P da fonte é 11.775,521298 kW no pandapower e 11.931,337724 kW no DSS MT; perdas são 1.002,351292 e 1.158,187237 kW, respectivamente. Há uma versus 21 linhas sobrecarregadas. As métricas de P/Q, perdas e balanço complementar estão em [metricas_pareadas_complementares.csv](metricas_pareadas_complementares.csv).

[05_validacao_fases_e_linhas.csv](05_validacao_fases_e_linhas.csv) preserva a auditoria de terminais físicos das 2.483 linhas. PASS de topologia não significa equivalência do fluxo. [06_validacao_controles.csv](06_validacao_controles.csv) contém razões de tensão montante/jusante com taps nominais, não uma validação de todos os controles dinâmicos.

Três pares do JSON não existem no circuito energizado: `193-48013/C`, `193-51796/B`, `e182745/C`. Correspondem a terminais de chaves monofásicas desabilitadas incorporados aos metadados. A cobertura é explicitamente 3.820/3.823; não omitimos a diferença nem a tratamos como falha genérica de convergência.

Há outra diferença de pós-processamento a preservar no diagnóstico: o pareamento histórico usa `i_*_ka` do pandapower (máximo dos terminais) e corrente do primeiro terminal no DSS. Portanto, erro de corrente inclui essa diferença de definição, além do modelo elétrico. Ampacidades devem ser idênticas antes de comparar percentuais. As perdas do DSS completo incluem secundários ausentes no pandapower, de modo que seu erro relativo não é comparação de fronteiras idênticas.

## Validação A nova: OpenDSS completo, controles automáticos

[native_check.py](native_check.py) reutiliza o backend existente, recompila para cada snapshot, conserva secundários, delta–estrela, controles e fonte nativos. Executou 3 h e 18 h, tanto no conjunto do projeto quanto no instalado. São quatro fluxos novos; não são uma série temporal com memória de taps entre horas.

| Entrada do projeto | Vmin física MT pu | Resíduo P kW | Resíduo Q kvar |
|---|---:|---:|---:|
| 3 h, multiplicador 0,55 | 1,005662253 | 0,015927835 | 0,000608070 |
| 18 h, multiplicador 1,0 | 0,938297777 | 0,099346451 | −0,053954317 |

Balanço calculado: fonte − cargas terminais − capacitores terminais − perdas. Não há DER nesse teste. Fonte positiva significa importação; injeções dos perfis do projeto têm sinal oposto ao consumo. Resíduos pequenos verificam a consistência numérica deste circuito, não aceitabilidade de tensão.

As duas fontes locais produziram erro máximo de tensão de 6,991e−7 pu (pior dos dois horários), erro máximo de corrente de 7,407e−6 A, mesmas classificações de sobrecarga e mesmos taps nos 12 reguladores. Consulte [native_s0/manifest.json](native_s0/manifest.json), [comparação de tensões](native_s0/project_vs_installed.csv) e [correntes/controles](native_s0/current_controls_comparison.csv). O motor é o mesmo DSS C-API; isso verifica consistência entre datasets, **não independência entre solvers** nem todos os pontos publicados IEEE.

O antigo benchmark reportava 0,060761 pu porque filtrava apenas por nome da barra e incluía nós fora da máscara física. A correção isolada seleciona `(barra, fase)`, rejeita NaN e duplicação e registra os pares ausentes. Não foi alterado o código histórico nem recalibrado o circuito para elevar tensões.

## Metas e decisão

Metas históricas de triagem: RMSE ≤0,005 pu, diferença P ≤1%, perdas ≤5%, identidades das linhas sobrecarregadas iguais. São tolerâncias de diagnóstico, não normas. A margem histórica do S4 acima de 0,95 pu é menor que 0,001 pu; portanto mesmo passar RMSE de 0,005 não bastaria para sustentar a classificação operacional. Seria necessário erro máximo abaixo da margem relevante e nenhuma classificação divergente, além de comparação de controles, potência reativa e cobertura completa do conjunto energizado.

Para o balanço novo, adotou-se **1 kW/1 kvar** de resíduo absoluto como verificação numérica de ordem muito inferior à demanda do circuito; todos os quatro casos passam. Nenhum limite de operação foi alterado. ANSI C84.1 e PRODIST são referências comparativas, não normas de Honduras.

**Decisão técnica:** usar o backend OpenDSS completo existente como base da reprodução física, com injeções e controles claramente registrados. Não há evidência de equivalência do pandapower histórico. Não foi implementada uma suposta correção global de fases em pandapower, pois a ablação disponível não demonstra uma correção equivalente. A fidelidade física e a viabilidade operacional continuam sendo perguntas separadas.

## Validação D executada após os snapshots

O [piloto novo com os mesmos perfis históricos](07_impacto_nos_resultados_s0_s4.md) completou 120 snapshots de S0/S1/S4, incluindo S1/S4 com taps S0 travados. A rede é numericamente coerente, mas o S4 não satisfaz os limites operacionais. Os estados comandados de BESS/V2G foram validados separadamente, sem reotimização. Essa conclusão encerra o teste de transferência do caso principal para a rede nativa; não encerra a validação externa nem torna desnecessária a atribuição S2/S3.
