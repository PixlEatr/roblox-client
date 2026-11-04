import unittest

from physics_session import PhysicsSession, PhysicsSendAdapter


class Controller:
    def __init__(self):
        self.calls = 0
        self.intents = []

    def consume(self, intents):
        self.intents.extend(intents)

    def step(self, dt):
        self.calls += 1
        return {'position': (self.calls * dt, 0.0, 0.0)}


class Encoder:
    def __init__(self):
        self.calls = 0

    def __call__(self, output):
        self.calls += 1
        return repr(output['position']).encode('ascii')


class Stager:
    def __init__(self):
        self.payloads = []

    def stage(self, payload):
        self.payloads.append(payload)
        return len(self.payloads)

    def drain(self):
        result, self.payloads = self.payloads, []
        return result


PROTOCOL = {
    'outbound_payload_confirmed': True,
    'application_channel_confirmed': True,
    'source': 'synthetic unit-test fixture only',
}
AUTHORITY = {
    'own_player_confirmed': True,
    'character_model_confirmed': True,
    'humanoid_confirmed': True,
    'root_part_confirmed': True,
    'root_in_workspace_confirmed': True,
    'assembly_root_confirmed': True,
    'streaming_region_confirmed': True,
    'anchored': False,
    'anchored_source': 'server_replication',
    'ownership_granted': True,
    'ownership_source': 'server_replication',
}


