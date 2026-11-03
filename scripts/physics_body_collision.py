"""Bounded static-world response for a sized local character proxy.

The proxy is an axis-aligned root box, not Roblox's native avatar hull. Static
AABB/OBB and decoded triangle/voxel geometry use swept contact queries; the
received world is read-only and ground contact remains the separate foot model.
"""
from dataclasses import dataclass
import math

from movement_collision import _vector, sweep_aabb_obb, shallow_overlap_allows_motion
from physics_ground import (AABB, GroundGeometry, GroundStep, OBB, TriangleMesh, VoxelGrid,
                            follow_ground, step_vertical, ground_raycast)
from physics_stairs import walkable_normal, try_step_up

IDENTITY = (1., 0., 0., 0., 1., 0., 0., 0., 1.)
SKIN = 1e-6
MAX_CONTACTS = 4


@dataclass(frozen=True)
class BodyMove:
    position: tuple[float, float, float]
    normals: tuple[tuple[float, float, float], ...] = ()
    initial_overlap: bool = False
    limited: bool = False


def clip_horizontal(vector, normals):
    """Remove motion into contact planes without adding speed or lifting Y."""
    x, _, z = vector
    for normal in normals:
        length = math.hypot(normal[0], normal[2])
        if length < 1e-9:
            return (0., 0., 0.)
        nx, nz = normal[0]/length, normal[2]/length
        into = x*nx + z*nz
        if into < 0.:
            x, z = x-into*nx, z-into*nz
    # A later plane may turn the vector back into an earlier one. Stop a
    # wedged corner instead of iterating indefinitely or crossing either wall.
    if any(x*n[0]+z*n[2] < -1e-9 for n in normals):
        return (0., 0., 0.)
    return (x, 0., z)


def _first_hit(position, half_size, delta, geometry, *, allow_overlap_escape=False):
    lower = tuple(min(position[i], position[i]+delta[i])-half_size[i] for i in range(3))
    upper = tuple(max(position[i], position[i]+delta[i])+half_size[i] for i in range(3))
    best = None
    for box in geometry.colliders:
        if any(upper[i] < box.minimum[i]-SKIN or lower[i] > box.maximum[i]+SKIN for i in range(3)):
            continue
        if isinstance(box, TriangleMesh):
            hit = _sweep_aabb_mesh(position, half_size, delta, box)
            if hit is not None and (best is None or hit.distance < best.distance):
                best = hit
            continue
        if isinstance(box, VoxelGrid):
            for cell in _candidate_voxel_boxes(box, lower, upper):
                center = tuple((cell.minimum[i]+cell.maximum[i])/2 for i in range(3))
                half = tuple((cell.maximum[i]-cell.minimum[i])/2 for i in range(3))
                hit = sweep_aabb_obb(position, half_size, delta, center, half, IDENTITY)
                if (allow_overlap_escape and hit is not None and hit.normal == (0., 0., 0.)
                        and shallow_overlap_allows_motion(position, half_size, delta, center, half, IDENTITY)):
                    continue
                if hit is not None and (best is None or hit.distance < best.distance):
                    best = hit
            continue
        if isinstance(box, OBB):
            center, half, rotation = box.center, box.half_size, box.rotation
        else:
            center = tuple((box.minimum[i]+box.maximum[i])/2 for i in range(3))
            half = tuple((box.maximum[i]-box.minimum[i])/2 for i in range(3))
            rotation = IDENTITY
        hit = sweep_aabb_obb(position, half_size, delta, center, half, rotation)
        if (allow_overlap_escape and hit is not None and hit.normal == (0., 0., 0.)
                and shallow_overlap_allows_motion(position, half_size, delta, center, half, rotation)):
            continue
        if hit is not None and (best is None or hit.distance < best.distance):
            best = hit
    return best


def vertical_body_clearance(position, rise, half_size, geometry, *, support_collider):
    """Check a vertical root-box correction against every non-support collider.

    The support exception is object-identity based and must be the collider
    returned by a successful ground query. No other collider is ignored; a
    strict initial overlap or an intervening thin obstacle blocks correction.
    """
    point = _vector(position, 'position')
    half = _vector(half_size, 'half_size')
    if not isinstance(geometry, GroundGeometry):
        raise ValueError('clearance needs ground geometry')
    if (isinstance(rise, bool) or not isinstance(rise, (int, float))
            or not math.isfinite(rise) or rise < 0):
        raise ValueError('rise must be finite and non-negative')
    if any(v <= 0 for v in half):
        raise ValueError('half sizes must be positive')
    if not any(collider is support_collider for collider in geometry.colliders):
        return False
    if rise <= SKIN:
        return True
    # Triangle/Voxel supports may contain unrelated faces/cells (including a
    # ceiling) inside one collider record. Without per-face support identity,
    # fail closed rather than exempting that entire shape.
    if isinstance(support_collider, (TriangleMesh, VoxelGrid)):
        return False
    blockers = GroundGeometry(tuple(collider for collider in geometry.colliders
                                    if collider is not support_collider), geometry.unsupported)
    return _first_hit(point, half, (0., float(rise), 0.), blockers) is None


