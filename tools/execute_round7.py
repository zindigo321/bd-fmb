"""Capture true command completion; never impose an outer STK process kill."""
import datetime,json,os,subprocess,sys,time,uuid,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT,read,write,files_manifest,digest,utc
from tools.verify_round7 import verify


def audit_files(out):
    invalid=[];expected_corrupt=[];encoding=[]
    for p in out.rglob('*'):
        if not p.is_file() or p.suffix not in ['.json','.md','.csv','.txt']:continue
        try:
            b=p.read_bytes();s=b.decode('utf-8-sig')
            if p.suffix=='.json':json.loads(s)
            if '\ufffd' in s:encoding.append(str(p))
        except (UnicodeError,ValueError) as e:
            exact=(p.name=='result.json' and p.parent.name=='probe' and p.parent.parent.name=='corrupt' and p.read_bytes()==b'{bad JSON'
                and p.parent.parent.parent.name.startswith('test_missing_corrupt_timeout_cannot_pass_')
                and read(p.parent.parent/'run.json').get('mechanism_test') is True)
            (expected_corrupt if exact else invalid).append(dict(path=str(p),error=repr(e)))
    return dict(status='PASS' if not invalid and not encoding and len(expected_corrupt)==1 else 'FAIL',invalid_json_or_encoding=invalid,expected_deliberate_bad_json=expected_corrupt,replacement_characters=encoding)


def main():
    if Path(sys.executable).resolve()!=Path(r'D:\conda_envs\fmb\python.exe').resolve():raise RuntimeError('fmb interpreter required')
    base=(ROOT/'work/round7/latest_preflight.txt').read_text(encoding='utf-8')
    stamp=datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%dT%H%M%S')
    out=Path(r'G:\gpt-file\Codex\outputs\bd-fmb\round7')/(stamp+'_'+uuid.uuid4().hex[:8])
    argv=[sys.executable,'-X','utf8',str(ROOT/'tools/run_round7.py'),'--baseline',base,'--output',str(out)]
    print('OUTPUT',out,flush=True);started=utc();start=time.perf_counter()
    p=subprocess.run(argv,cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1'))
    command=dict(argv=argv,started=True,completed=True,started_utc=started,completed_utc=utc(),exit_code=p.returncode,elapsed_s=time.perf_counter()-start,external_timeout=False,outer_process_tree_kill=False)
    write(out/'command_result.json',command)
    if (out/'final_status.json').exists():
        f=read(out/'final_status.json');f['command'].update(command);write(out/'final_status.json',f)
    numeric=verify(out);write(out/'independent_numeric_review.json',numeric)
    encoding=audit_files(out);write(out/'delivery_encoding_audit.json',encoding)
    prior=Path(read(out/'config.json')['config']['round7']['source_round6']);checks=[]
    for item in read(prior/'artifact_manifest.json'):
        path=Path(item['path']);path=path if path.is_absolute() else prior/path
        try:b=path.read_bytes();status='MATCH' if digest(b)==item['sha256'] else 'MISMATCH';error=None
        except OSError as e:status='MISSING' if isinstance(e,FileNotFoundError) else 'UNREADABLE';error=repr(e)
        checks.append(dict(path=str(path),status=status,error=error))
    write(out/'round6_manifest_authorized_scope.json',dict(scope='Current round7 authorized execution context; separate from preflight ordinary reads',files=checks,counts={s:sum(x['status']==s for x in checks) for s in ['MATCH','MISMATCH','MISSING','UNREADABLE']}))
    ctx=read(out/'run.json');write(out/'log_manifest.json',files_manifest(Path(ctx['work']).glob('*.log')))
    write(out/'artifact_index.json',dict(report='audit_report.md',contract='metrics_contract.md',baseline_erratum='baseline/round6_erratum.md',gate='main_launch_gate.json',
        actual_command='command_result.json',status='final_status.json',budgets='cumulative_stk_budget.json',numeric_review='independent_numeric_review.json',source_paths='input_manifest.json'))
    manifest=files_manifest(x for x in out.rglob('*') if x.is_file() and x.name not in ['artifact_manifest.json','delivery_verification.json'])
    write(out/'artifact_manifest.json',manifest)
    matched=sum(Path(x['path']).stat().st_size==x['bytes'] and digest(Path(x['path']).read_bytes())==x['sha256'] for x in manifest)
    ok=matched==len(manifest) and encoding['status']=='PASS' and (numeric['status']=='PASS' if p.returncode==0 else True)
    write(out/'delivery_verification.json',dict(status='PASS' if ok else 'FAIL',scope='authorized current execution',manifest_sha256=digest((out/'artifact_manifest.json').read_bytes()),listed=len(manifest),matched=matched,
        scientific_command_exit=p.returncode,encoding_status=encoding['status'],independent_numeric_status=numeric['status'],checked_utc=utc()))
    rc=p.returncode if p.returncode else (0 if ok else 1)
    print('COMMAND_EXIT',p.returncode,'DELIVERY_EXIT',rc,'ELAPSED',time.perf_counter()-start,'OUTPUT',out,flush=True);return rc


if __name__=='__main__':raise SystemExit(main())
