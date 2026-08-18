"""Capture a WebGPU frame from Chrome's D3D12 backend using RenderDoc.

Windows only. Chrome v144+ (Canary at time of writing). See
`.claude/skills/renderdoc-gpu-debug/references/webgpu-capture.md` for the manual flow
this script automates.

RenderDoc has no native WebGPU backend, so we hook Chrome's D3D12 backend by
injecting into the GPU process *before* it initializes its device. The script:

  1. (optionally) kills a running Chrome, then launches Chrome with
     `--gpu-startup-dialog` + Dawn's renderdoc-injection features.
  2. Finds the "Google Chrome GPU" dialog and reads the GPU process PID from it.
  3. Injects RenderDoc into that PID via `rd.InjectIntoProcess`.
  4. Dismisses the dialog so the GPU process finishes initializing D3D12.
  5. Collects the first `.rdc` capture via TargetControl and copies it locally.

Once injected, RenderDoc captures EVERY WebGPU frame (no on-demand trigger); the
first frame's capture is what we save. Close the Chrome tab to stop captures.
"""
import argparse
import ctypes
import os
import re
import shutil
import subprocess
import sys
import time
from ctypes import wintypes

# --- RenderDoc import -------------------------------------------------------
RENDERDOC_MODULE = os.environ.get('RENDERDOC_MODULE', 'D:/renderdoc/module')
sys.path.insert(0, RENDERDOC_MODULE)
import renderdoc as rd  # noqa: E402


# --- Constants --------------------------------------------------------------
CANARY_DEFAULT = os.path.join(
    os.environ.get('LOCALAPPDATA', r'C:\Users\Default\AppData\Local'),
    'Google', 'Chrome SxS', 'Application', 'chrome.exe',
)
STABLE_DEFAULT = os.path.join(
    os.environ.get('LOCALAPPDATA', r'C:\Users\Default\AppData\Local'),
    'Google', 'Chrome', 'Application', 'chrome.exe',
)

# Dawn features, combined into one comma-separated flag:
#   enable_renderdoc_process_injection   -> lets RenderDoc hook the D3D12 backend
#   use_user_defined_labels_in_backend   -> keeps three.js .setName() resource names
#   emit_hlsl_debug_symbols, disable_symbol_renaming -> debuggable HLSL source
DAWN_FEATURES = (
    'enable_renderdoc_process_injection,'
    'use_user_defined_labels_in_backend,'
    'emit_hlsl_debug_symbols,'
    'disable_symbol_renaming'
)

GPU_DIALOG_TITLE = 'Google Chrome GPU'
PID_RE = re.compile(r'PID[^\d]*(\d+)', re.IGNORECASE)

WM_COMMAND = 0x0111
IDOK = 1


def _user32():
    return ctypes.windll.user32


def find_gpu_dialog(timeout=30.0):
    """Return the hwnd of the "Google Chrome GPU" dialog, or None."""
    u32 = _user32()
    found = []

    def _enum(hwnd, _lp):
        length = u32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        u32.GetWindowTextW(hwnd, buf, length + 1)
        if GPU_DIALOG_TITLE in buf.value:
            found.append(hwnd)
            return False  # stop
        return True

    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_enum)

    deadline = time.time() + timeout
    while time.time() < deadline:
        del found[:]
        u32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.2)
    return None


def dialog_body(hwnd):
    """Concatenate the text of the dialog's Static child controls (its body)."""
    u32 = _user32()
    parts = []

    def _enum_child(child, _lp):
        cls = ctypes.create_unicode_buffer(64)
        u32.GetClassNameW(child, cls, 64)
        if cls.value == 'Static':
            length = u32.GetWindowTextLengthW(child)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                u32.GetWindowTextW(child, buf, length + 1)
                parts.append(buf.value)
        return True

    cb = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(_enum_child)
    u32.EnumChildWindows(hwnd, cb, 0)
    return '\n'.join(parts)


def find_gpu_pid_from_cmdline():
    """Fallback: PID of the chrome.exe child whose cmdline has --type=gpu-process."""
    script = (
        "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
        "Where-Object { $_.CommandLine -match '--type=gpu-process' } | "
        "Select-Object -First 1 -ExpandProperty ProcessId"
    )
    out = subprocess.run(
        ['powershell', '-NoProfile', '-Command', script],
        capture_output=True, text=True,
    )
    pid = out.stdout.strip()
    return int(pid) if pid.isdigit() else None


def kill_chrome():
    subprocess.run(['taskkill', '/F', '/IM', 'chrome.exe'],
                   capture_output=True)
    time.sleep(1.0)


