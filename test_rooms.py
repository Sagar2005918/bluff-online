import unittest
from app import Matchmaker, LOBBY_WAIT, ROOM_LIFETIME


class RoomTests(unittest.TestCase):
    def test_code_isolation_and_countdown(self):
        m = Matchmaker()
        token, room, _ = m.join('', 0, '007')
        self.assertEqual(LOBBY_WAIT, 10)
        self.assertIs(m.join('', 2, '007')[1], room)
        self.assertIsNot(m.join('', 2, '008')[1], room)
        m.tick(9.99)
        self.assertEqual(room.phase, 'lobby')
        m.tick(10)
        self.assertEqual(room.phase, 'play')
        self.assertEqual(sum(bool(s) for s in room.seats), 2)
        with self.assertRaises(ValueError):
            m.join('', 10, '007')
        self.assertIs(m.join(token, 11)[1], room)
        room.check()

    def test_full_room(self):
        m = Matchmaker()
        room = m.join('', 0, '123')[1]
        for _ in range(3):
            m.join('', 1, '123')
        self.assertEqual(room.phase, 'lobby')
        with self.assertRaises(ValueError):
            m.join('', 1, '123')
        m.tick(10)
        self.assertEqual(room.phase, 'play')
        self.assertTrue(all(room.seats))

    def test_expiry_and_reuse(self):
        m = Matchmaker()
        token, room, _ = m.join('', 0, '007')
        self.assertEqual(ROOM_LIFETIME, 1500)
        self.assertEqual(room.expires_at, 1500)
        m.tick(1500)
        self.assertEqual(m.locate(token), (None, None))
        fresh = m.join('', 1500, '007')[1]
        self.assertIsNot(fresh, room)
        self.assertEqual(fresh.deadline, 1510)
        self.assertEqual(fresh.expires_at, 3000)

    def test_invalid_codes(self):
        m = Matchmaker()
        for code in ['1', 'abcd', '１２３', '1234', 123, None]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                m.join('', 0, code)


if __name__ == '__main__':
    unittest.main()
