#!/bin/bash
# Выключить автозапуск (launchd). Ручной режим send_today.command остаётся.
for L in ru.example.outreach.send ru.example.outreach.check; do
  launchctl unload ~/Library/LaunchAgents/$L.plist 2>/dev/null; rm -f ~/Library/LaunchAgents/$L.plist
done
echo "Автозапуск выключен. Ручной режим: send_today.command"
read -p "Enter — закрыть"
