"""Independent Euclidean/ellipsoidal geometry; does not call STK Access."""
import math
import numpy as np

def unit(v):
    v=np.asarray(v,dtype=float);norm=np.linalg.norm(v)
    if not np.isfinite(norm) or norm==0:raise ValueError('invalid vector')
    return v/norm

def body_basis(position,velocity):
    z=-unit(position);x=unit(velocity-np.dot(velocity,z)*z);y=unit(np.cross(z,x))
    return np.column_stack([x,y,z])

def quaternion_matrix(q):
    x,y,z,w=unit(q)
    return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
                     [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
                     [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])

def ray_ellipsoid(origin,direction,a,b):
    origin=np.asarray(origin);direction=unit(direction);scale=np.array([a,a,b])
    o=origin/scale;d=direction/scale
    roots=np.roots([np.dot(d,d),2*np.dot(o,d),np.dot(o,o)-1])
    positive=[float(x.real) for x in roots if abs(x.imag)<1e-10 and x.real>0]
    if not positive:raise ValueError('ray does not intersect visible ellipsoid')
    return origin+min(positive)*direction

def ecef_to_geodetic(p,a,b):
    x,y,z=p;lon=math.atan2(y,x);rho=math.hypot(x,y);e2=1-b*b/(a*a)
    lat=math.atan2(z,rho*(1-e2))
    for _ in range(15):
        n=a/math.sqrt(1-e2*math.sin(lat)**2)
        lat=math.atan2(z+e2*n*math.sin(lat),rho)
    n=a/math.sqrt(1-e2*math.sin(lat)**2)
    height=rho/math.cos(lat)-n if abs(math.cos(lat))>1e-10 else abs(z)-b
    return math.degrees(lat),math.degrees(lon),height

def geodetic_to_ecef(lat,lon,h,a,b):
    lat,lon=map(math.radians,[lat,lon]);e2=1-b*b/(a*a);n=a/math.sqrt(1-e2*math.sin(lat)**2)
    return np.array([(n+h)*math.cos(lat)*math.cos(lon),(n+h)*math.cos(lat)*math.sin(lon),(n*(1-e2)+h)*math.sin(lat)])

def angles(position,basis,target):
    v=basis.T@(np.asarray(target)-position)
    return math.degrees(math.atan2(v[0],v[2])),math.degrees(math.atan2(v[1],v[2])),float(v[2])

def visible(position,target,a,b):
    normal=unit(np.asarray(target)/np.array([a*a,a*a,b*b]))
    return bool(np.dot(position-target,normal)>0)

def bisect_crossing(fn,left,right,tolerance=1e-9):
    fl=fn(left);fr=fn(right)
    if fl*fr>=0:raise ValueError('root not bracketed')
    for _ in range(70):
        if right-left<=tolerance:break
        mid=(left+right)/2;fm=fn(mid)
        if fl*fm<=0:right=mid
        else:left=mid;fl=fm
    return (left+right)/2
