"""The shared native test helper must observe assertions after suspension."""
import unittest
import test_joint_poses as poses


class PersistentTestCompletionTests(unittest.TestCase):
    def test_assertion_after_wait_fails_the_test(self):
        host=poses.JointPoseTests.host(self)
        with self.assertRaisesRegex(AssertionError,'completion-probe'):
            poses.JointPoseTests.execute(self,host,'task.wait(.001);assert(false,"completion-probe")')

    def test_return_value_and_mutations_after_wait_are_collected(self):
        host=poses.JointPoseTests.host(self)
        result=poses.JointPoseTests.execute(self,host,
            'task.wait(.001);local part=Instance.new("Part",workspace);part.Name="AfterWait";return 42')
        self.assertTrue(result['completed'])
        self.assertEqual(result['values'],[42])
        self.assertTrue(any(m.get('property')=='Name' and m.get('value')=='AfterWait' for m in result['mutations']))


if __name__=='__main__':unittest.main()
