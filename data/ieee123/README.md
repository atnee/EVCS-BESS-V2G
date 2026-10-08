# IEEE123 em pandapower

A simulação padrão usa `pandapower.runpp_3ph`. Não importa nem executa OpenDSS. Os arquivos de texto da distribuição pública do caso IEEE123 são utilizados **somente como fonte de dados**, normalizados para JSON por `scripts/build_ieee123_data.py`, sem conversor ou motor DSS.

## Proveniência e reprodução
- Fonte: https://github.com/tshort/OpenDSS/tree/5005c668a72d20775f4c2d060feebb2866ba1d38/Distrib/IEEETestCases/123Bus
- Commit fixado: `5005c668a72d20775f4c2d060feebb2866ba1d38`; obtenção: 08/10/2026.
- Originais preservados em `source/`, com licença EPRI BSD em `source/License.txt`.
- Dados de execução: `src/network/data/ieee123.json`, incluídos no pacote Python.
- SHA256 de cada fonte no campo `provenance.sha256` do JSON. O manifesto de cada execução também registra a proveniência, o hash do conteúdo normalizado, versão do pandapower e hashes dos YAML.
- Reconstrução offline: `python scripts/build_ieee123_data.py`.

O conjunto contém 118 linhas, 8 chaves (6 fechadas, 2 abertas), 91 cargas (3.490 kW / 1.920 kvar nominais), quatro bancos reguladores, um transformador 4,16/0,48 kV de 150 kVA e quatro bancos capacitivos (750 kvar). A representação tem **130 barras computacionais**, preservando terminais adicionais de equipamentos. Os dois terminais artificiais de chaves abertas do arquivo de origem foram substituídos por chaves abertas ligadas às barras 300 e 94. Os nomes originais das barras permanecem como texto.

Comprimentos e matrizes estão originalmente em milhares de pés: 1 unidade = 0,3048 km. As matrizes completas R/X/C e conexões originais são preservadas no JSON; as tabelas escalares `Network.lines` servem apenas como metadados topológicos. O cálculo elétrico usa a rede construída por `create_pandapower_network()`.

## O que o pandapower calcula
- Fluxo AC trifásico assimétrico, com cargas por fase e conexões estrela/delta.
- Cargas PQ, corrente constante e impedância constante. Um laço externo atualiza P/Q a partir da tensão nos terminais; para delta utiliza tensão fase-fase.
- Capacitores por fase com Q proporcional ao quadrado da tensão.
- Transformadores e bancos reguladores equivalentes com taps fixos.
- Tensões, correntes, perdas de linhas e transformadores, importação/exportação e violações.

## Aproximações explícitas
O resultado é uma **aproximação do IEEE123 no pandapower nativo**, não uma reprodução certificada dos resultados IEEE publicados. O solver nativo usa componentes de sequência; consulte https://pandapower.readthedocs.io/en/v3.3.3/powerflow/ac_3ph.html.

| Componente | Representação e limite |
|---|---|
| Linhas trifásicas não transpostas | Médias das diagonais e mútuas: Z1 = Zself − Zmutual, Z0 = Zself + 2 Zmutual. O acoplamento assimétrico original é perdido. A mesma transformação é aplicada a C. |
| Linhas mono/bifásicas | Equivalentes trifásicos desacoplados, com média das impedâncias próprias, sem mútuas. Fases auxiliares existem no cálculo; somente fases físicas são usadas nos indicadores de tensão/carregamento. Isso também introduz capacitâncias auxiliares. |
| Reguladores | Taps de referência 7; −1; (0, −1); (8, 1, 5), convertidos na média por banco, com passo de 0,625%. Sem controle independente de fase ou comutação temporal. |
| Impedância dos reguladores | Reatância equivalente regularizada para `vk_percent=vk0_percent=0.05`, resistência de 0,00001%, para evitar mau condicionamento do fluxo trifásico. Parâmetros de sequência zero/magnetização são hipóteses do equivalente. |
| Transformador Dd0 61s–610 | Equivalente aterrado YNyn, mantendo tensão, potência e impedância de sequência positiva. Não há carga original na barra 610; a resposta de sequência zero de cargas novas nessa barra não reproduz o Dd0. |
| Fonte | Fonte equilibrada 1 pu em 4,16 kV, potência de curto 173.056 MVA, X0/X1=1 e R/X=0,001; aproximação da fonte rígida de referência. |
| Chaves fechadas | Conexões ideais; despreza-se a resistência de 1 micro-ohm usada no arquivo-fonte. |
| Limites térmicos | 400 A por linha, hipótese explícita. Não interpretar violações como validação de ampacidades IEEE. |

O arquivo-fonte da configuração 4 contém uma capacitância mútua positiva; esse valor é preservado sem correção especulativa. Os taps fixos podem produzir sobretensões em carga leve. O programa reporta essas condições; não reajusta taps nem otimiza o despacho para eliminá-las.

## Integração
`load_ieee123()` retorna o contrato comum `Network`; `create_pandapower_network()` disponibiliza as tabelas pandapower. `PandapowerSolver.solve(network, profile)` recebe **somente as injeções adicionais** de EVCS/BESS/V2G. As cargas originais são internas ao backend e não devem ser somadas novamente ao perfil.

`configs/ieee123.yaml` define a curva diária de carga; `configs/bess.yaml` define a conexão BESS e `configs/evcs.yaml` define a estação. A localização inicial na barra 67 e a curva diária são hipóteses, não dados oficiais nem solução ótima. Para reproduzir o snapshot nominal, use multiplicadores 1,0. O BESS usa demanda nominal mais DER para a heurística; as métricas finais usam a potência efetivamente calculada na fonte.

Os testes verificam proveniência, dados, fases, unidades, sinal das injeções, cargas dependentes de tensão, repetibilidade, finitude e balanço ativo. Comparação independente com tensões/perdas publicadas pelo IEEE continua pendente; convergência não prova fidelidade integral.
