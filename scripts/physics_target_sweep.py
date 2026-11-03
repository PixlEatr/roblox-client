"""Continuous proximity on a constant-acceleration movement segment."""
import math


def _quadratic_roots(a, b, c):
    if a == 0:
        return [] if b == 0 else [-c / b]
    scale = max(abs(a), abs(b), abs(c))
    a, b, c = a / scale, b / scale, c / scale
    discriminant = b*b - 4*a*c
    if discriminant < 0:
        return []
    q = -.5 * (b + math.copysign(math.sqrt(discriminant), b))
    return [-b / (2*a)] if q == 0 else [q/a, c/q]


def curve_reaches_target(start, velocity, acceleration, dt, target, radius=.1):
    """Check the entire parabola, including tangent contacts and reversals.

    Squared distance from a quadratic path is quartic. Its derivative is a
    cubic, whose monotone intervals are bounded by a quadratic's roots. Test
    those intervals' stationary points and endpoints instead of sampling time
    or treating a curved trajectory as a straight chord.
    """
    for vector in (start, velocity, acceleration, target):
        if (not isinstance(vector, (tuple, list)) or len(vector) != 3
                or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)):
            raise ValueError('target sweep vectors must have three finite numbers')
    if (type(dt) not in (int, float) or not math.isfinite(dt) or not 0 <= dt <= .25
            or type(radius) not in (int, float) or not math.isfinite(radius) or radius < 0):
        raise ValueError('invalid target sweep duration or radius')
    # Normalize time to [0,1] to avoid tiny dt polynomial coefficients.
    a = tuple(.5*acceleration[i]*dt*dt for i in range(3))
    b = tuple(velocity[i]*dt for i in range(3))
    c = tuple(start[i]-target[i] for i in range(3))
    dot = lambda x, y: math.fsum(x[i]*y[i] for i in range(3))
    coefficients = (2*dot(a,a), 3*dot(a,b), dot(b,b)+2*dot(a,c), dot(b,c))
    scale = max(map(abs, coefficients))
    if scale == 0:
        return math.hypot(*c) <= radius
    p, q, r, s = (x/scale for x in coefficients)
    evaluate = lambda t: ((p*t+q)*t+r)*t+s
    cuts = sorted({0., 1., *(t for t in _quadratic_roots(3*p, 2*q, r) if 0 < t < 1)})
    candidates = list(cuts)
    for low, high in zip(cuts, cuts[1:]):
        fl, fh = evaluate(low), evaluate(high)
        if (fl > 0) == (fh > 0):
            continue
        for _ in range(64):
            middle = (low+high)*.5
            fm = evaluate(middle)
            if (fm > 0) == (fl > 0):
                low, fl = middle, fm
            else:
                high = middle
        candidates.append((low+high)*.5)
    return any(math.hypot(*((a[i]*t+b[i])*t+c[i] for i in range(3))) <= radius
               for t in candidates)
