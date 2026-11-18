# RTSP setup: MediaMTX + FFmpeg

Ye setup original input `assets\traffic_demo.mp4` ko continuously loop karke local RTSP par publish karta hai:

```text
rtsp://localhost:8554/traffic
```

Input clip 13.23 seconds, H.264, 2160x3840, 30 FPS hai. OpenCV ke smooth playback ke liye outgoing stream ko 640x1138, 15 FPS H.264 mein transcode kiya jata hai.

## 1. One-time install

CMD ya PowerShell mein:

```text
winget install --id bluenviron.mediamtx --exact --accept-package-agreements --accept-source-agreements
winget install --id Gyan.FFmpeg --exact --accept-package-agreements --accept-source-agreements
```

Install ke baad existing terminal band karke naya CMD/PowerShell kholo, taaki updated PATH load ho. Verify:

```text
mediamtx --version
ffmpeg -version
```

Python environment project mein pehle se maujood hai. `test_rtsp.py` usi environment ka OpenCV use karta hai.

## 2. Project folder kholo

**CMD:**

```cmd
cd /d "C:\Users\91639\OneDrive\Desktop\projects\video-ingest-project"
```

**PowerShell:**

```powershell
Set-Location "C:\Users\91639\OneDrive\Desktop\projects\video-ingest-project"
```

Har terminal mein project folder tak jao. Teen terminals alag-alag chahiye; pehle do khule rakho.

## 3. Terminal 1: MediaMTX start karo

```text
mediamtx.exe mediamtx.yml
```

`RTSP started with listener on :8554` aana chahiye. `mediamtx.yml` RTSP/TCP ko port 8554 par enable karta hai.

## 4. Terminal 2: FFmpeg se looped stream publish karo

```text
ffmpeg -re -stream_loop -1 -i "assets\traffic_demo.mp4" -an -vf "fps=15,scale=640:-2" -c:v libx264 -preset ultrafast -tune zerolatency -crf 30 -pix_fmt yuv420p -f rtsp -rtsp_transport tcp "rtsp://localhost:8554/traffic"
```

`-stream_loop -1` clip ko unlimited repeat karta hai; `-re` real-time playback pace rakhta hai. Ye FFmpeg-to-MediaMTX publish pattern MediaMTX docs ke example par based hai: [Publish with FFmpeg](https://mediamtx.org/docs/publish/ffmpeg).

## 5. Terminal 3: OpenCV test chalao

```text
.venv\Scripts\python.exe test_rtsp.py
```

Test default 30 seconds chalta hai, yani source clip ke do se zyada cycles. Ye OpenCV/FFmpeg RTSP connection, received frames, stable frame dimensions, aur frame gaps check karta hai. Koi frame 8 seconds tak na aaye to test fail hota hai.

Expected output ka example (actual FPS machine load ke hisaab se badal sakta hai):

```text
PASS: RTSP connection opened with OpenCV FFmpeg backend.
PASS: First frame received; dimensions 640x1138.
PASS: Stream kept delivering frames for 30.0 seconds.
Frames received: ...
Frame dimensions: 640x1138
Source clip duration: 13.23 seconds; monitored about 2.3 clip durations.
```

## 6. Stop karna

FFmpeg aur MediaMTX terminals dono mein `Ctrl+C` dabao.

## Agar command recognize na ho

Install ke baad naya terminal zaroor kholo. Phir `where ffmpeg` ya `where mediamtx` run karke PATH check karo. Winget package executable paths is machine par:

**CMD fallback:** environment variables use `%NAME%` syntax.

```cmd
"%LOCALAPPDATA%\Microsoft\WinGet\Packages\bluenviron.mediamtx_Microsoft.Winget.Source_8wekyb3d8bbwe\mediamtx.exe" mediamtx.yml
"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe" -version
```

**PowerShell fallback:** environment variables use `$env:NAME` syntax.

```powershell
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\bluenviron.mediamtx_Microsoft.Winget.Source_8wekyb3d8bbwe\mediamtx.exe" mediamtx.yml
& "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe" -version
```

## Tested behavior

Original 4K stream-copy test connected, but MediaMTX logged `reader is too slow, discarding ... frames`, and OpenCV emitted H.264 decode warnings. The validated command above downsizes and rate-limits to 640x1138 at 15 FPS; in the final 30-second run OpenCV read frames continuously without those reader-too-slow or decoder warnings.

