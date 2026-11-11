"""Reproducible FL=0 curve audit; additive quarantine, no source label mutation."""
import io, json, re, sys, zipfile, hashlib, warnings
from pathlib import Path
import numpy as np
import pandas as pd
import skrf as rf
import zipfile_deflate64
from skrf.calibration.deembedding import ShortOpen
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.deembed import match_calibration, DeembedMethod
from app.core.extract import find_resonances, calc_q_phase

ROOT=Path('surrogate_data/optimization_fl0_audit_20261102')
SOURCE=Path('surrogate_data/optimization_p3_20261015/data/records.csv.gz')
ARCHIVE=Path('/Users/jingbozuo/00.PROJECTS/ALN-DATA/data/#2.zip')

def metrics(n):
    f=n.f; z=n.z[:,0,0]; s=n.s[:,0,0]; mag=np.abs(z)
    a,b=find_resonances(mag,f); fp=f[b]; step=np.median(np.diff(f))
    q=calc_q_phase(z,f,a,b)[1]
    phase=np.unwrap(np.angle(z)); qu=abs(fp*np.gradient(phase,f)[b]/2)
    level=mag[b]/np.sqrt(2); left=b; right=b
    while left>0 and mag[left]>level: left-=1
    while right<len(f)-1 and mag[right]>level: right+=1
    bw=np.nan
    if left<b<right and mag[left]<=level and mag[right]<=level:
        lo=np.interp(level,mag[left:left+2],f[left:left+2])
        hi=np.interp(level,mag[right-1:right+1][::-1],f[right-1:right+1][::-1])
        bw=hi-lo
    qbw=fp/bw if bw>0 else np.nan
    # Cubic local derivative avoids systematic wide-window linear-phase flattening.
    qs=[]
    for h in [2,3,4]:
        ids=np.arange(max(0,b-h),min(len(f),b+h+1)); x=(f[ids]-fp)/step
        qs.append(abs(fp*np.polynomial.polynomial.polyfit(x,phase[ids],3)[1]/step/2))
    ids=np.arange(max(0,b-3),min(len(f),b+4)); y=1/z[ids]; x=(f[ids]-fp)/step
    slope=np.polynomial.polynomial.polyfit(x,y.imag,2)[1]/step
    g=np.polynomial.polynomial.polyfit(x,y.real,2)[0]
    qy=fp*slope/(2*g) if g>0 else np.nan
    local=np.abs(f-fp)<=max(3*bw if np.isfinite(bw) else 0,10*step)
    return dict(qp=q,qp_unwrapped=qu,qp_cubic=float(np.median(qs)),qp_cubic_spread=(max(qs)-min(qs))/max(qu,1),qp_bandwidth=qbw,qp_admittance=qy,fp_ghz=fp/1e9,fs_ghz=f[a]/1e9,zp_ohm=mag[b],bw_mhz=bw/1e6,step_mhz=step/1e6,bw_points=bw/step,negative_r_fraction=float(np.mean(z.real[local]<-1e-6)),max_s_local=float(np.max(np.abs(s[local]))),phase_at_fp_deg=np.angle(z[b],deg=True)),(f,z)

