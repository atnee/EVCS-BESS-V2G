"""OpenDSS elétrico completo, sem alterar elementos da topologia original.

Condições de fronteira: cargas IEEE originais na BT, carregamento multiplicado
por hora, controles do circuito ativos por padrão, injeções externas explicitamente
em barramentos MT físicos, limites térmicos indicados pelo estudo (JSON).
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
import math
import pandas as pd

KV_LN=12.47/math.sqrt(3.0)
NUMBER_PHASE={1:"A",2:"B",3:"C"}


def error_checked(dss, command):
    dss.Text.Command(command)
    number=dss.Error.Number()
    if number:
        raise RuntimeError(f"OpenDSS error {number} for {command!r}: {dss.Error.Description()}")


class ODSSEngine:
    def __init__(self, ctx, mode="original", ampacity="study"):
        from opendssdirect import dss
        self.dss=dss
        self.ctx=ctx
        self.mode=mode
        self.ampacity=ampacity
        self.controls=ctx["settings"]["simulation"]["controls"]
        self.assets={}
        self.capnames=[]
        self.regnames=[]
        self.original_load_count=0
        self._compiled=False

    def compile(self):
        """Recompila de Master.dss; NÃO desativa originais, NÃO reduz a rede."""
        dss=self.dss
        master=Path(self.ctx["files"]["master"]).resolve()
        error_checked(dss, 'Compile "'+master.as_posix()+'"')
        error_checked(dss,"Set mode=snapshot")
        error_checked(dss,"Set algorithm=normal")
        error_checked(dss,"Set maxiterations=100")
        error_checked(dss,"Set maxcontroliter=100")
        if self.controls=="auto":
            error_checked(dss,"Set controlmode=static")
        elif self.controls=="fixed":
            error_checked(dss,"Set controlmode=off")
            error_checked(dss,"BatchEdit RegControl..* enabled=no")
            error_checked(dss,"BatchEdit CapControl..* enabled=no")
        else:
            raise ValueError("simulation.controls deve ser auto ou fixed")
        if self.mode == "original":
            pass  # preserva pu=1.05, delta-Y, vreg de referência e todos os modelos originais
        elif self.mode == "study_source":
            val=float(self.ctx["settings"]["simulation"]["source_pu"])
            if not 0.8<=val<=1.2:
                raise ValueError("source_pu fora da faixa permitida")
            error_checked(dss,f"Edit Vsource.Source pu={val:.8f}")
        else:
            raise ValueError(f"Modo desconhecido: {self.mode}")
        self.original_load_count=len([x for x in dss.Loads.AllNames() if str(x).lower()!="none"])
        self.capnames=[x for x in dss.Capacitors.AllNames() if str(x).lower()!="none"]
        self.regnames=[x for x in dss.RegControls.AllNames() if str(x).lower()!="none"]
        if self.original_load_count<1000:
            raise RuntimeError(f"A rede completa parece não ter carregado os consumidores: {self.original_load_count} loads")
        self._compiled=True

    def define_assets(self, profiles:pd.DataFrame):
        if not self._compiled:
            raise RuntimeError("Primeiro compile()")
        dss=self.dss
        if profiles.empty:
            return
        nonzero=profiles[(profiles.p_kw.abs()>1e-10)|(profiles.q_kvar.abs()>1e-10)]
        group=nonzero.groupby(["bus","phase"],sort=True)
        for n,((bus,ph),block) in enumerate(group):
            if ph not in self.ctx["buses"].get(bus,set()):
                raise ValueError(f"Barra/fase inexistente: {bus}.{ph}")
            idx="%05d"%n
            # Dois elementos com sinais físicos não ambíguos: carga para absorção,
            # gerador para injeção. status=fixed evita aplicação de LoadMult duas vezes.
            load="REBUILD_LOAD_"+idx
            gen="REBUILD_GEN_"+idx
            bus_id=f"{bus}.{ {'A':1,'B':2,'C':3}[ph] }"
            error_checked(dss,(f"New Load.{load} phases=1 bus1={bus_id} conn=wye "
                               f"kv={KV_LN:.8f} kw=0.001 kvar=0 model=1 "
                               f"status=fixed enabled=no"))
            kva=max(1.0,1.25*max(float((block.p_kw**2+block.q_kvar**2).pow(0.5).max()),1.0))
            error_checked(dss,(f"New Generator.{gen} phases=1 bus1={bus_id} conn=wye "
                               f"kv={KV_LN:.8f} kw=0.001 kvar=0 kva={kva:.8f} "
                               f"model=1 enabled=no"))
            self.assets[(bus,ph)]={"load":load,"gen":gen}

    def update_assets(self, row:pd.DataFrame):
        dss=self.dss
        inj=defaultdict(lambda:[0.0,0.0])
        for r in row.itertuples(index=False):
            key=(str(r.bus),str(r.phase))
            inj[key][0]+=float(r.p_kw)
            inj[key][1]+=float(r.q_kvar)
        for key,names in self.assets.items():
            p,q=inj[key]
            # Cargas: -P positivo = consumo; Gerador: +P positivo = injeção.
            # Q segue a mesma convenção (positiva = injeção).
            if p<0 or (abs(p)<1e-9 and q<0):
                if p>0: raise AssertionError("Sinal inconsistente")
                error_checked(dss,f"Edit Load.{names['load']} kw={-p:.12g} kvar={-q:.12g} enabled=yes")
                error_checked(dss,f"Edit Generator.{names['gen']} enabled=no")
            elif p>0 or q>0:
                error_checked(dss,f"Edit Generator.{names['gen']} kw={p:.12g} kvar={q:.12g} enabled=yes")
                error_checked(dss,f"Edit Load.{names['load']} enabled=no")
            else:
                error_checked(dss,f"Edit Load.{names['load']} enabled=no")
                error_checked(dss,f"Edit Generator.{names['gen']} enabled=no")

    def solve_hour(self,hour:int,mult:float,profiles:pd.DataFrame):
        dss=self.dss
        self.update_assets(profiles.loc[profiles.hour==hour])
        error_checked(dss,f"Set loadmult={mult:.10g}")
        # O circuito tem séries sintéticas sem hora absoluta. SNAPSHOT + STATIC
        # executa ações do regulador até estabilizar em cada hora.
        error_checked(dss,"Solve")
        if not dss.Solution.Converged():
            raise RuntimeError(f"OpenDSS não convergiu no horário {hour} (LoadMult={mult})")
        return self.read_results(hour)

    def read_results(self,hour:int) -> dict:
        dss=self.dss
        names=dss.Circuit.AllNodeNames()
        pu=dss.Circuit.AllBusMagPu()
        if len(names)!=len(pu):
            raise RuntimeError("Nomes de nós e tensões possuem tamanhos diferentes")
        allowed=self.ctx["physical_voltage_nodes"]
        vr=[]
        for node,value in zip(names,pu):
            bus,sep,phase=str(node).lower().rpartition(".")
            k=(bus,NUMBER_PHASE.get(int(phase),"") if phase in ("1","2","3") else "")
            if sep and k in allowed and math.isfinite(float(value)):
                vr.append({"hour":hour,"bus":bus,"phase":k[1],"v_pu":float(value)})
        if len(vr)<0.98*len(allowed):
            raise RuntimeError(f"Só {len(vr)}/{len(allowed)} nós físicos MT obtidos. Verificar mapeamento.")
        branches=[]
        line_phases=self.ctx["line_phases"]
        ampacity=self.ctx["ampacity"]
        found=set()
        for name in dss.Lines.AllNames():
            key=str(name).lower()
            if key not in line_phases:
                continue
            if dss.Circuit.SetActiveElement("Line."+str(name))<=0:
                raise RuntimeError("Falha ao ativar linha: "+str(name))
            if not dss.CktElement.Enabled():
                continue
            nodes=list(dss.CktElement.NodeOrder())[:dss.CktElement.NumConductors()]
            currents=list(dss.CktElement.CurrentsMagAng())
            rating=(ampacity[key] if self.ampacity=="study"
                    else float(dss.CktElement.NormalAmps()))
            if rating<=0:
                raise RuntimeError(f"Ampacidade inválida em {key}: {rating}")
            for j,idx in enumerate(nodes):
                ph=NUMBER_PHASE.get(int(idx))
                if ph not in line_phases[key] or 2*j>=len(currents):
                    continue
                current=float(currents[2*j]);
                if not math.isfinite(current):
                    raise RuntimeError("Corrente não finita: "+key)
                branches.append({"hour":hour,"line":key,"phase":ph,
                                 "current_a":current,"ampacity_a":rating,
                                 "loading_pct":100*current/rating})
                found.add((key,ph))
        expected={(line,phase) for line,phases in line_phases.items() for phase in phases}
        if len(found)<0.95*len(expected):
            raise RuntimeError(f"Só {len(found)}/{len(expected)} linhas/fases obtidas. Rever nomes.")
        p,q=dss.Circuit.TotalPower()
        losses_w,_=dss.Circuit.Losses()
        if not all(math.isfinite(x) for x in (p,q,losses_w)):
            raise RuntimeError("Potência/perdas não finitas")
        source=[{"hour":hour,"p_kw":-float(p),"q_kvar":-float(q),
                 "loss_kw":float(losses_w)/1000,"loadmult":float(dss.Solution.LoadMult())}]
        regs=[]
        for name in self.regnames:
            dss.RegControls.Name(name)
            regs.append({"hour":hour,"regulator":str(name).lower(),
                         "tap":int(dss.RegControls.TapNumber())})
        caps=[]
        for name in self.capnames:
            dss.Capacitors.Name(name)
            states=[int(x) for x in dss.Capacitors.States()]
            caps.append({"hour":hour,"capacitor":str(name).lower(),"states":str(states)})
        return {"voltages":vr,"branches":branches,"source":source,"regulators":regs,"capacitors":caps}
