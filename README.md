# zenmoney-mcp

Самостоятельный MCP-сервер для ZenMoney. Он переносит 28 инструментов `zenmoney-skill` с прежними именами и финансовым ядром. MCP вызывает Python-обработчики напрямую; старый CLI не поставляется. План и критерии — [MIGRATION_PLAN.md](MIGRATION_PLAN.md).

## Поведение

- Локально: `stdio`. Удалённо: защищённый Streamable HTTP `/mcp` за HTTPS.
- Каждый инструмент, которому нужны данные, получает свежий полный снимок через публичный `POST /v8/diff/` с `serverTimestamp: 0`. Между вызовами финансовые сущности не хранятся; `.cache.json` не читается и не записывается.
- Правила Plans, Analytics, периодов, валют, бюджетов и напоминаний сохранены. Запись проверяется повторной серверной загрузкой затронутых сущностей.
- Объёмные чтения по умолчанию используют `response_mode=compact`; `response_mode=full` возвращает исходный полный JSON. Сокращается только ответ модели, не расчёт и не запрос к ZenMoney. Ошибки всегда полные.
- Изменяющие инструменты требуют `confirm_write=true`; удалённо также нужны `finance:read` и `finance:write`.

## Установка и локальный запуск

Нужен Python 3.10+.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:ZENMONEY_STATE_DIR = Join-Path $env:LOCALAPPDATA 'zenmoney-mcp'
$env:ZENMONEY_TOKEN = '<your token>'
.\.venv\Scripts\python.exe -m zenmoney_mcp
```

`ZENMONEY_STATE_DIR` содержит приватный `config.json` с локальными настройками (`billing_period_start_day`, `plan_user_id`, `accounts_meta` и другие). По умолчанию MCP использует `%LOCALAPPDATA%\zenmoney-mcp` на Windows либо `$XDG_DATA_HOME/zenmoney-mcp` / `~/.local/share/zenmoney-mcp` в POSIX. Старый каталог skill можно временно указать как каталог настроек: MCP не использует его `.cache.json`. Токен ищется сначала в `ZENMONEY_TOKEN`, затем в `config.json` как совместимый резервный источник. Для Codex/Claude Desktop задайте команду интерпретатора из `.venv` и абсолютный путь к `run_stdio.py` аргументом. Этот запуск работает из любого каталога. Секреты передавайте только через environment или приватный каталог настроек.

## Удалённый вход для ChatGPT Web и Claude Web

Нужен публично достижимый HTTPS URL вида `https://finance.example/mcp` либо HTTPS reverse proxy к приватному серверу. Внутренний HTTP-процесс:

```powershell
.\.venv\Scripts\python.exe -m uvicorn zenmoney_mcp.remote:create_app --factory --host 127.0.0.1 --port 8000
```

| Переменная | Назначение |
|---|---|
| `ZENMONEY_TOKEN` | Токен ZenMoney только на сервере |
| `ZENMONEY_STATE_DIR` | Приватный каталог `config.json` |
| `ZENMONEY_MCP_RESOURCE` | Точный публичный HTTPS URL `/mcp`, он же JWT audience |
| `ZENMONEY_OAUTH_ISSUER` | HTTPS issuer внешнего OAuth-провайдера |
| `ZENMONEY_OAUTH_JWKS_URL` | JWKS endpoint провайдера |
| `ZENMONEY_OAUTH_SUBJECT` | Единственный разрешённый `sub` владельца профиля |

Remote-сервер является OAuth resource server, но не authorization server. Внешний провайдер должен поддерживать Authorization Code + PKCE S256, discovery, CIMD или DCR (либо заранее зарегистрированные клиенты), redirect URI для ChatGPT и Claude, refresh tokens и подписанные JWT access tokens с точными `iss`, `aud`, `sub` и scopes. Поддерживаются RS256/ES256. MCP публикует Protected Resource Metadata и возвращает `WWW-Authenticate` при отказе. Локальный `stdio` не использует OAuth. ZenMoney-токен не передаётся клиентам.

