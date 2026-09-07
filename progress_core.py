"""信号与系统 AI 助教 · 学生进度统计模块。

按章节粒度。统计规则：

1. 学生每次问问题 → rag_core 检索到 K 个 chunk（带物理页号）→
   根据页号反推命中章节 → upsert 到 student_progress 表。
2.「进度 = 该章节被命中过的次数」。简单累加，不做 LLM 评估。
3. 章节字典在下方 DEFAULT_CHAPTERS。用户后续如要调整，可直接改这里并 push。

数据库：
- student_progress(username, chapter_id, ask_count, last_updated)
- users 加 role 列（默认 student / teacher）
"""
from datetime import timezone, timedelta
import datetime
import streamlit as st

import db_core


# ============================================================
# 章节目录
# ============================================================
# 默认占位字典：起始页号 -> 章节名。
# PDF: textbook.pdf（744 页信号与系统教材）。
# 用户可直接编辑这个列表调整章节划分，并在 Supabase SQL Editor
# 用 TRUNCATE student_progress 清空历史统计后即可生效。
DEFAULT_CHAPTERS = [
    {"id": 1,  "title": "第1章 信号与系统概述",     "start_page": 1,   "end_page": 30},
    {"id": 2,  "title": "第2章 连续信号的时域分析", "start_page": 31,  "end_page": 80},
    {"id": 3,  "title": "第3章 离散信号的时域分析", "start_page": 81,  "end_page": 130},
    {"id": 4,  "title": "第4章 傅里叶级数与变换",   "start_page": 131, "end_page": 200},
    {"id": 5,  "title": "第5章 拉普拉斯变换",       "start_page": 201, "end_page": 260},
    {"id": 6,  "title": "第6章 Z变换",              "start_page": 261, "end_page": 320},
    {"id": 7,  "title": "第7章 系统函数与频响",     "start_page": 321, "end_page": 380},
    {"id": 8,  "title": "第8章 状态变量分析",       "start_page": 381, "end_page": 450},
    {"id": 9,  "title": "第9章 数字滤波器",         "start_page": 451, "end_page": 530},
    {"id": 10, "title": "第10章 综合与拓展",        "start_page": 531, "end_page": 744},
]


def get_chapters():
    """获取章节目录（用户后续可改成读 config 或 Supabase）。"""
    return DEFAULT_CHAPTERS


def get_chapter_by_id(cid):
    for ch in get_chapters():
        if ch["id"] == cid:
            return ch
    return None


# ============================================================
# 章节推断：FAISS 命中 chunk → 命中章节 ID 集合
# ============================================================
def infer_chapter_ids_from_docs(docs):
    """根据 FAISS 命中的 chunks 判断本次问答涉及的章节 ID。

    docs 是 LangChain Document 列表，metadata["page"] 是 PyPDFLoader 给的 0-based 页号。
    """
    chapter_ids = set()
    for doc in docs or []:
        page_zero_based = doc.metadata.get("page", -1)
        real_page = page_zero_based + 1  # 转 1-based
        for ch in get_chapters():
            if ch["start_page"] <= real_page <= ch["end_page"]:
                chapter_ids.add(ch["id"])
                break
    return chapter_ids


# ============================================================
# 写进度
# ============================================================
def _now_str():
    """复用 db_core 的北京时间格式。"""
    return db_core._now_str()


def record_progress(username, source_docs):
    """学生问完一个问题后写一条进度。

    用户名为空、检索无结果时不报错（让学生体验不被打断）。
    """
    if not username:
        return
    chapter_ids = infer_chapter_ids_from_docs(source_docs)
    if not chapter_ids:
        return
    try:
        sb = db_core.get_supabase()
        now = db_core._now_str()
        for cid in chapter_ids:
            existing = sb.table("student_progress").select("id, ask_count") \
                .eq("username", username).eq("chapter_id", cid).execute()
            if existing.data:
                row = existing.data[0]
                sb.table("student_progress").update({
                    "ask_count": (row.get("ask_count") or 0) + 1,
                    "last_updated": now,
                }).eq("id", row["id"]).execute()
            else:
                sb.table("student_progress").insert({
                    "username": username,
                    "chapter_id": cid,
                    "ask_count": 1,
                    "last_updated": now,
                }).execute()
    except Exception:
        # 进度统计失败不影响主流程；后续可加 sidebar warning
        pass


# ============================================================
# 读进度
# ============================================================
def get_student_progress(username):
    """返回带章节元数据的进度列表：
        [{chapter_id, title, start_page, end_page, ask_count}, ...]
    """
    progress_map = {}
    try:
        sb = db_core.get_supabase()
        rows = sb.table("student_progress").select("chapter_id, ask_count") \
            .eq("username", username).execute()
        for r in rows.data:
            progress_map[r["chapter_id"]] = r.get("ask_count") or 0
    except Exception:
        pass

    result = []
    for ch in get_chapters():
        result.append({
            "chapter_id": ch["id"],
            "title": ch["title"],
            "start_page": ch["start_page"],
            "end_page": ch["end_page"],
            "ask_count": progress_map.get(ch["id"], 0),
        })
    return result


def get_student_summary(username):
    """取学生进度并计算汇总指标，用于学生页快速展示。"""
    progress = get_student_progress(username)
    total_asks = sum(c["ask_count"] for c in progress)
    touched_chapters = sum(1 for c in progress if c["ask_count"] > 0)
    total_chapters = len(progress)
    # 薄弱章节 Top3：被问次数最少的（先按 0 优先）
    weak = sorted(progress, key=lambda c: (c["ask_count"], c["chapter_id"]))[:3]
    return {
        "progress": progress,
        "total_asks": total_asks,
        "touched_chapters": touched_chapters,
        "total_chapters": total_chapters,
        "weak_chapters": weak,
    }


# ============================================================
# 班级总览（老师视角）
# ============================================================
def get_class_overview():
    """读取全部学生的进度信息，返回：
        [{username, total_asks, chapter_counts: {cid: count}, touched}, ...]
    """
    try:
        sb = db_core.get_supabase()
        rows = sb.table("student_progress").select(
            "username, chapter_id, ask_count"
        ).execute()

        students = {}
        for r in rows.data:
            u = r["username"]
            if u not in students:
                students[u] = {
                    "username": u,
                    "total_asks": 0,
                    "chapter_counts": {},
                    "touched_chapters": 0,
                }
            cid = r["chapter_id"]
            cnt = r.get("ask_count") or 0
            students[u]["chapter_counts"][cid] = cnt
            students[u]["total_asks"] += cnt

        # 计算每个学生的"覆盖章节数"
        for s in students.values():
            s["touched_chapters"] = sum(1 for v in s["chapter_counts"].values() if v > 0)

        # 按总问答数降序
        return sorted(students.values(), key=lambda x: -x["total_asks"])
    except Exception as e:
        st.error(f"⚠️ 读取班级进度失败: {e}")
        return []


def get_class_chapter_heatmap(students_data):
    """统计每章节被多少学生问过、以及总次数。
    返回 [{chapter_id, title, student_count, total_asks}, ...]
    """
    chapter_stats = {ch["id"]: {
        "chapter_id": ch["id"],
        "title": ch["title"],
        "student_count": 0,
        "total_asks": 0,
    } for ch in get_chapters()}

    for s in students_data:
        for cid, cnt in s["chapter_counts"].items():
            if cid in chapter_stats and cnt > 0:
                chapter_stats[cid]["student_count"] += 1
                chapter_stats[cid]["total_asks"] += cnt

    return list(chapter_stats.values())
