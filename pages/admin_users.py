"""Admin page: user management."""

import streamlit as st
from db import list_users, create_user, delete_user, update_user_password
from auth import hash_password


def render():
    st.title("Управление пользователями")

    if not st.session_state.get("is_admin"):
        st.error("Доступ запрещён")
        return

    # List users
    users = list_users()
    st.subheader(f"Пользователи ({len(users)})")

    for u in users:
        role = "admin" if u["is_admin"] else "user"
        col1, col2, col3, col4 = st.columns([3, 2, 2, 1])
        with col1:
            st.text(f"{u['username']} ({role})")
        with col2:
            st.text(u.get("name", "") or "—")
        with col3:
            st.text(u.get("company", "") or "—")
        with col4:
            if u["id"] != st.session_state["user_id"]:
                if st.button("Удалить", key=f"del_{u['id']}"):
                    st.session_state[f"confirm_del_{u['id']}"] = True

        # Confirm delete
        if st.session_state.get(f"confirm_del_{u['id']}"):
            st.warning(f"Удалить пользователя **{u['username']}** и все его данные?")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("Да, удалить", key=f"yes_del_{u['id']}", type="primary"):
                    delete_user(u["id"])
                    st.session_state.pop(f"confirm_del_{u['id']}", None)
                    st.success(f"Пользователь {u['username']} удалён")
                    st.rerun()
            with c2:
                if st.button("Отмена", key=f"no_del_{u['id']}"):
                    st.session_state.pop(f"confirm_del_{u['id']}", None)
                    st.rerun()

    st.divider()

    # Create user
    st.subheader("Новый пользователь")
    with st.form("create_user"):
        username = st.text_input("Логин")
        password = st.text_input("Пароль", type="password")
        name = st.text_input("Имя")
        company = st.text_input("Компания")
        is_admin = st.checkbox("Администратор")
        submit = st.form_submit_button("Создать", type="primary")

    if submit:
        if not username or not password:
            st.error("Введите логин и пароль")
        elif len(password) < 4:
            st.error("Пароль минимум 4 символа")
        else:
            try:
                uid = create_user(
                    username=username.strip().lower(),
                    password_hash=hash_password(password),
                    name=name.strip(),
                    company=company.strip(),
                    is_admin=is_admin,
                )
                st.success(f"Создан пользователь: {username} (ID {uid})")
                st.rerun()
            except Exception as e:
                if "UNIQUE" in str(e):
                    st.error(f"Логин '{username}' уже занят")
                else:
                    st.error(f"Ошибка: {e}")
