#!/usr/bin/env python3
"""V2.4 — coordenação SEQUENCIAL de RegControls + despacho BESS/V2G.

Etapa 1: busca limitada de ajustes PERMANENTES de Vreg (lado secundário do PT)
          e, opcionalmente, de limiares de CapControl tipo VOLTAGE.
Etapa 2: uma reotimização MILP de BESS/V2G sob a política selecionada.
Todos os candidatos são simulados por 24 h no OpenDSS com controles automáticos.

ATENÇÃO: não otimiza taps diretamente, não inventa Q/inversor, não altera Master.dss.
Não certifica instalações, rede BT, N-1 nem todos os limites de equipamentos.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sys
import traceback
import numpy as np
import pandas as pd
from distributed_stochastic import restaurar_viabilidade_v22 as v22
from distributed_stochastic import restaurar_tensao_v23 as v23
from distributed_stochastic._milp_tensao_v23 import make_milp

DT = 1.0


def ensure_paths(repo, baseline, v22_dir, v23_dir, output):
    for p in (repo / 'opendss_rebuild' / 'rebuild' / 'engine.py',
              repo / 'src' / 'v2g' / 'strategies.py',
              baseline / 'S0' / 'voltages.csv',
              v22_dir / 'best' / 'injections_reoptimized.csv',
              v23_dir / 'best' / 'injections_reoptimized.csv',
              v23_dir / 'best' / 'AC' / 'voltages.csv'):
        if not p.is_file():
            raise FileNotFoundError(f'Arquivo obrigatório ausente: {p}')
    if output == v23_dir or output == v22_dir or v23_dir in output.parents or v22_dir in output.parents:
        raise ValueError('Saída V2.4 não pode sobrescrever resultados V2.2/V2.3')


def load_state(repo, baseline, v22_dir, v23_dir):
    """Importa V2.3 sem dupla contagem de frota; V2.2 fornece alocações fixas."""
    state = v23.read_incumbent(repo, baseline, v22_dir)
    best = v23_dir / 'best'
    vehicles = state['team']['vehicles']
    ids = [str(v.id) for v in vehicles]
    old_sites = state['places']
    cars_df = pd.read_csv(best / 'vehicle_dispatch.csv')
    if len(cars_df) != len(ids) * 24 or cars_df.duplicated(['vehicle_id', 'hour']).any():
        raise ValueError('V2.3: vehicle_dispatch não cobre 150 × 24 sem duplicatas')
    sites = cars_df.groupby('vehicle_id').site.first().to_dict()
    if any(sites.get(name) != old_sites[i] for i, name in enumerate(ids)):
        raise ValueError('V2.3: veículos mudaram de estação sem atualização da alocação')
    cars = cars_df.pivot(index='vehicle_id', columns='hour', values='p_kw').reindex(index=ids, columns=range(24))
    if cars.isna().any().any():
        raise ValueError('V2.3: lacunas de despacho individual')
    state['cars'] = cars.to_numpy(float)
    bess_df = pd.read_csv(best / 'bess_dispatch.csv')
    if len(bess_df) != len(state['cap']) * 24 or bess_df.duplicated(['site','hour']).any():
        raise ValueError('V2.3: despacho BESS incompleto')
    state['bess'] = {}
    for site in state['cap']:
        vals = bess_df.loc[bess_df.site == site].set_index('hour').reindex(range(24)).p_kw
        if vals.isna().any():
            raise ValueError(f'Despacho BESS V2.3 incompleto para {site}')
        state['bess'][site] = vals.to_numpy(float)
    profiles = state['build_injections'](state['s1'], state['team']['old_fleet_profile'],
                                         state['bess'], state['cars'], state['places'])
    saved = pd.read_csv(best / 'injections_reoptimized.csv')
    for col in ('bus', 'phase'):
        profiles[col] = profiles[col].astype(str).str.lower() if col == 'bus' else profiles[col].astype(str).str.upper()
        saved[col] = saved[col].astype(str).str.lower() if col == 'bus' else saved[col].astype(str).str.upper()
    keys = ['hour','bus','phase']
    j = profiles.merge(saved, on=keys, suffixes=('_generated','_saved'), how='outer',
                       indicator=True, validate='one_to_one')
    if (j._merge != 'both').any() or any((j[f'{x}_generated'] - j[f'{x}_saved']).abs().max() > 1e-3
                                       for x in ('p_kw', 'q_kvar')):
        raise RuntimeError('Injeções V2.3 não reproduzidas; não continuar')
    state['profiles'] = profiles
    state['ac'] = v22.read_ac_folder(best / 'AC')
    state['score'] = v22.score_ac(state['refs']['S0'], state['ac'])
    logged = json.loads((best / 'ac_score.json').read_text(encoding='utf-8'))
    for k in ('strict_overload_samples','strict_voltage_samples','new_overloaded_lines_vs_s0',
              'thermal_aggravation_samples','voltage_aggravation_samples'):
        if logged[k] != state['score'][k]:
            raise RuntimeError(f'Métricas AC V2.3 divergentes: {k}')
    validate_resources(state['team'], state['bess'], state['cars'], state['places'], state['cap'])
    return state


def validate_resources(team, bess, cars, places, cap):
    """Verifica os 150 VEs, deslocamentos/SOC e BESS, sem aceitar clipping silencioso."""
    from v2g.strategies import fleet_profile
    vehicles = team['vehicles']
    if len(vehicles) != 150 or len(places) != 150 or cars.shape != (150,24):
        raise ValueError('É necessário manter 150 VEs e 24 horas')
    allowed = np.array([17 <= h < 20 for h in range(24)], bool)
    fleet_profile(team['station'], team['grid'], vehicles, cars, team['ind'], (), allowed, True)
    if set(bess) != set(cap):
        raise ValueError('Conjunto de BESS mudou')
    if abs(sum(float(v['power_kw']) for v in cap.values()) - 2200) > 1e-3:
        raise ValueError('Potência total BESS mudou')
    if abs(sum(float(v['energy_kwh']) for v in cap.values()) - 7200) > 1e-3:
        raise ValueError('Energia total BESS mudou')
    for s, p in bess.items():
        p = np.asarray(p, float)
        kw, kwh = float(cap[s]['power_kw']), float(cap[s]['energy_kwh'])
        if p.shape != (24,) or not np.isfinite(p).all() or np.max(np.abs(p)) > kw + 1e-3:
            raise ValueError(f'Potência BESS inválida: {s}')
        energy = .3 * kwh
        for h in range(24):
            energy += .95 * max(-p[h], 0) - max(p[h], 0) / .95
            if not .1 * kwh - 1e-3 <= energy <= .9 * kwh + 1e-3:
                raise ValueError(f'SOC BESS fora do limite: {s} h={h}')
            if h in (17,18,19) and p[h] < -1e-5:
                raise ValueError(f'Recarga BESS na ponta: {s} h={h}')
        if abs(energy - .3*kwh) > 1e-2:
            raise ValueError(f'SOC final do BESS não retorna a 30%: {s}')


def reg_phase(name):
    """Somente nomes compatíveis com o IEEE 8500 observado são classificados."""
    name = str(name).lower()
    m = re.fullmatch(r'(?:feeder_reg|vreg\d+_)([abc])', name)
    return m.group(1).upper() if m else None


def is_cap_voltage(mode):
    """No OpenDSSDirect: 0=current, 1=voltage, 2=kvar, 3=time, 4=PF."""
    if isinstance(mode, (int, np.integer)):
        return int(mode) == 1
    return str(mode).strip().lower() in ('voltage','dsscapcontrolvoltage','1')


def inventory(engine):
    dss = engine.dss
    regs = {}
    for name in engine.regnames:
        if str(name).lower() == 'none':
            continue
        dss.RegControls.Name(name)
        regs[str(name).lower()] = {
            'vreg_v': float(dss.RegControls.ForwardVreg()),
            'band_v': float(dss.RegControls.ForwardBand()),
            'pt_ratio': float(dss.RegControls.PTRatio()),
            'phase_from_name': reg_phase(name),
        }
    caps = {}
    for name in dss.CapControls.AllNames():
        if str(name).lower() == 'none':
            continue
        dss.CapControls.Name(name)
        mode = dss.CapControls.Mode()
        caps[str(name).lower()] = {
            'mode': str(mode), 'voltage_mode': is_cap_voltage(mode),
            'on_v': float(dss.CapControls.ONSetting()),
            'off_v': float(dss.CapControls.OFFSetting()),
            'phase_from_name': reg_phase(name) or (str(name).lower()[-1:].upper()
                  if str(name).lower().endswith(('a','b','c')) else None),
        }
    return {'regulators': regs, 'capcontrols': caps, 'capacitor_names': list(engine.capnames),
            'notes': 'RegControl ForwardVreg e CapControl voltage ON/OFF em volts de PT; não são valores p.u.'}


def policy(name, regs=None, caps=None):
    return {'name': name, 'reg_vreg_offset_v': regs or {}, 'cap_onoff_offset_v': caps or {}}


def build_policies(inv, step, limit, include_caps=False):
    if not 0.0 < step <= 1.0 or limit < 1:
        raise ValueError('Faixa de ajuste inválida')
    regs = inv['regulators']
    ga = sorted(k for k, v in regs.items() if v['phase_from_name'] == 'A')
    gc = sorted(k for k, v in regs.items() if v['phase_from_name'] == 'C')
    options = [policy('original')]
    def add(name, ra=(), rc=(), amp=None, cc=()):
        amp = step if amp is None else amp
        target = {r: float(amp) for r in ra}
        target.update({r: -float(amp) for r in rc})
        cap_targets = {x: -float(step) for x in cc}
        if target or cap_targets:
            options.append(policy(name, target, cap_targets))
    add('fase_A_elevada', ra=ga)
    add('fase_C_reduzida', rc=gc)
    add('A_elevada_C_reduzida', ra=ga, rc=gc)
    add('alimentador_A_C', ra=[x for x in ga if x.startswith('feeder_reg')],
        rc=[x for x in gc if x.startswith('feeder_reg')])
    for group in ('vreg2','vreg3','vreg4'):
        add(f'{group}_A_C', ra=[x for x in ga if x.startswith(group)],
            rc=[x for x in gc if x.startswith(group)])
    # CapControl changes ONLY voltage-mode devices. Never force capacitor states.
    if include_caps:
        candidates = sorted(k for k,v in inv['capcontrols'].items()
                            if v['voltage_mode'] and v['phase_from_name'] == 'C')
        if candidates:
            add('capcontrol_C_mais_sensivel_a_sobretensao', cc=candidates)
            add('reg_A_e_cap_C', ra=ga, cc=candidates)
    seen = set(); unique = []
    for p in options:
        signature = json.dumps((p['reg_vreg_offset_v'],p['cap_onoff_offset_v']),sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        unique.append(p)
    return unique[:limit]


def apply_policy(engine, inv, cfg, *, max_vreg_change_v=1.0, max_cap_change_v=.5):
    dss = engine.dss
    offsets = cfg['reg_vreg_offset_v']; cap_offsets = cfg['cap_onoff_offset_v']
    if not set(offsets).issubset(inv['regulators']):
        raise ValueError('RegControl desconhecido')
    if not set(cap_offsets).issubset(inv['capcontrols']):
        raise ValueError('CapControl desconhecido')
    record = {'policy': cfg['name'], 'regulators': {}, 'capcontrols': {}}
    for name, delta in offsets.items():
        val0 = inv['regulators'][name]['vreg_v']
        if not np.isfinite(delta) or abs(delta) > max_vreg_change_v + 1e-8:
            raise ValueError(f'Ajuste Vreg fora do limite: {name}')
        new = val0 + delta
        # Not a design approval; basic sanity guard against incompatible PT bases.
        if not 95 <= val0 <= 135 or not 95 <= new <= 135:
            raise ValueError(f'Vreg fora da faixa 95..135 V de PT: {name}')
        dss.RegControls.Name(name)
        dss.RegControls.ForwardVreg(float(new))
        actual = float(dss.RegControls.ForwardVreg())
        if abs(actual-new) > 1e-5:
            raise RuntimeError(f'OpenDSS não aplicou Vreg: {name}')
        record['regulators'][name] = {'original_v':val0,'new_v':actual,'delta_v':float(delta)}
    for name, delta in cap_offsets.items():
        data = inv['capcontrols'][name]
        if not data['voltage_mode']:
            raise ValueError('CapControl de tipo não-voltagem: não alterar limiares')
        if not np.isfinite(delta) or abs(delta) > max_cap_change_v + 1e-8:
            raise ValueError(f'Delta de limiar de CapControl excede {max_cap_change_v} V')
        on, off = data['on_v'] + delta, data['off_v'] + delta
        if not (90 <= on < off <= 140):
            raise ValueError(f'Limiar de CapControl inseguro/incompatível: {name}')
        dss.CapControls.Name(name)
        dss.CapControls.ONSetting(float(on))
        dss.CapControls.OFFSetting(float(off))
        if abs(dss.CapControls.ONSetting()-on)>1e-5 or abs(dss.CapControls.OFFSetting()-off)>1e-5:
            raise RuntimeError(f'CapControl não aplicou limiares: {name}')
        record['capcontrols'][name] = {'original_on_v':data['on_v'],'original_off_v':data['off_v'],
                                       'new_on_v':float(on),'new_off_v':float(off)}
    return record


def control_changes(ac):
    r = ac['regulators'].sort_values(['regulator','hour']).copy()
    c = ac['capacitors'].sort_values(['capacitor','hour']).copy()
    tap = r.groupby('regulator').tap.diff().fillna(0)
    cap = c.groupby('capacitor').states.shift(1)
    return {'tap_absolute_steps_total':int(tap.abs().sum()),
            'tap_change_events':int((tap != 0).sum()),
            'capacitor_switch_events':int(((cap.notna()) & (c.states != cap)).sum())}


def voltage_components(s0, ac):
    scores, _ = v23.audit_components(s0, ac)
    return scores


def guard_and_accept(ref_q, ref_c, q, c, min_improvement=.008,
                     thermal_growth=.03, component_growth=.035, max_new_lines=0,
                     ref_ops=None, cand_ops=None):
    """Never certify AC feasibility through aggregate improvement or soft MILP slacks."""
    if q['new_overloaded_lines_vs_s0'] > max_new_lines:
        return False, 'new_distinct_overloaded_lines'
    if q['source_peak_kw'] > ref_q['source_peak_kw'] + 300.:
        return False, 'source_peak_growth_above_300kw'
    if ref_ops is not None and cand_ops is not None:
        # Excessive control operations are a screening red flag, not proof of actual wear.
        if cand_ops['tap_absolute_steps_total'] > ref_ops['tap_absolute_steps_total']*1.25+8:
            return False, 'excessive_tap_steps_vs_reference'
        if cand_ops['capacitor_switch_events'] > ref_ops['capacitor_switch_events']+6:
            return False, 'excessive_capacitor_switching_vs_reference'
    if q['thermal_aggravation_pp_h'] > ref_q['thermal_aggravation_pp_h']*(1+thermal_growth)+.5:
        return False, 'thermal_severity_guard'
    if q['thermal_aggravation_samples'] > ref_q['thermal_aggravation_samples'] + 4:
        return False, 'thermal_sample_guard'
    for side in ('under_rel_puh','over_rel_puh'):
        if c[side] > ref_c[side]*(1+component_growth)+.005:
            return False, f'voltage_component_guard_{side}'
    if q['strict_pass'] and not ref_q['strict_pass']:
        return True, 'AC_absolute_pass'
    if q['relative_s0_pass'] and not ref_q['relative_s0_pass']:
        return True, 'AC_nonaggravation_pass'
    if c['voltage_rel_puh'] <= ref_c['voltage_rel_puh']*(1-min_improvement)-1e-7:
        return True, 'voltage_improvement_with_AC_guards'
    return False, 'insufficient_improvement'


def evaluate(repo, profiles, sites, original_inventory, policy_cfg, out, *, verbose=False):
    ctx, eng = v22.make_engine(repo, profiles, sites)  # always a fresh compilation
    actual_inventory = inventory(eng)
    if actual_inventory['regulators'] != original_inventory['regulators'] or (
       actual_inventory['capcontrols'] != original_inventory['capcontrols']):
        raise RuntimeError('Inventário de controles variou entre recompilações')
    applied = apply_policy(eng, original_inventory, policy_cfg)
    records={k:[] for k in ('branches','voltages','source','regulators','capacitors')}
    for h in range(24):
        solution=eng.solve_hour(h, float(ctx['multipliers'][h]), profiles)
        for k in records: records[k].extend(solution[k])
        if verbose and h in (0,6,12,17,18,19,23):
            print(f'   [OpenDSS] h={h:02d} P={solution["source"][0]["p_kw"]:.1f} kW', flush=True)
    ac={key:pd.DataFrame(value) for key,value in records.items()}
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    for kind,df in ac.items():
        df.to_csv(out/(kind+'.csv'), index=False)
    v22.as_json(out.parent/'controles_aplicados.json', applied)
    return ac


def persist_best(out, state, ac, cfg, q, c, change, *, source):
    best=out/'best';best.mkdir(parents=True,exist_ok=True)
    v22.write_dispatch(best, state['bess'], state['cars'], state['team']['vehicles'],
                       state['places'], state['cap'])
    state['profiles'].to_csv(best/'injections_reoptimized.csv',index=False)
    (best/'AC').mkdir(parents=True,exist_ok=True)
    for name,df in ac.items():df.to_csv(best/'AC'/f'{name}.csv',index=False)
    v22.as_json(best/'ac_score.json',q)
    v22.as_json(best/'voltage_components_v24.json',c)
    v22.as_json(best/'control_policy_v24.json',cfg)
    v22.as_json(best/'control_operations_v24.json',change)
    v22.as_json(best/'provenance_v24.json',{'chosen_from':source,
                'control_policy':cfg['name'],'original_v23_preserved':True})


def compare_replay_full(saved, actual):
    result=v22.compare_replay(saved, actual)
    for kind,key in [('regulators',['hour','regulator']),('capacitors',['hour','capacitor'])]:
        column='tap' if kind=='regulators' else 'states'
        merged=saved[kind][key+[column]].merge(actual[kind][key+[column]],on=key,
             how='outer',indicator=True,validate='one_to_one',suffixes=('_old','_new'))
        if (merged._merge != 'both').any() or (merged[column+'_old'].astype(str) != merged[column+'_new'].astype(str)).any():
            raise RuntimeError(f'V2.4 baseline alterou estados de {kind}; controles/engine divergentes')
    return result


@contextmanager
def temporary_control_policy(inv, cfg):
    """Redirect only v22.make_engine while calibrating/replaying; restore in finally."""
    original=v22.make_engine
    def controlled(repo, profiles, site_caps):
        ctx, engine=original(repo, profiles, site_caps)
        apply_policy(engine, inv, cfg)
        return ctx,engine
    v22.make_engine=controlled
    try: yield
    finally: v22.make_engine=original



def calibrate_isolated_v24(repo, profiles, reference, s0, site_caps, delta, out):
    """Derivadas por hora em circuitos LIMPOS, sem contaminação de controles.

    O v22.calibrate original efetua perturbações ao longo de 24 h em um único
    circuito OpenDSS; embora as perturbações sejam desfeitas em potência, a
    execução extra pode afetar o estado/precisão dos controles ao longo do dia.

    Aqui cada h é reproduzido a partir de Master.dss, com prefixo cronológico
    0..h SEM perturbações. Só depois da checagem da linha de base calculamos
    +delta/-delta com controles congelados. Cada motor é descartado antes da
    próxima hora. Todas as tolerâncias v22 permanecem inalteradas.
    """
    if not np.isfinite(delta) or delta <= 0:
        raise ValueError('delta_kw deve ser positivo e finito')
    from rebuild.engine import error_checked
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    line_keys = v22.screen_keys(s0, reference, 'lines')
    voltage_keys = v22.screen_keys(s0, reference, 'voltages')
    sites = sorted(site_caps)
    expected = {(site, sign) for site in sites for sign in (-1, +1)}
    slopes_l, slopes_v, slopes_src = {}, {}, {}
    actual = {k: [] for k in ('branches','voltages','source','regulators','capacitors')}
    diagnostic = []
    try:
        for h in range(24):
            # NEVER reuse a circuit that has already undergone a perturbation.
            ctx, engine = v22.make_engine(repo, profiles, site_caps)
            base = None
            for t in range(h + 1):
                base = engine.solve_hour(t, float(ctx['multipliers'][t]), profiles)
            assert base is not None
            for key in actual:
                actual[key].extend(base[key])
            row = {'hour': h}
            for table, keys, column, tol in (
                ('branches', v22.KEY_L, 'loading_pct', .03),
                ('voltages', v22.KEY_V, 'v_pu', 2e-5),
                ('source', ['hour'], 'p_kw', 3.),
            ):
                saved_hour = reference[table].loc[reference[table].hour == h, keys + [column]]
                replay_hour = pd.DataFrame(base[table])[keys + [column]]
                joined = saved_hour.merge(replay_hour, on=keys, how='outer',
                                          indicator=True, validate='one_to_one',
                                          suffixes=('_saved', '_replayed'))
                if (joined._merge != 'both').any():
                    raise RuntimeError(f'h={h:02d}: elementos não coincidem em {table}')
                error = float((joined[column+'_saved']-joined[column+'_replayed']).abs().max())
                row[f'{table}_max_abs_error'] = error
                if error > tol:
                    diagnostic.append(row)
                    raise RuntimeError(f'h={h:02d}: replay LIMPO divergente em {table}: '
                                       f'{error:.8g} > {tol}; verificar política/engine, '
                                       'não aumentar tolerância automaticamente')
            for table, key, val in (
                ('regulators', 'regulator', 'tap'),
                ('capacitors', 'capacitor', 'states'),
            ):
                saved_hour = reference[table].loc[reference[table].hour == h, ['hour', key, val]]
                replay_hour = pd.DataFrame(base[table])[['hour', key, val]]
                joined = saved_hour.merge(replay_hour, on=['hour', key], how='outer',
                                          indicator=True, validate='one_to_one',
                                          suffixes=('_saved', '_replayed'))
                if (joined._merge != 'both').any() or not (
                    joined[val+'_saved'].astype(str).equals(joined[val+'_replayed'].astype(str))
                ):
                    diagnostic.append(row)
                    raise RuntimeError(f'h={h:02d}: replay limpo alterou estados de {table}; '
                                       'calibração não executada')
            diagnostic.append(row)
            base_l = {(h, r['line'], r['phase']): r['loading_pct'] for r in base['branches']
                      if (h, r['line'], r['phase']) in line_keys}
            base_v = {(h, r['bus'], r['phase']): r['v_pu'] for r in base['voltages']
                      if (h, r['bus'], r['phase']) in voltage_keys}
            p0 = float(base['source'][0]['p_kw'])
            error_checked(engine.dss, 'Set controlmode=off')
            for site in sites:
                for sign in (+1, -1):
                    injection = v22.inject_hour(profiles, h, site, sign * delta)
                    engine.update_assets(injection)
                    error_checked(engine.dss, 'Solve')
                    if not engine.dss.Solution.Converged():
                        raise RuntimeError(f'Perturbação não convergiu: h={h}, {site}, sign={sign}')
                    pert = engine.read_results(h)
                    scale = sign * delta
                    for point in pert['branches']:
                        k = (h, point['line'], point['phase'])
                        if k in base_l:
                            slopes_l.setdefault(k, {})[(site, sign)] = (
                                point['loading_pct']-base_l[k]) / scale
                    for point in pert['voltages']:
                        k = (h, point['bus'], point['phase'])
                        if k in base_v:
                            slopes_v.setdefault(k, {})[(site, sign)] = (
                                point['v_pu']-base_v[k]) / scale
                    slopes_src.setdefault(h, {})[(site, sign)] = (
                        float(pert['source'][0]['p_kw'])-p0) / scale
                    # Restoring P/Q is necessary inside the hour; it is NOT relied on
                    # for the next hour, because that hour starts from fresh Master.dss.
                    engine.update_assets(profiles.loc[profiles.hour == h])
                    error_checked(engine.dss, 'Solve')
                    if not engine.dss.Solution.Converged():
                        raise RuntimeError(f'Falha ao restaurar potência: h={h}, {site}, sign={sign}')
            print(f'  [V2.4.1] h={h:02d}: replay limpo OK; '
                  f'{len(sites)*2} perturbações isoladas; '
                  f'{len(base_l)} linhas-fase; {len(base_v)} barras-fase', flush=True)
            # Explicitly discard this OpenDSS circuit. The next one recompiles.
            del engine
    finally:
        if diagnostic:
            pd.DataFrame(diagnostic).to_csv(out/'replay_diagnostico_por_hora.csv', index=False)
    real = {k: pd.DataFrame(v) for k, v in actual.items()}
    errors = compare_replay_full(reference, real)
    for title, dataset in [('térmico',slopes_l),('tensão',slopes_v),('fonte',slopes_src)]:
        if any(set(v) != expected for v in dataset.values()):
            raise RuntimeError(f'Derivadas incompletas em {title}')
    pd.DataFrame([{'hour':k[0], 'line':k[1], 'phase':k[2],
                   'site':loc[0], 'direction':loc[1], 'd_loading_pct_per_kw':value}
                  for k, pairs in slopes_l.items() for loc, value in pairs.items()]).to_csv(
                      out/'derivadas_linhas_v22.csv.gz', index=False, compression='gzip')
    pd.DataFrame([{'hour':k[0], 'bus':k[1], 'phase':k[2],
                   'site':loc[0], 'direction':loc[1], 'd_v_pu_per_kw':value}
                  for k, pairs in slopes_v.items() for loc, value in pairs.items()]).to_csv(
                      out/'derivadas_tensoes_v22.csv.gz', index=False, compression='gzip')
    pd.DataFrame([{'hour':h, 'site':loc[0], 'direction':loc[1], 'd_source_kw_per_kw':value}
                  for h, pairs in slopes_src.items() for loc, value in pairs.items()]).to_csv(
                      out/'derivadas_fonte_v22.csv', index=False)
    v22.as_json(out/'calibration_manifest.json', {
        'baseline_replay_errors':errors, 'delta_kw':delta,
        'buses':sites, 'line_constraints':len(slopes_l),
        'voltage_constraints':len(slopes_v),
        'method':'ISOLATED_CHRONOLOGICAL_REPLAY_EACH_HOUR',
        'state_integrity':'REPLAY_BEFORE_PROBES_AND_TAP_CAP_STATE_CHECK',
        'type':'LOCAL_FROZEN_CONTROL_PIECEWISE_NOT_AC_CERTIFICATE',
    })
    return {'lines':slopes_l, 'volts':slopes_v, 'source':slopes_src, 'errors':errors}


def run_refine(repo, baseline, state, out, inv, args):
    best=out/'best'
    if not (best/'control_policy_v24.json').is_file():
        raise FileNotFoundError('Execute --task sweep antes de --task refine')
    cfg=json.loads((best/'control_policy_v24.json').read_text(encoding='utf-8'))
    state['cars'] = read_cars(best, state)
    state['bess'] = read_bess(best, state)
    state['profiles']=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                  state['bess'],state['cars'],state['places'])
    state['ac']=v22.read_ac_folder(best/'AC')
    prior_q=v22.score_ac(state['refs']['S0'],state['ac'])
    prior_c=voltage_components(state['refs']['S0'],state['ac'])
    prior_ops=control_changes(state['ac'])
    trial=out/'refine_01';trial.mkdir(exist_ok=True)
    print('[V2.4] Recalibrando sensibilidade sob política selecionada...',flush=True)
    with temporary_control_policy(inv,cfg):
        slopes=calibrate_isolated_v24(repo,state['profiles'],state['ac'],state['refs']['S0'],
                state['sites'],args.delta_kw,trial/'calibration')
    weights=v23.PROFILES[args.refine_profile]
    try:
        sol=make_milp(state['team'],state['places'],state['cap'],state['bess'],state['cars'],
          state['ac'],state['refs']['S0'],slopes,criterion='relative',
          trust_bess=args.bess_trust_kw, trust_v2g=args.v2g_trust_kw,
          time_limit=args.time_limit_s,mip_gap=args.mip_gap,voltage_weights=weights,
          thermal_budget=float(prior_q['thermal_aggravation_pp_h'])*1.1+8.)
        validate_resources(state['team'],sol['bess'],sol['cars'],state['places'],state['cap'])
        trial_profiles=state['build_injections'](state['s1'],state['team']['old_fleet_profile'],
                         sol['bess'],sol['cars'],state['places'])
        v22.write_dispatch(trial,sol['bess'],sol['cars'],state['team']['vehicles'],
                           state['places'],state['cap'])
        trial_profiles.to_csv(trial/'injections_reoptimized.csv',index=False)
        ac=evaluate(repo,trial_profiles,state['sites'],inv,cfg,trial/'AC',verbose=True)
        q=v22.score_ac(state['refs']['S0'],ac)
        c=voltage_components(state['refs']['S0'],ac)
        ops=control_changes(ac)
        acceptable,reason=guard_and_accept(prior_q,prior_c,q,c,
             max_new_lines=prior_q['new_overloaded_lines_vs_s0'],
             ref_ops=prior_ops,cand_ops=ops)
        report={'status':'AC_ACCEPTED' if acceptable else 'AC_REJECTED', 'reason':reason,
          'ac_score':q,'voltage_components':c,
          'milp_status':sol['solver_message'],'mip_gap':sol['mip_gap'],'solver_objective':sol['objective'],
          'note':'Uma iteração do MILP com controles de setpoint fixos; não é co-otimização global discreta'}
        v22.as_json(trial/'report_v24.json',report)
        if acceptable:
            state.update({'cars':sol['cars'],'bess':sol['bess'],'profiles':trial_profiles})
            persist_best(out,state,ac,cfg,q,c,control_changes(ac),source='refine_01_AC_accepted')
        return report
    except Exception as exc:
        v22.as_json(trial/'error_v24.json',{'error':str(exc),'traceback':traceback.format_exc()})
        raise


def read_cars(best,state):
    frame=pd.read_csv(best/'vehicle_dispatch.csv')
    ids=[v.id for v in state['team']['vehicles']]
    df=frame.pivot(index='vehicle_id',columns='hour',values='p_kw').reindex(index=ids,columns=range(24))
    if df.isna().any().any():raise ValueError('Despacho V2.4 veículos incompleto')
    return df.to_numpy(float)


def read_bess(best,state):
    frame=pd.read_csv(best/'bess_dispatch.csv')
    ans={}
    for s in state['cap']:
        values=frame[frame.site==s].set_index('hour').reindex(range(24)).p_kw
        if values.isna().any():raise ValueError('Despacho V2.4 BESS incompleto')
        ans[s]=values.to_numpy(float)
    return ans


def run_sweep(repo,state,out,inv,args):
    policy_set=build_policies(inv,args.vreg_step_v,args.max_candidates,args.include_capcontrols)
    baseline_cfg=policy_set[0]
    print('[V2.4] Reproduzindo V2.3 com controles originais...',flush=True)
    replay=evaluate(repo,state['profiles'],state['sites'],inv,baseline_cfg,
                    out/'candidates'/'original'/'AC',verbose=True)
    errors=compare_replay_full(state['ac'],replay)
    q0=v22.score_ac(state['refs']['S0'],replay)
    c0=voltage_components(state['refs']['S0'],replay)
    changes0=control_changes(replay)
    persist_best(out,state,replay,baseline_cfg,q0,c0,changes0,source='v23_unchanged')
    v22.as_json(out/'reproducao_v23.json',{'state':'PASS','max_errors':errors,
                          'n_regcontrols':len(inv['regulators']),
                          'n_capcontrols':len(inv['capcontrols'])})
    history=[{'name':'original','status':'REPLAY_PASS','accepted':True,
        'thermal_aggravation_pp_h':q0['thermal_aggravation_pp_h'],
        'voltage_rel_puh':c0['voltage_rel_puh'],
        'under_rel_puh':c0['under_rel_puh'],'over_rel_puh':c0['over_rel_puh'],
        'source_peak_kw':q0['source_peak_kw'],'strict_pass':q0['strict_pass'],
        'relative_s0_pass':q0['relative_s0_pass']}]
    best_q=q0;best_c=c0;best_policy=baseline_cfg
    for index,p in enumerate(policy_set[1:],start=1):
        cand_dir=out/'candidates'/f'{index:02d}_{p["name"]}'
        print(f'[V2.4] Política {index}/{len(policy_set)-1}: {p["name"]}',flush=True)
        try:
            ac=evaluate(repo,state['profiles'],state['sites'],inv,p,cand_dir/'AC')
            q=v22.score_ac(state['refs']['S0'],ac)
            c=voltage_components(state['refs']['S0'],ac)
            ops=control_changes(ac)
            ok,reason=guard_and_accept(q0,c0,q,c,
                max_new_lines=q0['new_overloaded_lines_vs_s0'],
                ref_ops=changes0,cand_ops=ops)
            # Compare every candidate against baseline, then choose best among acceptable.
            better=ok and c['voltage_rel_puh'] < best_c['voltage_rel_puh']-1e-6
            if better:
                best_q,best_c,best_policy=q,c,p
                persist_best(out,state,ac,p,q,c,control_changes(ac),source=p['name']+'_AC_accepted')
            info={'name':p['name'],'status':'AC_ACCEPTED' if ok else 'AC_REJECTED',
              'reason':reason,'accepted':ok,'selected':better,
              'ac_score':q,'voltage_components':c,'control_operations':control_changes(ac),
              'policy':p}
            v22.as_json(cand_dir/'report_v24.json',info)
            history.append({'name':p['name'],'status':info['status'],'accepted':ok,
              'selected':better,'reason':reason,
              'thermal_aggravation_pp_h':q['thermal_aggravation_pp_h'],
              'voltage_rel_puh':c['voltage_rel_puh'],
              'under_rel_puh':c['under_rel_puh'],'over_rel_puh':c['over_rel_puh'],
              'source_peak_kw':q['source_peak_kw'],
              'strict_pass':q['strict_pass'],'relative_s0_pass':q['relative_s0_pass']})
            print(f' [AC] tensão adicional={c["voltage_rel_puh"]:.5f} p.u.-h, '
                  f'térmica={q["thermal_aggravation_pp_h"]:.2f} p.p.-h, aceite={ok}',flush=True)
        except Exception as exc:
            v22.as_json(cand_dir/'error_v24.json',{'error':str(exc),'traceback':traceback.format_exc()})
            history.append({'name':p['name'],'status':'ERROR','error':str(exc)})
            print(f' [FALHOU] {p["name"]}: {exc}',flush=True)
    pd.DataFrame(history).to_csv(out/'politicas_comparadas_v24.csv',index=False)
    final={'stage':'sweep','state':'ABSOLUTE_AC_PASS' if best_q['strict_pass'] else (
            'RELATIVE_S0_AC_PASS_NOT_ABSOLUTE' if best_q['relative_s0_pass'] else 'AC_VIOLATIONS_REMAIN'),
           'chosen_policy':best_policy,'reference_v23':q0,'reference_voltage_components':c0,
           'best_ac':best_q,'best_voltage_components':best_c,
           'number_attempted':len(policy_set),'number_accepted':sum(x.get('accepted',False) for x in history[1:]),
           'notes':['Controles ajustados por setpoint constante em 24 h e reações automáticas.',
                    'Os estados discretos de taps/caps NÃO foram escolhidos livremente.',
                    'Sem evidência de ratings de inversores para suporte de Q; q_kvar de recursos não modificado.',
                    'Perdas, proteção, transformadores, BT e N-1 exigem validação específica.']}
    v22.as_json(out/'final_report_v24.json',final)
    return final


def run(args):
    repo=Path(args.repo).resolve(); baseline=Path(args.baseline).resolve()
    v22_dir=Path(args.v22_dir).resolve();v23_dir=Path(args.v23_dir).resolve()
    out=Path(args.output).resolve()
    ensure_paths(repo,baseline,v22_dir,v23_dir,out)
    sys.path[:0]=[str(repo),str(repo/'src'),str(repo/'opendss_rebuild')]
    state=load_state(repo,baseline,v22_dir,v23_dir)
    out.mkdir(parents=True,exist_ok=True)
    v22.as_json(out/'precheck_v24.json',{'status':'V23_DATA_VALIDATED',
        'v23_ac':state['score'],'bess_sites':list(state['cap']),
        'v2g_sites':sorted(set(state['places'])),'vehicle_count':len(state['cars']),
        'q_injections_unchanged':True})
    print('[V2.4] Reconstituição das injeções V2.3 e contratos de armazenamento: OK.',flush=True)
    if args.task=='precheck':
        return
    ctx,engine=v22.make_engine(repo,state['profiles'],state['sites'])
    inv=inventory(engine)
    v22.as_json(out/'inventario_controles_v24.json',inv)
    print(f'[V2.4] {len(inv["regulators"])} RegControls, {len(inv["capcontrols"])} CapControls.',flush=True)
    if args.task=='inventory':return
    if args.task in ('sweep','all'):
        run_sweep(repo,state,out,inv,args)
    if args.task in ('refine','all'):
        r=run_refine(repo,baseline,state,out,inv,args)
        status=json.loads((out/'final_report_v24.json').read_text(encoding='utf-8')) if (out/'final_report_v24.json').is_file() else {}
        last=v22.score_ac(state['refs']['S0'],v22.read_ac_folder(out/'best'/'AC'))
        status['stage']='all' if args.task=='all' else 'refine'
        status['refine']=r
        status['best_ac']=last
        status['state']='ABSOLUTE_AC_PASS' if last['strict_pass'] else (
            'RELATIVE_S0_AC_PASS_NOT_ABSOLUTE' if last['relative_s0_pass'] else 'AC_VIOLATIONS_REMAIN')
        v22.as_json(out/'final_report_v24.json',status)
    print('[V2.4] Resultado:',out/'final_report_v24.json',flush=True)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo',default='.')
    p.add_argument('--baseline',required=True)
    p.add_argument('--v22-dir',required=True)
    p.add_argument('--v23-dir',required=True)
    p.add_argument('--output',default='results/v24_controles')
    p.add_argument('--task',choices=('precheck','inventory','sweep','refine','all'),default='sweep')
    p.add_argument('--vreg-step-v',type=float,default=.5)
    p.add_argument('--max-candidates',type=int,default=8)
    p.add_argument('--include-capcontrols',action='store_true',
                   help='Sondar limiares de CapControl apenas para modo voltage, sem forçar estados')
    p.add_argument('--delta-kw',type=float,default=100.)
    p.add_argument('--refine-profile',choices=('under','balanced','over'),default='balanced')
    p.add_argument('--bess-trust-kw',type=float,default=150.)
    p.add_argument('--v2g-trust-kw',type=float,default=100.)
    p.add_argument('--time-limit-s',type=int,default=360)
    p.add_argument('--mip-gap',type=float,default=.03)
    args=p.parse_args(argv)
    if not 0 < args.vreg_step_v <= 1 or args.max_candidates<1 or not 0 < args.delta_kw or args.bess_trust_kw<=0 or args.v2g_trust_kw<=0 or args.time_limit_s<=0 or not 0<args.mip_gap<1:
        p.error('Limites numéricos inválidos')
    run(args)

if __name__=='__main__':
    main()
