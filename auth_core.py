import streamlit as st

# ==========================================
# 🛡️ 本地硬编码白名单
# ==========================================
USER_CREDENTIALS = {
    "student01": "123456",
    "student02": "666888",
    "teacher": "123456"
}

def check_login():
    """拦截未登录用户"""
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "current_user" not in st.session_state:
        st.session_state.current_user = None

    if not st.session_state.authenticated:
        # 为了美观，未登录时在左侧栏和主页面同步提示
        st.info("👋 欢迎来到《信号与系统》智能教学平台，请先登录。")
        
        with st.form("login_form"):
            username = st.text_input("👤 账号").strip()
            password = st.text_input("🔑 密码", type="password").strip()
            submit_button = st.form_submit_button("登 录")
            
            if submit_button:
                if username in USER_CREDENTIALS and USER_CREDENTIALS[username] == password:
                    st.session_state.authenticated = True
                    st.session_state.current_user = username
                    st.success(f"登录成功！欢迎，{username}。正在进入系统...")
                    st.rerun() 
                else:
                    st.error("❌ 账号或密码错误！")
                    
        # 严格阻断未登录用户向下执行主程序的任何代码
        st.stop()
