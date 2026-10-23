"""Freeze the subset before trajectory or access calculations."""
import csv
import json
import math
from pathlib import Path
from shapely.geometry import shape, Point
from shapely.ops import unary_union
from src.round2.evidence import canonical, digest, write


def freeze(boundary, cfg, folder, run_id):
    raw=Path(boundary).read_bytes()
    data=json.loads(raw)
    features=data['features'] if data['type']=='FeatureCollection' else [data]
    geometry=unary_union([shape(f['geometry']) for f in features])
    if not geometry.is_valid or geometry.is_empty:
        raise ValueError('Boundary invalid; no repair permitted')
    step=cfg['spacing_deg']; ax=cfg['anchor_lon']; ay=cfg['anchor_lat']
    x0,y0,x1,y1=geometry.bounds
    full=[]
    components=list(geometry.geoms) if geometry.geom_type=='MultiPolygon' else [geometry]
    for iy in range(math.ceil((y0-ay)/step),math.floor((y1-ay)/step)+1):
        for ix in range(math.ceil((x0-ax)/step),math.floor((x1-ax)/step)+1):
            lon=round(ax+ix*step,10);lat=round(ay+iy*step,10);pt=Point(lon,lat)
            if geometry.covers(pt):
                full.append(dict(point_id=f'G_{iy:04d}_{ix:04d}',latitude_deg=lat,longitude_deg=lon,
                                 altitude_km=0.0,use_terrain=False,
                                 component=next(i for i,g in enumerate(components) if g.covers(pt))))
    n=cfg['count']
    if len(full)<n or n<2:
        raise ValueError('Insufficient fixed grid points')
    ranks=[i*(len(full)-1)//(n-1) for i in range(n)]
    points=[full[i] for i in ranks]
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    for name,rows in [('full_grid.csv',full),('fixed_grid.csv',points)]:
        with (folder/name).open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    frozen=dict(run_id=run_id,purpose='FIXED_DEBUG_SUBSET_NOT_COMPLETE_REGIONAL_EVALUATION',
        boundary_sha256=digest(raw),point_set_sha256=digest(canonical(points)),
        full_grid_sha256=digest(canonical(full)),full_grid_count=len(full),points=points,
        rule='0.1 degree (0,0) anchor; GeoJSON planar lon/lat covers (boundary included); lat/lon sorted equal integer ranks',
        selected_ranks=ranks,component_count=len(components),
        component_grid_counts=[sum(p['component']==i for p in full) for i in range(len(components))],
        component_subset_counts=[sum(p['component']==i for p in points) for i in range(len(components))],
        assumptions=['WGS84 assumed; datum provenance unconfirmed',
                     'GeoJSON straight lon/lat segments differ from STK GreatArc edges',
                     'All polygons and islands retained; no forced island point insertion',
                     'Ellipsoid zero height, terrain disabled; no diagnostic points in denominator'])
    write(folder/'frozen.json',frozen)
    return frozen
