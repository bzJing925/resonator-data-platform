"""Validate audit formulas and completed evidence integrity."""
import json
import numpy as np
import pandas as pd
import skrf as rf
from audit_fl0_qp_20261102 import metrics, ROOT
f=np.linspace(4.99e9,5.01e9,2001); w=2*np.pi*f; c=1e-12
for q in [500,1000,1700]:
    l=1/((2*np.pi*5e9)**2*c); r=q/(2*np.pi*5e9*c)
    z=1/(1/r+1j*(w*c-1/(w*l)))
    n=rf.Network(f=f,s=((z-50)/(z+50)).reshape(-1,1,1),z0=50)
    m,_=metrics(n)
    for k in ['qp','qp_cubic','qp_bandwidth','qp_admittance']:
        assert abs(m[k]/q-1)<.005,(q,k,m[k])
    assert m['negative_r_fraction']==0
    z=1/(-1/r+1j*(w*c-1/(w*l)))
    n=rf.Network(f=f,s=((z-50)/(z+50)).reshape(-1,1,1),z0=50)
    m,_=metrics(n); assert m['negative_r_fraction']>0
print('Analytic parallel RLC Q=500/1000/1700 within 0.5%; active-resistance control flagged.')
if (ROOT/'record_audit.csv').exists():
    d=pd.read_csv(ROOT/'record_audit.csv'); a=pd.read_csv(ROOT/'fl0_curve_supported.csv'); b=pd.read_csv(ROOT/'fl0_quarantine.csv');s=pd.read_csv(ROOT/'structure_summary.csv')
    assert len(d)==3320 and d.row_id.is_unique and len(s)==72
    assert len(a)+len(b)==len(d) and not set(a.row_id)&set(b.row_id)
    assert set(a.row_id)|set(b.row_id)==set(d.row_id)
    assert a.decision.eq('curve_supported').all()
    assert len(list((ROOT/'curves').glob('*.npz')))==len(d[d.decision.ne('unverified')])
    result=dict(analytic_q_relative_tolerance=.005,negative_resistance_control=True,records=len(d),structures=len(s),partition_complete=True,all_curves_saved=True)
    (ROOT/'validation.json').write_text(json.dumps(result,indent=2));print(result)