## Развёртывание на budget.theblackhaired.ru

Файлы в [deploy](deploy/) рассчитаны на Debian 13: Keycloak 26.7.4 с отдельным PostgreSQL в Docker Compose, MCP как systemd-служба от `kgorosov`, nginx и сертификат Let's Encrypt. Публичные адреса: `https://budget.theblackhaired.ru/mcp` и issuer `https://budget.theblackhaired.ru/auth/realms/budget`. Keycloak слушает только `127.0.0.1:8081`, MCP — только `127.0.0.1:8769`; PostgreSQL не публикует порт. Нужны DNS A-запись на сервер, доступные извне TCP 80/443 и установленные Docker Compose, nginx, certbot с его nginx/webroot поддержкой и Python 3.10+.

1. Разместите код в `/home/kgorosov/apps/zenmoney-mcp/current`, создайте `.venv` и установите `requirements.txt`. Секреты, `config.json` и снимки счетов в каталог кода не копируйте.
2. На сервере от имени `kgorosov` задайте `ZENMONEY_IDP_ENV_FILE=/home/kgorosov/apps/zenmoney-mcp/private/keycloak.env` и выполните `python3 deploy/generate_env.py`, затем `docker compose --env-file "$ZENMONEY_IDP_ENV_FILE" -f deploy/compose.yaml config --quiet` и `docker compose --env-file "$ZENMONEY_IDP_ENV_FILE" -f deploy/compose.yaml up -d`. Генератор создаёт файл один раз с правами `0600` и не показывает пароли. Храните его вне каталога релиза; резервную копию базы и учётных данных храните отдельно от исходников.
3. Подготовьте приватный каталог и параметры службы: `sudo install -d -m 0700 /etc/zenmoney-mcp`; `sudo install -m 0600 deploy/server.env.template /etc/zenmoney-mcp/server.env`; `sudo install -d -o kgorosov -g kgorosov -m 0700 /var/lib/zenmoney-mcp`; `sudoedit /etc/zenmoney-mcp/server.env`. Добавьте серверный `ZENMONEY_TOKEN` либо положите приватный `config.json` в `/var/lib/zenmoney-mcp` с владельцем `kgorosov` и правами `0600`. Установите unit командой `sudo install -m 0644 deploy/zenmoney-mcp.service /etc/systemd/system/zenmoney-mcp.service`, затем `sudo systemctl daemon-reload` и `sudo systemd-analyze verify zenmoney-mcp.service`. Запустите службу после создания пользователя Keycloak и задания его `sub`.
4. Для первой выдачи сертификата: `sudo install -d -m 0755 /var/www/letsencrypt/.well-known/acme-challenge`; `sudo install -m 0644 deploy/nginx.bootstrap.conf /etc/nginx/sites-available/budget.theblackhaired.ru`; `sudo ln -s /etc/nginx/sites-available/budget.theblackhaired.ru /etc/nginx/sites-enabled/budget.theblackhaired.ru`; проверьте `sudo nginx -t`, перезагрузите nginx. Затем выполните `sudo certbot certonly --webroot -w /var/www/letsencrypt -d budget.theblackhaired.ru`. После выдачи установите `deploy/nginx.https.conf` на то же место, снова выполните `sudo nginx -t` и `sudo systemctl reload nginx`. Установите hook `sudo install -m 0755 deploy/reload-nginx-on-renew.sh /etc/letsencrypt/renewal-hooks/deploy/reload-nginx-on-renew.sh` и проверьте продление через `sudo certbot renew --dry-run`. Не заменяйте действующий vhost, пока не проверены его текущие маршруты.
5. Подготовьте точные HTTPS redirect URI, показанные ChatGPT Web и Claude Web при добавлении удалённого MCP. Скрипт `deploy/bootstrap_realm.py` требует `CHATGPT_REDIRECT_URIS` и `CLAUDE_REDIRECT_URIS` как JSON-массивы без wildcard, `BUDGET_USERNAME`, начальный `BUDGET_PASSWORD` длиной не менее 16 символов, `CHATGPT_CLIENT_SECRET` и `CLAUDE_CLIENT_SECRET` длиной не менее 32 символов, а также пароль администратора Keycloak. На сервере можно загрузить переменные из приватных файлов без вывода в терминал: `. "$ZENMONEY_IDP_ENV_FILE"; . /home/kgorosov/apps/zenmoney-mcp/private/client-secrets.env; export KC_ADMIN_PASSWORD="$KEYCLOAK_ADMIN_PASSWORD" KC_ADMIN_USERNAME=admin`; задайте остальные переменные через безопасный ввод и запустите `python3 deploy/bootstrap_realm.py`. Скрипт создаёт realm `budget`, пользователя с обязательной сменой начального пароля, два конфиденциальных клиента `budget-chatgpt` и `budget-claude` с Authorization Code и PKCE S256, scopes `finance:read`/`finance:write` и mapper точного JWT `aud=https://budget.theblackhaired.ru/mcp`. Для DCR он разрешает только callback-домены `chatgpt.com`, `claude.ai`, `claude.com`, сохраняет проверку адресов клиента и ограничение числа регистраций. Его JSON-вывод содержит `owner_sub`: задайте его как `ZENMONEY_OAUTH_SUBJECT` в `/etc/zenmoney-mcp/server.env`, затем выполните `sudo systemctl enable --now zenmoney-mcp.service`. Уберите временные секреты из shell через `unset KC_ADMIN_PASSWORD KEYCLOAK_ADMIN_PASSWORD KEYCLOAK_DB_PASSWORD BUDGET_PASSWORD CHATGPT_CLIENT_SECRET CLAUDE_CLIENT_SECRET`. Keycloak не поддерживает OAuth `resource` из RFC 8707; для этого одного MCP используется фиксированный audience, а вход проверяется реальным подключением.
6. Claude Web при DCR может запросить только `finance:read` и `offline_access`, оставив `finance:write` вне списка необязательных прав созданного клиента. От имени `kgorosov` создайте `~/.config/systemd/user` с режимом `0700`, установите туда `deploy/zenmoney-mcp-claude-scopes.service` и `deploy/zenmoney-mcp-claude-scopes.timer` с режимом `0644`, затем выполните `loginctl enable-linger "$USER"`, `systemctl --user daemon-reload` и `systemctl --user enable --now zenmoney-mcp-claude-scopes.timer`. Таймер добавляет `finance:write` только как необязательный scope клиентам с точным callback Claude и уже назначенным `finance:read`; согласие пользователя по-прежнему требуется. Для немедленной сверки выполните `systemctl --user start zenmoney-mcp-claude-scopes.service` и проверьте `systemctl --user show -p Result zenmoney-mcp-claude-scopes.service`. После регистрации Claude проверьте `journalctl --user -u zenmoney-mcp-claude-scopes.service -n 20 --no-pager`: JSON последнего запуска должен содержать `"matched": 1` или больше.
7. Проверка без финансовой записи: `curl -i https://budget.theblackhaired.ru/.well-known/oauth-protected-resource/mcp` должен вернуть адрес MCP и issuer, `curl -i https://budget.theblackhaired.ru/mcp` без токена — `401` с `WWW-Authenticate`, а `https://budget.theblackhaired.ru/auth/realms/budget/.well-known/openid-configuration` — discovery с правильным issuer. Затем проверьте вход и только читающий инструмент в каждом веб-клиенте. Изменяющие инструменты требуют scope `finance:write` и `confirm_write=true`; не используйте их для проверки развёртывания.

## Проверки

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Тесты используют искусственные финансовые сущности и ключи OAuth, без реальных записей в ZenMoney. Источники: [MCP](https://modelcontextprotocol.io/specification/2026-07-28), [Python SDK v2](https://py.sdk.modelcontextprotocol.io/), [OpenAI OAuth](https://developers.openai.com/plugins/build/auth), [Claude remote MCP](https://claude.com/docs/connectors/building/authentication).
