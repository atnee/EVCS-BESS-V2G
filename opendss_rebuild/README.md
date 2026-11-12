# OpenDSS Rebuild — IEEE 8500, Kenia + Cesar + integração S0–S4

## Objetivo

Implementação **paralela, não destrutiva**, para reconstruir o fluxo de potência
trifásico completo com OpenDSS e reaproveitar os parâmetros e contratos de dados
da equipe. **Não** usa `pandapower.runpp_3ph` para calcular tensões/correntes.

**Estado atual: versão inicial executável para auditoria, replay e avaliação AC.**
A otimização BESS de Kenia **ainda depende** das sensibilidades antigas quando
`--input modules` é usado. Não interpretar esse resultado como despacho recalibrado.

## Estrutura

```text
EVCS-BESS-V2G/
  configs/                   # originais, NÃO alterados
  data/ieee8500/source/      # rede IEEE original, NÃO alterada
  src/                       # módulos EVCS/BESS/V2G existentes, NÃO alterados
  opendss_rebuild/           # ESTA pasta; independente do backend pandapower
    run.py
    settings.yaml
    requirements.txt
    rebuild/{contracts,profiles,engine,report}.py
    tests/
    results/                 # criado pela execução
```

## Instalação e comandos (PowerShell na raiz do EVCS-BESS-V2G)

```powershell
python -m pip install -r requirements-tested.txt
python -m pip install -r opendss_rebuild/requirements.txt
python -m pip install pytest

# 0. Conferir parâmetros de Kenia, Cesar, fases, hashes e curvas de carga.
python opendss_rebuild/run.py audit

# 1. Teste inicial S0, hora 18 (sem perfis externos).
python opendss_rebuild/run.py run --input none --scenarios S0 --hours 18 --output results/smoke_s0_h18

# 2. Validação do S0 completo, 24 horas (reguladores e capacitores originais).
python opendss_rebuild/run.py run --input none --scenarios S0 --output results/s0_24h

# 3. Reproduzir os 5 perfis com Kenia + Cesar (DESPACHO LEGADO, não recalibrado).
python opendss_rebuild/run.py run --input modules --output results/replay_modulos

# Alternativa: usar perfis CSV exportados da integração original.
python opendss_rebuild/run.py run --input exports --legacy-dados results/ieee8500/integration/dados --output results/replay_exports

# Testes automáticos do código sem simulação.
python -m pytest opendss_rebuild/tests -q
```

**Importante:** o teste isolado `--hours 18` é diagnóstico. Para resultados
cronológicos de controle e de energia, usar todas as 24 horas.

Saídas: `opendss_rebuild/results/<estudo>/summary.csv`, `manifest.json`,
`auditoria_config.json`, `S0/source.csv`, `S0/voltages.csv`, `S0/branches.csv`,
`S0/regulators.csv`, `S0/capacitors.csv`, e equivalentes de S1–S4.
Os arquivos `Sx_new_vs_S0.csv` registram exatamente quais linhas/fases
**novas** violam o limite, sem subtrair contagens de forma incorreta.

## Decisões preservadas da equipe

| Item | Valor configurado |
|---|---|
| Carga original | IEEE 8500 completo, transformadores/ramais secundários presentes |
| Série temporal | 24 horas, multiplicadores sintéticos de `configs/ieee8500.yaml` |
| Eletropostos (S1–S4) | Implantação e sessões de `configs/evcs.yaml` |
| Frota total | 2.850 VEs nos eletropostos públicos + 150 V2G |
| BESS Kenia | 2,2 MW / 7,2 MWh, `l3029502`, SOC inicial 0,30 |
| V2G Cesar | 150 veículos, `cesar_v1`, 17–20h, `m1069504` |
| Estação V2G | 154 portas, 1.694 kW, CAPEX US$ 770.000 |
| Comparações | S0; S1; S2=S1+BESS; S3=S1+V2G; S4=S1+BESS+V2G |
| Limites de tensão | 0,95–1,05 p.u. |
| Ampacidade | Hipóteses do JSON do artigo (`study`) ou original OpenDSS (`original`) |

## Decisões de modelagem e cuidados importantes

- O modo padrão compila **`Master.dss` original** com fonte de **1,05 p.u.**,
  transformador **delta–estrela**, reguladores independentes por fase e
  capacitores controlados. Não usa taps fixos nem matrizes aproximadas.
- `LoadMult` é aplicado às cargas originais e **não** aos equipamentos DER:
  cargas adicionais recebem `status=fixed`, impedindo multiplicação dupla.
- Consumo é registrado com sinal negativo nos perfis Python e modelado por
  `Load`; descarga V2G/BESS com sinal positivo, por `Generator` no OpenDSS.
- Os ativos novos são ligados ao **barramento MT existente**, por fase. O
  transformador de conexão novo de cada eletroposto/BESS ainda não foi modelado.
  Esse detalhe precisa ser tratado antes da submissão.
- A rede IEEE original mantém as cargas em baixa tensão; seus modelos de carga
  dependentes da tensão são preservados. Isso NÃO busca equivalência numérica
  artificial com a redução MT antiga.
- A diferença de bases e controles em relação ao artigo original deve ser
  explicitada em estudo de sensibilidade, não apagada.
- Regras de carregamento de transformadores de serviço e regras de emergência
  N−1 não estão implementadas neste primeiro fluxo. Números só serão
  reportados se houver modelos e limites rastreáveis.
- O critério de sobrecarga é **por corrente de fase**, não uma razão genérica
  agregada trifásica. `line_limit_pct = 100` por padrão.
- Perdas contabilizam todo o circuito via `Circuit.Losses()`; **não** somar
  perdas por fases de linha outra vez.
- Não alterar e não reusar os 54 coeficientes `line_limit` de Kenia para
  emitir conclusões como se eles fossem calibrados neste OpenDSS.

## Próximas etapas científicas (não declaradas implementadas)

1. Rodar S0 e validar que a rede original converge sob controles automáticos.
2. Reproduzir S1 e S4 por 24h com os mesmos perfis (replay): comparar tensões,
   sobrecargas **existentes e novas**, perdas, limites de potência, SOC, filas,
   atendimento e CAPEX.
3. **Recalibrar a otimização de Kenia usando sensibilidades OpenDSS** por
   linha/fase/hora e limites de tensão/transformadores. Reotimizar e verificar
   novamente o fluxo de potência AC, iterativamente.
4. Validar o V2G de Cesar (mobilidade, janela, energia de viagem, SOC,
   potência da estação 1.694 kW e atendimento aos usuários).
5. Ampliar os cenários e sensibilidades 15 min/1h, faixa do regulador,
   tamanhos BESS e incertezas somente após a calibração.
6. Gerar figuras/tabelas finais **apenas de casos convergidos e auditados**.

Para os critérios de reconstrução, ver **[VALIDATION_PROTOCOL.md](VALIDATION_PROTOCOL.md)**.

Esta pasta não substitui os resultados do artigo automaticamente. A aceitação
científica depende dos testes de consistência e da reotimização AC.
