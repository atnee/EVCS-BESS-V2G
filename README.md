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
| EVCS — siting screening / triagem | `python -m integration.evcs_screening` (1st run ~1.5 h) | `configs/evcs.yaml` (`planning:`) | `results/ieee8500/evcs_screening/` |
| EVCS — S1 intraday (15 min) / S1 intradiário | `python -m integration.s1_study` | `configs/evcs.yaml` (`planning:`) | `results/ieee8500/s1/` |

### Where to find what / Dónde encontrar / Onde encontrar

```
configs/      parameters (YAML) — one per module · network.yaml (feeder of every study) · one per feeder (ieee8500, ieee123)
src/          code: evcs/ bess/ v2g/ (one per owner) · network/ (feeders, power flow) · integration/ (studies, coordinator) · core/
data/         feeder source data (ieee123/, ieee8500/ — see each README) · honduras/ templates
notebooks/    01 EVCS (Ayrton) · 02 BESS (Kenia) · 03 V2G (Cesar) · 04 integration
results/      generated outputs, not in Git — START at results/ieee8500/resumo/ (4 figures + LEIA-ME.md); map in results/README.md
docs/         methodology, team guide, docs/evcs/figures (figures shown in src/evcs/README.md)
scripts/      rebuild the feeder JSON from data/<feeder>/source
tests/        pytest
```

EVCS documentation and results: **[src/evcs/README.md](src/evcs/README.md)** · outputs: [results/README.md](results/README.md).

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

> **Every study (S0–S4 integration and the EVCS studies) uses the IEEE 8500-node feeder**, a medium-voltage reduction solved with three-phase pandapower (`configs/network.yaml`; `ieee123` still available). Two values differ from the IEEE data on purpose (conductor ampacity; 1.0 pu source with regulators at 1.03 pu) — see [data/ieee8500/README.md](data/ieee8500/README.md). Numerical equivalence with the IEEE reference case has not been validated.

### Getting started
Python 3.12 recommended. Run the commands above from the project root so `configs/` is found. The backend is `pandapower.runpp_3ph` (3.4.x), with no dependency on OpenDSS or a commercial solver. The data ships with the package; the simulation does not need internet access.

### What is ready
| Module | Status |
|---|---|
| core | Data structures, units, profiles per bus/phase/hour and validation |
| network | IEEE 8500 (default) and IEEE123 in pandapower, unbalanced loads, PQ/I/Z, capacitors, regulators with tap control per step (IEEE 8500) |
| EVCS | Aggregated demand, power limit, served/unserved energy and topological candidates |
| BESS | SOC dynamics, efficiency, limits, command rejection and peak shaving with terminal SOC |
| V2G | Per-vehicle fleet, availability, mobility reserve, terminal SOC and shared EVCS infrastructure |
| integration | S0–S4, metrics, demonstration costs, CSV/JSON and plot |
| XLSX | Empty template with 10 tables, normalized importer and equipment preservation |
| collaboration | Git guide, commented CODEOWNERS, PR template and CI |
| optimization | Reserved interfaces; functions raise NotImplementedError |

### Scenarios
- **S0:** IEEE 8500 base case with original loads and a configurable daily curve (`configs/ieee8500.yaml`).
- **S1:** EVCS and unidirectional fleet.
- **S2:** S1 + BESS.
- **S3:** same EVCS population with V2G enabled.
- **S4:** EVCS + BESS + V2G.

The fleet has the same starting energy in S1–S4. Each asset is aggregated only once. Independent demand uses chargers at the same station without duplicating fleet vehicles.

### Key files
- `notebooks/01_evcs_ayrton.ipynb`: EVCS on the IEEE 8500 — base case S0, siting screening and intraday S1 curves.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: individual work.
- `notebooks/04_integrated_analysis.ipynb`: comparison.
- `src/evcs/README.md`: EVCS module — sessions, hubs and eletropostos, daily and intraday curves (Portuguese).
- `configs/*.yaml`: parameters and assumptions.
- `data/honduras/templates/honduras_template.xlsx`: empty spreadsheet.
- `results/`: generated outputs, map in [results/README.md](results/README.md).
- `results/demo_reference/`: historical synthetic results, kept for comparison.
- `docs/mathematical_formulation.md`: equations and accounting boundaries.
- `docs/methodology.md`: sources and limitations.
- `docs/collaboration_guide.md`: branches, PRs and next tasks.
- `docs/validation.md`: tests and runtime environment.

