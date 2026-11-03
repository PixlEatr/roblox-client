"""Real mass edits must affect impulse/force prediction in statement order."""
import copy
import unittest
from test_body_velocity import world,bridge
from test_authored_forces import write
from test_physics_controller import intent
from test_physics_mass_shapes import part,weld
from physics_forces import predicted_assembly_mass


def mass_world(native=False):
    if native:
        from test_direct_velocity import native_world
        data,root=native_world()
    else:data=world();root=data['nodes'][-1]
    props=part(size=(1,1,1))['properties']
    props.update(Mass=2,AssemblyMass=2,RootPriority=100)
    props['AssemblyRootPart']={'$ref':root['id']}
    root['properties'].update(props);root.setdefault('origins',{}).update(dict.fromkeys(props,'received'))
    return data,root


class AuthoredMassPredictionTests(unittest.TestCase):
    def test_size_edit_changes_impulse_and_body_proxy_without_mutating_receipt(self):
        data,root=mass_world();old=copy.deepcopy(data);b=bridge(data)
        b.apply_mutations([write(root['id'],'Size',{'$vector3':[2,2,2]})])
        self.assertEqual(b.controller.assembly_mass,8)
        self.assertEqual(b.controller.body_half_size,(1,1,1))
        self.assertEqual(b.controller.assembly_mass_source,'authored_mass_primitive_density')
        out=b.consume([intent(1,'Impulse',(16,0,0),root['id'])])
        self.assertTrue(out[0]['accepted']);self.assertEqual(b.controller._impulse_velocity,(2,0,0))
        self.assertEqual(data,old)

    def test_statement_order_uses_mass_at_each_impulse(self):
        for cursor,expected in [(0,8),(1,2)]:
            data,root=mass_world();b=bridge(data)
            out=b.consume_batch([write(root['id'],'Size',{'$vector3':[2,2,2]})],
                [{**intent(1,'Impulse',(16,0,0),root['id']),'mutation_cursor':cursor}])
            self.assertTrue(out[0]['accepted']);self.assertEqual(b.controller._impulse_velocity,(expected,0,0))

    def test_density_material_shape_and_variant(self):
        data,root=mass_world();b=bridge(data);identifier=root['id']
        b.apply_mutations([write(identifier,'CustomPhysicalProperties',{'$physical_properties':[3,0,0,1,1]})])
        self.assertEqual(b.controller.assembly_mass,3)
        b.apply_mutations([write(identifier,'CustomPhysicalProperties',None),
            write(identifier,'Material',{'$enum':{'type':'Material','value':512}})])
        self.assertAlmostEqual(b.controller.assembly_mass,.35)
        b.apply_mutations([write(identifier,'Shape',{'$enum':{'type':'PartType','value':3}})])
        self.assertAlmostEqual(b.controller.assembly_mass,.175)
        b.apply_mutations([write(identifier,'MaterialVariant','UnknownDensity')])
        self.assertIsNone(b.controller.assembly_mass)
        b.apply_mutations([write(identifier,'CustomPhysicalProperties',{'$physical_properties':[2,0,0,1,1]})])
        self.assertEqual(b.controller.assembly_mass,1)

    def test_continuous_force_reacts_to_mass_change(self):
        data,root=mass_world();b=bridge(data)
        b.apply_mutations([{'kind':'create','node':{'id':'local:force','class_name':'BodyForce',
            'parent':root['id'],'properties':{'Force':{'$vector3':[16,0,0]}}}}])
        self.assertEqual(b.controller._force_acceleration(),(8,0,0))
        b.apply_mutations([write(root['id'],'Size',{'$vector3':[2,2,2]})])
        self.assertEqual(b.controller._force_acceleration(),(2,0,0))

    def test_massless_root_selection_and_priority_edits(self):
        data,root=mass_world();other=part('other',size=(1,1,1));other['parent']=root['parent']
        other['properties'].update(Mass=3,RootPriority=0,AssemblyRootPart={'$ref':root['id']})
        other['origins'].update(Mass='received',RootPriority='received')
        data['nodes'] += [other,weld(root['id'],'other')]
        b=bridge(data)
        b.apply_mutations([write(root['id'],'Massless',True),write('other','Massless',True)])
        self.assertEqual(b.controller.assembly_mass,2)
        b.apply_mutations([write('other','RootPriority',127)])
        self.assertEqual(b.controller.assembly_mass,3)
        b.apply_mutations([write(root['id'],'Massless',False)])
        self.assertEqual(b.controller.assembly_mass,2)

    def test_incomplete_or_mesh_edits_reject_obsolete_mass(self):
        for kind,missing in [('MeshPart',None),('Part','CustomPhysicalProperties'),('Part','Shape')]:
            data,root=mass_world();root['class_name']=kind
            if missing:root['origins'].pop(missing)
            b=bridge(data);b.apply_mutations([write(root['id'],'Size',{'$vector3':[2,2,2]})])
            self.assertIsNone(b.controller.assembly_mass)
            self.assertFalse(b.consume([intent(1,'Impulse',(16,0,0),root['id'])])[0]['accepted'])

    def test_unrelated_and_newer_receipts_handle_overrides(self):
        data,root=mass_world();b=bridge(data)
        b.apply_mutations([write(root['id'],'Size',{'$vector3':[2,2,2]})])
        b.update(copy.deepcopy(data));self.assertEqual(b.controller.assembly_mass,8)
        root['property_revisions']={'Size':10};root['properties']['AssemblyMass']=19
        b.update(data);self.assertEqual(b.controller.assembly_mass,19)
        b.apply_mutations([write(root['id'],'Size',{'$vector3':[3,3,3]})],observed_revision=9)
        self.assertEqual(b.controller.assembly_mass,19)

    def test_unrelated_part_edit_does_not_replace_owned_mass(self):
        data,root=mass_world();other=part('other');other['parent']='unrelated'
        data['nodes'].append(other);b=bridge(data)
        b.apply_mutations([write('other','Size',{'$vector3':[2,2,2]})])
        self.assertEqual(b.controller.assembly_mass,2)
        self.assertEqual(b.controller.assembly_mass_source,'received_assembly_mass')

    def test_local_geometry_requires_opt_in_for_predicted_mass(self):
        data,root=mass_world();root['origins']['Size']='local'
        root['properties']['Size']={'$vector3':[3,3,3]}
        self.assertEqual(predicted_assembly_mass(data,root['id']),(2,'received_assembly_mass'))

    def test_native_luau_mass_readback_and_prediction_agree(self):
        import test_cframe_transforms as transforms
        data,root=mass_world(native=True)
        source='''local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.Size=Vector3.new(2,2,2)
near(root.AssemblyMass,8)
root:ApplyImpulse(Vector3.new(16,0,0))
root.CustomPhysicalProperties=PhysicalProperties.new(2,0,0)
near(root.AssemblyMass,16)
root:ApplyImpulse(Vector3.new(16,0,0))
'''
        for result in transforms.CFrameTransformTests.run_script(self,source,data):
            b=bridge(data);out=b.consume_batch(result['mutations'],result['movement_intents'])
            self.assertTrue(all(item['accepted'] for item in out),out)
            self.assertEqual(b.controller.assembly_mass,16)
            self.assertEqual(b.controller._impulse_velocity,(3,0,0))

    def test_persistent_runtime_routes_mass_edits_to_real_controller(self):
        import os
        from pathlib import Path
        from unittest import mock
        import persistent_runtime
        from test_authored_physics_properties import ready_world
        state,root=ready_world()
        state.instances[root]['properties'].update(Mass=2,AssemblyMass=2,Massless=False)
        state.touch(root,['Mass','AssemblyMass','Massless'])
        original=copy.deepcopy(state.instances)
        host=Path(os.environ.get('CFRAME_PERSISTENT_HOST',str(persistent_runtime.RUST_HOST)))
        with mock.patch.object(persistent_runtime,'RUST_HOST',host):
            runtime=persistent_runtime.PersistentClientScriptRuntime(lambda:state,123)
            try:
                result=runtime.execute('''
local root=game:GetService("Players").LocalPlayer.Character.HumanoidRootPart
root.Shape=Enum.PartType.Block
root.Size=Vector3.new(2,2,2)
root.CustomPhysicalProperties=PhysicalProperties.new(1,0,0)
assert(root.AssemblyMass==8)
root:ApplyImpulse(Vector3.new(16,0,0))
root.CustomPhysicalProperties=PhysicalProperties.new(2,0,0)
root:ApplyImpulse(Vector3.new(16,0,0))
return true
''')
                self.assertTrue(result['ok'],result)
                c=runtime.movement_preview.bridge.controller
                self.assertEqual(c.accepted,2)
                self.assertEqual(c.assembly_mass,16)
                self.assertEqual(c._impulse_velocity,(3,0,0))
                self.assertEqual(state.instances,original)
            finally:runtime.close()


if __name__=='__main__':unittest.main()
