# BESS — configuración seleccionada de Kenia

> **Actualización S4 (4 nov. 2026):** la estrategia oficial ahora es `line_limit`: tu regla de la línea (pruebas 4–5) escrita como un LP (HiGHS), sin los ajustes manuales de las pruebas 6 y 7, con 54 líneas calibradas (`python -m integration.bess_calibration`). La BESS sigue siendo la tuya: 2.200 kW / 7.200 kWh en `l3029502`, SOC 0,30. El CAPEX pasó a 2.404.800 USD (334 USD/kWh, NREL 2025). `trial7_schedule` queda en `configs/bess.yaml` como referencia. Decisiones y resultados: [docs/S4_integracao.md](../../docs/S4_integracao.md). El texto de abajo describe la prueba 7.

`configs/bess.yaml` selecciona la **prueba 7** para el estudio horario S2.
Usa una BESS de 7.200 kWh y 2.200 kW en `l3029502`, con SOC inicial y
final de 0,30, límites de SOC 0,10–0,90 y eficiencias de carga y descarga
de 0,95. La estrategia `trial7_schedule` reproduce los 24 comandos de
potencia AC medidos en
`results/ieee8500/peak_reserve_l3029502_trial7/modules/bess/S2/dispatch.csv`.
El comando positivo descarga. El horario fijo es una **hipótesis para el
día S1 estudiado**, no un optimizador que se adapte a otras demandas; el
parámetro histórico `target_kw` no se aplica a esta estrategia.

La BESS carga 620 kW por hora entre 00:00 y 06:00, descarga 401 kW a las
16:00 y 1.679 kW a las 18:00. `simulate` valida límites de potencia, SOC
y estado final, y la estrategia rechaza potencia no realizable.

## Cómo ejecutar

Desde la raíz del repositorio, con el entorno Python instalado:

```powershell
$env:PYTHONPATH="src"
& .\.venv\Scripts\python.exe -m bess
& .\.venv\Scripts\python.exe -m integration.coordinator --output results\ieee8500\bess_final_kenia --frozen-taps
```

La integración completa tarda aproximadamente 15–20 minutos. El estudio
original de la prueba 7 ya está en
`results/ieee8500/peak_reserve_l3029502_trial7/`. El script
`src/bess/peak_reserve_trial.py` sirve para reproducir ese despacho con
una carpeta de salida nueva.

## Resultado y límite pendiente

En el S2 horario de la prueba 7, la punta en la subestación bajó de
13.998,581 kW (S1) a 12.786,149 kW, las pérdidas diarias de
17.880,252 a 17.613,006 kWh, y la tensión mínima con reguladores activos
subió de 0,933108 a 0,951719 pu. El SOC terminó en 0,30 sin potencia
rechazada. La carga máxima de línea bajó de 132,082 % a 100,116 %.
Persisten tres tramos de la rama sur por encima del 100 % a las 18:00;
por eso **todavía no se cumple el criterio de cero sobrecargas** de
`docs/S2_BESS_Kenia.md`. Con taps congelados, la tensión mínima fue
0,931256 pu: el resultado de tensión depende de los reguladores.

La prueba 9, conservada como comparación de ubicación, movió el mismo
despacho a `m1069504`: eliminó la sobrecarga de la rama sur, pero elevó
`ln6351510-1.C` hasta 128,885 %. Las pruebas 8 y 10 aumentaron la
descarga a las 18:00 en `l3029502`, pero cambiaron el tap de `vreg3` de
12 a 11 y empeoraron líneas y tensión. Sus resultados históricos siguen
en `results/ieee8500/`; los scripts descartados se retiraron para dejar
una sola configuración activa.
