# Windows・macOS・Linux で同じように動かす

Status: `[~]` 実装中(2026-11-08)。サーバー(Linux)へのデプロイの前提。

## 目的

開発環境が Windows のため、起動のしくみ(`start-local.bat`、対話キー、プロセス停止、自動起動)が
Windows 専用になっている。macOS の PC と、デプロイ先の Linux(EC2 など)でも、同じ手順で動かせるようにする。
アプリ本体(API・Worker・DB・フロントエンド)は、すでに OS に依存しない(CI は Linux で全テストを通している)。
依存が残るのは起動まわりと、ファイル権限だけ。

## 調査の結果(Windows 依存の箇所)

| 箇所 | Windows 専用の部分 | 対応 |
|---|---|---|
| `scripts/start_local.py` | 対話キー(`msvcrt`)。POSIX では「Windows が必要」と終了する | `termios`/`select` で、同じキー(R/A/Q)を読む。標準入力が端末でないときは、キー操作なしで動かす |
| 同上 | ポートの占有確認(`SO_EXCLUSIVEADDRUSE`)、ウィンドウタイトル、警告ダイアログ、プロセスグループ(`CREATE_NEW_PROCESS_GROUP`) | POSIX は `SO_REUSEADDR`(LISTEN 中のポートは拒否、TIME_WAIT は許す)、エスケープシーケンスのタイトルとベル、`start_new_session` |
| 同上 | Node.js の探索が `C:\Program Files\nodejs` だけ | OS ごとの慣例の場所(Homebrew など)も探す |
| `start-local.bat` | Python 環境を `.venv\Scripts\python.exe` で探す | 同じ探索をする `start-local.sh` を追加 |
| `scripts/windows/` | ログオン時の自動起動(タスクスケジューラ)と再起動ループ | 再起動ループの `scripts/unix/run_trading_worker.sh` を追加(systemd・launchd・tmux から呼ぶ) |
| `app/security/secret_store.py` | 暗号化した秘密ファイルが、umask 任せの権限(通常 0644)で作られる。Windows では ACL が既定で保護される | POSIX では、ディレクトリを 0700、ファイルを 0600 で作る |
| `README.md` | PowerShell のコマンドだけ | macOS / Linux の手順を併記 |

Worker の終了シグナル(`SIGTERM`/`SIGBREAK`)、DB・取引所・暗号化の各コードは、すでに両対応。

## 変えないこと

- アプリのロジック・API・DB スキーマ。
- 本番取引の制限(paper/testnet 限定)。
- Windows の起動方法(`start-local.bat` のダブルクリック)。

## 検証の方針

- 単体テスト: 起動スクリプトの POSIX 分岐を、Windows 上でも OS を差し替えて検査する。
- **CI で 3 つの OS を動かす**: 既存の Linux に加え、macOS と Windows で pytest(DB 不要分)とフロントエンドの
  lint・test・build を実行する(`cross-platform` ジョブ)。手元に macOS・Linux が無いので、これが実機での検証になる。
- 手元の Windows: 全テスト、ruff、mypy(`--platform win32` も)。

## 検証できないこと(記録)

- macOS・Linux での対話的な起動(キー入力、ブラウザの自動オープン)は、CI では確認できない
  (端末が無い)。利用者の Mac / 実際のサーバーで、初回に確認する。
- systemd・launchd への登録手順は、このリポジトリでは用意しない(サーバーの準備と一緒に行う)。
  再起動ループのスクリプトだけを、どちらからも呼べる形で置く。
