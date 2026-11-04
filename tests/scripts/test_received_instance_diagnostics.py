import copy
import unittest
from received_instance_diagnostics import inspect_received
from test_live_physics_trial import ready_world


class ReceivedInspectionTests(unittest.TestCase):
    def test_reads_current_received_node_without_mutation(self):
        world,root=ready_world();world.instances[(1,3)]['properties']['Name']=b'Avatar'
        world.instances[root]['attributes']={b'ap':b'\x03\x01'}
        before=copy.deepcopy(world.instances)
        result=inspect_received(world,['Workspace','Avatar','HumanoidRootPart'])
        self.assertEqual(result['node']['id'],'1:4')
        self.assertEqual(result['raw_incremental_attributes'][0]['value_hex'],'0301')
        self.assertEqual(world.instances,before)

    def test_bad_missing_and_ambiguous_paths_fail(self):
        world,root=ready_world()
        for path in ([],['Workspace']*17,['Missing'],['Workspace',123],['Workspace','missing']):
            with self.assertRaises(ValueError):inspect_received(world,path)
        world.instances[(1,7)]=copy.deepcopy(world.instances[root])
        world.instances[(1,3)]['properties']['Name']=b'Avatar'
        with self.assertRaises(ValueError):inspect_received(world,['Workspace','Avatar','HumanoidRootPart'])


if __name__=='__main__':unittest.main()
