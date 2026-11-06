# S3 — V2G (Cesar)

Para: **Cesar** · De: Ayrton (EVCS, S1) · Branch de referencia: `feature/ieee8500`

Tu escenario es el **S3 = S1 con V2G**. El S1 sobrecarga el alimentador; los autos conectados a la estación pueden devolver energía en la punta y aliviar esa sobrecarga, sin comprometer el próximo viaje de ninguno de ellos. El S4 (EVCS + BESS + V2G) se armará solo cuando el S2 (Kenia) y el S3 estén completos.

Antes de empezar, lee el [tutorial de VS Code y de la IA](tutorial_vscode_ia.md) (en portugués). Tu asistente de IA lee solo las reglas del proyecto en [AGENTS.md](../AGENTS.md).

## 1. El problema que vas a resolver

La red es el **IEEE 8500**: 2.521 barras, 170 km de líneas, 1.177 consumidores, 10,8 MW de carga nominal, ~11 × 12 km. Los detalles y los dos ajustes hechos a los datos IEEE están en [data/ieee8500/README.md](../data/ieee8500/README.md) (en portugués).

El **S1** agrega una flota mixta de **3.000 vehículos eléctricos**:
- 85 % autos particulares, muchos sin garaje;
- 15 % taxis y autos de aplicación.

Recargan en **75 estaciones públicas** (4 hubs DC, 9 electrolineras trifásicas y 62 monofásicas), que piden 27 MWh/día a la red. El modelo está en la [metodología](metodologia.md), sección 5 (en portugués).

Estudio del EVCS, paso de 15 min:

| | S0 (sin estaciones) | S1 (3.000 vehículos) |
|---|---|---|
| Punta en la subestación | 11.750 kW a las 18:30 | **13.946 kW a las 18:00 (+18,7 %)** |
| Demanda de las estaciones | — | hasta 2,1 MW (15 min); ~1,9 MW de 12 h a 15 h y de 18 h a 21 h |
| Línea más cargada | 96,7 % | **108,9 %**, troncal de salida (`ln5513564-1`), de 17 h a 20 h |
| Troncal sur 2/0 ACSR (`ln5835160-1`) | 97 % | 101,6 % |
| Tensión mínima, con los reguladores actuando | 0,951 pu | **0,933 pu** (137 consumidores por debajo de 0,95 pu) |
| Tensión mínima, con los taps fijos en el S0 | 0,951 pu | **0,924 pu** (el efecto de las estaciones solas) |
| Pérdidas en el día | 14,3 MWh | 17,8 MWh (+24 %) |

Hoy la ventana de descarga del V2G es **17 h–21 h** (`discharge_window_h` en `v2g.yaml`), que coincide con la sobrecarga del S1.

## 2. Orden de magnitud de la flota V2G

Hoy `configs/v2g.yaml` tiene **3 autos de 11 kW**, un valor de prueba de la primera fase (una red de 3,5 MW). En el IEEE 8500 no se notan.

| Autos descargando a 11 kW | Potencia devuelta | Energía en 3 h |
|---|---|---|
| 50 | 0,55 MW | ~1,6 MWh |
| 100 | 1,1 MW | ~3,3 MWh |
| 200 | 2,2 MW | ~6,6 MWh |

Para comparar con lo que hay que recortar: llevar la punta de la subestación a 13,0 MW pide ~0,95 MW y ~1,9 MWh; volver al S0, ~2,2 MW y ~7,2 MWh. Esto supone autos con carga suficiente: la energía real depende del SOC de llegada y de la reserva de cada uno para el próximo viaje.

## 3. Dónde se conectan los autos

La estación anfitriona está en la barra `m1125976`, a 4,4 km de la subestación; ahí también está uno de los hubs DC. Hoy tiene **8 puertos bidireccionales de 11 kW (88 kW)**, definidos en el bloque `station:` de `configs/evcs.yaml` (mi archivo).

- **Troncal de salida** (108,9 %): lleva el alimentador entero. El V2G **en cualquier barra** la alivia, incluso en `m1125976`.
- **Troncal sur** (101,6 %): solo se alivia con descarga **aguas abajo de ella**, por ejemplo en `m1069504` (8,7 km).
- **Tamaño de la estación:** entre 50 y 200 autos necesitan muchos más puertos y potencia de conexión que los 88 kW de hoy.