def _candidate_voxel_boxes(grid, lower, upper):
    """Yield occupied cells intersecting the swept root-box broad phase."""
    ox, oy, oz = grid.origin
    sx, sy, sz = grid.cell_size
    nx, ny, nz = grid.dimensions
    sizes = (sx, sy, sz)
    origin = (ox, oy, oz)
    first = [max(0, math.floor((lower[i]-origin[i])/sizes[i])) for i in range(3)]
    last = [min(grid.dimensions[i]-1, math.floor((upper[i]-origin[i])/sizes[i])) for i in range(3)]
    for ix in range(first[0], last[0]+1):
        for iy in range(first[1], last[1]+1):
            for iz in range(first[2], last[2]+1):
                index = (ix*ny+iy)*nz+iz
                if not grid.occupancy[index]:
                    continue
                cell_min = (ox+ix*sx, oy+iy*sy, oz+iz*sz)
                cell_max = tuple(cell_min[i]+sizes[i] for i in range(3))
                yield AABB(cell_min, cell_max)


def _sweep_aabb_mesh(center, half, delta, mesh):
    """Continuous SAT sweep of an axis-aligned box against exact mesh triangles."""
    from movement_collision import Hit
    axes_box = ((1.,0.,0.), (0.,1.,0.), (0.,0.,1.))
    best = None
    start = center
    finish = tuple(center[i]+delta[i] for i in range(3))
    sweep_min = tuple(min(start[i], finish[i])-half[i] for i in range(3))
    sweep_max = tuple(max(start[i], finish[i])+half[i] for i in range(3))
    for indices in mesh.triangles:
        a,b,c = (mesh.vertices[i] for i in indices)
        tri_min = tuple(min(a[i],b[i],c[i]) for i in range(3))
        tri_max = tuple(max(a[i],b[i],c[i]) for i in range(3))
        if any(sweep_max[i] < tri_min[i]-SKIN or sweep_min[i] > tri_max[i]+SKIN for i in range(3)):
            continue
        edges = (tuple(b[i]-a[i] for i in range(3)),
                 tuple(c[i]-b[i] for i in range(3)),
                 tuple(a[i]-c[i] for i in range(3)))
        normal = (edges[0][1]*edges[1][2]-edges[0][2]*edges[1][1],
                  edges[0][2]*edges[1][0]-edges[0][0]*edges[1][2],
                  edges[0][0]*edges[1][1]-edges[0][1]*edges[1][0])
        axes = list(axes_box)
        if math.hypot(*normal) > 1e-12:
            axes.append(normal)
        for edge in edges:
            for axis in axes_box:
                cross = (edge[1]*axis[2]-edge[2]*axis[1],
                         edge[2]*axis[0]-edge[0]*axis[2],
                         edge[0]*axis[1]-edge[1]*axis[0])
                if math.hypot(*cross) > 1e-12:
                    axes.append(cross)
        near, far, contact_normal = -math.inf, math.inf, (0.,0.,0.)
        strictly_overlapping = True
        separated = False
        for axis in axes:
            projection = tuple(sum(p[i]*axis[i] for i in range(3)) for p in (a,b,c))
            low, high = min(projection), max(projection)
            c0 = sum(center[i]*axis[i] for i in range(3))
            speed = sum(delta[i]*axis[i] for i in range(3))
            radius = sum(half[i]*abs(axis[i]) for i in range(3))
            tol = 1e-10*max(1., radius, abs(low), abs(high))
            if c0+radius <= low+tol or c0-radius >= high-tol:
                strictly_overlapping = False
            if speed == 0.:
                if c0+radius < low-tol or c0-radius > high+tol:
                    separated = True
                    break
                continue
            enter1 = (low-radius-c0)/speed
            exit1 = (high+radius-c0)/speed
            if speed > 0:
                enter, exit = enter1, exit1
                candidate_normal = tuple(-v for v in axis)
            else:
                enter, exit = exit1, enter1
                candidate_normal = axis
            if enter > near:
                near, contact_normal = enter, candidate_normal
            far = min(far, exit)
            if near > far+tol:
                separated = True
                break
        if separated or far < -1e-10 or near > 1.+1e-10:
            continue
        hit = Hit(0., (0.,0.,0.)) if strictly_overlapping else Hit(max(0.,near), contact_normal)
        if best is None or hit.distance < best.distance:
            best = hit
    return best


