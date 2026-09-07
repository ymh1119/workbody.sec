"""信号与系统 AI 助教 · Streamlit 主入口（个性化版）。

变化要点：
1. 三个专家模式合并为一个 📡 信号与系统 AI 助教，AI 自动判断学生意图。
2. 侧边栏底部导航切到三页：💬 答疑对话 / 📊 我的学习（学生）/ 👨‍🏫 班级总览（老师）。
3. 学生每次提问按 FAISS 命中页号自动累加章节进度（progress_core）。
4. 老师角色可看全班进度表 + 章节热度统计。
"""
import streamlit as st
import datetime
from datetime import timezone, timedelta

import db_core
import rag_core
import session_core
import plot_core
import auth_core
import progress_core

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
                    st.session_state.user_role = auth_core.get_user_role(login_username)
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
                    st.success(msg)
                else:
                    st.error(f"❌ {msg}")

    st.stop()
else:
    st.sidebar.success(f"欢迎回来：{st.session_state.username}")
    if st.sidebar.button("退出登录", use_container_width=True):
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.user_role = ""
        st.session_state.current_user = None
        st.session_state.current_session_id = None
        st.session_state.current_page = "💬 答疑对话"
        st.rerun()

# 3. 已登录区：导航 + 子页 dispatcher
_sync_current_user()
if "current_page" not in st.session_state:
    st.session_state.current_page = "💬 答疑对话"

st.sidebar.markdown("---")

# 导航页（根据角色显示）
_pages = ["💬 答疑对话", "📊 我的学习"]
if st.session_state.get("user_role") == "teacher":
    _pages.append("👨‍🏫 班级总览")

# st.session_state.current_page 写回 radio 选择
try:
    _idx = _pages.index(st.session_state.current_page)
except ValueError:
    _idx = 0
st.session_state.current_page = st.sidebar.radio(
    "导航", _pages,
    index=_idx,
    key="current_page_radio",
)


# ============================================================
# 子页 1：💬 答疑对话
# ============================================================
def _page_chat(PDF_FILE_PATH):
    st.sidebar.markdown(f"### {rag_core.EXPERT_MODE}")
    st.sidebar.caption("答疑 · 出题 · 绘图，一站式 AI 助教")

    # 渲染左下角历史对话
    messages = session_core.render_sidebar_history()

    # 主区
    st.title(rag_core.EXPERT_MODE)
    st.caption("我会自动识别你的问题属于「答疑 / 出题 / 绘图」中的哪一类，给出最合适的回答。")

    # 渲染历史对话气泡
    for i, msg in enumerate(messages):
        with st.chat_message(msg["role"]):
            if msg.get("time"):
                st.caption(f"🕒 {msg['time']}")
            render_markdown_with_latex(msg["content"])

            if msg["role"] == "assistant":
                with st.expander("📋 一键复制全文"):
                    st.code(msg["content"], language="markdown")

            if msg["role"] == "assistant" and "```python" in msg["content"]:
                plot_core.render_interactive_plot(msg["content"], msg_index=f"history_{i}")

    # 接收输入
    if prompt := st.chat_input("💬 输入你的问题（答疑 / 出题 / 绘图均可）..."):
        bj_tz = timezone(timedelta(hours=8))
        current_time = datetime.datetime.now(bj_tz).strftime("%Y年%m月%d日 %H:%M:%S")

        history_for_chain = []
        for msg in messages:
            role = "assistant" if msg["role"] == "assistant" else "user"
            history_for_chain.append((role, msg["content"]))

        # 第一次提问时，把空壳会话（「默认对话」或「新对话 X」）改成问题摘要
        current_sid = st.session_state.current_session_id
        sessions_map = st.session_state.chat_sessions
        is_first_question = not sessions_map.get(current_sid, [])
        needs_rename = (
            is_first_question
            and current_sid
            and (current_sid == "默认对话" or current_sid.startswith("新对话"))
        )

        old_sid = None
        new_sid = None
        if needs_rename:
            old_sid = current_sid
            new_sid = prompt[:12] + ("…" if len(prompt) > 12 else "")

        with st.chat_message("user"):
            st.caption(f"🕒 {current_time}")
            render_markdown_with_latex(prompt)

        ai_reply = None
        source_docs = []
        with st.chat_message("assistant"):
            with st.spinner("🔍 正在检索《信号与系统》课本并生成精准解答..."):
                try:
                    api_key = st.secrets["API_KEY"]
                    ask_fn = rag_core.init_rag_system(
                        api_key=api_key,
                        expert_mode=rag_core.EXPERT_MODE,
                        pdf_name=PDF_FILE_PATH,
                    )
                    ai_reply, source_docs = ask_fn(prompt, history_for_chain)

                    st.caption(f"🕒 {current_time}")
                    render_markdown_with_latex(ai_reply)
                    with st.expander("📋 一键复制全文"):
                        st.code(ai_reply, language="markdown")

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

        if ai_reply is not None:
            # 写对话（用旧标题写入，方便后续按 id 范围精确重命名）
            log_sid = old_sid if needs_rename else current_sid
            logged_id = db_core.log_interaction(
                username=st.session_state.username,
                session_id=log_sid,
                query=prompt,
                response=ai_reply,
            )

            # 第一次提问后，精确重命名本次写入的会话
            if needs_rename and logged_id is not None:
                session_core.rename_session_in_memory(old_sid, new_sid)
                db_core.rename_session_in_db(
                    st.session_state.username, old_sid, new_sid, since_id=logged_id
                )

            session_core.append_messages([
                {"role": "user", "content": prompt, "time": current_time},
                {"role": "assistant", "content": ai_reply, "time": current_time},
            ])
            progress_core.record_progress(st.session_state.username, source_docs)

        if needs_rename:
            st.rerun()


