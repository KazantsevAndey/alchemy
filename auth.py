"""
Authentication for Alchemy.

- bcrypt password hashing
- Streamlit session management
- Login page rendering
"""

import bcrypt
import streamlit as st
from datetime import datetime, timedelta

SESSION_TIMEOUT_HOURS = 8


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def is_authenticated() -> bool:
    """Check if user is logged in and session not expired."""
    if "user_id" not in st.session_state:
        return False
    login_time = st.session_state.get("login_time")
    if login_time and datetime.now() - login_time > timedelta(hours=SESSION_TIMEOUT_HOURS):
        logout()
        return False
    return True


def get_current_user_id() -> int | None:
    return st.session_state.get("user_id")


def logout():
    for key in ["user_id", "username", "user_name", "user_company",
                "is_admin", "login_time"]:
        st.session_state.pop(key, None)


def login_page():
    """Render centered login form."""
    try:
        st.set_page_config(page_title="Alchemy ⚗️", page_icon="⚗️", layout="centered")
    except st.errors.StreamlitAPIException:
        pass  # already set

    st.markdown("""
    <style>
    [data-testid="stSidebar"] { display: none; }
    .login-title { text-align: center; font-size: 2.5rem; margin-top: 2rem; }
    .login-sub { text-align: center; color: #888; margin-bottom: 2rem; }
    </style>
    """, unsafe_allow_html=True)

    st.markdown('<div class="login-title">Alchemy ⚗️</div>', unsafe_allow_html=True)
    st.markdown('<div class="login-sub">Аналитика маркетплейсов</div>',
                unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.5, 1])
    with col2:
        with st.form("login_form"):
            username = st.text_input("Логин")
            password = st.text_input("Пароль", type="password")
            submitted = st.form_submit_button("Войти", use_container_width=True,
                                               type="primary")

        if submitted:
            if not username or not password:
                st.error("Введите логин и пароль")
                return

            from db import get_user
            user = get_user(username.strip().lower())
            if user and check_password(password, user["password_hash"]):
                st.session_state["user_id"] = user["id"]
                st.session_state["username"] = user["username"]
                st.session_state["user_name"] = user["name"] or user["username"]
                st.session_state["user_company"] = user["company"] or ""
                st.session_state["is_admin"] = bool(user["is_admin"])
                st.session_state["login_time"] = datetime.now()
                st.rerun()
            else:
                st.error("Неверный логин или пароль")
