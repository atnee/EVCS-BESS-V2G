# EVCS–BESS–V2G

[English](#english) · [Español](#español) · [Português](#português)

**New to the team? / ¿Nuevo en el equipo? / Novo na equipe? → [ONBOARDING.md](ONBOARDING.md)**

| Owner / Responsable / Responsável | Run / Ejecutar / Executar | Config | Output / Salida / Saída |
|---|---|---|---|
| Ayrton (EVCS) | `python -m evcs` | `configs/evcs.yaml` | `results/modules/evcs/` |
| Kenia (BESS) | `python -m bess` | `configs/bess.yaml` | `results/modules/bess/` |
| Cesar (V2G) | `python -m v2g` | `configs/v2g.yaml` | `results/modules/v2g/` |
| Integration S0–S4 / Integración / Integração | `python -m integration.coordinator --output results/ieee8500/integration` | `configs/*.yaml` (feeder: `configs/network.yaml`) | `results/ieee8500/integration/` |
| EVCS — S0 base case (IEEE 8500) / caso base | `python -m integration.s0_study` | `configs/ieee8500.yaml` | `results/ieee8500/s0/` |
| EVCS — siting screening / triagem | `python -m integration.evcs_screening` (1st run: hosting sweep, ~1.5 h on one core) | `configs/evcs.yaml` (`planning:`) | `results/ieee8500/evcs_screening/` |
| EVCS — S1 intraday (15 min) / S1 intradiário | `python -m integration.s1_study` (~35 min in parallel) | `configs/evcs.yaml` (`planning:`) | `results/ieee8500/s1/` |
| EVCS — S0 × S1 analysis / análisis / análise | `python -m integration.analysis` | — | `results/ieee8500/s0_vs_s1/` |
| Redraw figures only / Rehacer figuras / Refazer figuras | `python -m integration.redraw` (no power flow) | — | `results/ieee8500/` |

Studies run on several CPU cores (one process per fleet or per group of buses): `--workers N` or `EVCS_WORKERS=N` sets the number of processes (default: physical cores − 1).

### Where to find what / Dónde encontrar / Onde encontrar

```
configs/      parameters (YAML) — one per module · network.yaml (feeder of every study: ieee8500) · one per feeder
src/          code: evcs/ bess/ v2g/ (one per owner) · network/ (feeders, power flow) · integration/ (studies, coordinator) · core/
data/         feeder source data (ieee8500/ — see its README) · honduras/ templates
notebooks/    01 EVCS (Ayrton) · 02 BESS (Kenia) · 03 V2G (Cesar) · 04 integration
results/      generated outputs, not in Git — START at results/ieee8500/resumo/ (8 figures + LEIA-ME.md); S0 × S1 in results/ieee8500/s0_vs_s1/ANALISE.md; map in results/README.md
docs/         metodologia.md (methodology for the article) · S2_BESS_Kenia.md, S3_V2G_Cesar.md (what S2 and S3 must deliver) · tutorial_vscode_ia.md (VS Code + AI assistant) · team guide · evcs/figures
AGENTS.md     rules the AI assistants (Claude Code, Copilot, Codex, Cursor) read automatically; CLAUDE.md imports it
scripts/      rebuild the feeder JSON from data/<feeder>/source
tests/        pytest
```

EVCS documentation and results: **[src/evcs/README.md](src/evcs/README.md)** · methodology: **[docs/metodologia.md](docs/metodologia.md)** · S2/S3: [docs/S2_BESS_Kenia.md](docs/S2_BESS_Kenia.md), [docs/S3_V2G_Cesar.md](docs/S3_V2G_Cesar.md) · outputs: [results/README.md](results/README.md).

### Quick start / Inicio rápido / Começar
```bash
python -m venv .venv
.venv\Scripts\Activate.ps1           # Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m pytest -q
evcs-demo --output results/demo
jupyter lab
```
Without installing / Sin instalar / Sem instalar:
```bash
PYTHONPATH=src python -m integration.coordinator                 # Linux/macOS
$env:PYTHONPATH="src"; python -m integration.coordinator         # Windows PowerShell
```

Repository / Repositorio / Repositório: https://github.com/atnee/EVCS-BESS-V2G

---

## English

Collaborative research framework for Ayrton (EVCS), Kenia (BESS) and Cesar (V2G).

> **Every study (S0–S4 integration and the EVCS studies) uses the IEEE 8500-node feeder** (`configs/network.yaml`): a medium-voltage reduction with 2,521 buses, 170 km of lines, 1,177 customers and 10.8 MW of nominal load over ~11 × 12 km, solved with three-phase pandapower. Two values differ from the IEEE data on purpose (conductor ampacity; 1.0 pu source with regulators at 1.03 pu) — see [data/ieee8500/README.md](data/ieee8500/README.md). Numerical equivalence with the IEEE reference case has not been validated.

### Getting started
Python 3.12 or newer (tested on 3.13.5). Run the commands above from the project root so `configs/` is found. The backend is `pandapower.runpp_3ph` (3.4.x), with no dependency on OpenDSS or a commercial solver. The data ships with the package; the simulation does not need internet access.

### What is ready
| Module | Status |
|---|---|
| core | Data structures, units, profiles per bus/phase/hour and validation |
| network | IEEE 8500 in pandapower: unbalanced loads, PQ/I/Z, capacitors, regulator tap control at every step, hosting capacity per bus |
| EVCS | Fleet and minute-level charging sessions, charger sizing, DC hubs plus three-phase and single-phase AC stations sited by full-coverage optimization (MILP, HiGHS), FIFO queues, mixed fleet (private cars and taxis/ride-hailing), intraday S1 (15 min) for 1,000–3,000 vehicles |
| BESS | SOC dynamics, efficiency, limits, command rejection and peak shaving with terminal SOC (baseline 40 kW / 120 kWh, to be resized in S2) |
| V2G | Per-vehicle fleet, availability, mobility reserve, terminal SOC and shared EVCS infrastructure (baseline of 3 vehicles, to be scaled in S3) |
| integration | S0–S4, metrics, demonstration costs, CSV/JSON and figures; S0 × S1 power-quality analysis (ANSI C84.1, PRODIST Module 8); parallel runs on several CPU cores |
| XLSX | Empty template with 10 tables, normalized importer and equipment preservation |
| collaboration | Git guide, commented CODEOWNERS, PR template and CI |
| optimization | EVCS siting implemented (`evcs.planning.optimal_coverage`); BESS/V2G siting and sizing stubs (`bess/optimization.py`, `v2g/optimization.py`) still raise NotImplementedError; S2/S3/S4 formulations proposed in [docs/metodologia.md](docs/metodologia.md) |

### Scenarios
- **S0:** IEEE 8500 base case with the original loads and a configurable daily curve (`configs/ieee8500.yaml`); no charging stations.
- **S1:** a mixed fleet of 3,000 EVs (85 % private cars, many without a garage; 15 % taxis/ride-hailing), 27 MWh/day at 75 public stations (4 DC hubs, 9 three-phase and 62 single-phase AC stations, 4.5 MW installed), plus the V2G host station. S1 overloads the feeder; in the 15-min EVCS study, the substation peak rises 19 %, a line reaches 109 % and the minimum voltage drops to 0.933 pu (0.924 pu without the regulators' response).
- **S2:** S1 + BESS (Kenia), which must reduce the S1 overload.
- **S3:** S1 with V2G enabled (Cesar).
- **S4:** S1 + BESS + V2G, built after S2 and S3 are complete.

The fleet has the same starting energy in S1–S4. Each asset is aggregated only once. Independent demand uses chargers at the same station without duplicating fleet vehicles.

### Key files
- `notebooks/01_evcs_ayrton.ipynb`: EVCS on the IEEE 8500 — base case S0, siting screening and intraday S1 curves.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: individual work.
- `notebooks/04_integrated_analysis.ipynb`: comparison.
- `src/evcs/README.md`: EVCS module — sessions, hubs and eletropostos, daily and intraday curves (Portuguese).
- `configs/*.yaml`: parameters and assumptions.
- `data/honduras/templates/honduras_template.xlsx`: empty spreadsheet.
- `results/`: generated outputs, map in [results/README.md](results/README.md).
- `docs/metodologia.md`: methodology — network model, siting optimization (MILP), variables and units, solvers, indicators, limitations (Portuguese).
- `docs/S2_BESS_Kenia.md`, `docs/S3_V2G_Cesar.md`: what S2 (BESS) and S3 (V2G) must deliver, one document each (Spanish).
- `docs/collaboration_guide.md`: branches, PRs and next tasks.
- `docs/validation.md`: tests, runtime environment and what has been verified.

### Independent work per team
Each module command (table above) reads only its own YAML and writes only to its own subfolder. Notebooks 01–03 use the same standalone runners. BESS receives a fixed demand curve; V2G receives fixed infrastructure/occupancy data. These test inputs belong to the module itself and do not follow changes made by other teams. All modules export `profile.csv` and `manifest.json`; BESS also exports dispatch/SOC, and V2G per-vehicle power/energy. `--output` changes the parent folder and keeps the per-module subfolders.

### Integration
`python -m integration.coordinator --output results/ieee8500/integration` runs the integration (~8 min on the IEEE 8500). Besides the totals, it exports each module's contribution to `results/ieee8500/integration/modules/<module>/<scenario>/`. The coordinator reaches each module only through `<module>.runner.run_integrated`; the rest of each team's code can be reorganized freely. It uses the effective EVCS demand for V2G and the aggregated demand for BESS. Independent development does not remove this physical coupling. See the [team guide](docs/collaboration_guide.md).

### Key limitations
The IEEE 8500 is reduced to medium voltage (2,521 buses): service transformers and 120/240 V loads are lumped on the primary bus. Native pandapower uses sequence components: line matrices are approximated, missing phases require equivalent branches and regulator banks share one tap. See [data/ieee8500/README.md](data/ieee8500/README.md) and the limitations in [docs/metodologia.md](docs/metodologia.md).

In S1–S4 the charging stations are at the 75 buses listed in `configs/evcs.yaml` (`integration:`); the V2G host station and the BESS are at bus `m1125976` (a DC hub site), a study assumption to be revised in S2/S3. The daily curve, locations and peak-shaving target are configurable in the YAML files. `*_injections.csv` contains only additional injections; `*_native_loads.csv` contains the effective original loads and capacitors after accounting for voltage.

XLSX is a preliminary contract and does not automatically accept arbitrary utility formats. EVCS siting is optimized; BESS and V2G sizing and dispatch are still heuristics. Electrical violations are reported, not corrected. Costs are assumptions in USD, with no real Honduran tariff. The license is pending the authors' choice. CODEOWNERS and `main` branch protection are not configured yet.

---

## Español

Base colaborativa de investigación para Ayrton (EVCS), Kenia (BESS) y Cesar (V2G).

> **Todos los estudios (integración S0–S4 y estudios del EVCS) usan el alimentador IEEE 8500 nodos** (`configs/network.yaml`): reducción de media tensión con 2.521 barras, 170 km de líneas, 1.177 consumidores y 10,8 MW de carga nominal en ~11 × 12 km, resuelta con pandapower trifásico. Dos valores difieren a propósito de los datos IEEE (ampacidad por conductor; fuente en 1,0 pu y reguladores en 1,03 pu) — ver [data/ieee8500/README.md](data/ieee8500/README.md). La equivalencia numérica con el caso IEEE de referencia no ha sido validada.

### Primeros pasos
Python 3.12 o superior (probado con 3.13.5). Ejecuta los comandos de arriba en la raíz del proyecto para que se encuentre `configs/`. El backend es `pandapower.runpp_3ph` (3.4.x), sin dependencia de OpenDSS ni de un solver comercial. Los datos vienen incluidos en el paquete; la simulación no necesita internet.

### Qué está listo
| Módulo | Estado |
|---|---|
| core | Estructuras, unidades, perfiles por barra/fase/hora y validación |
| network | IEEE 8500 en pandapower: cargas desbalanceadas, PQ/I/Z, capacitores, control de tap de los reguladores en cada paso, capacidad de alojamiento por barra |
| EVCS | Flota y sesiones de recarga minuto a minuto, dimensionamiento de cargadores, hubs DC y electrolineras AC trifásicas y monofásicas ubicadas por optimización de cobertura total (MILP, HiGHS), colas FIFO, S1 flota mixta (autos particulares y taxis/aplicaciones), S1 intradiario (15 min) para 1.000–3.000 vehículos |
| BESS | Dinámica de SOC, eficiencia, límites, rechazo de comandos y recorte de picos con SOC terminal (base de 40 kW / 120 kWh, a redimensionar en el S2) |
| V2G | Flota por vehículo, disponibilidad, reserva de movilidad, SOC terminal e infraestructura EVCS compartida (base de 3 vehículos, a ampliar en el S3) |
| integration | S0–S4, métricas, costos demostrativos, CSV/JSON y figuras; análisis de calidad de energía S0 × S1 (ANSI C84.1, PRODIST Módulo 8); ejecución en paralelo en varios núcleos |
| XLSX | Plantilla vacía con 10 tablas, importador normalizado y preservación de equipos |
| colaboración | Guía Git, CODEOWNERS comentado, plantilla de PR y CI |
| optimización | Ubicación del EVCS implementada (`evcs.planning.optimal_coverage`); los esbozos de ubicación y dimensionamiento de BESS/V2G (`bess/optimization.py`, `v2g/optimization.py`) aún lanzan NotImplementedError; formulaciones de S2/S3/S4 propuestas en [docs/metodologia.md](docs/metodologia.md) |

### Escenarios
- **S0:** caso base IEEE 8500 con las cargas originales y curva diaria configurable (`configs/ieee8500.yaml`); sin estaciones de recarga.
- **S1:** flota mixta de 3.000 VE (85 % autos particulares, muchos sin garaje; 15 % taxis/aplicaciones), 27 MWh/día en 75 estaciones públicas (4 hubs DC, 9 electrolineras AC trifásicas y 62 monofásicas, 4,5 MW instalados), más la estación anfitriona del V2G. El S1 sobrecarga el alimentador; en el estudio del EVCS de 15 min, la punta de la subestación sube 19 %, una línea llega a 109 % y la tensión mínima baja a 0,933 pu (0,924 pu sin la acción de los reguladores).
- **S2:** S1 + BESS (Kenia), que debe reducir la sobrecarga del S1.
- **S3:** S1 con V2G habilitado (Cesar).
- **S4:** S1 + BESS + V2G, después de completar S2 y S3.

La flota tiene la misma energía inicial en S1–S4. Cada activo se agrega una sola vez. La demanda independiente usa cargadores de la misma estación, sin duplicar los vehículos de la flota.

### Archivos principales
- `notebooks/01_evcs_ayrton.ipynb`: EVCS en el IEEE 8500 — caso base S0, triaje de sitios y curvas intradiarias del S1.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: trabajo individual.
- `notebooks/04_integrated_analysis.ipynb`: comparación.
- `src/evcs/README.md`: módulo EVCS — sesiones, hubs y electrolineras, curvas diarias e intradiarias (en portugués).
- `configs/*.yaml`: parámetros e hipótesis.
- `data/honduras/templates/honduras_template.xlsx`: planilla vacía.
- `results/`: salidas generadas, mapa en [results/README.md](results/README.md).
- `docs/metodologia.md`: metodología — modelo de la red, optimización de la ubicación (MILP), variables y unidades, solvers, indicadores, limitaciones (en portugués).
- `docs/S2_BESS_Kenia.md`, `docs/S3_V2G_Cesar.md`: qué deben entregar el S2 (BESS) y el S3 (V2G), un documento cada uno (en español).
- `docs/collaboration_guide.md`: branches, PR y próximas tareas.
- `docs/validation.md`: pruebas, entorno de ejecución y qué fue verificado.

### Trabajo independiente por equipo
Cada comando de módulo (tabla de arriba) lee solo su propio YAML y escribe solo en su propia subcarpeta. Los notebooks 01–03 usan los mismos ejecutores independientes. BESS recibe una curva de demanda fija; V2G recibe datos fijos de infraestructura/ocupación. Estas entradas de prueba pertenecen al propio módulo y no siguen los cambios de otros equipos. Todos exportan `profile.csv` y `manifest.json`; BESS también exporta despacho/SOC, y V2G potencia/energía por vehículo. `--output` cambia la carpeta padre y conserva las subcarpetas por módulo.

### Integración
`python -m integration.coordinator --output results/ieee8500/integration` ejecuta la integración (~8 min en el IEEE 8500). Además de los totales, exporta la contribución de cada módulo en `results/ieee8500/integration/modules/<módulo>/<escenario>/`. El coordinador accede a cada módulo solo mediante `<módulo>.runner.run_integrated`; el resto del código de cada equipo puede reorganizarse libremente. Usa la demanda EVCS efectiva para V2G y la demanda agregada para BESS. El desarrollo independiente no elimina este acoplamiento físico. Consulta la [guía de los equipos](docs/collaboration_guide.md).

### Limitaciones principales
El IEEE 8500 se reduce a media tensión (2.521 barras): transformadores de servicio y cargas de 120/240 V se agregan en la barra primaria. pandapower nativo usa componentes de secuencia: las matrices de línea son aproximadas, las fases ausentes requieren ramas equivalentes y cada banco de reguladores comparte un tap. Ver [data/ieee8500/README.md](data/ieee8500/README.md) y las limitaciones en [docs/metodologia.md](docs/metodologia.md).

En S1–S4 las estaciones de recarga están en las 75 barras listadas en `configs/evcs.yaml` (`integration:`); la estación anfitriona del V2G y la BESS están en la barra `m1125976` (sitio de un hub DC), una hipótesis del estudio a revisar en S2/S3. La curva diaria, las ubicaciones y el objetivo de recorte de picos son configurables en los YAML. `*_injections.csv` contiene solo las inyecciones adicionales; `*_native_loads.csv` contiene las cargas originales efectivas y los capacitores, después de considerar la tensión.

XLSX es un contrato preliminar y no acepta automáticamente formatos arbitrarios de la distribuidora. La ubicación del EVCS está optimizada; el dimensionamiento y el despacho de BESS y V2G aún son heurísticos. Las violaciones eléctricas se reportan, no se corrigen. Los costos son hipótesis en USD, sin tarifa real de Honduras. La licencia está pendiente de elección por los autores. CODEOWNERS y la protección de la branch `main` aún no están configurados.

---

## Português

Base colaborativa de pesquisa para Ayrton (EVCS), Kenia (BESS) e Cesar (V2G).

> **Todos os estudos (integração S0–S4 e estudos do EVCS) usam o alimentador IEEE 8500 nós** (`configs/network.yaml`): redução de média tensão com 2.521 barras, 170 km de linhas, 1.177 consumidores e 10,8 MW de carga nominal em ~11 × 12 km, resolvida com pandapower trifásico. Dois valores diferem de propósito dos dados IEEE (ampacidade por condutor; fonte em 1,0 pu e reguladores em 1,03 pu) — veja [data/ieee8500/README.md](data/ieee8500/README.md). A equivalência numérica com o caso IEEE de referência não foi validada.

### Começar
Python 3.12 ou mais novo (testado no 3.13.5). Execute os comandos acima na raiz do projeto para que `configs/` seja encontrado. O backend é `pandapower.runpp_3ph` (3.4.x), sem dependência de OpenDSS ou de solver comercial. Os dados estão incluídos no pacote; a simulação não precisa de internet.

### O que está pronto
| Módulo | Estado |
|---|---|
| core | Estruturas, unidades, perfis por barra/fase/hora e validação |
| network | IEEE 8500 em pandapower: cargas assimétricas, PQ/I/Z, capacitores, controle de tap dos reguladores a cada passo, capacidade de hospedagem por barra |
| EVCS | Frota e sessões de recarga minuto a minuto, dimensionamento dos carregadores, hubs DC e eletropostos AC trifásicos e monofásicos alocados por otimização de cobertura total (MILP, HiGHS), filas FIFO, frota mista (carros particulares e táxis/aplicativo), S1 intradiário (15 min) para 1.000–3.000 veículos |
| BESS | Dinâmica de SOC, eficiência, limites, rejeição de comandos e redução de pico com SOC terminal (base de 40 kW / 120 kWh, a redimensionar no S2) |
| V2G | Frota por veículo, disponibilidade, reserva de mobilidade, SOC terminal e infraestrutura EVCS compartilhada (base de 3 veículos, a ampliar no S3) |
| integration | S0–S4, métricas, custos demonstrativos, CSV/JSON e figuras; análise de qualidade de energia S0 × S1 (ANSI C84.1, PRODIST Módulo 8); execução em paralelo em vários núcleos |
| XLSX | Modelo vazio com 10 tabelas, importador normalizado e preservação de equipamentos |
| colaboração | Guia Git, CODEOWNERS comentado, modelo de PR e CI |
| otimização | Alocação do EVCS implementada (`evcs.planning.optimal_coverage`); os esboços de alocação e dimensionamento de BESS/V2G (`bess/optimization.py`, `v2g/optimization.py`) ainda lançam NotImplementedError; formulações de S2/S3/S4 propostas em [docs/metodologia.md](docs/metodologia.md) |

### Cenários
- **S0:** caso base IEEE 8500 com as cargas originais e curva diária configurável (`configs/ieee8500.yaml`); sem estações de recarga.
- **S1:** frota mista de 3.000 veículos elétricos (85 % carros particulares, muitos sem garagem; 15 % táxis/aplicativo), 27 MWh/dia em 75 estações públicas (4 hubs DC, 9 eletropostos AC trifásicos e 62 monofásicos, 4,5 MW instalados), mais a estação anfitriã do V2G. O S1 sobrecarrega o alimentador; no estudo do EVCS de 15 min, a ponta da subestação sobe 19 %, uma linha chega a 109 % e a tensão mínima cai para 0,933 pu (0,924 pu sem a ação dos reguladores).
- **S2:** S1 + BESS (Kenia), que deve reduzir a sobrecarga do S1.
- **S3:** S1 com V2G habilitado (Cesar).
- **S4:** S1 + BESS + V2G, depois que S2 e S3 estiverem completos.

A frota tem a mesma energia de partida em S1–S4. Cada ativo é agregado uma única vez. A demanda independente usa carregadores da mesma estação, sem duplicar os veículos da frota.

### Arquivos para começar
- `notebooks/01_evcs_ayrton.ipynb`: EVCS no IEEE 8500 — caso base S0, triagem de locais e curvas intradiárias do S1.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: trabalho individual.
- `notebooks/04_integrated_analysis.ipynb`: comparação.
- `src/evcs/README.md`: módulo EVCS — sessões, hubs e eletropostos, curvas diárias e intradiárias.
- `configs/*.yaml`: parâmetros e hipóteses.
- `data/honduras/templates/honduras_template.xlsx`: planilha vazia.
- `results/`: saídas geradas, mapa em [results/README.md](results/README.md).
- `docs/metodologia.md`: metodologia — modelo da rede, otimização da alocação (MILP), variáveis e unidades, solvers, indicadores, limitações.
- `docs/S2_BESS_Kenia.md`, `docs/S3_V2G_Cesar.md`: o que o S2 (BESS) e o S3 (V2G) devem entregar, um documento para cada (em espanhol).
- `docs/collaboration_guide.md`: branches, PR e próximas tarefas.
- `docs/validation.md`: testes, ambiente de execução e o que foi verificado.

### Execução independente por equipe
Cada comando de módulo (tabela acima) lê somente seu próprio YAML e escreve apenas em sua própria subpasta. Os notebooks 01–03 usam esses mesmos executores independentes. BESS recebe uma curva fixa de demanda; V2G recebe dados fixos de infraestrutura/ocupação. Essas entradas de teste pertencem ao próprio módulo e não acompanham alterações de outras equipes. Todos exportam `profile.csv` e `manifest.json`; BESS também exporta despacho/SOC, e V2G potência/energia por veículo. `--output` altera a pasta-pai, preservando as subpastas por módulo.

### Integração
`python -m integration.coordinator --output results/ieee8500/integration` executa a integração (~8 min no IEEE 8500). Além dos totais, exporta a contribuição de cada módulo em `results/ieee8500/integration/modules/<módulo>/<cenário>/`. O coordenador acessa cada módulo somente por `<módulo>.runner.run_integrated`; o restante do código de cada equipe pode ser reorganizado livremente. Ele usa a demanda EVCS efetiva para V2G e a demanda agregada para BESS. A independência de desenvolvimento não elimina esse acoplamento físico. Veja o [guia das equipes](docs/collaboration_guide.md).

### Limitações essenciais
O IEEE 8500 é reduzido à média tensão (2.521 barras): transformadores de serviço e cargas de 120/240 V são agregados na barra primária. O pandapower nativo usa componentes de sequência: as matrizes de linha são aproximadas, fases ausentes exigem ramos equivalentes e cada banco de reguladores compartilha um tap. Veja [data/ieee8500/README.md](data/ieee8500/README.md) e as limitações em [docs/metodologia.md](docs/metodologia.md).

No S1–S4 as estações de recarga ficam nas 75 barras listadas em `configs/evcs.yaml` (`integration:`); a estação anfitriã do V2G e a BESS ficam na barra `m1125976` (local de um hub DC), uma hipótese do estudo a rever no S2/S3. Curva diária, localizações e alvo de redução de pico são configuráveis nos YAML. `*_injections.csv` contém somente injeções adicionais; `*_native_loads.csv` contém as cargas originais efetivas e os capacitores, após considerar a tensão.

XLSX é um contrato preliminar e não aceita automaticamente formatos arbitrários da concessionária. A alocação do EVCS é otimizada; o dimensionamento e o despacho da BESS e do V2G ainda são heurísticos. Violações elétricas são reportadas, não corrigidas. Custos são hipóteses em USD, sem tarifa real de Honduras. A licença está pendente de escolha dos autores. CODEOWNERS e proteção da `main` ainda não foram configurados.
