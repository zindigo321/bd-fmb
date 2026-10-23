"""Explicit fault fixture executed by mechanism tests, never actual STK evidence."""
import argparse,time
from pathlib import Path
from .evidence import load_run,result
def main():
    p=argparse.ArgumentParser();p.add_argument('output');p.add_argument('mode');args=p.parse_args();ctx,_=load_run(args.output)
    if not ctx['mechanism_test']:raise ValueError('Mechanism probe requires labelled context')
    path=Path(args.output)/'probe';path.mkdir(exist_ok=True)
    if args.mode=='timeout':time.sleep(4)
    elif args.mode=='corrupt':(path/'result.json').write_text('{bad JSON')
    elif args.mode=='missing':pass
    elif args.mode=='pass':result(ctx,'probe','PASS',[dict(id='fixture',status='PASS')])
    else:raise ValueError(args.mode)
if __name__=='__main__':main()
