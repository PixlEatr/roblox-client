"""Normal launch paths enable chat and persistent source without extra flags."""
import unittest

from game_session import parse_args
from terminal_client import session_command


AUTH = ['--protocol-sync', '--luau-range', '--ticket']


class RuntimeDefaultsTests(unittest.TestCase):
    def test_normal_authenticated_session_enables_both_runtimes(self):
        args = parse_args(AUTH)
        self.assertTrue(args.persistent_scripts)
        self.assertTrue(args.chat_console)

    def test_console_and_startup_scripts_need_no_chat_flags(self):
        for options in (['--script-console'], ['--script', 'user.luau'],
                        ['--early-script', 'observer.luau']):
            with self.subTest(options=options):
                args = parse_args(AUTH + options)
                self.assertTrue(args.persistent_scripts)
                self.assertTrue(args.chat_console)

    def test_terminal_normal_launch_enables_chat_for_any_game(self):
        for place in (6252985844, 123):
            command = session_command(place, 0)
            args = parse_args(command[3:])
            self.assertTrue(args.script_console)
            self.assertTrue(args.persistent_scripts)
            self.assertTrue(args.chat_console)

    def test_old_explicit_flags_remain_compatible(self):
        args = parse_args(AUTH + ['--chat-console', '--persistent-scripts'])
        self.assertTrue(args.persistent_scripts)
        self.assertTrue(args.chat_console)

    def test_transport_probe_does_not_start_authenticated_services(self):
        args = parse_args([])
        self.assertFalse(args.ticket)
        self.assertFalse(args.chat_console)
        self.assertFalse(args.persistent_scripts)


if __name__ == '__main__':
    unittest.main()
