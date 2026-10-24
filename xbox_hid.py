"""Read raw Xbox controller input on Windows via HID, using nothing but ctypes.

Enumerates HID device interfaces with SetupAPI, finds a controller matching the
Microsoft vendor ID and a known product ID, then streams 64-byte input reports
and decodes them into sticks, triggers, buttons and paddles.

Stdlib only. No pip installs, no drivers, no XInput wrapper.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import struct
import sys
import time

GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

VID = 0x045E
PIDS = {0x02E3, 0x0B00, 0x0B05, 0x02FF}


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wt.DWORD),
        ("Data2", wt.WORD),
        ("Data3", wt.WORD),
        ("Data4", wt.BYTE * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wt.DWORD),
        ("Reserved", ctypes.c_size_t),
    ]


class SP_DEVICE_INTERFACE_DETAIL_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("DevicePath", ctypes.c_wchar * 260)]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wt.ULONG),
        ("VendorID", wt.USHORT),
        ("ProductID", wt.USHORT),
        ("VersionNumber", wt.USHORT),
    ]


setupapi = ctypes.WinDLL("setupapi.dll")
hid = ctypes.WinDLL("hid.dll")
kernel32 = ctypes.WinDLL("kernel32.dll")

setupapi.SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wt.LPCWSTR,
    wt.HWND,
    wt.DWORD,
]
setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE

setupapi.SetupDiEnumDeviceInterfaces.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wt.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL

setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wt.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.POINTER(SP_DEVICE_INTERFACE_DETAIL_DATA),
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wt.HANDLE]
setupapi.SetupDiDestroyDeviceInfoList.restype = wt.BOOL

hid.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
hid.HidD_GetAttributes.argtypes = [wt.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
hid.HidD_GetAttributes.restype = wt.BOOL

kernel32.CreateFileW.argtypes = [
    wt.LPCWSTR,
    wt.DWORD,
    wt.DWORD,
    ctypes.c_void_p,
    wt.DWORD,
    wt.DWORD,
    wt.HANDLE,
]
kernel32.CreateFileW.restype = wt.HANDLE

kernel32.ReadFile.argtypes = [
    wt.HANDLE,
    ctypes.c_void_p,
    wt.DWORD,
    ctypes.POINTER(wt.DWORD),
    ctypes.c_void_p,
]
kernel32.ReadFile.restype = wt.BOOL

kernel32.CloseHandle.argtypes = [wt.HANDLE]
kernel32.CloseHandle.restype = wt.BOOL


def find_device_path() -> str | None:
    """Return the device interface path of the first matching HID controller."""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))

    dev_info = setupapi.SetupDiGetClassDevsW(
        ctypes.byref(guid),
        None,
        None,
        DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
    )
    if dev_info == INVALID_HANDLE_VALUE:
        return None

    if_data = SP_DEVICE_INTERFACE_DATA()
    if_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
    idx = 0

    try:
        while setupapi.SetupDiEnumDeviceInterfaces(
            dev_info, None, ctypes.byref(guid), idx, ctypes.byref(if_data)
        ):
            idx += 1
            req_size = wt.DWORD()

            setupapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info, ctypes.byref(if_data), None, 0, ctypes.byref(req_size), None
            )

            detail = SP_DEVICE_INTERFACE_DETAIL_DATA()
            detail.cbSize = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 5

            if setupapi.SetupDiGetDeviceInterfaceDetailW(
                dev_info,
                ctypes.byref(if_data),
                ctypes.byref(detail),
                req_size.value,
                None,
                None,
            ):
                path = detail.DevicePath
                h_dev = kernel32.CreateFileW(
                    path,
                    GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None,
                    OPEN_EXISTING,
                    0,
                    None,
                )
                if h_dev != INVALID_HANDLE_VALUE:
                    attr = HIDD_ATTRIBUTES()
                    attr.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
                    if hid.HidD_GetAttributes(h_dev, ctypes.byref(attr)):
                        if attr.VendorID == VID and (
                            attr.ProductID in PIDS or not PIDS
                        ):
                            kernel32.CloseHandle(h_dev)
                            return path
                    kernel32.CloseHandle(h_dev)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(dev_info)

    return None


class Controller:
    """Open HID controller handle and decode its input reports."""

    def __init__(self, path: str):
        self.path = path
        self.handle = kernel32.CreateFileW(
            path,
            GENERIC_READ | GENERIC_WRITE,
            FILE_SHARE_READ | FILE_SHARE_WRITE,
            None,
            OPEN_EXISTING,
            0,
            None,
        )
        if self.handle == INVALID_HANDLE_VALUE:
            raise OSError(f"handle creation failed with code {kernel32.GetLastError()}")

    def close(self):
        if self.handle and self.handle != INVALID_HANDLE_VALUE:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def read_raw(self, size: int = 64) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        read_bytes = wt.DWORD()
        ok = kernel32.ReadFile(
            self.handle, buf, size, ctypes.byref(read_bytes), None
        )
        if not ok or read_bytes.value == 0:
            return None
        return buf.raw[: read_bytes.value]

    def parse(self, data: bytes) -> dict | None:
        if len(data) < 16:
            return None

        offset = 1 if data[0] in (0x01, 0x02) else 0

        lx, ly, rx, ry = struct.unpack_from("<hhhh", data, offset)
        lt, rt = struct.unpack_from("<HH", data, offset + 8)

        btn_lo, btn_hi = struct.unpack_from("<BB", data, offset + 12)
        buttons = (btn_hi << 8) | btn_lo

        p_byte = data[offset + 14] if len(data) > offset + 14 else 0
        paddles = {
            "p1": bool(p_byte & 0x01),
            "p2": bool(p_byte & 0x02),
            "p3": bool(p_byte & 0x04),
            "p4": bool(p_byte & 0x08),
        }

        return {
            "ls": (round(lx / 32767.0, 3), round(ly / 32767.0, 3)),
            "rs": (round(rx / 32767.0, 3), round(ry / 32767.0, 3)),
            "lt": round(min(1.0, lt / 1023.0), 3),
            "rt": round(min(1.0, rt / 1023.0), 3),
            "buttons": buttons,
            "paddles": paddles,
        }


def run(show_raw: bool = False):
    path = find_device_path()
    if not path:
        print("No matching controller found.", file=sys.stderr)
        return

    dev = Controller(path)
    try:
        while True:
            raw = dev.read_raw()
            if raw:
                if show_raw:
                    print(" ".join(f"{b:02x}" for b in raw), flush=True)
                    time.sleep(1 / 120)
                    continue

                state = dev.parse(raw)
                if state:
                    p = [k for k, v in state["paddles"].items() if v]
                    print(
                        f"\rls: {state['ls']}  rs: {state['rs']}  "
                        f"lt: {state['lt']:.2f} rt: {state['rt']:.2f}  "
                        f"p: {p or '-'}".ljust(70),
                        end="",
                        flush=True,
                    )
            time.sleep(1 / 120)
    except KeyboardInterrupt:
        pass
    finally:
        dev.close()


if __name__ == "__main__":
    run(show_raw="--raw" in sys.argv[1:])
