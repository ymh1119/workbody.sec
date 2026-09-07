"""信号与系统 AI 助教：数据库（Supabase）读写模块（统一版）。

变化：
- 历史数据不再按"专家模式"分层。三个专家合并后，session_id 就是顶层 key，
  跨模式同名 session 不会冲突。
- 老数据兼容：如果该行 expert_mode 字段还是旧的"🔍 深度答疑专家 ..."等，
  自动用前缀 `[🔍]` 等区分，避免新旧数据串台。
"""
import datetime
from datetime import timezone, timedelta
import streamlit as st
from supabase import create_client, Client

from rag_core import EXPERT_MODE  # 复用统一定义

# 老的三个专家名（用于兼容旧数据库记录，按此加前缀即可保留并区分）
_LEGACY_EXPERT_PREFIX = {
    "🔍 深度答疑专家 (讲解/解惑)": "🔍",
    "📝 测验与解析专家 (出题/批改)": "📝",
    "📊 仿真绘图专家 (波形/频谱)": "📊",
}


@st.cache_resource
def get_supabase() -> Client:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    return create_client(url, key)


def _now_str():
    bj_tz = timezone(timedelta(hours=8))
    return datetime.datetime.now(bj_tz).strftime("%Y年%m月%d日 %H:%M:%S")


def _normalize_session_id(expert_mode_value, session_id):
    """老数据按专家分层，所以同名 session 实际上不是同一个会话。给老数据加前缀避免串台。"""
    if not session_id:
        return "默认对话"
    if expert_mode_value != EXPERT_MODE and expert_mode_value in _LEGACY_EXPERT_PREFIX:
        prefix = _LEGACY_EXPERT_PREFIX[expert_mode_value]
        return f"[{prefix}] {session_id}"
    return session_id


def log_interaction(username, session_id, query, response):
    """把一次对话写入数据库。统一 EXPERT_MODE 字段，便于查询分析。"""
    try:
        supabase = get_supabase()
        supabase.table("chat_logs").insert({
            "timestamp": _now_str(),
            "username": username,
            "expert_mode": EXPERT_MODE,
            "session_id": session_id,
            "student_query": query,
            "ai_response": response,
        }).execute()
    except Exception as e:
        st.warning(f"⚠️ 写入云数据库失败（仅本次不持久化）: {e}")


def load_user_history(username):
    """
    读取某用户全部历史对话，返回扁平 dict：
        { session_id: [ {"role":..., "content":..., "time":...}, ... ] }

    老数据兼容：旧专家模式下的同名 session 会自动加前缀（如 `[🔍] 默认对话`），
    避免和新数据合并出错。
    """
    sessions = {}
    try:
        supabase = get_supabase()
        response = supabase.table("chat_logs").select("*") \
            .eq("username", username).order("id").execute()

        for row in response.data:
            expert_mode_value = row.get("expert_mode")
            raw_session_id = row.get("session_id", "默认对话")
            query = row.get("student_query", "")
            response_text = row.get("ai_response", "")
            timestamp_full = row.get("timestamp", "")
            time_str = timestamp_full.rsplit(":", 1)[0] if ":" in timestamp_full else timestamp_full

            session_id = _normalize_session_id(expert_mode_value, raw_session_id)

            sessions.setdefault(session_id, [])
            sessions[session_id].append({"role": "user", "content": query, "time": time_str})
            sessions[session_id].append({"role": "assistant", "content": response_text, "time": time_str})
    except Exception as e:
        st.sidebar.warning(f"⚠️ 读取云数据库历史失败: {e}")

    if not sessions:
        sessions["默认对话"] = []

    return sessions


def rename_session_in_db(username, old_title, new_title):
    """重命名数据库中的会话。注意：老数据按 expert_mode 分层，所以要精准更新。"""
    try:
        supabase = get_supabase()

        # 新数据：把当前统一 expert_mode 的同名 session 改名
        supabase.table("chat_logs").update({"session_id": new_title}) \
            .eq("username", username) \
            .eq("expert_mode", EXPERT_MODE) \
            .eq("session_id", old_title).execute()

        # 老数据：兼容带前缀的 session（比如 `[🔍] 默认对话`）。
        # 找到所有 session_id 等于 old_title 的行直接改名（兼容极早期未分层的数据）。
        supabase.table("chat_logs").update({"session_id": new_title}) \
            .eq("username", username) \
            .eq("session_id", old_title).execute()
    except Exception as e:
        st.warning(f"⚠️ 更新对话标题失败: {e}")