### Independent work per team
Each module command (table above) reads only its own YAML and writes only to its own subfolder. Notebooks 01–03 use the same standalone runners. BESS receives a fixed demand curve; V2G receives fixed infrastructure/occupancy data. These test inputs belong to the module itself and do not follow changes made by other teams. All modules export `profile.csv` and `manifest.json`; BESS also exports dispatch/SOC, and V2G per-vehicle power/energy. `--output` changes the parent folder and keeps the per-module subfolders.

### Integration
`python -m integration.coordinator --output results/ieee8500/integration` runs the integration (~8 min on the IEEE 8500). Besides the totals, it exports each module's contribution to `results/ieee8500/integration/modules/<module>/<scenario>/`. The coordinator reaches each module only through `<module>.runner.run_integrated`; the rest of each team's code can be reorganized freely. It uses the effective EVCS demand for V2G and the aggregated demand for BESS. Independent development does not remove this physical coupling. See the [team guide](docs/collaboration_guide.md).

### Key limitations
The IEEE 8500 is reduced to medium voltage (2,521 buses): service transformers and 120/240 V loads are lumped on the primary bus. Native pandapower uses sequence components: line matrices are approximated, missing phases require equivalent branches and regulator banks share one tap. See [data/ieee8500/README.md](data/ieee8500/README.md) (and [data/ieee123/README.md](data/ieee123/README.md) for the old feeder).

EVCS, BESS and V2G connect at bus `m1125976` (IEEE 8500; the DC hub site of the EVCS screening for 2000 cars), a study assumption. The daily curve, locations and peak-shaving target are configurable in the YAML files. `*_injections.csv` contains only additional injections; `*_native_loads.csv` contains the effective original loads and capacitors after accounting for voltage.

The synthetic backend remains for analytical tests. XLSX is a preliminary contract and does not automatically accept arbitrary utility formats. The heuristics do not solve optimal allocation/sizing. Electrical violations are reported, not corrected. Costs are assumptions in USD, with no real Honduran tariff. The license is pending the authors' choice. CODEOWNERS and `main` branch protection are not configured yet.

---

## Español

Base colaborativa de investigación para Ayrton (EVCS), Kenia (BESS) y Cesar (V2G).

> **Todos los estudios (integración S0–S4 y estudios del EVCS) usan el alimentador IEEE 8500 nodos**, reducción de media tensión resuelta con pandapower trifásico (`configs/network.yaml`; `ieee123` sigue disponible). Dos valores difieren a propósito de los datos IEEE (ampacidad por conductor; fuente en 1,0 pu y reguladores en 1,03 pu) — ver [data/ieee8500/README.md](data/ieee8500/README.md). La equivalencia numérica con el caso IEEE de referencia no ha sido validada.

### Primeros pasos
Se recomienda Python 3.12. Ejecuta los comandos de arriba en la raíz del proyecto para que se encuentre `configs/`. El backend es `pandapower.runpp_3ph` (3.4.x), sin dependencia de OpenDSS ni de un solver comercial. Los datos vienen incluidos en el paquete; la simulación no necesita internet.

### Qué está listo
| Módulo | Estado |
|---|---|
| core | Estructuras, unidades, perfiles por barra/fase/hora y validación |
| network | IEEE 8500 (predeterminado) e IEEE123 en pandapower, cargas desbalanceadas, PQ/I/Z, capacitores, reguladores con control de tap por paso (IEEE 8500) |
| EVCS | Demanda agregada, límite de potencia, energía atendida/no atendida y candidatos topológicos |
| BESS | Dinámica de SOC, eficiencia, límites, rechazo de comandos y recorte de picos con SOC terminal |
| V2G | Flota por vehículo, disponibilidad, reserva de movilidad, SOC terminal e infraestructura EVCS compartida |
| integration | S0–S4, métricas, costos demostrativos, CSV/JSON y gráfico |
| XLSX | Plantilla vacía con 10 tablas, importador normalizado y preservación de equipos |
| colaboración | Guía Git, CODEOWNERS comentado, plantilla de PR y CI |
| optimización | Interfaces reservadas; las funciones lanzan NotImplementedError |

### Escenarios
- **S0:** caso base IEEE 8500 con cargas originales y curva diaria configurable (`configs/ieee8500.yaml`).
- **S1:** EVCS y flota unidireccional.
- **S2:** S1 + BESS.
- **S3:** misma población EVCS con V2G habilitado.
- **S4:** EVCS + BESS + V2G.

La flota tiene la misma energía inicial en S1–S4. Cada activo se agrega una sola vez. La demanda independiente usa cargadores de la misma estación, sin duplicar los vehículos de la flota.

