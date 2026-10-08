# Onboarding — EVCS–BESS–V2G

[English](#english) · [Español](#español) · [Português](#português)

| Owner / Responsable / Responsável | Branch | Code / Código | Config | Notebook |
|---|---|---|---|---|
| Ayrton (EVCS) | `feature/evcs-ayrton` | `src/evcs/` | `configs/evcs.yaml` | `notebooks/01_evcs_ayrton.ipynb` |
| Kenia (BESS) | `feature/bess-kenia` | `src/bess/` | `configs/bess.yaml` | `notebooks/02_bess_kenia.ipynb` |
| Cesar (V2G) | `feature/v2g-cesar` | `src/v2g/` | `configs/v2g.yaml` | `notebooks/03_v2g_cesar.ipynb` |

### Setup (first time / primera vez / primeira vez)
```bash
git clone https://github.com/atnee/EVCS-BESS-V2G.git
cd EVCS-BESS-V2G
git switch feature/bess-kenia        # EVCS: feature/evcs-ayrton · V2G: feature/v2g-cesar
python -m venv .venv
.venv\Scripts\Activate.ps1           # Linux/macOS: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m bess                       # EVCS: python -m evcs · V2G: python -m v2g
```

### Daily work / Trabajo diario / Dia a dia
```bash
git switch feature/bess-kenia
git pull                             # your branch / tu branch / sua branch
git merge origin/main                # latest shared version / última versión común / última versão comum
python -m pytest tests/test_bess.py tests/test_module_independence.py -q
git add src/bess configs/bess.yaml tests/test_bess.py notebooks/02_bess_kenia.ipynb
git commit -m "Describe the change"
git push
```
Replace `bess` with `evcs` or `v2g` in your module's paths and commands.
Reemplaza `bess` por `evcs` o `v2g` en las rutas y comandos de tu módulo.
Substitua `bess` por `evcs` ou `v2g` nos caminhos e comandos do seu módulo.

---

## English

Each team member works on their own module without depending on the others or breaking their work.

`python -m <module>` runs only your module, using the test data in your own YAML file. It doesn't need the other modules or the power flow, and it writes only to `results/modules/<module>/`. When you want to merge into the main version, open a Pull Request from your branch to `main` on GitHub.

**Ground rules**
- You can change anything inside your own folder: rename, split into files, replace the algorithm.
- The only thing that must stay the same is `run_integrated` in `runner.py`. Keep the same name and parameters, and it must return `(Profile, tables)` with the indicators in `metadata["kpis"]`. The integrated simulation (S0–S4) calls your module only through this function. If you need to change it, tell the team first so all three can review it together.
- Don't change `src/core/`, `src/network/`, `src/integration/` or other people's folders without agreeing first.
- Don't edit someone else's notebook.

**Where to start**
- Ayrton: independent vehicle population, hourly demand, queues and bus selection criteria, then EVCS sizing.
- Kenia: P/E sizing, tariff, degradation and terminal SOC, then replace the heuristic with optimized dispatch.
- Cesar: availability, trips, sessions and V2G degradation, then refine charger sharing.

Full guide: [docs/collaboration_guide.md](docs/collaboration_guide.md).

---

## Español

Cada integrante trabaja en su propio módulo, sin depender de los demás ni romper lo que hicieron.

`python -m <módulo>` ejecuta solo tu módulo, con los datos de prueba de tu propio YAML. No necesita los otros módulos ni el flujo de potencia, y escribe solo en `results/modules/<módulo>/`. Cuando quieras integrar con la versión principal, abre un Pull Request desde tu branch hacia `main` en GitHub.

**Reglas acordadas**
- Puedes cambiar libremente todo lo que está dentro de tu carpeta: renombrar, dividir en archivos, cambiar el algoritmo.
- Lo único que debe seguir igual es la función `run_integrated` en `runner.py`: mismo nombre y mismos parámetros, y debe devolver `(Profile, tablas)` con los indicadores en `metadata["kpis"]`. La simulación integrada (S0–S4) llama a tu módulo solo a través de esta función. Si necesitas cambiarla, avisa antes al equipo para que la revisemos los tres juntos.
- No modifiques `src/core/`, `src/network/`, `src/integration/` ni las carpetas de los demás sin acordarlo.
- No edites el notebook de otra persona.

**Por dónde empezar**
- Ayrton: población de vehículos independiente, demanda horaria, colas y criterios de selección de barras; después, dimensionamiento EVCS.
- Kenia: dimensionamiento P/E, tarifa, degradación y SOC terminal; después, reemplazar la heurística por despacho optimizado.
- Cesar: disponibilidad, viajes, sesiones y degradación V2G; después, refinar el uso compartido de cargadores.

Guía completa: [docs/collaboration_guide.md](docs/collaboration_guide.md).

---

## Português

Cada integrante trabalha no próprio módulo, sem depender dos outros nem quebrar o que os outros fizeram.

`python -m <módulo>` roda só o seu módulo, com os dados de teste do seu próprio YAML. Ele não precisa dos outros módulos nem do fluxo de potência, e grava só em `results/modules/<módulo>/`. Quando quiser juntar com a versão principal, abra um Pull Request da sua branch para a `main` no GitHub.

**Regras combinadas**
- Pode mudar à vontade tudo que está dentro da sua pasta: renomear, dividir em arquivos, trocar o algoritmo.
- A única coisa que precisa continuar igual é a função `run_integrated` em `runner.py`: mesmo nome e mesmos parâmetros, e ela devolve `(Profile, tabelas)` com os indicadores em `metadata["kpis"]`. É só por ela que a simulação integrada (S0–S4) chama o seu módulo. Se precisar mudar essa função, avise a equipe antes, para os três revisarem juntos.
- Não mexa em `src/core/`, `src/network/`, `src/integration/` nem nas pastas dos outros sem combinar.
- Não edite o notebook de outra pessoa.

**Por onde começar**
- Ayrton: população de veículos independente, demanda horária, filas e critérios de seleção de barras; depois, dimensionamento EVCS.
- Kenia: dimensionamento P/E, tarifa, degradação e SOC terminal; depois, trocar a heurística por despacho otimizado.
- Cesar: disponibilidade, viagens, sessões e degradação V2G; depois, refinar o compartilhamento de carregadores.

Guia completo: [docs/collaboration_guide.md](docs/collaboration_guide.md).
