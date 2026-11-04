"""Offline session-local contracts for optional owned Part dynamics."""
import copy
import math
import unittest
from unittest.mock import Mock

import owned_part_simulation as owned_sim
from owned_part_poses import OwnedPartPoses
from physics_packet import decode_transform_component
from physics_root_record import encode_instance_reference
from physics_rotation import encode_native_quaternion
from owned_part_simulation import UnmodeledBounds, _overlaps, _received_pose
from test_live_physics_trial import ready_world


IDENTITY = [1., 0., 0., 0., 1., 0., 0., 0., 1.]


def fixture(*, position=(0., 10., 0.), gravity=10., velocity=None,
            size=(1., 1., 1.), anchored=False, can_collide=True,
            collision_group=None):
    world, _ = ready_world()
    world.instances[(1, 6)]['properties']['Gravity'] = gravity
    world.touch((1, 6), ['Gravity'])
    key = (1, 50)
    props = {
        'Name': b'OwnedBlock', 'Anchored': anchored,
        'NetworkOwnerV3': world.client_scope, 'CanCollide': can_collide,
        'Shape': 1, 'Size': list(size),
        'CFrame': {'position': list(position), 'rotation': IDENTITY[:]},
        'ReplicationPV': {'position': list(position), 'rotation': IDENTITY[:],
                          'packed_velocities': [None, [0, 0]] if velocity is None else velocity},
    }
    if collision_group is not None:
        props['CollisionGroup'] = collision_group
    world.instances[key] = {'class': 3, 'parent': (1, 6), 'properties': props}
    world.creation[key] = {'fresh': True, 'complete': True, 'ordinary': True}
    world.touch(key, list(props))
    return world, key


def cframe_change(key, position):
    return {'kind': 'set', 'id': '%d:%d' % key, 'property': 'CFrame',
            'value': {'$cframe': {'position': list(position), 'rotation': IDENTITY[:]}}}


def local_change(key, name, value):
    return {'kind': 'set', 'id': '%d:%d' % key, 'property': name, 'value': value}


def collision_group_table(rows):
    payload = bytearray((1, len(rows)))
    for group_id, name, mask in rows:
        raw = name.encode('utf-8')
        payload.extend((group_id, 4))
        payload.extend(int(mask).to_bytes(4, 'little'))
        payload.append(len(raw))
        payload.extend(raw)
    return bytes(payload)


def add_static_part(world, key, *, position, size, group='Default'):
    world.instances[key] = {'class': 3, 'parent': (1, 6), 'properties': {
        'Name': b'StaticBlock', 'Anchored': True, 'NetworkOwnerV3': 0,
        'CanCollide': True, 'Shape': 1, 'Size': list(size),
        'CollisionGroup': group,
        'CFrame': {'position': list(position), 'rotation': IDENTITY[:]},
    }}
    world.touch(key, list(world.instances[key]['properties']))


def add_body_velocity(world, part, *, target=(10., 0., 0.), max_force=(7., 0., 0.), gain=1e9):
    class_index = next((i for i, row in enumerate(world.schema['classes'])
                        if row['name'] == 'BodyVelocity'), None)
    if class_index is None:
        class_index = len(world.schema['classes'])
        world.schema['classes'].append({'name': 'BodyVelocity', 'properties': []})
    key = (part[0], part[1]+1000)
    properties = {'Velocity': {'$vector3': list(target)},
                  'MaxForce': {'$vector3': list(max_force)}, 'P': gain}
    world.instances[key] = {'class': class_index, 'parent': part, 'properties': properties}
    world.touch(key, list(properties))
    return key


def add_unknown_static_mesh(world, key, *, position, size):
    class_index = next((i for i, row in enumerate(world.schema['classes'])
                        if row['name'] == 'MeshPart'), None)
    if class_index is None:
        class_index = len(world.schema['classes'])
        world.schema['classes'].append({'name': 'MeshPart', 'properties': []})
    properties = {'Name': b'UnknownMeshCollider', 'Anchored': True,
                  'CanCollide': True, 'Size': list(size),
                  'CFrame': {'position': list(position), 'rotation': IDENTITY[:]}}
    world.instances[key] = {'class': class_index, 'parent': (1, 6), 'properties': properties}
    world.touch(key, list(properties))


