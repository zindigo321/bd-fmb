"""Deterministic equal-population Walker Delta, with first-order J2 SSO."""
import csv
import json
import math
from pathlib import Path

# Explicit provisional engineering constants; mean radius defines altitude ONLY.
CONSTANTS = dict(mean_radius_km=6371.0088, j2_reference_radius_km=6378.137,
                 mu_km3_s2=398600.4418, j2=1.08262668e-3, tropical_year_days=365.2421897)
RESIDUAL_TOL_RAD_S = 1e-15

def node_rate(h_km, i_deg, constants=None):
    constants = CONSTANTS if constants is None else constants
    a = constants['mean_radius_km'] + h_km
    n = math.sqrt(constants['mu_km3_s2'] / a**3)
    return -1.5 * constants['j2'] * n * (constants['j2_reference_radius_km']/a)**2 * math.cos(math.radians(i_deg))

def target_rate(constants=None):
    constants = CONSTANTS if constants is None else constants
    return 2*math.pi/(constants['tropical_year_days']*86400)

def sso_inclination(h_km, constants=None):
    constants = CONSTANTS if constants is None else constants
    if not math.isfinite(h_km) or not 500 <= h_km <= 700:
        raise ValueError('h_km must be finite and in [500,700]')
    a = constants['mean_radius_km'] + h_km
    coefficient = -1.5 * constants['j2'] * math.sqrt(constants['mu_km3_s2']/a**3) * (constants['j2_reference_radius_km']/a)**2
    return math.degrees(math.acos(target_rate(constants)/coefficient))

def generate(n=28, planes=7, phase_factor=1, h_km=600., raan0_deg=0., u0_deg=0., constants=None):
    constants = CONSTANTS if constants is None else constants
    for label, value in [('n',n),('planes',planes),('phase_factor',phase_factor)]:
        if type(value) is not int: raise ValueError(label+' must be an integer')
    if not 2 <= n <= 100: raise ValueError('Formal n must be in [2,100]; n=1 is diagnostic only')
    if not 1 <= planes <= n or n % planes:
        raise ValueError('Only equal-population Walker Delta supported: planes must divide n')
    if not 0 <= phase_factor < planes: raise ValueError('phase_factor must be in [0,planes)')
    if not all(math.isfinite(x) for x in (h_km,raan0_deg,u0_deg)): raise ValueError('non-finite parameter')
    inclination = sso_inclination(h_km, constants)
    per_plane = n//planes
    rows=[dict(sat_id=f'Sat{p*per_plane+s+1:03d}',h_km=h_km,i_deg=inclination,
               raan_deg=(raan0_deg+360*p/planes)%360,
               u_deg=(u0_deg+360*s/per_plane+360*phase_factor*p/n)%360)
          for p in range(planes) for s in range(per_plane)]
    config=dict(schema_version=1, family='Walker-Delta', n=n, planes=planes, per_plane=per_plane,
                phase_factor=phase_factor, raan0_deg=raan0_deg%360,u0_deg=u0_deg%360,
                plane_raan_deg=[(raan0_deg+360*p/planes)%360 for p in range(planes)],
                plane_phase_offset_deg=[360*phase_factor*p/n for p in range(planes)],
                h_km=h_km, debug_baseline=True, eccentricity=0, arg_perigee_deg=0,
                epoch='2027-01-01 00:00:00', time_system='UTCG', time_system_provisional=True,
                reference_frame='J2000', altitude_definition='a = mean_radius_km + h_km',
                constants=constants, constants_provisional=True,
                sso_formula='Omega_dot = -3/2 J2 sqrt(mu/a^3) (R_J2/[a(1-e^2)])^2 cos(i)',
                target_node_rate_rad_s=target_rate(constants), target_node_rate_deg_day=math.degrees(target_rate(constants))*86400,
                residual_tolerance_rad_s=RESIDUAL_TOL_RAD_S,
                model_note='First-order secular mean-element J2 relation; not proof of STK long-term nodal drift',
                propagation_step_s=30, report_sample_step_s=30, smoke_duration_s=7200,
                sensor_assumption='Half angles provisionally 0.001622 deg along X and 0.81 deg across Y; identity mounting; +Z geocentric nadir')
    validate(rows,n,constants)
    return rows,config

def validate(rows, expected_n, constants=None):
    if len(rows)!=expected_n or len({r['sat_id'] for r in rows})!=expected_n: raise ValueError('count or IDs invalid')
    residuals=[]
    for row in rows:
        if not all(math.isfinite(row[k]) for k in ('h_km','i_deg','raan_deg','u_deg')): raise ValueError('non-finite orbit')
        if not 500<=row['h_km']<=700 or not 0<row['i_deg']<180: raise ValueError('orbit range')
        if not all(0<=row[k]<360 for k in ('raan_deg','u_deg')): raise ValueError('angles must be normalized')
        residual=abs(node_rate(row['h_km'],row['i_deg'],constants)-target_rate(constants))
        if residual>RESIDUAL_TOL_RAD_S: raise ValueError('SSO nodal-rate residual exceeds tolerance')
        residuals.append(residual)
    return dict(count=len(rows),max_sso_residual_rad_s=max(residuals))

def export(output, **kwargs):
    rows,config=generate(**kwargs)
    path=Path(output); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    path.with_suffix('.config.json').write_text(json.dumps(config,indent=2,ensure_ascii=False),encoding='utf-8')
    return validate(rows,len(rows))
