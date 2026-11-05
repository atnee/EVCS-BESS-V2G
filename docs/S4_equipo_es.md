# S4 — qué cambió y qué sigue (Kenia y Cesar)

El S4 junta las tres partes: las estaciones del S1 (Ayrton), la BESS de Kenia y el V2G de Cesar, en una sola configuración oficial (`configs/`). La integración calcula S0, S1, S2, S3 y S4 en la misma corrida. El detalle técnico, en portugués, está en [S4_integracao.md](S4_integracao.md).

## Resultado

Corrida oficial `results/ieee8500/integration/` (4 nov. 2026, paso de 1 h):

| Escenario | Qué incluye | Líneas nuevas > 100 % | Tensión mínima (reguladores / taps fijos del S0) | Criterio |
|---|---|---|---|---|
| S1 | estaciones, sin mitigación | 49 (hasta 110,2 %) | 0,938 / 0,915 pu | no cumple |
| S2 | S1 + BESS | 8 (tronco sur, fuera del alcance de la BESS) | 0,940 / 0,928 pu | no cumple |
| S3 | S1 + V2G | 21 (tronco de salida, hasta 103,5 %) | 0,946 / 0,931 pu | no cumple |
| **S4** | **S1 + BESS + V2G** | **0** | **0,951** / 0,932 pu | **cumple** |

**Criterio:** ninguna línea que estaba hasta 100 % en el S0 pasa de 100 %, y la tensión mínima con los reguladores actuando queda en 0,95 pu o más. Las líneas ya sobrecargadas en el S0 (`ln6351510-1`, 108,7 % en el S0, 117,7 % en el S4) se reportan aparte. ANSI C84.1 y el límite de 100 % son referencias del estudio, no la norma de Honduras.

**Por qué solo el S4 cumple.** Cada recurso alcanza un tramo distinto de la red: la BESS en `l3029502` alivia el tronco de salida y el ramal #4; el V2G en `m1069504` alivia el tronco de salida y el tronco sur. Ninguno solo llega a los tres grupos de líneas.

## Decisiones acordadas (4 nov. 2026)

| Tema | Decisión |
|---|---|
| Flota V2G | 150 autos **dentro** de los 3.000: 2.850 públicos + 150 V2G |
| Estación V2G | `m1069504`, 154 puertos, 1.694 kW, 770.000 USD (hipótesis: 5.000 USD por puerto) |
| V2G | `cesar_v1`, descarga 17–20 h, sin carga 17–22 h, salida con energía de llegada + viaje |
| BESS | 2.200 kW / 7.200 kWh en `l3029502`, SOC 0,30 |
| CAPEX de la BESS | 2.404.800 USD: 334 USD/kWh × 7.200 kWh (NREL/TP-6A40-93281, 2025; sistema de 4 h, USD de 2024, costos de EE. UU.) |
| Despacho de la BESS | estrategia `line_limit` (ver abajo) |
| Criterio de éxito | el de arriba |

## Kenia — qué cambió en la BESS

- **Tu BESS se mantiene:** 2,2 MW / 7,2 MWh en `l3029502`, SOC 0,30.
- **Estrategia oficial `line_limit`** (`src/bess/strategies.py`). Es tu regla de las pruebas 4–5 (descargar lo justo para limitar la línea, con la sensibilidad medida en el S1), escrita como un LP con HiGHS, **sin los ajustes manuales de las pruebas 6 y 7**. El contrato de la BESS solo entrega la demanda del alimentador, así que cada línea se modela como:
  - carga antes de la BESS = `a + b × demanda`;
  - efecto de la BESS = `− s × inyección` (positivo descarga);
  - límite de la línea = 100 % − su mayor error de ajuste.
- **Calibración** (`python -m integration.bess_calibration <corrida>`): calcula `a`, `b` y `s` de una corrida guardada. Resultado: 54 líneas, R² de 0,978 a 0,999; en el ramal #4, `s` = 0,033 % por kW (tú mediste 0,038). Los valores están en `configs/bess.yaml` → `line_limit.lines`. **Hay que recalibrar si cambia el S1** (flota, estaciones o curva de carga).
- **CAPEX:** pasó de 50.000 USD (era el de la BESS de 40 kW) a 2.404.800 USD, con fuente.
- **Lo que queda como estaba:** `trial7_schedule` sigue en `bess.yaml` como referencia. También siguen tus scripts `peak_reserve_trial.py` y `south_bus_trial9.py`, y los tests en `src/bess/`.
- **Diferencia a tener en cuenta:** `line_limit` solo mira las líneas, no la tensión. En la corrida de calibración, tu prueba 7 dio mejor tensión en el S2 (0,949 contra 0,940 pu), porque la ajustaste también para la tensión.

