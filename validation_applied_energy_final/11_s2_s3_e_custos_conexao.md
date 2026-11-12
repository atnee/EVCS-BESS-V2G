# S2/S3 na rede completa e custos de conexão

## Resultado

Os cenários **S2 e S3 foram reproduzidos no OpenDSS completo**, tanto para as injeções históricas de BESS única/V2G em uma estação quanto para a alocação distribuída existente. Em cada caso foram avaliados os reguladores automáticos e os taps horários do S0 travados. As simulações são de 24 intervalos de 1 h, fonte IEEE original, secundários explícitos e ampacidades assumidas no estudo. Não se reotimizou nenhum despacho.

O custo incremental de conexão **não pôde ser expresso em USD com os dados do projeto**. Foi possível conferir os custos assumidos de BESS e portas e dimensionar envelopes mínimos de potência por local. Faltam projeto de interface, comprimentos de linhas dedicadas, proteção, medição, obras, condições dos equipamentos existentes e cotações. O IEEE 8500 é alimentador teste sintético, não uma instalação hondurenha real.

## Cenários históricos na rede física

Fonte: [summary.csv](native_s2_s3/summary.csv); perfis de `results/ieee8500/integration/dados/` do projeto original. As 150 unidades V2G do S3 passam novamente nas validações de SOC, saída, reserva e portas; a BESS S2 já havia sido verificada quanto ao SOC final.

| Cenário | Regulação | Novas linhas versus S0 | Maior carga % | Vmin pu | Pico kW |
|---|---|---:|---:|---:|---:|
| S0 | automática | 0 | 111,03 | 0,93707 | 11.985,82 |
| S1 | automática | 41 | 132,13 | 0,91915 | 14.463,78 |
| S2, BESS única | automática | 21 | 115,91 | 0,92259 | 12.968,62 |
| S3, V2G no host histórico | automática | 28 | 131,52 | 0,92836 | 13.545,26 |
| S4, BESS única + V2G histórico | automática | 23 | 120,80 | 0,92357 | 12.909,00 |
| S2, BESS única | taps S0 | 32 | 117,97 | 0,91151 | 13.004,64 |
| S3, V2G no host histórico | taps S0 | 40 | 131,22 | 0,91769 | 13.584,56 |

S2 e S3 reduzem indicadores distintos de S1, mas **nenhum satisfaz os limites absolutos**. A comparação entre S2 e S4 não é monotônica em todos os indicadores, porque a frota carrega de forma diferente quando V2G está ativo e os controles da rede respondem. A contagem de linhas novas exclui as já sobrecarregadas no S0, mas estas continuam reportadas nas séries completas.

## Recursos distribuídos isolados

Fonte: [comparison.csv](distributed_s2_s3/comparison.csv). As injeções são reconstruídas a partir do S1 físico salvo, das três BESS da V3.0.1 e dos 150 perfis V2G atribuídos aos três locais. S2 mantém a recarga não controlada dos veículos e acrescenta apenas as BESS; S3 substitui a recarga original pela operação distribuída do V2G, sem BESS. A identidade `S2 + S3 − S1 = S4` foi verificada por hora, barra e fase, com erro máximo **8,53e−14 kW/kvar**. O S4 reconstruído coincide com as injeções V3.0.1 salvas a **2,84e−14 kW/kvar**. [verification.json](distributed_s2_s3/verification.json) registra isso e a integridade das fontes.

| Política / cenário | Novas linhas versus S0 | Maior carga % | Vmin pu | Pico kW | Severidade térmica pp·h |
|---|---:|---:|---:|---:|---:|
| Nativa — S0 | 0 | 111,03 | 0,93707 | 11.985,82 | 73,42 |
| Nativa — S1 | 41 | 132,13 | 0,91915 | 14.463,78 | 2.210,73 |
| Nativa — S2, três BESS | **0** | 114,57 | 0,92438 | 12.720,56 | 410,00 |
| Nativa — S3, V2G distribuído | 32 | 129,70 | 0,92558 | 13.549,89 | 679,89 |
| Nativa — S4 combinado | **0** | 108,51 | 0,93648 | 12.399,68 | 145,11 |
| Política C — S0 | 0 | 111,93 | 0,93811 | 11.985,26 | 73,76 |
| Política C — S1 | 41 | 133,36 | 0,92090 | 14.466,67 | 2.189,96 |
| Política C — S2 | **0** | 114,34 | 0,92583 | 12.723,30 | 411,78 |
| Política C — S3 | 33 | 130,86 | 0,92707 | 13.553,51 | 667,54 |
| Política C — S4 | **0** | 109,36 | 0,93752 | 12.404,12 | 148,88 |

Com os taps do S0 travados, as linhas novas são **21/49/2** em S2/S3/S4 com política nativa, e **13/49/0** com política C. A política C altera setpoints de reguladores da fase C em −0,5 V nos TPs; o seu S0 é referência separada para taps travados. Capacitores permanecem automáticos. A dependência dos controles é material. Mesmo S2/S4 com zero linhas novas mantêm violações absolutas de linha e tensão; não equivalem a certificação de operação.

