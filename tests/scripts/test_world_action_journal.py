import os
import copy
import pickle
import time
import unittest

from world_action_journal import WorldActionJournal
from world_snapshot import make_world_delta
from test_world_snapshot import fixture
from persistent_runtime import PersistentClientScriptRuntime, RUST_HOST


def record(sequence):
    return {'sequence':sequence,'kind':'event','key':(1,42),
            'arguments':[None,False,b'\x00\xff',sequence]}


class CountingFile:
    def __init__(self, wrapped):
        self.wrapped=wrapped
        self.calls={'seek':0,'read':0,'write':0,'truncate':0}

    def seek(self,*args):
        self.calls['seek']+=1
        return self.wrapped.seek(*args)

    def read(self,*args):
        self.calls['read']+=1
        return self.wrapped.read(*args)

    def write(self,*args):
        self.calls['write']+=1
        return self.wrapped.write(*args)

    def truncate(self,*args):
        self.calls['truncate']+=1
        return self.wrapped.truncate(*args)

    def __getattr__(self,name):
        return getattr(self.wrapped,name)


class WorldActionJournalTests(unittest.TestCase):
    def test_unacknowledged_evictions_replay_in_receipt_order(self):
        journal=WorldActionJournal(maxlen=3)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        expected=[record(i) for i in range(1,10)]
        journal.extend(expected)
        self.assertEqual(len(journal),3)
        self.assertEqual(list(journal.iter_since(0)),expected)
        if os.name != 'nt':
            self.assertEqual(os.fstat(journal._file.fileno()).st_mode&0o777,0o600)
        journal.acknowledge(reader,4)
        self.assertEqual(list(journal.iter_since(4)),expected[4:])
        journal.acknowledge(reader,9)
        self.assertEqual(journal.retention_stats()['spill_bytes'],0)
        self.assertIsNone(journal._file)

    def test_slowest_reader_controls_retention_and_release_cleans_up(self):
        journal=WorldActionJournal(maxlen=2)
        slow,fast=journal.retain(0),journal.retain(0)
        journal.extend(record(i) for i in range(1,6))
        journal.acknowledge(fast,5)
        self.assertEqual(len(list(journal.iter_since(0))),5)
        journal.release(slow)
        self.assertIsNone(journal._file)
        with self.assertRaisesRegex(ValueError,'overrun'):
            list(journal.iter_since(0))
        journal.release(fast)

    def test_compaction_reuses_storage_without_reordering_records(self):
        size=len(pickle.dumps(record(1),protocol=5))
        journal=WorldActionJournal(maxlen=2,max_spill_bytes=size*4)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        journal.extend(record(i) for i in range(1,7))
        journal.acknowledge(reader,3)
        journal.extend(record(i) for i in range(7,10))
        self.assertEqual(list(journal.iter_since(3)),[record(i) for i in range(4,10)])
        self.assertLessEqual(os.fstat(journal._file.fileno()).st_size,size*4)

    def test_repeated_compaction_stays_within_physical_cap_and_preserves_suffix(self):
        size=len(pickle.dumps(record(1),protocol=5))
        cap=size*8
        journal=WorldActionJournal(maxlen=2,max_spill_bytes=cap,max_spill_records=1000)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        compactions=0
        expected=[]
        for sequence in range(1,121):
            before=journal._file_size
            current=record(sequence)
            expected.append(current)
            journal.append(current)
            if journal._file_size < before:
                compactions+=1
            self.assertLessEqual(journal._file_size,cap)
            self.assertLessEqual(journal.retention_stats()['spill_bytes'],cap)
            if journal._file is not None:
                self.assertLessEqual(os.fstat(journal._file.fileno()).st_size,cap)
            cursor=max(0,sequence-4)
            journal.acknowledge(reader,cursor)
            self.assertEqual(list(journal.iter_since(cursor)),expected[cursor:])
        self.assertGreater(compactions,0)
        self.assertEqual(journal.retention_stats()['lost_through'],0)

    def test_compaction_file_io_scales_with_chunks_not_record_count(self):
        record_bytes=[len(pickle.dumps(record(i),protocol=5)) for i in range(1,2204)]
        cap=sum(record_bytes[:2000])
        journal=WorldActionJournal(maxlen=2,max_spill_bytes=cap,max_spill_records=5000)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        expected=[]
        for sequence in range(1,2003):
            current=record(sequence)
            expected.append(current)
            journal.append(current)
        self.assertEqual(journal._file_size,cap)

        journal.acknowledge(reader,100)
        counting=CountingFile(journal._file)
        journal._file=counting
        current=record(2003)
        expected.append(current)
        journal.append(current)

        live_bytes=journal.retention_stats()['spill_bytes']
        chunks=(live_bytes+1024*1024-1)//(1024*1024)
        self.assertGreaterEqual(live_bytes,cap-record_bytes[0]*101)
        self.assertLessEqual(counting.calls['read'],chunks)
        self.assertLessEqual(counting.calls['write'],chunks+1)
        self.assertLessEqual(counting.calls['seek'],2*chunks+2)
        self.assertEqual(counting.calls['truncate'],1)
        self.assertLess(counting.calls['read'],1900)
        self.assertLessEqual(journal._file_size,cap)
        self.assertLessEqual(os.fstat(journal._file.fileno()).st_size,cap)
        self.assertEqual(list(journal.iter_since(100)),expected[100:])

    def test_retention_exhaustion_fails_replay_without_stopping_network_history(self):
        journal=WorldActionJournal(maxlen=2,max_spill_records=1)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        journal.extend(record(i) for i in range(1,6))
        self.assertEqual([r['sequence'] for r in journal],[4,5])
        with self.assertRaisesRegex(ValueError,'overrun'):
            list(journal.iter_since(0))
        stats=journal.retention_stats()
        self.assertEqual(stats['lost_through'],2)
        self.assertEqual(stats['last_loss'],{
            'reason':'record_limit','sequence':2,
            'spill_records_before_loss':1,
            'spill_bytes_before_loss':len(pickle.dumps(record(1),protocol=5)),
            'physical_bytes_before_loss':len(pickle.dumps(record(1),protocol=5)),
            'reader_cursor_min':0,
            'incoming_record_bytes':len(pickle.dumps(record(2),protocol=5)),
            'max_spill_bytes':100*1024*1024,'max_spill_records':1,
        })
        journal.release(reader)
        self.assertEqual(journal.retention_stats()['last_loss']['reason'],'record_limit')

    def test_byte_limit_loss_reason_and_preclear_counters_survive_cleanup(self):
        size=len(pickle.dumps(record(1),protocol=5))
        journal=WorldActionJournal(maxlen=2,max_spill_bytes=size-1)
        reader=journal.retain(0)
        journal.extend(record(i) for i in range(1,4))
        stats=journal.retention_stats()
        self.assertEqual(stats['lost_through'],1)
        self.assertEqual(stats['last_loss'],{
            'reason':'byte_limit','sequence':1,
            'spill_records_before_loss':0,'spill_bytes_before_loss':0,
            'physical_bytes_before_loss':0,'reader_cursor_min':0,
            'incoming_record_bytes':size,
            'max_spill_bytes':size-1,'max_spill_records':262144,
        })
        journal.release(reader)
        self.assertEqual(journal.retention_stats()['last_loss']['reason'],'byte_limit')

    def test_world_delta_replays_intermediate_values_after_history_rollover(self):
        world,key=fixture()
        world.actions=WorldActionJournal(maxlen=2)
        cursor=world.revision
        reader=world.actions.retain(cursor)
        self.addCleanup(world.actions.release,reader)
        for name in ['First','Second','Third','Fourth','Fifth']:
            world.instances[key]['properties']['Name']=name
            world.touch(key,['Name'])
        first=make_world_delta(world,cursor,max_actions=3,max_bytes=4096)
        self.assertEqual([a['fields']['Name'] for a in first['actions']],['First','Second','Third'])
        world.actions.acknowledge(reader,first['sequence'])
        second=make_world_delta(world,first['sequence'],max_actions=3,max_bytes=4096)
        self.assertEqual([a['fields']['Name'] for a in second['actions']],['Fourth','Fifth'])
        self.assertEqual(second['sequence'],world.revision)

    def test_unretained_history_still_detects_gaps(self):
        journal=WorldActionJournal(maxlen=2)
        journal.extend(record(i) for i in range(1,5))
        self.assertIsNone(journal._file)
        with self.assertRaisesRegex(ValueError,'overrun'):
            list(journal.iter_since(0))

    def test_world_copy_has_independent_history_and_no_runtime_lease(self):
        journal=WorldActionJournal(maxlen=2)
        reader=journal.retain(0)
        self.addCleanup(journal.release,reader)
        journal.extend(record(i) for i in range(1,5))
        cloned=copy.deepcopy(journal)
        self.assertEqual(list(cloned),list(journal))
        self.assertEqual(cloned.maxlen,2)
        self.assertIsNone(cloned._file)
        self.assertEqual(cloned._readers,{})
        cloned[-1]['arguments'][0]='changed'
        self.assertIsNone(journal[-1]['arguments'][0])


