import argparse, json
from .constellation import export

def main():
    parser=argparse.ArgumentParser(description='Round-1 equal-plane Walker Delta generator, independent of STK')
    sub=parser.add_subparsers(dest='command',required=True)
    g=sub.add_parser('generate')
    g.add_argument('--n',type=int,default=28);g.add_argument('--planes',type=int,default=7)
    g.add_argument('--phase-factor',type=int,default=1);g.add_argument('--h-km',type=float,default=600.)
    g.add_argument('--raan0-deg',type=float,default=0.);g.add_argument('--u0-deg',type=float,default=0.)
    g.add_argument('--output',required=True)
    args=vars(parser.parse_args());args.pop('command')
    try: print(json.dumps(export(**args)))
    except ValueError as e: parser.error(str(e))
if __name__=='__main__': main()
