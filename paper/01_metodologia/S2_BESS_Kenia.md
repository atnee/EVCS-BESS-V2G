# S2 — BESS (Kenia)

Para: **Kenia** · De: Ayrton (EVCS, S1) · Branch de referencia: `feature/ieee8500`

Tu escenario es el **S2 = S1 + BESS**. El S1 sobrecarga el alimentador; la batería tiene que aliviar esa sobrecarga y terminar el día con la misma carga con la que empezó. El S4 (EVCS + BESS + V2G) se armará solo cuando el S2 y el S3 (Cesar) estén completos.

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
| Líneas por encima del 100 % | 0 | 37 |
| Tensión mínima, con los reguladores actuando | 0,951 pu | **0,933 pu** (137 consumidores por debajo de 0,95 pu) |
| Tensión mínima, con los taps fijos en el S0 | 0,951 pu | **0,924 pu** (el efecto de las estaciones solas) |
| Pérdidas en el día | 14,3 MWh | 17,8 MWh (+24 %) |

Tu meta es sacar al alimentador de esa situación en la punta de la noche: menos punta, ninguna línea por encima del 100 % y la tensión de vuelta por encima de 0,95 pu.

## 2. Orden de magnitud de la batería

Energía por encima de cada meta de punta en la subestación, en el día del S1. Es una cuenta simple, sin pérdidas ni eficiencia de la batería:

| Meta de punta | Potencia de la BESS | Energía | Ventana |
|---|---|---|---|
| 13,0 MW | ~0,95 MW | ~1,9 MWh | 17 h–20 h 15 |
| 12,5 MW | ~1,45 MW | ~3,7 MWh | 16 h 30–20 h 30 |
| 11,75 MW (vuelta al S0) | ~2,2 MW | ~7,2 MWh | 15 h 45–21 h |

Hoy la BESS de `configs/bess.yaml` tiene **40 kW / 120 kWh**, un valor de prueba de la primera fase (una red de 3,5 MW). En el IEEE 8500 prácticamente no cambia nada; redimensionarla es la primera tarea.

## 3. Dónde conectarla

La barra actual es `m1125976`, a 4,4 km de la subestación; ahí también está uno de los hubs DC.

- **Troncal de salida** (`ln5513564-1`, 108,9 %): lleva el alimentador entero. Una BESS **en cualquier barra** la alivia, incluso en `m1125976`.
- **Troncal sur** (`ln5835160-1`, 101,6 %): solo se alivia con una BESS **aguas abajo de ella**. La barra trifásica más cercana es `m1069504` (8,7 km), con 1.210 barras y 5,0 MW de carga aguas abajo.
- **Recarga de la batería:** también pasa por las troncales y tiene que quedar fuera de la punta.

## 4. Lo que ya está listo en el código

| | |
|---|---|
| Código | `src/bess/`: `model.py` (SOC y límites), `dispatch.py` (recorte de punta), `strategies.py` (`peak_shave`, `template`) |
| Parámetros | `configs/bess.yaml`: batería, `target_kw`, barra de conexión |
| Contrato con la integración (no cambiar sin acordar) | `bess.runner.run_integrated(config_dir, grid, demand_kw)` |
| Lo que recibes | `demand_kw`: demanda **total** del alimentador antes de la BESS (cargas + estaciones + V2G), en kW, **cada hora** |
| Cómo armar el resultado | `simulate(battery, commands_kw, dt_h, final_soc=battery.soc_initial)`, con comando positivo descargando. Aplica los límites de potencia y SOC y verifica el SOC final. |
| Indicadores ya calculados | *Throughput*, ciclos equivalentes, degradación (USD) y CAPEX |
| Pruebas del módulo | `python -m pytest tests/test_bess.py tests/test_strategies.py tests/test_module_independence.py -q` (~5 s) |

Las ecuaciones del modelo actual y una formulación de optimización propuesta (MILP con restricciones de red linealizadas) están en la [metodología](metodologia.md), secciones 11.0 y 11.1 (en portugués).

## 5. Tareas

1. **Dimensionar potencia y energía.** Define el criterio: recortar la punta de la subestación, aliviar la troncal o costo × beneficio. Usa la tabla de la sección 2 como punto de partida.
2. **Elegir la barra** (sección 3).
3. **Estrategia de despacho.** `peak_shave` con `target_kw` fijo es el punto de partida. Implementa la tuya como una nueva estrategia en `strategies.py`; ideas: meta según la curva prevista, tarifa horaria, límite de ciclos.
4. **Correr la integración y comparar con el S1:**
   ```powershell
   python -m bess                                   # módulo solo, sin red (segundos)
   python -m integration.coordinator --output results/ieee8500/integration --frozen-taps   # S0–S4 en la red (~20 min)
   ```
   Tu efecto es la diferencia **S2 − S1** en `results/ieee8500/integration/summary.csv` y en las figuras `S2/impact_*.png` y `S2/network_*.png`.
5. **Escribir el `src/bess/README.md`,** con el formato del [README del EVCS](../src/evcs/README.md): hipótesis, cómo correr y resultados.

**Atención al paso de tiempo:** la integración es horaria, y el promedio de 1 h suaviza los picos. El pico de las estaciones es de 1,9 MW en 1 h, contra 2,1 MW en 15 min. Los números de la sección 1 son de 15 min.

## 6. Cuándo el S2 está listo

- [ ] El contrato `run_integrated` no cambió, o el cambio se acordó y lo aprobaron los tres.
- [ ] `python -m pytest -q` pasa (~12 min).
- [ ] Los parámetros nuevos están en `bess.yaml`, cada uno diciendo si es dato (con fuente) o hipótesis.
- [ ] La integración se corrió con `--frozen-taps` y muestra, respecto al S1:
  - punta de la subestación menor;
  - ninguna línea por encima del 100 %;
  - tensión mínima ≥ 0,95 pu;
  - pérdidas iguales o menores.
- [ ] El SOC final es igual al inicial.
- [ ] El `src/bess/README.md` está escrito.

## 7. Decisiones pendientes

| Decisión | Quién |
|---|---|
| Barra de la BESS, y si necesitamos más de una conexión. Hoy la integración pasa solo la demanda total y acepta una barra; despachar según el flujo de una línea o usar dos BESS exige cambiar el contrato. | Kenia + todos |
| Paso de la integración: 1 h hoy; 15 min mostraría picos mayores, y el EVCS ya calcula a 15 min | todos |
| Costos y tarifas (CAPEX por kWh y por kW, tarifa de energía y de demanda) | todos; la zonificación en `results/ieee8500/zoneamento/` ya separa consumidores residenciales, comerciales e industriales |

Dudas sobre la red, el S1 o los contratos: habla conmigo (Ayrton).