### Archivos principales
- `notebooks/01_evcs_ayrton.ipynb`: EVCS en el IEEE 8500 — caso base S0, triaje de sitios y curvas intradiarias del S1.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: trabajo individual.
- `notebooks/04_integrated_analysis.ipynb`: comparación.
- `src/evcs/README.md`: módulo EVCS — sesiones, hubs y electrolineras, curvas diarias e intradiarias (en portugués).
- `configs/*.yaml`: parámetros e hipótesis.
- `data/honduras/templates/honduras_template.xlsx`: planilla vacía.
- `results/`: salidas generadas, mapa en [results/README.md](results/README.md).
- `results/demo_reference/`: resultados sintéticos históricos, conservados para comparación.
- `docs/mathematical_formulation.md`: ecuaciones y fronteras de contabilización.
- `docs/methodology.md`: fuentes y limitaciones.
- `docs/collaboration_guide.md`: branches, PR y próximas tareas.
- `docs/validation.md`: pruebas y entorno de ejecución.

### Trabajo independiente por equipo
Cada comando de módulo (tabla de arriba) lee solo su propio YAML y escribe solo en su propia subcarpeta. Los notebooks 01–03 usan los mismos ejecutores independientes. BESS recibe una curva de demanda fija; V2G recibe datos fijos de infraestructura/ocupación. Estas entradas de prueba pertenecen al propio módulo y no siguen los cambios de otros equipos. Todos exportan `profile.csv` y `manifest.json`; BESS también exporta despacho/SOC, y V2G potencia/energía por vehículo. `--output` cambia la carpeta padre y conserva las subcarpetas por módulo.

### Integración
`python -m integration.coordinator --output results/ieee8500/integration` ejecuta la integración (~8 min en el IEEE 8500). Además de los totales, exporta la contribución de cada módulo en `results/ieee8500/integration/modules/<módulo>/<escenario>/`. El coordinador accede a cada módulo solo mediante `<módulo>.runner.run_integrated`; el resto del código de cada equipo puede reorganizarse libremente. Usa la demanda EVCS efectiva para V2G y la demanda agregada para BESS. El desarrollo independiente no elimina este acoplamiento físico. Consulta la [guía de los equipos](docs/collaboration_guide.md).

### Limitaciones principales
El IEEE 8500 se reduce a media tensión (2.521 barras): transformadores de servicio y cargas de 120/240 V se agregan en la barra primaria. pandapower nativo usa componentes de secuencia: las matrices de línea son aproximadas, las fases ausentes requieren ramas equivalentes y cada banco de reguladores comparte un tap. Ver [data/ieee8500/README.md](data/ieee8500/README.md).

EVCS, BESS y V2G se conectan en la barra `m1125976` (IEEE 8500; sitio del hub DC del triaje para 2000 autos), una hipótesis del estudio. La curva diaria, las ubicaciones y el objetivo de recorte de picos son configurables en los YAML. `*_injections.csv` contiene solo las inyecciones adicionales; `*_native_loads.csv` contiene las cargas originales efectivas y los capacitores, después de considerar la tensión.

El backend sintético se mantiene para pruebas analíticas. XLSX es un contrato preliminar y no acepta automáticamente formatos arbitrarios de la distribuidora. Las heurísticas no resuelven la asignación/dimensionamiento óptimo. Las violaciones eléctricas se reportan, no se corrigen. Los costos son hipótesis en USD, sin tarifa real de Honduras. La licencia está pendiente de elección por los autores. CODEOWNERS y la protección de la branch `main` aún no están configurados.

---

## Português

Base colaborativa de pesquisa para Ayrton (EVCS), Kenia (BESS) e Cesar (V2G).

> **Todos os estudos (integração S0–S4 e estudos do EVCS) usam o alimentador IEEE 8500 nós**, redução de média tensão resolvida com pandapower trifásico (`configs/network.yaml`; `ieee123` continua disponível). Dois valores diferem de propósito dos dados IEEE (ampacidade por condutor; fonte em 1,0 pu e reguladores em 1,03 pu) — veja [data/ieee8500/README.md](data/ieee8500/README.md). A equivalência numérica com o caso IEEE de referência não foi validada.

### Começar
Python 3.12 recomendado. Execute os comandos acima na raiz do projeto para que `configs/` seja encontrado. O backend é `pandapower.runpp_3ph` (3.4.x), sem dependência de OpenDSS ou de solver comercial. Os dados estão incluídos no pacote; a simulação não precisa de internet.

