# Деплой в Kubernetes

## Подготовка образа

1. Соберите и опубликуйте Docker‑образ бота (пример: `registry.example.com/dutyscdp-bot:latest`).
2. Убедитесь, что кластер имеет доступ к реестру образов (при необходимости добавьте `imagePullSecrets`).

## Настройка конфигурации

Helm chart формирует `config.toml` в виде `ConfigMap`, поэтому файл можно редактировать прямо в кластере.

- Базовое содержимое задаётся в `values.yaml` по ключу `configToml`.
- Путь монтирования файла задаётся параметром `configPath` (по умолчанию `/config/config.toml`).

### Секреты Loop

Токен Loop читается из `Secret` (по умолчанию `loop-token`, имя задаётся параметром `loopTokenSecretName`):

```bash
kubectl create secret generic loop-token \\
  --from-literal=token="<loop_token>"
```

При необходимости можно переопределить имя секрета в `values.yaml` через блок `env`.

## Установка chart'а

```bash
helm install dutyscdp charts/dutyscdp-bot \
  --set image.repository=registry.example.com/dutyscdp-bot \
  --set image.tag=latest \
  -f values.override.yaml
```

В `values.override.yaml` обычно указывают финальную версию `configToml` и дополнительные переменные окружения (`env`).

Базовый сервис публикует HTTP webhook на порту `8080` (можно изменить `service.port`).

## Обновление конфигурации в кластере

`config.toml` хранится в `ConfigMap` с именем `<release>-config`. Его можно менять двумя способами:

1. **Через Helm upgrade (рекомендуется):** обновите `configToml` в values-файле и выполните `helm upgrade dutyscdp charts/dutyscdp-bot -f values.override.yaml`. Pod перезапустится автоматически благодаря аннотации `checksum/config`.
2. **Прямое редактирование в кластере:**

   ```bash
   kubectl edit configmap dutyscdp-config
   kubectl rollout restart deployment/dutyscdp-dutyscdp-bot
   ```

   Этот способ подходит для быстрых правок без изменения Helm‑релиза.

## Параметры chart'а

- `image.repository` / `image.tag` — образ контейнера.
- `configToml` — содержимое файла `config.toml` (обязательно заполнить секцию `[loop]`, а при использовании Grafana OnCall добавить `[oncall]` с токеном/URL/расписанием).
- `loopTokenSecretName` — имя `Secret` с токеном Loop.
- `env` — дополнительные переменные окружения (например, альтернативные секреты).
- `service.port` — порт HTTP сервера бота.
- `extraArgs` — дополнительные аргументы командной строки для `python -m dutyscdp_bot.main`.
- `serviceMonitor.enabled` — создаёт `ServiceMonitor` для Prometheus Operator.
