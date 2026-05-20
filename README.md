# bidzaar_mcp_server

[![MCP](https://img.shields.io/badge/Model%20Context%20Protocol-blue)](https://modelcontextprotocol.io)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

MCP-сервер для интеграции ИИ-агентов с торговой платформой **Bidzaar** (ЭТП).  
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