def move_horizontal_body(position, displacement, half_size, geometry, *,
                         grounded, foot_offset=3.0, max_slope_angle=89.0,
                         max_step_height=1.0):
    """Sweep each proposed segment, clipping remaining motion at wall hits.

    On supported slopes each segment follows its support plane before testing
    the body. This avoids sweeping horizontally into the same ramp being
    climbed. Contacts are bounded. Shallow box penetration allows only requested
    escape or tangent motion; unresolved initial overlap still stops motion.
    """
    point = _vector(position, 'position')
    remaining = _vector(displacement, 'displacement')
    half = _vector(half_size, 'half_size')
    if remaining[1] != 0. or any(v <= 0 for v in half):
        raise ValueError('body movement needs horizontal displacement and positive half sizes')
    if not isinstance(geometry, GroundGeometry) or type(grounded) is not bool:
        raise ValueError('body movement needs ground geometry and a boolean grounded state')
    if (isinstance(max_slope_angle, bool) or not isinstance(max_slope_angle, (int, float))
            or not math.isfinite(max_slope_angle) or not 0 <= max_slope_angle <= 89):
        raise ValueError('max_slope_angle must be finite and between 0 and 89 degrees')
    if (isinstance(max_step_height, bool) or not isinstance(max_step_height, (int, float))
            or not math.isfinite(max_step_height) or max_step_height < 0):
        raise ValueError('max_step_height must be finite and non-negative')
    normals = []
    for _ in range(MAX_CONTACTS):
        if math.hypot(remaining[0], remaining[2]) <= 1e-10:
            return BodyMove(point, tuple(normals))
        followed = follow_ground(point, remaining, geometry, foot_offset=foot_offset) if grounded else None
        if followed is not None:
            support = ground_raycast(followed.position, geometry, foot_offset=foot_offset,
                                     max_distance=.05)
            if support is None or not walkable_normal(support.normal, max_slope_angle):
                followed = None
        rise = followed.position[1]-point[1] if followed is not None else 0.
        delta = (remaining[0], rise, remaining[2])
        hit = _first_hit(point, half, delta, geometry, allow_overlap_escape=True)
        if hit is None:
            return BodyMove(tuple(point[i]+delta[i] for i in range(3)), tuple(normals))
        if hit.normal == (0., 0., 0.):
            return BodyMove(point, tuple(normals), initial_overlap=True)
        if grounded and max_step_height > 0 and hit.normal[1] < 0.5:
            stepped = try_step_up(point, remaining, half, geometry,
                                  foot_offset=foot_offset, max_step_height=max_step_height,
                                  max_slope_angle=max_slope_angle)
            if stepped is not None:
                return BodyMove(stepped, tuple(normals))
        fraction = max(0., hit.distance-SKIN/math.hypot(*delta))
        point = tuple(point[i]+fraction*delta[i] for i in range(3))
        normals.append(hit.normal)
        rest = tuple((1-fraction)*v for v in remaining)
        remaining = clip_horizontal(rest, normals)
        if math.hypot(remaining[0]-rest[0], remaining[2]-rest[2]) <= 1e-10:
            return BodyMove(point, tuple(normals), limited=True)
    return BodyMove(point, tuple(normals), limited=True)


