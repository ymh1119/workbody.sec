"""信号与系统 AI 助教 · Streamlit 主入口（统一版）。

变化要点：
1. 三个专家模式合并为一个 📡 信号与系统 AI 助教，AI 自动判断学生意图。
2. 左下角历史会话直接展示每条对话的"摘要 + 消息数 + 最后活跃时间"。
3. 用 session_core.render_sidebar_history() 作为历史 UI 的唯一入口。
4. 其它历史 fix（embedding / 模型名 / 废弃参数 / 错误可视化）保持不变。
"""
import streamlit as st
import datetime
from datetime import timezone, timedelta

import db_core
import rag_core
import session_core
import plot_core
import auth_core

# 把当前用户同步到 session_core 使用的 key（兼容老 auth_core）
def _sync_current_user():
    username = st.session_state.get("username")
    if username:
        st.session_state.current_user = username

# 1. 页面全局设置
st.set_page_config(page_title="信号与系统 AI 助教", page_icon="📡", layout="wide")

st.markdown(
    """
<style>
.block-container {
    max-width: 900px !important;
    padding-top: 2rem;
    padding-left: 2rem;
    padding-right: 2rem;
}
/* 让侧边栏历史卡片在窄屏也漂亮 */
[data-testid="stSidebar"] .stButton > button {
    text-align: left;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
/* ===== 高亮底部提问输入框 ===== */
/* 输入框整体容器：加边框 + 圆角 + 阴影 */
[data-testid="stChatInput"],
.stChatInput {
    border: 2px solid #3399ff !important;
    border-radius: 14px !important;
    background-color: #f0f7ff !important;
    padding: 0.35rem 0.6rem !important;
    box-shadow: 0 2px 12px rgba(51, 153, 255, 0.30) !important;
}
/* 聚焦时更醒目 */
[data-testid="stChatInput"]:focus-within,
.stChatInput:focus-within {
    border-color: #0072e5 !important;
    box-shadow: 0 0 0 3px rgba(51, 153, 255, 0.25) !important;
}
/* 输入框内文字区域背景保持透明，避免双层底色 */
[data-testid="stChatInput"] textarea,
.stChatInput textarea {
    background-color: transparent !important;
}
/* 发送按钮配色统一 */
[data-testid="stChatInput"] button,
.stChatInput button {
    background-color: #3399ff !important;
    border-radius: 8px !important;
}
</style>
""",
    unsafe_allow_html=True,
)


def render_markdown_with_latex(text):
    if not isinstance(text, str):
        return
    text = text.replace("\\[", "$$").replace("\\]", "$$")
    text = text.replace("\\(", "$").replace("\\)", "$")
    st.markdown(text)


# 2. 登录认证（auth_core 提供注册/登录双 tab，密码 PBKDF2 哈希存 Supabase users 表）
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "username" not in st.session_state:
    st.session_state.username = ""

st.sidebar.title("🔐 用户登录")

if not st.session_state.logged_in:
    tab_login, tab_register = st.sidebar.tabs(["登录", "注册"])

    with tab_login:
        st.caption("已有账号？直接登录即可。")
        with st.form("login_form", clear_on_submit=False):
            login_username = st.text_input("👤 用户名", key="login_username").strip()
            login_password = st.text_input("🔑 密码", type="password", key="login_password")
            if st.form_submit_button("登 录", use_container_width=True):
                ok, msg = auth_core.login_user(login_username, login_password)
                if ok:
                    st.session_state.logged_in = True
                    st.session_state.username = login_username
                    st.success(msg)
                    st.rerun()
                else:
                    st.error(f"❌ {msg}")

    with tab_register:
        st.caption("首次使用？请创建一个新账号。")
        with st.form("register_form", clear_on_submit=True):
            reg_username = st.text_input("👤 设置用户名（≥3 字）", key="reg_username").strip()
            reg_password = st.text_input("🔑 设置密码（≥6 字）", type="password", key="reg_password")
            reg_password2 = st.text_input("🔑 再次输入密码", type="password", key="reg_password2")
            if st.form_submit_button("注 册", use_container_width=True):
                ok, msg = auth_core.register_user(reg_username, reg_password, reg_password2)
                if ok:
                    st.success(f"✅ {msg}")
                else:
                    st.error(f"❌ {msg}")

    st.stop()
