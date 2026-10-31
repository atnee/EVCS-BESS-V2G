# Prueba 9: ubicación de la BESS en la rama sur

Hipótesis experimental: la barra trifásica `m1069504`, situada aguas abajo
de la troncal sur según `docs/S2_BESS_Kenia.md`, podría aliviar directamente
las tres sobrecargas residuales de la prueba 7. Se conserva **exactamente**
la BESS de 2.200 kW / 7.200 kWh y su despacho de 24 horas, incluido el
SOC inicial y final de 0,30. Solo cambia la barra de `l3029502` a
`m1069504`.

La prueba no cambia `configs/bess.yaml`, los scripts anteriores ni el
coordinador. Guarda las configuraciones efectivas y los resultados S0–S4
en una carpeta nueva, con reguladores activos y taps congelados.

Desde la raíz del repositorio:

```powershell
$env:PYTHONPATH="src"
& .\.venv\Scripts\python.exe -m bess.south_bus_trial9 preview
& .\.venv\Scripts\python.exe -m bess.south_bus_trial9 integrate --output results\ieee8500\south_bus_m1069504_trial9
```

La vista previa verifica que ambos escenarios usan el despacho de la
prueba 7, sin potencia rechazada. La simulación integral debe decidir si
la nueva ubicación elimina las sobrecargas y mantiene tensión mínima de
0,95 pu o mayor. Compare con el `summary.csv` de la prueba 7 y revise
`dados/S2_branches.csv` y `dados/S2_voltages.csv`.

## Resultado observado

La simulación horaria S0–S4 terminó; los 24 pasos de S2 convergieron. En
S2, la troncal sur `ln5835160-1.A` quedó en 72,474 % a las 18:00 y no
superó el 100 % en ninguna hora. La tensión mínima con reguladores activos
fue 0,951580 pu (cero muestras bajo 0,95 pu); con taps congelados fue
0,937321 pu. El pico en la subestación fue 12.771,554 kW, las pérdidas
diarias 17.515,472 kWh y el SOC final 0,30.

Sin embargo, la línea `ln6351510-1` quedó sobrecargada en las tres fases
en varias horas: 19 muestras sobre el 100 % y máximo 128,885 % en la
fase C a las 18:00. En la prueba 7, esa línea estaba en 70,445 % a las
18:00. El cambio de ubicación alivió directamente la rama sur, pero dejó
sin apoyo la rama de `ln6351510-1`. La prueba 9 no cumple el criterio de
cero sobrecargas y muestra un conflicto de ubicación que merece estudiar
dos conexiones BESS o una solución coordinada con el equipo.
