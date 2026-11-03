#!/bin/bash
# Один раз для каждого ящика: сохранить пароль приложения Яндекса в Связку ключей и отправить тестовые письма себе.
# Пароль вводится в Терминале (символы не видны), в файлы, логи и чат не попадает.
cd "$(dirname "$0")"
LIST="$(mktemp)"
python3 - <<'PY' > "$LIST"
import json
c = json.load(open('config.json', encoding='utf-8'))
print("\n".join(b["login"] for b in (c.get("mailboxes") or [{"login": c["login"]}])))
PY
SERVICE="$(python3 -c "import json;print(json.load(open('config.json',encoding='utf-8'))['keychain_service'])")"
while read -r LOGIN; do
  case "$LOGIN" in *@*) ;; *) continue;; esac
  if security find-generic-password -s "$SERVICE" -a "$LOGIN" -w >/dev/null 2>&1; then
    echo "Ящик $LOGIN: пароль уже сохранён, пропускаю."; continue
  fi
  echo "Ящик: $LOGIN"
  echo "Пароль приложения: войди в этот ящик → id.yandex.ru → Безопасность → Пароли приложений → Почта (IMAP, POP3, SMTP)."
  echo "Вставь его ниже и нажми Enter (символы не отображаются):"
  security add-generic-password -U -s "$SERVICE" -a "$LOGIN" -w
done < "$LIST"
rm -f "$LIST"
echo "Отправляю тестовые письма (каждый ящик пишет сам себе, касание 2 с КП)..."
python3 send_outreach.py --test
read -p "Проверь входящие (и папку Спам) на каждом ящике. Enter — закрыть"
