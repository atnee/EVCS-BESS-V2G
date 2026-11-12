OpenDSS Rebuild V0.6 — teste da interação elétrica BESS + V2G
============================================================

Instalação: extraia o ZIP NA RAIZ do projeto EVCS-BESS-V2G. Ele adiciona
opendss_rebuild/calibrar_interacao_bess_v2g.py e não substitui a base atual.

Pré-requisitos: patches V0.4.1 e V0.5 instalados. S0/S1 24h e duas pastas de
calibração individual de 200 kW às 17h, 18h e 19h.

Comando (PowerShell, da raiz do projeto):

python .\opendss_rebuild\calibrar_interacao_bess_v2g.py --results .\results\s0_s1_opendss_24h --bess-dir .\results\calibracao_bess_odss_ponta --v2g-dir .\results\calibracao_v2g_odss_ponta --hours 17 18 19 --bess-kw 200 --v2g-kw 200 --output .\results\interacao_bess_v2g_200_200

Saídas:
- baseline_replay_check.csv: reprodução cronológica de S1.
- resumo_interacao.csv: contagens e verificação do balanço de potência.
- superposicao_linhas.csv: modelo linear de dois recursos versus resultado AC conjunto.
- superposicao_tensoes.csv: idem para tensão em barras/fases físicas.
- manifest.json: proveniência e ressalvas.

ATENÇÃO: é um ensaio de PERTURBAÇÃO CONJUNTA, NÃO uma solução operacional
admissível. Os estados de reguladores e capacitores são congelados depois
de reproduzir o S1 de cada hora. O ensaio NÃO usa nem valida SOC da frota,
viagens, conectividade, estratégias finais, corrente dos ramais BT, nem
controle automático sob a dupla perturbação.

Próximo marco: construir otimização temporal multi-recurso (SOC BESS, restrições
individuais de Cesar e limites da estação), verificar S2/S3/S4 em OpenDSS com
automação de reguladores/capacitores e caracterizar violações existentes S0.
