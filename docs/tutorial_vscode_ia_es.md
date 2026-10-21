# Trabajar en VS Code con un asistente de IA

Para: **Kenia** (BESS, S2) y **Cesar** (V2G, S3) · De: Ayrton

Este tutorial muestra cómo preparar VS Code y cómo usar un asistente de IA (Claude Code, GitHub Copilot u otro) sin romper el trabajo de los demás.

El repositorio ya trae las reglas para la IA. Están en el archivo [AGENTS.md](../AGENTS.md), en la raíz. Los asistentes leen ese archivo por sí solos:
- **Claude Code** a través de `CLAUDE.md`, que importa `AGENTS.md`;
- **Copilot** mediante la opción `chat.useAgentsMdFile`, que ya viene activada en `.vscode/settings.json`.

Lo que `AGENTS.md` le enseña a la IA:
- qué es el proyecto y qué carpeta es de quién;
- los contratos que no se pueden romper;
- las convenciones de unidades y de signo;
- qué simulaciones tardan;
- cómo probar y cuándo hacer commit.

No necesitas repetir esto en los pedidos.

## 1. Instalar (una vez)

1. **Git** (https://git-scm.com) y **Python 3.12 o más reciente** (https://www.python.org). En Windows, marca "Add python.exe to PATH".
2. **VS Code** (https://code.visualstudio.com).
3. **Asistente de IA**, uno de los dos:
   - **Claude Code:** extensión "Claude Code" de Anthropic;
   - **GitHub Copilot:** extensiones "GitHub Copilot" y "GitHub Copilot Chat". Usa el modo *Agent* del chat.
4. Al abrir la carpeta del proyecto, VS Code sugiere las extensiones recomendadas (Python, Pylance, Jupyter, YAML, Mermaid). Acéptalas.

## 2. Preparar el proyecto (una vez)

En la terminal de VS Code (PowerShell):

```powershell
git clone https://github.com/atnee/EVCS-BESS-V2G.git
cd EVCS-BESS-V2G
git switch feature/bess-kenia          # Cesar: feature/v2g-cesar
git merge origin/feature/ieee8500      # trae el IEEE 8500 y todo lo que ya se hizo
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q
```

Cesar cambia `bess` por `v2g` en los comandos.

**Branch de referencia.** Hoy la versión común más reciente es `feature/ieee8500`, no `main`. Sus branches y `main` todavía están 11 commits detrás de ella. Cuando entre en `main`, les avisamos y pasan a usar `origin/main` en su lugar.

**Ajustes de VS Code.**
- `Ctrl+Shift+P` → **Python: Select Interpreter** → elige el `.venv`.
- Si PowerShell bloquea `Activate.ps1`, ejecuta una vez: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`.
- La pestaña **Testing** (ícono de matraz) muestra y ejecuta las pruebas. La carpeta `.vscode/` del repositorio ya configura pytest, `PYTHONPATH=src` y la carpeta de los notebooks.

## 3. Rutina diaria

```powershell
git switch feature/bess-kenia
git fetch origin
git merge origin/feature/ieee8500      # trae lo que los demás entregaron
python -m bess                         # ejecuta el módulo solo, en segundos
python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q
```

Tu trabajo está en:

| | Kenia | Cesar |
|---|---|---|
| Algoritmo | `src/bess/strategies.py` | `src/v2g/strategies.py` |
| Parámetros | `configs/bess.yaml` | `configs/v2g.yaml` |
| Pruebas | `tests/test_bess.py` | `tests/test_v2g.py` |
| Notebook | `notebooks/02_bess_kenia.ipynb` | `notebooks/03_v2g_cesar.ipynb` |
| Qué entregar | [S2_BESS_Kenia.md](S2_BESS_Kenia.md) | [S3_V2G_Cesar.md](S3_V2G_Cesar.md) |

## 4. Cómo pedirle las cosas a la IA

Trabaja en ciclos cortos, en este orden:

1. **Planificar sin programar.** Pide un plan y léelo antes de aprobarlo.
2. **Implementar un paso pequeño,** con prueba.
3. **Ejecutar las pruebas** y leer la salida.
4. **Revisar la diferencia** (`git diff` o la pestaña Source Control) antes de aceptar.
5. **Commit** cuando estés de acuerdo.

### Pedidos que funcionan bien

**Kenia (BESS).**
> Lee docs/S2_BESS_Kenia.md y docs/metodologia.md (sección 11). Sin escribir código todavía: propón una estrategia de despacho de la BESS que reduzca la punta de las 18–20 h del S1, termine el día con el SOC inicial y respete los límites. Enumera las hipótesis y los parámetros nuevos que irían en configs/bess.yaml, indicando cuáles son datos y cuáles son hipótesis.

> Implementa el plan como la estrategia `kenia_v1` en src/bess/strategies.py, registrada en STRATEGIES, con una prueba en tests/test_bess.py. Ejecuta las pruebas del módulo y muéstrame la salida.

> Explica el diff línea por línea: unidades de cada variable, signo de la potencia y dónde entra el dt_h.

**Cesar (V2G).**
> Lee docs/S3_V2G_Cesar.md y src/v2g/strategies.py. Propón, sin programar, cómo escalar la flota V2G a 50–100 autos con llegada, salida y energía de viaje realistas. Indica qué hay que cambiar en la estación anfitriona (bloque `station:` de configs/evcs.yaml, que es de Ayrton) y qué queda solo en configs/v2g.yaml.

> Implementa la estrategia `cesar_v1` usando fleet_profile, que debe garantizar la energía de salida de todos los vehículos. Ejecuta tests/test_v2g.py y tests/test_strategies.py.

**Los dos.**
> Ejecuta la integración con --frozen-taps en segundo plano y, al terminar, compara S2 (o S3) con S1 en summary.csv: punta, línea más cargada, tensión mínima con y sin los reguladores, pérdidas.

La integración tarda ~15 min. Pídela solo cuando el algoritmo ya pase las pruebas.

### Qué no aceptar de la IA

- Cambios en `src/core/`, `src/network/`, `src/integration/`, `configs/network.yaml`, o en la carpeta de otra persona. La IA debe detenerse y avisar; si no avisa, rechaza el cambio y habla con el grupo.
- Cambios en la firma de `run_integrated` o de la estrategia.
- Pruebas borradas, relajadas o marcadas como `skip` para "pasar".
- Números en el informe que no vengan de un archivo en `results/` o de un cálculo que ella haya mostrado.
- Parámetros ajustados solo para que el resultado se vea bonito, sin justificación.
- Ejecuciones largas (integración, S1, cribado de 1,5 h) que no pediste.
- Commit o push que no pediste, o cualquier `--force`.

### Consejos

- Pídele a la IA que **diga qué verificó y qué no verificó** al final de cada tarea.
- Si la respuesta usa una unidad o un signo extraño, pregunta. Las convenciones están en la sección 11.0 de la [metodología](metodologia.md): inyección positiva entrega energía a la red; potencia en kW; energía en kWh.
- Para dudas sobre el EVCS (demanda de las estaciones, dónde están, por qué el S1 sobrecarga), dirige a la IA a [src/evcs/README.md](../src/evcs/README.md) y a `results/ieee8500/resumo/LEIA-ME.md` (generado localmente).
- Una conversación por tarea. Las conversaciones muy largas pierden el contexto; empieza otra y di dónde te quedaste.

## 5. Resultados

Las salidas quedan en `results/`, fuera de Git: cada uno genera las suyas. Empieza por `results/ieee8500/resumo/` y por el mapa en [results/README.md](../results/README.md).

Para cambiar solo la apariencia de un gráfico, no vuelvas a simular: modifica la función del gráfico y ejecuta `python -m integration.redraw` (~3 min).

## 6. Entregar (Pull Request)

1. `python -m pytest -q`: la suite completa tarda ~12 min (la mayor parte es la prueba de integración) y debe pasar.
2. Limpia las salidas del notebook: **Clear All Outputs** y guardar.
3. Haz commit solo de tus archivos y push de tu branch.
4. En GitHub, abre el Pull Request de tu branch hacia `feature/ieee8500` y completa la plantilla: problema, impacto en los otros módulos, evidencias.
5. Los criterios de "listo" están en la sección 6 de tu documento: [S2_BESS_Kenia.md](S2_BESS_Kenia.md) o [S3_V2G_Cesar.md](S3_V2G_Cesar.md).

## 7. Problemas comunes

| Síntoma | Causa y solución |
|---|---|
| `ModuleNotFoundError: No module named 'bess'` | El paquete no se instaló, o la terminal se abrió antes del `.venv`. Ejecuta `python -m pip install -e ".[dev]"` o `$env:PYTHONPATH="src"`. |
| `FileNotFoundError: configs/...` | El comando se ejecutó fuera de la raíz. Vuelve a la carpeta `EVCS-BESS-V2G`. |
| `Activate.ps1 cannot be loaded` | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Aviso `CRLF will be replaced by LF` | Normal en Windows; se puede ignorar. |
| Conflicto en `git merge` | Archivos tuyos: resuélvelo y ejecuta las pruebas. Archivos compartidos: no elijas un lado por tu cuenta, habla con el grupo. Para desistir: `git merge --abort`. |
| La IA no sigue las reglas | Verifica que la carpeta abierta en VS Code sea la raíz del repositorio (donde está `AGENTS.md`). En Copilot, usa el modo *Agent* y confirma `chat.useAgentsMdFile`. |
| Integración muy lenta | Es normal (~15 min). Cierra otros programas pesados; `--workers N` controla los procesos en los estudios del EVCS. |

Dudas sobre la red, el EVCS o los contratos: hablen conmigo (Ayrton).
