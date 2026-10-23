"""STK detection is optional and contains no COM activation."""
import importlib.metadata
from pathlib import Path
import platform
import re
import struct
import sys

def detect_stk(config, fault=None):
    try:
        if fault=='missing':raise FileNotFoundError('controlled mechanism test: STK not installed')
        if fault=='error':raise PermissionError('controlled mechanism test: registry access denied')
        import winreg
        override=config['stk'].get('executable')
        if override:exe=Path(override)
        else:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,config['stk']['progid']+'\\CLSID') as key:clsid=winreg.QueryValue(key,None)
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT,'CLSID\\'+clsid+'\\LocalServer32') as key:server=winreg.QueryValue(key,None)
            match=re.match(r'^\s*"?(.+?\.exe)"?(?:\s|$)',server,re.I)
            if not match:raise ValueError('Cannot parse LocalServer32 executable')
            exe=Path(match.group(1))
        if not exe.is_file():raise FileNotFoundError(str(exe))
        import win32api
        v=win32api.GetFileVersionInfo(str(exe),'\\')
        version='.'.join(str(x) for x in [v['FileVersionMS']>>16,v['FileVersionMS']&65535,v['FileVersionLS']>>16,v['FileVersionLS']&65535])
        return dict(status='PASS',executable=str(exe),installation=str(exe.parent.parent),version=version,controlled_fault=fault)
    except Exception as exc:return dict(status='BLOCKED',error_type=type(exc).__name__,error=str(exc),controlled_fault=fault)

def environment(config,skip_stk=False,fault=None):
    record=dict(python=sys.executable,prefix=sys.prefix,version=sys.version,bits=struct.calcsize('P')*8,platform=platform.platform(),packages={d.metadata['Name']:d.version for d in importlib.metadata.distributions()})
    record['stk']=dict(status='SKIPPED',reason='--skip-stk: detector and registry deliberately not called',controlled_fault=fault) if skip_stk else detect_stk(config,fault)
    return record
