# Liquify Island

A Dynamic Island-inspired floating desktop widget for Windows 11 built with Python and PySide6. It sits cleanly at the top center of your screen with real-time liquid glass refraction, dynamic album-art color theming, system media controls, a weekly calendar strip, and a live clock and battery ring gauge.

## Features

- **Liquid Glass Optics**: Captures the live desktop backdrop behind the notch (excluding itself via `SetWindowDisplayAffinity` to prevent feedback loops) and applies refractive edge banding, chromatic aberration, and a subtle aurora glow tinted to the current track's album art.
- **Media Controls & Elastic Seek Bar**: Connects to Windows Global System Media Transport Controls (`winsdk` / `winrt`) for play/pause, next/previous track, spring-physics timeline scrubbing, source app badges (Spotify, YouTube, Chrome, Edge, VLC, and more), and click-to-focus on the active media window.
- **Interactive Equalizer Mute Toggle**: Displays a live 5-bar equalizer while audio plays; clicking it toggles Windows system master mute directly via Core Audio COM (`IAudioEndpointVolume`).
- **Calendar & Battery Ring Gauge**: Displays a compact pill with the time, date, and battery ring at rest, and expands on hover to reveal a 7-day calendar strip alongside an animated circular clock and battery dial.
- **Window & Fullscreen Aware**: Automatically tucks out of the way when a maximized window occupies the top-center area (revealing when your cursor touches the top edge) and hides completely during fullscreen apps and games.

## Requirements

- Windows 11 (or Windows 10 version 2004 and later)
- Python 3.10 or newer

## Installation & Usage

1. Clone the repository and open a terminal inside the project directory:
   ```powershell
   git clone [https://github.com/ArnavSharma1305/liquify-island.git](https://github.com/ArnavSharma1305/liquify-island.git)
   cd liquify-island
   ```

2. Install the dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

3. Run the widget:
   ```powershell
   python liquify_island.py
   ```
   To run it without keeping a terminal window open, use `pythonw`:
   ```powershell
   pythonw liquify_island.py
   ```

### Optional Custom Fonts

Drop `.ttf` or `.otf` font files into a `fonts/` folder next to `liquify_island.py` to override the default display typography. Supported preferred families include **Space Grotesk**, **Sora**, **Outfit**, **Manrope**, **Plus Jakarta Sans**, and **Inter**.

## Building a Standalone `.exe`

Package the app into a single windowless executable using PyInstaller:

```powershell
pip install pyinstaller
pyinstaller --noconsole --onefile --name "LiquifyIsland" --collect-all winsdk liquify_island.py
```

The compiled executable will be created at `dist\LiquifyIsland.exe`. Place a shortcut to it inside `shell:startup` (`Win + R` -> `shell:startup`) to launch it automatically when you sign in.

## Controls

- **Hover top-center pill / top screen edge**: Expand the island.
- **Click album art or track title**: Bring the active media player window to the foreground.
- **Click equalizer bars**: Toggle Windows system mute.
- **Click gear icon**: Pin the island open (`Keep open`) or quit the app.

## License

MIT