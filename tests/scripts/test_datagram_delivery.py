"""Local QUIC peers exercise actual encrypted delivery and recovery, no sockets."""
import datetime
import ssl
import unittest

from aioquic.buffer import Buffer
from aioquic.h3.connection import H3Connection
from aioquic.quic.configuration import QuicConfiguration
from aioquic.quic.connection import QuicConnection
from aioquic.quic.events import DatagramFrameReceived, HandshakeCompleted
from aioquic.quic.packet import pull_quic_header
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from datagram_delivery import DatagramTrackingQuicConnection


class DatagramDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key=ec.generate_private_key(ec.SECP256R1())
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
        now=datetime.datetime.now(datetime.timezone.utc)
        cls.cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(cls.key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-datetime.timedelta(days=1))
            .not_valid_after(now+datetime.timedelta(days=1)).sign(cls.key,hashes.SHA256()))

    def pair(self):
        client=DatagramTrackingQuicConnection(configuration=QuicConfiguration(
            is_client=True,verify_mode=ssl.CERT_NONE,alpn_protocols=['h3'],max_datagram_frame_size=65536))
        client.connect(('127.0.0.1',4433),now=1.)
        initial=client.datagrams_to_send(now=1.)
        head=pull_quic_header(Buffer(data=initial[0][0]),host_cid_length=8)
        config=QuicConfiguration(is_client=False,alpn_protocols=['h3'],max_datagram_frame_size=65536)
        config.certificate=self.cert;config.private_key=self.key
        server=QuicConnection(configuration=config,original_destination_connection_id=head.destination_cid)
        for data,_ in initial:server.receive_datagram(data,('127.0.0.1',12345),now=1.)
        self.now=1.;self.client_events=[];self.server_events=[]
        for _ in range(50):self.pump(client,server)
        self.assertTrue(any(isinstance(e,HandshakeCompleted) for e in self.client_events))
        self.assertTrue(any(isinstance(e,HandshakeCompleted) for e in self.server_events))
        return client,server

    def pump(self,client,server,*,drop_client=False):
        self.now+=.01
        for peer in (client,server):
            timer=peer.get_timer()
            if timer is not None and timer<=self.now:peer.handle_timer(now=self.now)
        for data,_ in client.datagrams_to_send(now=self.now):
            if not drop_client:server.receive_datagram(data,('127.0.0.1',12345),now=self.now)
        for data,_ in server.datagrams_to_send(now=self.now):
            client.receive_datagram(data,('127.0.0.1',4433),now=self.now)
        for peer,events in ((client,self.client_events),(server,self.server_events)):
            while (e:=peer.next_event()) is not None:events.append(e)

    def test_h3_datagram_is_acknowledged_only_after_peer_receives_it(self):
        client,server=self.pair();results=[];http=H3Connection(client,enable_webtransport=True)
        with client.observe_datagram_delivery(results.append):
            http.send_datagram(0,b'owned-state')
        self.assertEqual(results,[])
        self.assertEqual(client.datagram_delivery_report()['pending'],1)
        for _ in range(10):self.pump(client,server)
        self.assertEqual(results,['acknowledged'])
        self.assertIn(b'\0owned-state',[e.data for e in self.server_events if isinstance(e,DatagramFrameReceived)])
        report=client.datagram_delivery_report()
        self.assertEqual((report['registered'],report['framed'],report['acknowledged'],report['pending']),(1,1,1,0))
        self.assertFalse(report['application_acceptance_confirmed'])

    def test_dropped_datagram_reports_loss_and_is_not_retransmitted(self):
        client,server=self.pair();results=[]
        with client.observe_datagram_delivery(results.append):client.send_datagram_frame(b'lost-state')
        self.pump(client,server,drop_client=True)
        self.assertEqual(results,[])
        for index in range(10):
            client.send_ping(index)
            self.pump(client,server)
        for _ in range(10):self.pump(client,server)
        self.assertEqual(results,['lost'])
        self.assertNotIn(b'lost-state',[e.data for e in self.server_events if isinstance(e,DatagramFrameReceived)])
        self.assertEqual(client.datagram_delivery_report()['framed'],1)
        self.assertEqual(client.datagram_delivery_report()['acknowledged'],0)

    def test_scope_errors_do_not_watch_a_later_unrelated_datagram(self):
        client=DatagramTrackingQuicConnection(configuration=QuicConfiguration(is_client=True))
        results=[]
        with self.assertRaises(RuntimeError):
            with client.observe_datagram_delivery(results.append):raise RuntimeError('H3 validation')
        client.send_datagram_frame(b'unrelated')
        self.assertEqual(client.datagram_delivery_report()['registered'],0)
        with self.assertRaises(RuntimeError):
            with client.observe_datagram_delivery(results.append):pass
        self.assertIsNone(client._datagram_delivery_scope)

    def test_callback_failure_cannot_break_packet_recovery(self):
        client,server=self.pair()
        def broken(outcome):raise RuntimeError('consumer failed')
        with client.observe_datagram_delivery(broken):client.send_datagram_frame(b'state')
        for _ in range(10):self.pump(client,server)
        report=client.datagram_delivery_report()
        self.assertEqual(report['acknowledged'],1)
        self.assertEqual(report['callback_errors'],1)
        self.assertEqual(report['pending'],0)

    def test_tracking_capacity_is_bounded_without_affecting_untracked_sends(self):
        client=DatagramTrackingQuicConnection(configuration=QuicConfiguration(is_client=True))
        client.MAX_TRACKED_DATAGRAMS=2
        for _ in range(2):
            with client.observe_datagram_delivery(lambda _:None):client.send_datagram_frame(b'state')
        with self.assertRaises(BufferError):
            with client.observe_datagram_delivery(lambda _:None):client.send_datagram_frame(b'third')
        client.send_datagram_frame(b'untracked')
        self.assertEqual(len(client._datagrams_pending),3)
        self.assertEqual(client.datagram_delivery_report()['pending'],2)


if __name__=='__main__':unittest.main()