## 4. Lo que ya está listo en el código

| | |
|---|---|
| Código | `src/v2g/`: `model.py` (vehículos, reserva de movilidad, agenda), `availability.py`, `strategies.py` (`heuristic`, `template`) |
| Parámetros | `configs/v2g.yaml`: vehículos (llegada, salida, energía de viaje, SOC), ventana de descarga |
| Contrato con la integración (no cambiar sin acordar) | `v2g.runner.run_integrated(config_dir, grid, evcs_profile, enable_v2g)` |
| Lo que recibes | El perfil del EVCS. La estación viene en `evcs_profile.metadata["station"]` y la demanda que ya usa la estación en `metadata["station_demand_kw"]`, **cada hora**. |
| Cómo armar el resultado | `fleet_profile(...)` con la matriz vehículos × pasos (positivo = descarga). Verifica conexión, potencia, SOC, reserva de movilidad y puertos. |
| Pruebas del módulo | `python -m pytest tests/test_v2g.py tests/test_strategies.py tests/test_module_independence.py -q` (~5 s) |

Las reglas actuales (reserva de salida, meta final, reserva de puertos) y una formulación de optimización propuesta están en la [metodología](metodologia.md), secciones 11.0 y 11.2 (en portugués).

## 5. Tareas

1. **Definir la flota V2G.** Cuántos autos y de qué tipo, con llegada, salida, energía de viaje y SOC mínimo realistas. Los autos particulares estacionados en el trabajo o en casa quedan conectados más tiempo que los taxis.
2. **Acordar conmigo la estación:** número de puertos, potencia de conexión y, si hace falta, otra barra (sección 3). Yo cambio el bloque `station:` de `evcs.yaml`.
3. **Estrategia.** `heuristic` es el punto de partida. Implementa la tuya como una nueva estrategia en `strategies.py`: agenda por vehículo respetando la reserva de movilidad, el SOC y los puertos.
4. **Correr la integración y comparar con el S1:**
   ```powershell
   python -m v2g                                    # módulo solo, sin red (segundos)
   python -m integration.coordinator --output results/ieee8500/integration --frozen-taps   # S0–S4 en la red (~20 min)
   ```
   Tu efecto es la diferencia **S3 − S1** en `results/ieee8500/integration/summary.csv` y en las figuras `S3/impact_*.png` y `S3/network_*.png`.
5. **Escribir el `src/v2g/README.md`,** con el formato del [README del EVCS](../src/evcs/README.md): hipótesis, cómo correr y resultados.

**Atención al paso de tiempo:** la integración es horaria, y el promedio de 1 h suaviza los picos. El pico de las estaciones es de 1,9 MW en 1 h, contra 2,1 MW en 15 min. Los números de la sección 1 son de 15 min.

## 6. Cuándo el S3 está listo

- [ ] El contrato `run_integrated` no cambió, o el cambio se acordó y lo aprobaron los tres.
- [ ] `python -m pytest -q` pasa (~12 min).
- [ ] Los parámetros nuevos están en `v2g.yaml`, cada uno diciendo si es dato (con fuente) o hipótesis.
- [ ] La integración se corrió con `--frozen-taps` y muestra, respecto al S1:
  - punta de la subestación menor;
  - ninguna línea por encima del 100 %;
  - tensión mínima ≥ 0,95 pu;
  - pérdidas iguales o menores.
- [ ] Todos los vehículos salen con la energía garantizada para el próximo viaje.
- [ ] El `src/v2g/README.md` está escrito.

## 7. Decisiones pendientes

| Decisión | Quién |
|---|---|
| Tamaño y barra de la estación anfitriona del V2G | Cesar + Ayrton |
| Si los autos del V2G son parte de los 3.000 de la flota del S1 (y entonces salen de la demanda de las estaciones públicas, para no contar dos veces la misma recarga) o si son una flota adicional | Cesar + Ayrton |
| Paso de la integración: 1 h hoy; 15 min mostraría picos mayores, y el EVCS ya calcula a 15 min | todos |
| Remuneración de la energía devuelta y costo de degradación de la batería del auto | todos; la zonificación en `results/ieee8500/zoneamento/` ya separa consumidores residenciales, comerciales e industriales |

Dudas sobre la red, el S1 o los contratos: habla conmigo (Ayrton).
