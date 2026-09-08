"""信号与系统 AI 助教核心 RAG 模块（统一版）。

变化：
- 三个专家（答疑 / 出题 / 绘图）合并为一个统一 AI 助教，让模型根据学生提问
  自动判断意图并切换对应能力，避免冗余切换逻辑。
- 保留 embedding provider 分离、LangChain 新 API、matplotlib 兼容性清洗等
  之前会话已确认的修复。
"""
import os
import json
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.output_parsers import StrOutputParser
from langchain_core.documents import Document
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from langchain_core.runnables import RunnablePassthrough
from langchain_text_splitters import RecursiveCharacterTextSplitter
import streamlit as st

# ============================================================
# 全局：统一的助教标签。新增功能、改提示词都围绕这一常量。
# ============================================================
EXPERT_MODE = "📡 信号与系统 AI 助教"


# ============================================================
# 向量库：缓存避免重复构建（数据来自离线 OCR 的 textbook_pages.json）
# ============================================================
# 教材是纯扫描版 PDF（无文字层），无法在线解析。因此先用本地 OCR
# （RapidOCR, dpi=200）把全书正文转成带页码的 JSON 数据文件，
# 部署时随仓库一起上传，网页直接读 JSON 建 FAISS 索引。
# 页码说明：printed_page 是书里印刷的页码（物理页码 - 23），
# AI 回答中引用的就是印刷页码，和纸质书一一对应。
@st.cache_resource
def get_vectorstore(data_path, api_key):
    """读取 OCR 文本数据并构建 FAISS 向量库（注入印刷页码）"""
    if not os.path.exists(data_path):
        st.warning(f"⚠️ 未找到课本数据文件 {data_path}，检索功能将降级为纯对话模式。")
        return None

    with open(data_path, encoding="utf-8") as f:
        data = json.load(f)

    offset = data.get("page_offset", 23)
    docs = []
    for p in data.get("pages", []):
        text = (p.get("text") or "").strip()
        if not text:
            continue
        printed = p.get("printed") or (p["page"] - offset)
        docs.append(Document(
            page_content=text,
            metadata={
                "page": p["page"],            # PDF 物理页码
                "printed_page": printed,      # 书上印刷的页码
                "page_label": f"第 {printed} 页",
            },
        ))

    if not docs:
        st.warning("⚠️ 课本数据文件为空，检索功能将降级为纯对话模式。")
        return None

    # DeepSeek 不提供 Embedding 接口，单独走第三方 provider。
    # 推荐配置：硅基流动 + BAAI/bge-large-zh-v1.5。
    embedding_api_key = st.secrets.get("EMBEDDING_API_KEY", api_key)
    embedding_base_url = st.secrets.get("EMBEDDING_BASE_URL", "https://api.siliconflow.cn/v1")
    embedding_model = st.secrets.get("EMBEDDING_MODEL", "BAAI/bge-large-zh-v1.5")

    embeddings = OpenAIEmbeddings(
        model=embedding_model,
        api_key=embedding_api_key,
        base_url=embedding_base_url,
        # 关键：非 OpenAI provider 必须关闭 tiktoken，否则返回 400 错误 20015
        # （另一个触发 20015 的原因见下方切片注释：文本超过 512 tokens）
        check_embedding_ctx_length=False,
        encoding_format="float",
    )

    # bge-large-zh-v1.5 最大输入 512 tokens（约 400 汉字）。整页 OCR 文本
    # 平均 884 字、最长 3184 字，96% 的页超限，直接 embedding 会被
    # SiliconFlow 拒绝（400 code 20015）。必须先切片，chunk 保留原页码
    # 元数据，页码引用与章节统计不受影响。
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=400,
        chunk_overlap=50,
        separators=["\n", "。", "，", "；", " ", ""],
    )
    docs = splitter.split_documents(docs)

    # LangChain 默认 chunk_size=1000 会把上千段拼成一个巨型请求
    # （约 40 万 tokens），硅基流动处理不过来导致请求挂起、网页长时间转圈。
    # 改为小批量分批向量化 + 进度条 + 失败重试。
    texts = [d.page_content for d in docs]
    metas = [d.metadata for d in docs]

    all_vecs = []
    batch_size = 32
    prog = st.progress(0.0, text="正在构建课本索引（首次约 1 分钟，之后走缓存秒开）…")
    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i:i + batch_size]
        for attempt in range(3):
            try:
                all_vecs.extend(embeddings.embed_documents(batch_texts))
                break
            except Exception:
                if attempt == 2:
                    prog.empty()
                    st.warning("⚠️ 课本索引构建失败（向量化接口异常），本次降级为纯对话模式。")
                    return None
        done = min(i + batch_size, len(texts))
        prog.progress(done / len(texts), text=f"正在构建课本索引 {done}/{len(texts)} 段…")
    prog.empty()

    vectorstore = FAISS.from_embeddings(
        text_embeddings=list(zip(texts, all_vecs)),
        embedding=embeddings,
        metadatas=metas,
    )
    return vectorstore


