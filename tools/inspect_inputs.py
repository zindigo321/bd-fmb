"""Read-only source inspection and safe extraction; never executes archive code."""
import hashlib, json, pathlib, zipfile, xml.etree.ElementTree as ET

ROOT = pathlib.Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'
WORK.mkdir(exist_ok=True)
def sha(b): return hashlib.sha256(b).hexdigest()
def extract_selected():
    outer = pathlib.Path(r'G:\百度-伐谋杯\百度伐谋杯.zip')
    z = zipfile.ZipFile(outer)
    candidates = [x for x in z.infolist() if '面向全球覆盖与区域重访的星座构型优化设计数据/' in x.filename and x.filename.endswith('.zip')]
    assert len(candidates) == 1
    dest = ROOT / 'data' / 'raw' / 'yangtze_delta'
    dest.mkdir(parents=True, exist_ok=True)
    nested = dest.parent / 'boundary_original.zip'
    content = z.read(candidates[0])
    if nested.exists(): assert nested.read_bytes() == content
    else: nested.write_bytes(content)
    manifest = [{'path': str(outer), 'sha256': sha(outer.read_bytes())}, {'path': str(nested), 'outer_member': candidates[0].filename, 'sha256': sha(content)}]
    with zipfile.ZipFile(nested) as archive:
        assert sum(i.file_size for i in archive.infolist()) < 200_000_000
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = pathlib.PurePosixPath(name)
            if parts.is_absolute() or '..' in parts.parts or ':' in name or (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Unsafe archive member: ' + name)
            target = (dest / name).resolve()
            assert target.is_relative_to(dest.resolve())
            if info.is_dir(): continue
            target.parent.mkdir(parents=True, exist_ok=True)
            b = archive.read(info)
            if target.exists(): assert target.read_bytes() == b
            else: target.write_bytes(b)
            manifest.append({'path': str(target.relative_to(ROOT)), 'size':len(b), 'sha256':sha(b)})
    for p in pathlib.Path(r'G:\百度-伐谋杯\陈').glob('*.docx'):
        if not ('洞一科技：面向全球覆盖' in p.name or '立项书-version1' in p.name): continue
        xml = ET.fromstring(zipfile.ZipFile(p).read('word/document.xml'))
        ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        lines=[''.join(t.text or '' for t in el.iter() if t.tag.endswith('}t')) for el in xml.findall('.//w:p',ns)]
        (WORK / (p.stem+'.txt')).write_text('\n'.join(lines), encoding='utf-8')
        manifest.append({'path':str(p), 'sha256':sha(p.read_bytes())})
    (WORK/'source_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False,indent=2))
if __name__ == '__main__': extract_selected()
