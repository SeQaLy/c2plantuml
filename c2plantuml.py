#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""c2plantuml — C ソースの制御フローを PlantUML アクティビティ図 (beta) に変換する。

各関数定義を 1 つの ``@startuml ... @enduml`` ブロックに書き出す。
if / else if / else, while, do-while, for, switch/case, return, break,
continue を、PlantUML アクティビティ図構文へマッピングする。
goto 文・ラベルは通常のアクティビティ (:goto x; / :label:;) として表示する。

仕様参照: https://plantuml.com/ja/activity-diagram-beta

外部依存なし。プリプロセス不要（マクロは展開せずそのまま扱う）。

使い方:
    python c2plantuml.py foo.c                 # foo/<関数名>.puml を生成
    python c2plantuml.py foo.c bar.c           # ファイルごとにフォルダ生成
    python c2plantuml.py foo.c -o out.puml     # まとめて out.puml に出力
    python c2plantuml.py foo.c --stdout        # 標準出力へ
    cat foo.c | python c2plantuml.py - --stdout# 標準入力から
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------
# 字句解析
# --------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      (?P<ws>\s+)
    | (?P<line_comment>//[^\n]*)
    | (?P<block_comment>/\*.*?\*/)
    | (?P<string>"(?:\\.|[^"\\])*")
    | (?P<char>'(?:\\.|[^'\\])*')
    | (?P<number>\.?\d(?:[\w.]|[eEpP][+-])*)
    | (?P<ident>[A-Za-z_]\w*)
    | (?P<punct>
          \.\.\.|<<=|>>=
        | ->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\|
        | \+=|-=|\*=|/=|%=|&=|\|=|\^=
        | [-+*/%&|^~!<>=?:;,.()\[\]{}]
      )
    | (?P<other>.)
    """,
    re.VERBOSE | re.DOTALL,
)


class Token:
    __slots__ = ("kind", "val")

    def __init__(self, kind: str, val: str):
        self.kind = kind
        self.val = val

    def __repr__(self):  # pragma: no cover - デバッグ用
        return f"Token({self.kind!r}, {self.val!r})"


def preprocess(src: str) -> str:
    """行継続を畳み、プリプロセッサ指令行を除去する。"""
    # 行継続 (バックスラッシュ + 改行) を 1 行に結合
    src = re.sub(r"\\\n", " ", src)
    # #include / #define / #ifdef ... などの指令行を削除
    src = re.sub(r"(?m)^[ \t]*#.*$", "", src)
    return src


def tokenize(src: str) -> List[Token]:
    """C ソースをトークン列へ。空白・コメントは捨てる。"""
    tokens: List[Token] = []
    for m in _TOKEN_RE.finditer(src):
        kind = m.lastgroup
        if kind in ("ws", "line_comment", "block_comment"):
            continue
        if kind in ("punct", "other"):
            tokens.append(Token("punct", m.group()))
        else:
            tokens.append(Token(kind, m.group()))
    return tokens


# --------------------------------------------------------------------------
# 関数定義の抽出
# --------------------------------------------------------------------------

_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {v: k for k, v in _OPEN.items()}


def _match_forward(tokens: List[Token], i: int) -> int:
    """tokens[i] の開き括弧に対応する閉じ括弧の添字を返す。"""
    open_ch = tokens[i].val
    close_ch = _OPEN[open_ch]
    depth = 0
    j = i
    n = len(tokens)
    while j < n:
        v = tokens[j].val
        if v == open_ch:
            depth += 1
        elif v == close_ch:
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return n - 1


def _match_backward(tokens: List[Token], i: int) -> int:
    """tokens[i] の閉じ括弧に対応する開き括弧の添字を返す。"""
    close_ch = tokens[i].val
    open_ch = _CLOSE[close_ch]
    depth = 0
    j = i
    while j >= 0:
        v = tokens[j].val
        if v == close_ch:
            depth += 1
        elif v == open_ch:
            depth -= 1
            if depth == 0:
                return j
        j -= 1
    return 0


def _is_function_head(tokens: List[Token], start: int, brace: int) -> bool:
    """tokens[start:brace] が関数定義のシグネチャか判定する。"""
    if brace - 1 < start:
        return False
    # 直前トークンが ')' (= 仮引数リストの閉じ) であること
    if tokens[brace - 1].val != ")":
        return False
    # トップレベルに '=' があれば初期化子付き宣言なので関数ではない
    depth = 0
    for k in range(start, brace):
        v = tokens[k].val
        if v in "([{":
            depth += 1
        elif v in ")]}":
            depth -= 1
        elif v == "=" and depth == 0:
            return False
    return True


def _function_name(tokens: List[Token], open_paren: int) -> Optional[str]:
    """仮引数リスト '(' の手前から関数名を取り出す。"""
    k = open_paren - 1
    while k >= 0:
        t = tokens[k]
        if t.kind == "ident":
            if t.val in ("if", "for", "while", "switch", "return", "sizeof"):
                return None
            return t.val
        # `(*name)` のような関数ポインタ宣言子の括弧などは読み飛ばす
        if t.val in (")", "]"):
            k = _match_backward(tokens, k) - 1
            continue
        if t.val == "*":
            k -= 1
            continue
        break
    return None


def find_functions(tokens: List[Token]) -> List[Tuple[str, List[Token]]]:
    """トップレベルの関数定義を (名前, 本体トークン列) のリストで返す。"""
    funcs: List[Tuple[str, List[Token]]] = []
    n = len(tokens)
    i = 0
    depth = 0
    start = 0
    while i < n:
        v = tokens[i].val
        if v == "{":
            if depth == 0 and _is_function_head(tokens, start, i):
                close_paren = i - 1
                open_paren = _match_backward(tokens, close_paren)
                name = _function_name(tokens, open_paren)
                end = _match_forward(tokens, i)
                if name:
                    funcs.append((name, tokens[i + 1:end]))
                i = end + 1
                start = i
                continue
            depth += 1
        elif v == "}":
            if depth > 0:
                depth -= 1
            if depth == 0:
                start = i + 1
        elif v == ";" and depth == 0:
            start = i + 1
        i += 1
    return funcs


# --------------------------------------------------------------------------
# 文法解析 (制御フローのみ)
# --------------------------------------------------------------------------

class Node:
    pass


class Simple(Node):
    def __init__(self, text):
        self.text = text


class Return(Node):
    def __init__(self, text):
        self.text = text


class Break(Node):
    pass


class Continue(Node):
    pass


class Goto(Node):
    def __init__(self, label):
        self.label = label


class Label(Node):
    def __init__(self, name):
        self.name = name


class If(Node):
    def __init__(self, branches, els):
        self.branches = branches  # List[(cond, body_list)]
        self.els = els            # body_list | None


class While(Node):
    def __init__(self, cond, body):
        self.cond = cond
        self.body = body


class DoWhile(Node):
    def __init__(self, cond, body):
        self.cond = cond
        self.body = body


class For(Node):
    def __init__(self, init, cond, incr, body):
        self.init = init
        self.cond = cond
        self.incr = incr
        self.body = body


class Switch(Node):
    def __init__(self, expr, cases):
        self.expr = expr
        self.cases = cases  # List[(labels_list, body_list)]


class Block(Node):
    def __init__(self, stmts):
        self.stmts = stmts


_KEYWORDS = {"if", "while", "do", "for", "switch", "return", "break",
             "continue", "goto"}


def _tokens_to_text(tokens: List[Token]) -> str:
    """トークン列を読みやすい 1 行の文字列へ復元する。"""
    text = " ".join(t.val for t in tokens)
    text = re.sub(r"\s*->\s*", "->", text)              # アロー演算子
    text = re.sub(r"\s*\.\s*", ".", text)               # メンバアクセス
    text = re.sub(r"\s+([,;)\]])", r"\1", text)         # 閉じ括弧/区切りの前
    text = re.sub(r"([(\[])\s+", r"\1", text)           # 開き括弧の後
    text = re.sub(r"([\w)\]])\s+([(\[])", r"\1\2", text)  # 関数呼出/添字
    text = re.sub(r"([\w)\]])\s*(\+\+|--)", r"\1\2", text)  # 後置 i++ / a[i]--
    text = re.sub(r"(\+\+|--)\s*(\w)", r"\1\2", text)    # 前置 ++i / --i
    text = re.sub(r"([!~])\s+", r"\1", text)            # 単項 ! ~
    text = re.sub(r"^([-+~!*&])\s+", r"\1", text)       # 先頭の単項演算子
    # 演算子/開き括弧の直後にある単項 -, +, !, ~ の余分な空白を詰める
    text = re.sub(r"(?<=[-+*/%=<>!&|^~(,\[{:?]) ([-+!~]) (?=[\w(])", r" \1",
                  text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


class Parser:
    def __init__(self, tokens: List[Token]):
        self.toks = tokens
        self.pos = 0
        self.n = len(tokens)

    # --- 低水準 ---
    def peek(self, k: int = 0) -> Optional[Token]:
        j = self.pos + k
        return self.toks[j] if j < self.n else None

    def peekv(self, k: int = 0) -> Optional[str]:
        t = self.peek(k)
        return t.val if t else None

    def next(self) -> Optional[Token]:
        t = self.peek()
        if t is not None:
            self.pos += 1
        return t

    def _read_group(self) -> List[Token]:
        """現在位置の開き括弧 '(' 群の中身トークンを返し、位置を ')' の次へ。"""
        assert self.peekv() == "("
        start = self.pos
        end = _match_forward(self.toks, start)
        inner = self.toks[start + 1:end]
        self.pos = end + 1
        return inner

    def _read_paren_text(self) -> str:
        return _tokens_to_text(self._read_group())

    def _read_until_semicolon(self) -> List[Token]:
        """';' (括弧の外) までのトークンを返し、位置を ';' の次へ。"""
        out: List[Token] = []
        depth = 0
        while self.pos < self.n:
            t = self.toks[self.pos]
            if t.val in "([{":
                depth += 1
            elif t.val in ")]}":
                depth -= 1
            elif t.val == ";" and depth == 0:
                self.pos += 1
                return out
            out.append(t)
            self.pos += 1
        return out

    # --- 文 ---
    def parse_body(self) -> List[Node]:
        """ブロック '{...}' または単文を、文のリストとして返す。"""
        if self.peekv() == "{":
            self.next()  # consume '{'
            stmts: List[Node] = []
            while self.pos < self.n and self.peekv() != "}":
                node = self.parse_statement()
                if node is not None:
                    stmts.append(node)
            if self.peekv() == "}":
                self.next()
            return stmts
        node = self.parse_statement()
        return [node] if node is not None else []

    def parse_statement(self) -> Optional[Node]:
        t = self.peek()
        if t is None:
            return None
        v = t.val

        if v == ";":            # 空文
            self.next()
            return None
        if v == "{":            # 入れ子ブロック
            return Block(self.parse_body())

        if t.kind == "ident":
            if v == "if":
                return self.parse_if()
            if v == "while":
                return self.parse_while()
            if v == "do":
                return self.parse_do()
            if v == "for":
                return self.parse_for()
            if v == "switch":
                return self.parse_switch()
            if v == "return":
                self.next()
                text = _tokens_to_text(self._read_until_semicolon())
                return Return(text)
            if v == "break":
                self.next()
                self._read_until_semicolon()
                return Break()
            if v == "continue":
                self.next()
                self._read_until_semicolon()
                return Continue()
            if v == "goto":
                self.next()
                label = self.peekv() or ""
                self._read_until_semicolon()
                return Goto(label)
            # ラベル: `name :`  (三項演算子 '?:' とは区別される)
            if self.peekv(1) == ":":
                self.next()  # name
                self.next()  # ':'
                return Label(v)

        # それ以外は式文 → アクティビティ 1 個
        text = _tokens_to_text(self._read_until_semicolon())
        if not text:
            return None
        return Simple(text)

    def parse_if(self) -> If:
        branches: List[Tuple[str, List[Node]]] = []
        self.next()  # 'if'
        cond = self._read_paren_text()
        body = self.parse_body()
        branches.append((cond, body))
        els: Optional[List[Node]] = None
        while self.peekv() == "else":
            self.next()  # 'else'
            if self.peekv() == "if":
                self.next()  # 'if'
                cond = self._read_paren_text()
                body = self.parse_body()
                branches.append((cond, body))
            else:
                els = self.parse_body()
                break
        return If(branches, els)

    def parse_while(self) -> While:
        self.next()  # 'while'
        cond = self._read_paren_text()
        body = self.parse_body()
        return While(cond, body)

    def parse_do(self) -> DoWhile:
        self.next()  # 'do'
        body = self.parse_body()
        if self.peekv() == "while":
            self.next()
            cond = self._read_paren_text()
        else:
            cond = ""
        if self.peekv() == ";":
            self.next()
        return DoWhile(cond, body)

    def parse_for(self) -> For:
        self.next()  # 'for'
        inner = self._read_group()  # '(' ... ')' の中身
        # ';' 2 個で 3 節に分割 (括弧の外側で)
        clauses: List[List[Token]] = [[], [], []]
        idx = 0
        depth = 0
        for tk in inner:
            if tk.val in "([{":
                depth += 1
            elif tk.val in ")]}":
                depth -= 1
            if tk.val == ";" and depth == 0 and idx < 2:
                idx += 1
                continue
            clauses[idx].append(tk)
        init = _tokens_to_text(clauses[0])
        cond = _tokens_to_text(clauses[1])
        incr = _tokens_to_text(clauses[2])
        body = self.parse_body()
        return For(init, cond, incr, body)

    def parse_switch(self) -> Switch:
        self.next()  # 'switch'
        expr = self._read_paren_text()
        cases: List[Tuple[List[str], List[Node]]] = []
        if self.peekv() == "{":
            self.next()  # '{'
            pending_labels: List[str] = []
            body: List[Node] = []
            have_case = False
            while self.pos < self.n and self.peekv() != "}":
                v = self.peekv()
                if v == "case":
                    if have_case and body:
                        cases.append((pending_labels, body))
                        pending_labels, body = [], []
                    self.next()  # 'case'
                    label_toks = []
                    while self.pos < self.n and self.peekv() not in (":", None):
                        label_toks.append(self.next())
                    if self.peekv() == ":":
                        self.next()
                    pending_labels.append(_tokens_to_text(label_toks))
                    have_case = True
                elif v == "default":
                    if have_case and body:
                        cases.append((pending_labels, body))
                        pending_labels, body = [], []
                    self.next()  # 'default'
                    if self.peekv() == ":":
                        self.next()
                    pending_labels.append("default")
                    have_case = True
                else:
                    node = self.parse_statement()
                    if node is not None:
                        body.append(node)
            if self.peekv() == "}":
                self.next()
            if have_case:
                cases.append((pending_labels, body))
        return Switch(expr, cases)


# --------------------------------------------------------------------------
# PlantUML アクティビティ図への変換
# --------------------------------------------------------------------------

def _match_brace_str(s: str, i: int) -> int:
    """文字列 s の位置 i の '{' に対応する '}' の位置を返す。"""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return j
    return len(s) - 1


def _split_top_commas(s: str) -> List[str]:
    """最上位 (括弧の外) のカンマで分割する。"""
    items: List[str] = []
    depth = 0
    cur: List[str] = []
    for c in s:
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        if c == "," and depth == 0:
            items.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    items.append("".join(cur))
    return items


def _format_braces(text: str, indent: str = "") -> str:
    """波括弧の初期化子を整形する。

    ネストした波括弧を含む '{...}' は要素ごとに改行・インデントし、
    末端 (内側に波括弧を持たない) の '{...}' は 1 行のまま保つ。

    例: a = {{X,Y},{X2,Y2}}
        ->
        a = {
            {X, Y},
            {X2, Y2}
        }
    """
    out: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            j = _match_brace_str(text, i)
            inner = text[i + 1:j]
            if "{" in inner:  # ネストあり -> 展開
                child = indent + "    "
                items = [_format_braces(it.strip(), child)
                         for it in _split_top_commas(inner) if it.strip()]
                body = ",\n".join(child + it for it in items)
                out.append("{\n" + body + "\n" + indent + "}")
            else:             # 末端 -> 1 行
                out.append("{" + inner.strip() + "}")
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


class Emitter:
    def __init__(self, max_len: int = 80):
        self.lines: List[str] = []
        self.max_len = max_len

    def _act(self, text: str) -> str:
        """式文を PlantUML アクティビティ用テキストへ整形・エスケープする。"""
        text = text.replace("\\", "\\\\")
        text = re.sub(r"\s+", " ", text).strip()
        if "{" in text:  # 波括弧の初期化子を複数行に整形
            text = _format_braces(text)
        if self.max_len:  # 1 行ずつ長さ制限 (複数行を壊さない)
            lines = []
            for ln in text.split("\n"):
                if len(ln) > self.max_len:
                    ln = ln[: self.max_len - 1].rstrip() + "…"
                lines.append(ln)
            text = "\n".join(lines)
        return text

    def _cond(self, text: str) -> str:
        text = re.sub(r"\s+", " ", text).strip()
        if self.max_len and len(text) > self.max_len:
            text = text[: self.max_len - 1].rstrip() + "…"
        if not text:
            text = "?"
        elif not text.endswith("?"):
            text = text + "?"
        return text

    def add(self, indent: int, line: str):
        self.lines.append("  " * indent + line)

    def emit_list(self, stmts: List[Node], indent: int, ctx: Optional[str]) -> bool:
        """文のリストを出力。途中で制御が抜ける(終端する)なら True。"""
        terminated = False
        for node in stmts:
            terminated = self.emit_node(node, indent, ctx)
            if terminated:
                break  # 以降はデッドコードなので出力しない
        return terminated

    def emit_node(self, node: Node, indent: int, ctx: Optional[str]) -> bool:
        if isinstance(node, Simple):
            self.add(indent, f":{self._act(node.text)};")
            return False

        if isinstance(node, Block):
            return self.emit_list(node.stmts, indent, ctx)

        if isinstance(node, Return):
            if node.text:
                self.add(indent, f":return {self._act(node.text)};")
            else:
                self.add(indent, ":return;")
            self.add(indent, "stop")
            return True

        if isinstance(node, Break):
            if ctx == "loop":
                self.add(indent, "break")
            # switch 内の break は図では暗黙 (出力しない)
            return True

        if isinstance(node, Continue):
            self.add(indent, ":continue;")
            return False

        if isinstance(node, Goto):
            # goto はジャンプなので、誤った順次矢印を避けるため detach で
            # 行き止まりにし、以降の同一ブロックは到達不能として扱う。
            self.add(indent, f":goto {self._act(node.label)};")
            self.add(indent, "detach")
            return True

        if isinstance(node, Label):
            self.add(indent, f":{node.name}:;")
            return False

        if isinstance(node, If):
            return self.emit_if(node, indent, ctx)

        if isinstance(node, While):
            self.add(indent, f"while ({self._cond(node.cond)}) is (yes)")
            self.emit_list(node.body, indent + 1, "loop")
            self.add(indent, "endwhile (no)")
            return False

        if isinstance(node, DoWhile):
            self.add(indent, "repeat")
            self.emit_list(node.body, indent + 1, "loop")
            self.add(indent, f"repeat while ({self._cond(node.cond)}) is (yes)")
            return False

        if isinstance(node, For):
            if node.init:
                self.add(indent, f":{self._act(node.init)};")
            cond = node.cond if node.cond else "true"
            self.add(indent, f"while ({self._cond(cond)}) is (yes)")
            self.emit_list(node.body, indent + 1, "loop")
            if node.incr:
                self.add(indent + 1, f":{self._act(node.incr)};")
            self.add(indent, "endwhile (no)")
            return False

        if isinstance(node, Switch):
            return self.emit_switch(node, indent, ctx)

        return False

    def emit_if(self, node: If, indent: int, ctx: Optional[str]) -> bool:
        first_cond, first_body = node.branches[0]
        self.add(indent, f"if ({self._cond(first_cond)}) then (yes)")
        term_flags = [self.emit_list(first_body, indent + 1, ctx)]
        for cond, body in node.branches[1:]:
            self.add(indent, f"elseif ({self._cond(cond)}) then (yes)")
            term_flags.append(self.emit_list(body, indent + 1, ctx))
        if node.els is not None:
            self.add(indent, "else (no)")
            term_flags.append(self.emit_list(node.els, indent + 1, ctx))
            all_terminated = all(term_flags)
        else:
            all_terminated = False  # else が無い = 素通りする経路がある
        self.add(indent, "endif")
        return all_terminated

    def emit_switch(self, node: Switch, indent: int, ctx: Optional[str]) -> bool:
        self.add(indent, f"switch ({self._cond(node.expr)})")
        for labels, body in node.cases:
            label_text = ", ".join(labels) if labels else "?"
            self.add(indent, f"case ({label_text})")
            self.emit_list(body, indent + 1, "switch")
        self.add(indent, "endswitch")
        return False


def function_to_puml(name: str, body_tokens: List[Token],
                     max_len: int = 80) -> str:
    parser = Parser(body_tokens)
    stmts: List[Node] = []
    while parser.pos < parser.n:
        node = parser.parse_statement()
        if node is not None:
            stmts.append(node)
        else:
            # 解析が進まない場合の保険 (構文外トークン)
            if parser.pos < parser.n and parser.peekv() not in (";", "}"):
                parser.next()

    em = Emitter(max_len=max_len)
    em.add(0, "@startuml")
    em.add(0, f"title {name}")
    em.add(0, "start")
    terminated = em.emit_list(stmts, 0, None)
    if not terminated:
        em.add(0, "stop")
    em.add(0, "@enduml")
    return "\n".join(em.lines) + "\n"


def source_to_puml(src: str, max_len: int = 80) -> Tuple[str, List[str]]:
    """ソース全体を PlantUML 文字列へ。(出力, 関数名リスト) を返す。"""
    tokens = tokenize(preprocess(src))
    funcs = find_functions(tokens)
    blocks = [function_to_puml(name, body, max_len) for name, body in funcs]
    names = [name for name, _ in funcs]
    return "\n".join(blocks), names


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _read_input(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _safe_name(name: str) -> str:
    """ファイル名に使えない文字を '_' へ置換する。"""
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) or "anon"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="C ソースを PlantUML アクティビティ図に変換する")
    ap.add_argument("inputs", nargs="+",
                    help="入力 C ファイル (- で標準入力)")
    ap.add_argument("-d", "--outdir",
                    help="出力先のベースディレクトリ "
                         "(既定: 各 C ファイルと同じ場所)")
    ap.add_argument("-o", "--output",
                    help="全入力をまとめて 1 ファイルに出力 (フォルダ分割しない)")
    ap.add_argument("--stdout", action="store_true",
                    help="標準出力へまとめて書き出す (フォルダ分割しない)")
    ap.add_argument("--max-len", type=int, default=80,
                    help="アクティビティ/条件テキストの最大長 (既定 80)")
    args = ap.parse_args(argv)

    combined: List[str] = []
    total_funcs = 0
    combine = bool(args.output or args.stdout)

    for path in args.inputs:
        try:
            src = _read_input(path)
        except OSError as e:
            print(f"[error] 読み込み失敗: {path}: {e}", file=sys.stderr)
            continue

        if combine:
            # まとめて 1 出力 (フォルダ分割しない)
            puml, names = source_to_puml(src, args.max_len)
            total_funcs += len(names)
            combined.append(puml)
            continue

        # 既定: C ファイルごとのフォルダ + 関数ごとの .puml
        if path == "-":
            stem, src_dir = "stdin", "."
        else:
            stem = os.path.splitext(os.path.basename(path))[0]
            src_dir = os.path.dirname(os.path.abspath(path))
        base = args.outdir if args.outdir else src_dir
        out_dir = os.path.join(base, _safe_name(stem))

        tokens = tokenize(preprocess(src))
        funcs = find_functions(tokens)
        if not funcs:
            print(f"[warn] 関数が見つかりません: {path}", file=sys.stderr)
            continue

        os.makedirs(out_dir, exist_ok=True)
        for name, body in funcs:
            puml = function_to_puml(name, body, args.max_len)
            out_path = os.path.join(out_dir, f"{_safe_name(name)}.puml")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(puml)
            total_funcs += 1
            print(f"[ok] {out_path}", file=sys.stderr)
        print(f"[done] {path} -> {out_dir}{os.sep}  ({len(funcs)} 関数)",
              file=sys.stderr)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write("\n".join(combined))
        print(f"[ok] {args.output}  ({total_funcs} 関数)", file=sys.stderr)
    elif args.stdout:
        sys.stdout.write("\n".join(combined))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
