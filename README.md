# c2plantuml

C 言語ソースの **制御フロー** を解析し、関数ごとに [PlantUML アクティビティ図 (beta)](https://plantuml.com/ja/activity-diagram-beta) を書き出すツール。

- 外部依存なし（Python 標準ライブラリのみ）
- プリプロセス不要（マクロは展開せずそのまま図に出す）
- 1 関数 = 1 つの `.puml` ファイル（`@startuml … @enduml`）
- **出力は「C ファイル名のフォルダ」＋「関数名の .puml」**

## 出力構造

```
examples/sample.c  →  examples/sample/classify.puml
                      examples/sample/scan.puml
                      examples/sample/countdown.puml
                      examples/sample/retry_connect.puml
```

C ファイルごとに同名フォルダを作り、その中に関数ごとの `<関数名>.puml` を生成する。
`--outdir` でフォルダの出力先（ベースディレクトリ）を変更できる。
`-o` / `--stdout` を使うとフォルダ分割せず全関数を 1 つにまとめる。

## 必要環境

- Python 3.7 以上（標準ライブラリのみ。`pip install` 不要。GUI も標準の `tkinter` を使用）
- 生成した `.puml` を画像化したい場合のみ [PlantUML](https://plantuml.com/) 本体 + Java（手動で実行・任意）

## GUI

ファイル/フォルダ選択をマウス操作で行える tkinter 製 GUI を同梱:

```bash
python c2plantuml_gui.py
```

- **入力**: 「ファイル…」または「フォルダ…」で C ソースを選択（フォルダはサブフォルダ探索可）
- **出力先**: 空欄なら各 C ファイルと同じ場所に `<C名>/<関数名>.puml` を生成
- 変換は別スレッドで実行し、ログと進捗を表示。設定は次回起動時に復元（`~/.c2plantuml_gui.json`）

## 使い方

```bash
# foo.c の隣に foo/ フォルダを作り、関数ごとに <関数名>.puml を生成
python c2plantuml.py foo.c

# 複数ファイル（a/ と b/ フォルダにそれぞれ生成）
python c2plantuml.py a.c b.c

# 出力先のベースディレクトリを指定（diagrams/foo/<関数名>.puml）
python c2plantuml.py foo.c --outdir diagrams

# フォルダ分割せず、全関数を 1 ファイルにまとめる
python c2plantuml.py foo.c -o out.puml

# フォルダ分割せず標準出力へ
python c2plantuml.py foo.c --stdout

# 標準入力から（stdin/ フォルダに出力）
type foo.c | python c2plantuml.py -            # Windows
cat  foo.c | python c2plantuml.py - --stdout   # POSIX
```

### オプション

| オプション | 説明 | 既定 |
|---|---|---|
| （なし） | C ファイル名フォルダ＋関数名 `.puml` を生成 | これが既定 |
| `-d, --outdir DIR` | 出力先のベースディレクトリ | 各 C ファイルと同じ場所 |
| `-o, --output FILE` | 全関数をまとめて 1 ファイルへ（分割しない） | — |
| `--stdout` | 全関数をまとめて標準出力へ（分割しない） | — |
| `--max-len N` | アクティビティ／条件テキストの最大文字数（超過は `…` で省略） | 80 |

## .puml の画像化（任意・手動）

生成した `.puml` は、PlantUML 本体（`plantuml.jar` など）で手動で PNG/SVG にできる:

```bash
python c2plantuml.py examples/sample.c            # examples/sample/*.puml を生成
java -jar plantuml.jar examples/sample/*.puml      # 各 .puml を PNG 化
java -jar plantuml.jar -tsvg examples/sample/*.puml # SVG
```

## C 構文 → PlantUML 対応表

| C 構文 | PlantUML アクティビティ図 |
|---|---|
| 式文 `foo();` `x = 1;` | `:foo();` `:x = 1;` |
| `if / else if / else` | `if (…?) then (yes) / elseif (…?) / else (no) / endif` |
| `while (c) { … }` | `while (c?) is (yes) … endwhile (no)` |
| `do { … } while (c);` | `repeat … repeat while (c?) is (yes)` |
| `for (init; c; inc) { … }` | `:init;` + `while (c?)` …本体… `:inc;` + `endwhile (no)` |
| `switch / case / default` | `switch (e?) / case (…) / endswitch`（連続ラベルは `case (1, 2)`） |
| `return v;` | `:return v;` + `stop` |
| `break;`（ループ内） | `break` |
| `break;`（switch 内） | 図では暗黙（出力しない） |
| `continue;` | `:continue;` |
| `goto L;` / `L:` | `:goto L;` + `detach`（行き止まり）/ ラベルは `:L:;` |

## 解析方式と制限

- 制御フローに特化した **自作の軽量トークナイザ＋再帰下降パーサ**。完全な C パーサではない。
- マクロは展開しない。`#include` / `#define` 等のプリプロセッサ指令行は無視する。
- マクロが制御構造を隠している場合（例: `FOR_EACH(...) { … }`）は活動として扱う。
- K&R 形式の関数定義は対象外。
- `return` / `break` / `continue` / `goto` 以降の同一ブロック内の文は、到達不能コードとして出力しない。
- `goto` 文は `:goto x;` + `detach` で行き止まりにする（誤った順次矢印を引かない）。ラベルは通常のアクティビティ `:ラベル:;` として残る。ジャンプ先への実線は引かない（ラベル名で対応を示す）。

## ファイル構成

```
c2plantuml.py        変換エンジン（CLI 本体・単一スクリプト）
c2plantuml_gui.py    tkinter GUI（c2plantuml を利用）
examples/sample.c    基本サンプル C
examples/sample/     生成例（関数ごとの .puml）
examples/complex.c   複雑サンプル（5 重ループ・多数分岐・ネスト switch・goto）
examples/complex/    生成例
README.md
```