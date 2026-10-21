"""Windows job containment: tool descendants die when their MCP owner exits."""
import ctypes
import time
from ctypes import wintypes


class _BasicLimits(ctypes.Structure):
    _fields_ = [('ProcessTime', ctypes.c_int64), ('JobTime', ctypes.c_int64),
                ('Flags', wintypes.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                ('MaxWorkingSet', ctypes.c_size_t), ('ActiveLimit', wintypes.DWORD),
                ('Affinity', ctypes.c_size_t), ('Priority', wintypes.DWORD),
                ('Scheduling', wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in
                ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('Basic', _BasicLimits), ('Io', _IoCounters),
                ('ProcessMemory', ctypes.c_size_t), ('JobMemory', ctypes.c_size_t),
                ('PeakProcessMemory', ctypes.c_size_t), ('PeakJobMemory', ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [('UserTime', ctypes.c_int64), ('KernelTime', ctypes.c_int64),
                ('PeriodUserTime', ctypes.c_int64), ('PeriodKernelTime', ctypes.c_int64),
                ('PageFaults', wintypes.DWORD), ('Total', wintypes.DWORD),
                ('Active', wintypes.DWORD), ('Terminated', wintypes.DWORD)]


class WindowsJob:
    """Noninherited kill-on-close handle owned by toolbox, never by its children."""
    def __init__(self):
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                     ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
            ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = _ExtendedLimits()
        limits.Basic.Flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def attach_and_resume(self, proc):
        if not self.api.AssignProcessToJobObject(self.handle, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
        # subprocess closes the initial thread handle; resume the suspended process by handle.
        resume = ctypes.WinDLL('ntdll').NtResumeProcess
        resume.argtypes = [wintypes.HANDLE]
        resume.restype = ctypes.c_long
        status = resume(int(proc._handle))
        if status:
            raise OSError(f'NtResumeProcess failed: {status:#x}')

    def terminate(self):
        if not self.api.TerminateJobObject(self.handle, 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self.wait_empty()

    def detach(self):
        # Successful tools may intentionally start a persistent service (interactsh).
        # Preserve that existing lifecycle; kill-on-close applies only while the call runs.
        limits = _ExtendedLimits()
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            raise ctypes.WinError(ctypes.get_last_error())

    def wait_empty(self):
        deadline = time.monotonic() + 2
        while True:
            accounting = _Accounting()
            if not self.api.QueryInformationJobObject(self.handle, 1, ctypes.byref(accounting),
                                                      ctypes.sizeof(accounting), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if not accounting.Active:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError('工具进程树未在清理时限内退出')
            time.sleep(.01)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None
