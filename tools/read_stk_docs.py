import pathlib, re, html, hashlib, json
root=pathlib.Path(r'G:\Help\Programming')
out=pathlib.Path('work/stk_docs'); out.mkdir(parents=True,exist_ok=True)
names=['cmd_SetStateClassical.htm','cmd_SetAttitudeStandard.htm','cmd_Define.htm','cmd_PointFixed.htm','cmd_AccessConfig.htm','cmd_ReportCreate.htm','cmd_GetSTKVersion.htm','cmd_GetLicenseReport.htm','cmd_SetUnits.htm','cmd_GetPosition.htm','cmd_GetTimePeriod.htm','STKObjects~IAgStkObjectRoot~Isolate.html','STKObjects~IAgStkObjectRoot~SaveScenarioAs.html','STKObjects~IAgStkObjectRoot~LoadScenario.html','STKObjects~IAgSnRectangularPattern.html','STKObjects~IAgStkAccess~Advanced.html','STKObjects~IAgAccessAdvancedSettings.html','AgUiApplicationLib~IAgUiApplication.html']
manifest=[]
for name in names:
    paths=list(root.rglob(name))
    if not paths: print('NOT FOUND',name); continue
    p=paths[0]; b=p.read_bytes()
    t=b.decode('utf-8-sig',errors='replace')
    t=re.sub(r'<(script|style)\b[^>]*>.*?</\1>','',t,flags=re.S|re.I)
    t=html.unescape(re.sub('<[^>]+>',' ',t))
    t=re.sub(r'\s+',' ',t)
    (out/(name+'.txt')).write_text(t,encoding='utf-8')
    manifest.append({'file':str(p),'sha256':hashlib.sha256(b).hexdigest()})
    print(name, t[:15000])
(out/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