else:
    st.sidebar.success(f"欢迎回来：{st.session_state.username}")
    if st.sidebar.button("退出登录", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.current_user = None
        st.session_state.current_session_id = None
        st.rerun()

_sync_current_user()

# 3. 统一助教标题（已替代原先的三选一 radio）
st.sidebar.markdown("---")
st.sidebar.markdown(f"### {rag_core.EXPERT_MODE}")
st.sidebar.caption("答疑 · 出题 · 绘图，一站式 AI 助教")

# 4. 渲染左下角历史对话（替代原 session_core 旧 API）
messages = session_core.render_sidebar_history()

# 5. 主界面标题
st.title(rag_core.EXPERT_MODE)
st.caption("我会自动识别你的问题属于\"答疑 / 出题 / 绘图\"中的哪一类，给出最合适的回答。")

# 6. 渲染历史对话气泡
for i, msg in enumerate(messages):
    with st.chat_message(msg["role"]):
        if msg.get("time"):
            st.caption(f"🕒 {msg['time']}")
        render_markdown_with_latex(msg["content"])

        if msg["role"] == "assistant":
            with st.expander("📋 一键复制全文"):
                st.code(msg["content"], language="markdown")

        # 如果该助手回答包含 ```python 代码块，尝试渲染图表（兼容历史记录）
        if msg["role"] == "assistant" and "```python" in msg["content"]:
            plot_core.render_interactive_plot(msg["content"], msg_index=f"history_{i}")

# 7. 接收输入并生成回答
PDF_FILE_PATH = "textbook.pdf"

if prompt := st.chat_input("💬 输入你的问题（答疑 / 出题 / 绘图均可）..."):
    bj_tz = timezone(timedelta(hours=8))
    current_time = datetime.datetime.now(bj_tz).strftime("%Y年%m月%d日 %H:%M:%S")

    # 准备对话历史
    history_for_chain = []
    for msg in messages:
        role = "assistant" if msg["role"] == "assistant" else "user"
        history_for_chain.append((role, msg["content"]))

    # 第一次提问时把"新对话 X"自动改名为用户问题摘要
    was_renamed = False
    if (st.session_state.current_session_id or "").startswith("新对话"):
        old_sid = st.session_state.current_session_id
        new_sid = prompt[:12] + ("…" if len(prompt) > 12 else "")
        session_core.rename_session_in_memory(old_sid, new_sid)
        db_core.rename_session_in_db(st.session_state.username, old_sid, new_sid)
        was_renamed = True

    # 渲染用户气泡
    with st.chat_message("user"):
        st.caption(f"🕒 {current_time}")
        render_markdown_with_latex(prompt)

    # 调用 RAG 链 + 渲染助手气泡
    ai_reply = None
    with st.chat_message("assistant"):
        with st.spinner("🔍 正在检索《信号与系统》课本并生成精准解答..."):
            try:
                api_key = st.secrets["API_KEY"]
                qa_chain = rag_core.init_rag_system(
                    api_key=api_key,
                    expert_mode=rag_core.EXPERT_MODE,
                    pdf_name=PDF_FILE_PATH,
                )
                response = qa_chain.invoke({
                    "query": prompt,
                    "chat_history": history_for_chain,
                })
                ai_reply = str(response)

                st.caption(f"🕒 {current_time}")
                render_markdown_with_latex(ai_reply)
                with st.expander("📋 一键复制全文"):
                    st.code(ai_reply, language="markdown")

                # 检测到代码块 → 自动进入图表渲染工作台
                if "```python" in ai_reply:
                    plot_core.render_interactive_plot(
                        ai_reply,
                        msg_index=f"new_{datetime.datetime.now().timestamp()}",
                    )
            except KeyError:
                st.error("🔑 发生错误：未能从系统配置 (Secrets) 中找到 API_KEY，请检查配置！")
            except Exception as e:
                import traceback
                st.error(f"❌ 系统发生异常: {e}")
                with st.expander("查看详细报错（定位问题用）"):
                    st.code(traceback.format_exc(), language="text")

    # 把这次对话同步写入 db + 内存视图
    if ai_reply is not None:
        db_core.log_interaction(
            username=st.session_state.username,
            session_id=st.session_state.current_session_id,
            query=prompt,
            response=ai_reply,
        )
        session_core.append_messages([
            {"role": "user", "content": prompt, "time": current_time},
            {"role": "assistant", "content": ai_reply, "time": current_time},
        ])

    if was_renamed:
        st.rerun()
