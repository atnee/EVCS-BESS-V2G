# Validação — IEEE 8500

O que foi verificado no código e nos resultados, e o que **ainda não** foi. Atualizado em 11/10/2026, branch `feature/ieee8500`.

## Ambiente

| Item | Versão |
|---|---|
| Sistema | Windows 11, AMD Ryzen 5 3600 (6 núcleos), 16 GB de RAM; a GPU não é usada |
| Python | 3.13.5 |
| Bibliotecas | pandapower 3.4.0, numpy 2.3.3, pandas 2.3.3, scipy 1.16.1 (HiGHS), networkx 3.6.1, matplotlib 3.10.7, PyYAML 6.0.3, pytest 9.1.1 |

As versões estão fixadas em `requirements-tested.txt`. O GitHub Actions (`.github/workflows/`) roda a suíte em Python 3.12 e 3.13.

## Testes automatizados

`python -m pytest -q`: **75 testes aprovados (mais 22 subtestes) em ~11 min**. A maior parte do tempo é o teste da integração S0–S4.

| Arquivo | O que verifica |
|---|---|
| `test_ieee8500.py` | Hash SHA-256 dos dados de origem, contagens (barras, linhas, cargas, potência nominal), rede radial e conectada, fluxo nominal, reguladores baixando o tap em carga leve, seleção de candidatas |
| `test_s0.py` | Topologia, inventário elétrico e tabelas do caso base |
| `test_planning.py` | Energia diária por classe de veículo e serviço, limites de potência dos carros, reprodutibilidade, conservação de energia na fila, fila com poucos carregadores, sessões que passam da meia-noite ocupando o carregador, MILP de cobertura total (custo mínimo, capacidade, pontos inalcançáveis), eletropostos monofásicos, capacidade de hospedagem |
| `test_impact.py` | Variação de tensão S1 − S0, perdas integradas no tempo (`dt_h`), PVUR e faixas do PRODIST |
| `test_zoning.py` | Classe do consumidor pelo transformador de serviço, regra das regiões, transformador para todas as 1.177 cargas |
| `test_evcs.py`, `test_bess.py`, `test_v2g.py`, `test_strategies.py` | Limites, SOC, reserva de mobilidade, portas e contrato de cada estratégia registrada |
| `test_integration.py` | S0–S4 no IEEE 8500 com resultados finitos e **balanço ativo fechando em 0,001 kW** em todos os passos e cenários |
| `test_module_independence.py` | Cada módulo roda sozinho, a integração só usa os contratos públicos, pacotes de saída detectam adulteração |
| `test_network.py`, `test_xlsx.py` | Validação de fases dos perfis, recusa de redes de teste pelo modelo pandapower, importador XLSX |

## Verificações feitas nos estudos

- **Paralelismo:** a execução em vários processos dá resultados idênticos à sequencial, na varredura de capacidade e no S1.
- **Frota mista:** o modelo por classes de veículo, configurado com uma única classe, reproduziu exatamente as sessões da rodada anterior (753 sessões, mesma energia).
- **Correção da fila periódica:** a simulação com dia de aquecimento não mudou os resultados da rodada anterior, que quase não tinha recarga noturna (atendimento 97,7 % e pico de 1.746,7 kW iguais).
- **Alocação:** o MILP fecha o ótimo (gap 0) no nó raiz em 1,4 s com 3.000 veículos (2.515 variáveis binárias).
- **Notebook 01 do EVCS:** executado de ponta a ponta sem erros com os resultados atuais.
- **Perdas:** a energia de perdas usa a duração do passo (`dt_h`); a versão anterior somava as potências de 15 min como se fossem horas, o que dava 4 vezes mais. Está corrigida e coberta por teste.

## O que ainda não foi validado

- **Comparação com o OpenDSS:** tensões e perdas do IEEE 8500 no caso de referência (sem os dois ajustes do estudo) ainda não foram comparadas com a solução publicada. Convergência e balanço de potência não provam fidelidade.
- **Aproximação por componentes de sequência:** o erro que ela introduz nas linhas desequilibradas não foi quantificado.
- **Hipóteses sem dados locais:** frota, curva de carga, custos e as classes de consumidor não vêm de dados de Honduras.
- **Robustez estatística:** cada estudo usa uma semente; falta repetir com várias sementes e reportar intervalos de confiança.

O alimentador IEEE 123 e a demonstração sintética de 4 barras, da primeira fase, foram retirados do projeto; ficam no histórico do Git (até o commit `06a15c1`).
