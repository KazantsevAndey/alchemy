"""Settings page: API keys management."""

import streamlit as st
from db import get_credentials, save_credential, delete_credentials
from crypto import encrypt, decrypt


# Marketplace configs: (marketplace_db_key, display_name, fields)
MP_CONFIGS = [
    ("ozon", "Ozon", [
        ("OZON_SELLER_CLIENT_ID", "Client ID"),
        ("OZON_SELLER_API_KEY", "API Key (основной)"),
        ("OZON_SELLER_API_KEY_V2", "API Key (расширенный, FBO/остатки)"),
        ("OZON_PERF_CLIENT_ID", "Performance Client ID"),
        ("OZON_PERF_CLIENT_SECRET", "Performance Client Secret"),
    ]),
    ("wb", "Wildberries", [
        ("WB_API_KEY", "Токен статистики"),
    ]),
    ("ym", "Яндекс Маркет", [
        ("YM_API_KEY", "API Token"),
        ("YM_CAMPAIGN_ID", "Campaign ID"),
        ("YM_BUSINESS_ID", "Business ID"),
    ]),
    ("ai", "AI (аналитика)", [
        ("DEEPSEEK_API_KEY", "DeepSeek API Key"),
        ("GIGACHAT_CREDENTIALS", "GigaChat Credentials"),
    ]),
]


def _mask(value: str) -> str:
    if not value or len(value) < 8:
        return "****"
    return "****" + value[-4:]


def render(user_id: int):
    st.title("API-ключи")
    st.caption("Ключи хранятся в зашифрованном виде (Fernet)")

    for mp_key, mp_name, fields in MP_CONFIGS:
        with st.expander(f"**{mp_name}**", expanded=False):
            existing = get_credentials(user_id, mp_key)

            # Decrypt existing values for masking
            current = {}
            for key_name, _ in fields:
                enc = existing.get(key_name)
                if enc:
                    try:
                        current[key_name] = decrypt(enc)
                    except Exception:
                        current[key_name] = ""

            # Status
            configured = all(current.get(k) for k, _ in fields)
            if configured:
                st.success("Настроено")
            elif any(current.get(k) for k, _ in fields):
                st.warning("Частично настроено")
            else:
                st.info("Не настроено")

            with st.form(f"form_{mp_key}"):
                values = {}
                for key_name, label in fields:
                    cur = current.get(key_name, "")
                    placeholder = _mask(cur) if cur else ""
                    values[key_name] = st.text_input(
                        label,
                        value="",
                        placeholder=placeholder,
                        type="password",
                        key=f"api_{mp_key}_{key_name}",
                    )

                col1, col2 = st.columns(2)
                with col1:
                    save_btn = st.form_submit_button("Сохранить", type="primary")
                with col2:
                    clear_btn = st.form_submit_button("Очистить")

            if save_btn:
                saved = 0
                for key_name, _ in fields:
                    val = values[key_name].strip()
                    if val:
                        save_credential(user_id, mp_key, key_name, encrypt(val))
                        saved += 1
                if saved:
                    st.success(f"Сохранено {saved} ключ(ей)")
                    st.rerun()
                else:
                    st.info("Введите значения для сохранения")

            if clear_btn:
                delete_credentials(user_id, mp_key)
                st.success(f"Ключи {mp_name} удалены")
                st.rerun()

            # Test connection
            if configured and st.button(f"Проверить {mp_name}", key=f"test_{mp_key}"):
                _test_connection(mp_key, current)


def _test_connection(mp_key: str, creds: dict):
    """Make a lightweight test API call."""
    import requests

    try:
        if mp_key == "ozon":
            resp = requests.post(
                "https://api-seller.ozon.ru/v2/category/tree",
                headers={
                    "Client-Id": creds["OZON_SELLER_CLIENT_ID"],
                    "Api-Key": creds["OZON_SELLER_API_KEY"],
                },
                json={"category_id": 0, "language": "DEFAULT"},
                timeout=10,
            )
            if resp.status_code == 200:
                st.success("Ozon Seller API: подключение успешно")
            else:
                st.error(f"Ozon: ошибка {resp.status_code}")

        elif mp_key == "wb":
            resp = requests.get(
                "https://statistics-api.wildberries.ru/api/v1/supplier/stocks",
                headers={"Authorization": creds["WB_API_KEY"]},
                params={"dateFrom": "2026-01-01"},
                timeout=10,
            )
            if resp.status_code in (200, 204):
                st.success("WB Statistics API: подключение успешно")
            else:
                st.error(f"WB: ошибка {resp.status_code}")

        elif mp_key == "ym":
            cid = creds.get("YM_CAMPAIGN_ID", "")
            resp = requests.get(
                f"https://api.partner.market.yandex.ru/campaigns/{cid}",
                headers={"Api-Key": creds["YM_API_KEY"]},
                timeout=10,
            )
            if resp.status_code == 200:
                st.success("Яндекс Маркет API: подключение успешно")
            else:
                st.error(f"ЯМ: ошибка {resp.status_code}")

    except Exception as e:
        st.error(f"Ошибка подключения: {e}")
