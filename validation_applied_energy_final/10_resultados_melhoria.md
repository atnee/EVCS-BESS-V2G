# Experimentos de melhoria: despacho, localização e participação V2G

## Decisão prática

**A melhor base encontrada é a solução distribuída já existente, com três BESS e os mesmos 150 veículos V2G. Não há justificativa, nos testes realizados, para aumentar a participação para 200 ou 250 veículos nos locais históricos.** A recomendação é de continuidade de pesquisa, não de instalação: custos civis, conexões e infraestrutura distribuída ainda precisam ser projetados.

Nenhum candidato demonstrou operação integralmente dentro de 0,95–1,05 pu e 100%. O resultado é melhoria parcial, com violações remanescentes da rede e dependência dos controles.

## Desenho do experimento

Foram executados seis candidatos novos, sem criar um algoritmo de otimização: reutilizou-se `rebuild.joint_optimization.solve_joint`, com sensibilidades obtidas no OpenDSS, estados de veículos/SOC e despacho BESS validados pelas rotinas existentes. O YAML [improvement_settings.yaml](improvement_settings.yaml) fixa parâmetros, hipóteses e limites computacionais antes das execuções.

- Dois despachos com BESS e V2G nos locais históricos: janela 17–20 h e janela 16–21 h. O instante é início do intervalo; a segunda inclui suporte até o intervalo das 20 h.
- Dois testes de localização, mantendo 150 VEs e uma BESS de 2.200 kW/7.200 kWh, em candidatos da triagem já existente. Isso testa mudança de local, não divisão da BESS.
- Dois testes de quantidade, 200/250 VEs no local histórico, com 2.800/2.750 veículos no modelo público, preservando a soma de 3.000. Foram regeneradas sessões e frotas com o modelo existente e a mesma semente. Não são amostras pareadas dos mesmos indivíduos; também mudam a composição da demanda pública e a disponibilidade da frota. Não extrapolar causalidade universal de quantidade.

Os seis MILPs terminaram com `solver_status=0`, gap reportado zero. Isso certifica o problema linear/misto construído sob suas tolerâncias, **não o ótimo AC nem viabilidade elétrica**. Todos foram submetidos a replay automático de 24 h. A análise com taps horários do S0 foi executada separadamente, mantendo controles dos capacitores ativos. Os cinco primeiros completaram essa análise; o caso 250 falhou no controle.

A regra de aceitação rejeita compensar agravamento de severidade de tensão por melhora térmica; verifica também extremos globais, com tolerâncias numéricas registradas. Os limites absolutos usados para reportar violações não mudaram. Nenhum dos seis candidatos novos passou integralmente pela regra comparativa contra o S4 histórico reproduzido no OpenDSS.

## Resultados com controles automáticos

Todos os números abaixo são saídas do OpenDSS completo com a mesma fonte nativa e ampacidades do estudo. A referência é o replay das injeções históricas do artigo, não o antigo resultado pandapower.

| Alternativa | V2G | Novas linhas vs S0 | Máximo % | Vmin pu | Severidade térmica pp·h |
|---|---:|---:|---:|---:|---:|
| Referência histórica no OpenDSS | 150 | 23 | 120,798 | 0,923569 | 311,801 |
| Redespacho, janela original | 150 | 21 | 123,772 | 0,928998 | 301,027 |
| Redespacho, janela ampliada | 150 | 21 | 122,405 | 0,928800 | 303,764 |
| V2G em l2692633, BESS original | 150 | 23 | 114,607 | 0,923377 | 379,211 |
| BESS em l2711226 e V2G em l2692633 | 150 | 14 | 111,698 | 0,922561 | 270,544 |
| Participação maior, locais históricos | 200 | 21 | 127,541 | 0,930160 | 372,753 |
| Participação maior, locais históricos | 250 | 21 | 132,660 | 0,930591 | 412,590 |
| Três BESS/V2G distribuído existente, controle nativo | 150 | **0** | **108,506** | **0,936481** | **145,107** |
| Mesmo distribuído, política de controle salva | 150 | **0** | **109,358** | **0,937517** | **148,882** |

Fonte: [comparison_improvements_all.csv](comparison_improvements_all.csv), com visões identificadas. Severidades somam excessos por linha-fase e hora, `dt_h=1`. Não confundir máximos instantâneos, contagem de linhas novas, severidade térmica e agravamentos relativos.

### Interpretação

O despacho temporal melhora a subtensão, mas não elimina a restrição da pior linha. Mover uma estação pode aliviar um ramal e retirar suporte de outro. Aumento da participação no mesmo ponto não resolveu isso nos casos testados e aumentou o pior carregamento. Assim, ampliar frota antes de estabelecer localização e coordenação seria prematuro.

O caso distribuído já existente reduz a severidade térmica em cerca de **53,46%** com controles nativos, comparado ao replay histórico. O pico passa de 12.909,004 kW para 12.399,681 kW. As perdas sobem de 21.566,758 para 21.628,703 kWh/dia; portanto não é melhoria de todos os indicadores.

A política salva reduz em **0,5 V na base dos TPs** os setpoints dos quatro reguladores da fase C; não é redução de 0,5 pu. Com ela, a severidade de sobretensão cai de 2,637125 para 1,088725 pu·h em relação ao distribuído com controles nativos, mas a térmica cresce de 145,107 para 148,882 pp·h. É um compromisso explícito, não aprovação automática da política.

## Configuração distribuída confirmada

Não foi descoberta outra versão de otimização: foi reaproveitado e reproduzido o candidato V3.0.1 existente.

| BESS | kW | kWh |
|---|---:|---:|
| l2692633 | 703,017 | 1.070,118 |
| l2711226 | 1.148,248 | 4.983,803 |
| l3029502 | 348,735 | 1.146,079 |
| Total | 2.200 | 7.200 |