### O que está pronto
| Módulo | Estado |
|---|---|
| core | Estruturas, unidades, perfis por barra/fase/hora e validação |
| network | IEEE 8500 (padrão) e IEEE123 em pandapower, cargas assimétricas, PQ/I/Z, capacitores, reguladores com controle de tap a cada passo (IEEE 8500) |
| EVCS | Demanda agregada, limite de potência, energia atendida/não atendida e candidatos topológicos |
| BESS | Dinâmica de SOC, eficiência, limites, rejeição de comandos e redução de pico com SOC terminal |
| V2G | Frota por veículo, disponibilidade, reserva de mobilidade, SOC terminal e infraestrutura EVCS compartilhada |
| integration | S0–S4, métricas, custos demonstrativos, CSV/JSON e gráfico |
| XLSX | Modelo vazio com 10 tabelas, importador normalizado e preservação de equipamentos |
| colaboração | Guia Git, CODEOWNERS comentado, modelo de PR e CI |
| otimização | Interfaces reservadas; funções lançam NotImplementedError |

### Cenários
- **S0:** caso base IEEE 8500 com cargas originais e curva diária configurável (`configs/ieee8500.yaml`).
- **S1:** EVCS e frota unidirecional.
- **S2:** S1 + BESS.
- **S3:** mesma população EVCS com V2G habilitado.
- **S4:** EVCS + BESS + V2G.

A frota tem a mesma energia de partida em S1–S4. Cada ativo é agregado uma única vez. A demanda independente usa carregadores da mesma estação, sem duplicar os veículos da frota.

### Arquivos para começar
- `notebooks/01_evcs_ayrton.ipynb`: EVCS no IEEE 8500 — caso base S0, triagem de locais e curvas intradiárias do S1.
- `notebooks/02_bess_kenia.ipynb`, `03_v2g_cesar.ipynb`: trabalho individual.
- `notebooks/04_integrated_analysis.ipynb`: comparação.
- `src/evcs/README.md`: módulo EVCS — sessões, hubs e eletropostos, curvas diárias e intradiárias.
- `configs/*.yaml`: parâmetros e hipóteses.
- `data/honduras/templates/honduras_template.xlsx`: planilha vazia.
- `results/`: saídas geradas, mapa em [results/README.md](results/README.md).
- `results/demo_reference/`: resultados sintéticos históricos, preservados para comparação.
- `docs/mathematical_formulation.md`: equações e fronteiras de contabilização.
- `docs/methodology.md`: fontes e limitações.
- `docs/collaboration_guide.md`: branches, PR e próximas tarefas.
- `docs/validation.md`: testes e ambiente de execução.

### Execução independente por equipe
Cada comando de módulo (tabela acima) lê somente seu próprio YAML e escreve apenas em sua própria subpasta. Os notebooks 01–03 usam esses mesmos executores independentes. BESS recebe uma curva fixa de demanda; V2G recebe dados fixos de infraestrutura/ocupação. Essas entradas de teste pertencem ao próprio módulo e não acompanham alterações de outras equipes. Todos exportam `profile.csv` e `manifest.json`; BESS também exporta despacho/SOC, e V2G potência/energia por veículo. `--output` altera a pasta-pai, preservando as subpastas por módulo.

### Integração
`python -m integration.coordinator --output results/ieee8500/integration` executa a integração (~8 min no IEEE 8500). Além dos totais, exporta a contribuição de cada módulo em `results/ieee8500/integration/modules/<módulo>/<cenário>/`. O coordenador acessa cada módulo somente por `<módulo>.runner.run_integrated`; o restante do código de cada equipe pode ser reorganizado livremente. Ele usa a demanda EVCS efetiva para V2G e a demanda agregada para BESS. A independência de desenvolvimento não elimina esse acoplamento físico. Veja o [guia das equipes](docs/collaboration_guide.md).

### Limitações essenciais
O IEEE 8500 é reduzido à média tensão (2.521 barras): transformadores de serviço e cargas de 120/240 V são agregados na barra primária. O pandapower nativo usa componentes de sequência: as matrizes de linha são aproximadas, fases ausentes exigem ramos equivalentes e cada banco de reguladores compartilha um tap. Veja [data/ieee8500/README.md](data/ieee8500/README.md).

EVCS, BESS e V2G se conectam na barra `m1125976` (IEEE 8500; local do hub DC da triagem para 2000 carros), uma hipótese do estudo. Curva diária, localizações e alvo de redução de pico são configuráveis nos YAML. `*_injections.csv` contém somente injeções adicionais; `*_native_loads.csv` contém as cargas originais efetivas e os capacitores, após considerar a tensão.

O backend sintético permanece para testes analíticos. XLSX é um contrato preliminar e não aceita automaticamente formatos arbitrários da concessionária. As heurísticas não resolvem a alocação/dimensionamento ótimo. Violações elétricas são reportadas, não corrigidas. Custos são hipóteses em USD, sem tarifa real de Honduras. A licença está pendente de escolha dos autores. CODEOWNERS e proteção da `main` ainda não foram configurados.