def launch_chrome(chrome, url):
    env = os.environ.copy()
    env['RENDERDOC_HOOK_EGL'] = '0'
    cmd = [
        chrome,
        '--no-sandbox',
        '--disable-gpu-sandbox',
        '--disable-direct-composition',
        '--gpu-startup-dialog',
        f'--enable-dawn-features={DAWN_FEATURES}',
        url,
    ]
    subprocess.Popen(cmd, env=env)


def inject_into_pid(pid, cap_path, opts):
    # renderdoc.InjectIntoProcess(pid, env, capturefile, opts, waitForExit)
    result = rd.InjectIntoProcess(pid, [], cap_path, opts, False)
    if result.ident == 0:
        raise RuntimeError('InjectIntoProcess failed (ident == 0)')
    return result.ident


def dismiss_dialog(hwnd):
    # Click OK (IDOK) on the message box so the GPU process continues.
    _user32().SendMessageW(hwnd, WM_COMMAND, IDOK, 0)


def wait_for_capture(ident, cap_path, timeout=60.0):
    target = rd.CreateTargetControl('', ident, 'capture-webgpu', True)
    if target is None or not target.Connected():
        raise RuntimeError('Failed to connect TargetControl')

    deadline = time.time() + timeout
    cap_received = False
    while time.time() < deadline:
        msg = target.ReceiveMessage(None)
        if msg.type == rd.TargetControlMessageType.NewCapture:
            cap = msg.newCapture
            if cap.local and cap.path and cap.path != cap_path:
                os.makedirs(os.path.dirname(cap_path), exist_ok=True)
                shutil.copy2(cap.path, cap_path)
            elif not cap.local:
                target.CopyCapture(cap.captureId, cap_path)
            cap_received = True
            break
        time.sleep(0.1)

    target.Shutdown()
    return cap_received


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--chrome', default=None,
                   help='path to chrome.exe (defaults to Canary, then stable)')
    p.add_argument('--url', default='http://localhost:5189',
                   help='URL to open (default: 12_ddgi Vite dev server)')
    p.add_argument('-o', '--output', default='D:/renderdoc/captures/webgpu.rdc',
                   help='output .rdc path')
    p.add_argument('--kill-existing', action='store_true',
                   help='kill a running Chrome first (required if Chrome is open)')
    p.add_argument('--no-dismiss', action='store_true',
                   help='do NOT auto-click OK; dismiss the GPU dialog by hand')
    args = p.parse_args(argv)

    chrome = args.chrome
    if not chrome:
        for candidate in (CANARY_DEFAULT, STABLE_DEFAULT):
            if os.path.exists(candidate):
                chrome = candidate
                break
    if not chrome or not os.path.exists(chrome):
        print('chrome.exe not found. Pass --chrome <path>. (v144+ is in Canary.)')
        return 1
    print(f'Chrome: {chrome}')

    if args.kill_existing:
        print('Killing existing Chrome...')
        kill_chrome()

    print('Launching Chrome with Dawn renderdoc-injection features...')
    launch_chrome(chrome, args.url)

    print('Waiting for "Google Chrome GPU" dialog...')
    hwnd = find_gpu_dialog()
    if hwnd is None:
        print('No GPU dialog appeared. Is Chrome v144+ with --gpu-startup-dialog?')
        return 1

    body = dialog_body(hwnd)
    m = PID_RE.search(body)
    pid = int(m.group(1)) if m else find_gpu_pid_from_cmdline()
    if pid is None:
        print('Could not determine GPU process PID from the dialog.')
        print(f'Dialog body was: {body!r}')
        return 1
    print(f'GPU process PID: {pid}')

    opts = rd.CaptureOptions()
    opts.apiValidation = False
    opts.captureCallstacks = False
    # Reference all resources so buffer reads (e.g. ddgi_rayData) are inspectable.
    opts.refAllResources = True

    print('Injecting RenderDoc...')
    ident = inject_into_pid(pid, args.output, opts)

    if not args.no_dismiss:
        print('Dismissing the GPU dialog...')
        dismiss_dialog(hwnd)

    print('Waiting for first WebGPU capture...')
    if wait_for_capture(ident, args.output):
        size = os.path.getsize(args.output) if os.path.exists(args.output) else 0
        print(f'OK: {args.output} ({size:,} bytes)')
        print('Open with: rdc open ' + args.output)
        return 0
    print('No capture received within timeout. Navigate to a WebGPU page and retry.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
