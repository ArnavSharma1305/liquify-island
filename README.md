# Liquify Island

A minimalist, Dynamic Island-style desktop widget for Windows 11 built with Python and PySide6. It stays pinned cleanly at the top center of your screen as a compact pill and expands smoothly on hover to give quick access to system controls, media playback, focus timers, and workspace utilities.

## Features

* **Ambient Home Pill:** Displays live time, date, and a circular battery percentage gauge while staying out of your way.
* **Media & Lyrics Controls:** Integrates with Windows Media Session (`winsdk`) to show active track info, play/pause/skip controls, click-to-mute functionality, and synced/fetched lyrics.
* **Focus Timer:** Built-in Pomodoro / countdown timer right from the top bar.
* **Agent & System Watcher:** Monitors active background tasks, system status, and quick telemetry.
* **Quick Screen Capture:** Instant screen snipping and capture tools directly from the expanded island.
* **Native Windows Integration:** Uses `PySide6` and Windows Runtime APIs (`winsdk`) with translucent styling and hover-driven expansion.

## Installation & Usage

### Option 1: Run the Standalone `.exe` (Recommended)
1. Go to the [**Releases**](https://github.com/ArnavSharma1305/Liquify-Island/releases) page.
2. Download the latest `LiquifyIsland.exe`.
3. Double-click `LiquifyIsland.exe` to launch the widget.
   *(Optional)* Press `Win + R`, type `shell:startup`, and place a shortcut to `LiquifyIsland.exe` inside the folder to launch it automatically when Windows starts.

### Option 2: Run from Source
1. **Clone the repository:**
   ```bash
   git clone [https://github.com/ArnavSharma1305/Liquify-Island.git](https://github.com/ArnavSharma1305/Liquify-Island.git)
   cd Liquify-Island
   ```

2. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the application:**
   ```bash
   python liquify_island_3.py
   ```

## Building the `.exe` Yourself

If you modify the source code and want to package a fresh standalone executable:

```bash
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "LiquifyIsland" --collect-all winsdk liquify_island_3.py
```

The compiled executable will be generated inside the `dist/` folder.

## Tech Stack

* **Language:** Python 3.10+
* **UI Framework:** PySide6 (Qt for Python)
* **Windows APIs:** `winsdk` (Windows Runtime projections) & `ctypes`

## License

This project is open-source under the [MIT License](LICENSE).