class OwnedPartSimulationTests(unittest.TestCase):
    def test_session_state_is_bound_to_one_world(self):
        world, key = fixture()
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=1.)
        queue.advance(world, 1.1)
        first_position = queue.simulation.bodies[key].position
        other_world, _ = fixture(position=(0., 100., 0.))
        with self.assertRaisesRegex(ValueError, 'cannot cross world sessions'):
            queue.advance(other_world, 1.1)
        self.assertEqual(queue.simulation.bodies[key].position, first_position)

    def test_received_gravity_drives_analytic_freefall_without_world_mutation(self):
        world, key = fixture(position=(0., 10., 0.), gravity=10.)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        before = copy.deepcopy((world.instances, world.property_versions, world.revision))
        queue.advance(world, .2)
        body = queue.simulation.bodies[key]
        self.assertAlmostEqual(body.position[1], 9.8, places=6)
        self.assertAlmostEqual(body.linear[1], -2., places=6)
        self.assertAlmostEqual(body.simulated_seconds, .2, places=9)
        self.assertEqual((world.instances, world.property_versions, world.revision), before)

    def test_cframe_teleport_preserves_integrated_velocity_in_submitted_record(self):
        world, key = fixture(position=(0., 10., 0.), gravity=10.)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.advance(world, .1)
        velocity_before = queue.simulation.bodies[key].linear
        self.assertAlmostEqual(velocity_before[1], -1., places=6)
        queue.observe(world, [cframe_change(key, (5., 20., 3.))],
                      world.revision, now=.1)
        self.assertEqual(queue.simulation.bodies[key].position, (5., 20., 3.))
        self.assertEqual(queue.simulation.bodies[key].linear, velocity_before)
        records, keys = queue.prepare(world)
        self.assertEqual(keys, [key])
        prefix = encode_instance_reference(world.client_scope, key)+b'\x00\x01\x20\x00'
        component = decode_transform_component(records[0][len(prefix):-1], velocity_mode='compact')
        for actual, expected in zip(component.linear_velocity.vector, velocity_before):
            self.assertAlmostEqual(actual, expected, delta=.01)
        for actual, expected in zip(component.position, (5., 20., 3.)):
            self.assertAlmostEqual(actual, expected, delta=.1)

    def test_received_pose_owner_and_anchor_changes_invalidate_pending_state(self):
        for field, value in (('CFrame', {'position': [0., 9., 0.], 'rotation': IDENTITY[:]}),
                             ('NetworkOwnerV3', 2), ('Anchored', True)):
            with self.subTest(field=field):
                world, key = fixture()
                queue = OwnedPartPoses(simulate=True)
                queue.observe(world, [cframe_change(key, (5., 9., 0.))],
                              world.revision, now=0.)
                self.assertIn(key, queue.pending)
                world.instances[key]['properties'][field] = value
                world.touch(key, [field])
                queue.advance(world, .1)
                records, keys = queue.prepare(world)
                self.assertEqual((records, keys), ([], []))
                self.assertNotIn(key, queue.simulation.bodies)

    def test_local_velocity_write_is_used_and_feedback_is_read_only(self):
        world, key = fixture(position=(0., 10., 0.), gravity=10.)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        before = copy.deepcopy((world.instances, world.property_versions, world.revision))
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                            {'$vector3': [3., 4., 5.]})], world.revision, now=0.)
        feedback = queue.engine_updates(world)
        self.assertEqual(feedback['frames'][0]['linear_velocity'], [3., 4., 5.])
        queue.advance(world, .1)
        body = queue.simulation.bodies[key]
        self.assertAlmostEqual(body.position[0], .3, places=6)
        self.assertAlmostEqual(body.position[1], 10.35, places=6)
        self.assertAlmostEqual(body.position[2], .5, places=6)
        self.assertAlmostEqual(body.linear[1], 3., places=6)
        self.assertEqual((world.instances, world.property_versions, world.revision), before)

    def test_local_anchored_write_removes_body_and_cancels_pending_pose(self):
        world, key = fixture()
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [cframe_change(key, (2., 10., 0.))], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'Anchored', True)], world.revision, now=.1)
        queue.advance(world, .2)
        self.assertNotIn(key, queue.simulation.bodies)
        # The local Anchored overlay must also suppress an authored pose still
        # queued by OwnedPartPoses; received World state remains Anchored=false.
        self.assertEqual(queue.prepare(world), ([], []))
        self.assertFalse(world.instances[key]['properties']['Anchored'])

    def test_static_collision_filter_respects_received_collision_group_masks(self):
        rows = [(0, 'Default', 1), (1, 'NoCollide', 2)]
        for static_group, expected_x in (('Default', 1.), ('NoCollide', 2.)):
            with self.subTest(static_group=static_group):
                world, key = fixture(position=(0., 10., 0.), gravity=0., velocity=[None, [0, 0]])
                workspace = world.instances[(1, 6)]['properties']
                workspace['CollisionGroupData'] = collision_group_table(rows)
                add_static_part(world, (1, 80), position=(2., 10., 0.),
                                size=(1., 10., 10.), group=static_group)
                world.touch((1, 6), ['CollisionGroupData'])
                queue = OwnedPartPoses(simulate=True)
                queue.observe(world, [], world.revision, now=0.)
                queue.simulation.bodies[key].linear = (10., 0., 0.)
                queue.advance(world, .2)
                self.assertAlmostEqual(queue.simulation.bodies[key].position[0], expected_x,
                                       delta=1e-4)

    def test_static_collider_local_group_overlay_controls_collision_filter(self):
        rows = [(0, 'Default', 1), (1, 'NoCollide', 2)]
        world, key = fixture(position=(0., 10., 0.), gravity=0., velocity=[None, [0, 0]])
        world.instances[(1, 6)]['properties']['CollisionGroupData'] = collision_group_table(rows)
        add_static_part(world, (1, 80), position=(2., 10., 0.),
                        size=(1., 10., 10.), group='Default')
        world.touch((1, 6), ['CollisionGroupData'])
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        queue.observe(world, [local_change((1, 80), 'CollisionGroup', 'NoCollide')],
                      world.revision, now=.01)
        queue.advance(world, .2)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 2., delta=1e-4)
        self.assertEqual(world.instances[(1, 80)]['properties']['CollisionGroup'], 'Default')

    def test_body_velocity_uses_validated_fields_and_constructor_scaled_mass(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0., size=(1., 1., 1.))
        add_body_velocity(world, key, target=(10., 0., 0.), max_force=(7., 0., 0.), gain=1e9)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        self.assertIn(key, queue.simulation.bodies)
        queue.advance(world, .1)
        body = queue.simulation.bodies[key]
        # Native Part default mass 6.72 scaled by received 1x1x1 size / 4x1.2x2
        # is 0.7. A 7-unit force therefore adds 1 stud/s in 0.1 seconds.
        self.assertAlmostEqual(body.linear[0], 1., places=4)
        self.assertAlmostEqual(body.position[0], .05, places=4)
        self.assertEqual(body.velocity_source, 'local_owned_part_body_velocity_model')

    def test_body_velocity_refuses_mass_when_creation_material_state_is_unknown(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0., size=(1., 1., 1.))
        world.instances[key]['properties']['Material'] = 256
        world.touch(key, ['Material'])
        add_body_velocity(world, key)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        self.assertNotIn(key, queue.simulation.bodies)
        self.assertGreater(queue.simulation.skipped['initial_state_unavailable'], 0)

    def test_unknown_static_collider_in_motion_bounds_blocks_simulation(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_unknown_static_mesh(world, (1, 90), position=(2., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        queue.advance(world, .2)
        self.assertEqual(queue.simulation.bodies[key].position, (0., 10., 0.))
        self.assertGreater(queue.simulation.skipped['unmodeled_static_geometry_near_motion'], 0)

    def test_huge_finite_far_unmodeled_collider_does_not_block_body(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 90), position=(0., -3.4028234663852886e38, 0.),
                        size=(1., 1., 1.))
        world.instances[(1, 90)]['properties']['CFrame']['rotation'] = [0.] * 9
        world.touch((1, 90), ['CFrame'])
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        self.assertIsInstance(queue.simulation.blockers[0][1], UnmodeledBounds)
        queue.advance(world, .2)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 2., delta=1e-4)
        self.assertNotIn('unmodeled_static_geometry_near_motion', queue.simulation.skipped)

    def test_nonfinite_unmodeled_collider_still_blocks_body(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 90), position=(0., 10., 0.), size=(1., 1., 1.))
        world.instances[(1, 90)]['properties']['CFrame']['rotation'] = [0.] * 9
        world.instances[(1, 90)]['properties']['CFrame']['position'][0] = float('inf')
        world.touch((1, 90), ['CFrame'])
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        queue.advance(world, .2)
        self.assertEqual(queue.simulation.bodies[key].position, (0., 10., 0.))
        self.assertGreater(queue.simulation.skipped['unmodeled_static_geometry_near_motion'], 0)

    def test_local_static_can_collide_and_cframe_overlays_change_collision_world(self):
        for field, value in (('CanCollide', False),
                             ('CFrame', {'$cframe': {'position': [20., 10., 0.],
                                                     'rotation': IDENTITY[:]}})):
            with self.subTest(field=field):
                world, key = fixture(position=(0., 10., 0.), gravity=0.)
                add_static_part(world, (1, 80), position=(2., 10., 0.), size=(1., 10., 10.))
                queue = OwnedPartPoses(simulate=True)
                queue.observe(world, [], world.revision, now=0.)
                queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                               {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
                overlay_value = value
                queue.observe(world, [local_change((1, 80), field, overlay_value)],
                               world.revision, now=.01)
                queue.advance(world, .2)
                self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 2.,
                                       delta=1e-4)
                self.assertIs(world.instances[(1, 80)]['properties']['CanCollide'], True)

    def test_local_static_transform_updates_geometry_without_full_rebuild(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(20., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        queue.observe(world, [local_change((1, 80), 'CFrame', {'$cframe': {
            'position': [2., 10., 0.], 'rotation': IDENTITY[:]}})], world.revision, now=.01)
        queue.advance(world, .2)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 1., delta=1e-4)
        self.assertEqual(queue.simulation._rebuild.call_count, 0)
        self.assertEqual(world.instances[(1, 80)]['properties']['CFrame']['position'], [20., 10., 0.])

    def test_spatial_bounds_follow_size_and_rotation_updates_and_match_bruteforce(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(20., 10., 0.), size=(4., 1., 2.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)

        def assert_index_matches(lower, upper):
            simulation = queue.simulation
            root = (1, 6)
            indexed = [(source, collider) for source, collider, minimum, maximum
                       in simulation.collider_index.query(root, lower, upper)
                       if _overlaps(lower, upper, minimum, maximum)]
            brute = [(collider.source, collider) for collider in simulation.geometry.colliders
                     if all(collider.maximum[i] >= lower[i] and collider.minimum[i] <= upper[i]
                            for i in range(3))]
            self.assertEqual([(source, collider.source) for source, collider in indexed],
                             [(source, collider.source) for source, collider in brute])

        # Size changes rebuild the true geometry and its index together.
        world.instances[(1, 80)]['properties']['Size'] = [6., 1., 2.]
        world.touch((1, 80), ['Size'])
        queue.advance(world, .02)
        collider = next(item for item in queue.simulation.geometry.colliders if item.source == (1, 80))
        self.assertEqual(collider.maximum[0]-collider.minimum[0], 6.)
        assert_index_matches((16., 9., -2.), (24., 11., 2.))

        # A received quarter-turn pose is refreshed incrementally; the cached
        # world AABB swaps the size axes and the exact sweep still hits it.
        yaw_90 = [0., 0., 1., 0., 1., 0., -1., 0., 0.]
        world.instances[(1, 80)]['properties']['CFrame'] = {
            'position': [2., 10., 0.], 'rotation': yaw_90}
        world.touch((1, 80), ['CFrame'])
        queue.advance(world, .2)
        collider = next(item for item in queue.simulation.geometry.colliders if item.source == (1, 80))
        self.assertAlmostEqual(collider.maximum[0]-collider.minimum[0], 2., places=8)
        self.assertAlmostEqual(collider.maximum[2]-collider.minimum[2], 6., places=8)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], .5, delta=1e-4)
        assert_index_matches((0., 9., -4.), (3., 11., 4.))
        # One full rebuild was needed for Size; the later pose update was
        # handled by the per-key index update.
        self.assertEqual(queue.simulation._rebuild.call_count, 1)

    def test_spanning_bounds_stay_candidates_and_exact_recheck_preserves_overlap(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(0., 10., 0.), size=(1000., 1000., 1000.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        index = queue.simulation.collider_index
        near = index.query((1, 6), (400., 400., 400.), (401., 401., 401.))
        far = index.query((1, 6), (600., 600., 600.), (601., 601., 601.))
        self.assertTrue(any(token == (1, 80) for token, *_ in near))
        self.assertTrue(any(token == (1, 80) for token, *_ in far))
        self.assertTrue(any(token == (1, 80) and _overlaps(
            (400., 400., 400.), (401., 401., 401.), minimum, maximum)
            for token, _, minimum, maximum in near))
        self.assertFalse(any(token == (1, 80) and _overlaps(
            (600., 600., 600.), (601., 601., 601.), minimum, maximum)
            for token, _, minimum, maximum in far))

    def test_pose_only_refresh_reuses_collision_context_but_updates_collision(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(20., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        old_context, _ = queue.simulation._collision_context(world, key)
        original_context_builder = owned_sim.context_from_world
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        queue.observe(world, [local_change((1, 80), 'CFrame', {'$cframe': {
            'position': [2., 10., 0.], 'rotation': IDENTITY[:]}})], world.revision, now=.01)
        with unittest.mock.patch.object(owned_sim, 'context_from_world',
                                        wraps=original_context_builder) as builder:
            queue.advance(world, .2)
        new_context, _ = queue.simulation._collision_context(world, key)
        self.assertIs(new_context, old_context)
        self.assertEqual(builder.call_count, 0)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 1., delta=1e-4)

    def test_static_collider_membership_change_invalidates_cached_context(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(2., 10., 0.), size=(1., 10., 10.))
        world.instances[(1, 80)]['properties']['CFrame']['rotation'] = [0.] * 9
        world.touch((1, 80), ['CFrame'])
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        old_context, _ = queue.simulation._collision_context(world, key)
        original_context_builder = owned_sim.context_from_world
        world.instances[(1, 80)]['properties']['CFrame']['rotation'] = IDENTITY[:]
        world.touch((1, 80), ['CFrame'])
        with unittest.mock.patch.object(owned_sim, 'context_from_world',
                                        wraps=original_context_builder) as builder:
            queue.advance(world, .1)
        new_context, _ = queue.simulation._collision_context(world, key)
        self.assertIsNot(new_context, old_context)
        self.assertEqual(builder.call_count, 1)
        self.assertTrue(any(collider.source == (1, 80) for collider in queue.simulation.geometry.colliders))

    def test_received_collision_table_and_group_changes_invalidate_context(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(2., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original_context_builder = owned_sim.context_from_world
        context0, _ = queue.simulation._collision_context(world, key)
        rows = [(0, 'Default', 1), (1, 'NoCollide', 2)]
        world.instances[(1, 6)]['properties']['CollisionGroupData'] = collision_group_table(rows)
        world.touch((1, 6), ['CollisionGroupData'])
        with unittest.mock.patch.object(owned_sim, 'context_from_world',
                                        wraps=original_context_builder) as builder:
            queue.advance(world, .1)
            context1, _ = queue.simulation._collision_context(world, key)
            self.assertIsNot(context1, context0)
            self.assertTrue(context1.table.complete)
            world.instances[(1, 80)]['properties']['CollisionGroup'] = 'NoCollide'
            world.touch((1, 80), ['CollisionGroup'])
            queue.advance(world, .2)
            context2, _ = queue.simulation._collision_context(world, key)
        self.assertIsNot(context2, context1)
        self.assertEqual(builder.call_count, 2)

    def test_newer_received_static_pose_supersedes_local_overlay_incrementally(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(2., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [20., 0., 0.]})], world.revision, now=0.)
        queue.observe(world, [local_change((1, 80), 'CFrame', {'$cframe': {
            'position': [20., 10., 0.], 'rotation': IDENTITY[:]}})], world.revision, now=.01)
        world.instances[(1, 80)]['properties']['CFrame'] = {
            'position': [4., 10., 0.], 'rotation': IDENTITY[:]}
        world.touch((1, 80), ['CFrame'])
        queue.advance(world, .2)
        self.assertNotIn(((1, 80), 'CFrame'), queue.simulation.local)
        self.assertAlmostEqual(queue.simulation.bodies[key].position[0], 3., delta=1e-4)
        self.assertEqual(queue.simulation._rebuild.call_count, 0)

    def test_moved_unmodeled_static_blocker_is_refreshed_incrementally(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 90), position=(20., 10., 0.), size=(1., 10., 10.))
        world.instances[(1, 90)]['properties']['Shape'] = 2
        world.touch((1, 90), ['Shape'])
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        world.instances[(1, 90)]['properties']['CFrame'] = {
            'position': [2., 10., 0.], 'rotation': IDENTITY[:]}
        world.touch((1, 90), ['CFrame'])
        queue.advance(world, .2)
        self.assertEqual(queue.simulation.bodies[key].position, (0., 10., 0.))
        self.assertGreater(queue.simulation.skipped['unmodeled_static_geometry_near_motion'], 0)
        self.assertEqual(queue.simulation._rebuild.call_count, 0)

    def test_received_moved_meshpart_keeps_conservative_blocker(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_unknown_static_mesh(world, (1, 90), position=(20., 10., 0.), size=(1., 10., 10.))
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [10., 0., 0.]})], world.revision, now=0.)
        world.instances[(1, 90)]['properties']['CFrame'] = {
            'position': [2., 10., 0.], 'rotation': IDENTITY[:]}
        world.touch((1, 90), ['CFrame'])
        queue.advance(world, .2)
        self.assertEqual(queue.simulation.bodies[key].position, (0., 10., 0.))
        self.assertGreater(queue.simulation.skipped['unmodeled_static_geometry_near_motion'], 0)
        self.assertTrue(any(blocker[2] == (1, 90) for blocker in queue.simulation.blockers))
        self.assertEqual(queue.simulation._rebuild.call_count, 0)

    def test_noop_transform_and_velocity_controller_writes_do_not_rebuild_world(self):
        world, key = fixture(position=(0., 10., 0.), gravity=0.)
        add_static_part(world, (1, 80), position=(2., 10., 0.), size=(1., 10., 10.))
        controller = add_body_velocity(world, key)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        original = queue.simulation._rebuild
        queue.simulation._rebuild = Mock(wraps=original)
        world.instances[(1, 80)]['properties']['ReplicationPV'] = {
            'position': [2., 10., 0.], 'rotation': 1,
        }
        world.touch((1, 80), ['ReplicationPV'])
        queue.observe(world, [local_change((1, 80), 'CFrame', {'$cframe': {
            'position': [2., 10., 0.], 'rotation': IDENTITY[:]}})], world.revision, now=.01)
        self.assertNotIn(((1, 80), 'CFrame'), queue.simulation.local)
        self.assertEqual(queue.simulation.static_geometry_dirty, set())
        queue.observe(world, [local_change(key, 'AssemblyLinearVelocity',
                       {'$vector3': [1., 0., 0.]})], world.revision, now=.02)
        queue.observe(world, [local_change(controller, 'Velocity',
                       {'$vector3': [3., 0., 0.]})], world.revision, now=.03)
        queue.observe(world, [local_change(controller, 'P', 123.)], world.revision, now=.04)
        queue.advance(world, .1)
        self.assertEqual(queue.simulation._rebuild.call_count, 0)
        self.assertAlmostEqual(queue.simulation.bodies[key].linear[0], 1.8, places=6)

    def test_body_enrollment_and_authored_pose_queue_are_bounded(self):
        world, first = fixture()
        keys = [first]
        for index in range(51, 121):
            key = (1, index)
            template = copy.deepcopy(world.instances[first])
            template['properties']['Name'] = ('Block%d' % index).encode()
            world.instances[key] = template
            world.creation[key] = {'fresh': True, 'complete': True, 'ordinary': True}
            world.touch(key, list(template['properties']))
            keys.append(key)
        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        self.assertLessEqual(len(queue.simulation.bodies), queue.simulation.MAX_BODIES)
        changes = [cframe_change(key, (float(i), 10., 0.)) for i, key in enumerate(keys)]
        queue.observe(world, changes, world.revision, now=.1)
        self.assertLessEqual(len(queue.pending), 64)
        self.assertLessEqual(len(queue.simulation.bodies), queue.simulation.MAX_BODIES)

    def test_fresh_complete_ordinary_creation_uses_constructor_identity_pose(self):
        world, key = fixture()
        world.instances[key]['properties'].pop('CFrame')
        world.instances[key]['properties'].pop('ReplicationPV')
        world.property_versions[key].pop('CFrame', None)
        world.property_versions[key].pop('ReplicationPV', None)
        world.creation[key] = {'fresh': True, 'complete': True, 'ordinary': True}
        position, rotation = _received_pose(world, key)
        self.assertEqual(position, (0., 0., 0.))
        self.assertEqual(rotation, tuple(IDENTITY))

        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        self.assertIn(key, queue.simulation.bodies)
        self.assertEqual(queue.simulation.bodies[key].position, position)
        self.assertEqual(queue.simulation.bodies[key].rotation, rotation)

    def test_partial_creation_cannot_fall_back_to_constructor_pose(self):
        world, key = fixture()
        world.instances[key]['properties'].pop('CFrame')
        world.instances[key]['properties'].pop('ReplicationPV')
        world.property_versions[key].pop('CFrame', None)
        world.property_versions[key].pop('ReplicationPV', None)
        world.creation[key] = {'fresh': True, 'complete': False, 'ordinary': True}
        with self.assertRaisesRegex(ValueError, 'received_pose_unavailable'):
            _received_pose(world, key)

        queue = OwnedPartPoses(simulate=True)
        queue.observe(world, [], world.revision, now=0.)
        self.assertNotIn(key, queue.simulation.bodies)
        self.assertEqual(queue.simulation.skipped['initial_state_unavailable'], 1)

    def test_packed_cframe_quaternion_expands_to_measured_rotation(self):
        world, key = fixture()
        packed = encode_native_quaternion((0., math.sin(math.pi / 4), 0.,
                                           math.cos(math.pi / 4))).hex()
        world.instances[key]['properties'].pop('ReplicationPV')
        world.instances[key]['properties']['CFrame'] = {
            'position': [3., 4., 5.], 'rotation_id': 0, 'packed_rotation': packed,
        }
        world.touch(key, ['CFrame'])
        position, rotation = _received_pose(world, key)
        self.assertEqual(position, (3., 4., 5.))
        expected = (0., 0., 1., 0., 1., 0., -1., 0., 0.)
        for actual, target in zip(rotation, expected):
            self.assertAlmostEqual(actual, target, places=3)

    def test_replication_pv_compact_selector_one_is_identity(self):
        world, key = fixture()
        world.instances[key]['properties'].pop('CFrame')
        world.instances[key]['properties']['ReplicationPV'] = {
            'position': [7., 8., 9.], 'rotation': 1,
            'packed_velocities': [None, [0, 0]],
        }
        world.touch(key, ['ReplicationPV'])
        position, rotation = _received_pose(world, key)
        self.assertEqual(position, (7., 8., 9.))
        self.assertEqual(rotation, tuple(IDENTITY))


if __name__ == '__main__':
    unittest.main()
