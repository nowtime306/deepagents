"""Fixed Linux/amd64 child launcher. The root supervisor never installs this filter."""
import ctypes
import errno
import hashlib
import json
import mmap
import os
import platform
import socket
import sys
from pathlib import Path

AUDIT_AMD64 = 0xC000003E
DENIED = (*range(41, 56), 288, 299, 307, 425, 426, 427, 438)
ERRNO = 0x00050000 | errno.EPERM
ALLOW = 0x7FFF0000


def policy():
    # Check the ABI first; x32 shares AUDIT_ARCH_X86_64. Legacy x32 numbers >=512 also fail closed.
    rules = [(0x20, 0, 0, 4), (0x15, 1, 0, AUDIT_AMD64), (0x06, 0, 0, ERRNO),
             (0x20, 0, 0, 0), (0x45, 0, 1, 0x40000000), (0x06, 0, 0, ERRNO),
             (0x35, 0, 1, 512), (0x06, 0, 0, ERRNO)]
    for number in DENIED:
        rules += [(0x15, 0, 1, number), (0x06, 0, 0, ERRNO)]
    return [*rules, (0x06, 0, 0, ALLOW)]


def libc():
    library = ctypes.CDLL(None, use_errno=True)
    library.prctl.argtypes = [ctypes.c_int, *([ctypes.c_ulong] * 4)]
    library.prctl.restype = ctypes.c_int
    library.syscall.restype = ctypes.c_long
    return library


def install():
    if (sys.platform != "linux" or platform.machine() != "x86_64"
            or os.getresuid() != (1000, 1000, 1000) or os.getresgid() != (1000, 1000, 1000) or os.getgroups()):
        raise RuntimeError("The fixed AMD64 unprivileged launcher is required")
    class Filter(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte), ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]
    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filters", ctypes.POINTER(Filter))]
    rules = policy()
    filters = (Filter * len(rules))(*(Filter(*row) for row in rules))
    program = Program(len(rules), filters)
    library = libc()
    pointer = ctypes.cast(ctypes.pointer(program), ctypes.c_void_p).value
    if library.prctl(38, 1, 0, 0, 0) != 0 or library.prctl(22, 2, pointer, 0, 0) != 0:
        raise RuntimeError("The inherited verifier seccomp filter could not be installed")


def inherited_probe():
    """Only harmless denied syscalls: no Execd HTTP request or command is sent."""
    library = libc()
    if library.prctl(39, 0, 0, 0, 0) != 1 or library.prctl(21, 0, 0, 0, 0) != 2:
        raise RuntimeError("The child did not inherit its protection")
    errors = {}
    for name, family in (("inet", socket.AF_INET), ("inet6", socket.AF_INET6)):
        try:
            created = socket.socket(family, socket.SOCK_STREAM)
        except OSError as error:
            errors[name] = error.errno
        else:
            created.close()
            raise RuntimeError("Unfiltered socket creation; no further network probe is permitted")
    try:
        pair = socket.socketpair()
    except OSError as error:
        errors["socketpair"] = error.errno
    else:
        for created in pair:
            created.close()
        raise RuntimeError("Socketpair was allowed")
    try:
        connection = socket.create_connection(("127.0.0.1", 44772), timeout=1)
    except OSError as error:
        errors["execd_loopback"] = error.errno
    else:
        connection.close()
        raise RuntimeError("The control-plane connection was allowed")
    # Invalid FD ensures no connection even without a filter. EPERM rather than EBADF proves syscall denial.
    address = ctypes.create_string_buffer(b"\x02\x00\xae\xe4\x7f\x00\x00\x01" + b"\x00" * 8)
    ctypes.set_errno(0)
    answer = library.syscall(42, -1, ctypes.cast(address, ctypes.c_void_p), 16)
    errors["connect"] = ctypes.get_errno() if answer == -1 else 0
    ctypes.set_errno(0)
    answer = library.syscall(0x40000000 | 41, socket.AF_INET, socket.SOCK_STREAM, 0)
    errors["x32"] = ctypes.get_errno() if answer == -1 else 0
    # A fixed compat getpid, not socketcall: no network side effect on an unfiltered kernel either.
    with mmap.mmap(-1, 4096, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC) as code:
        code.write(bytes.fromhex("b814000000cd80c3"))
        pointer = ctypes.addressof(ctypes.c_char.from_buffer(code))
        compat = ctypes.CFUNCTYPE(ctypes.c_long)(pointer)
        errors["compat"] = 1 if compat() & 0xFFFFFFFF == 0xFFFFFFFF else 0
        del compat
    for number in (425, 426, 427, 438):
        ctypes.set_errno(0)
        answer = library.syscall(number, -1, 0, 0, 0, 0, 0)
        errors["syscall_" + str(number)] = ctypes.get_errno() if answer == -1 else 0
    if any(error != errno.EPERM for error in errors.values()):
        raise RuntimeError("A required denial probe failed")
    no_sockets = True
    for descriptor in Path("/proc/self/fd").iterdir():
        try:
            no_sockets &= not os.readlink(descriptor).startswith("socket:")
        except FileNotFoundError:
            pass
    if not no_sockets or library.syscall(39) != os.getpid():
        raise RuntimeError("Descriptor or positive syscall probe failed")
    return {"schema_version": 1, "architecture": "amd64", "uid": os.getuid(), "gid": os.getgid(),
            "euid": os.geteuid(), "egid": os.getegid(), "saved_uid": os.getresuid()[2], "saved_gid": os.getresgid()[2],
            "no_supplementary_groups": not os.getgroups(),
            "no_new_privs": 1, "seccomp_mode": 2, "denied_errno": errors, "no_inherited_sockets": True,
            "native_getpid_allowed": True, "policy_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--inherited-probe"]:
            print(json.dumps(inherited_probe()))
        else:
            install()
            if sys.argv[1:] == ["--probe"]:
                os.execv(sys.executable, [sys.executable, "-I", __file__, "--inherited-probe"])
            os.execvp(sys.argv[1], sys.argv[1:])
    except Exception:  # noqa: BLE001 -- Every launcher failure must stop before candidate execution.
        raise SystemExit(125) from None  # Never execute the candidate if isolation is unavailable.
