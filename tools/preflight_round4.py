"""Read-only historical audit and baseline; never edits historical deliverables."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT, files_manifest, read, write, code_manifest, digest


def main():
    base = ROOT/'work/round4_prechange'
    base.mkdir(exist_ok=False)
    files = code_manifest()
    write(base/'code_before.json', files)
    for item in files:
        p = Path(item['path']); dest = base/'source'/p.relative_to(ROOT)
        dest.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(p, dest)
    history = Path(r'G:\gpt-file\Codex\outputs\bd-fmb')
    paths = [p for d in history.iterdir() if d.is_dir() and d.name != 'round4'
             for p in d.rglob('*') if p.is_file()]
    paths += [p for p in (ROOT/'data').rglob('*') if p.is_file()]
    write(base/'preservation_manifest.json', files_manifest(paths))
    prior = history/'round3_20261023T180751_7f077d4f'
    text = (prior/'audit_report.md').read_text(encoding='utf-8')
    write(base/'history_audit.json', dict(source=str(prior), report_sha256=digest((prior/'audit_report.md').read_bytes()),
        corrupted_question_lines=[i+1 for i, s in enumerate(text.splitlines()) if '???' in s],
        replacement_count=text.count('\ufffd'), original_C=read(prior/'C/result.json'),
        original_summary_C=next(s for s in read(prior/'summary.json')['stages'] if s['stage']=='C'),
        encoding_cause='Known UTF-8 generator source is intact; later PowerShell pipeline passed Chinese source through legacy stdin encoding, leaving literal question marks. UTF-8 decoding alone cannot detect this.',
        historical_acceptance=read(prior/'stage_acceptance.json')))
    env = os.environ.copy(); env['PYTHONIOENCODING']='utf-8'; env['PYTHONDONTWRITEBYTECODE']='1'
    start=time.perf_counter()
    with (base/'baseline.stdout.log').open('wb') as out, (base/'baseline.stderr.log').open('wb') as err:
        p=subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-v'],cwd=ROOT,env=env,stdout=out,stderr=err,timeout=120)
    write(base/'baseline.json',dict(python=sys.executable,exit_code=p.returncode,elapsed_s=time.perf_counter()-start,
        argv=[sys.executable,'-m','unittest','discover','-s','tests','-v'],logs=files_manifest([base/'baseline.stdout.log',base/'baseline.stderr.log'])))
    print('BASELINE', p.returncode, base, flush=True)
    return p.returncode

if __name__=='__main__':raise SystemExit(main())
