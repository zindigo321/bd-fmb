"""Run one bounded evidence command with the fmb interpreter and append its record."""
import argparse,datetime,json,os,pathlib,subprocess,sys,time,shutil,hashlib
ROOT=pathlib.Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--id',required=True);p.add_argument('--timeout',type=int,default=180);p.add_argument('command',nargs=argparse.REMAINDER)
    args=p.parse_args();assert pathlib.Path(sys.prefix).name.lower()=='fmb'
    out=pathlib.Path(args.output);stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    work=ROOT/'work'/out.name/stamp;work.mkdir(parents=True,exist_ok=True)
    for name in ['sensor/sensor_results.json','results.json']:
        if (out/name).exists():shutil.copy2(out/name,work/('previous_'+pathlib.Path(name).name))
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    argv=[sys.executable,*command];env=os.environ.copy();env['PYTHONIOENCODING']='utf-8'
    t=time.perf_counter()
    try:
        r=subprocess.run(argv,cwd=ROOT,env=env,capture_output=True,timeout=args.timeout);rc=r.returncode;stdout=r.stdout;stderr=r.stderr
    except subprocess.TimeoutExpired as e:rc=124;stdout=e.stdout or b'';stderr=(e.stderr or b'')+b'\nTIMEOUT. Partial evidence retained; own STK process may remain.'
    elapsed=time.perf_counter()-t
    (work/(args.id+'.stdout.log')).write_bytes(stdout);(work/(args.id+'.stderr.log')).write_bytes(stderr)
    records=json.loads((out/'commands.json').read_text(encoding='utf-8'))
    records.append(dict(id=args.id,argv=argv,powershell_command='& '+' '.join("'"+a.replace("'","''")+"'" for a in argv),started_utc=stamp,elapsed_s=elapsed,exit_code=rc,expected_exit_code=0,timeout_s=args.timeout,log_directory=str(work),stderr_tail=stderr.decode('utf-8',errors='replace')[-3000:]))
    (out/'commands.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
    if (out/'output_manifest.json').exists():
        manifest=[dict(path=str(p.relative_to(out)),bytes=p.stat().st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in sorted(out.rglob('*')) if p.is_file() and p.name!='output_manifest.json']
        (out/'output_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(stdout.decode('utf-8',errors='replace')[-5000:]);print(stderr.decode('utf-8',errors='replace')[-3000:]);print('EXIT',rc,'ELAPSED',elapsed)
    return rc
if __name__=='__main__':raise SystemExit(main())
