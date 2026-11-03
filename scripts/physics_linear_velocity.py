"""Bounded local sequential-impulse model for LinearVelocity constraints.

This projects velocity onto orthonormal vector, line, or plane axes. Impulse
limits accumulate across iterations, so repeated passes do not multiply the
allowed force. This is not a claim of Roblox solver equivalence.
"""
import math


def scalar(value, *, limit=False):
    if limit and value=={'$number':'inf'}:value=math.inf
    if (type(value) not in (int,float) or math.isnan(value)
            or (limit and value<0) or (not limit and (not math.isfinite(value) or abs(value)>1e6))):
        raise ValueError('invalid velocity constraint number')
    return float(value)


def vector(value, size=3, *, limit=False):
    if isinstance(value,dict):value=value.get('$vector'+str(size))
    if not isinstance(value,(list,tuple)) or len(value)!=size:raise ValueError('invalid constraint vector')
    return tuple(scalar(x,limit=limit) for x in value)


def unit(value):
    value=vector(value);length=math.hypot(*value)
    if length<1e-9:raise ValueError('constraint direction is zero')
    return tuple(x/length for x in value)


def validated_constraint(source):
    axes=tuple(vector(v) for v in source['axes'])
    if len(axes) not in (1,2,3):raise ValueError('invalid constraint dimension')
    for i,a in enumerate(axes):
        if abs(math.hypot(*a)-1)>1e-6:raise ValueError('constraint axis must be unit length')
        if any(abs(sum(a[k]*b[k] for k in range(3)))>1e-6 for b in axes[:i]):
            raise ValueError('constraint axes must be orthogonal')
    targets=vector(source['targets'],len(axes))
    mode=source['limit_mode']
    if mode not in ('Unlimited','Magnitude','PerAxis'):raise ValueError('invalid force limit mode')
    limits=vector(source.get('limits',(math.inf,)*len(axes)),len(axes),limit=True)
    maximum=scalar(source.get('max_force',math.inf),limit=True)
    reference=vector(source.get('reference_velocity',(0,0,0)))
    result={'axes':axes,'targets':targets,'limit_mode':mode,'limits':limits,
            'max_force':maximum,'reference_velocity':reference}
    if 'root_reference_yaw' in source:
        result['root_reference_yaw']=scalar(source['root_reference_yaw'])
    return result


def constraint_acceleration(velocity, sources, mass, dt):
    if not sources or dt==0:return (0.,0.,0.)
    if (type(mass) not in (int,float) or not math.isfinite(mass) or mass<=0
            or type(dt) not in (int,float) or not math.isfinite(dt) or not 0<dt<=.25):
        raise ValueError('constraint mass and timestep must be finite and positive')
    current=list(velocity)
    impulses=[[0.]*len(s['axes']) for s in sources]
    for _ in range(24):
        largest=0.
        for source,old in zip(sources,impulses):
            relative=tuple(current[i]-source['reference_velocity'][i] for i in range(3))
            proposed=[old[i]+mass*(source['targets'][i]-sum(a[k]*relative[k] for k in range(3)))
                      for i,a in enumerate(source['axes'])]
            if source['limit_mode']=='Magnitude':
                length=math.hypot(*proposed);cap=source['max_force']*dt
                if length>cap:proposed=[v*(cap/length) for v in proposed]
            elif source['limit_mode']=='PerAxis':
                proposed=[max(-limit*dt,min(limit*dt,v)) for v,limit in zip(proposed,source['limits'])]
            change=[(proposed[i]-old[i])/mass for i in range(len(old))]
            for k in range(3):current[k]+=sum(a[k]*dv for a,dv in zip(source['axes'],change))
            largest=max(largest,math.hypot(*change));old[:]=proposed
        if largest<1e-9:break
    return tuple((current[i]-velocity[i])/dt for i in range(3))
