"""Audit source data without modifying or repairing geometries."""
import argparse, hashlib, json, math, os, pathlib, re
from shapely.geometry import shape, Polygon
from shapely.ops import unary_union
from shapely.validation import explain_validity

ROOT=pathlib.Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'work'/'mpl'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

def text_decode(b):
    for enc in (['utf-8-sig'] if b.startswith(b'\xef\xbb\xbf') else ['utf-8','gb18030']):
        try: return b.decode(enc),enc
        except UnicodeDecodeError: pass
    return b.decode('latin1'), 'undetermined ANSI; latin1 lossless byte mapping (not verified source encoding)'

def audit(path):
    raw=path.read_bytes(); txt,encoding=text_decode(raw); data=json.loads(txt)
    features=data['features'] if data['type']=='FeatureCollection' else [data]
    details=[]; geometries=[]
    for f in features:
        obj=f.get('geometry'); errors=[]; count=0; closed=True; rings=0; holes=0
        if not obj:
            details.append(dict(properties=f.get('properties'),empty=True,errors=['null geometry']));continue
        if obj['type']=='Polygon': polygons=[obj['coordinates']]
        elif obj['type']=='MultiPolygon': polygons=obj['coordinates']
        else: raise ValueError('Unsupported boundary geometry '+obj['type'])
        for poly in polygons:
            holes+=max(0,len(poly)-1)
            for ring in poly:
                rings+=1;count+=len(ring)
                if len(ring)<4 or ring[0]!=ring[-1]: closed=False;errors.append('unclosed or short ring')
                for point in ring:
                    if len(point)<2 or not all(math.isfinite(x) for x in point): errors.append('non-finite coordinate')
                    elif not (-180<=point[0]<=180 and -90<=point[1]<=90): errors.append('illegal lon/lat')
        g=shape(obj);geometries.append(g)
        if g.is_empty: errors.append('empty geometry')
        if not g.is_valid: errors.append(explain_validity(g))
        details.append(dict(properties=f.get('properties'),type=g.geom_type,components=len(polygons),rings=rings,
                            holes=holes,vertices_including_closure=count,closed=closed,empty=g.is_empty,
                            bounds=list(g.bounds),errors=errors))
    return dict(file=str(path.relative_to(ROOT)),sha256=hashlib.sha256(raw).hexdigest(),encoding=encoding,
                crs_declared=data.get('crs'),crs_interpretation='GeoJSON geographic lon/lat degrees; WGS84 assumed when CRS absent; original datum provenance unverified',
                coordinate_order='longitude, latitude',features=len(features),details=details),geometries

