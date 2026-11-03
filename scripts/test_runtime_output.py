"""Real asynchronous Luau output and modern chat packets, without a live post."""
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
import time
import unittest

from chat_console import ChatConsole
from chat_protocol import V4_FEATURE
from local_runtime import RUST_HOST
from persistent_runtime import PersistentClientScriptRuntime
from terminal_client import OutputFollower
from test_chat_console import FakeClient
from test_chat_protocol import fixture, dictionary_received


@unittest.skipUnless(RUST_HOST.is_file(), 'build the Rust host first')
class RuntimeOutputTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='runtime-output-')
        self.addCleanup(self.directory.cleanup)
        self.world,_=fixture()
        self.bridge=ChatConsole(Path(self.directory.name)/'chat.sock',42)
        self.bridge.chat.configure_server_features({V4_FEATURE: 'true'})
        self.addCleanup(self.bridge.close)
        self.client=FakeClient();self.bridge.tick(self.client,self.world)
        self.runtime=PersistentClientScriptRuntime(lambda:self.world,42)
        self.runtime.chat_bridge=self.bridge
        self.addCleanup(self.runtime.close)

    def execute_chat(self):
        return self.runtime.execute('''
local message=game:GetService("TextChatService").TextChannels.RBXGeneral:SendAsync("hello")
print("after reply",message.Status.Name)
return message.Text
''')

    def test_modern_packet_and_dictionary_response_produce_late_output_once(self):
        first=self.execute_chat()
        self.assertEqual(first['state'],'running')
        cursor=first['runtime_sequence']
        self.runtime.poll(force=True)
        decoded=type(self.world)(self.world.schema);decoded.packet(self.client.sent[-1][1])
        event=list(decoded.events)[0]
        self.assertEqual(self.world.schema['events'][event['event']]['name'],
                         'ClientToServerMessageReplicateSignalV4')
        self.assertEqual(event['arguments'][-4:],[False,False,b'',b''])
        dictionary_received(self.world,self.bridge.chat,event['arguments'][2].decode(),text='server filtered')
        self.bridge.tick(self.client,self.world);self.runtime.poll(force=True)
        stream=self.runtime.events(cursor)
        self.assertIn('after reply\tSuccess', ''.join(row.get('output','') for row in stream['events']))
        jobs=[job for row in stream['events'] for job in row.get('completed_jobs',[])]
        self.assertEqual(jobs[0]['job_id'],first['job_id'])
        self.assertEqual(jobs[0]['values'],['server filtered'])
        outcomes=[item for row in stream['events'] for item in row.get('chat_outcomes',[])]
        self.assertEqual(outcomes[0]['status'],'Success')
        self.assertEqual(self.runtime.events(stream['cursor'])['events'],[])

    def test_real_chat_timeout_reaches_output_stream_and_terminal(self):
        first=self.execute_chat();self.runtime.poll(force=True)
        for message in self.bridge.chat.pending.values():message['deadline']=0
        self.bridge.tick(self.client,self.world);self.runtime.poll(force=True)
        stream=self.runtime.events(first['runtime_sequence'])
        failed=[job for row in stream['events'] for job in row.get('completed_jobs',[]) if not job['ok']]
        self.assertEqual(len(failed),1)
        self.assertIn('response_timeout',failed[0]['error'])
        output=io.StringIO();follower=OutputFollower()
        with redirect_stdout(output):
            follower.display({**first,'sequence':first['runtime_sequence']})
            for row in stream['events']:follower.display(row)
            for row in stream['events']:follower.display(row)
        text=output.getvalue()
        self.assertIn('response_timeout',text)
        self.assertNotIn('Script completed',text)
        self.assertEqual(text.count('SendAsync failed:'),1)
        self.assertEqual(len(self.client.sent),2)

    def test_background_print_and_error_are_retained_after_root_returns(self):
        first=self.runtime.execute('task.delay(.01,function()print("late output");error("late failure")end);return true')
        self.assertTrue(first['completed'])
        time.sleep(.03);self.runtime.poll(force=True)
        stream=self.runtime.events(first['runtime_sequence'])
        self.assertIn('late output',''.join(row.get('output','') for row in stream['events']))
        self.assertTrue(any('late failure' in error for row in stream['events'] for error in row.get('task_errors',[])))

    def test_empty_heartbeat_polls_do_not_evict_actual_output(self):
        first=self.runtime.execute('print("keep this output")')
        for _ in range(150):self.runtime._record({'ok':True,'values':[],'pending_tasks':0})
        stream=self.runtime.events(0)
        self.assertFalse(stream['overrun'])
        self.assertTrue(any('keep this output' in row.get('output','') for row in stream['events']))
        with self.assertRaises(ValueError):self.runtime.events(-1)


if __name__ == '__main__':
    unittest.main()
