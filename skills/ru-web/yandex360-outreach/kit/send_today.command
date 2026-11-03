#!/bin/bash
# Ручной режим, каждый рабочий день: проверить ответы, показать очередь и отправить только после слова «да».
cd "$(dirname "$0")"
python3 send_outreach.py --check
python3 send_outreach.py
read -p "Отправить эти письма? Напиши да и Enter: " a
if [ "$a" = "да" ]; then python3 send_outreach.py --send; else echo "Ничего не отправлено."; fi
python3 send_outreach.py --report
python3 crm_sync.py
read -p "Enter — закрыть"
