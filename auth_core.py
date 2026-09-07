"""信号与系统 AI 助教 · 多用户注册/登录模块。

设计要点：
- 密码使用 PBKDF2-HMAC-SHA256 + 随机 salt 哈希存储，绝不明文落库。
- 用户名 3 字符以上、密码 6 字符以上、用户名全局唯一。
- 注册 / 登录失败原因详细回传给 UI（避免静默失败）。
- 所有数据库读写通过 db_core.get_supabase() 复用同一个连接。
"""
import hashlib
import secrets

import streamlit as st

import db_core


# ====== 密码哈希（标准库，不依赖 bcrypt 等第三方包） ======
_HASH_ALGO = "sha256"
_HASH_ITERATIONS = 120_000  # PBKDF2 迭代次数，抗暴力破解
_SALT_BYTES = 16


def _hash_password(password: str, salt: str | None = None):
    """对密码做 PBKDF2 哈希。返回 (hash_hex, salt_hex)。"""
    if salt is None:
        salt = secrets.token_hex(_SALT_BYTES)
    pwd_hash = hashlib.pbkdf2_hmac(
        _HASH_ALGO,
        password.encode("utf-8"),
        salt.encode("utf-8"),
        _HASH_ITERATIONS,
    ).hex()
    return pwd_hash, salt


# ====== 业务接口 ======
def register_user(username: str, password: str, password_confirm: str):
    """
    注册新用户。返回 (success: bool, message: str)。
    - success=True 表示注册成功；
    - success=False 表示注册失败，message 为原因。
    """
    username = (username or "").strip()
    password = (password or "").strip()
    password_confirm = (password_confirm or "").strip()

    # ====== 1. 前端校验 ======
    if len(username) < 3:
        return False, "用户名至少需要 3 个字符"
    if len(username) > 32:
        return False, "用户名不能超过 32 个字符"
    if len(password) < 6:
        return False, "密码至少需要 6 个字符"
    if len(password) > 64:
        return False, "密码不能超过 64 个字符"
    if password != password_confirm:
        return False, "两次输入的密码不一致"

    # ====== 2. 写数据库 ======
    try:
        sb = db_core.get_supabase()

        # 先查重：用户名唯一
        existing = sb.table("users").select("id").eq("username", username).execute()
        if existing.data:
            return False, "该用户名已被占用，请换一个"

        pwd_hash, salt = _hash_password(password)
        sb.table("users").insert({
            "username": username,
            "password_hash": pwd_hash,
            "password_salt": salt,
            "created_at": db_core._now_str(),
        }).execute()

        return True, f"账号「{username}」注册成功！请切换到登录标签登录。"
    except Exception as e:
        return False, f"注册失败：{e}"


def login_user(username: str, password: str):
    """
    登录校验。返回 (success: bool, message: str)。
    - success=True 时 message 为欢迎语；
    - success=False 时 message 为错误原因（不区分用户不存在 / 密码错误，
      防止被用于探测账号是否存在）。
    """
    username = (username or "").strip()
    password = (password or "").strip()

    if not username or not password:
        return False, "请输入用户名和密码"

    try:
        sb = db_core.get_supabase()
        result = sb.table("users").select("*").eq("username", username).execute()
        if not result.data:
            return False, "用户名或密码错误"

        user = result.data[0]
        pwd_hash, _ = _hash_password(password, user["password_salt"])
        if pwd_hash != user["password_hash"]:
            return False, "用户名或密码错误"

        return True, f"欢迎回来，{user['username']}！"
    except Exception as e:
        return False, f"登录失败：{e}"


def change_password(username: str, old_password: str, new_password: str):
    """改密码（备用，UI 上暂未挂出，按需启用）。"""
    ok, _ = login_user(username, old_password)
    if not ok:
        return False, "原密码错误"

    if len(new_password) < 6:
        return False, "新密码至少需要 6 个字符"

    try:
        sb = db_core.get_supabase()
        new_hash, new_salt = _hash_password(new_password)
        sb.table("users").update({
            "password_hash": new_hash,
            "password_salt": new_salt,
        }).eq("username", username).execute()
        return True, "密码修改成功"
    except Exception as e:
        return False, f"修改失败：{e}"
