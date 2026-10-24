"""Cubic cell enclosures for candidate generation, never event truth.

The Bernstein convex-hull test is conservative for the fitted cubic. The
50-metre component guard is a declared engineering interpolation allowance,
not a proven bound for every possible trajectory. Real Access supplies events.
No along-track zero or root-distance deduplication is required.
"""
import numpy as np


def coefficients(times, values):
    t=np.asarray(times,dtype=float);v=np.asarray(values,dtype=float)
    if len(t)<4 or v.shape!=(len(t),3) or not np.all(np.isfinite(v)) or not np.all(np.isfinite(t)) or not np.all(np.diff(t)>0):
        raise ValueError('UNRESOLVED_TRAJECTORY: cubic enclosure requires four finite ordered states')
    n=len(t)-1;j=np.clip(np.arange(n)-1,0,len(t)-4);width=np.diff(t)
    nodes=np.stack([(t[j+k]-t[:-1])/width for k in range(4)],axis=1)
    a,b,c,d=[nodes[:,k,None] for k in range(4)]
    y=[v[j+k] for k in range(4)]
    q=[(y[k+1]-y[k])/(nodes[:,k+1,None]-nodes[:,k,None]) for k in range(3)]
    r=[(q[k+1]-q[k])/(nodes[:,k+2,None]-nodes[:,k,None]) for k in range(2)]
    s=(r[1]-r[0])/(d-a)
    power=np.stack([y[0]-q[0]*a+r[0]*a*b-s*a*b*c,
        q[0]-r[0]*(a+b)+s*(a*b+a*c+b*c),r[0]-s*(a+b+c),s],axis=1)
    if not np.all(np.isfinite(power)):raise ValueError('UNRESOLVED_NONFINITE_POLYNOMIAL')
    return power


def bernstein(power):
    c0,c1,c2,c3=[power[:,i] for i in range(4)]
    return np.stack([c0,c0+c1/3,c0+2*c1/3+c2/3,c0+c1+c2+c3],axis=1)


def cell_windows(times,body,along_half_deg,cross_half_deg,guard_km):
    t=np.asarray(times,dtype=float);p=coefficients(t,body)
    aa=np.tan(np.radians(along_half_deg));cc=np.tan(np.radians(cross_half_deg))
    # All five inequalities must be possible. A common positive interval is
    # found from every polynomial boundary, including repeated roots.
    f=np.stack([aa*p[:,:,2]-p[:,:,0],aa*p[:,:,2]+p[:,:,0],
        cc*p[:,:,2]-p[:,:,1],cc*p[:,:,2]+p[:,:,1],p[:,:,2]],axis=2)
    f[:,0,:4]+=guard_km
    bound=bernstein(f);possible=np.all(np.max(bound,axis=1)>=0,axis=1)
    records=[]
    # Retain rejected along-envelope opportunities and aggregate every cell's
    # disposition. No costly exact states are used for rejected cubics.
    along_possible=np.all(np.max(bound[:,:,:2],axis=1)>=0,axis=1)
    for i in np.flatnonzero(along_possible):
        item=dict(cell_index=int(i),coarse_bracket=[float(t[i]),float(t[i+1])],
            bernstein_upper_km=np.max(bound[i],axis=0).tolist(),possible_intervals=[])
        if not possible[i]:
            item['reason_code']='CUBIC_CROSS_OR_BORESIGHT_ENCLOSURE_REJECT';records.append(item);continue
        cuts=[0.,1.]
        for k in range(5):
            c=f[i,:,k].copy()
            while len(c)>1 and abs(c[-1])<1e-14:c=c[:-1]
            if len(c)>1:
                for r in np.polynomial.polynomial.polyroots(c):
                    if abs(r.imag)<1e-7 and -1e-10<=r.real<=1+1e-10:cuts.append(float(np.clip(r.real,0,1)))
        cuts=sorted(set(cuts))
        for a,b in zip(cuts,cuts[1:]):
            mid=(a+b)/2
            if np.all(np.array([np.polynomial.polynomial.polyval(mid,f[i,:,k]) for k in range(5)])>=-1e-10):
                item['possible_intervals'].append([float(t[i]+a*(t[i+1]-t[i])),float(t[i]+b*(t[i+1]-t[i]))])
        item['reason_code']='CUBIC_POSSIBLE_FOV' if item['possible_intervals'] else 'CUBIC_NO_COMMON_FOV_INTERVAL'
        records.append(item)
    return records,dict(total_cells=len(t)-1,along_enclosure_rejected=int(sum(~along_possible)),
        candidate_cells=sum(bool(r['possible_intervals']) for r in records),guard_km=guard_km,
        scope='Conservative for fitted cubics plus declared guard; finite validation, not a universal J2 interpolation bound')


def normalize_sources(records,half_window,duration):
    windows=[]
    for index,r in enumerate(records):
        for interval in r['possible_intervals']:
            windows.append(dict(window=[max(0.,interval[0]-half_window),min(duration,interval[1]+half_window)],source_indices=[index]))
    groups=[]
    for w in sorted(windows,key=lambda x:x['window']):
        if groups and w['window'][0]<=groups[-1]['window'][1]:
            groups[-1]['window'][1]=max(groups[-1]['window'][1],w['window'][1])
            groups[-1]['source_indices']=sorted(set(groups[-1]['source_indices']+w['source_indices']))
        else:groups.append(w)
    return groups