def main(output):
    output=pathlib.Path(output);output.mkdir(parents=True,exist_ok=True)
    base=ROOT/'data/raw/yangtze_delta/Description of the Boundaries of the Yangtze River Delta Region'
    merged,gm=audit(base/'yangtze_river_delta_boundary.geojson')
    source,gs=audit(base/'source_data/geoBoundaries-CHN-ADM1_simplified.geojson')
    names={'Shanghai Municipality','Jiangsu Province','Zhejiang Province','Anhui Province'}
    selected=[g for d,g in zip(source['details'],gs) if d['properties'].get('shapeName') in names]
    assert len(selected)==4
    union=unary_union(selected);boundary=gm[0]
    union_check=dict(topological_equals=union.equals(boundary),symmetric_difference_deg2=union.symmetric_difference(boundary).area,
                     hausdorff_deg=union.hausdorff_distance(boundary),repair_applied=False)
    polygons=list(boundary.geoms) if boundary.geom_type=='MultiPolygon' else [boundary]
    polygons.sort(key=lambda p:p.area,reverse=True)
    at=[]
    for file in sorted((base/'Scenario1').glob('*.at')):
        text,enc=text_decode(file.read_bytes())
        body=re.search(r'BEGIN PolygonPoints(.*?)END PolygonPoints',text,re.S).group(1)
        points=[list(map(float,line.split())) for line in body.strip().splitlines()]
        idx=int(re.search(r'Island_(\d+)',file.stem).group(1)) if 'Island_' in file.stem else 0
        target=polygons[idx];actual=[(p[1],p[0]) for p in points]
        wanted=list(target.exterior.coords)[:-1]
        comparable=actual[:-1] if actual[0]==actual[-1] else actual
        delta=max(abs(a-b) for x,y in zip(comparable,wanted) for a,b in zip(x,y)) if len(comparable)==len(wanted) else None
        at.append(dict(file=file.name,encoding=enc,description_corrupted='????' in text,vertices=len(points),declared_vertices=int(re.search(r'NumberOfPolygonPts\s+(\d+)',text).group(1)),
                       coordinate_order='latitude,longitude,altitude',explicit_closure=actual[0]==actual[-1],implicit_STK_closure=True,
                       matches_sorted_component=idx,max_vertex_error_deg=delta,holes_in_source=len(target.interiors),
                       bounds=list(Polygon(actual).bounds),valid=Polygon(actual).is_valid,
                       all_coordinates_finite=all(math.isfinite(v) for p in points for v in p),
                       altitudes=sorted(set(p[2] for p in points))))
    checks=[dict(id='data_geometry',status='PASS' if not any(d['errors'] for a in [merged,source] for d in a['details']) else 'FAIL'),
            dict(id='source_union',status='PASS' if union_check['topological_equals'] else 'FAIL'),
            dict(id='at_correspondence',status='PASS' if len(at)==len(polygons) and all(a['max_vertex_error_deg'] is not None and a['max_vertex_error_deg']<1e-10 and a['holes_in_source']==0 and a['valid'] and a['all_coordinates_finite'] and a['vertices']==a['declared_vertices'] for a in at) else 'FAIL'),
            dict(id='at_description_integrity',status='FAIL' if any(a['description_corrupted'] for a in at) else 'PASS',reason='Original AT descriptions contain replacement question marks; numeric geometry is intact; no source repair')]
    result=dict(merged=merged,source=source,union_check=union_check,area_targets=at,checks=checks,
                pending=['主办方确认行政区全域是否就是正式评估边界','源简化数据的空间精度、原始坐标基准及版本溯源','未复用原 STK 场景；仅对 AT 坐标进行文本核验'],
                script_review=['未执行附件脚本','选择四个省级要素 unary_union，可能 buffer(0) 自动修复','将各 Polygon 外环转为 lat/lon 并移除重复闭合点','按面积递减创建大陆及两个岛屿；忽略孔洞的实现不能通用于带孔区域','GetActiveObject 会修改并保存当前用户场景，禁止复用该流程'])
    (output/'data_audit.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    fig,ax=plt.subplots(figsize=(9,8))
    colors=['#496cb1','#348d74','#c18c32','#aa678a']
    for g,color in zip(selected,colors):
        for p in (g.geoms if g.geom_type=='MultiPolygon' else [g]):
            x,y=p.exterior.xy;ax.plot(x,y,color=color,lw=.7,alpha=.7)
            for ring in p.interiors: ax.plot(*ring.xy,color=color,lw=.7,linestyle='--')
    for idx,p in enumerate(polygons):
        ax.plot(*p.exterior.xy,color='#162337',lw=1.3,label='Merged external boundary' if idx==0 else None)
        for ring in p.interiors: ax.plot(*ring.xy,color='red',lw=1.2,linestyle='--')
        if idx: ax.annotate('Island '+str(idx),p.representative_point().coords[0],xytext=(10,-22*idx),textcoords='offset points',arrowprops={'arrowstyle':'->'})
    for a in at:
        p=polygons[a['matches_sorted_component']]; ax.scatter(*zip(*list(p.exterior.coords)[:-1]),s=5,c='#d15c31',zorder=3,label='AT verified vertices' if a['matches_sorted_component']==0 else None)
    ax.set_aspect(1/math.cos(math.radians(31)));ax.set_xlabel('Longitude (degrees east)');ax.set_ylabel('Latitude (degrees north)')
    ax.set_title('Yangtze Delta boundary audit\nSource provinces / merged boundary / STK AT vertices')
    ax.grid(alpha=.2);ax.legend(loc='upper left');fig.tight_layout();fig.savefig(output/'boundary_check.png',dpi=180);plt.close(fig)
    (output/'data_audit.md').write_text('# 数据审计\n\n'+f"合并边界：{boundary.geom_type}，{len(polygons)} 个组成部分；边界范围 {list(boundary.bounds)}。\n\n"+
        f"四省源要素重做 union 与提供边界拓扑相等：{union_check['topological_equals']}；对称差面积 {union_check['symmetric_difference_deg2']} 平方度。没有修复或简化几何。\n\n"+
        '\n'.join(f"- {a['file']}: {a['vertices']} 点，与第 {a['matches_sorted_component']} 个组成部分对应，最大坐标误差 {a['max_vertex_error_deg']} 度。" for a in at)+
        '\n\nGeoJSON 是经度、纬度；AT 是纬度、经度、高度；本包 AT 已重复首尾点显式闭合，比较前去掉该重复点。原 AT 描述出现问号替代，原始编码无法确定，标记 FAIL；数值坐标不受影响。所有源文件保留。编码、哈希、源要素属性、每要素顶点/环/孔洞/有效性见 data_audit.json 和 source_manifest.json。AT GreatArc 边与 GeoJSON 平面连线的连续曲线并不严格等价，顶点对应不代替边界口径确认。\n\n附件脚本审查：\n'+ '\n'.join('- '+x for x in result['script_review'])+
        '\n\n待确认：\n'+'\n'.join('- '+x for x in result['pending']),encoding='utf-8')
    print(json.dumps(dict(checks=checks,union_check=union_check,area_targets=at),ensure_ascii=False))
    return 0 if all(c['status']=='PASS' for c in checks) else 1
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    raise SystemExit(main(parser.parse_args().output))