# ============================================================
# 系统提示词：答疑 / 出题 / 绘图 三合一
# ============================================================
EXPERT_PROMPTS = {
    EXPERT_MODE: f"""你是《信号与系统》课程的 AI 助教。面对学生的提问，请先判断意图，再调用对应的能力回答。

【你能做的事】
1. **答疑解惑** —— 用生动的语言和物理直觉讲解概念、公式、定理。
2. **出题与批改** —— 根据课本章节出练习题，或对学生答案做专业批改和详细解析。
3. **仿真绘图** —— 在学生想看波形、频谱或系统响应时，生成可运行的 Python + matplotlib 代码。

【课本检索参考内容】
{{context}}

【回答规范】
1. 涉及的数学公式必须用标准 LaTeX 语法输出。
2. 必须在回答中明确指出所依据的课本页码，格式：`📖 参考课本：第 XX 页`。页码只能取自上方【课本检索参考内容】中标注的页码，严禁自行编造或修改页码；若检索内容与问题无关，回答后注明"本问题未检索到课本内容"。
3. 回答使用简体中文，专业术语保持准确。
4. 仅在学生要求绘图时才输出代码：用一段简短中文先说明课本出处和页码，然后**且只能**输出**一段**完整的 Python 代码，包裹在 ```python 和 ``` 之间。
   - 代码必须以 `import numpy as np` 和 `import matplotlib.pyplot as plt` 开头。
   - 图表内的标题、坐标轴标签、图例使用英文（matplotlib 默认字体渲染中文会变方框），其他说明文字用中文。
   - 必须兼容 matplotlib 3.8+：严禁使用已废弃参数（例如 plt.stem() 的 use_line_collection 参数），不要调用 plt.show()。
""",
}


# ============================================================
# 工具函数
# ============================================================
def format_docs(docs):
    """把检索到的文档片段与页码拼接为上下文文本"""
    formatted = []
    for doc in docs:
        page = doc.metadata.get("page_label", "未知页码")
        content = doc.page_content.strip()
        formatted.append(f"【课本内容 ({page})】:\n{content}")
    return "\n\n".join(formatted)


def init_rag_system(api_key, expert_mode, pdf_name):
    """初始化带课本页码检索的问答函数。

    pdf_name 现在指向 OCR 数据文件（textbook_pages.json）。
    不再直接返回 chain，而是返回一个 `ask_fn(query, history)`
    闭包，对外接口是 `(answer_str, source_docs)` 元组。`source_docs`
    带 printed_page（印刷页码），用于 progress_core 章节进度统计。
    """
    system_prompt = EXPERT_PROMPTS.get(expert_mode, EXPERT_PROMPTS[EXPERT_MODE])

    # 聊天模型默认走 DeepSeek，可全部通过 secrets 切换到任意
    # OpenAI 兼容服务商（如硅基流动），无需改代码：
    #   CHAT_MODEL / CHAT_API_KEY / CHAT_BASE_URL
    chat_model = st.secrets.get("CHAT_MODEL", "deepseek-v4-flash")
    chat_api_key = st.secrets.get("CHAT_API_KEY", api_key)
    chat_base_url = st.secrets.get("CHAT_BASE_URL", "https://api.deepseek.com/v1")

    llm = ChatOpenAI(
        api_key=chat_api_key,
        model=chat_model,
        base_url=chat_base_url,
        max_tokens=2048,
        temperature=0.1,
    )

    vectorstore = get_vectorstore(pdf_name, api_key)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 3}) if vectorstore else None

    prompt_template = ChatPromptTemplate.from_messages([
        ("system", system_prompt),
        MessagesPlaceholder(variable_name="chat_history"),
        ("user", "{query}"),
    ])

    def get_context(inputs):
        if retriever:
            # LangChain 1.x 用 invoke，旧的 get_relevant_documents 已删除
            docs = retriever.invoke(inputs["query"])
            return format_docs(docs)
        return "未找到相关课本上下文。请根据自身知识回答，并在末尾注明'本页未检索到课本上下文'。"

    chain = (
        RunnablePassthrough.assign(context=get_context)
        | prompt_template
        | llm
        | StrOutputParser()
    )

    def ask_fn(query, chat_history):
        """学生问一个问题的完整调用：返回 (answer, source_docs)。

        - answer: LLM 生成的 Markdown 回答
        - source_docs: FAISS 命中的 chunks（带物理页号），用于章节进度推断
          检索失败时返回空列表，绝不抛出导致 UI 卡死。
        """
        source_docs = []
        if retriever:
            try:
                source_docs = retriever.invoke(query)
            except Exception:
                source_docs = []

        answer = chain.invoke({"query": query, "chat_history": chat_history})
        return str(answer), source_docs

    return ask_fn
