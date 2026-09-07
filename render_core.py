import re
import streamlit as st
import matplotlib.pyplot as plt

def format_latex(text):
    """暴力洗标：将括号格式替换为标准的美元符号格式"""
    return text.replace(r'\(', '$').replace(r'\)', '$').replace(r'\[', '$$').replace(r'\]', '$$')

def _sanitize_matplotlib_code(code_str):
    """清洗 AI 生成的 matplotlib 代码：移除已废弃参数，避免新版库报错"""
    code_str = code_str.replace("plt.show()", "")
    code_str = re.sub(r",\s*use_line_collection\s*=\s*(?:True|False)\s*", "", code_str)
    code_str = re.sub(r"\s*use_line_collection\s*=\s*(?:True|False)\s*,?\s*", "", code_str)
    code_str = re.sub(r"\(\s*,", "(", code_str)
    code_str = re.sub(r",\s*\)", ")", code_str)
    return code_str

def process_and_render_response(answer_content, is_drawing_mode):
    """处理回答，提取文本并执行画图代码"""
    fixed_content = format_latex(answer_content)
    
    if is_drawing_mode and ("```python" in fixed_content or "```Python" in fixed_content):
        # 剥离代码，渲染纯理论文字
        text_only_answer = re.sub(r'```python\s*\n.*?\n```', '', fixed_content, flags=re.DOTALL | re.IGNORECASE)
        st.markdown(text_only_answer)
        
        # 提取并执行代码
        code_match = re.search(r'```python\s*\n(.*?)\n```', fixed_content, flags=re.DOTALL | re.IGNORECASE)
        if code_match:
            st.divider()
            st.caption("📈 云端渲染引擎正在生成标准示意图...")
            original_code = code_match.group(1)
            
            # 清洗 AI 代码里的已废弃参数，再强行引入所有底层包
            clean_code = _sanitize_matplotlib_code(original_code)
            safe_code = "import numpy as np\nimport matplotlib.pyplot as plt\nimport scipy.signal as signal\n" + clean_code
            
            try:
                plt.close('all') 
                exec_env = {}
                exec(safe_code, exec_env, exec_env)
                st.pyplot(plt.gcf()) 
                plt.close('all') 
            except Exception as eval_e:
                st.error(f"图像渲染失败，底层代码报错：{eval_e}")
    else:
        # 普通模式直接显示洗标后的文本
        st.markdown(fixed_content)
