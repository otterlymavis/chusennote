# chusennote iOS

This folder contains a lightweight SwiftUI iOS client for the local chusennote API.

## Run in Xcode

1. Open `ios/Chusennote.xcodeproj` in Xcode.
2. Run the Python server:

```bash
python lottery_monitor.py web --db chusennote.sqlite3 --port 8765
```

3. In the iOS simulator, use the default base URL `http://127.0.0.1:8765`.

For a physical device, set the base URL to your computer's LAN IP, for example `http://192.168.1.20:8765`, and make sure both devices are on the same network.
