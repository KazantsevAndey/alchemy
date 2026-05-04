# Alchemy

Аналитический дашборд для принятия управленческих решений при работе с Ozon, Wildberries и Яндекс Маркет.

## Возможности

- Расчёт маржи и прибыли по каждому SKU
- Учёт рекламных расходов (ДРР) из Performance/Promotion API
- Интеграция с тремя маркетплейсами через API
- AI-анализ и рекомендации (DeepSeek, GigaChat)
- Планирование поставок по складам
- Многопользовательский режим с шифрованием ключей

## Технологии

- Python 3.10+
- Streamlit
- pandas, Plotly
- SQLite + Fernet encryption
- DeepSeek API, GigaChat API

## Запуск

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Структура

```
alchemy/
├── app.py              # Точка входа
├── data_loader.py      # Загрузка данных из API
├── ozon_margin.py      # Расчёт маржи Ozon
├── wb_margin.py        # Расчёт маржи Wildberries
├── ym_margin.py        # Расчёт маржи Яндекс Маркет
├── auth.py             # Авторизация
├── db.py               # База данных
└── crypto.py           # Шифрование API-ключей
```

## Автор

Казанцев Андрей — НИУ ВШЭ, 2026

## Лицензия

Проект разработан в рамках ВКР. Все права защищены.