A primeira execução do verificador terminou com código 1 por uma falha de **contabilidade do script**, que esperava oito, em vez dos dez replays novos efetivamente concluídos. Nenhum fluxo falhou. O código foi corrigido, e [verify_distributed_s2_s3.py](verify_distributed_s2_s3.py) reabriu os dez conjuntos, conferiu as 24 horas, taps congelados, hashes e aditividade, depois registrou `COMPLETE_VERIFIED_AFTER_BOOKKEEPING_CORRECTION`. O erro e o traceback permanecem no [log original](replay_distributed_s2_s3.log); nenhum resultado físico foi apagado ou ajustado.

## Auditoria de conexão e custos

O [inventário por local](connection_costs/site_requirements.csv) e a [conciliação dos valores](connection_costs/cost_reconciliation.json) usam as configurações YAML, o despacho distribuído salvo, as 150 atribuições de veículos e o catálogo de estações. Os seguintes valores são **hipóteses do modelo**, não cotações:

| Item | Cálculo | Valor USD | O que não demonstra |
|---|---|---:|---|
| BESS histórica | 7.200 kWh × 334 USD/kWh | 2.404.800 | custo final de três unidades menores, interface e conexão |
| Portas V2G históricas | 154 × 5.000 USD/porta | 770.000 | obras/transformadores em três locais; custo líquido de realocar portas |
| BESS distribuída sob a **mesma regra linear** | soma 7.200 kWh × 334 | 2.404.800 | economia ou igualdade de CAPEX real |
| 154 portas distribuídas sob a **mesma regra linear** | 71 + 67 + 16 portas × 5.000 | 770.000 | custo real do upgrade de duas estações públicas AC |

As duas estações AC já presentes em `m1209790` e `l3160107` têm custo hipotético de 60.000 USD cada na alocação pública, incluindo um componente fixo assumido de 20.000 USD por local. Esse componente **já integra o S1** e não deve ser somado novamente como se financiasse a expansão V2G. Há **duas novas barras BESS** no modelo de instalação e **duas estações AC que exigiriam upgrade bidirecional**, mas os arquivos não atribuem custo incremental à interface física. A igualdade de 2.404.800 + 770.000 USD entre as duas disposições resulta apenas de aplicar as mesmas taxas lineares a totais iguais.

### Envelopes para estudo de conexão

| Local | Ativo | Potência ativa de referência | Potência de conexão conjunta conservadora, se houver EVCS público |
|---|---|---:|---:|
| `l2692633` | BESS | 703,02 kW | 703,02 kW |
| `l2711226` | BESS | 1.148,25 kW | 1.148,25 kW |
| `l3029502` | BESS | 348,74 kW | 348,74 kW |
| `m1069504` | 67 V2G + 4 portas reservadas | 781 kW | 781 kW |
| `m1209790` | 67 V2G + AC público de 132 kW | 737 kW V2G | 869 kW |
| `l3160107` | 16 V2G + AC público de 132 kW | 176 kW V2G | 308 kW |

Potência ativa em kW é apenas **limite inferior de kVA quando FP=1**, antes de potência reativa, fator de segurança, transitórios e critérios de seleção dos transformadores/inversores. Os totais conservadores somam placas de carregadores que podem não operar simultaneamente; não são resultados de fluxo nem ratings de equipamentos aprovados. Os locais e distâncias são de um alimentador teste; falta planta de campo para calcular comprimentos de conexão.

**Itens sem preço verificável:** transformador MV/LV e PCS/inversor por local; proteção, seccionamento, sincronismo e medição bidirecional; estudos de curto-circuito/coordenação; linha dedicada, cabos e postes; terrenos, licença e obras; capacidade e eventual substituição de transformadores/ramais já existentes; testes e tarifa/cargo de conexão de uma distribuidora específica. O catálogo não permite somá-los sem projeto e cotação. Registre `incremental_connection_cost_usd = unknown`, não zero.

Como referência institucional hondurenha para **instalações geradoras que se enquadrem na norma**, a [norma técnica da CREE para conexão em média tensão](https://www.cree.gob.hn/norma-tecnica-de-conexion-y-operacion-de-centrales-generadoras-en-redes-de-distribucion-de-media-tension/) descreve instalação de conexão com isolamento, acoplamento, medição, proteção e controle, e um estudo de viabilidade que identifica medidas corretivas e seus custos. A aplicação jurídica exata a BESS/V2G neste estudo hipotético não foi determinada; o documento não fornece cotação para os seis pontos sintéticos. Esta referência **não** transforma a faixa ANSI/PRODIST adotada no estudo em norma de Honduras.

## Decisão para o paper

S2/S3 não são mais pendências de **replay físico da programação salva**. A atribuição ficou clara: as três BESS resolvem a contagem de novas linhas com controles automáticos, o V2G isolado melhora outros indicadores, e o S4 reduz ainda mais a severidade, mas a rede permanece fora dos limites absolutos. Para uma comparação econômica publicável da alternativa distribuída, **o custo de conexão permanece pendente e não pode ser afirmado como igual ao da BESS única**. Solicitar projeto preliminar e cotação para os dois novos pontos BESS e os dois upgrades V2G, ou apresentar análise de sensibilidade parametrizada com custos explicitamente hipotéticos, sem alegar orçamento real verificado.

As séries e scripts de reprodução ficam em [native_s2_s3/](native_s2_s3/) e [distributed_s2_s3/](distributed_s2_s3/); os dados originais e resultados históricos foram preservados. Nenhuma configuração oficial, contrato ou área compartilhada foi alterada.
