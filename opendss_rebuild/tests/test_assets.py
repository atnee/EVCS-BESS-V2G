import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pandas as pd
from rebuild.engine import ODSSEngine

class StubError:
    def Number(self): return 0
class StubText:
    def __init__(self):self.commands=[]
    def Command(self,cmd):self.commands.append(cmd)
class StubDss:
    def __init__(self):self.Text=StubText();self.Error=StubError()

def test_asset_load_is_fixed_and_sign_is_preserved():
    obj=ODSSEngine.__new__(ODSSEngine)
    obj.dss=StubDss();obj.ctx={"buses":{"bus0":set("ABC")}}
    obj._compiled=True;obj.assets={}
    prof=pd.DataFrame([{"hour":0,"bus":"bus0","phase":"A","p_kw":-12,"q_kvar":-3},
                       {"hour":1,"bus":"bus0","phase":"A","p_kw":8,"q_kvar":2}])
    obj.define_assets(prof)
    cmd=" ".join(obj.dss.Text.commands)
    assert "status=fixed" in cmd and "Generator.REBUILD_GEN_00000" in cmd
    obj.update_assets(prof[prof.hour==0]);obj.update_assets(prof[prof.hour==1])
    cmd="\n".join(obj.dss.Text.commands)
    assert "kw=12 kvar=3" in cmd
    assert "kw=8 kvar=2" in cmd
