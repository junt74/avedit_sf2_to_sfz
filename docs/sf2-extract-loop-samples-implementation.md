# SF2 ループポイント付きサンプル抽出ツール 実装方針

## 目的

SoundFont 2.0 `.sf2` ファイルから、内部サンプル波形を個別の `.wav` として抽出する。

同時に、SF2内のサンプルヘッダ `shdr` に含まれる以下の情報を保持する。

* サンプル名
* サンプル開始位置
* サンプル終了位置
* ループ開始位置
* ループ終了位置
* サンプルレート
* ルートキー
* ピッチ補正
* リンク情報
* サンプル種別

出力は最低限、以下に対応する。

```text
output/
  samples/
    SampleName.wav
    SampleName.json
  extracted.sfz
```

`.wav` は通常のPCM WAVとして保存する。
ループ情報は `.json` と `.sfz` に確実に保存する。
余力があれば、WAVの `smpl` チャンクにもループ情報を書き込む。

## 非目的

初期実装では、SF2プリセット音色の完全再現は目指さない。

以下はスコープ外、または後続対応とする。

* プリセット単位の完全なキーマップ復元
* `pgen` / `igen` の全generator解釈
* エンベロープ、フィルター、LFO、モジュレーションの完全変換
* ステレオサンプルの完全な自動ペアリング
* ROM参照SoundFontへの対応
* 24bit拡張 `sm24` の完全対応

まずは **SF2内サンプルの抽出ツール**として成立させる。

---

# 実装対象

## CLI名

```bash
python sf2_extract_samples.py input.sf2 -o output
```

またはパッケージ化するなら、

```bash
python -m sf2_extract input.sf2 -o output
```

## CLIオプション案

```bash
python sf2_extract_samples.py input.sf2 \
  -o output \
  --write-json \
  --write-sfz \
  --write-smpl \
  --sanitize-names
```

### 必須

```text
input.sf2
-o / --output
```

### 任意

```text
--write-json      各WAVに対応するJSONメタデータを書き出す。デフォルトON。
--write-sfz       extracted.sfzを書き出す。デフォルトON。
--write-smpl      WAV内にsmplチャンクを書き込む。初期実装ではOFFでもよい。
--sanitize-names  ファイル名に使えない文字を置換する。デフォルトON。
--skip-empty      無音または長さ0のサンプルをスキップする。
--verbose         詳細ログを出す。
```

---

# SF2構造の最低限理解

SF2はRIFF形式である。

大枠は以下。

```text
RIFF "sfbk"
  LIST "INFO"
  LIST "sdta"
    smpl
    sm24 optional
  LIST "pdta"
    phdr
    pbag
    pmod
    pgen
    inst
    ibag
    imod
    igen
    shdr
```

今回の最低限の抽出に必要なのは、

```text
sdta/smpl : 16bit PCMサンプル波形本体
pdta/shdr : サンプルごとの位置・ループ・ピッチ情報
```

のみ。

---

# 重要チャンク

## `sdta/smpl`

SF2内のすべてのサンプルが、16bit little-endian PCMとして連結されている。

Pythonでは `struct.unpack` せず、バイト列として扱ってよい。

1サンプルあたり16bitなので、サンプルフレーム数からバイト位置に直すには、

```python
byte_start = sample_start * 2
byte_end = sample_end * 2
```

とする。

## `pdta/shdr`

サンプルヘッダ。1エントリ46バイト。

構造は以下。

```c
struct sfSample {
    char achSampleName[20];
    DWORD dwStart;
    DWORD dwEnd;
    DWORD dwStartloop;
    DWORD dwEndloop;
    DWORD dwSampleRate;
    BYTE byOriginalPitch;
    CHAR chPitchCorrection;
    WORD wSampleLink;
    WORD sfSampleType;
};
```

Python `struct` の形式案。

```python
SHDR_STRUCT = struct.Struct("<20sIIIIIBbHH")
```

各フィールド。

```text
name             20 bytes
start            uint32
end              uint32
start_loop       uint32
end_loop         uint32
sample_rate      uint32
original_pitch   uint8
pitch_correction int8
sample_link      uint16
sample_type      uint16
```

注意点として、`shdr` の最後には必ず `EOS` という終端レコードがある。
これはサンプルとして書き出さない。

---

# ループポイント変換

SF2の `start_loop` / `end_loop` は、`sdta/smpl` 全体に対する絶対サンプル位置である。

個別WAVに切り出す場合は、WAV内の相対位置へ変換する。

```python
relative_loop_start = start_loop - start
relative_loop_end = end_loop - start
```

WAV本体の長さは、

```python
length = end - start
```

ループが有効かどうかは、最低限以下で判定する。

