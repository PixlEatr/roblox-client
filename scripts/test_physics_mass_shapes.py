"""Mass used by impulses must follow received geometry and rigid membership."""
import math
import unittest

from physics_forces import received_assembly_mass
from physics_controller import OwnCharacterPhysicsController
from physics_ground import GroundGeometry
from test_physics_controller import snapshot as controller_snapshot


def part(name='root', *, shape=1, size=(2, 4, 6), kind='Part', parent='char', massless=False):
    properties={'Size':{'$vector3':list(size)}, 'Shape':shape, 'Material':256,
                'CustomPhysicalProperties':[1., .3, .5, 1., 1.],
                'Massless':massless, 'Anchored':False, 'AssemblyRootPart':{'$ref':name}}
    return {'id':name, 'class_name':kind, 'parent':parent, 'properties':properties,
            'origins':dict.fromkeys(properties, 'received')}


def state(*parts):
    return {'initial_replication_complete':True, 'nodes':list(parts)}


def weld(a, b, **fields):
    properties={'Part0':{'$ref':a}, 'Part1':{'$ref':b}, 'Enabled':True, **fields}
    return {'id':a+'-'+b, 'class_name':'WeldConstraint', 'parent':a,
            'properties':properties, 'origins':dict.fromkeys(properties, 'received')}


class ReceivedMassShapeTests(unittest.TestCase):
    def test_supported_shapes_use_real_volume_with_uneven_round_dimensions(self):
        for shape,volume in [(0,4*math.pi/3),(1,48),(2,8*math.pi),(3,24),(4,16)]:
            with self.subTest(shape=shape):
                mass,source=received_assembly_mass(state(part(shape=shape)), 'root')
                self.assertAlmostEqual(mass,volume)
                self.assertEqual(source,'received_primitive_density')
        for kind,volume in [('WedgePart',24),('CornerWedgePart',16),('VehicleSeat',48)]:
            p=part(kind=kind);p['properties'].pop('Shape');p['origins'].pop('Shape')
            self.assertAlmostEqual(received_assembly_mass(state(p),'root')[0],volume)

    def test_native_tagged_shape_and_density_are_accepted(self):
        p=part();p['properties']['Shape']={'$enum':{'type':'PartType','name':'Wedge','value':3}}
        p['properties']['CustomPhysicalProperties']={'$physical_properties':[2,.3,.5,1,1,0]}
        self.assertEqual(received_assembly_mass(state(p),'root'),(48,'received_primitive_density'))

    def test_mesh_or_unreceived_shape_stays_explicitly_approximate(self):
        for p in [part(kind='MeshPart'),part()]:
            p['origins'].pop('Shape')
            self.assertEqual(received_assembly_mass(state(p),'root'),
                             (48,'received_geometry_density_approximate'))
        p=part(kind='MeshPart');p['properties']['Mass']=7;p['origins']['Mass']='received'
        self.assertEqual(received_assembly_mass(state(p),'root'),(7,'received_assembly_parts'))

    def test_invalid_dimensions_and_explicit_shape_do_not_fall_back_to_boxes(self):
        for shape in [True,100,1.5,{'$enum':{'type':'Material','value':256}}]:
            self.assertEqual(received_assembly_mass(state(part(shape=shape)),'root'),(None,None))
        for size in [(0,2,3),(-1,-2,3),(float('nan'),2,3)]:
            self.assertEqual(received_assembly_mass(state(part(size=size)),'root'),(None,None))

    def test_disabled_and_inactive_welds_do_not_add_mass(self):
        for key in ['Enabled','Active']:
            a,b=part(),part('other')
            joint=weld('root','other',**{key:False})
            self.assertEqual(received_assembly_mass(state(a,b,joint),'root'),
                             (48,'received_primitive_density'))

    def test_external_welded_part_contributes_and_anchoring_blocks_force_mass(self):
        a,b=part(),part('external',parent='workspace',size=(1,2,3))
        joint=weld('root','external')
        snapshot=state(a,b,joint)
        self.assertEqual(received_assembly_mass(snapshot,'root'),(54,'received_primitive_density'))
        b['properties']['Anchored']=True
        self.assertEqual(received_assembly_mass(snapshot,'root'),(None,None))
        joint['properties']['Enabled']=False
        self.assertEqual(received_assembly_mass(snapshot,'root'),(48,'received_primitive_density'))

    def test_missing_connected_part_prevents_partial_mass(self):
        self.assertEqual(received_assembly_mass(state(part(),weld('root','missing')),'root'),(None,None))
        p=part();p['properties']['AssemblyRootPart']={'$ref':'missing'}
        self.assertEqual(received_assembly_mass(state(p),'root'),(None,None))

    def test_massless_selected_root_contributes_but_accessories_do_not(self):
        a,b=part(massless=True),part('other',massless=True)
        b['properties']['AssemblyRootPart']={'$ref':'root'}
        self.assertEqual(received_assembly_mass(state(a,b,weld('root','other')),'root'),
                         (48,'received_primitive_density'))
        self.assertEqual(received_assembly_mass(state(part(massless=True)),'root'),
                         (48,'received_primitive_density'))

    def test_custom_density_overrides_material_variant(self):
        p=part();p['properties']['MaterialVariant']='HeavyWood';p['origins']['MaterialVariant']='received'
        self.assertEqual(received_assembly_mass(state(p),'root'),(48,'received_primitive_density'))
        p['properties']['CustomPhysicalProperties']=None
        self.assertEqual(received_assembly_mass(state(p),'root'),(None,None))

    def test_anchored_received_mass_is_not_used_for_acceleration(self):
        p=part();p['properties'].update(Anchored=True,AssemblyMass=48);p['origins']['AssemblyMass']='received'
        self.assertEqual(received_assembly_mass(state(p),'root'),(None,None))

    def test_shape_mass_reaches_controller_impulse_and_continuous_force_paths(self):
        snapshot=controller_snapshot()
        snapshot['initial_replication_complete']=True
        root=snapshot['nodes'][3]
        derived=part(shape=3,size=(2,2,2))
        root['properties'].update(derived['properties']);root.setdefault('origins',{}).update(derived['origins'])
        controller=OwnCharacterPhysicsController(snapshot,geometry=GroundGeometry((),()),gravity=0.)
        self.assertEqual(controller.assembly_mass,4)
        self.assertEqual(controller.assembly_mass_source,'received_primitive_density')
        controller.consume([{'sequence':1,'kind':'Impulse','instance_id':'root','vector':[8.,0.,0.]}])
        self.assertEqual(controller._impulse_velocity,(2.,0.,0.))
        controller.update_force_sources([{'source':'received_force','id':'force','kind':'BodyForce',
                                          'part_id':'root','relative_to':'World','force':[8.,0.,0.]}],
                                        mass=4,mass_source='received_primitive_density')
        self.assertEqual(controller._force_acceleration(),(2.,0.,0.))
        report=controller.step(.1)
        self.assertAlmostEqual(report['predicted_state']['linear_velocity'][0],2.2)
        self.assertGreater(report['predicted_state']['position'][0],.2)


if __name__=='__main__':
    unittest.main()
