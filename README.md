# Bidzaar MCP tools

[![MCP](https://img.shields.io/badge/Model%20Context%20Protocol-blue)](https://modelcontextprotocol.io)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

Простой stdio MCP-сервер для интеграции ИИ-агентов с торговой платформой **Bidzaar** (ЭТП).  
Позволяет создавать, управлять и анализировать закупочные процедуры, участников, документы, события, а также генерировать описания через AI.

> 📘 **Официальная документация API Bidzaar**  
> [https://phoenix.bidzaar.com/doc/connector/index.htm](https://phoenix.bidzaar.com/doc/connector/index.htm)

---

## 🚀 Основные возможности

- **Полный жизненный цикл процедур**  
  создание (черновик/публикация), получение, обновление, удаление черновика, публикация
- **Управление участниками**  
  приглашение (по ИНН/email), блокировка/разблокировка, список заблокированных, детальная информация с поиском
- **Файлы**  
  загрузка (локальный путь или base64), скачивание файла по ID
- **События и отчёты**  
  получение событий по фильтрам, генерация сравнительного отчёта (XLSX)
- **AI‑функции**  
  `improve_description` — улучшение описания процедуры через AI  
  `predict_and_apply_tags` — автоматическая генерация и применение тегов
- **Завершение процедур**  
  без победителей, возврат на этап оценки, завершение этапа приёма заявок
- **Работа с заявками участников**  
  запрос документов, просмотр заявок, смена цены
- **Информационные методы**  
  данные о компаниях, теги, сегменты, чаты, специальные условия

---

## 🛠 Список инструментов (tools)

> Реализовано в коде сервера. Ниже – ключевые группы.

### Процедуры
- `create_procedure` — создание процедуры (сразу публикуется или черновик)
- `get_procedure` — получение полных данных по UUID
- `update_procedure` — обновление параметров (даты, позиции, теги, контакты и т.д.)
- `delete_procedure_draft` — удаление черновика
- `publish_procedure` — публикация (с возможной отложенной датой)
- `complete_without_winners` — завершить без победителей
- `return_to_evaluation` — вернуть завершённую процедуру на оценку
- `finish_proposals_acceptance` — принудительно закрыть приём заявок

### Участники
- `get_participants` — список участников (сырой)
- `get_participants_with_details` — поиск с фильтром по имени/ИНН/email
- `invite_participants` — приглашение (массив объектов с tin/email/companyName)
- `block_participants` / `unblock_participants` — блокировка/разблокировка по имени, email, ИНН или UUID
- `get_blocked_participants` — список заблокированных
- `approve_participants` / `reject_participants` — одобрение/отклонение (для закрытых процедур)

### Файлы
- `upload_files` — загрузка (локальный путь `file_path` или base64)
- `get_file` — получение файла в base64

### События и отчёты
- `get_events` — с фильтрацией по дате, типу, процедуре, этапу
- `get_comparison_file` — запрос на генерацию XLSX-отчёта (возвращает taskId)
- `get_report_file` — скачать готовый отчёт по taskId

### AI
- `improve_description` — улучшить HTML-описание процедуры
- `predict_and_apply_tags` — предсказать теги для процедуры и применить их

### Дополнительные
- `get_companies_info` — информация по списку UUID компаний
- `get_tags` — поиск тегов с пагинацией
- `get_stages`, `get_stages_full_info` — информация об этапах процедуры
- `announce_new_stage` — создание нового этапа
- `set_winners` / `get_choices` — выбор победителей
- `request_documents` — запрос документов у участников
- `send_chat_message` — отправка сообщения в чат
- `add_additional_currency` / `update_additional_currencies`
- `cancel_delayed_publication`, `cancel_stage`
- и другие (полный список см. в `list_tools`)

---

## 📦 Установка

### Требования
- Python 3.10+
- Учётные данные Bidzaar (Client ID, Client Secret, email пользователя)

```bash
git clone https://github.com/lxxr/bidzaar_mcp_server.git
cd bidzaar_mcp_server
python -m venv venv
source venv/bin/activate   # или .\venv\Scripts\activate
pip install -r requirements.txt
```
### Переменные окружения
Создайте файл .env в корне проекта (или укажите путь в Settings_env):
```
ini
# Stage (тест https://phoenix.bidzaar.com) или production (https://bidzaar.com)
BIDZAAR_BASE_URL=https://phoenix.bidzaar.com 

# Учётные данные из личного кабинета Bidzaar
BIDZAAR_CLIENT_ID=your_client_id
BIDZAAR_CLIENT_SECRET=your_client_secret

# Email пользователя-организатора (должен существовать в системе)
BIDZAAR_USER_EMAIL=organizer@example.com

# Версия API (согласно документации – 5.4)
BIDZAAR_API_VERSION=5.4

# Путь для временного хранения загружаемых файлов (должен существовать)
BIDZAAR_FILES_BASE_PATH=/tmp/bidzaar_uploads
```
⚠️ Важно: Путь BIDZAAR_FILES_BASE_PATH используется при загрузке файлов через file_path. Сервер не создаёт его автоматически.

## 🔌 Конфигурация для ИИ-агентов
### 1. Claude Desktop
Добавьте в claude_desktop_config.json (macOS: ~/Library/Application Support/Claude/, Windows: %APPDATA%\Claude\):
```
json
{
  "mcpServers": {
    "bidzaar": {
      "command": "python",
      "args": ["/абсолютный/путь/к/bidzaar_mcp_server.py"],
      "env": {
        "BIDZAAR_BASE_URL": "https://phoenix.bidzaar.com",
        "BIDZAAR_CLIENT_ID": "your_client_id",
        "BIDZAAR_CLIENT_SECRET": "your_client_secret",
        "BIDZAAR_USER_EMAIL": "organizer@example.com",
        "BIDZAAR_API_VERSION": "5.3",
        "BIDZAAR_FILES_BASE_PATH": "/tmp/bidzaar_uploads"
      }
    }
  }
}
```
### 2. LangChain (с langchain-mcp-adapters)
```
python
from langchain_mcp_adapters.client import MultiServerMCPClient

MCP_SERVERS = {
    "bidzaar": {
        "command": "python",
        "args": ["/path/to/bidzaar_mcp_server.py"],
        "transport": "stdio",
        "env": {
            "BIDZAAR_CLIENT_ID": "...",
            "BIDZAAR_CLIENT_SECRET": "...",
            "BIDZAAR_USER_EMAIL": "...",
            "BIDZAAR_BASE_URL": "https://phoenix.bidzaar.com",
            "BIDZAAR_API_VERSION": "5.3",
            "BIDZAAR_FILES_BASE_PATH": "/tmp/bidzaar_uploads"
        }
    }
}

async def get_tools():
    client = MultiServerMCPClient(MCP_SERVERS)
    return await client.get_tools()
```
### 3. Nanobot
```
python
from nanobot import Nanobot
from mcp import StdioServerParameters

server_params = StdioServerParameters(
    command="python",
    args=["/path/to/bidzaar_mcp_server.py"],
    env={
        "BIDZAAR_CLIENT_ID": "...",
        "BIDZAAR_CLIENT_SECRET": "...",
        "BIDZAAR_USER_EMAIL": "...",
        "BIDZAAR_BASE_URL": "https://phoenix.bidzaar.com",
        "BIDZAAR_API_VERSION": "5.3",
        "BIDZAAR_FILES_BASE_PATH": "/tmp/bidzaar_uploads"
    }
)
bot = Nanobot()
bot.register_mcp_server("bidzaar", server_params)
```
### 4. Любой MCP-клиент
```
bash
python /path/to/bidzaar_mcp_server.py
```
## 💡 Примеры использования
### Создание процедуры (RFI – мониторинг рынка)
```
python
result = await agent.call_tool("create_procedure", {
    "name": "Поиск поставщиков ноутбуков",
    "type": 1,                     # закупка
    "trading_type": 8,             # мониторинг рынка (RFI)
    "description": "Необходимо оценить рынок ноутбуков...",
    "open_type": 0,                # открытая
    "acceptance_end_days": 10,
    "contacts": "Иван Иванов, +7...",
    "tags": ["IT", "ноутбуки"],
    "publish_immediately": True
})
```
### Приглашение участников по ИНН
```
python
await agent.call_tool("invite_participants", {
    "procedure_id": "550e8400-e29b-41d4-a716-446655440000",
    "invitations": [
        {"tin": "7707083893"},          # по ИНН
        {"email": "supplier@example.com", "companyName": "ООО Ромашка"}
    ]
})
```
### Блокировка участника по названию компании
```
python
await agent.call_tool("block_participants", {
    "procedure_id": "...",
    "participant_ids": ["ООО Ромашка"],   # или UUID / email
    "block_reason": "Не предоставил документы"
})
```
### Загрузка файла (локальный путь)
```
python
await agent.call_tool("upload_files", {
    "files": [{
        "file_path": "contract.pdf",
        "name": "Договор.pdf",
        "extension": "pdf"
    }]
})
```
### Улучшение описания через AI
```
python
improved = await agent.call_tool("improve_description", {
    "procedure_id": "...",
    "description": "нужны стулья офисные недорогие, 50 штук"
})
```
## 📁 Структура проекта
```
text
bidzaar_mcp_server/
├── bidzaar_mcp_server.py   # Основной сервер MCP (включает API клиент)
├── requirements.txt        # Зависимости (mcp, pydantic-settings, requests)
├── .env.example            # Шаблон переменных окружения
└── README.md
```
## 🧪 Особенности реализации

- **Транспорт** – stdio (совместим со всеми MCP-клиентами)
- **Авторизация** – OAuth2 `client_credentials`, автоматическое обновление токена (с буфером 60 сек)
- **Логирование** – выводится в `stderr` (чтобы не мешать протоколу MCP)
- **Файлы** – поддерживаются два способа передачи: `base64` или `file_path` (относительно `BIDZAAR_FILES_BASE_PATH`)
- **Обработка участников** – методы `block/unblock` умеют находить участников по названию, email, ИНН или UUID
- **Типы процедур**: `trading_type: 1` (RFP), `2` (RFQ), `4` (PCO), `8` (RFI)етоды block/unblock умеют находить участников по названию, email, ИНН или UUID

## 🐛 Устранение неполадок

### Ошибка: `File not found` при загрузке файла
Убедитесь, что:
1. Директория `BIDZAAR_FILES_BASE_PATH` существует
2. Файл находится внутри этой директории или указан полный путь
3. Есть права на чтение файла

### Ошибка: `401 Unauthorized`
Проверьте:
1. Корректность `BIDZAAR_CLIENT_ID` и `BIDZAAR_CLIENT_SECRET`
2. Активность учётной записи в системе Bidzaar
3. Правильность `BIDZAAR_BASE_URL`

### Ошибка: `ModuleNotFoundError`
Установите зависимости: `pip install -r requirements.txt`

## 📄 Лицензия и ссылки

**Лицензия:** MIT

**Полезные ссылки:**
- [Документация API Bidzaar (v5.3)](https://phoenix.bidzaar.com/doc/connector/index.htm)
- [Model Context Protocol](https://modelcontextprotocol.io)
- [LangChain MCP Adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
- [Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)
