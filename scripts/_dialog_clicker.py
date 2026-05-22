"""JVLink setup dialog auto-clicker.

Monitors for Windows dialogs from JVLink and clicks
"スタートキットを持っていない" automatically.
Exits when the monitored process (--monitor-pid) finishes.
"""

import argparse
import ctypes
import ctypes.wintypes
import sys
import time

user32 = ctypes.windll.user32

EnumWindowsProc = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HWND,
    ctypes.wintypes.LPARAM,
)
BM_CLICK = 0x00F5


def _get_text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _get_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _collect_dialogs():
    found = []

    @EnumWindowsProc
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd) and _get_class(hwnd) == "#32770":
            found.append((hwnd, _get_text(hwnd)))
        return True

    user32.EnumWindows(cb, 0)
    return found


def _collect_buttons(dialog_hwnd):
    found = []

    @EnumWindowsProc
    def cb(hwnd, _):
        if _get_class(hwnd) == "Button":
            t = _get_text(hwnd)
            if t:
                found.append((hwnd, t))
        return True

    user32.EnumChildWindows(dialog_hwnd, cb, 0)
    return found


def _is_pid_alive(pid):
    SYNCHRONIZE = 0x00100000
    h = ctypes.windll.kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not h:
        return False
    rc = ctypes.windll.kernel32.WaitForSingleObject(h, 0)
    ctypes.windll.kernel32.CloseHandle(h)
    return rc != 0  # 0 = WAIT_OBJECT_0 (process exited)


def monitor(monitor_pid: int, timeout: int = 7200):
    print(f"[dialog_clicker] Monitoring PID {monitor_pid} for JVLink dialogs...")
    start = time.time()
    last_click = 0.0
    click_count = 0

    while time.time() - start < timeout:
        # Exit when target process finishes
        if monitor_pid and not _is_pid_alive(monitor_pid):
            print(f"[dialog_clicker] PID {monitor_pid} exited. Stopping monitor.")
            break

        now = time.time()
        if now - last_click > 1.5:
            for hwnd, title in _collect_dialogs():
                buttons = _collect_buttons(hwnd)
                print(f"[dialog_clicker] Dialog: '{title}'  buttons: {[t for _, t in buttons]}")

                clicked = False
                # "持っていない" を優先。"持っている（有料）" とは別物なので
                # 末尾が "ない" で終わるボタンを選ぶ（「持っていない」はOK、「持っている」はNG）
                for btn_hwnd, btn_text in buttons:
                    if btn_text.rstrip().endswith("ない"):
                        print(f"[dialog_clicker]  -> Clicking '{btn_text}'")
                        user32.SendMessageW(btn_hwnd, BM_CLICK, 0, 0)
                        clicked = True
                        click_count += 1
                        last_click = now
                        break

                # Fallback: click the LAST button (usually cancel/no in JVLink)
                if not clicked and buttons:
                    btn_hwnd, btn_text = buttons[-1]
                    print(f"[dialog_clicker]  -> Fallback click '{btn_text}'")
                    user32.SendMessageW(btn_hwnd, BM_CLICK, 0, 0)
                    click_count += 1
                    last_click = now

        time.sleep(0.3)

    print(f"[dialog_clicker] Done. Total clicks: {click_count}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--monitor-pid", type=int, default=0)
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()
    monitor(args.monitor_pid, args.timeout)