**Pendiente para ti:**
1. Revisar `line_limit` y la calibración (`docs/S4_integracao.md`, sección de la estrategia).
2. Los tests de `src/bess/test_*.py` leen `results/`, que no está en Git, y `pytest` no los corre porque están fuera de `tests/`. Muévelos a `tests/test_bess.py` sin depender de `results/`, o bórralos.

## Cesar — qué cambió en el V2G

- **Tu configuración del S3 pasó a ser la oficial.** `configs/v2g.yaml` ya tiene `strategy: cesar_v1`, `vehicle_source: fleet`, `departure_rule: arrival_plus_trip`, ventana 17–20 h y la estación en `m1069504` con 154 puertos. `configs/evcs.yaml` tiene la misma estación y `integration.evs: 2850`. El S3 y el S4 de la corrida oficial usan tu V2G real; ya no hace falta la carpeta generada por `s3_configs`.
- **Variante de comparación:** como la antigua variante B ahora es la oficial, el bloque `s3_case.variants` tiene una variante **A**, con la estación de vuelta en `m1125976`.
- **Tests ajustados:** tres tests de `tests/test_v2g.py` y uno de `tests/test_integration.py` fijaban la configuración oficial anterior (flota de ejemplo de 3 autos, heurística, variante B). Ahora verifican lo mismo con la configuración nueva:
  - la matriz exacta de la heurística, con la flota de ejemplo y la ventana 17–21 h;
  - la regla de salida `arrival`;
  - la variante A cambia solo la barra;
  - cada auto sale con la misma energía en S1–S4 (tolerancia de 1e-5 kWh por el margen de tu LP) y nunca por debajo del objetivo.
- **Probamos mover la estación** a `m1047521`, después del VREG2, para mejorar la tensión. Subió solo 0,002 pu, así que la estación quedó en `m1069504` (detalle en `docs/S4_integracao.md`).

**Pendiente para ti:** revisar la nota que agregamos al inicio de `src/v2g/README.md`. El resto del README describe el estado del 30 de octubre.

## La margen de tensión

El S4 cumple con 0,951 pu, solo 0,001 pu por encima del límite. No falta BESS ni V2G: con los taps fijos del S0, las dos suben la peor barra (`l3065750`, a 14 km) de 0,915 a 0,942 pu. Con los reguladores actuando, la carga menor sube la tensión a la salida del VREG2, que **baja 2 taps** y devuelve parte de esa ganancia al final del alimentador. En el modelo, el regulador controla la tensión en su propio terminal, sin compensación de caída en la línea (LDC). Modelar la LDC queda como trabajo futuro, porque cambia todos los escenarios, también el S0. En el artículo hay que mostrar las dos visiones.

## Cómo actualizar su branch

Después de que el PR de `feature/s4` entre en `main`:

```powershell
git switch feature/bess-kenia        # Cesar: feature/v2g-cesar
git fetch origin
git merge origin/main
python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q   # Cesar: test_v2g.py
```

La versión común del proyecto ahora es `main` (no `feature/ieee8500`).

## Cómo reproducir

```powershell
$env:PYTHONPATH="src"
python -m integration.coordinator --output results/ieee8500/integration --frozen-taps   # ~20 min; S0-S4 y resumo
python -m integration.bess_calibration results/ieee8500/s4_calibration                  # segundos; solo para recalibrar la BESS
```

Resumen de una página con la tabla del criterio: `results/ieee8500/resumo/LEIA-ME.md`.

## Limitaciones

- Paso de 1 h: el estudio del EVCS a 15 min muestra puntas mayores.
- Una sola semilla para la flota y las sesiones.
- Costos: CAPEX de la BESS con costos de EE. UU. y duración de 4 h (esta BESS es de 3,3 h); estación V2G por puerto; tarifas de Cesar (CREE y BCH, estructura de Moreira et al.).
- No se modelan los transformadores de la BESS (2,2 MW) ni de la estación V2G (1,7 MW).
- El IEEE 8500 no se validó contra el OpenDSS; no hay datos de Honduras además de la tarifa.
