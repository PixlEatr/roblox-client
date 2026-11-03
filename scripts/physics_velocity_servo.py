"""Stable local integration of capped proportional BodyVelocity feedback.

Uses backward Euler for feedback (not Roblox solver equivalence), with one
monotone scalar solve per axis. Force caps remain per source and per axis.
"""
import math


def servo_acceleration(velocity, sources, mass, dt, external_acceleration=(0.,0.,0.)):
    return solve_movers(velocity, sources, mass, dt, external_acceleration)[0]


def solve_movers(velocity, sources, mass, dt, external_acceleration=(0.,0.,0.),
                 *, position=None, position_sources=()):
    """Joint implicit feedback solve, with trapezoidal position integration.

    Each spring contributes P*(goal - x_next) - D*v_next. Substituting
    x_next=x_start+dt*(v_start+v_next)/2 leaves a monotone linear feedback
    term before its per-axis force cap. This is a local model, not a claim
    about Roblox's numerical solver. Return acceleration and each spring's
    actually solved force, for script feedback.
    """
    if (not sources and not position_sources) or dt == 0:return (0.,0.,0.), {}
    if (type(mass) not in (int,float) or not math.isfinite(mass) or mass<=0
            or type(dt) not in (int,float) or not math.isfinite(dt) or not 0<dt<=.25):
        raise ValueError('servo mass and timestep must be finite and positive')
    result=[]
    spring_forces={s["id"]:[0.,0.,0.] for s in position_sources}
    for axis in range(3):
        start=velocity[axis];free=start+external_acceleration[axis]*dt
        active=[(s['gain'],s['target_velocity'][axis],s['max_force'][axis])
                for s in sources if s['gain']>0 and s['max_force'][axis]>0]
        springs=[]
        for s in position_sources:
            gain=s['damping']+s['gain']*dt*.5
            if gain<=0 or s['max_force'][axis]<=0:continue
            drive=s['gain']*(s['target_position'][axis]-position[axis]-velocity[axis]*dt*.5)
            entry=(gain,drive/gain,s['max_force'][axis])
            active.append(entry);springs.append((s['id'],entry))
        if not active:result.append(0.);continue
        low=min([free]+[target for _,target,_ in active])
        high=max([free]+[target for _,target,_ in active])
        # v_next - v_free - dt/m * sum(clamp(P*(goal-v_next))) is
        # increasing, so this bracket contains its unique zero at any gain.
        for _ in range(60):
            middle=(low+high)*.5
            force=sum(max(-limit,min(limit,gain*(target-middle)))
                      for gain,target,limit in active)
            residual=middle-free-dt*force/mass
            if residual>0:high=middle
            else:low=middle
        end=(low+high)*.5
        result.append((end-free)/dt)
        for identifier,(gain,target,limit) in springs:
            spring_forces[identifier][axis]=max(-limit,min(limit,gain*(target-end)))
    return tuple(result), spring_forces