def run():
    ROOT.mkdir(parents=True,exist_ok=False); (ROOT/'curves').mkdir()
    records=pd.read_csv(SOURCE); rows=records[records['mode'].eq('deembedded') & records.geometry_eligible & records.fl_um.eq(0)].copy()
    rawlabels=records[records['mode'].eq('raw')].set_index('device_id')
    # Freeze screen before inspecting per-curve results. Review thresholds are not physical absolutes.
    (ROOT/'protocol.json').write_text(json.dumps(dict(n_records=len(rows),negative_r='any below -1e-6 ohm within 3 linewidths',review_bandwidth_ratio=[0.7,1.3],review_cubic_relative=0.2,min_linewidth_samples=5,label_rtol=1e-7,historical_recipe_verified=False),indent=2))
    results=[]; cache={}
    with zipfile.ZipFile(ARCHIVE) as archive:
        names=archive.namelist(); nameset=set(names)
        cal=[n for n in names if n.lower().endswith('.s2p') and ('OPEN' in n.upper() or 'SHORT' in n.upper())]
        calpaths={port:[Path(Path(n).stem+'_'+port+'.s1p') for n in cal] for port in ['S11','S22']}
        def load(member,port):
            key=(member,port)
            if key in cache: return cache[key]
            stream=io.StringIO(archive.read(member).decode('utf-8-sig')); stream.name=member
            n=rf.Network(stream); p=0 if port=='S11' else 1
            one=rf.Network(frequency=n.frequency,s=n.s[:,p,p].reshape(-1,1,1),z0=n.z0[:,p])
            if member in cal: cache[key]=one
            return one
        for i,row in enumerate(rows.itertuples()):
            item=row._asdict(); item.pop('Index'); item['source_qp']=row.qp
            try:
                parent=re.sub(r'^S(?:11|22)_','',row.device_id); parent=re.sub(r'\.s1p$','.s2p',parent)
                assert parent in nameset
                dutpath=Path(Path(parent).stem+'_'+row.port+'.s1p')
                op,sh=match_calibration(dutpath,calpaths[row.port],DeembedMethod.ORIGINAL)
                item.update(parent=parent,parent_crc=archive.getinfo(parent).CRC,open=op.name,short=sh.name)
                nets=[load(parent,row.port),load(op.name.rsplit('_S',1)[0]+'.s2p',row.port),load(sh.name.rsplit('_S',1)[0]+'.s2p',row.port)]
                assert all(np.array_equal(nets[0].f,n.f) and np.array_equal(nets[0].z0,n.z0) and np.isfinite(n.s).all() for n in nets)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore'); de=ShortOpen(dummy_short=nets[2],dummy_open=nets[1]).deembed(nets[0])
                assert np.isfinite(de.s).all()
                rm,rc=metrics(nets[0]); dm,dc=metrics(de)
                for prefix,m in [('raw',rm),('de',dm)]: item.update({prefix+'_'+k:v for k,v in m.items()})
                item['label_match']=bool(np.isclose(dm['qp'],row.qp,rtol=1e-7,atol=1e-7))
                item['raw_label_qp']=rawlabels.loc[row.device_id,'qp'] if row.device_id in rawlabels.index else np.nan
                item['raw_label_match']=bool(np.isclose(rm['qp'],item['raw_label_qp'],rtol=1e-7,atol=1e-7))
                reasons=[]
                if not item['label_match']: reasons.append('label_not_reproduced')
                if dm['negative_r_fraction']>0: reasons.append('local_nonpassive')
                if dm['bw_points']<5 or not np.isfinite(dm['bw_points']): reasons.append('linewidth_underresolved')
                if abs(dm['qp_unwrapped']/max(dm['qp'],1)-1)>.01: reasons.append('phase_wrap')
                if abs(dm['qp_cubic']/max(dm['qp'],1)-1)>.2 or dm['qp_cubic_spread']>.2: reasons.append('phase_derivative_unstable')
                if not .7<dm['qp_bandwidth']/max(dm['qp'],1)<1.3: reasons.append('bandwidth_disagrees')
                item['decision']='review' if reasons else 'curve_supported'; item['reasons']=';'.join(reasons)
                np.savez_compressed(ROOT/'curves'/f'{row.row_id.replace(":","_")}.npz',f=dc[0],raw_z=rc[1],de_z=dc[1])
            except Exception as e: item.update(decision='unverified',reasons=f'{type(e).__name__}: {e}')
            results.append(item)
            if (i+1)%200==0: print(i+1,'/',len(rows),flush=True)
    df=pd.DataFrame(results); df.to_csv(ROOT/'record_audit.csv',index=False)
    df[df.decision.eq('curve_supported')].to_csv(ROOT/'fl0_curve_supported.csv',index=False)
    df[~df.decision.eq('curve_supported')].to_csv(ROOT/'fl0_quarantine.csv',index=False)
    summary=[]
    for sid,g in df.groupby('structure_id'):
        ok=g[g.decision.eq('curve_supported')]; first=g.iloc[0]
        summary.append(dict(structure_id=sid,mark=first.mark,port=first.port,eg_um=first.eg_um,area_um2=first.area_um2,has_pf=first.has_pf,n=len(g),supported=len(ok),review=len(g)-len(ok),original_qp=g.groupby('coord').source_qp.median().median(),screened_qp=ok.groupby('coord').source_qp.median().median(),raw_qp=g.groupby('coord').raw_qp.median().median(),bandwidth_qp=g.groupby('coord').de_qp_bandwidth.median().median()))
    pd.DataFrame(summary).to_csv(ROOT/'structure_summary.csv',index=False)
    # Matched position comparison: retain all measurements; no causal attribution.
    all_de=records[records['mode'].eq('deembedded') & records.geometry_eligible & records.valid_qp]
    keys=['eg_um','area_um2','has_pf','cohort','port','coord']
    zero=all_de[all_de.fl_um.eq(0)].groupby(keys,dropna=False).qp.median().rename('qp_fl0').reset_index()
    framed=all_de[all_de.fl_um.gt(0)].groupby(keys+['fl_um'],dropna=False).qp.median().reset_index()
    pairs=framed.merge(zero,on=keys,validate='many_to_one'); pairs['ratio_frame_over_fl0']=pairs.qp/pairs.qp_fl0
    pairs.to_csv(ROOT/'matched_frame_comparison.csv',index=False)
    pd.DataFrame([dict(fl_um=fl,n_pairs=len(g),median_ratio=g.ratio_frame_over_fl0.median(),fraction_frame_higher=g.ratio_frame_over_fl0.gt(1).mean()) for fl,g in pairs.groupby('fl_um')]).to_csv(ROOT/'matched_frame_summary.csv',index=False)
    receipt=dict(source=str(SOURCE.resolve()),source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),archive=str(ARCHIVE),archive_size=ARCHIVE.stat().st_size,script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),decisions=df.decision.value_counts().to_dict(),labels_modified=False,model_retrained=False,historical_recipe_verified=False)
    (ROOT/'receipt.json').write_text(json.dumps(receipt,indent=2)); print(receipt,flush=True)
if __name__=='__main__': run()
