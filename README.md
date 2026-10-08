# EVCS–BESS–V2G

Base colaborativa de pesquisa para Ayrton (EVCS), Kenia (BESS) e Cesar (V2G).
**Novo na equipe? / New to the team? / ¿Nuevo en el equipo? → [ONBOARDING.md](ONBOARDING.md)**
**S0–S4 utilizam o IEEE123 com pandapower trifásico. A representação contém aproximações explícitas; a equivalência numérica ao caso IEEE de referência ainda não foi validada.**

## Começar
Python 3.12 recomendado. Na pasta do projeto:
```bash
python -m venv .venv
```
Ativar no Windows PowerShell: `.venv\Scripts\Activate.ps1`. Linux/macOS: `source .venv/bin/activate`.
```bash
python -m pip install -e ".[dev]"
python -m pytest -q
evcs-demo --output results/demo
jupyter lab
```
Alternativa sem instalar o pacote, Linux/macOS: `PYTHONPATH=src python -m integration.coordinator`.
Windows PowerShell: `$env:PYTHONPATH="src"; python -m integration.coordinator`.
Executar comandos na raiz para resolver `configs/`. O backend é `pandapower.runpp_3ph` (3.4.x), sem dependência de OpenDSS ou solver comercial. Os dados estão incluídos no pacote; a simulação não precisa de internet.

## O que está pronto
| Módulo | Estado |
|---|---|
| core | Estruturas, unidades, perfis por barra/fase/hora e validação |
| network | IEEE123 em pandapower, cargas assimétricas estrela/delta, PQ/I/Z, capacitores e reguladores equivalentes com taps fixos |
| EVCS | Demanda agregada, limite de potência, energia atendida/não atendida e candidatos topológicos |
| BESS | Dinâmica de SOC, eficiência, limites, rejeição de comandos e redução de pico com SOC terminal |
| V2G | Frota por veículo, disponibilidade, reserva de mobilidade, SOC terminal e infraestrutura EVCS compartilhada |
| integration | S0–S4, métricas, custos demonstrativos, CSV/JSON e gráfico |
| XLSX | Modelo vazio com 10 tabelas, importador normalizado e preservação de equipamentos |
| colaboração | Guia Git, CODEOWNERS comentado, modelo de PR e CI |
| otimização | Interfaces reservadas; funções lançam NotImplementedError |

## Cenários
S0: IEEE123 com cargas originais e curva diária configurável. S1: EVCS e frota unidirecional. S2: S1 + BESS.
S3: mesma população EVCS com V2G habilitado. S4: EVCS + BESS + V2G.
A frota tem a mesma energia de partida em S1–S4. Cada ativo é agregado uma única vez. A demanda independente usa carregadores da mesma estação, sem duplicar os veículos da frota.

## Arquivos para começar
- `notebooks/00_network_baseline.ipynb`: IEEE123 em pandapower.
- `notebooks/01_evcs_ayrton.ipynb`, `02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: trabalho individual.
- `notebooks/04_integrated_analysis.ipynb`: comparação.
- `configs/*.yaml`: parâmetros e hipóteses.
- `data/honduras/templates/honduras_template.xlsx`: planilha vazia.
- `results/ieee123/`: resultados IEEE123, gerados por `evcs-demo --output results/ieee123`.
- `results/demo_reference/`: resultados sintéticos históricos, preservados para comparação.
- `docs/mathematical_formulation.md`: equações e fronteiras de contabilização.
- `docs/methodology.md`: fontes e limitações.
- `docs/collaboration_guide.md`: branches, PR e próximas tarefas.
- `docs/validation.md`: testes e ambiente de execução.

## Execução independente por equipe
Cada comando lê somente seu próprio YAML e escreve apenas em sua subpasta:

| Equipe | Comando | Configuração | Saída |
|---|---|---|---|
| EVCS / Ayrton | `python -m evcs` | `configs/evcs.yaml` | `results/modules/evcs/` |
| BESS / Kenia | `python -m bess` | `configs/bess.yaml` | `results/modules/bess/` |
| V2G / Cesar | `python -m v2g` | `configs/v2g.yaml` | `results/modules/v2g/` |

Os notebooks 01–03 usam esses mesmos executores independentes. BESS recebe uma curva fixa de demanda; V2G recebe dados fixos de infraestrutura/ocupação. Essas entradas de teste pertencem ao próprio módulo e não acompanham alterações de outras equipes. Todos exportam `profile.csv` e `manifest.json`; BESS também exporta despacho/SOC, e V2G potência/energia por veículo. `--output` altera a pasta-pai, preservando as subpastas por módulo.

`python -m integration.coordinator --output results/ieee123` executa a integração. Além dos totais, exporta cada contribuição em `results/ieee123/modules/<módulo>/<cenário>/`. O coordenador acessa cada módulo somente por `<módulo>.runner.run_integrated`; o restante do código de cada equipe pode ser reorganizado livremente. Ele usa a demanda EVCS efetiva para V2G e a demanda agregada para BESS. A independência de desenvolvimento não elimina esse acoplamento físico. Veja o [guia das equipes](docs/collaboration_guide.md).

## Limitações essenciais
O modelo usa os dados do alimentador IEEE123, com 130 barras computacionais incluindo terminais de equipamentos. O pandapower nativo usa componentes de sequência: as matrizes de linha são aproximadas, fases ausentes exigem ramos equivalentes, reguladores usam taps médios fixos e o transformador delta-delta sem carga usa um equivalente aterrado. Os limites térmicos de linha são hipóteses. Consulte [dados e aproximações](data/ieee123/README.md).

EVCS e BESS estão inicialmente na barra 67, como hipótese de estudo. Curva diária, localizações e alvo de redução de pico são configuráveis nos YAML. `*_injections.csv` contém somente injeções adicionais; `*_native_loads.csv` contém as cargas originais efetivas e os capacitores, após considerar a tensão.

O backend sintético permanece para testes analíticos. XLSX é um contrato preliminar e não aceita automaticamente formatos arbitrários da concessionária.
As heurísticas não resolvem a alocação/dimensionamento ótimo. Violações elétricas são reportadas, não corrigidas. Custos são hipóteses em USD, sem tarifa real de Honduras. A licença está pendente de escolha dos autores.

Repositório: https://github.com/atnee/EVCS-BESS-V2G. CODEOWNERS e proteção da `main` ainda não foram configurados.
