# Liquify Island

A Dynamic Island-inspired floating desktop utility for Windows 11 built with Python and PySide6. It sits cleanly at the top center of your screen with real-time liquid glass refraction, dynamic album-art color theming, system media controls, Bluetooth device cards, live weather, a weekly calendar strip, and a macOS-style screen capture & recording suite.

## Features

- **Liquid Glass Optics**: Captures the live desktop backdrop behind the notch (excluding itself via `SetWindowDisplayAffinity` to prevent feedback loops) and applies refractive edge banding, chromatic aberration, and a subtle aurora glow tinted to the current track's album art.
- **Screen Capture & Recording Suite**: Switch to the Capture tab to take PNG screenshots or H.264/AAC MP4 recordings (with automatic MJPEG AVI fallback) across three modes—**Entire Screen**, **Snap to Window**, or **Custom Region**—complete with live WASAPI system audio + microphone mixing and a compact live recording timer pill.
- **Media Controls & Elastic Seek Bar**: Connects to Windows Global System Media Transport Controls (`winsdk` / `winrt`) for play/pause, next/previous track, spring-physics timeline scrubbing, source app badges (Spotify, YouTube, Chrome, Edge, VLC, and more), and click-to-focus on the active media window.
- **Interactive Equalizer Mute Toggle**: Displays a live 5-bar equalizer while audio plays; clicking it toggles Windows system master mute directly via Core Audio COM (`IAudioEndpointVolume`).
- **Live Weather & Calendar Strip**: Automatically detects your location (Windows Location Services with IP geolocation fallback) to display real-time temperature and custom vector weather icons from Open-Meteo alongside a 7-day calendar strip and circular clock/battery dial.
- **Bluetooth Audio Device Cards**: Monitors paired wireless headphones, earbuds, and speakers, surfacing connection status and live battery percentages right inside the island.
- **Window & Fullscreen Aware**: Automatically tucks out of the way when a maximized window occupies the top-center area (revealing when your cursor touches the top edge) and hides completely during fullscreen apps and games.

## Requirements

- Windows 11 (or Windows 10 version 2004 and later)
- Python 3.10 or newer *(only required if running from source)*

## Quick Start (Standalone `.exe`)

1. Download **`LiquifyIsland.exe`** from the [Releases](https://github.com/ArnavSharma1305/liquify-island/releases) page.
2. Double-click `LiquifyIsland.exe` to launch the pill at the top center of your screen.
3. Hover the pill, click the **Gear icon**, and enable **Start with Windows** to launch automatically on sign-in.

## Installation & Usage (From Source)

1. Clone the repository and open a terminal inside the project directory:
   ```powershell
   git clone [https://github.com/ArnavSharma1305/liquify-island.git](https://github.com/ArnavSharma1305/liquify-island.git)
   cd liquify-island
   ```

2. Create a virtual environment and install dependencies:
   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Run the widget:
   ```powershell
   python liquify_island_2.py
   ```
   To run it without keeping a terminal window open, use `pythonw`:
   ```powershell
   pythonw liquify_island_2.py
   ```

### Optional Custom Fonts

Drop `.ttf` or `.otf` font files into a `fonts/` folder next to `liquify_island_2.py` to override the default display typography. Supported preferred families include **Space Grotesk**, **Sora**, **Outfit**, **Manrope**, **Plus Jakarta Sans**, and **Inter**.

## Building a Standalone `.exe`

Package the app into a single windowless executable using PyInstaller:

```powershell
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "LiquifyIsland" --collect-all winsdk liquify_island_2.py
```

The compiled executable will be created at `dist\LiquifyIsland.exe`. You can enable **Start with Windows** directly from the in-app settings menu (or system tray menu) without manually creating startup shortcuts.

## Controls

- **Hover top-center pill / top screen edge**: Expand the island.
- **Switch tabs (Island / Capture)**: Toggle between the main media/weather/calendar view and the screen capture & recording suite.
- **Click album art or track title**: Bring the active media player window to the foreground.
- **Click equalizer bars**: Toggle Windows system mute.
- **Click gear icon (or right-click tray icon)**: Pin the island open (`Keep open`), toggle `Start with Windows`, or quit the app.

## License

MIT