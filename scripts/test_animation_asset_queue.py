import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from animation_assets import AnimationAssetQueue, AnimationAssetError

CLIP = {'version': 1, 'kind': 'KeyframeSequence', 'length': 1, 'looped': True,
        'priority': 1000, 'source_sha256': 'a'*64,
        'keyframes': [{'time': 1, 'name': 'End', 'markers': [], 'poses': [{'test': 1}]}]}


class AnimationAssetQueueTests(unittest.TestCase):
    def drain_wait(self, queue, count):
        end=time.monotonic()+2;out=[]
        while len(out)<count and time.monotonic()<end:
            out.extend(queue.drain());time.sleep(.005)
        self.assertEqual(len(out),count)
        return out

    def test_nonblocking_pending_work_deduplicates_numeric_asset_id(self):
        release=threading.Event();started=threading.Event();calls=[]
        def fetch(ref):
            calls.append(ref);started.set();release.wait(2);return CLIP
        queue=AnimationAssetQueue(fetcher=fetch)
        self.addCleanup(queue.close);self.addCleanup(release.set)
        queue.submit([{'reference':'rbxassetid://123'}, {'reference':'https://www.roblox.com/asset/?id=123'}])
        self.assertTrue(started.wait(1))
        self.assertEqual(queue.drain(),[])
        self.assertEqual(calls,['rbxassetid://123'])
        release.set();out=self.drain_wait(queue,2)
        self.assertTrue(all('poses' not in r['clip']['keyframes'][0] for r in out))
        queue.submit([{'reference':'http://www.roblox.com/asset/?id=123'}])
        self.assertEqual(len(queue.drain()),1)
        self.assertEqual(len(calls),1)

    def test_invalid_reference_and_fetch_failure_are_explicit_errors(self):
        def fetch(_):raise RuntimeError('private diagnostic must not escape')
        queue=AnimationAssetQueue(fetcher=fetch);self.addCleanup(queue.close)
        queue.submit([{'reference':'https://untrusted.invalid/x'}, {'reference':'rbxassetid://123'}])
        out=self.drain_wait(queue,2)
        self.assertTrue(all('error' in r and 'clip' not in r for r in out))
        self.assertNotIn('private diagnostic',str(out))

    def test_pose_samples_remain_available_without_an_extra_fetch_or_large_vm_response(self):
        calls=[]
        queue=AnimationAssetQueue(fetcher=lambda ref:calls.append(ref) or CLIP)
        self.addCleanup(queue.close)
        self.assertIsNone(queue.pose_clip('rbxassetid://123'))
        queue.submit([{'reference':'rbxassetid://123'}])
        response=self.drain_wait(queue,1)[0]
        self.assertNotIn('poses',response['clip']['keyframes'][0])
        self.assertNotIn('_pose_clip',response)
        self.assertEqual(queue.pose_clip('http://www.roblox.com/asset/?id=123')['keyframes'][0]['poses'],[{'test':1}])
        self.assertEqual(len(calls),1)
        queue.close()
        self.assertIsNone(queue.pose_clip('rbxassetid://123'))

    def test_pose_budget_does_not_turn_a_valid_timeline_into_fake_joint_data(self):
        queue=AnimationAssetQueue(fetcher=lambda _:CLIP,pose_cache_limit_bytes=1)
        self.addCleanup(queue.close)
        queue.submit([{'reference':'rbxassetid://123'}])
        self.assertIn('clip',self.drain_wait(queue,1)[0])
        self.assertIsNone(queue.pose_clip('rbxassetid://123'))
        self.assertEqual(queue.pose_cache_bytes,0)

    def test_known_failure_reason_survives_background_worker(self):
        def fetch(_):raise AnimationAssetError('animation_decode:unsupported_pose_child')
        queue=AnimationAssetQueue(fetcher=fetch);self.addCleanup(queue.close)
        queue.submit([{'reference':'rbxassetid://123'}])
        self.assertEqual(self.drain_wait(queue,1)[0]['error'],
                         'animation_decode:unsupported_pose_child')

    def test_transient_asset_delivery_failure_retries_but_returns_real_clip(self):
        calls=[]
        def fetch(_):
            calls.append(1)
            if len(calls) == 1:
                raise AnimationAssetError('animation_asset_http_503')
            return CLIP
        queue=AnimationAssetQueue(fetcher=fetch);self.addCleanup(queue.close)
        queue.submit([{'reference':'rbxassetid://123'}])
        self.assertIn('clip',self.drain_wait(queue,1)[0])
        self.assertEqual(len(calls),2)

    def test_permanent_asset_delivery_failure_is_not_retried(self):
        calls=[]
        def fetch(_):
            calls.append(1)
            raise AnimationAssetError('animation_asset_http_404')
        queue=AnimationAssetQueue(fetcher=fetch);self.addCleanup(queue.close)
        queue.submit([{'reference':'rbxassetid://123'}])
        self.assertEqual(self.drain_wait(queue,1)[0]['error'],'animation_asset_http_404')
        self.assertEqual(len(calls),1)

    def test_budget_and_closed_queue_reject_further_requests(self):
        queue=AnimationAssetQueue(fetcher=lambda _:CLIP,max_assets=1)
        self.addCleanup(queue.close)
        queue.submit([{'reference':'rbxassetid://1'}])
        with self.assertRaises(ValueError):queue.submit([{'reference':'rbxassetid://2'}])
        queue.close()
        with self.assertRaises(RuntimeError):queue.submit([])

    def test_cache_overflow_is_failure_not_partial_clip(self):
        queue=AnimationAssetQueue(fetcher=lambda _:CLIP);self.addCleanup(queue.close)
        queue.cache_bytes=16*1024*1024
        queue.submit([{'reference':'rbxassetid://1'}])
        self.assertEqual(self.drain_wait(queue,1)[0]['error'],'animation_cache_size_limit')

    def test_runtime_delivers_completed_asset_without_world_delta(self):
        from persistent_runtime import PersistentClientScriptRuntime
        world=SimpleNamespace(revision=0,tags={},shared={},actions=[])
        runtime=PersistentClientScriptRuntime(lambda:world,1)
        runtime._ensure=lambda:world
        runtime.world=world
        runtime._catalog_needs_refresh=lambda *args:False
        runtime.initial_replication_complete=False
        runtime.script_catalog={}
        runtime.host=Mock()
        runtime.host.call.return_value={'ok':True,'values':[]}
        runtime.animation_assets=Mock()
        response={'reference':'rbxassetid://123','clip':CLIP}
        runtime.animation_assets.drain.return_value=[response]
        result=runtime.synchronize()
        self.assertTrue(result['ok'])
        command=runtime.host.call.call_args.args[0]
        self.assertEqual(command['delta']['actions'],[])
        self.assertEqual(command['delta']['animation_responses'],[response])

    def test_runtime_records_requests_and_closes_queue(self):
        from persistent_runtime import PersistentClientScriptRuntime
        runtime=PersistentClientScriptRuntime(lambda:None,1)
        runtime.animation_assets=Mock()
        requests=[{'reference':'rbxassetid://123'}]
        runtime._record({'ok':True,'animation_requests':requests})
        runtime.animation_assets.submit.assert_called_once_with(requests)
        runtime.close()
        runtime.animation_assets.close.assert_called_once_with()


if __name__=='__main__':unittest.main()
