import math
import random
import struct
import unittest

from quaternion32 import Quaternion32Error, decode_quaternion32, encode_quaternion32


def same_rotation(a, b, tolerance=0.002):
    dot=abs(sum(x*y for x,y in zip(a,b)))
    return 1.0-dot <= tolerance


class Quaternion32Tests(unittest.TestCase):
    def test_identity_and_basis_quaternions_have_expected_words(self):
        self.assertEqual(encode_quaternion32((0,0,0,1)),struct.pack('<I',3<<30))
        self.assertEqual(encode_quaternion32((1,0,0,0)),b'\0\0\0\0')
        self.assertEqual(decode_quaternion32(b'\0\0\0\0'),(1.0,0.0,0.0,0.0))
        self.assertEqual(decode_quaternion32(struct.pack('<I',3<<30)),(0.0,0.0,0.0,1.0))

    def test_random_quaternions_round_trip_orientation_and_pack_four_bytes(self):
        rng=random.Random(0x104CBEF68)
        for _ in range(1000):
            q=[rng.uniform(-1,1) for _ in range(4)]
            packed=encode_quaternion32(q)
            self.assertEqual(len(packed),4)
            decoded=decode_quaternion32(packed)
            norm=math.sqrt(sum(float(x)*x for x in q))
            expected=tuple(x/norm for x in q)
            self.assertTrue(same_rotation(expected,decoded), (q,packed.hex(),decoded))

    def test_negative_equivalent_quaternion_has_same_canonical_encoding(self):
        q=(.2,-.3,.4,-.5)
        self.assertEqual(encode_quaternion32(q),encode_quaternion32(tuple(-x for x in q)))

    def test_omitted_component_first_tie_wins(self):
        raw=encode_quaternion32((1,1,0,0))
        word=struct.unpack('<I',raw)[0]
        self.assertEqual(word>>30,0)

    def test_signed_ten_bit_fields_and_truncation(self):
        word=struct.unpack('<I',encode_quaternion32((0.1,-0.2,0.3,0.9)))[0]
        fields=((word>>20)&0x3ff,(word>>10)&0x3ff,word&0x3ff)
        signed=tuple(v-1024 if v&512 else v for v in fields)
        self.assertTrue(any(v<0 for v in signed))
        self.assertTrue(all(-511<=v<=511 for v in signed))

    def test_bad_inputs_are_rejected(self):
        for value in (None,(1,2,3),(1,2,3,float('nan')),(1,2,3,float('inf')),
                      (0,0,0,0),(True,0,0,1)):
            with self.subTest(value=value), self.assertRaises(Quaternion32Error):
                encode_quaternion32(value)
        for data,offset in ((b'\0\0\0',0),(b'1234',-1),(b'1234',True)):
            with self.subTest(data=data,offset=offset), self.assertRaises(Quaternion32Error):
                decode_quaternion32(data,offset)


if __name__ == '__main__':
    unittest.main()
