"""Nonblocking line input for an interactive Windows terminal."""
class WindowsLineInput:
    def __init__(self):
        import msvcrt
        self.keyboard = msvcrt
        self.characters = []
        self.special = False
        self.high_surrogate = ''

    def read(self):
        # No blocked daemon thread is left holding sys.stdin during shutdown.
        for _ in range(4096):
            if not self.keyboard.kbhit(): return None
            character = self.keyboard.getwch()
            if self.special:
                self.special = False
                continue
            if character in ('\x00', '\xe0'):
                self.special = True
                continue
            if character == '\x03': raise KeyboardInterrupt
            if character == '\x1a' and not self.characters: return b''
            if character in ('\r', '\n'):
                line = ''.join(self.characters).encode('utf-8') + b'\n'
                self.characters.clear()
                self.high_surrogate = ''
                print(flush=True)
                return line
            if character == '\b':
                if self.high_surrogate:
                    self.high_surrogate = ''
                    continue
                if self.characters:
                    self.characters.pop()
                    print('\b \b', end='', flush=True)
                continue
            if 0xd800 <= ord(character) <= 0xdbff:
                self.high_surrogate = character
                continue
            if self.high_surrogate:
                character = (self.high_surrogate+character).encode('utf-16-le', 'surrogatepass').decode('utf-16-le', 'replace')
                self.high_surrogate = ''
            if character < ' ' and character != '\t': continue
            self.characters.append(character)
            if len(self.characters) > 128*1024:
                raise ValueError('Input exceeds 128 KiB')
            print(character, end='', flush=True)
        return None
