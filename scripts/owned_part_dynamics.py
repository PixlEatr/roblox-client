"""Pure, bounded gravity and static-collision step for isolated owned blocks.

This is a deliberately small axis-aligned box model. It does not mutate a
World, infer velocity, or claim Roblox solver parity. Known AABB/OBB initial
overlaps may receive a bounded minimum-translation correction; ambiguous,
unsupported, or over-budget overlaps stop without changing the input state.
The caller supplies body state, gravity, and collision-group-filtered geometry.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from movement_collision import _vector, rotation_matrix
from physics_body_collision import _first_hit
from physics_ground import AABB, OBB, GroundGeometry
from physics_forces import velocity_servo_values
from physics_velocity_servo import servo_acceleration

MAX_STEP_SECONDS = 0.25
MAX_SUBSTEP_SECONDS = 1.0 / 240.0
MAX_CONTACTS_PER_SUBSTEP = 4
MAX_VELOCITY_SOURCES = 32
_VECTOR_EPSILON = 1e-10


@dataclass(frozen=True)
class DynamicsResult:
    position: tuple[float, float, float]
    linear_velocity: tuple[float, float, float]
    contacts: tuple[tuple[float, float, float], ...]
    simulated_time: float
    unsupported_reason: str | None = None
    corrections: tuple['CollisionCorrection', ...] = ()


@dataclass(frozen=True)
class CollisionCorrection:
    """One bounded initial-overlap correction along an SAT separating axis."""
    normal: tuple[float, float, float]
    penetration_depth: float
    displacement: tuple[float, float, float]
    source: object = None


def _axis_aligned_half_size(size, rotation):
    dimensions = _vector(size, 'size')
    if any(value <= 0.0 for value in dimensions):
        raise ValueError('size components must be positive')
    matrix = rotation_matrix(rotation)
    # Only world-axis-aligned rotations are modeled. Signed permutations allow
    # quarter turns while keeping the moving collision proxy an exact AABB.
    rows = [matrix[3*i:3*i+3] for i in range(3)]
    for row in rows:
        nonzero = [value for value in row if abs(value) > 1e-8]
        if len(nonzero) != 1 or abs(abs(nonzero[0])-1.0) > 1e-8:
            raise ValueError('rotation_not_axis_aligned')
    for column in range(3):
        nonzero = [rows[row][column] for row in range(3)
                   if abs(rows[row][column]) > 1e-8]
        if len(nonzero) != 1:
            raise ValueError('rotation_not_axis_aligned')
    determinant = (matrix[0]*(matrix[4]*matrix[8]-matrix[5]*matrix[7])
                   - matrix[1]*(matrix[3]*matrix[8]-matrix[5]*matrix[6])
                   + matrix[2]*(matrix[3]*matrix[7]-matrix[4]*matrix[6]))
    if abs(determinant-1.0) > 1e-8:
        raise ValueError('rotation_not_axis_aligned')
    half = tuple(value*0.5 for value in dimensions)
    return tuple(sum(abs(rows[i][j])*half[j] for j in range(3)) for i in range(3))


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _project_outward(vector, normals):
    result = list(vector)
    # Sequential projection is energy non-increasing. Repeating a small fixed
    # number handles non-orthogonal corner normals without an iterative solver.
    for _ in range(4):
        changed = False
        for normal in normals:
            into = _dot(result, normal)
            if into < -_VECTOR_EPSILON:
                for axis in range(3):
                    result[axis] -= into*normal[axis]
                changed = True
        if not changed:
            break
    return tuple(result)


def _project_acceleration(acceleration, normals):
    # Resting contacts suppress only acceleration into a contact plane; tangent
    # acceleration remains available to slide along a wall or floor.
    return _project_outward(acceleration, normals)


def _single_source_servo_acceleration(velocity, source, mass, dt, external):
    """Solve one capped proportional source in closed form per axis.

    This is the same backward-Euler root as ``solve_movers`` for a single
    velocity source. Multiple simultaneous sources retain the general solver.
    """
    target = source['target_velocity']
    limits = source['max_force']
    gain = source['gain']
    if not gain or not any(limits):
        return (0., 0., 0.)
    acceleration = []
    for axis in range(3):
        free = velocity[axis] + external[axis]*dt
        if not math.isfinite(free):
            raise ValueError('single_velocity_servo_state_unbounded')
        limit = limits[axis]
        if not limit:
            acceleration.append(0.)
            continue
        a = dt/mass
        ratio = gain*a
        delta = target[axis]-free
        if math.isfinite(ratio):
            alpha = ratio/(1.+ratio)
            effective_gain = gain/(1.+ratio)
        else:
            # Avoid inf/inf when the implicit gain ratio exceeds float range.
            alpha = 1.
            effective_gain = mass/dt
        required_force = effective_gain*delta
        if abs(required_force) <= limit:
            end = free + alpha*delta
        else:
            end = free + a*math.copysign(limit, required_force)
        if not math.isfinite(end):
            raise ValueError('single_velocity_servo_state_unbounded')
        acceleration.append((end-free)/dt)
    return tuple(acceleration)


def _velocity_sources(sources, mass):
    if not isinstance(sources, (tuple, list)) or len(sources) > MAX_VELOCITY_SOURCES:
        raise ValueError('velocity_sources must be a bounded list or tuple')
    normalized = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('invalid_velocity_source')
        try:
            normalized.append(velocity_servo_values(
                source['target_velocity'], source['max_force'], source['gain']))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('invalid_velocity_source: '+str(exc)) from exc
    if normalized and (type(mass) not in (int, float) or not math.isfinite(mass) or mass <= 0):
        raise ValueError('velocity_servo_mass_unavailable')
    return tuple(normalized)


def _acceleration(velocity, sources, mass, dt, gravity, normals):
    external = _project_acceleration((0., -gravity, 0.), normals)
    if sources:
        # servo_acceleration returns the controller's acceleration contribution
        # relative to the free velocity after external acceleration; add that
        # contribution back to gravity before enforcing unilateral contacts.
        if len(sources) == 1:
            servo = _single_source_servo_acceleration(velocity, sources[0], mass, dt, external)
        else:
            servo = servo_acceleration(velocity, sources, mass, dt, external)
        return _project_acceleration(tuple(external[i]+servo[i] for i in range(3)), normals)
    return external


def _box_mtv(position, half_size, collider):
    """Return the deterministic minimum SAT translation for an overlapping box."""
    if isinstance(collider, AABB):
        center = tuple((collider.minimum[i]+collider.maximum[i])*0.5 for i in range(3))
        extent = tuple((collider.maximum[i]-collider.minimum[i])*0.5 for i in range(3))
        obstacle_axes = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    elif isinstance(collider, OBB):
        center, extent = collider.center, collider.half_size
        matrix = rotation_matrix(collider.rotation)
        obstacle_axes = tuple(tuple(matrix[3*i+j] for i in range(3)) for j in range(3))
    else:
        return None
    body_axes = ((1., 0., 0.), (0., 1., 0.), (0., 0., 1.))
    axes = list(body_axes)+list(obstacle_axes)
    for first in body_axes:
        for second in obstacle_axes:
            cross = (first[1]*second[2]-first[2]*second[1],
                     first[2]*second[0]-first[0]*second[2],
                     first[0]*second[1]-first[1]*second[0])
            length = math.hypot(*cross)
            if length > 1e-12:
                axes.append(tuple(value/length for value in cross))
    delta = tuple(position[i]-center[i] for i in range(3))
    candidates = []
    for order, axis in enumerate(axes):
        distance = _dot(delta, axis)
        radius_body = sum(half_size[i]*abs(axis[i]) for i in range(3))
        radius_obstacle = sum(extent[j]*abs(_dot(axis, obstacle_axes[j])) for j in range(3))
        penetration = radius_body+radius_obstacle-abs(distance)
        tolerance = 1e-10*max(1., radius_body+radius_obstacle)
        if penetration <= tolerance:
            return None
        candidates.append((penetration, order, axis, distance))
    minimum = min(row[0] for row in candidates)
    tied = [row for row in candidates
            if abs(row[0]-minimum) <= 1e-10*max(1., minimum)]
    # A symmetric centerline has no evidence-based escape direction. Stop
    # instead of choosing a side and silently moving the part arbitrarily.
    selected = next((row for row in tied if abs(row[3]) > 1e-12), None)
    if selected is None:
        raise ValueError('ambiguous_separation_direction')
    penetration, _, axis, distance = selected
    sign = 1. if distance > 0. else -1.
    normal = tuple(sign*value for value in axis)
    return CollisionCorrection(normal, penetration,
                               tuple(normal[i]*penetration for i in range(3)),
                               getattr(collider, 'source', None))


def _resolve_initial_overlap(position, half_size, geometry, maximum):
    candidate = tuple(position)
    corrections = []
    total_correction = 0.0
    for _ in range(4):
        overlap = None
        unsupported_overlap = False
        try:
            for collider in geometry.colliders:
                if isinstance(collider, (AABB, OBB)):
                    mtv = _box_mtv(candidate, half_size, collider)
                    if mtv is not None and (overlap is None
                            or mtv.penetration_depth < overlap.penetration_depth-1e-10):
                        overlap = mtv
                else:
                    hit = _first_hit(candidate, half_size, (0., 0., 0.),
                                     GroundGeometry((collider,), ()))
                    if hit is not None and hit.normal == (0., 0., 0.):
                        unsupported_overlap = True
        except ValueError as exc:
            return tuple(position), (), str(exc)
        if unsupported_overlap:
            return tuple(position), (), 'overlap_geometry_unsupported'
        if overlap is None:
            return candidate, tuple(corrections), None
        distance = overlap.penetration_depth
        if distance > maximum or total_correction+distance > maximum:
            return tuple(position), (), 'overlap_correction_limit'
        candidate = tuple(candidate[i]+overlap.displacement[i] for i in range(3))
        total_correction += distance
        corrections.append(overlap)
    # Re-sweep after each of the four corrections; unresolved or trapped
    # overlaps are left untouched rather than partially depenetrated.
    for collider in geometry.colliders:
        if isinstance(collider, (AABB, OBB)):
            try:
                if _box_mtv(candidate, half_size, collider) is not None:
                    return tuple(position), (), 'overlap_correction_budget_exhausted'
            except ValueError:
                return tuple(position), (), 'ambiguous_separation_direction'
        else:
            hit = _first_hit(candidate, half_size, (0., 0., 0.),
                             GroundGeometry((collider,), ()))
            if hit is not None and hit.normal == (0., 0., 0.):
                return tuple(position), (), 'overlap_geometry_unsupported'
    return candidate, tuple(corrections), None


def step_owned_part(position, rotation, linear_velocity, angular_velocity, size,
                    dt, gravity, geometry, *, max_overlap_correction=1.0,
                    velocity_sources=(), mass=None) -> DynamicsResult:
    """Advance one isolated block against stationary geometry.

    ``rotation`` is a row-major 3x3 matrix. Angular velocity must be zero and
    the matrix must be a world-axis-aligned rotation. ``gravity`` is a finite,
    non-negative scalar directed down world Y. ``geometry`` must already have
    collision-group filtering applied by the caller.

    Optional ``velocity_sources`` contain validated target velocity, per-axis
    force caps, and gain. They require an explicit positive ``mass`` and use
    the existing stable implicit servo solve with gravity as external
    acceleration. This is not Roblox force-constraint or assembly parity.

    Contacts are perfectly inelastic in the contact normal and frictionless in
    the tangent plane. No restitution, friction, torque, dynamic-body pairs,
    joints, or Roblox-specific solver behavior is modeled. Initial overlaps
    against known boxes are resolved with at most four minimum-translation
    corrections within ``max_overlap_correction``. Ambiguous, non-box, trapped,
    or over-budget overlaps return unsupported without a partial correction.
    """
    point = _vector(position, 'position')
    velocity = _vector(linear_velocity, 'linear_velocity')
    angular = _vector(angular_velocity, 'angular_velocity')
    half = _axis_aligned_half_size(size, rotation)
    if any(value != 0.0 for value in angular):
        raise ValueError('nonzero_angular_velocity_unsupported')
    if (isinstance(dt, bool) or not isinstance(dt, (int, float))
            or not math.isfinite(dt) or dt < 0.0 or dt > MAX_STEP_SECONDS):
        raise ValueError('dt must be finite and between 0 and 0.25 seconds')
    if (isinstance(gravity, bool) or not isinstance(gravity, (int, float))
            or not math.isfinite(gravity) or gravity < 0.0):
        raise ValueError('gravity must be finite and non-negative')
    if not isinstance(geometry, GroundGeometry):
        raise ValueError('geometry must be GroundGeometry')
    sources = _velocity_sources(velocity_sources, mass)
    if (isinstance(max_overlap_correction, bool)
            or not isinstance(max_overlap_correction, (int, float))
            or not math.isfinite(max_overlap_correction) or max_overlap_correction < 0.0):
        raise ValueError('max_overlap_correction must be finite and non-negative')
    point, corrections, overlap_error = _resolve_initial_overlap(
        point, half, geometry, float(max_overlap_correction))
    if overlap_error:
        return DynamicsResult(position=_vector(position, 'position'),
                              linear_velocity=velocity, contacts=(), simulated_time=0.0,
                              unsupported_reason=overlap_error)
    for correction in corrections:
        velocity = _project_outward(velocity, (correction.normal,))
    if dt == 0.0:
        return DynamicsResult(point, velocity,
                              tuple(correction.normal for correction in corrections),
                              0.0, corrections=corrections)

    total = float(dt)
    remaining_total = total
    contacts = [correction.normal for correction in corrections]
    gravity_value = float(gravity)
    while remaining_total > 1e-12:
        span = min(MAX_SUBSTEP_SECONDS, remaining_total)
        remaining_total -= span
        left = span
        active_normals = []
        for contact_index in range(MAX_CONTACTS_PER_SUBSTEP + 1):
            effective_acceleration = _acceleration(velocity, sources, mass, left,
                                                   gravity_value, active_normals)
            end_velocity = tuple(velocity[i]+effective_acceleration[i]*left
                                 for i in range(3))
            delta = tuple((velocity[i]+end_velocity[i])*0.5*left for i in range(3))
            if math.hypot(*delta) <= 1e-14:
                point = tuple(point[i]+delta[i] for i in range(3))
                velocity = end_velocity
                break
            hit = _first_hit(point, half, delta, geometry)
            if hit is None:
                point = tuple(point[i]+delta[i] for i in range(3))
                velocity = end_velocity
                left = 0.0
                break
            normal = tuple(float(v) for v in hit.normal)
            normal_length = math.hypot(*normal)
            if normal_length <= 1e-12:
                return DynamicsResult(point, velocity, tuple(contacts), total-remaining_total-left,
                                     'initial_interpenetration')
            normal = tuple(v/normal_length for v in normal)
            fraction = max(0.0, min(1.0, float(hit.distance)))
            elapsed = left*fraction
            chord_contact = tuple(point[i]+delta[i]*fraction for i in range(3))
            contact_plane = _dot(chord_contact, normal)
            event_acceleration = (_acceleration(velocity, sources, mass, elapsed,
                                                gravity_value, active_normals)
                                  if elapsed > 0.0 else effective_acceleration)
            event_velocity = tuple(velocity[i]+event_acceleration[i]*elapsed
                                   for i in range(3))
            # The collision primitive sweeps the short substep chord. Advance
            # analytically to the corresponding time; do not add a per-contact
            # skin offset, which would make a resting body drift over time.
            point = tuple(point[i] + (velocity[i]+event_velocity[i])*0.5*elapsed
                          for i in range(3))
            plane_error = contact_plane-_dot(point, normal)
            point = tuple(point[i]+normal[i]*plane_error for i in range(3))
            velocity = event_velocity
            velocity = _project_outward(velocity, (normal,))
            is_new_contact = not any(_dot(normal, old) > 1.0-1e-8
                                     for old in active_normals)
            if is_new_contact:
                active_normals.append(normal)
                contacts.append(normal)
            left -= elapsed
            if left <= 1e-12:
                break
            if contact_index >= MAX_CONTACTS_PER_SUBSTEP:
                return DynamicsResult(point, velocity, tuple(contacts), total-remaining_total-left,
                                     'contact_budget_exhausted')
            # If the solver repeatedly reports a zero-time contact which adds
            # no new plane, this geometry is outside the bounded response.
            if elapsed <= 1e-12 and not is_new_contact:
                return DynamicsResult(point, velocity, tuple(contacts), total-remaining_total-left,
                                     'contact_progress_unresolved')

    return DynamicsResult(point, velocity, tuple(contacts), total,
                          corrections=corrections)
