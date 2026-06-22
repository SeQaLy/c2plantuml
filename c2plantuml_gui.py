#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""c2plantuml GUI — tkinter 製のフロントエンド。

- C ファイル / フォルダを選んで PlantUML アクティビティ図 (.puml) を生成
- 変換ロジックは c2plantuml.py をそのまま利用する (標準ライブラリのみ)

起動: python c2plantuml_gui.py
"""
from __future__ import annotations

import glob
import json
import os
import queue
import subprocess
import sys
import threading
from typing import List, Optional

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext

# 同じフォルダの c2plantuml をインポート
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import c2plantuml as core  # noqa: E402

SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".c2plantuml_gui.json")


# --------------------------------------------------------------------------
# 変換ロジック (ワーカースレッドから呼ぶ)
# --------------------------------------------------------------------------

def collect_c_files(input_path: str, recursive: bool) -> List[str]:
    """入力 (ファイル or フォルダ) から対象 C ファイル一覧を作る。"""
    if os.path.isfile(input_path):
        return [input_path]
    if os.path.isdir(input_path):
        pat = "**/*.c" if recursive else "*.c"
        return sorted(glob.glob(os.path.join(input_path, pat),
                                recursive=recursive))
    return []


def convert_one(cfile: str, outbase: Optional[str], max_len: int,
                log) -> List[str]:
    """1 つの C ファイルを「フォルダ + 関数ごとの .puml」へ変換する。"""
    with open(cfile, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()
    stem = os.path.splitext(os.path.basename(cfile))[0]
    base = outbase if outbase else os.path.dirname(os.path.abspath(cfile))
    out_dir = os.path.join(base, core._safe_name(stem))

    tokens = core.tokenize(core.preprocess(src))
    funcs = core.find_functions(tokens)
    if not funcs:
        log(f"[warn] 関数が見つかりません: {cfile}")
        return []

    os.makedirs(out_dir, exist_ok=True)
    written: List[str] = []
    for name, body in funcs:
        puml = core.function_to_puml(name, body, max_len)
        out_path = os.path.join(out_dir, f"{core._safe_name(name)}.puml")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(puml)
        written.append(out_path)
        log(f"[ok] {out_path}")
    log(f"[done] {cfile} -> {out_dir}{os.sep}  ({len(funcs)} 関数)")
    return written


# --------------------------------------------------------------------------
# GUI
# --------------------------------------------------------------------------

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        root.title("c2plantuml — C ソース → PlantUML アクティビティ図")
        root.geometry("760x520")
        self.q: "queue.Queue[tuple]" = queue.Queue()
        self.worker: Optional[threading.Thread] = None
        self.last_outdir: Optional[str] = None

        self._build_widgets()
        self._load_settings()
        self.root.after(100, self._drain_queue)

    # --- ウィジェット構築 ---
    def _build_widgets(self):
        pad = {"padx": 6, "pady": 4}
        frm = ttk.Frame(self.root, padding=8)
        frm.pack(fill="both", expand=True)
        frm.columnconfigure(1, weight=1)

        # 入力
        ttk.Label(frm, text="入力 (C ファイル / フォルダ):").grid(
            row=0, column=0, sticky="w", **pad)
        self.in_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.in_var).grid(
            row=0, column=1, sticky="ew", **pad)
        btns = ttk.Frame(frm)
        btns.grid(row=0, column=2, sticky="e", **pad)
        ttk.Button(btns, text="ファイル…", command=self._pick_file).pack(
            side="left", padx=2)
        ttk.Button(btns, text="フォルダ…", command=self._pick_dir).pack(
            side="left", padx=2)

        # 出力先
        ttk.Label(frm, text="出力先フォルダ (空欄=各 C と同じ場所):").grid(
            row=1, column=0, sticky="w", **pad)
        self.out_var = tk.StringVar()
        ttk.Entry(frm, textvariable=self.out_var).grid(
            row=1, column=1, sticky="ew", **pad)
        ttk.Button(frm, text="参照…", command=self._pick_outdir).grid(
            row=1, column=2, sticky="e", **pad)

        # オプション
        opt = ttk.LabelFrame(frm, text="オプション", padding=6)
        opt.grid(row=2, column=0, columnspan=3, sticky="ew", **pad)
        self.recursive_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opt, text="サブフォルダも探索",
                        variable=self.recursive_var).grid(
            row=0, column=0, sticky="w", padx=6)
        ttk.Label(opt, text="最大文字数:").grid(row=0, column=1, sticky="e",
                                                padx=6)
        self.maxlen_var = tk.IntVar(value=80)
        ttk.Spinbox(opt, from_=20, to=400, width=6,
                    textvariable=self.maxlen_var).grid(row=0, column=2,
                                                       sticky="w")

        # 実行ボタン群
        run = ttk.Frame(frm)
        run.grid(row=3, column=0, columnspan=3, sticky="ew", **pad)
        self.run_btn = ttk.Button(run, text="変換を実行", command=self._run)
        self.run_btn.pack(side="left", padx=4)
        ttk.Button(run, text="ログをクリア", command=self._clear_log).pack(
            side="left", padx=4)
        ttk.Button(run, text="出力フォルダを開く",
                   command=self._open_out).pack(side="left", padx=4)

        # ログ
        ttk.Label(frm, text="ログ:").grid(row=4, column=0, sticky="w", **pad)
        self.log = scrolledtext.ScrolledText(frm, height=16, wrap="none")
        self.log.grid(row=5, column=0, columnspan=3, sticky="nsew", **pad)
        frm.rowconfigure(5, weight=1)
        # ログの色分け: OK=緑 / NG=赤
        self.log.tag_config("ok", foreground="#2e7d32")
        self.log.tag_config("ng", foreground="#c62828")

        self.status = tk.StringVar(value="準備完了")
        ttk.Label(self.root, textvariable=self.status, relief="sunken",
                  anchor="w").pack(fill="x", side="bottom")

    # --- 入出力選択 ---
    def _pick_file(self):
        p = filedialog.askopenfilename(
            title="C ファイルを選択",
            filetypes=[("C ソース", "*.c"), ("すべて", "*.*")])
        if p:
            self.in_var.set(p)

    def _pick_dir(self):
        p = filedialog.askdirectory(title="C ソースのフォルダを選択")
        if p:
            self.in_var.set(p)

    def _pick_outdir(self):
        p = filedialog.askdirectory(title="出力先フォルダを選択")
        if p:
            self.out_var.set(p)

    # --- ログ / キュー ---
    def _log(self, msg: str):
        self.q.put(("log", msg))

    def _clear_log(self):
        self.log.delete("1.0", "end")

    @staticmethod
    def _log_tag(msg: str) -> str:
        """ログ行の先頭タグから色タグ (ok=緑 / ng=赤) を決める。"""
        m = msg.lstrip()
        if m.startswith("[ok]") or m.startswith("[done]"):
            return "ok"
        if m.startswith("[error]") or m.startswith("[warn]"):
            return "ng"
        return ""

    def _drain_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "log":
                    self.log.insert("end", payload + "\n", self._log_tag(payload))
                    self.log.see("end")
                elif kind == "done":
                    self.run_btn.configure(state="normal")
                    self.status.set(payload)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    # --- 実行 ---
    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        input_path = self.in_var.get().strip()
        if not input_path or not os.path.exists(input_path):
            messagebox.showerror("エラー", "入力の C ファイル/フォルダを指定してください。")
            return
        params = dict(
            input_path=input_path,
            outbase=self.out_var.get().strip() or None,
            recursive=self.recursive_var.get(),
            max_len=int(self.maxlen_var.get()),
        )
        self._save_settings(params)
        self.run_btn.configure(state="disabled")
        self.status.set("変換中…")
        self.worker = threading.Thread(target=self._work, args=(params,),
                                       daemon=True)
        self.worker.start()

    def _work(self, p: dict):
        try:
            files = collect_c_files(p["input_path"], p["recursive"])
            if not files:
                self._log("[warn] 対象の .c ファイルがありません。")
                self.q.put(("done", "完了 (対象なし)"))
                return
            self._log(f"=== {len(files)} ファイルを処理 ===")
            all_puml: List[str] = []
            for cf in files:
                all_puml.extend(
                    convert_one(cf, p["outbase"], p["max_len"], self._log))
            if all_puml:
                self.last_outdir = os.path.dirname(all_puml[0])
            self.q.put(("done", f"完了: {len(all_puml)} 図"))
        except Exception as e:  # ワーカー内の例外を UI に伝える
            self._log(f"[error] {e}")
            self.q.put(("done", "エラーで終了"))

    def _open_out(self):
        target = self.last_outdir or self.out_var.get().strip()
        if not target:
            inp = self.in_var.get().strip()
            target = inp if os.path.isdir(inp) else os.path.dirname(inp)
        if target and os.path.isdir(target):
            try:
                if hasattr(os, "startfile"):
                    os.startfile(target)  # type: ignore[attr-defined]
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", target])
                else:
                    subprocess.Popen(["xdg-open", target])
            except OSError as e:
                messagebox.showerror("エラー", str(e))
        else:
            messagebox.showinfo("情報", "開けるフォルダがまだありません。")

    # --- 設定の保存/復元 ---
    def _save_settings(self, p: dict):
        try:
            with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
                json.dump(p, f, ensure_ascii=False, indent=2)
        except OSError:
            pass

    def _load_settings(self):
        try:
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                s = json.load(f)
        except (OSError, ValueError):
            return
        self.in_var.set(s.get("input_path", ""))
        self.out_var.set(s.get("outbase") or "")
        self.recursive_var.set(bool(s.get("recursive", True)))
        self.maxlen_var.set(int(s.get("max_len", 80)))


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