```python
has_loop = (
    start <= start_loop < end and
    start < end_loop <= end and
    end_loop > start_loop
)
```

SF2の `end_loop` は実装・再生系によって解釈差が出やすいため、JSONには生値と相対値の両方を残す。

```json
{
  "loop_start": 1234,
  "loop_end": 5678,
  "loop_start_absolute": 99999,
  "loop_end_absolute": 104443
}
```

---

# 出力JSON仕様

各サンプルにつき、同名 `.json` を出す。

例：

```json
{
  "sample_name": "Organ_C4",
  "file": "samples/Organ_C4.wav",
  "sample_rate": 44100,
  "channels": 1,
  "bits_per_sample": 16,
  "length_samples": 32000,
  "root_key": 60,
  "pitch_correction_cents": 0,
  "has_loop": true,
  "loop_mode": "forward",
  "loop_start": 1200,
  "loop_end": 30000,
  "loop_start_absolute": 501200,
  "loop_end_absolute": 530000,
  "sf2_sample_type": 1,
  "sample_link": 0
}
```

`loop_mode` は初期実装では `"forward"` 固定でよい。
ループなしの場合は、

```json
"has_loop": false,
"loop_mode": "none",
"loop_start": null,
"loop_end": null
```

とする。

---

# SFZ出力仕様

最低限、1サンプルにつき1 `<region>` を出力する。

```sfz
<group>
loop_mode=loop_continuous

<region>
sample=samples/Organ_C4.wav
pitch_keycenter=60
loop_mode=loop_continuous
loop_start=1200
loop_end=30000
tune=0
```

ループなしの場合。

```sfz
<region>
sample=samples/NoiseHit.wav
pitch_keycenter=60
loop_mode=no_loop
tune=0
```

`pitch_keycenter` は `original_pitch` を使う。

`pitch_correction` は cents なので、SFZの `tune` に入れる。

```sfz
tune=-3
```

## 注意

この初期SFZは「サンプル一覧を鳴らせる」程度のSFZでよい。
SF2プリセットのキーレンジやベロシティレイヤーを再現するものではない。

---

# WAV書き出し

Python標準の `wave` モジュールを使う。

```python
with wave.open(path, "wb") as wav:
    wav.setnchannels(1)
    wav.setsampwidth(2)
    wav.setframerate(sample_rate)
    wav.writeframes(sample_bytes)
```

SF2の `smpl` は基本モノラルサンプルとして扱う。
ステレオ情報は `sample_type` と `sample_link` によって表現されるが、初期実装では個別モノラルWAVとして抽出する。

---

# WAV `smpl` チャンク対応

優先度は中。
初期実装では JSON/SFZ優先でよい。

対応する場合、Python標準 `wave` では任意チャンクを簡単に挿入しづらいため、以下のどちらかにする。

## 案A

まず `wave` で通常WAVを書き出し、その後にRIFFを再構築して `smpl` チャンクを追加する。

## 案B

WAVファイルを自前で書く。

WAV構造は以下。

```text
RIFF
  fmt 
  smpl optional
  data
```

`smpl` chunkにはループ情報を1つ書く。

ただし、読み込み側によって対応が分かれるため、`smpl` 埋め込みは補助扱いとする。

---

# ファイル名処理

SF2内のサンプル名は20byte固定で、NULL終端の可能性がある。

処理手順。

```python
name = raw_name.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
```

ファイル名に使えない文字は `_` に置換。

```text
\/:*?"<>|
```

同名サンプルがある場合は連番を付ける。

```text
Piano.wav
Piano_002.wav
Piano_003.wav
```

---

# 実装構成案

```text
sf2_extract/
  __init__.py
  cli.py
  riff.py
  sf2.py
  wav_writer.py
  sfz_writer.py
  names.py
tests/
  test_riff.py
  test_shdr_parse.py
  test_loop_conversion.py
  fixtures/
README.md
```

単一スクリプトで始める場合。

```text
sf2_extract_samples.py
README.md
```

Codexにはまず単一スクリプトで実装させ、後で分割してもよい。

---

# 主要クラス案

## `RiffChunk`

```python
@dataclass
class RiffChunk:
    chunk_id: bytes
    chunk_type: bytes | None
    data_start: int
    data_size: int
    children: list["RiffChunk"]
```

## `Sf2SampleHeader`

```python
@dataclass
class Sf2SampleHeader:
    name: str
    start: int
    end: int
    start_loop: int
    end_loop: int
    sample_rate: int
    original_pitch: int
    pitch_correction: int
    sample_link: int
    sample_type: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def has_valid_loop(self) -> bool:
        return (
            self.start <= self.start_loop < self.end and
            self.start < self.end_loop <= self.end and
            self.end_loop > self.start_loop
        )
```

