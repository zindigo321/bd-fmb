import argparse
import csv
import importlib
import json
import os
import sys
import traceback
import unittest
from pathlib import Path
from .evidence import ROOT,load_run,write,result

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--stage',required=True);p.add_argument('--skip-stk',action='store_true');p.add_argument('--probe-fault',choices=['missing','error'])
    args=p.parse_args();ctx,cfg=load_run(args.output);folder=Path(args.output)/args.stage;folder.mkdir(exist_ok=True);checks=[]
    def check(id,ok,required=True,**detail):
        item=dict(id=id,status='PASS' if ok else 'FAIL',required=required,**detail);checks.append(item);write(folder/'progress.json',dict(run_id=ctx['run_id'],lifecycle='running',checks=checks));print(item,flush=True)
    try:
        if args.probe_fault and not ctx['mechanism_test']:raise ValueError('Fault injection requires labelled mechanism test')
        if args.stage=='environment':
            from .environment import environment
            e=environment(cfg,args.skip_stk,args.probe_fault);write(folder/'environment.json',e)
            check('fmb_environment',Path(sys.prefix).name.lower()=='fmb' and e['bits']==64,python=sys.executable)
            if not args.skip_stk:check('stk_detection',e['stk']['status']=='PASS',required=False,probe=e['stk'])
        elif args.stage=='generate':
            from ..constellation import generate,validate
            rows,derived=generate(**cfg['constellation'],constants=cfg['earth']);again,_=generate(**cfg['constellation'],constants=cfg['earth'])
            with (folder/'orbits.csv').open('w',newline='',encoding='utf-8') as f:
                w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
            write(folder/'orbits.config.json',dict(run_id=ctx['run_id'],config_sha256=ctx['config_sha256'],config=cfg,plane_raan_deg=derived['plane_raan_deg'],plane_phase_offset_deg=derived['plane_phase_offset_deg']))
            check('orbits_valid',validate(rows,cfg['constellation']['n'],cfg['earth'])['max_sso_residual_rad_s']<=cfg['tolerances']['sso_rad_s'])
            check('deterministic_generation',rows==again)
            with (folder/'orbits.csv').open() as f:back=list(csv.DictReader(f))
            check('csv_readback',len(back)==cfg['constellation']['n'] and len({r['sat_id'] for r in back})==len(back))
        elif args.stage=='data':
            # Reuse the audited real code; only output into this run's new stage directory.
            sys.path.insert(0,str(ROOT/'tools'))
            from audit_data import main as audit
            audit(folder)
            data=json.loads((folder/'data_audit.json').read_text(encoding='utf-8'))
            for item in data['checks']:check(item['id'],item['status']=='PASS',required=item['id']!='at_description_integrity',reason=item.get('reason',''))
        elif args.stage=='tests':
            os.environ['R2_MECHANISM_EVIDENCE']=str(folder/'cases')
            class Recorder(unittest.TextTestResult):
                def addSuccess(self,test):super().addSuccess(test);check(str(test),True)
                def addFailure(self,test,err):super().addFailure(test,err);check(str(test),False,reason=self._exc_info_to_string(err,test))
                def addError(self,test,err):super().addError(test,err);check(str(test),False,reason=self._exc_info_to_string(err,test))
            suite=unittest.defaultTestLoader.discover(str(ROOT/'tests'))
            unittest.TextTestRunner(verbosity=2,resultclass=Recorder).run(suite)
            write(folder/'mechanism_tests.json',dict(run_id=ctx['run_id'],kind='controlled mechanism tests plus independent unit regressions, not actual STK evidence',cases=checks))
        elif args.stage=='stk':
            from .stk_geometry import run
            run(ctx,cfg,check)
        else:raise ValueError('Unknown stage')
    except Exception as exc:
        traceback.print_exc();check(args.stage+'_exception',False,reason=repr(exc))
    status='PASS' if checks and all(c['status']=='PASS' for c in checks if c['required']) else 'FAIL'
    result(ctx,args.stage,status,checks)
    return 0 if status=='PASS' else 1
if __name__=='__main__':raise SystemExit(main())
