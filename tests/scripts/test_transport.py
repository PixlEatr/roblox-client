import hashlib
import unittest
from datetime import datetime, timedelta, timezone

from aioquic import tls
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from probe_transport import validate_pinned_certificate
from rupp import client_header, split_datagram
from channels import reliable_header, parse_reliable_header


class RoutingTests(unittest.TestCase):
    def test_native_layout_vector(self):
        # Independent literal expected layout: kind/flags/BE16 header length,
        # token TLV(type 1,len 17), IPv4 TLV(type 2,len 6).
        value = client_header(bytes(range(16)), "10.20.30.40", 0x1234)
        self.assertEqual(value.hex(), "0100001f011101000102030405060708090a0b0c0d0e0f02060a141e281234")
        header, payload = split_datagram(value + b"payload")
        self.assertEqual(header["size"], 31)
        self.assertEqual(header["fields"][1], (2, bytes.fromhex("0a141e281234")))
        self.assertEqual(payload, b"payload")

    def test_truncation_and_raw_quic(self):
        for value in (b"\x01", bytes.fromhex("01000003"), bytes.fromhex("0100000802100000")):
            with self.assertRaises(ValueError):
                split_datagram(value)
        self.assertEqual(split_datagram(b"\xc0raw-quic"), (None, b"\xc0raw-quic"))

    def test_channel_header(self):
        self.assertEqual(reliable_header(4, 0x10203040).hex(), "06010410203040")
        self.assertEqual(parse_reliable_header(bytes.fromhex("06010410203040")), (4, 0x10203040))
        for value in (bytes.fromhex("060104"), bytes.fromhex("06010900000000"), bytes.fromhex("07010400000000")):
            with self.assertRaises(ValueError):
                parse_reliable_header(value)


class CertificateTests(unittest.TestCase):
    def test_pin_and_validity(self):
        now = datetime.now(timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test.invalid")])
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
                .serial_number(1).not_valid_before(now - timedelta(minutes=1))
                .not_valid_after(now + timedelta(minutes=1)).sign(key, hashes.SHA256()))
        pin = hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).digest()
        validate_pinned_certificate(cert, [pin], now)
        with self.assertRaises(tls.AlertBadCertificate):
            validate_pinned_certificate(cert, [bytes(32)], now)
        with self.assertRaises(tls.AlertCertificateExpired):
            validate_pinned_certificate(cert, [pin], now + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
