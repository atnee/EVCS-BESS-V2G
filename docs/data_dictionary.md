# Contrato de dados v1

| Campo | Unidade / semântica |
|---|---|
| bus/id | Texto, preservar zeros iniciais |
| phases | Subconjunto único de ABC |
| phase | Uma fase A, B ou C por linha do perfil |
| time | ISO8601 com offset; início do intervalo |
| p_kw / q_kvar em Profile | Injeção líquida assinada, positiva para a rede |
| p_kw / q_kvar nas abas loads/load_profiles | Consumo positivo; conversão feita no importador |
| p_kw / q_kvar em generation | Injeção positiva (registro preservado, sem despacho integrado) |
| capacity_kwh | Energia nominal |
| power_kw / charger_kw | Potência nominal AC |
| soc | Fração entre 0 e 1 |
| vn_ln_v | Tensão fase-neutro em V |
| vn_hv_kv / vn_lv_kv | Tensões nominais linha-linha em kV |
| sn_kva | Potência aparente nominal |
| r_ohm / x_ohm | Impedância total por fase do trecho, não por km |
| ampacity_a | Limite por fase em A |
| capex / degradation_per_kwh | Moeda declarada / moeda por kWh AC |
| x / y | Coordenadas opcionais, CRS a documentar |
| tap_pu / setpoint_pu | pu |
| vk_pct / vkr_pct | Percentual, 5 representa 5% |
| status / closed | 0 desligado/aberto; 1 ligado/fechado |
| model | PQ, I ou Z; IEEE123 usa iteração externa sobre o fluxo trifásico pandapower |
| vector_group | Grupo vetorial explícito; não inferido |

`Profile.validate` confere cobertura temporal completa para cada par barra/fase presente, unicidade, finitude e referências. Pares ausentes representam injeção zero. Dados agregados em `combine` não podem repetir asset_id nem vehicle_ids. Os perfis da estação independente e da frota são populações diferentes.

`Network` é a única representação comum. Equipamentos adicionais ficam em `equipment`; preservação não significa validação elétrica ou capacidade de simulação. Não alterar `src/core/` sem revisar todos os consumidores e incrementar o contrato quando incompatível.

Fluxo de uso: `read_parameters` → construção tipada/validação → cenário → `demand_profile`, `peak_shave`, `schedule` → `Profile` → `combine` → `ElectricalSolver.solve` → `evaluate` → CSV/JSON/PNG. O protocolo `OperationalModel` é um ponto de extensão; os módulos atuais expõem funções tipadas.

## Saída IEEE123 / pandapower
`*_injections.csv` contém somente DER adicional (injeção positiva); cargas nativas não são duplicadas nesses perfis. `*_native_loads.csv` contém consumo ativo/reativo efetivo, inclusive capacitores com Q negativo. Cargas delta são contabilizadas por ativo, evitando confundir potência entre fases com potência fase-neutro.

`branches.element_type` distingue linhas de transformadores/reguladores. `physical_phase` identifica fases existentes no dado original; as demais são auxiliares do equivalente pandapower. As perdas de todas as fases entram no balanço do modelo; tensões e indicadores térmicos usam apenas fases físicas. `current_a` dos transformadores é o maior módulo entre terminais HV/LV; `loading_pct` usa as bases nominais de cada terminal. Os 400 A das linhas são uma hipótese, não um limite térmico validado.

## Contrato de saída individual v1
`core/artifacts.py` grava e lê pacotes `profile.csv` + `manifest.json`. O manifesto registra `schema_version=1`, módulo, contexto, `TimeGrid`, `asset_id`, `vehicle_ids`, metadados, hash SHA256 do perfil e hashes dos YAML de entrada. Identificadores de barras são lidos como texto, incluindo zeros iniciais. Alterar o CSV exige gerar novamente o pacote; o leitor rejeita hash incompatível.

Os contratos `Battery` e `Station` estão em `core/assets.py`, sem importação das implementações dos módulos. Isso preserva o contrato de dados v1; os imports públicos anteriores permanecem compatíveis. Pacotes independentes são entradas de teste de cada equipe; não contêm a solução elétrica da rede. Pacotes integrados são separados por cenário e somam exatamente o perfil DER do coordenador.