# ============================================================
# 子页 2：📊 我的学习（学生视角）
# ============================================================
def _page_my_progress():
    st.title("📊 我的学习进度")
    username = st.session_state.username
    summary = progress_core.get_student_summary(username)

    # 顶部指标卡
    c1, c2, c3 = st.columns(3)
    c1.metric("累计提问", summary["total_asks"])
    c2.metric("已学章节", f"{summary['touched_chapters']} / {summary['total_chapters']}")
    coverage = (summary["touched_chapters"] / summary["total_chapters"] * 100) if summary["total_chapters"] else 0
    c3.metric("章节覆盖率", f"{coverage:.0f}%")

    st.markdown("---")

    # 章节进度条（按被问次数归一化）
    st.subheader("📚 各章节提问分布")
    max_count = max((c["ask_count"] for c in summary["progress"]), default=0)
    if max_count == 0:
        st.info("你还没在答疑对话中提过问题。快去 💬 答疑对话 开始学习吧！")
    else:
        for ch in summary["progress"]:
            with st.container():
                cols = st.columns([4, 1])
                cols[0].markdown(f"**{ch['title']}**（{ch['start_page']}–{ch['end_page']} 页）")
                cols[1].markdown(f"`{ch['ask_count']}` 次")
                cols[0].progress(ch["ask_count"] / max_count)

    st.markdown("---")

    # 薄弱章节 Top3
    st.subheader("🎯 推荐你尝试")
    weak = [c for c in summary["weak_chapters"] if c["ask_count"] == 0]
    if not weak:
        st.success("🎉 你已经覆盖了全部章节！可以复习一下最早学过的章节。")
    else:
        for ch in weak:
            st.write(f"· **{ch['title']}** （暂未提问，第 {ch['start_page']} 页起）")

    st.caption("💡 进度统计来自你在答疑对话中提问时被检索命中的章节。仅作参考。")


# ============================================================
# 子页 3：👨‍🏫 班级总览（老师视角）
# ============================================================
def _page_class_overview():
    st.title("👨‍🏫 班级总览")
    students = progress_core.get_class_overview()

    if not students:
        st.info("还没有学生开始提问。等学生有问答数据后这里会显示他们的进度。")
        return

    total_students = len(students)
    total_asks = sum(s["total_asks"] for s in students)

    c1, c2, c3 = st.columns(3)
    c1.metric("参与学生", total_students)
    c2.metric("累计提问", total_asks)
    c3.metric("人均提问", f"{total_asks / total_students:.1f}" if total_students else "0")

    st.markdown("---")

    # 全班进度表（用 markdown 表格，省掉 pandas 依赖）
    st.subheader("📋 全班进度表")
    table_rows = []
    for s in students:
        row = {
            "学生": s["username"],
            "总提问": s["total_asks"],
            "覆盖章节": s["touched_chapters"],
        }
        for ch in progress_core.get_chapters():
            short = ch["title"].replace("第", "Ch").replace("章", "").strip()
            row[short] = s["chapter_counts"].get(ch["id"], 0)
        table_rows.append(row)

    if table_rows:
        # markdown 表格渲染，自动横向滚动
        headers = list(table_rows[0].keys())
        md_lines = ["| " + " | ".join(headers) + " |",
                    "|" + "|".join(["---"] * len(headers)) + "|"]
        for r in table_rows:
            md_lines.append("| " + " | ".join(str(r.get(h, "")) for h in headers) + " |")
        st.markdown("\n".join(md_lines))

        # CSV 下载（备选）
        import io, csv
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=headers)
        writer.writeheader()
        for r in table_rows:
            writer.writerow(r)
        st.download_button(
            "📥 下载 CSV",
            data=buf.getvalue().encode("utf-8-sig"),
            file_name=f"class_progress_{datetime.date.today()}.csv",
            mime="text/csv",
        )

    st.markdown("---")

    # 章节热度
    st.subheader("🔥 章节热度（按触及学生数排序）")
    chapter_stats = progress_core.get_class_chapter_heatmap(students)
    chapter_stats_sorted = sorted(chapter_stats, key=lambda x: -x["student_count"])

    for ch in chapter_stats_sorted:
        pct = ch["student_count"] / total_students if total_students else 0
        cols = st.columns([4, 1, 1])
        cols[0].markdown(f"**{ch['title']}**")
        cols[1].markdown(f"`{ch['student_count']}/{total_students}` 学生")
        cols[2].markdown(f"共 `{ch['total_asks']}` 次")
        cols[0].progress(pct)

    st.caption(f"💡 数据基于 {total_students} 名学生的 {total_asks} 次提问统计。")


# ============================================================
# Dispatcher
# ============================================================
PDF_FILE_PATH = "textbook.pdf"

if st.session_state.current_page == "💬 答疑对话":
    _page_chat(PDF_FILE_PATH)
elif st.session_state.current_page == "📊 我的学习":
    _page_my_progress()
elif st.session_state.current_page == "👨‍🏫 班级总览":
    _page_class_overview()
