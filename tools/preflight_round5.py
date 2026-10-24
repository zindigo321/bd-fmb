"""Read-only baseline capture for the single round5 experiment."""
from pathlib import Path
import re
import html
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.round2.evidence import ROOT, read, write, files_manifest, code_manifest
from src.round2.environment import environment


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    folder = ROOT/'work/round5/preflight'
    folder.mkdir(parents=True, exist_ok=True)
    prior = Path(r'G:\gpt-file\Codex\outputs\bd-fmb\round4\20261023T192721_bb112ff4')
    frozen = Path(r'G:\gpt-file\Codex\outputs\bd-fmb\round3_20261023T180751_7f077d4f\grid')
    paths = [p for base in [prior, frozen, ROOT/'data', ROOT/'_local/handoff'] for p in base.rglob('*') if p.is_file()]
    paths.append(Path(r'G:\百度-伐谋杯\陈\handoff\2026-10-23-audit-chen.md'))
    manifest=[];unreadable=[]
    for p in paths:
        try:manifest.extend(files_manifest([p]))
        except OSError as exc:unreadable.append(dict(path=str(p),error=repr(exc)))
    write(folder/'preservation_manifest.json',manifest)
    write(folder/'unreadable.json',unreadable)
    if not (folder/'code_before.json').exists():write(folder/'code_before.json',code_manifest())
    git={}
    for name,args in [('head',['rev-parse','HEAD']),('status',['status','--short']),('diff',['diff']),('diff_stat',['diff','--stat'])]:
        p=subprocess.run(['git','-c','safe.directory=G:/fmb666',*args],capture_output=True,cwd=ROOT)
        git[name]=dict(exit_code=p.returncode,stdout=p.stdout.decode('utf-8'),stderr=p.stderr.decode('utf-8'))
    write(folder/'baseline.json',dict(git=git,audit_head='83d2caa459e9fdda2b623aa7f5288ba800e24206',
        baseline_tests=dict(count=37,exit_code=0,elapsed_s=9.631,log=str(folder/'baseline_tests.log')),
        dependency_probe_note='An exploratory import included scipy, which is absent but not a project dependency. No environment changes made.',
        source_round4=str(prior),source_round3_grid=str(frozen)))
    cfg=read(ROOT/'configs/round4.json')
    write(folder/'environment.json',environment(cfg))
    names=['final_status.json','config.json','selection.json','next_scale.json','next_step_review.json',
           'G/T4/experiment.json','G/T4/raw_intervals.json','G/T4/geometry_audit.json','reopen/instance/experiment.json','lifecycle_index.json']
    write(folder/'history_review.json',{n:read(prior/n) for n in names})
    print('BASELINE',git['head']['stdout'].strip(),'files',len(manifest),'unreadable',len(unreadable))
    docs=Path(r'G:\Help\Programming')
    wanted=['STKObjects~IAgVePropagatorJ2Perturbation.html','STKObjects~IAgVePropagatorJ2Perturbation~EphemerisInterval.html',
        'STKObjects~IAgAccessConstraintCollection.html','cmd_GetTimePeriod.htm','STKObjects~IAgVePropagatorJ2Perturbation~StartTime.html',
        'STKObjects~IAgVePropagatorJ2Perturbation~StopTime.html']
    for name in wanted:
        found=list(docs.rglob(name))
        if found:
            p=found[0];t=html.unescape(re.sub('<[^>]+>',' ',p.read_text(encoding='utf-8-sig')))
            t=re.sub(r'\s+',' ',t)
            (folder/(name+'.txt')).write_text(t,encoding='utf-8')
            print(name,t[:9000])


if __name__=='__main__':main()
