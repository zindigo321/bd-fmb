"""Analytic mechanisms using the actual production chain and a labelled oracle.

The oracle is NOT STK: its intervals follow closed-form polynomial inequalities.
Its purpose is to falsify candidate loss, including two visits <0.01 s apart.
"""
from pathlib import Path
from unittest.mock import patch
import copy, math, time
import numpy as np
from src.round2.evidence import KEYS,write
from .production import search_batch
from .integrity import audit_ledger


def declarations():
    return dict(scope='ANALYTIC_CONSTRUCTED_ONLY',oracle='Closed-form intervals, supplied to Access stub only; never to candidate search',
        cases=['no_sign_change_tangent','two_roots_one_cell','distinct_roots_under_0_01_s','edge_only_no_center',
               'head_clipped','tail_clipped','adjacent_cell_duplicate_sources','negative_empty_query'],
        sampling=[0.,5.,10.,15.],window=[0.,15.],endpoint_tolerance_s=1e-10,
        required='Every analytic interval retained; close events remain distinct; source/query bijection including empty queries',
        unsupported='Arbitrary unresolved subcell oscillations are not certified by cubic fits; fewer than four states raises UNRESOLVED')


def run_mechanisms(ctx,cfg,folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);write(folder/'declaration.json',declarations())
    threshold=600*math.tan(math.radians(cfg['camera']['along_half_deg']))
    target=np.array([0.,0.,cfg['earth']['ellipsoid_a_km']]);basis=np.diag([1.,-1.,-1.])
    cases=[]
    def add(name,fn,intervals,window=(0.,15.),cross=0.):cases.append((name,fn,intervals,list(window),cross))
    a=7.3;radius=math.sqrt(threshold)
    add('no_sign_change_tangent',lambda t:(t-a)**2,[[a-radius,a+radius]])
    def quadratic_case(name,left,right,k):
        mid=(left+right)/2;h=(right-left)/2
        outer=math.sqrt(h*h+threshold/k);inner=math.sqrt(h*h-threshold/k)
        add(name,lambda t:k*(t-left)*(t-right),[[mid-outer,mid-inner],[mid+inner,mid+outer]])
    quadratic_case('two_roots_one_cell',6.8,7.4,2.)
    quadratic_case('distinct_roots_under_0_01_s',7.2,7.205,1e4)
    edge=lambda t:threshold*(.5+(t-a)**2/.02)
    add('edge_only_no_center',edge,[[a-.1,a+.1]])
    add('head_clipped',edge,[[a-.1,a+.1]],(a,15.))
    add('tail_clipped',edge,[[a-.1,a+.1]],(0.,a))
    add('adjacent_cell_duplicate_sources',lambda t:t-5,[[5-threshold,5+threshold]])
    add('negative_empty_query',lambda t:t-a,[],cross=1.5)
    results=[];localcfg=copy.deepcopy(cfg);localcfg['duration_s']=15.
    for name,fn,known,window,cross in cases:
        class Oracle:
            def __init__(self):
                self.ctx=ctx;self.cfg=localcfg;self.cam=localcfg['camera'];self.a=localcfg['earth']['ellipsoid_a_km'];self.b=localcfg['earth']['ellipsoid_b_km']
                self.sat_id='Sat001';self.access_calls=0;self.profile={}
            def coarse_states(self,lo,hi,step,phase):
                # Keep a four-state independent polynomial construction even in
                # clipped cases; trajectory audit belongs to the real fixtures.
                t=np.array([lo,lo+(hi-lo)/3,lo+2*(hi-lo)/3,hi])
                body=np.column_stack([fn(t),np.full(4,600*math.tan(math.radians(cross*self.cam['cross_half_deg']))),np.full(4,600.)])
                p=target-np.einsum('ij,nj->ni',basis,body)
                return t.tolist(),p,np.repeat(basis[None,:,:],4,axis=0),dict(path='CONSTRUCTED_POLYNOMIAL_NO_STK',sha256=None)
            def access(self,fac,w):
                self.access_calls+=1;rows=[]
                for lo,hi in known:
                    lo=max(lo,w[0]);hi=min(hi,w[1])
                    if lo<hi:rows.append(dict(start_epsec=lo,stop_epsec=hi,duration_s=hi-lo))
                return dict(window=w,event_count=len(rows),intervals=rows,elapsed_s=0.,settings_readback={})
        def geometry(*args):return dict(status='PASS',scope='ANALYTIC_ORACLE_ONLY_NOT_REAL_GEOMETRY',medium_access={})
        with patch('src.round7.production.audit_geometry',geometry):
            value=search_batch([Oracle()],[('P',None,target)],window,5.,0.,folder/name,'CONSTRUCTED',name,'MECHANISM_ONLY')
        expected=[(max(lo,window[0]),min(hi,window[1])) for lo,hi in known if max(lo,window[0])<min(hi,window[1])]
        actual=[(r['start_epsec'],r['stop_epsec']) for r in value['raw']]
        ledger=audit_ledger(ctx,'CONSTRUCTED',value['opportunities'],value['candidates'],value['queries'],value['raw'],'MECHANISM_ONLY',cfg['round3']['search']['candidate_half_window_s'],15.)
        ok=actual==expected and ledger['status']=='PASS'
        if name=='negative_empty_query':ok &= len(value['queries'])>=1 and all(q['event_count']==0 for q in value['queries'])
        if name=='adjacent_cell_duplicate_sources':ok &= any(len(c['source_ids'])>=2 for c in value['candidates'])
        results.append(dict(case=name,status='PASS' if ok else 'FAIL',expected=expected,actual=actual,queries=len(value['queries']),
            events=len(actual),source_counts=[len(c['source_ids']) for c in value['candidates']],ledger=ledger,
            old_center_only_would_miss=name in ['no_sign_change_tangent','two_roots_one_cell','distinct_roots_under_0_01_s','edge_only_no_center']))
    result=dict(status='PASS' if all(r['status']=='PASS' for r in results) else 'FAIL',cases=results,declaration=declarations())
    write(folder/'result.json',result);return result