@unittest.skipUnless(RUST_HOST.is_file(),'build the persistent worker first')
class NativeWorldReplayTests(unittest.TestCase):
    def test_background_runtime_catches_up_in_multiple_bounded_batches(self):
        world,key=fixture()
        world.actions=WorldActionJournal(maxlen=8)
        runtime=PersistentClientScriptRuntime(lambda:world,123)
        self.addCleanup(runtime.close)
        self.assertTrue(runtime.execute('return true')['ok'])
        initial=runtime.revision
        for index in range(4100):
            world.instances[key]['properties']['Name']=f'receipt{index}'.encode()
            world.touch(key,['Name'])
        self.assertGreater(world.actions.retention_stats()['spill_records'],4000)

        revisions=[initial]
        deadline=time.monotonic()+8
        while runtime.revision < world.revision and time.monotonic() < deadline:
            runtime.poll_background()
            if revisions[-1] != runtime.revision:
                revisions.append(runtime.revision)
            time.sleep(.005)

        self.assertEqual(runtime.revision,world.revision)
        self.assertGreaterEqual(len(revisions),4)
        self.assertTrue(all(0 < end-start <= 2048 for start,end in zip(revisions,revisions[1:])))
        self.assertEqual(world.actions.retention_stats()['spill_records'],0)
        result=runtime.execute('return game.Players.LocalPlayer.Character:FindFirstChild("receipt4099").Name')
        self.assertEqual(result['values'],['receipt4099'])

    def test_rollover_delivers_every_property_event_to_the_real_vm(self):
        world,key=fixture()
        world.actions=WorldActionJournal(maxlen=2)
        runtime=PersistentClientScriptRuntime(lambda:world,123)
        self.addCleanup(runtime.close)
        armed=runtime.execute('''
getgenv().held=game.Players.LocalPlayer.Character.HumanoidRootPart
getgenv().calls=0
held:GetPropertyChangedSignal("Name"):Connect(function()calls+=1 end)
''')
        self.assertTrue(armed['ok'],armed)
        for i in range(9):
            world.instances[key]['properties']['Name']=('receipt'+str(i)).encode()
            world.touch(key,['Name'])
        self.assertEqual(world.actions.retention_stats()['spill_records'],7)
        runtime.synchronize()
        result=runtime.execute('task.wait();return calls,held.Name')
        if result.get('state')=='running':
            for _ in range(20):
                runtime.poll(force=True)
                if runtime.results.get(result['job_id'],{}).get('completed'):
                    result=runtime.results[result['job_id']];break
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['values'],[9,'receipt8'])
        self.assertEqual(world.actions.retention_stats()['spill_records'],0)
