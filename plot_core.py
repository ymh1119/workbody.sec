import streamlit as st
import matplotlib.pyplot as plt
import numpy as np
import re
import uuid

def extract_code(ai_response):
    """从 AI 回答中提取 Python 代码"""
    code_match = re.search(r"```python(.*?)```", ai_response, re.DOTALL)
    return code_match.group(1).strip() if code_match else None

def _sanitize_matplotlib_code(code_str):
    """清洗 AI 生成的 matplotlib 代码：移除已废弃参数，避免新版库报错"""
    # 移除 plt.show()，避免在 Streamlit 里弹窗或阻塞
    code_str = code_str.replace("plt.show()", "")
    # 移除新版 matplotlib 已删除的 use_line_collection 参数
    # 先处理 "..., use_line_collection=True/False" 这种前面有逗号的情况
    code_str = re.sub(r",\s*use_line_collection\s*=\s*(?:True|False)\s*", "", code_str)
    # 再处理 "use_line_collection=True/False, " 或单独存在的情况
    code_str = re.sub(r"\s*use_line_collection\s*=\s*(?:True|False)\s*,?\s*", "", code_str)
    # 清理可能留下的空括号或多余逗号，例如 stem(a, ) -> stem(a)
    code_str = re.sub(r"\(\s*,", "(", code_str)
    code_str = re.sub(r",\s*\)", ")", code_str)
    return code_str

def execute_and_render(code_str):
    """底层安全渲染引擎"""
    try:
        # 清空旧画布，防止内存泄漏和图像重叠
        plt.clf() 
        plt.close('all')
        
        # 清洗 AI 代码里的已废弃参数
        safe_code = _sanitize_matplotlib_code(code_str)
        
        local_vars = {}
        # 强制注入 numpy 和 plt 兜底
        safe_globals = globals().copy()
        safe_globals['np'] = np
        safe_globals['plt'] = plt
        
        exec(safe_code, safe_globals, local_vars)
        
        fig = plt.gcf()
        if fig.get_axes(): 
            st.pyplot(fig)
            return True
    except Exception as e:
        st.error(f"❌ 图像渲染失败，底层报错: {e}")
        return False

def render_interactive_plot(ai_response, msg_index):
    """
    带微调工作台的交互式渲染组件
    msg_index 用于保证每个聊天气泡里的微调代码框都有唯一的 ID，防止冲突
    """
    code_str = extract_code(ai_response)
    if not code_str:
        return

    # 1. 首先尝试直接渲染 AI 给出的默认图像
    execute_and_render(code_str)

    # 2. 提供一个折叠的代码微调面板
    with st.expander("🛠️ 对这幅图不满意？展开手动微调"):
        st.caption("你可以直接修改下方的 Python 代码（例如改标题、线宽、颜色、坐标范围），然后点击应用。")
        
        # 使用 text_area 让用户可以编辑代码
        edited_code = st.text_area(
            "修改绘图代码", 
            value=code_str, 
            height=250, 
            key=f"editor_{msg_index}"
        )
        
        # 点击按钮后，使用修改后的代码重新渲染
        if st.button("🔄 应用修改并重新渲染", key=f"btn_{msg_index}"):
            st.info("☁️ 正在应用你的自定义修改...")
            execute_and_render(edited_code)
