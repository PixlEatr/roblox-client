"""Owner-only files and non-following opens on POSIX and native Windows."""
import os
from pathlib import Path
import stat

# Windows SDK CreateFile flag; some pywin32 builds omit it from win32con.
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000


def _windows_security():
    import win32api
    import win32security as security
    import win32con
    token = security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    try:
        user = security.GetTokenInformation(token, security.TokenUser)[0]
    finally:
        token.Close()
    return security, user


def protect_private(path):
    path = Path(path)
    if path.is_symlink() or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400:
        raise ValueError('Private path must not be a link or reparse point')
    if os.name != 'nt':
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    security, user = _windows_security()
    acl = security.ACL()
    # Explicit, protected DACL; descendants inherit only the current user's ACE.
    acl.AddAccessAllowedAceEx(security.ACL_REVISION, 3 if path.is_dir() else 0,
                             0x1F01FF, user)
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
        security.DACL_SECURITY_INFORMATION | security.PROTECTED_DACL_SECURITY_INFORMATION,
        None, None, acl, None)
    # Elevated Windows processes may create files owned by Administrators.
    # Normalize the owner as well as the DACL, matching read_private's policy.
    # Set the DACL first so the current user has WRITE_OWNER on existing files.
    security.SetNamedSecurityInfo(str(path), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION, user, None, None, None)


def private_descriptor(fd):
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        return False
    if os.name != 'nt':
        return metadata.st_uid == os.getuid() and not metadata.st_mode & 0o077
    import msvcrt
    security, user = _windows_security()
    sd = security.GetSecurityInfo(msvcrt.get_osfhandle(fd), security.SE_FILE_OBJECT,
        security.OWNER_SECURITY_INFORMATION | security.DACL_SECURITY_INFORMATION)
    if sd.GetSecurityDescriptorOwner() != user:
        return False
    acl = sd.GetSecurityDescriptorDacl()
    if acl is None:
        return False
    allowed = {str(user), str(security.CreateWellKnownSid(security.WinLocalSystemSid)),
               str(security.CreateWellKnownSid(security.WinBuiltinAdministratorsSid))}
    for index in range(acl.GetAceCount()):
        ace = acl.GetAce(index)
        # Only ordinary allow/deny ACEs are expected in these private files.
        if ace[0][0] == 0 and ace[1] and str(ace[2]) not in allowed:
            return False
        if ace[0][0] not in (0, 1):
            return False
    return True


def open_nofollow(path, flags, mode=0o600):
    if os.name != 'nt':
        return os.open(path, flags | os.O_NOFOLLOW, mode)
    import msvcrt
    import win32file
    import win32con
    import pywintypes
    security, user = _windows_security()
    acl = security.ACL()
    acl.AddAccessAllowedAce(security.ACL_REVISION, 0x1F01FF, user)
    sd = security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorOwner(user, False)
    sd.SetSecurityDescriptorDacl(True, acl, False)
    sd.SetSecurityDescriptorControl(security.SE_DACL_PROTECTED, security.SE_DACL_PROTECTED)
    attributes = pywintypes.SECURITY_ATTRIBUTES()
    attributes.SECURITY_DESCRIPTOR = sd
    access = win32con.GENERIC_READ
    if flags & (os.O_WRONLY | os.O_RDWR): access |= win32con.GENERIC_WRITE
    disposition = win32con.OPEN_EXISTING
    if flags & os.O_CREAT:
        disposition = win32con.CREATE_NEW if flags & os.O_EXCL else win32con.OPEN_ALWAYS
    try:
        handle = win32file.CreateFile(str(path), access,
        win32con.FILE_SHARE_READ | win32con.FILE_SHARE_WRITE, attributes, disposition,
        FILE_FLAG_OPEN_REPARSE_POINT, None)
    except pywintypes.error as error:
        if error.winerror in (2,3): raise FileNotFoundError(str(path)) from None
        if error.winerror in (80,183): raise FileExistsError(str(path)) from None
        raise OSError(error.winerror, 'Cannot open local file') from None
    try:
        info = win32file.GetFileInformationByHandle(handle)
        if info[0] & (win32con.FILE_ATTRIBUTE_REPARSE_POINT | win32con.FILE_ATTRIBUTE_DIRECTORY):
            raise OSError('Expected a regular file without reparse points')
        raw = handle.Detach(); handle = None
        try: fd = msvcrt.open_osfhandle(raw, flags & (os.O_RDONLY | os.O_WRONLY | os.O_RDWR) | os.O_BINARY)
        except BaseException:
            win32file.CloseHandle(raw); raise
    finally:
        if handle is not None: handle.Close()
    if flags & os.O_TRUNC:
        # All private writers must reject an existing public destination before
        # truncating it. Newly created files already have the protected DACL.
        if not private_descriptor(fd):
            os.close(fd)
            raise ValueError('Private write destination has public permissions')
        os.ftruncate(fd, 0)
    return fd


def read_private(path, limit):
    fd = open_nofollow(path, os.O_RDONLY)
    try:
        if not private_descriptor(fd) or os.fstat(fd).st_size > limit:
            raise ValueError('File must be owner-only and within its size limit')
        with os.fdopen(fd, 'rb') as stream:
            fd = None
            data = stream.read(limit + 1)
        if len(data) > limit: raise ValueError('Private file exceeds size limit')
        return data
    finally:
        if fd is not None: os.close(fd)
