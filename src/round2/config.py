import math
from .evidence import read

def load_config(path):
    c=read(path)
    if c.get('schema_version')!=2:raise ValueError('config schema must be 2')
    if c['time_system']!='UTCG' or c['reference_frame']!='J2000':raise ValueError('Only explicit UTCG/J2000 supported')
    if c['camera']['attitude_profile']!='NadirECIVel' or c['camera']['constraint_offset_deg']!=0 or c['camera']['mount_quaternion_xyzw']!=[0,0,0,1]:raise ValueError('This diagnostic supports nadir/ECI velocity and identity camera mounting only')
    if c['diagnostic_surface']['use_terrain'] or c['diagnostic_surface']['altitude_km']!=0:raise ValueError('This experiment requires the declared zero-height ellipsoid, with terrain disabled for diagnostic points only')
    for k,v in c['earth'].items():
        if not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError('invalid Earth constant '+k)
    if not 0<c['diagnostic_time_s']-c['diagnostic_half_window_s']<c['diagnostic_time_s']+c['diagnostic_half_window_s']<c['duration_s']<=7200:raise ValueError('invalid diagnostic window')
    if c['propagation_step_s']<=0 or c['report_sample_step_s']<=0:raise ValueError('invalid step')
    if len(c['access_tiers'])<3:raise ValueError('three tiers required')
    for tier in c['access_tiers']:
        if not 0.001<=tier['min_step_s']<=tier['max_step_s'] or tier['time_convergence_s']<=0:raise ValueError('invalid access settings')
    return c
