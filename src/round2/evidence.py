import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
KEYS = ('run_id', 'config_sha256', 'input_sha256', 'code_sha256')
STATUSES = {'PASS','FAIL','BLOCKED','SKIPPED'}

def utc(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()
def canonical(obj): return json.dumps(obj,sort_keys=True,ensure_ascii=False,allow_nan=False).encode('utf-8')
def read(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def write(path, obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.tmp')
    temp.write_bytes(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8'))
    os.replace(temp,path)

def files_manifest(paths):
    return [{'path':str(p.resolve()),'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(set(map(Path,paths))) if p.is_file()]

def code_manifest():
    paths=[p for folder in ['src','tools','tests','configs','docs'] for p in (ROOT/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts]
    return files_manifest(paths+[ROOT/'README.md',ROOT/'requirements-smoke.txt',ROOT/'.gitignore'])

def create_run(output, config, input_paths, mechanism_test=False):
    out=Path(output).resolve()
    if out.exists() and any(out.iterdir()): raise ValueError('Refusing nonempty output directory; choose a new run directory')
    out.mkdir(parents=True,exist_ok=True)
    rid=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:10]
    inputs=files_manifest(input_paths);code=code_manifest()
    ctx=dict(schema_version=2,run_id=rid,config_sha256=digest(canonical(config)),input_sha256=digest(canonical(inputs)),code_sha256=digest(canonical(code)),created_utc=utc(),output=str(out),work=str(ROOT/'work'/rid),mechanism_test=mechanism_test)
    Path(ctx['work']).mkdir(parents=True)
    write(out/'run.json',ctx);write(out/'config.json',dict(**{k:ctx[k] for k in KEYS},config=config))
    write(out/'input_manifest.json',dict(run_id=rid,files=inputs));write(out/'code_manifest.json',dict(run_id=rid,files=code))
    return ctx

def load_run(output):
    out=Path(output);ctx=read(out/'run.json');cfg=read(out/'config.json')
    if ctx.get('schema_version')!=2 or any(cfg.get(k)!=ctx[k] for k in KEYS):raise ValueError('Run/config ownership mismatch')
    if digest(canonical(cfg['config']))!=ctx['config_sha256']:raise ValueError('Config hash mismatch')
    for name,key in [('input_manifest.json','input_sha256'),('code_manifest.json','code_sha256')]:
        manifest=read(out/name)
        if manifest.get('run_id')!=ctx['run_id'] or digest(canonical(manifest['files']))!=ctx[key]:raise ValueError('Manifest binding mismatch: '+name)
    return ctx,cfg['config']

def artifacts(folder):
    folder=Path(folder)
    return [{'path':str(p.relative_to(folder)),'sha256':digest(p.read_bytes()),'bytes':p.stat().st_size} for p in sorted(folder.rglob('*')) if p.is_file() and p not in [folder/'result.json',folder/'result.json.tmp',folder/'output_manifest.json']]

def result(ctx, stage, status, checks, detail=None):
    if status not in STATUSES:raise ValueError(status)
    folder=Path(ctx['output'])/stage;folder.mkdir(exist_ok=True)
    obj=dict(schema_version=2,**{k:ctx[k] for k in KEYS},stage=stage,lifecycle='complete',status=status,finished_utc=utc(),checks=checks,detail=detail or {},artifacts=artifacts(folder))
    write(folder/'result.json',obj)
    return obj

def validate_stage(ctx,stage,execution=None):
    try:
        obj=read(Path(ctx['output'])/stage/'result.json')
        if obj.get('schema_version')!=2 or any(obj.get(k)!=ctx[k] for k in KEYS):raise ValueError('old schema / run or hash mismatch')
        if obj.get('stage')!=stage or obj.get('lifecycle')!='complete':raise ValueError('wrong stage or incomplete result')
        if obj.get('status') not in STATUSES or not isinstance(obj.get('checks'),list) or not obj['checks']:raise ValueError('invalid status/checks')
        if any(c.get('status') not in STATUSES for c in obj['checks']):raise ValueError('invalid check status')
        if obj['status']=='PASS' and any(c['status']!='PASS' for c in obj['checks'] if c.get('required',True)):raise ValueError('PASS conflicts with required checks')
        folder=Path(ctx['output'])/stage
        if obj.get('artifacts')!=artifacts(folder):raise ValueError('artifact missing/modified or unmanifested')
        # Keep valid business evidence even when the command reports an unmet
        # acceptance target. Process failure and business failure are distinct.
        obj['business_status']=obj['status']
        obj['evidence_validation']=dict(valid=True,current_run=True,complete=True)
        obj['reasons']=[dict(code=c.get('reason_code','BUSINESS_CHECK_'+c['status']),
            check_id=c.get('id'),message=c.get('reason',c.get('id','')))
            for c in obj['checks'] if c['status']!='PASS']
        if execution is not None:
            command_valid=(all(execution.get(k)==ctx[k] for k in KEYS)
                and execution.get('stage')==stage and type(execution.get('exit_code')) is int
                and type(execution.get('timed_out')) is bool)
            obj['command']=dict(started=execution.get('started',True),valid=command_valid,
                exit_code=execution.get('exit_code'),timed_out=execution.get('timed_out'),
                record=execution)
            if not command_valid:
                obj['status']='FAIL';obj['reasons'].append(dict(code='COMMAND_EVIDENCE_INVALID',message='Command identity or fields invalid'))
            elif execution['timed_out']:
                obj['status']='FAIL';obj['reasons'].append(dict(code='COMMAND_TIMEOUT',message='Command exceeded its timeout; business claim does not override timeout'))
            elif execution['exit_code']!=0:
                obj['status']='FAIL'
                obj['reasons'].append(dict(code='COMMAND_BUSINESS_CONFLICT' if obj['business_status']=='PASS' else 'COMPLETED_TARGET_UNMET',
                    message='Valid business result retained; nonzero command cannot pass'))
        return obj
    except (ValueError,OSError,KeyError,TypeError) as exc:
        return dict(stage=stage,status='FAIL',business_status=None,
            evidence_validation=dict(valid=False,current_run=False,complete=False),
            reasons=[dict(code='RESULT_EVIDENCE_INVALID',message=str(exc))],
            checks=[dict(id=stage+'_evidence',status='FAIL',reason=str(exc))],validation_error=str(exc),
            command=execution)

def execute(ctx,stage,argv,timeout):
    work=Path(ctx['work']);start=utc();t=time.perf_counter();env=os.environ.copy();env['PYTHONIOENCODING']='utf-8';env['MPLCONFIGDIR']=str(work/'mpl')
    # All child Python processes inherit this exact fmb executable.
    with (work/(stage+'.stdout.log')).open('wb') as stdout, (work/(stage+'.stderr.log')).open('wb') as stderr:
        proc=subprocess.Popen(argv,cwd=ROOT,env=env,stdout=stdout,stderr=stderr)
        timed_out=False
        try: rc=proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out=True;proc.kill();proc.wait();rc=124
    record=dict(**{k:ctx[k] for k in KEYS},stage=stage,started_utc=start,elapsed_s=time.perf_counter()-t,argv=argv,exit_code=rc,timed_out=timed_out,timeout_s=timeout,stdout=str(work/(stage+'.stdout.log')),stderr=str(work/(stage+'.stderr.log')))
    write(Path(ctx['output'])/'commands'/f'{stage}.json',record)
    return record

def summarize(ctx, stages):
    load_run(ctx['output'])
    results=[]
    for spec in stages:
        stage=spec['id'];p=Path(ctx['output'])/'commands'/f'{stage}.json'
        try:execution=read(p) if p.exists() else None
        except (ValueError,OSError):execution=dict(run_id='invalid',stage=stage,exit_code=1,timed_out=False)
        r=validate_stage(ctx,stage,execution)
        if r['status']=='PASS' and execution is None:
            r=dict(stage=stage,status='FAIL',checks=[dict(id='command_evidence',status='FAIL',reason='PASS requires command evidence')])
        r['required']=spec['required'];results.append(r)
    inputs=read(Path(ctx['output'])/'input_manifest.json')['files']
    preserved=all(Path(x['path']).is_file() and digest(Path(x['path']).read_bytes())==x['sha256'] for x in inputs)
    code_unchanged=digest(canonical(code_manifest()))==ctx['code_sha256']
    accepted=preserved and code_unchanged and all(r['status']=='PASS' for r in results if r['required'])
    summary=dict(schema_version=2,**{k:ctx[k] for k in KEYS},mechanism_test=ctx['mechanism_test'],execution_completed=True,report_generated=False,core_acceptance='PASS' if accepted else 'FAIL',source_preserved=preserved,code_unchanged=code_unchanged,stages=results,exit_code=0 if accepted else 1)
    write(Path(ctx['output'])/'summary.json',summary)
    return summary
