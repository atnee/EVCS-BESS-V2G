# Formulação matemática — API v1

## Convenções e rede
p_inj = p_dis − p_ch. Consumo líquido = −p_inj, em kW. Q segue a mesma convenção: carga indutiva tem q_inj < 0. Cada perfil tem uma linha por intervalo, barra e fase. Energia = Σ P_t Δt, Δt em horas. O estado tem T+1 valores; potências têm T valores. O instante t denota o início do intervalo.
O modelo padrão IEEE123 usa `pandapower.runpp_3ph`, em componentes de sequência. As matrizes trifásicas são aproximadas por Z1 = média(Zii) − média(Zij) e Z0 = média(Zii) + 2 média(Zij). Cargas estrela/delta são assimétricas. Um laço externo aplica S(V) = S_nominal · multiplicador_t · (|V_terminal|/V_nominal)^α, com α = 0 para PQ, 1 para corrente constante e 2 para impedância constante. Capacitores usam α = 2 e não recebem o multiplicador de demanda. Em delta, V_terminal é fase-fase. Taps são fixos e médios por banco. As demais aproximações estão em `data/ieee123/README.md`.

Balanço ativo por intervalo: P_fonte + Σ P_injeções_DER = P_cargas_originais_efetivas + P_perdas_linhas + P_perdas_transformadores. Cargas nativas não aparecem novamente no perfil DER. As perdas incluem fases auxiliares do equivalente pandapower, explicitamente identificadas nas exportações.

O modelo sintético legado, mantido para testes analíticos, usa carga PQ em estrela referida à terra ideal:
I_load,iφ = conjugado(S_load,iφ / V_iφ).
I_branch,iφ = I_load,iφ + Σ I_branch,jφ (jusante).
V_jφ = V_iφ − (R_ij + jX_ij) I_branch,jφ.
Perdas ativas = Σ R_ij |I_ijφ|² / 1000, em kW. Corrente em A, tensão fase-neutro em V. Não há impedâncias mútuas nem caminho explícito de neutro. O desequilíbrio entre potências por fase é preservado, mas o acoplamento físico entre fases está ausente.

## EVCS
P_atendida,t = min(P_solicitada,t, N_carregadores P_carregador, P_conexão).
ENS = Σ(P_solicitada,t − P_atendida,t)Δt.
Esta ENS mede exclusivamente demanda agregada independente rejeitada pela capacidade EVCS, não interrupções da rede nem viagens não atendidas. Não há fila ou modelo de eventos dos veículos independentes.
Na versão inicial Q dos dispositivos novos é zero. A divisão de potência entre as fases declaradas da estação é igual, explícita e não altera as fases das cargas originais.

## BESS
E_(t+1) = E_t + η_ch P_ch,t Δt − P_dis,t Δt/η_dis.
SOC_t = E_t / E_nom. SOC_min ≤ SOC_t ≤ SOC_max.
0 ≤ P_ch,t,P_dis,t ≤ P_nom. P_ch,t P_dis,t = 0, garantido pela representação de potência assinada por intervalo.
O simulador limita comandos inviáveis e retorna a diferença rejeitada. Se SOC final é solicitado, não atendê-lo gera erro. A heurística de redução de pico reserva tempo para recarregar e exige SOC_T = SOC_0. A recarga forçada pode criar outro pico; não há garantia de ótimo.
Throughput AC = Σ|P_inj,t|Δt. Ciclos equivalentes = throughput interno/(2 E_nom), com eficiências na conversão AC–bateria. Custo de degradação = coeficiente por kWh AC × throughput AC.

## V2G
Disponibilidade a_v,t = 1 para chegada ≤ t < saída. Caso contrário P_v,t = 0.
Reserva de partida R_v = max(SOC_partida E_nom, E_viagem + SOC_min E_nom).
Alvo terminal demonstrativo = max(R_v, SOC_inicial E_nom), igual em S1–S4.
O despacho não descarrega abaixo de R_v e preserva tempo de carga para recompor o alvo terminal. Ao sair, cada veículo deve atingir o alvo, ou a simulação falha.
A viagem ocorre após a saída; sua energia não é deduzida durante esta sessão. Não modelar múltiplas viagens com este modelo sem extensão.
Cada veículo conectado reserva um carregador inteiro. Para demanda independente agregada, N_ocupados = teto(P_EVCS/P_carregador).
N_ocupados + N_V2G_conectados ≤ N_carregadores.
P_EVCS + N_V2G_conectados P_carregador ≤ P_conexão (reserva conservadora).
Essas reservas são mais restritivas que Σ|P_v|+P_EVCS ≤ P_conexão e evitam cancelamento artificial entre carga e descarga. IDs da frota e da demanda independente são disjuntos.

## Custos e planejamento futuro
A tarifa demonstrativa constante é 0,15 USD/kWh importado. Exportação não é remunerada. CAPEX é separado do custo diário e não é somado diretamente a ele. Hipóteses sintéticas, sem pretensão de representar Honduras. Degradação V2G, tarifas de demanda, OPEX fixo, desconto e NPV estão pendentes.
Objetivo futuro: min CAPEX anualizado + custo de importação − receita de exportação + degradação + penalidade de energia não atendida, sujeito a fluxo trifásico, limites V/I, potência de conexão, mobilidade e dinâmica de SOC.
Decisões futuras: z_i binária de instalação, número inteiro de carregadores, E_BESS e P_BESS, além de despacho temporal. `optimization.py` sinaliza explicitamente que a otimização não foi implementada.