## `ExtractedSample`

```python
@dataclass
class ExtractedSample:
    header: Sf2SampleHeader
    wav_path: Path
    relative_loop_start: int | None
    relative_loop_end: int | None
```

---

# RIFFパーサ方針

RIFFはチャンクごとに以下の構造。

```text
4 bytes chunk id
4 bytes size little-endian uint32
N bytes data
padding byte if size is odd
```

`RIFF` と `LIST` は、data先頭4byteに type を持つ。

```text
RIFF size type children...
LIST size type children...
```

必要なのは、以下のチャンクを探すこと。

```text
RIFF type sfbk
LIST type sdta -> smpl
LIST type pdta -> shdr
```

パーサは再帰で実装する。

注意点：

```python
next_offset = data_start + size
if size % 2 == 1:
    next_offset += 1
```

---

# エラー処理

以下は明確なエラーにする。

* ファイル先頭が `RIFF` ではない
* RIFF type が `sfbk` ではない
* `sdta/smpl` が存在しない
* `pdta/shdr` が存在しない
* `shdr` サイズが46の倍数でない
* sample rate が0
* `end <= start`
* `smpl` の範囲外を参照している

範囲外サンプルは全体停止ではなく、警告してスキップでもよい。

---

# テスト方針

## ユニットテスト

最低限、以下を作る。

```text
test_shdr_parse
test_loop_relative_conversion
test_duplicate_filename
test_invalid_loop_detection
test_riff_padding
```

## 実ファイルテスト

可能なら小さなSF2を `tests/fixtures` に置く。
ただし著作権・ライセンスに注意する。リポジトリに含める場合は、明示的に再配布可能な最小SF2だけにする。

実ファイルを置けない場合は、テスト内で最小RIFF/SF2風バイナリを生成する。

---

# 実装ステップ

## Step 1: RIFF探索だけ実装

* RIFFを読む
* `sdta/smpl` と `pdta/shdr` を見つける
* チャンクサイズを表示する

この時点でWAV書き出しは不要。

## Step 2: `shdr` パース

* 46byte単位で読む
* `EOS` を除外
* sample name、start、end、loop、sample_rateを表示する

## Step 3: WAV抽出

* `smpl` バイト列から `start * 2 : end * 2` を切り出す
* mono 16bit WAVとして保存
* ファイル名を安全化する

## Step 4: JSON出力

* 各WAVに対応する `.json` を出す
* 絶対ループ位置と相対ループ位置を両方保存する

## Step 5: SFZ出力

* `extracted.sfz` を出す
* loopあり/なしを反映する

## Step 6: 任意でWAV `smpl` チャンク

* 必要なら追加
* ただしJSON/SFZが正なので、WAV smplは補助

---

# Codexへの実装依頼プロンプト例

以下をCodexに渡す。

```text
PythonでSF2ファイルからサンプルを抽出するCLIツールを実装してください。

要件:
- 入力: .sf2
- 出力: samples/*.wav, samples/*.json, extracted.sfz
- SF2はRIFF/sfbkとしてパースする
- sdta/smpl から16bit PCMサンプル本体を取得する
- pdta/shdr からサンプルヘッダを取得する
- shdr構造は <20sIIIIIBbHH、1エントリ46byte
- shdr末尾の EOS は書き出さない
- ループポイントは start_loop - start, end_loop - start でWAV内相対位置に変換する
- WAVはmono 16bit PCMで書き出す
- JSONには絶対ループ位置と相対ループ位置を両方保存する
- SFZには sample, pitch_keycenter, tune, loop_mode, loop_start, loop_end を出す
- 初期実装ではプリセット/インストゥルメントの完全再現は不要
- 標準ライブラリ中心で実装する
- argparseでCLI化する
- ファイル名は安全化し、重複時は _002 のように連番を付ける
- エラー処理と最低限のpytestを追加する

まずは単一ファイル sf2_extract_samples.py と README.md を作成してください。
```

---

# 判断ポイント

このツールの初期版は、**「SoundFont音源を完全変換するツール」ではなく、「SF2からループ付きPCM素材を掘り出すツール」**として設計するのが安全です。

DTM運用では、抽出したWAVをそのまま使うより、

```text
SF2抽出
→ WAV + loop JSON / SFZ化
→ TAL-Sampler / Kontakt / DecentSampler / Ableton Samplerへ移植
→ RX950 / Decimort / TAL-Sampler DACで再サンプラー化
```

という流れが実用的です。特にPCMグルーヴや80sゲーム音源素材化の観点では、最初から完全再現を狙うより、**ループ付き素材ライブラリ化**をゴールにしたほうが扱いやすいです。
