# pi-eink-endpoint

Raspberry Pi 3 に接続した Waveshare の電子ペーパーを HTTP API で更新する Python アプリです。
FastAPI + Uvicorn でテキストと画像のリクエストを受け付け、1 つのキューで順番に描画します。

## 動作環境

- Raspberry Pi 3
- Raspberry Pi OS Lite 64-bit（aarch64、以下の手順は Bookworm 以降を想定）
- Waveshare 2.9 インチ電子ペーパー V3（`epd2in9_V3`、128 × 296 ピクセル）と対応する接続基板
- Python 3.11 以降、FastAPI、Uvicorn、Pillow、gpiozero、spidev、GPIO バックエンド
- デプロイ元の PC: Bash、Git、SSH、`--mkpath` オプションに対応する rsync

このプロジェクトでは Pi 3 の aarch64 環境を前提とします。OS については
[Raspberry Pi 公式ドキュメント](https://www.raspberrypi.com/documentation/computers/os.html)を参照してください。
アプリは OS の `/usr/bin/python3` と `apt` でインストールしたパッケージを使います。

`scripts/setup_waveshare_eink.sh` で電子ペーパー用の実行環境をセットアップします。
[Waveshare 公式マニュアル](https://www.waveshare.com/wiki/2.9inch_e-Paper_Module_Manual#Python)の
SPI・Python 用の手順を、このアプリの構成に合わせて自動化しています。
依存パッケージは `apt` で導入します。リンク先の C デモ用ライブラリのビルドは、この Python アプリでは不要です。

## Pi 3 の初期設定

Raspberry Pi OS を用意し、ログインユーザー、ネットワーク、SSH 公開鍵を設定します。
以下の例ではホスト名を `pi3.local` としています。実際のホスト名または IP アドレスに置き換えてください。

### セットアップスクリプトの実行

デプロイ元 PC にこのリポジトリを取得し（[ソースの取得とデプロイ](#ソースの取得とデプロイ)を参照）、
Pi 3 にスクリプトを転送します。`pi` は実際のログインユーザー名に置き換えてください。
SSH 鍵を明示する場合は、`scp` と `ssh` に `-i /path/to/private-key` を追加します。

```bash
scp scripts/setup_waveshare_eink.sh pi@pi3.local:~/
ssh pi@pi3.local
```

Pi 3 上で、アプリを実行する一般ユーザーとして実行します。必要な操作には `sudo` が使われます。

```bash
bash ~/setup_waveshare_eink.sh
```

Pi 3 にリポジトリを clone 済みなら、ルートから `bash scripts/setup_waveshare_eink.sh` でも実行できます。
実行ユーザーを明示する場合は `sudo bash ~/setup_waveshare_eink.sh pi` のように指定します。
`sudo` 経由では元のユーザーを自動選択します。root で直接実行する場合は一般ユーザー名の指定が必要です。

スクリプトは次を実行します。

- FastAPI、Uvicorn、Pillow、NumPy、gpiozero、lgpio、RPi.GPIO、spidev と Python 3、デプロイ用の rsync を導入
- `raspi-config nonint do_spi 0` で SPI を有効化
- 指定ユーザーを `gpio` / `spi` グループに追加（既存の所属グループは保持）
- 指定ユーザーの `/usr/bin/python3` で依存パッケージの import を確認

途中で失敗した場合は停止します。原因を解消して同じコマンドを再実行できます。
Waveshare ドライバーは後述のサブモジュール取得・デプロイで配置します。
サービス登録と実際の画面更新は、後述の手順で確認してください。

完了後、Pi 3 を再起動します。スクリプトによる自動再起動は行いません。

```bash
sudo reboot
```

再接続後、SPI デバイスとグループへの所属を確認します。

```bash
ls -l /dev/spidev0.0
id
```

### 電子ペーパーの接続

Pi 3 の電源を切ってから接続します。現在の Waveshare ドライバーは SPI0 / CE0 と以下の
GPIO を使用します。GPIO 番号は BCM 番号です。HAT や接続基板の取り付け・電源配線は、
使用する基板のマニュアルに従ってください。

| 信号 | BCM GPIO |
| --- | --- |
| DIN / MOSI | 10 |
| CLK / SCLK | 11 |
| CS / CE0 | 8 |
| DC | 25 |
| RST | 17 |
| BUSY | 24 |
| PWR（ドライバー内の電源制御） | 18 |

設定はサブモジュール内の `RaspberryPi_JetsonNano/python/lib/waveshare_epd/epdconfig.py` にあります。
使用するパネルの型番・リビジョンが `epd2in9_V3` に対応することを確認してください。

## ソースの取得とデプロイ

以下はデプロイ元 PC で実行します。

```bash
git clone --recurse-submodules https://github.com/hapo31/pi-eink-endpoint.git
cd pi-eink-endpoint
```

取得済みのリポジトリでは、ルートディレクトリでサブモジュールを初期化します。

```bash
git submodule update --init --recursive
```

Waveshare ドライバーは `pi_eink_endpoint/waveshare_e_paper` に取得されます。
デプロイ時に Python ファイルをコピーするため、事前にサブモジュールの取得が必要です。

### 接続先の設定

```bash
cp .env.example .env
```

`.env` を編集して Pi 3 の接続情報を設定します。

| 変数 | 設定内容 |
| --- | --- |
| `RASPI_HOST` | Pi 3 のホスト名または IP アドレス |
| `RASPI_USER` | Pi 3 のログインユーザー名（サービスもこのユーザーで実行） |
| `RASPI_KEY_PATH` | デプロイ元 PC にある SSH 秘密鍵のパス |

`.env` は Git の管理対象外です。`deploy.sh` が Bash の `source` で読み込みます。
設定後、SSH で接続できることを確認します。

```bash
source .env
ssh -i "$RASPI_KEY_PATH" "$RASPI_USER@$RASPI_HOST"
```

### ファイルの転送

SSH 接続を終了し、デプロイ元 PC のリポジトリルートで実行します。
`deploy.sh` は実行開始時のディレクトリから `.env` を読むため、ルートで実行してください。

```bash
mkdir -p dist
bash scripts/deploy.sh
```

スクリプトはローカルの `dist/` を作り直し、Python ファイルと systemd ユニットを
Pi 3 の `/home/<user>/eink-endpoint/dist/` に転送します。転送先は `rsync --delete` で同期されるため、
このディレクトリはデプロイ専用にします。依存パッケージの導入やサービスの再起動は別途行います。

## サービスの登録と運用

既存の HTTP サーバーから移行する場合は、更新した `setup_waveshare_eink.sh` を Pi 3 で再実行し、
`python3-fastapi` と `python3-uvicorn` を導入してください。

デプロイ後、Pi 3 に `RASPI_USER` でログインして実行します。
ユニットはホームディレクトリが `/home/<user>` にある構成を前提としています。

```bash
cd "$HOME/eink-endpoint/dist"
sudo cp pi-eink-endpoint@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "pi-eink-endpoint@$USER.service"
```

状態とログの確認:

```bash
systemctl status "pi-eink-endpoint@$USER.service"
journalctl -u "pi-eink-endpoint@$USER.service" -f
```

アプリの再デプロイ後は、Pi 3 でサービスを再起動します。
ユニットファイルも変更した場合は、先に上記の `cp` と `daemon-reload` を再実行してください。

```bash
sudo systemctl restart "pi-eink-endpoint@$USER.service"
```

手動で動作を確認する場合は、同じディスプレイを操作するサービスを止めてから実行します。

```bash
sudo systemctl stop "pi-eink-endpoint@$USER.service"
cd "$HOME/eink-endpoint/dist"
/usr/bin/python3 -m uvicorn pi_eink_endpoint.main:app --host 0.0.0.0 --port 8000 --workers 1
```

確認後は `Ctrl+C` で終了し、`sudo systemctl start "pi-eink-endpoint@$USER.service"` でサービスを戻します。
ディスプレイ 1 台につきサーバープロセスは 1 つにしてください。Uvicorn は `--workers 1` で実行し、
実機では `--reload` を使わないでください。複数プロセスでは描画キューが分かれ、パネルに同時アクセスしてしまいます。

## API

サーバーは `0.0.0.0:8000` で待ち受けます。以下は Pi 3 に接続できる PC からの実行例です。
API ドキュメントは `http://pi3.local:8000/docs`、OpenAPI 定義は `/openapi.json` で確認できます。

### テキスト表示

```bash
curl -i http://pi3.local:8000/text \
  -H 'Content-Type: application/json' \
  --data '{"text":"Hello Pi 3"}'
```

JSON オブジェクトの `text` に文字列を指定します。省略時は `Hello E-ink` を表示します。
Pillow の既定フォントで白地に黒文字を描きます。

### 画像表示

```bash
curl -i http://pi3.local:8000/image \
  -H 'Content-Type: image/png' \
  --data-binary @image.png
```

Pillow が読み込める画像のバイト列を直接送信します（multipart 形式ではありません）。
画像は縦横比を保って横向きの 296 × 128 ピクセルに収め、余白を白で埋めて中央に配置し、
4 階調のグレースケールで表示します。

### 応答とキュー

`POST /text` と `POST /image` は、本文の受信とキューへの追加が完了すると、画面の更新を待たずに
HTTP `202 Accepted` と `{"message": "E-ink update queued"}` を返します。
不正な JSON は HTTP `400 Bad Request`、未定義の POST パスは HTTP `404 Not Found` です。

両エンドポイントはメモリ上の FIFO キューを共有し、1 つのワーカーが追加順に描画します。
更新中に届いたリクエストも順番に処理します。描画に失敗するとログに記録し、次のリクエストへ進みます。
`202` は受付の完了を示し、描画の成功を保証しません。画像の読み込みエラーも描画時にログへ記録されます。
描画ワーカーは FastAPI の lifespan で起動し、通常終了時は受付済みのキューを処理してから停止します。
強制終了や systemd の停止タイムアウトでは、メモリ上の未処理リクエストは失われます。

## Pi Zero からの移行

1. Pi 3 で `setup_waveshare_eink.sh` を実行して再起動します。
2. デプロイ元 PC の `.env` を Pi 3 のホスト名・ユーザー・SSH 鍵に更新します。
3. Pi 3 にデプロイし、サービスを登録します。
4. API を呼び出すクライアントの接続先を Pi 3 のポート `8000` に更新し、実際の画面表示とログを確認します。
5. 旧 Pi Zero のサービスが残っている場合は、旧端末で実行ユーザーとして
   `sudo systemctl disable --now "pi-eink-endpoint@$USER.service"` を実行します。

## トラブルシューティング

| 症状 | 確認すること |
| --- | --- |
| `No module named waveshare_epd` | サブモジュールを初期化してから再デプロイしたか |
| `fastapi` / `uvicorn` / `PIL` / `gpiozero` / `spidev` などの import エラー | Pi 3 に依存パッケージを導入し、`/usr/bin/python3` を使っているか |
| `/dev/spidev0.0` がない | SPI を有効にして再起動したか |
| GPIO / SPI の権限エラー | サービス実行ユーザーが `gpio` / `spi` グループに所属しているか |
| 接続できない | 接続先が Pi 3 のアドレスとポート `8000` になっているか、サービスが起動しているか |
| `202` なのに画面が更新されない | `journalctl` の描画エラー、パネルの型番、配線を確認する |
| デプロイ時の `--mkpath` エラー | デプロイ元の rsync がこのオプションに対応しているか |

## 開発時の確認

セットアップスクリプトのテストは OS 設定用コマンドを、キューのテストは GPIO ドライバーをモックするため、
Pi 3 や電子ペーパーがなくても実行できます。
開発用依存は `pyproject.toml` と `uv.lock` で管理します。uv を用意して、リポジトリルートから実行してください。
Pi の本番環境では引き続き apt のシステムパッケージを使います。
[Bookworm の FastAPI 0.92](https://packages.debian.org/bookworm/python3-fastapi)にも対応しています。

```bash
uv sync --locked
uv run --locked python -m unittest discover -s tests -v
```

このテストはセットアップのユーザー選択・失敗時の停止と、API の受付応答・処理順・エラー後の継続、
終了時のキュー処理、API ドキュメントを確認します。描画処理を止めた状態でも受付が完了することを検証します。
実際のパッケージ導入、SPI 通信、画面表示は Pi 3 で確認してください。
