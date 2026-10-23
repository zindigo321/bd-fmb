import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from src.round2.evidence import ROOT,create_run,result,read,write,validate_stage,execute,summarize
from src.round2.environment import environment
from src.round2.config import load_config
from src.constellation import generate

class EvidenceTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'work/mechanism').mkdir(parents=True,exist_ok=True)
        self.tmp=None
        if os.environ.get('R2_MECHANISM_EVIDENCE'):
            parent=Path(os.environ['R2_MECHANISM_EVIDENCE']);parent.mkdir(parents=True,exist_ok=True);self.base=Path(tempfile.mkdtemp(prefix=self._testMethodName+'_',dir=parent))
        else:self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'work/mechanism');self.base=Path(self.tmp.name)
        self.cfg=load_config(ROOT/'configs/round2.json');self.ctx=create_run(self.base/'run',self.cfg,[ROOT/'README.md'],True)
    def tearDown(self):
        if self.tmp:self.tmp.cleanup()
    def test_nonempty_old_pass_rejected(self):
        result(self.ctx,'stk','PASS',[dict(id='old',status='PASS')]);original=(self.base/'run/stk/result.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'nonempty'):create_run(self.base/'run',self.cfg,[],True)
        self.assertEqual(original,(self.base/'run/stk/result.json').read_bytes())
    def test_old_or_wrong_run_config_input_code_rejected(self):
        for key in ['run_id','config_sha256','input_sha256','code_sha256','schema_version','lifecycle']:
            obj=result(self.ctx,'stk','PASS',[dict(id='fake',status='PASS')]);obj[key]='wrong';write(self.base/'run/stk/result.json',obj)
            with self.subTest(key=key):self.assertEqual(validate_stage(self.ctx,'stk')['status'],'FAIL')
    def test_artifact_tamper_rejected(self):
        folder=self.base/'run/probe';folder.mkdir();(folder/'sample.csv').write_text('actual')
        result(self.ctx,'probe','PASS',[dict(id='artifact',status='PASS')]);(folder/'sample.csv').write_text('changed')
        self.assertEqual(validate_stage(self.ctx,'probe')['status'],'FAIL')
    def test_missing_corrupt_timeout_cannot_pass(self):
        for mode in ['missing','corrupt','timeout']:
            context=create_run(self.base/mode,self.cfg,[],True)
            command=execute(context,'probe',[sys.executable,'-m','src.round2.probe',context['output'],mode],.2 if mode=='timeout' else 10)
            summary=summarize(context,[dict(id='probe',required=True)])
            with self.subTest(mode=mode):
                self.assertEqual(summary['core_acceptance'],'FAIL');self.assertEqual(summary['exit_code'],1)
                if mode=='timeout':self.assertEqual(command['exit_code'],124);self.assertTrue(command['timed_out'])
    def test_nonzero_command_cannot_use_old_pass(self):
        result(self.ctx,'probe','PASS',[dict(id='old',status='PASS')]);execute(self.ctx,'probe',[sys.executable,'-c','raise SystemExit(7)'],10)
        self.assertEqual(summarize(self.ctx,[dict(id='probe',required=True)])['exit_code'],1)
    def test_pass_without_command_evidence_rejected(self):
        result(self.ctx,'probe','PASS',[dict(id='fake',status='PASS')]);self.assertEqual(summarize(self.ctx,[dict(id='probe',required=True)])['exit_code'],1)
    def test_skip_never_calls_stk_detector(self):
        with patch('src.round2.environment.detect_stk',side_effect=AssertionError('must not call')):actual=environment(self.cfg,skip_stk=True,fault='missing')
        self.assertEqual(actual['stk']['status'],'SKIPPED');self.assertEqual(actual['python'],sys.executable)
    def test_config_drives_inclination(self):
        first=generate(**self.cfg['constellation'],constants=self.cfg['earth'])[0][0];altered=copy.deepcopy(self.cfg['earth']);altered['j2']*=1.01
        self.assertNotEqual(first['i_deg'],generate(**self.cfg['constellation'],constants=altered)[0][0]['i_deg'])
    def test_skip_missing_install_and_detection_error_end_to_end(self):
        for mode,skip,expected in [('missing',True,0),('error',False,1)]:
            destination=self.base/('integration_'+mode)
            argv=[sys.executable,'tools/run_round2.py','--output',str(destination),'--mechanism-test','--probe-fault',mode]
            if skip:argv.append('--skip-stk')
            p=subprocess.run(argv,cwd=ROOT,capture_output=True,timeout=45)
            summary=read(destination/'summary.json');stages={s['stage']:s['status'] for s in summary['stages']}
            with self.subTest(mode=mode):
                self.assertEqual(p.returncode,expected,p.stderr.decode(errors='replace'));self.assertEqual(stages['data'],'PASS');self.assertEqual(stages['generate'],'PASS')
                self.assertEqual(stages['stk'],'SKIPPED' if skip else 'BLOCKED');self.assertTrue(summary['mechanism_test']);self.assertTrue(summary['report_generated'])
                if not skip:self.assertEqual(read(destination/'environment/environment.json')['stk']['error_type'],'PermissionError')
    def test_successful_probe_has_valid_bound_evidence(self):
        execute(self.ctx,'probe',[sys.executable,'-m','src.round2.probe',self.ctx['output'],'pass'],10)
        self.assertEqual(summarize(self.ctx,[dict(id='probe',required=True)])['exit_code'],0)
    def test_timeout_blocks_dependent_stk_but_keeps_data(self):
        destination=self.base/'timeout_pipeline'
        p=subprocess.run([sys.executable,'tools/run_round2.py','--output',str(destination),'--mechanism-test','--stage-fault','generate:timeout'],cwd=ROOT,capture_output=True,timeout=45)
        summary=read(destination/'summary.json');stages={s['stage']:s['status'] for s in summary['stages']}
        self.assertEqual(p.returncode,1);self.assertEqual(stages['generate'],'FAIL');self.assertEqual(stages['stk'],'BLOCKED');self.assertEqual(stages['data'],'PASS')

if __name__=='__main__':unittest.main(verbosity=2)
