"""Read-only reuse with a new current-run envelope; no old run_id mutation."""
import csv
from pathlib import Path
import shutil
from src.constellation import generate
from src.round2.evidence import KEYS, canonical, digest, read, write

EXPECTED='860d8f4b2b9391adaf68740bf43c500bdcd074f4bd579971d864332a6eb89071'


def bind(ctx,cfg):
    source=Path(cfg['round4']['source_round3'])/'grid'
    old=read(source/'frozen.json');points=old['points']
    if old['run_id']!=cfg['round4']['source_run_id'] or digest(canonical(points))!=EXPECTED or old['point_set_sha256']!=EXPECTED:
        raise ValueError('Frozen source identity/point sequence mismatch')
    with (source/'fixed_grid.csv').open(encoding='utf-8',newline='') as f:csvpoints=list(csv.DictReader(f))
    if len(points)!=16 or len(csvpoints)!=16:raise ValueError('Frozen count mismatch')
    for p,c in zip(points,csvpoints):
        if (p['point_id']!=c['point_id'] or any(p[k]!=float(c[k]) for k in ['latitude_deg','longitude_deg','altitude_km'])
            or p['use_terrain'] != (c['use_terrain']=='True') or p['use_terrain'] or p['altitude_km']!=0):raise ValueError('Frozen CSV/surface differs')
    with (source/'full_grid.csv').open(encoding='utf-8',newline='') as f:full=list(csv.DictReader(f))
    typed=[dict(point_id=x['point_id'],latitude_deg=float(x['latitude_deg']),longitude_deg=float(x['longitude_deg']),altitude_km=float(x['altitude_km']),use_terrain=x['use_terrain']=='True',component=int(x['component'])) for x in full]
    if len(full)!=3264 or digest(canonical(typed))!=old['full_grid_sha256']:raise ValueError('Full grid source hash mismatch')
    folder=Path(ctx['output'])/'grid';folder.mkdir()
    sources={n:dict(path=str(source/n),sha256=digest((source/n).read_bytes())) for n in ['frozen.json','fixed_grid.csv','full_grid.csv']}
    wrapper=dict(**{k:ctx[k] for k in KEYS},purpose='FIXED_DEBUG_SUBSET_NOT_COMPLETE_REGION',
        points=points,point_set_sha256=EXPECTED,source_run_id=old['run_id'],sources=sources,
        original_metadata={k:v for k,v in old.items() if k not in ['run_id','points']},
        limitation='16 mainland points, no points in the two island components; no replacement or extra points')
    write(folder/'frozen.json',wrapper)
    for name in ['fixed_grid.csv','full_grid.csv']:shutil.copy2(source/name,folder/name)
    all_rows,derived=generate(**cfg['constellation'],constants=cfg['earth'])
    firsts=all_rows[::derived['per_plane']]
    selection=dict(**{k:ctx[k] for k in KEYS},baseline_count=len(all_rows),baseline=all_rows,
        baseline_sha256=digest(canonical(all_rows)),rule='First satellite of each of the first 2, 4 or 7 original planes',
        plane_first_satellites=firsts,tiers=[dict(t,selected=firsts[:t['planes']]) for t in cfg['round4']['tiers']])
    write(Path(ctx['output'])/'selection.json',selection)
    return wrapper,selection
