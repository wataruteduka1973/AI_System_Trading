# 手元の mypy が CI と違うと、型エラーを見逃す

## 何が起きたか

`trade_history.py` の `dict` 内包表記で、キーが `UUID | None` になる型エラーを、手元の `.venv` の mypy
(2.3.1 / SQLAlchemy 2.0.52)は報告せず、CI の mypy(2.4.0 / SQLAlchemy 2.1.3)だけが報告した。
main の CI の `backend` ジョブが、そのまま数日間失敗していた。

## 原因

CI は `requirements-lock.txt` の固定バージョンで入れる(`pip install -e ".[dev]" -c requirements-lock.txt`)。
手元の環境が、ロックと違うバージョンのままだと、型の推論結果が変わる(SQLAlchemy 2.1 は、
`select()` の列の型を、nullable かどうかまで厳密に返す)。

## 避け方

- 型検査は、ロックの制約で入れた環境で実行する。

  ```bash
  python -m pip install -e ".[dev]" -c requirements-lock.txt
  python -m mypy
  python -m mypy --platform win32
  ```

- nullable な列(例: `LedgerEntry.fill_id`)を `dict` のキーなどに使うときは、`is not None` で絞る。
