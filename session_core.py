"""侧边栏历史对话 UI（统一版）。

目标：左下角以"按钮 + 摘要小字"形式展示历史会话，让用户一眼看到
"这个会话是什么 / 多长 / 最后什么时候活跃"，点击即可切换。

模块对外提供两个函数：
- init_session_state()   启动时调用一次：从 db 加载历史到 session_state
- render_sidebar_history()  渲染侧边栏左下角历史对话列表 + 返回当前会话消息
"""
import streamlit as st
from datetime import datetime, timezone, timedelta

import db_core
from rag_core import EXPERT_MODE


def init_session_state():
    """
    从数据库加载历史到 session_state。会在用户切换或重连时重新加载一次。
    老数据兼容由 db_core.load_user_history 内置处理。
    """
    if "current_user" not in st.session_state or not st.session_state.current_user:
        return

    current_user = st.session_state.current_user

    # 切换账号 / 重新登录时强制重新加载
    if st.session_state.get("loaded_user") != current_user:
        st.session_state.chat_sessions = db_core.load_user_history(current_user)
        st.session_state.loaded_user = current_user

    # 保底初始化
    if "chat_sessions" not in st.session_state:
        st.session_state.chat_sessions = {"默认对话": []}

    if "current_session_id" not in st.session_state:
        st.session_state.current_session_id = "默认对话"


def get_current_messages():
    """获取当前激活会话的消息列表（内存视图）。"""
    sid = st.session_state.get("current_session_id", "默认对话")
    return st.session_state.chat_sessions.setdefault(sid, [])


def append_messages(messages):
    """把一条问答追加到当前会话的内存视图，与 db 写入动作配合使用。"""
    sid = st.session_state.get("current_session_id", "默认对话")
    st.session_state.chat_sessions.setdefault(sid, []).extend(messages)


def rename_session_in_memory(old_sid, new_sid):
    """重命名内存里的会话（同时把消息搬过去）。"""
    if old_sid == new_sid or old_sid not in st.session_state.chat_sessions:
        return
    st.session_state.chat_sessions[new_sid] = st.session_state.chat_sessions.pop(old_sid)
    st.session_state.current_session_id = new_sid


def render_sidebar_history():
    """
    在 sidebar 底部渲染历史对话列表。
    每个会话展示为"标题 + 摘要 + 消息数 + 最后活跃时间"。
    返回当前会话的消息列表，便于主循环直接渲染。
    """
    init_session_state()

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📚 历史对话")

    # 新建按钮
    if st.sidebar.button("➕ 新建对话", use_container_width=True, key="btn_new_session"):
        bj_tz = timezone(timedelta(hours=8))
        new_id = f"新对话 {datetime.now(bj_tz).strftime('%m月%d日 %H:%M')}"
        st.session_state.chat_sessions.setdefault(new_id, [])
        st.session_state.current_session_id = new_id
        st.rerun()

    sessions = st.session_state.chat_sessions or {}

    if not sessions:
        st.sidebar.caption("📭 暂无历史，开始你的第一次提问吧~")
        return []

    # 按"会话最后活跃时间"倒序（最近的在最上面）
    def _last_time(sid):
        msgs = sessions.get(sid, [])
        return msgs[-1].get("time", "") if msgs else ""

    sorted_sids = sorted(
        sessions.keys(),
        key=lambda s: (_last_time(s), s),
        reverse=True,
    )

    # 空会话（默认占位 / 用户清空过）不展示在历史里，避免出现空壳子
    sorted_sids = [s for s in sorted_sids if sessions.get(s)]

    current_sid = st.session_state.get("current_session_id", "默认对话")

    # 前 3 条始终展开，第 4 条及之后折叠在 "📦 查看更多历史" 里
    VISIBLE_TOP_N = 3
    visible_sids = sorted_sids[:VISIBLE_TOP_N]
    hidden_sids = sorted_sids[VISIBLE_TOP_N:]

    def _render_card(sid):
        last_time = _last_time(sid) or "等待首次提问"

        is_active = sid == current_sid
        cursor = "👉" if is_active else "💬"

        # 顶部：会话按钮（短标题）
        short_title = sid if len(sid) <= 14 else (sid[:14] + "…")
        btn_label = f"{cursor} {short_title}"

        # 跟随调用处的容器上下文（sidebar 根部 或 expander 内部），
        # 不能写 st.sidebar.container()——那会把内容绕过 expander 漏到侧边栏根部
        with st.container():
            if st.button(
                btn_label,
                key=f"sessbtn_{sid}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state.current_session_id = sid
                st.rerun()

            # 只显示最后活跃时间
            st.caption(f"🕒 {last_time}")

    # 渲染前 N 条（显式进入 sidebar 上下文，_render_card 才能挂到侧边栏）
    with st.sidebar:
        for sid in visible_sids:
            _render_card(sid)

    # 其余折叠
    if hidden_sids:
        with st.sidebar.expander(f"📦 查看更多历史（还有 {len(hidden_sids)} 条）", expanded=False):
            for sid in hidden_sids:
                _render_card(sid)

    # 兜底：如果当前 sid 已不存在（极端情况下），跳到最近一个
    if current_sid not in sessions:
        st.session_state.current_session_id = sorted_sids[0]

    return sessions.get(current_sid, [])
