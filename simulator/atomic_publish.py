"""Linux-only atomic, nonreplacing publication of a prepared directory.

This helper carries no cryptography, broker, or application dependency so a
producer host can generate a plan without importing the collector stack.
Requires a trusted parent directory on a local filesystem implementing Linux
renameat2(RENAME_NOREPLACE); not a proof of network-filesystem atomicity.
"""
import ctypes
import os


def _publish_no_replace(staged, destination):
    libc=ctypes.CDLL(None,use_errno=True)
    renameat2=getattr(libc,'renameat2',None)
    if renameat2 is None:
        raise ValueError('Linux renameat2 RENAME_NOREPLACE unavailable; refuse unsafe publication')
    renameat2.argtypes=(ctypes.c_int,ctypes.c_char_p,ctypes.c_int,ctypes.c_char_p,ctypes.c_uint)
    renameat2.restype=ctypes.c_int
    result=renameat2(-100,os.fsencode(staged),-100,os.fsencode(destination),1)
    if result != 0:
        code=ctypes.get_errno()
        raise OSError(code,os.strerror(code),str(destination))
