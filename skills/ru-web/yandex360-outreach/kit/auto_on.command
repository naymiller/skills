#!/bin/bash
# Включить автозапуск через launchd (НЕ cron: cron не видит Связку ключей, все запуски падают «Пароль не найден»).
#  - отправка порции: пн–пт в 11:07 (команда --auto);
#  - проверка ответов: пн–пт каждый час в :45 с 9:45 до 18:45.
# Перед включением: первая волна уже отправлена вручную через send_today.command, и в config.json стоит
# "auto_send_approved": true — владелец ставит это сам. Без него отправка по расписанию не запускается.
# Мак должен быть включён и не спать: зарядка + «не переходить в сон», или sudo pmset repeat wakeorpoweron MTWRF 11:00:00
cd "$(dirname "$0")"; DIR="$(pwd)"; PY="$(command -v python3)"
if ! python3 -c "import json,sys; sys.exit(0 if json.load(open('config.json',encoding='utf-8')).get('auto_send_approved') else 1)"; then
  echo "auto_send_approved не true в config.json. Сначала ручная первая волна, потом владелец ставит true."
  read -p "Enter — закрыть"; exit 1
fi
mkdir -p ~/Library/LaunchAgents
mk() {  # $1 label, $2 аргумент скрипта, $3 часы через пробел, $4 минута
cat > ~/Library/LaunchAgents/$1.plist <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>$1</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$DIR/send_outreach.py</string><string>$2</string></array>
<key>WorkingDirectory</key><string>$DIR</string>
<key>StandardOutPath</key><string>$DIR/run.log</string><key>StandardErrorPath</key><string>$DIR/run.log</string>
<key>StartCalendarInterval</key><array>
$(for d in 1 2 3 4 5; do for h in $3; do echo "<dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>$h</integer><key>Minute</key><integer>$4</integer></dict>"; done; done)
</array></dict></plist>
PLIST
launchctl unload ~/Library/LaunchAgents/$1.plist 2>/dev/null; launchctl load ~/Library/LaunchAgents/$1.plist
}
mk ru.example.outreach.send --auto "11" 7
mk ru.example.outreach.check --check "9 10 11 12 13 14 15 16 17 18" 45
crontab -l 2>/dev/null | grep -v send_outreach | crontab - 2>/dev/null   # старые строки cron убрать
launchctl list | grep ru.example.outreach && echo "Автозапуск включён: отправка пн–пт 11:07, проверка ответов каждый час. Журнал: run.log. Выключить: auto_off.command"
read -p "Enter — закрыть"