class PhysicsSessionTests(unittest.TestCase):
    def make_ready(self, **kwargs):
        controller, encoder, transport = Controller(), Encoder(), Stager()
        protocol_evidence = kwargs.pop('protocol_evidence', PROTOCOL)
        session = PhysicsSession(controller, encoder, transport,
                                 enabled=True, protocol_evidence=protocol_evidence, **kwargs)
        return session, controller, encoder, transport

    def test_default_is_disabled_and_performs_no_processing(self):
        controller, encoder, transport = Controller(), Encoder(), Stager()
        session = PhysicsSession(controller, encoder, transport)
        self.assertEqual(session.start()['reason'], 'disabled_by_default')
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'session_not_running')
        self.assertEqual((controller.calls, encoder.calls, transport.payloads), (0, 0, []))
        self.assertEqual(session.summary()['network_send_count'], 0)

    def test_missing_protocol_evidence_fails_closed(self):
        session, controller, encoder, transport = self.make_ready(protocol_evidence=None)
        self.assertEqual(session.start()['reason'], 'outbound_protocol_not_evidenced')
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'session_not_running')
        self.assertEqual((controller.calls, encoder.calls, transport.payloads), (0, 0, []))

    def test_authority_is_rechecked_before_controller_encoder_or_stager(self):
        session, controller, encoder, transport = self.make_ready()
        self.assertTrue(session.start()['ok'])
        result = session.tick(0.1, authority_evidence={})
        self.assertEqual(result['reason'], 'authority_unconfirmed')
        self.assertEqual((controller.calls, encoder.calls, transport.payloads), (0, 0, []))

    def test_one_authorized_tick_only_stages_and_stop_drains_generation(self):
        session, controller, encoder, transport = self.make_ready()
        self.assertTrue(session.start()['ok'])
        staged = session.tick(0.1, authority_evidence=AUTHORITY)
        self.assertEqual(staged['reason'], 'locally_staged_only')
        self.assertEqual(transport.payloads, [b'(0.1, 0.0, 0.0)'])
        before_stop = session.summary()['generation']
        stopped = session.stop()
        self.assertGreater(stopped['generation'], before_stop)
        self.assertEqual(transport.payloads, [])
        self.assertFalse(session.summary()['running'])
        self.assertEqual(session.summary()['network_send_count'], 0)

    def test_session_duration_and_step_count_are_bounded(self):
        session, _, _, _ = self.make_ready(max_duration=0.2, max_steps=2)
        session.start()
        self.assertTrue(session.tick(0.1, authority_evidence=AUTHORITY)['ok'])
        self.assertTrue(session.tick(0.1, authority_evidence=AUTHORITY)['ok'])
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'session_bound_reached')
        self.assertEqual(session.summary()['step_count'], 2)

    def test_step_input_is_bounded(self):
        session, controller, encoder, transport = self.make_ready()
        session.start()
        self.assertEqual(session.tick(0.251, authority_evidence=AUTHORITY)['reason'],
                         'invalid_or_oversized_step')
        self.assertEqual((controller.calls, encoder.calls, transport.payloads), (0, 0, []))

    def test_intent_batches_are_capped_before_controller(self):
        session, controller, _, _ = self.make_ready()
        session.start()
        result = session.consume_intents(iter(range(1000000)))
        self.assertEqual(result['reason'], 'intent_batch_exceeds_limit')
        self.assertEqual(controller.intents, [])

    def make_sender_session(self, sender_fn):
        controller, encoder = Controller(), Encoder()
        evidence = {**PROTOCOL, 'application': 4, 'channel': 7}
        sender = PhysicsSendAdapter(sender_fn, application=4, channel=7,
                                    route_evidence=evidence)
        session = PhysicsSession(controller, encoder, sender=sender,
                                 enabled=True, protocol_evidence=PROTOCOL)
        return session, controller, encoder, sender

    def test_sender_requires_explicit_matching_route_evidence(self):
        with self.assertRaises(ValueError):
            PhysicsSendAdapter(lambda *_args, **_kwargs: None,
                               application=4, channel=7,
                               route_evidence={**PROTOCOL, 'application': 4,
                                               'channel': 8})

    def test_successful_sender_call_counts_handoff_not_server_acceptance(self):
        calls = []
        session, _, _, _ = self.make_sender_session(
            lambda channel, payload, *, application: calls.append(
                (application, channel, payload)))
        self.assertFalse(session.summary()['network_submission_enabled'])
        self.assertEqual(session.start()['reason'], 'ready_for_evidenced_submission')
        self.assertTrue(session.summary()['network_submission_enabled'])
        result = session.tick(0.1, authority_evidence=AUTHORITY)
        self.assertEqual(result['reason'],
                         'client_send_returned_server_acceptance_unconfirmed')
        self.assertEqual(calls, [(4, 7, b'(0.1, 0.0, 0.0)')])
        self.assertEqual(session.summary()['network_send_attempt_count'], 1)
        self.assertEqual(session.summary()['network_send_count'], 1)
        self.assertEqual(session.summary()['staged_count'], 0)

    def test_ambiguous_sender_exception_is_latched_without_retry(self):
        calls = []

        def ambiguous_send(_channel, _payload, *, application):
            calls.append(application)
            raise OSError('delivery status unknown')

        session, _, _, sender = self.make_sender_session(ambiguous_send)
        self.assertTrue(session.start()['ok'])
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'send_result_ambiguous_no_retry')
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'session_not_running')
        self.assertEqual(session.start()['reason'], 'sender_failed_no_retry')
        self.assertEqual(calls, [4])
        self.assertTrue(sender.closed)
        self.assertEqual(session.summary()['network_send_attempt_count'], 1)
        self.assertEqual(session.summary()['network_send_count'], 0)

    def test_false_sender_result_is_not_counted_and_stop_disables_adapter(self):
        session, _, _, sender = self.make_sender_session(
            lambda _channel, _payload, *, application: False)
        session.start()
        self.assertEqual(session.tick(0.1, authority_evidence=AUTHORITY)['reason'],
                         'send_result_ambiguous_no_retry')
        self.assertEqual(session.summary()['network_send_count'], 0)
        session.stop(reason='disconnect')
        self.assertTrue(sender.closed)
        self.assertFalse(session.summary()['network_submission_enabled'])


if __name__ == '__main__':
    unittest.main()
