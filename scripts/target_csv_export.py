#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
TARGET frontier JV 開催成績CSV自動出力スクリプト

TARGETのGUIを Win32 API で操作し
指定した年の全開催をCSV出力する。

Usage:
    py -3.12-32 scripts/target_csv_export.py [options]

    --year     YYYY       出力対象年（デフォルト: 今年）
    --out-dir  DIR        CSV出力先ディレクトリ（デフォルト: C:\\TFJV\\TXT）
    --out-file FILENAME   出力ファイル名（デフォルト: target_{YEAR}.txt）
    --tfjv-exe PATH       TFJV.EXE のパス（デフォルト: C:\\TFJV\\TFJV.EXE）
    --no-launch           TARGET起動済みの場合は起動しない
    --timeout  SEC        各操作のタイムアウト秒数（デフォルト: 30）
"""

import argparse
import ctypes
import ctypes.wintypes
import os
import subprocess
import sys
import time
from datetime import date

# ── Win32 定数 ────────────────────────────────────────────────────────────────
user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

WM_CLOSE       = 0x0010
WM_COMMAND     = 0x0111
WM_KEYDOWN     = 0x0100
WM_KEYUP       = 0x0101
WM_CHAR        = 0x0102
WM_SETTEXT     = 0x000C
BM_CLICK       = 0x00F5
VK_TAB         = 0x09
VK_CONTROL     = 0x11
VK_A           = 0x41
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004


# ── ヘルパー: ウィンドウ探索 ──────────────────────────────────────────────────

def _txt(hwnd: int) -> str:
    buf = ctypes.create_string_buffer(512)
    n = user32.GetWindowTextA(hwnd, buf, 512)
    try:
        return buf.raw[:n].decode('cp932')
    except Exception:
        return ''


def _cls(hwnd: int) -> str:
    buf = ctypes.create_string_buffer(64)
    user32.GetClassNameA(hwnd, buf, 64)
    return buf.value.decode('ascii', errors='replace')


def _rect(hwnd: int) -> ctypes.wintypes.RECT:
    r = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r


def _enum_children(parent: int) -> list[dict]:
    result: list[dict] = []
    def cb(hwnd, _):
        r = _rect(hwnd)
        result.append({
            'hwnd': hwnd, 'class': _cls(hwnd), 'txt': _txt(hwnd),
            'l': r.left, 't': r.top, 'r': r.right, 'b': r.bottom,
        })
        return True
    user32.EnumChildWindows(parent, WNDENUMPROC(cb), 0)
    return result


def _find_top(pid: int, class_name: str) -> int | None:
    found = [None]
    def cb(hwnd, _):
        p = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and _cls(hwnd) == class_name:
            found[0] = hwnd
            return False
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found[0]


def _wait_window(pid: int, class_name: str, timeout: float = 30.0) -> int:
    deadline = time.time() + timeout
    while time.time() < deadline:
        hwnd = _find_top(pid, class_name)
        if hwnd:
            return hwnd
        time.sleep(0.4)
    raise TimeoutError(f'Window {class_name!r} did not appear within {timeout}s')


def _find_by_txt(children: list[dict], label: str) -> dict | None:
    return next((c for c in children if label in c['txt']), None)


# ── ヘルパー: 入力操作 ────────────────────────────────────────────────────────

# ── SendInput 構造体（OS レベルのキーボード入力） ─────────────────────────────

class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ('wVk',         ctypes.c_ushort),
        ('wScan',       ctypes.c_ushort),
        ('dwFlags',     ctypes.c_ulong),
        ('time',        ctypes.c_ulong),
        ('dwExtraInfo', ctypes.c_ulong),
    ]

class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [('ki', _KEYBDINPUT)]
    _anonymous_ = ('_u',)
    _fields_    = [('type', ctypes.c_ulong), ('_u', _U)]

_KEYEVENTF_KEYUP   = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_INPUT_KEYBOARD    = 1


def _send_vk(vk: int, up: bool = False) -> None:
    inp = _INPUT(type=_INPUT_KEYBOARD)
    inp.ki.wVk    = vk
    inp.ki.dwFlags = _KEYEVENTF_KEYUP if up else 0
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
    time.sleep(0.02)


def _send_unicode(ch: str) -> None:
    for flag in (0, _KEYEVENTF_KEYUP):
        inp = _INPUT(type=_INPUT_KEYBOARD)
        inp.ki.wScan  = ord(ch)
        inp.ki.dwFlags = _KEYEVENTF_UNICODE | flag
        user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
        time.sleep(0.02)


def _click_abs(x: int, y: int) -> None:
    """絶対座標クリック（SetCursorPos 後に mouse_event は (0,0) ）。"""
    user32.SetCursorPos(x, y)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    time.sleep(0.05)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
    time.sleep(0.2)


def _click_center(c: dict) -> None:
    cx = (c['l'] + c['r']) // 2
    cy = (c['t'] + c['b']) // 2
    _click_abs(cx, cy)


def _bm_click(hwnd: int) -> None:
    user32.SendMessageW(hwnd, BM_CLICK, 0, 0)
    time.sleep(0.3)


def _type_year(foreground_hwnd: int, year_edit: dict, year: int) -> None:
    """
    SendInput で年フィールドに year を入力する。
    PostMessage/WM_CHAR ではなく SendInput を使うことで
    Delphi の OnChange が確実に発火する。
    """
    # フォアグラウンド確保 → EditをクリックしてOS側のフォーカスを移す
    user32.SetForegroundWindow(foreground_hwnd)
    time.sleep(0.3)
    _click_center(year_edit)
    time.sleep(0.2)

    # Ctrl+A → Delete で全消去
    _send_vk(VK_CONTROL)
    _send_vk(VK_A)
    _send_vk(VK_A, up=True)
    _send_vk(VK_CONTROL, up=True)
    time.sleep(0.1)
    _send_vk(0x2E)        # VK_DELETE
    _send_vk(0x2E, up=True)
    time.sleep(0.1)

    # 数字を 1 文字ずつ SendInput で送る（UNICODE フラグ付き）
    for ch in str(year):
        _send_unicode(ch)

    # Tab で次フィールドへ → Delphi OnExit / OnChange 発火
    _send_vk(VK_TAB)
    _send_vk(VK_TAB, up=True)
    time.sleep(0.8)


# ── プロセス探索 ──────────────────────────────────────────────────────────────

def _get_pid(exe_path: str) -> int | None:
    exe_name = os.path.basename(exe_path).upper()
    try:
        out = subprocess.check_output(
            ['tasklist', '/FI', f'IMAGENAME eq {exe_name}', '/FO', 'CSV', '/NH'],
            text=True, encoding='cp932', errors='replace')
        for line in out.strip().splitlines():
            parts = [p.strip('"') for p in line.split(',')]
            if parts and parts[0].upper() == exe_name:
                return int(parts[1])
    except Exception:
        pass
    return None


# ── コア操作 ──────────────────────────────────────────────────────────────────

def open_csv_dialog(main_hwnd: int, pid: int) -> int:
    """メニュー[0-17]「開催成績CSV出力」を開き TfmSeiOut hwnd を返す。"""
    hmenu  = user32.GetMenu(main_hwnd)
    hsub0  = user32.GetSubMenu(hmenu, 0)
    item_id = user32.GetMenuItemID(hsub0, 17)
    print(f'  menu id={item_id} → 開催成績CSV出力', flush=True)
    user32.PostMessageW(main_hwnd, WM_COMMAND, item_id, 0)
    time.sleep(1.5)
    return _wait_window(pid, 'TfmSeiOut')


_TGOFILE_LST = r'C:\TFJV\TgOutFile.Lst'

def prepare_output_path(out_path: str) -> None:
    """
    TgOutFile.Lst の先頭に out_path を追記する。
    TARGET 起動時にこのファイルを読んで ComboBox に履歴を設定するため、
    再起動後は out_path がデフォルトで表示される。
    """
    lines: list[str] = []
    if os.path.exists(_TGOFILE_LST):
        with open(_TGOFILE_LST, encoding='cp932', errors='replace') as f:
            lines = [l.rstrip('\r\n') for l in f if l.strip()]
    # 重複除去して先頭に追加
    lines = [out_path] + [l for l in lines if l != out_path]
    content = '\r\n'.join(lines) + '\r\n'
    with open(_TGOFILE_LST, 'w', encoding='cp932') as f:
        f.write(content)
    print(f'  TgOutFile.Lst 更新: {out_path} を先頭に追加', flush=True)


def set_output_path(dlg_hwnd: int, out_path: str) -> None:
    """
    TfmSeiOut の出力パス欄に out_path を設定する。
    WM_GETTEXT で読み取り、CB_FINDSTRINGEXACT→CB_SETCURSEL→CBN_SELCHANGE 通知の順で試みる。
    """
    CB_ADDSTRING       = 0x0143
    CB_SETCURSEL       = 0x014E
    CB_GETCOUNT        = 0x0146
    CB_GETCURSEL       = 0x0147
    CB_FINDSTRINGEXACT = 0x0158
    CBN_SELCHANGE      = 1
    WM_GETTEXT         = 0x000D

    def _wm_txt(hwnd: int) -> str:
        buf = ctypes.create_unicode_buffer(512)
        n = user32.SendMessageW(hwnd, WM_GETTEXT, 512, buf)
        return buf.value[:n] if n > 0 else ''

    # TfmSeiOut 内の全 TComboBox / Edit を列挙して診断
    children = _enum_children(dlg_hwnd)
    combos   = [c for c in children if c['class'] == 'TComboBox']
    edits    = [c for c in children if c['class'] in ('TEdit', 'Edit')]
    print(f'  TComboBox x{len(combos)}: ' +
          ', '.join(f'hwnd={c["hwnd"]} x={c["l"]}-{c["r"]} y={c["t"]}-{c["b"]} txt={repr(_wm_txt(c["hwnd"]))[:20]}'
                    for c in combos), flush=True)
    print(f'  TEdit/Edit x{len(edits)}: ' +
          ', '.join(f'hwnd={c["hwnd"]} x={c["l"]}-{c["r"]} y={c["t"]}-{c["b"]} txt={repr(_wm_txt(c["hwnd"]))[:20]}'
                    for c in edits), flush=True)

    if not combos:
        raise RuntimeError('TComboBox not found in TfmSeiOut')

    combo      = combos[0]
    combo_hwnd = combo['hwnd']
    ec         = _enum_children(combo_hwnd)
    edit_inner = next((c for c in ec if c['class'] == 'Edit'), None)
    edit_hwnd  = edit_inner['hwnd'] if edit_inner else None

    count = user32.SendMessageW(combo_hwnd, CB_GETCOUNT, 0, 0)
    cur   = user32.SendMessageW(combo_hwnd, CB_GETCURSEL, 0, 0)
    now   = _wm_txt(edit_hwnd) if edit_hwnd else _wm_txt(combo_hwnd)
    print(f'  combo={combo_hwnd} edit={edit_hwnd} items={count} cursel={cur} now={now!r}', flush=True)

    if now.strip() == out_path:
        print('  パス一致（スキップ）', flush=True)
        return

    # ── Step1: リスト内を検索して選択 ─────────────────────────────────
    found = user32.SendMessageW(combo_hwnd, CB_FINDSTRINGEXACT, -1, out_path)
    if found < 0:
        found = user32.SendMessageW(combo_hwnd, CB_ADDSTRING, 0, out_path)
        print(f'  CB_ADDSTRING -> idx={found}', flush=True)
    r = user32.SendMessageW(combo_hwnd, CB_SETCURSEL, found, 0)
    print(f'  CB_FINDSTRINGEXACT={found}  CB_SETCURSEL={r}', flush=True)

    # Delphi に CBN_SELCHANGE 通知（OnChange を発火させる）
    combo_id = user32.GetDlgCtrlID(combo_hwnd)
    user32.SendMessageW(dlg_hwnd, WM_COMMAND,
                        (CBN_SELCHANGE << 16) | (combo_id & 0xFFFF),
                        combo_hwnd)
    time.sleep(0.2)
    after1 = _wm_txt(edit_hwnd) if edit_hwnd else _wm_txt(combo_hwnd)
    print(f'  CBN_SELCHANGE後: {after1!r}', flush=True)
    if after1.strip() == out_path:
        return

    # ── Step2: WM_SETTEXT ────────────────────────────────────────────
    if edit_hwnd:
        user32.SendMessageW(edit_hwnd,   WM_SETTEXT, 0, out_path)
    user32.SendMessageW(combo_hwnd, WM_SETTEXT, 0, out_path)
    after2 = _wm_txt(edit_hwnd) if edit_hwnd else _wm_txt(combo_hwnd)
    print(f'  WM_SETTEXT後: {after2!r}', flush=True)
    if after2.strip() == out_path:
        return

    # ── Step3: フォーカス + SendInput ────────────────────────────────
    tgt_dict = edit_inner if edit_inner else combo
    user32.SetForegroundWindow(dlg_hwnd)
    time.sleep(0.3)
    _click_center(tgt_dict)
    time.sleep(0.3)
    _send_vk(VK_CONTROL); _send_vk(VK_A); _send_vk(VK_A, up=True); _send_vk(VK_CONTROL, up=True)
    time.sleep(0.1)
    _send_vk(0x2E); _send_vk(0x2E, up=True)  # DELETE
    time.sleep(0.1)
    for ch in out_path:
        _send_unicode(ch)
    time.sleep(0.3)
    _send_vk(VK_TAB); _send_vk(VK_TAB, up=True)
    time.sleep(0.3)
    after3 = _wm_txt(edit_hwnd) if edit_hwnd else _wm_txt(combo_hwnd)
    print(f'  SendInput後: {after3!r}', flush=True)
    if not after3.strip():
        print(f'  WARNING: パス設定失敗 out_path={out_path!r}', flush=True)


def select_output_mode(dlg_hwnd: int, mode: str) -> None:
    """
    TfmSeiOut のラジオボタン（Delphi TGroupButton）で出力モードを選択する。
      mode='seiseki' → 基本＋単勝オッズ          → import_target_seiseki.py
      mode='lap'     → 成績画面・レースデータ（ユーザー設定）→ import_target_csv.py
    TGroupButton は TRadioGroup 内の各ボタンに対応し HWND を持つ。
    BM_CLICK で直接クリック可能。
    """
    GAMEN   = chr(0x753B) + chr(0x9762)   # 画面
    KIHON   = chr(0x57FA) + chr(0x672C)   # 基本
    TANSHO  = chr(0x5358) + chr(0x52DD)   # 単勝

    children = _enum_children(dlg_hwnd)
    grp_btns = [c for c in children if c['class'] == 'TGroupButton']
    print(f'  TGroupButton(RadioButton) x{len(grp_btns)}:', flush=True)
    for b in grp_btns:
        print(f'    hwnd={b["hwnd"]} y={b["t"]}-{b["b"]}  txt={repr(b["txt"])}', flush=True)

    if mode == 'seiseki':
        # 基本＋単勝オッズ: 基本 と 単勝 を含む（フルセット+単勝 は 基本 を含まない）
        target = next((b for b in grp_btns
                       if KIHON in b['txt'] and TANSHO in b['txt']), None)
    elif mode == 'lap':
        # 成績画面・レースデータ（ユーザー設定）: '画面' を含む
        target = next((b for b in grp_btns if GAMEN in b['txt']), None)
    else:
        raise ValueError(f'不明なモード: {mode!r}  (seiseki / lap)')

    if target is None:
        raise RuntimeError(f'モード {mode!r} のラジオボタンが見つかりません')

    print(f'  [{mode}] クリック: hwnd={target["hwnd"]}  txt={repr(target["txt"])}', flush=True)
    _bm_click(target['hwnd'])
    time.sleep(0.3)


# navigate_year は不要（TfmSelSei はデフォルトで最新年を表示する）


def click_all_select(sel_hwnd: int, sel_children: list[dict]) -> None:
    """
    「全選択」または「選択完了」ボタンをクリックする。
    Unicode コードポイントで直接比較（エンコード依存なし）。

    ボタン優先順位:
      1. 選択完了 (U+9078 U+629E U+5B8C U+4E86) … 年ナビ後に表示される確認ボタン
      2. 全選択   (U+5168 U+9078 U+629E)         … 初期状態で表示される一括選択ボタン
    """
    SENTAKU_KANRYO = chr(0x9078) + chr(0x629E) + chr(0x5B8C) + chr(0x4E86)  # 選択完了
    ZENSENTAKU     = chr(0x5168) + chr(0x9078) + chr(0x629E)                 # 全選択

    btns = [c for c in sel_children if c['class'] == 'TButton']
    for b in btns:
        h = b['txt'].encode('utf-8').hex() if b['txt'] else '(empty)'
        print(f'  btn hex={h}  x={b["l"]}  y={b["t"]}-{b["b"]}', flush=True)

    if not btns:
        raise RuntimeError('No TButton found in TfmSelSei')

    target_btn = None
    label = ''

    # ① 選択完了
    for b in btns:
        if SENTAKU_KANRYO in b['txt']:
            target_btn = b
            label = '選択完了'
            break

    # ② 全選択
    if target_btn is None:
        for b in btns:
            if ZENSENTAKU in b['txt']:
                target_btn = b
                label = '全選択'
                break

    # ③ フォールバック: 最下行ボタン群の中で x 座標が中央付近のもの
    #    (右端=ヘルプ, 右2番目=キャンセル, 右3番目=選択完了/全選択 のレイアウトを想定)
    if target_btn is None:
        max_t = max(b['t'] for b in btns)
        bottom = sorted([b for b in btns if abs(b['t'] - max_t) < 20], key=lambda b: b['l'])
        print(f'  ⚠ テキスト検索失敗 → 位置フォールバック: {len(bottom)} buttons', flush=True)
        if len(bottom) >= 3:
            target_btn = bottom[-3]   # 右から3番目
            label = 'fallback(-3)'

    if target_btn is None:
        raise RuntimeError('全選択/選択完了 ボタンが見つかりません')

    print(f'  [{label}] クリック: x={target_btn["l"]}-{target_btn["r"]}  y={target_btn["t"]}-{target_btn["b"]}', flush=True)
    _click_center(target_btn)
    time.sleep(0.8)


def click_confirm_or_ok(sel_hwnd: int, pid: int) -> None:
    """
    TfmSelSei を OK か選択完了で閉じる。
    クリック後にダイアログが消えれば成功。消えなければ OK を追加クリック。
    """
    # ダイアログがまだ開いているか確認
    if _find_top(pid, 'TfmSelSei') is None:
        print('  TfmSelSei は既に閉じている', flush=True)
        return

    sel_children = _enum_children(sel_hwnd)
    btns = [c for c in sel_children if c['class'] == 'TButton']

    # 'OK' は ASCII なので確実に比較できる
    ok = next((b for b in btns if b['txt'].strip() == 'OK'), None)
    if ok:
        print(f'  OK クリック: x={ok["l"]}  y={ok["t"]}', flush=True)
        _click_center(ok)
        time.sleep(1.0)
    else:
        print('  OK ボタンなし（選択完了で閉じた可能性あり）', flush=True)


def _open_sel_dialog(dlg_hwnd: int, pid: int, timeout: float) -> int:
    """「開催選択」ボタンをクリックして TfmSelSei を開き hwnd を返す。"""
    children = _enum_children(dlg_hwnd)
    kai_btn  = _find_by_txt(children, '開催選択')
    if not kai_btn:
        raise RuntimeError('「開催選択」ボタンが見つかりません')
    _click_center(kai_btn)
    time.sleep(1.0)
    return _wait_window(pid, 'TfmSelSei', timeout)


def select_meetings(dlg_hwnd: int, pid: int, timeout: float) -> None:
    """
    TfmSeiOut の「開催選択」→ TfmSelSei で全開催を選択・確定。

    「全選択」は TSpeedButton（HWND なし）のため TButton 検索では見つからない。
    TStringGrid の直下のボタン行に描画されており、座標計算でクリックする。
    当年グリッド = x 座標が最も小さい TStringGrid。
    全選択位置  = グリッド下端 +15px / グリッド左端 +20px。
    """
    SENTAKU_KANRYO = chr(0x9078) + chr(0x629E) + chr(0x5B8C) + chr(0x4E86)  # 選択完了

    print('  「開催選択」クリック', flush=True)
    sel_hwnd = _open_sel_dialog(dlg_hwnd, pid, timeout)
    print(f'  TfmSelSei: hwnd={sel_hwnd}', flush=True)
    user32.SetForegroundWindow(sel_hwnd)
    time.sleep(0.5)

    sel_children = _enum_children(sel_hwnd)
    btns  = [c for c in sel_children if c['class'] == 'TButton']
    grids = [c for c in sel_children if c['class'] == 'TStringGrid']

    # TButton 一覧ログ
    for b in btns:
        h = b['txt'].encode('utf-8').hex() if b['txt'] else '(empty)'
        print(f'  btn hwnd={b["hwnd"]} hex={h} txt={repr(b["txt"])}  x={b["l"]}-{b["r"]}  y={b["t"]}-{b["b"]}', flush=True)

    print(f'  TStringGrid x{len(grids)}: ' +
          ', '.join(f'x={g["l"]}-{g["r"]} y={g["t"]}-{g["b"]}' for g in grids), flush=True)

    # ── 全選択: TSpeedButton を座標クリック ──────────────────────────────
    if grids:
        # x 座標最小 = 当年（2026）グリッド
        grid_cur = min(grids, key=lambda g: g['l'])
        zen_x = grid_cur['l'] + 20       # グリッド左端から 20px（1個目のボタン中央付近）
        zen_y = grid_cur['b'] + 15       # グリッド下端から 15px（ボタン行中央）
        print(f'  全選択 TSpeedButton 座標クリック: ({zen_x}, {zen_y})', flush=True)
        user32.SetForegroundWindow(sel_hwnd)
        time.sleep(0.2)
        _click_abs(zen_x, zen_y)
        time.sleep(0.8)
    else:
        # フォールバック: TButton "全選択" を検索
        ZENSENTAKU = chr(0x5168) + chr(0x9078) + chr(0x629E)
        zen_btns = [b for b in btns if ZENSENTAKU in b['txt']]
        zen_btn  = min(zen_btns, key=lambda b: b['l']) if zen_btns else None
        if zen_btn:
            print(f'  TButton 全選択 クリック: x={zen_btn["l"]} y={zen_btn["t"]}', flush=True)
            _click_center(zen_btn)
            time.sleep(0.8)
        else:
            print('  WARNING: 全選択ボタン見つからず', flush=True)

    # ── 選択完了 でダイアログを閉じる ────────────────────────────────────
    sel_children = _enum_children(sel_hwnd)
    btns = [c for c in sel_children if c['class'] == 'TButton']
    sentaku_btn = next((b for b in btns if SENTAKU_KANRYO in b['txt']), None)
    if sentaku_btn:
        print(f'  選択完了クリック: x={sentaku_btn["l"]}-{sentaku_btn["r"]} y={sentaku_btn["t"]}-{sentaku_btn["b"]}', flush=True)
        user32.SetForegroundWindow(sel_hwnd)
        time.sleep(0.2)
        _click_center(sentaku_btn)
        time.sleep(0.8)

    # TfmSelSei が閉じていなければ OK をクリック
    click_confirm_or_ok(sel_hwnd, pid)


def start_output(dlg_hwnd: int) -> None:
    """「出力開始」ボタンをクリック。"""
    children = _enum_children(dlg_hwnd)
    btn = _find_by_txt(children, '出力開始')
    if not btn:
        raise RuntimeError('「出力開始」ボタンが見つかりません')
    print('  「出力開始」クリック', flush=True)
    _click_center(btn)


def _dismiss_overwrite_dialog(pid: int) -> bool:
    """
    「同名ファイル処理選択」ダイアログを検出して「上書き」を自動クリックする。

    検出方法（優先順位）:
      1. ウィンドウタイトルが OVERWRITE_TITLE を含む
      2. クラスが TMessageForm / #32770 で 上書き ボタンを持つ
    """
    OVERWRITE_TITLE = (chr(0x540C) + chr(0x540D) + chr(0x30D5) + chr(0x30A1) +
                       chr(0x30A4) + chr(0x30EB) + chr(0x51E6) + chr(0x7406) +
                       chr(0x9078) + chr(0x629E))   # 同名ファイル処理選択
    UWAGAKI         = chr(0x4E0A) + chr(0x66F8) + chr(0x304D)  # 上書き

    confirm_hwnd = [None]

    def top_cb(hwnd, _):
        p = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        title = _txt(hwnd)
        c     = _cls(hwnd)
        # タイトル一致（最優先）
        if OVERWRITE_TITLE in title:
            confirm_hwnd[0] = hwnd
            return False
        # クラス名フォールバック
        if c in ('TMessageForm', '#32770'):
            confirm_hwnd[0] = hwnd
            return False
        return True

    user32.EnumWindows(WNDENUMPROC(top_cb), 0)

    if not confirm_hwnd[0]:
        return False

    children = _enum_children(confirm_hwnd[0])
    btn = next((c for c in children if UWAGAKI in c['txt']), None)
    if btn:
        print(f'  [上書き] クリック hwnd={btn["hwnd"]}  txt={repr(btn["txt"])}', flush=True)
        _click_center(btn)
        time.sleep(0.5)
        return True

    # ボタンが見つからない場合はタイトルだけログ
    dlg_title = _txt(confirm_hwnd[0])
    print(f'  [上書きダイアログ検出] 上書きボタンなし title={repr(dlg_title)}', flush=True)
    return False


def wait_done(pid: int, out_path: str, timeout: float = 120.0) -> None:
    """TfmSeiOut が消えるか出力ファイルが更新されるまで待つ。
    上書き確認ダイアログが出た場合は自動で「上書き」をクリックする。
    """
    mtime_before = os.path.getmtime(out_path) if os.path.exists(out_path) else None
    deadline = time.time() + timeout
    while time.time() < deadline:
        # 上書き確認ダイアログを自動処理
        _dismiss_overwrite_dialog(pid)

        if _find_top(pid, 'TfmSeiOut') is None:
            print('  ダイアログが閉じた（完了）', flush=True)
            return
        if os.path.exists(out_path):
            mt = os.path.getmtime(out_path)
            if mtime_before is None or mt > mtime_before:
                time.sleep(1.0)
                print('  ファイル更新を検出（完了）', flush=True)
                return
        time.sleep(1.0)
    print('  警告: タイムアウト', flush=True)


def close_dialog(dlg_hwnd: int) -> None:
    children = _enum_children(dlg_hwnd)
    btn = _find_by_txt(children, '終了')
    if btn:
        _click_center(btn)
    else:
        user32.PostMessageW(dlg_hwnd, WM_CLOSE, 0, 0)
    time.sleep(0.5)


# ── メイン ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='TARGET CSV 自動出力')
    parser.add_argument('--year',      type=int, default=date.today().year)
    parser.add_argument('--out-dir',   default=r'C:\TFJV\TXT')
    parser.add_argument('--out-file',  default='')
    parser.add_argument('--tfjv-exe',  default=r'C:\TFJV\TFJV.EXE')
    parser.add_argument('--no-launch', action='store_true')
    parser.add_argument('--restart',     action='store_true',
                        help='TARGETを再起動して初期状態にする（推奨）')
    parser.add_argument('--mode', choices=['seiseki', 'lap'], default=None,
                        help='seiseki=基本+単勝オッズ / lap=成績画面・レースデータ(ユーザー設定)')
    parser.add_argument('--clear-ktlist', action='store_true',
                        help='KTList*.IDXを削除して開催選択を初期モード（全選択）で強制起動')
    parser.add_argument('--timeout',   type=float, default=30.0)
    args = parser.parse_args()

    out_file = args.out_file or f'target_{args.year}.txt'
    out_path = os.path.join(args.out_dir, out_file)

    print(f'=== TARGET CSV 出力 ===', flush=True)
    print(f'  対象年: {args.year}', flush=True)
    print(f'  出力先: {out_path}', flush=True)

    # 出力ディレクトリを作成
    os.makedirs(args.out_dir, exist_ok=True)

    # KTList*.IDX を削除して開催選択を初期モード（全選択ボタン表示）に強制
    if args.clear_ktlist:
        import glob as _glob
        ktlist_dir = os.path.dirname(args.tfjv_exe)
        for f in _glob.glob(os.path.join(ktlist_dir, 'KTList*.IDX')):
            os.remove(f)
            print(f'  KTList削除: {f}', flush=True)

    # 出力パスを TgOutFile.Lst に事前登録（TARGET 起動前に行う）
    prepare_output_path(out_path)

    # TARGET 起動確認（--restart 時は再起動）
    pid = _get_pid(args.tfjv_exe)
    if pid and args.restart:
        print(f'  TARGET再起動（PID={pid} 終了中）...', flush=True)
        subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                       capture_output=True)
        time.sleep(3)
        pid = None

    if pid is None:
        if args.no_launch:
            print('ERROR: TARGET未起動 (--no-launch)', file=sys.stderr)
            sys.exit(1)
        print('  TARGET起動中...', flush=True)
        subprocess.Popen([args.tfjv_exe])
        time.sleep(10)
        pid = _get_pid(args.tfjv_exe)
        if pid is None:
            print('ERROR: 起動失敗', file=sys.stderr)
            sys.exit(1)
    print(f'  PID: {pid}', flush=True)

    main_hwnd = _wait_window(pid, 'TTGM', args.timeout)
    print(f'  メインウィンドウ: {main_hwnd}', flush=True)
    user32.SetForegroundWindow(main_hwnd)
    time.sleep(0.5)

    # 既存ダイアログを閉じる
    for cls in ('TfmSelSei', 'TfmSeiOut'):
        h = _find_top(pid, cls)
        if h:
            user32.PostMessageW(h, WM_CLOSE, 0, 0)
            time.sleep(0.3)

    print('[1] CSV出力ダイアログを開く...', flush=True)
    dlg_hwnd = open_csv_dialog(main_hwnd, pid)
    print(f'  TfmSeiOut: {dlg_hwnd}', flush=True)

    print('[2] 出力パスを設定...', flush=True)
    set_output_path(dlg_hwnd, out_path)

    if args.mode:
        print(f'[2b] 出力モード選択: {args.mode}...', flush=True)
        select_output_mode(dlg_hwnd, args.mode)

    print('[3] 開催選択（全場選択）...', flush=True)
    select_meetings(dlg_hwnd, pid, args.timeout)

    # TfmSelSei が閉じた後に TfmSeiOut を再取得
    dlg_hwnd = _wait_window(pid, 'TfmSeiOut', args.timeout)

    print('[4] 出力開始...', flush=True)
    start_output(dlg_hwnd)

    print('[5] 完了待ち...', flush=True)
    wait_done(pid, out_path, timeout=args.timeout * 4)

    h = _find_top(pid, 'TfmSeiOut')
    if h:
        close_dialog(h)

    if os.path.exists(out_path):
        size = os.path.getsize(out_path)
        print(f'\n完了: {out_path}  ({size:,} bytes)', flush=True)
    else:
        print(f'\n警告: ファイルが見つかりません: {out_path}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
