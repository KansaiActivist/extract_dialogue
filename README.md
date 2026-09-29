# extract_dialogue
音声の分離、字幕化スクリプト<br>
AIを使い動画からセリフを分離させたり字幕を生成できるスクリプトです。
# 準備
以下のものが必要です。<br>
・ffmpeg を入れて PATH を通す<br>
・Python 3.9 以上<br>
・精度重視のwhisperモードを使う場合だけ```pip install faster-whisper```
# 使い方（例）
```python extract_dialogue.py movie.mp4 　　
python extract_dialogue.py movie.mp4 --mode whisper --lang ja -f mp3 --name-with-text```

# オプション
## モード
```silence```（デフォルト）は ffmpeg の無音検出だけで区切ります。追加インストール不要で高速です。BGM が大きい作品では、--noise -30 のように無音のしきい値を上げて調整してください。<br>
```whisper```は faster-whisper で発話区間を検出し、文字起こしも取ります。BGM や効果音が多い動画ではこちらのほうが正確です。（字幕生成のみなので注意）<br>
## 調整オプション
```--min-silence```：何秒の無音で区切るか<br>
```--min-duration```：これより短い区間は捨てる<br>
```--pad```：前後に付ける余白<br>
```--merge-gap```：近い区間をつなげる<br>
# 出力
```dialogue_out/<動画名>/``` に ```動画名_001.wav、_002.wav …``` と連番で保存されます。<br>
形式は ```-f``` で ```wav / mp3 / flac / m4a``` から選べます。<br>
```segments.csv``` に開始・終了秒と（whisper モードでは）セリフのテキストが入ります。<br>
whisper モードでは字幕ファイル ```.srt``` も出ます。
