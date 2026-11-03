import copy
import unittest
from unittest.mock import Mock

from persistent_runtime import PersistentClientScriptRuntime
from test_world_snapshot import fixture


def frame(x):
    return {'id':'1:4','cframe':{'$cframe':{'position':[x,2.,3.],
             'rotation':[1.,0.,0.,0.,1.,0.,0.,0.,1.]}}}


class RuntimeEnginePoseTests(unittest.TestCase):
    def setUp(self):
        self.world,_=fixture()
        self.runtime=PersistentClientScriptRuntime(lambda:self.world,123)
        self.addCleanup(self.runtime.close)
        self.runtime.world=self.world
        self.runtime._ensure=lambda:self.world
        self.runtime._catalog_needs_refresh=lambda *a:False
        self.runtime.revision=self.world.revision
        self.runtime.script_catalog={}
        self.runtime.initial_replication_complete=False
        self.runtime.shared_entry_count=0
        self.runtime.host=Mock()
        self.runtime.host.call.return_value={'ok':True,'values':[],'engine_pose_frames_applied':1}

    def test_latest_pose_forces_an_update_even_without_received_actions(self):
        before=copy.deepcopy(self.world.instances)
        first,latest=frame(10.),frame(20.)
        self.runtime.queue_engine_poses('1:3','1:4',[first])
        self.runtime.queue_engine_poses('1:3','1:4',[latest])
        latest['cframe']['$cframe']['position'][0]=99.
        self.runtime.synchronize()
        commands=[call.args[0] for call in self.runtime.host.call.call_args_list]
        delta=next(command['delta'] for command in commands if command['op']=='update')
        self.assertEqual(delta['actions'],[])
        self.assertEqual(delta['engine_poses']['frames'][0]['cframe']['$cframe']['position'][0],20.)
        self.assertEqual(self.world.instances,before)
        self.assertEqual(self.runtime.stats['engine_pose_frames_applied'],1)
        self.assertIsNone(self.runtime.synchronize())

    def test_stale_character_batch_is_dropped_before_rpc_and_invalid_batch_is_atomic(self):
        self.runtime.queue_engine_poses('1:3','1:4',[frame(10.)])
        with self.assertRaises(ValueError):
            self.runtime.queue_engine_poses('1:3','1:4',[frame(20.),frame(30.)])
        self.assertEqual(self.runtime._engine_pose_pending['frames'][0]['cframe']['$cframe']['position'][0],10.)
        self.world.instances[(1,2)]['properties']['Character']=None
        self.assertIsNone(self.runtime._take_engine_poses(self.world))
        self.runtime.host.call.assert_not_called()

    def test_removed_or_reparented_limb_drops_the_obsolete_batch(self):
        for removed in (False,True):
            with self.subTest(removed=removed):
                self.world,_=fixture()
                self.runtime.queue_engine_poses('1:3','1:4',[frame(10.)])
                if removed:
                    del self.world.instances[(1,4)]
                else:
                    self.world.instances[(1,4)]['parent']=(1,6)
                self.assertIsNone(self.runtime._take_engine_poses(self.world))


if __name__=='__main__':unittest.main()