def step_vertical_accelerated(position, velocity, dt, geometry, grounded, *,
                              acceleration, half_size=None, foot_offset=3.,
                              max_fall_speed=512., max_slope_angle=89.):
    """Sweep a constant net vertical acceleration, including upward forces.

    Negative acceleration uses the established ballistic path. Positive
    acceleration splits at its lowest point, so floor and ceiling sweeps see
    both segments even when net displacement is zero. Each monotone segment
    is swept at its average speed; contact positions remain geometric.
    """
    if type(acceleration) not in (int,float) or not math.isfinite(acceleration):
        raise ValueError('vertical acceleration must be finite')
    kwargs=dict(half_size=half_size,foot_offset=foot_offset,
                max_fall_speed=max_fall_speed,max_slope_angle=max_slope_angle)
    if acceleration<=0:
        return step_vertical_body(position,velocity,dt,geometry,grounded,gravity=-acceleration,**kwargs)
    # Validate public step bounds before using dt as a divisor.
    step_vertical(position,velocity,0.,geometry,grounded,foot_offset=foot_offset,
                  gravity=0.,max_fall_speed=max_fall_speed,support_half_size=half_size,
                  max_slope_angle=max_slope_angle)
    if type(dt) not in (int,float) or not math.isfinite(dt) or not 0<=dt<=.25:
        raise ValueError('vertical timestep is outside supported bounds')
    if dt==0:return step_vertical_body(position,velocity,dt,geometry,grounded,gravity=0.,**kwargs)
    point=_vector(position,'position');start=max(-max_fall_speed,min(max_fall_speed,velocity))
    remaining=dt
    if start<0:
        duration=min(remaining,-start/acceleration)
        end=start+acceleration*duration
        fallen,hit=step_vertical_body(point,(start+end)*.5,duration,geometry,grounded,gravity=0.,**kwargs)
        if hit is not None and hit.initial_overlap:return fallen,hit
        if fallen.grounded:
            distance=min(0.,fallen.position[1]-point[1])
            speed=math.sqrt(max(0.,start*start+2*acceleration*distance))
            impact=2*distance/(start-speed) if start-speed else 0.
            remaining-=max(0.,min(duration,impact));start=0.
        else:
            remaining-=duration;start=end
        point=fallen.position
        if remaining<=1e-12:return GroundStep(point,start,fallen.grounded,fallen.hit),hit
    duration=min(remaining,max(0.,(max_fall_speed-start)/acceleration))
    end=min(max_fall_speed,start+acceleration*duration)
    displacement=start*duration+.5*acceleration*duration*duration+end*(remaining-duration)
    risen,hit=step_vertical_body(point,displacement/remaining,remaining,geometry,False,gravity=0.,**kwargs)
    if hit is not None:return risen,hit
    return GroundStep(risen.position,end,False,risen.hit),None


def step_vertical_body(position, vertical_velocity, dt, geometry, grounded, *,
                       half_size=None, foot_offset=3., gravity=196.2, max_fall_speed=512.,
                       max_slope_angle=89.):
    """Ballistic foot contact plus swept root-box ceilings and edge support.

    The rising segment ends at its apex or earliest ceiling. Any remaining
    frame time is integrated as a fall, so thin ceilings cannot be crossed by
    a step that contains both ascent and descent. A descending root box can
    also land on an upward face missed by the footprint probes. Shallow box
    overlap permits requested motion away from or alongside its nearest face;
    unresolved penetration is held without inventing a displacement.
    """
    kwargs = dict(foot_offset=foot_offset, gravity=gravity, max_fall_speed=max_fall_speed,
                  support_half_size=half_size, max_slope_angle=max_slope_angle)
    free = step_vertical(position, vertical_velocity, dt, geometry, grounded, **kwargs)
    if dt == 0 or half_size is None:
        return free, None
    point = _vector(position, 'position')
    half = _vector(half_size, 'half_size')
    if any(v <= 0 for v in half):
        raise ValueError('half_size must be positive')
    if not grounded and vertical_velocity > 0:
        rise_time = min(dt, vertical_velocity/gravity) if gravity > 0 else dt
        rise = vertical_velocity*rise_time-gravity*rise_time*rise_time/2
        hit = _first_hit(point, half, (0., rise, 0.), geometry, allow_overlap_escape=True)
        if hit is None:
            return free, None
        if hit.normal == (0., 0., 0.):
            return GroundStep(point, 0., False), BodyMove(point, initial_overlap=True)
        contact_distance = hit.distance*rise
        # Stable smaller root of d=v*t-g*t*t/2, also valid when gravity is zero.
        end_speed = math.sqrt(max(0., vertical_velocity**2-2*gravity*contact_distance))
        impact_time = 2*contact_distance/(vertical_velocity+end_speed)
        contact = (point[0], point[1]+max(0., contact_distance-SKIN), point[2])
        fallen = step_vertical(contact, 0., max(0., dt-impact_time), geometry, False, **kwargs)
        return fallen, BodyMove(contact, (hit.normal,))
    if vertical_velocity > 0 or free.grounded:
        return free, None
    drop = free.position[1]-point[1]
    if drop >= 0:
        return free, None
    hit = _first_hit(point, half, (0., drop, 0.), geometry, allow_overlap_escape=True)
    if hit is None:
        return free, None
    if hit.normal == (0., 0., 0.):
        return GroundStep(point, 0., False), BodyMove(point, initial_overlap=True)
    if hit.normal[1] <= 1e-7:
        return free, None
    contact = (point[0], point[1]-max(0., -hit.distance*drop-SKIN), point[2])
    return GroundStep(contact, 0., True), None