| Ponto V2G | Veículos atribuídos |
|---|---:|
| m1209790 | 67 |
| m1069504 | 67 |
| l3160107 | 16 |
| Total | 150 |

[resource_validation.json](alternative_controls/resource_validation.json) confirma limites/SOC e preservação da frota, e reconstrói as injeções dos componentes com erro máximo de aproximadamente 2,84e−14 kW. A validação existente de portas é agregada; não certifica projeto físico das estações distribuídas. Não se pode alegar custo total igual só porque a potência/energia BESS total e a frota são iguais.

[distributed_reproduction.json](alternative_controls/distributed_reproduction.json) confirma que o replay da política salva reproduziu os dados V3.0.1: erro máximo de carregamento aproximadamente 1,42e−14 ponto percentual, tensão 2,22e−16 pu e fonte 1,82e−12 kW. Isso verifica reprodutibilidade neste motor; não é validação externa.

## Dependência dos reguladores

| Distribuído | Regulação | Novas linhas | Máximo % | Vmin pu |
|---|---|---:|---:|---:|
| Controles nativos | automática | 0 | 108,506 | 0,936481 |
| Controles nativos | taps S0 travados | 2 | 108,201 | 0,936689 |
| Política salva | automática | 0 | 109,358 | 0,937517 |
| Política salva | taps do S0 da própria política travados | 0 | 108,225 | 0,932589 |

No último caso, tanto os taps quanto a referência S0 correspondem à política modificada. A fonte e a topologia não mudaram; capacitores continuam automáticos. O ganho operacional depende dos controles. Mesmo com zero linhas novas, restam sobrecargas preexistentes e violações de tensão.

## Falhas registradas, sem ocultação

1. A primeira preparação da janela ampliada acionou `Price policy: the V2G remuneration inside the discharge window must exceed every value outside it`. Era uma incompatibilidade de preparação: o cenário S1 sem controle estava recebendo a janela experimental. Corrigiu-se apenas a orquestração: S1 conserva seu contrato original; o otimizador recebe a janela alternativa. Os erros e a primeira execução foram preservados; os casos concluídos não foram refeitos.
2. O caso 250 com taps S0 travados apresentou `#485 Warning Max Control Iterations Exceeded` à hora zero. O diagnóstico repetiu a falha com eventlog ativado e confirmou alternância de abertura/fechamento do `capbank0c`, incluindo `HIGH VOLTAGE OVERRIDE`, até 100 iterações. Ver [diagnostic.json](improvement_frozen/participation_250_control_log/diagnostic.json). Não foram aumentados limites ou descartados avisos para obter aprovação. O caso está incompleto nessa visão.

## O que melhorar agora

Na configuração distribuída com a política salva, os alvos remanescentes são concretos: `ln6351510-1`, fase C, chega a **109,358% às 15 h**; `ln6290230-1`, fase A, chega a **107,162% às 18 h**. A tensão mínima de **0,937517 pu** ocorre em `regxfmr_190-8581/A` às 18 h, enquanto a máxima de **1,057934 pu** ocorre perto da subestação, na fase A, às 6 h. Portanto, estender apenas a descarga noturna do V2G não cobre o pior evento térmico das 15 h, e elevar uniformemente a tensão pode agravar a sobretensão matinal.

1. **Usar a configuração distribuída como candidata principal**, preservando a BESS única como comparação histórica. Ainda é preciso justificar instalações e custos das três unidades/estações.
2. **Tratar as violações residuais e a coordenação dos controles**, em vez de acrescentar veículos no ponto histórico. Verificar estados de capacitores, controle por fase e alternativas físicas de suporte de tensão. Potência reativa requer capacidades kVA e curvas do conversor explícitas; não foi presumida neste teste.
3. **Manter 150 VEs na próxima comparação**. Os casos 200/250 não justificam o custo adicional no arranjo testado. Uma conclusão geral sobre tamanho exige amostras pareadas/múltiplas sementes e controle da energia/serviço prestado.
4. **Reproduzir S2 e S3 da configuração escolhida**, além de S4, para atribuir complementaridade e interações de controle. Depois executar sensibilidades relevantes e completar a referência externa.

Os validadores energéticos verificam o despacho comandado. O backend mantém as leis nativas de carga/geração do OpenDSS fora de suas faixas de tensão. Antes de uma conclusão sobre energia fisicamente entregue ao veículo em condição violada, conferir também a potência terminal efetiva de cada DER e sua consistência com o SOC comandado; não basta validar só as matrizes de potência solicitadas.

## Código, testes e preservação

- [improve.py](improve.py): seis candidatos, sem modificar algoritmos/contratos oficiais.
- [check_alternatives.py](check_alternatives.py): separação de controle nativo/política salva para o distribuído.
- [freeze_candidates.py](freeze_candidates.py): visão de taps S0; saídas completas para cinco candidatos, falha explícita para 250.
- [validate_distributed.py](validate_distributed.py): rastreamento dos componentes até as injeções, sem dupla contagem.
- [diagnose_controls.py](diagnose_controls.py): reprodução da falha com registro de eventos.
- [improvement_tests.log](improvement_tests.log): **11 testes passaram**, incluindo três novos contra aceitação indevida e testes existentes do otimizador conjunto.

As configurações oficiais, os contratos, o snapshot do artigo e os resultados históricos não foram alterados. Novos parâmetros são hipóteses registradas no YAML/copias experimentais. Não houve commit ou push. Todos os resultados acima permanecem condicionados ao modelo OpenDSS e às hipóteses de ampacidade, fonte, mobilidade e infraestrutura.
