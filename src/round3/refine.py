"""Safeguarded scalar root refinement with a verified final time bracket."""
import math

def root(fn, left, right, tolerance=1e-9):
    fl,fr=fn(left),fn(right)
    if not math.isfinite(fl+fr) or fl*fr>=0:
        raise ValueError('Finite opposite-sign bracket required')
    slow=0
    for _ in range(100):
        width=right-left
        if width<=tolerance:return (left+right)/2
        x=left-fl*(right-left)/(fr-fl)
        if not left<x<right or slow>=2:
            x=(left+right)/2;slow=0
        fx=fn(x)
        if not math.isfinite(fx):raise ValueError('Nonfinite root value')
        if fx==0:return x
        # Verify the small bracket around the secant estimate instead of
        # requiring dozens of slow COM bisection calls on an almost linear edge.
        a=max(left,x-tolerance*.49);b=min(right,x+tolerance*.49)
        fa,fb=fn(a),fn(b)
        if not math.isfinite(fa+fb):raise ValueError('Nonfinite root probes')
        if fa*fb<=0:return (a+b)/2
        if fl*fx<0:right,fr=x,fx
        else:left,fl=x,fx
        slow=slow+1 if right-left>width*.8 else 0
    raise RuntimeError('Root refinement did not converge')
