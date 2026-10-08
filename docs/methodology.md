# Método, fidelidade e reprodutibilidade

S0–S4 usam o mesmo alimentador IEEE123, calculado no pandapower 3.4.x com `runpp_3ph`. O modelo usa dados publicados do alimentador e aproximações de componentes de sequência detalhadas em [data/ieee123/README.md](../data/ieee123/README.md). Não há execução de OpenDSS, substituição pela rede sintética ou otimização conjunta.

1. S0 contém cargas originais PQ/I/Z e capacitores. A curva diária dos YAML é uma hipótese multiplicativa aplicada somente às cargas; capacitores dependem da tensão.
2. S1–S4 têm a mesma demanda EVCS independente e frota, com mesmas chegadas/saídas e energia terminal. S1/S2 carregam a frota; S3/S4 permitem V2G.
3. BESS é adicionado em S2/S4, com reposição da energia inicial. Sua heurística usa a demanda nominal agregada acrescida de DER, antes das perdas e da resposta à tensão.
4. Somar uma única vez perfis assinados de DER por barra/fase/hora. Cargas originais continuam dentro do backend e são exportadas separadamente.
5. Construir uma rede pandapower nova por cenário; resolver o fluxo trifásico e o laço externo de cargas I/Z/capacitores por intervalo. Rejeitar não convergência ou resultados não finitos.
6. Registrar tensões em fases físicas, correntes e perdas de linhas/transformadores, consumo original efetivo, potência na fonte e violações. Todas as perdas do equivalente entram no balanço; carregamento só inclui fases físicas.
7. Exportar CSV/JSON/PNG, SOC, energia da frota, proveniência/hashes dos dados, hashes dos YAML, versão do solver e limitações. Não há aleatoriedade.

## Limitações e próximos marcos
- Validar quantitativamente contra resultados publicados IEEE, medindo erro introduzido por linhas equivalentes e taps médios fixos.
- Para fidelidade integral, desenvolver representação matricial e controles independentes por fase além das capacidades nativas empregadas aqui.
- As sobretensões em carga leve são reportadas; taps não são reajustados e despacho não é reotimizado.
- Limites térmicos, curva diária, localização dos ativos e custos em USD são hipóteses. Não representam tarifas/demanda de Honduras.
- Otimização conjunta, alocação/dimensionamento ótimo, atendimento individual EVCS e degradação veicular continuam pendentes.
- O importador XLSX é preliminar. Preservar equipamentos não implica capacidade de simular automaticamente a rede real.
- SOC e energia atendida não demonstram viabilidade elétrica; a comparação S0–S4 é um estudo com aproximações declaradas.
- `synthetic_network()` e `SyntheticRadialSolver` ficam disponíveis para testes analíticos legados, sem uso na simulação padrão.
