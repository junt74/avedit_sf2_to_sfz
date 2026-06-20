# SF2 tools

SoundFont 2（`.sf2`）からサンプルWAVを抽出し、簡易SFZまたはプリセット単位のSFZへ変換する、依存パッケージ不要のPython CLIツールです。Python 3.10以降で動作します。

変換元の `.sf2` は `soundfont/` に配置してください。このディレクトリ内の `.sf2` は `.gitignore` の対象で、Gitには追加されません。

## サンプル抽出

```powershell
python .\sf2_extract_samples.py .\soundfont\Proteus1.sf2 -o .\output\proteus1
```

SF2内の `sdta/smpl` 波形と `pdta/shdr` サンプルヘッダを読み、個別のモノラル16bit PCM WAVとして抽出します。

出力:

```text
output/
  proteus1/
    samples/
      SampleName.wav
      SampleName.json
    extracted.sfz
```

## オプション

```text
--write-json / --no-write-json  各WAVに対応するJSONを書き出す。デフォルトON。
--write-sfz / --no-write-sfz    extracted.sfzを書き出す。デフォルトON。
--write-smpl                    WAVにsmplチャンクを書き込む。デフォルトOFF。
--sanitize-names / --no-sanitize-names
                                ファイル名に使えない文字を置換する。デフォルトON。
--skip-empty                    長さ0または完全無音のサンプルをスキップする。
--verbose                       詳細ログを表示する。
```

JSONにはSF2上の絶対ループ位置と、切り出したWAV内の相対ループ位置の両方を保存します。`extracted.sfz`はサンプル一覧を鳴らすための最小構成で、プリセットやキーマップの完全再現は行いません。

## SF2からプリセットSFZへ変換

```powershell
python .\sf2_to_sfz.py .\soundfont\Proteus1.sf2 -o .\output\proteus1_sfz
```

出力:

```text
output/
  proteus1_sfz/
    samples/
      SampleName.wav
    presets/
      000_000_PresetName.sfz
      ...
```

`sf2_to_sfz.py` は `phdr/pbag/pgen/inst/ibag/igen/shdr` を読み、プリセットごとにSFZを書き出します。対応する主な変換は key/velocity range、sample参照、root key、tune、pan、volume、amp envelope、loop modeです。SF2のmodulatorやLFOなど、すべての音色挙動の完全再現は対象外です。

```powershell
# 特定プリセットだけ変換
python .\sf2_to_sfz.py .\soundfont\Proteus1.sf2 -o .\output\piano --preset 0:0

# サンプルヘッダに有効ループがあれば常にSFZへ書く
python .\sf2_to_sfz.py .\soundfont\Proteus1.sf2 -o .\output\proteus1_sfz --loop-policy sample
```

## テスト

```powershell
python -m unittest discover -s tests
python .\sf2_extract_samples.py .\soundfont\Proteus1.sf2 -o .\output\proteus1
python .\sf2_to_sfz.py .\soundfont\Proteus1.sf2 -o .\output\proteus1_sfz
```
