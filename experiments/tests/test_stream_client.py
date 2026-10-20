import os
import socket
import struct
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "exposure"))
import stream_client  # noqa: E402


def frame(op: int, payload: bytes, fin: bool = True) -> bytes:
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | op])
    if n < 126:
        head += bytes([n])
    else:
        head += bytes([126]) + struct.pack(">H", n)
    return head + payload


class FramesTest(unittest.TestCase):
    def test_fragments_ping_and_close(self):
        a, b = socket.socketpair()
        big = b"[" + b'{"x":1},' * 40 + b'{"x":1}]'
        a.sendall(frame(1, b'[{"a"', fin=False) + frame(9, b"hi") + frame(0, b":1}]")
                  + frame(1, big) + frame(8, struct.pack(">H", 4401) + b"unauthenticated"))
        got = [(op, data) for op, data, _ in stream_client.messages(b)]
        self.assertEqual(got, [(1, b'[{"a":1}]'), (1, big), (8, struct.pack(">H", 4401) + b"unauthenticated")])
        pong = a.recv(16)
        self.assertEqual(pong[0], 0x8A)   # FIN + pong, answering the ping
        self.assertTrue(pong[1] & 0x80)   # masked, as every client frame must be
        mask = pong[2:6]
        self.assertEqual(bytes(x ^ mask[i % 4] for i, x in enumerate(pong[6:8])), b"hi")

    def test_fix_time(self):
        self.assertEqual(stream_client.fix_epoch("1970-01-01T00:00:10+00:00"), 10.0)
        self.assertEqual(stream_client.fix_epoch("1970-01-01T00:00:10Z"), 10.0)
        self.assertIsNone(stream_client.fix_epoch(None))


if __name__ == "__main__":
    unittest.main()
