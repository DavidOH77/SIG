#!/usr/bin/env python3
import csv
import io
import json
import math
import os
import random
import re
import sqlite3
import statistics
import zipfile
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "sig.db"
for p in [DATA_DIR, UPLOAD_DIR, EXPORT_DIR]:
    p.mkdir(parents=True, exist_ok=True)

NEG_WORDS = {"불편", "느림", "오류", "버그", "비싸", "복잡", "어렵", "실패", "짜증", "문제", "답답", "불만", "힘들"}
POS_WORDS = {"좋", "만족", "편리", "빠르", "추천", "훌륭", "최고", "안정", "도움", "유용"}

ROUTES = {}

def route(path, methods=("GET",)):
    def wrap(fn):
        ROUTES[(path, tuple(methods))] = fn
        return fn
    return wrap


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            file_name TEXT,
            file_path TEXT,
            data_json_path TEXT,
            schema_json_path TEXT,
            analysis_json_path TEXT
        )
    """)
    conn.commit()
    conn.close()


def db_query(query, params=(), one=False):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.commit()
    conn.close()
    if one:
        return dict(rows[0]) if rows else None
    return [dict(r) for r in rows]


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def infer_col_type(values, name):
    lowered = name.lower()
    non_empty = [v for v in values if str(v).strip() != ""]
    if not non_empty:
        return "metadata"
    if "id" in lowered and len(set(non_empty)) == len(non_empty):
        return "id"
    numeric_count = 0
    date_count = 0
    short_text_count = 0
    long_text_count = 0
    for v in non_empty:
        s = str(v).strip()
        if re.match(r"^\d{4}-\d{1,2}-\d{1,2}", s) or re.match(r"^\d{4}/\d{1,2}/\d{1,2}", s):
            date_count += 1
        if re.match(r"^-?\d+(\.\d+)?$", s):
            numeric_count += 1
        if len(s) > 35:
            long_text_count += 1
        elif len(s) <= 20:
            short_text_count += 1
    ratio = len(non_empty)
    uniq = len(set(non_empty))
    if date_count / ratio > 0.7:
        return "date"
    if numeric_count / ratio > 0.8:
        nums = [float(v) for v in non_empty if re.match(r"^-?\d+(\.\d+)?$", str(v).strip())]
        if nums and all(1 <= n <= 7 and float(n).is_integer() for n in nums):
            return "ordinal"
        return "numeric"
    joined = " ".join(str(v) for v in non_empty[:40])
    if any(sep in joined for sep in [",", ";", "|"]) and uniq < ratio * 0.8:
        return "multi_select"
    if long_text_count / ratio > 0.35 or "text" in lowered or "comment" in lowered or "요청" in name or "불편" in name:
        return "free_text"
    if uniq <= min(15, ratio * 0.5):
        return "categorical"
    return "metadata"


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


def col_to_index(col):
    n = 0
    for ch in col:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def read_xlsx(path):
    with zipfile.ZipFile(path, "r") as z:
        sst = []
        if "xl/sharedStrings.xml" in z.namelist():
            import xml.etree.ElementTree as ET
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for si in root.findall("a:si", ns):
                texts = [t.text or "" for t in si.findall(".//a:t", ns)]
                sst.append("".join(texts))
        import xml.etree.ElementTree as ET
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        ns = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        rows = []
        for r in sheet.findall("a:sheetData/a:row", ns):
            cells = {}
            for c in r.findall("a:c", ns):
                ref = c.attrib.get("r", "A1")
                col = re.match(r"([A-Z]+)", ref).group(1)
                idx = col_to_index(col)
                t = c.attrib.get("t")
                v_node = c.find("a:v", ns)
                value = ""
                if v_node is not None:
                    value = v_node.text or ""
                    if t == "s":
                        value = sst[int(value)] if value.isdigit() and int(value) < len(sst) else ""
                cells[idx] = value
            if cells:
                max_i = max(cells)
                arr = [cells.get(i, "") for i in range(max_i + 1)]
                rows.append(arr)
        if not rows:
            return []
        header = [h.strip() or f"col_{i+1}" for i, h in enumerate(rows[0])]
        out = []
        for r in rows[1:]:
            d = {}
            for i, h in enumerate(header):
                d[h] = r[i] if i < len(r) else ""
            out.append(d)
        return out


def parse_file(path):
    if path.suffix.lower() == ".csv":
        return read_csv(path)
    if path.suffix.lower() == ".xlsx":
        return read_xlsx(path)
    raise ValueError("지원하지 않는 파일 형식")


def calc_health(rows, schema):
    total = len(rows)
    cols = list(schema.keys())
    missing = {}
    suspicious = []
    for c in cols:
        vals = [str(r.get(c, "")).strip() for r in rows]
        miss = sum(1 for v in vals if v == "")
        missing[c] = {"count": miss, "ratio": round(miss / total, 3) if total else 0}
        non_empty = [v for v in vals if v]
        if non_empty and len(set(non_empty)) == 1:
            suspicious.append({"column": c, "issue": "상수값 컬럼"})
        if total and miss / total > 0.6:
            suspicious.append({"column": c, "issue": "결측치 과다"})
    id_col = next((k for k, v in schema.items() if v == "id"), None)
    dup_id = 0
    if id_col:
        ids = [r.get(id_col, "") for r in rows if r.get(id_col, "")]
        dup_id = len(ids) - len(set(ids))
    return {
        "row_count": total,
        "column_count": len(cols),
        "missing": missing,
        "duplicate_respondent_ids": dup_id,
        "suspicious": suspicious,
        "normalization_suggestions": [
            "카테고리 값의 대소문자/띄어쓰기 표준화",
            "Likert 값(1~5/1~7) 범위 외 값 점검",
            "다중선택 컬럼 구분자 통일(쉼표 권장)",
        ],
    }


def safe_float(v):
    try:
        return float(str(v).strip())
    except:
        return None


def analyze_quant(rows, schema, segment_col=None, target_col=None):
    cols = list(schema.keys())
    distributions = {}
    stats = {}
    for c in cols:
        vals = [r.get(c, "") for r in rows]
        if schema[c] in ["categorical", "ordinal", "multi_select"]:
            cnt = Counter([str(v).strip() for v in vals if str(v).strip()])
            distributions[c] = cnt.most_common(10)
        if schema[c] in ["numeric", "ordinal"]:
            nums = [safe_float(v) for v in vals]
            nums = [n for n in nums if n is not None]
            if nums:
                stats[c] = {
                    "mean": round(sum(nums) / len(nums), 3),
                    "median": round(statistics.median(nums), 3),
                    "std": round(statistics.pstdev(nums), 3) if len(nums) > 1 else 0,
                    "min": min(nums),
                    "max": max(nums),
                }
    group_comparison = []
    if segment_col and segment_col in cols:
        groups = defaultdict(list)
        for r in rows:
            key = str(r.get(segment_col, "미분류"))
            groups[key].append(r)
        for num_col in [c for c in cols if schema[c] in ["numeric", "ordinal"]][:5]:
            item = {"metric": num_col, "groups": []}
            all_vals = [safe_float(r.get(num_col, "")) for r in rows]
            all_vals = [v for v in all_vals if v is not None]
            overall = sum(all_vals) / len(all_vals) if all_vals else None
            for g, g_rows in groups.items():
                vals = [safe_float(r.get(num_col, "")) for r in g_rows]
                vals = [v for v in vals if v is not None]
                if vals:
                    m = sum(vals) / len(vals)
                    item["groups"].append({"segment": g, "mean": round(m, 3), "delta_vs_total": round(m - overall, 3) if overall else 0})
            group_comparison.append(item)
    corr = []
    num_cols = [c for c in cols if schema[c] in ["numeric", "ordinal"]]
    for i, a in enumerate(num_cols[:8]):
        for b in num_cols[i+1:8]:
            x = [safe_float(r.get(a, "")) for r in rows]
            y = [safe_float(r.get(b, "")) for r in rows]
            pairs = [(xx, yy) for xx, yy in zip(x, y) if xx is not None and yy is not None]
            if len(pairs) < 4:
                continue
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
            cov = sum((xx-mx)*(yy-my) for xx, yy in pairs)
            dx = math.sqrt(sum((xx-mx)**2 for xx in xs))
            dy = math.sqrt(sum((yy-my)**2 for yy in ys))
            r = cov / (dx*dy) if dx and dy else 0
            corr.append({"a": a, "b": b, "corr": round(r, 3)})
    corr.sort(key=lambda x: abs(x["corr"]), reverse=True)

    driver = []
    if target_col and target_col in num_cols:
        for c in num_cols:
            if c == target_col:
                continue
            vals = []
            for r in rows:
                t = safe_float(r.get(target_col, ""))
                v = safe_float(r.get(c, ""))
                if t is not None and v is not None:
                    vals.append((v, t))
            if len(vals) < 5:
                continue
            xs = [v for v, _ in vals]
            ys = [t for _, t in vals]
            mx, my = sum(xs)/len(xs), sum(ys)/len(ys)
            cov = sum((x-mx)*(y-my) for x, y in vals)
            dx = math.sqrt(sum((x-mx)**2 for x in xs))
            dy = math.sqrt(sum((y-my)**2 for y in ys))
            corr_val = cov/(dx*dy) if dx and dy else 0
            driver.append({"factor": c, "association": round(corr_val, 3), "interpretation": "연관 요인(인과 아님)"})
        driver.sort(key=lambda x: abs(x["association"]), reverse=True)
    return {
        "distributions": distributions,
        "stats": stats,
        "group_comparison": group_comparison,
        "correlations": corr[:20],
        "driver_analysis": driver[:10],
    }


def classify_sentiment(text):
    t = text.lower()
    pos = sum(1 for w in POS_WORDS if w in t)
    neg = sum(1 for w in NEG_WORDS if w in t)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def tokenize(text):
    text = re.sub(r"[^0-9a-zA-Z가-힣\s]", " ", text.lower())
    toks = [t for t in text.split() if len(t) > 1]
    return toks


def analyze_text(rows, schema, segment_col=None):
    text_cols = [c for c, t in schema.items() if t == "free_text"]
    clusters = []
    for col in text_cols:
        comments = [str(r.get(col, "")).strip() for r in rows if str(r.get(col, "")).strip()]
        if not comments:
            continue
        bag = defaultdict(list)
        for cm in comments:
            toks = tokenize(cm)
            key = "기타"
            for k in ["가격", "속도", "오류", "UX", "지원", "결제", "온보딩", "검색", "모바일", "알림", "보고서"]:
                if k.lower() in cm.lower():
                    key = k
                    break
            if key == "기타" and toks:
                key = toks[0]
            bag[key].append(cm)
        for k, arr in sorted(bag.items(), key=lambda x: len(x[1]), reverse=True)[:8]:
            sents = [classify_sentiment(a) for a in arr]
            clusters.append({
                "column": col,
                "cluster": k,
                "count": len(arr),
                "sentiment": Counter(sents).most_common(1)[0][0],
                "sample_quotes": arr[:3],
            })
    clusters.sort(key=lambda x: x["count"], reverse=True)

    seg_summary = []
    if segment_col and segment_col in schema:
        by_seg = defaultdict(list)
        for r in rows:
            by_seg[str(r.get(segment_col, "미분류"))].append(r)
        for s, s_rows in by_seg.items():
            neg = 0
            total = 0
            for c in text_cols:
                for r in s_rows:
                    txt = str(r.get(c, "")).strip()
                    if txt:
                        total += 1
                        if classify_sentiment(txt) == "negative":
                            neg += 1
            if total:
                seg_summary.append({"segment": s, "negative_ratio": round(neg/total, 3), "text_count": total})
        seg_summary.sort(key=lambda x: x["negative_ratio"], reverse=True)
    return {"clusters": clusters, "segment_text_risk": seg_summary}


def priority_model(rows, schema, quant, text, segment_col=None, target_col=None):
    issues = []
    total = len(rows) or 1
    for cl in text.get("clusters", [])[:20]:
        freq = cl["count"] / total
        severity = 1.0 if cl["sentiment"] == "negative" else 0.5 if cl["sentiment"] == "neutral" else 0.2
        seg_risk = 0.0
        if segment_col:
            seg_risk = 0.2 + min(0.8, len(set(str(r.get(segment_col, "")) for r in rows if str(r.get(segment_col, "")))) / 10)
        assoc = 0.0
        if quant.get("driver_analysis"):
            assoc = min(1.0, abs(quant["driver_analysis"][0].get("association", 0)))
        score = 0.4*freq + 0.3*severity + 0.2*seg_risk + 0.1*assoc
        issues.append({
            "pain_point": f"{cl['column']}:{cl['cluster']}",
            "frequency": round(freq, 3),
            "severity": round(severity, 3),
            "segment_concentration": round(seg_risk, 3),
            "target_association": round(assoc, 3),
            "priority_score": round(score, 3),
            "evidence_quotes": cl["sample_quotes"],
        })
    issues.sort(key=lambda x: x["priority_score"], reverse=True)
    return {
        "formula": "점수 = 0.4*빈도 + 0.3*심각도 + 0.2*세그먼트집중도 + 0.1*타깃연관",
        "items": issues[:15],
    }


def generate_report(project, analysis):
    top_findings = []
    for d_col, d_vals in list(analysis["quant"].get("distributions", {}).items())[:3]:
        if d_vals:
            top_findings.append(f"{d_col}에서 '{d_vals[0][0]}' 응답이 가장 많았습니다({d_vals[0][1]}건).")
    for g in analysis["quant"].get("group_comparison", [])[:2]:
        if g["groups"]:
            worst = sorted(g["groups"], key=lambda x: x["mean"])[0]
            top_findings.append(f"{g['metric']} 지표에서 {worst['segment']} 세그먼트가 가장 낮았습니다(평균 {worst['mean']}).")
    pains = analysis["priority"].get("items", [])[:5]
    hypotheses = []
    actions = []
    for p in pains:
        hypotheses.append({
            "statement": f"만약 '{p['pain_point']}' 문제를 개선하면 만족도 지표가 개선될 가능성이 있습니다.",
            "evidence": f"빈도 {p['frequency']} / 심각도 {p['severity']} / 우선순위 {p['priority_score']}",
            "confidence": "중간",
            "next_step": "해당 이슈 관련 사용성 테스트 + 개선안 A/B 비교",
        })
        actions.append({
            "what": p["pain_point"],
            "why": f"우선순위 점수 {p['priority_score']}로 상위 위험 영역",
            "who": "영향 세그먼트 우선 대응",
            "benefit": "불만 비율 감소 및 만족도 개선",
            "confidence": "중간",
        })
    return {
        "executive_summary": f"{project['title']} 분석 결과, 주요 불편 클러스터를 중심으로 우선 개선 영역이 식별되었습니다.",
        "key_findings": top_findings,
        "opportunities": ["고위험 세그먼트 집중 개선", "반복 불만 텍스트 기반 UX 개선"],
        "hypotheses": hypotheses,
        "actions": actions,
        "followup_questions": ["세그먼트별 핵심 태스크 실패 맥락은 무엇인가?", "가격/가치 인식 불일치의 원인은 무엇인가?"],
    }


def load_project(pid):
    p = db_query("SELECT * FROM projects WHERE id=?", (pid,), one=True)
    if not p:
        return None
    for key in ["data_json_path", "schema_json_path", "analysis_json_path"]:
        if p.get(key) and os.path.exists(p[key]):
            with open(p[key], encoding="utf-8") as f:
                p[key.replace("_path", "")] = json.load(f)
        else:
            p[key.replace("_path", "")] = None
    return p


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def render_layout(title, body):
    nav = """
    <style>
    body{font-family:Arial,sans-serif;background:#f4f6f8;margin:0;color:#102a43}
    .top{background:#102a43;color:#fff;padding:14px 20px;font-weight:700;display:flex;justify-content:space-between;align-items:center}
    .top small{font-weight:500;opacity:.85}
    .container{max-width:1240px;margin:0 auto;padding:20px}
    .card{background:#fff;border-radius:10px;padding:16px;margin-bottom:14px;box-shadow:0 1px 2px rgba(0,0,0,.08)}
    .hero{background:linear-gradient(120deg,#102a43,#0f766e);color:#fff}
    .hero h1{margin:0 0 8px 0}
    .hero p{margin:0;opacity:.9}
    table{border-collapse:collapse;width:100%;font-size:13px}
    th,td{border:1px solid #d9e2ec;padding:6px;text-align:left;vertical-align:top}
    .btn{display:inline-block;background:#0f766e;color:#fff;padding:8px 12px;border-radius:8px;text-decoration:none;border:none;cursor:pointer}
    .btn2{background:#334e68}
    .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px}
    .badge{background:#e6fffa;color:#0f766e;padding:2px 8px;border-radius:999px;font-size:12px}
    input,select,textarea{padding:8px;border:1px solid #bcccdc;border-radius:6px;width:100%}
    .muted{color:#627d98;font-size:12px}
    .kpi{font-size:26px;font-weight:700;margin-top:6px}
    .ia li{margin-bottom:5px}
    </style>
    """
    return f"<html><head><meta charset='utf-8'><title>{title}</title>{nav}</head><body><div class='top'><div>시그(SIG) · UX 설문 분석 워크스페이스</div><small>한국 스타트업 실무자용 분석 MVP</small></div><div class='container'>{body}</div></body></html>"


def redirect(start_response, location):
    start_response("302 Found", [("Location", location)])
    return [b""]


def parse_post(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
    except:
        size = 0
    body = environ["wsgi.input"].read(size)
    ctype = environ.get("CONTENT_TYPE", "")
    if "application/x-www-form-urlencoded" in ctype:
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
    return {}

@route("/")
def home(environ, start_response):
    projects = db_query("SELECT * FROM projects ORDER BY updated_at DESC")
    cards = []
    for p in projects:
        cards.append(
            f"<div class='card'><h3>{p['title']}</h3><p class='muted'>업데이트: {p['updated_at']}</p>"
            f"<a class='btn' href='/projects/{p['id']}'>프로젝트 개요</a> "
            f"<a class='btn btn2' href='/projects/{p['id']}/analysis'>분석 바로가기</a></div>"
        )
    body = f"""
    <div class='card hero'>
      <h1>시그(SIG) 설문 분석</h1>
      <p>업로드한 설문 시트를 자동 분석해 데이터 건강도·세그먼트·텍스트 인사이트·우선순위 액션까지 한 번에 제공합니다.</p>
    </div>
    <div class='grid'>
      <div class='card'><div class='muted'>활성 프로젝트</div><div class='kpi'>{len(projects)}</div></div>
      <div class='card'><div class='muted'>핵심 산출물</div><div class='kpi'>리포트/가설/액션</div></div>
      <div class='card'><div class='muted'>분석 철학</div><div class='kpi'>근거 기반 · 설명 가능</div></div>
    </div>
    <div class='grid'>
      <div class='card'>
        <h2>새 분석 프로젝트 생성</h2>
        <form method='POST' action='/projects/create'>
          <label>프로젝트 이름</label><input name='title' placeholder='예: 2026 Q1 온보딩 설문 분석' required />
          <br/><br/><button class='btn'>프로젝트 만들기</button>
        </form>
      </div>
      <div class='card'>
        <h3>구현된 IA 화면</h3>
        <ul class='ia'>
          <li>홈 / 프로젝트 개요 / 데이터 업로드 & 매핑</li>
          <li>데이터 건강도 / 분석 개요 / 정량 분석</li>
          <li>세그먼트 분석 / 텍스트 인사이트</li>
          <li>Pain Point 우선순위 / 리포트 / 내보내기</li>
        </ul>
      </div>
    </div>
    <h3>최근 프로젝트</h3>
    """ + ("".join(cards) if cards else "<div class='card'>아직 프로젝트가 없습니다.</div>")
    html = render_layout("SIG 홈", body)
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
    return [html.encode("utf-8")]

@route("/projects/create", methods=("POST",))
def create_project(environ, start_response):
    form = parse_post(environ)
    title = form.get("title", "새 프로젝트")
    ts = now()
    db_query("INSERT INTO projects(title,created_at,updated_at) VALUES (?,?,?)", (title, ts, ts))
    p = db_query("SELECT id FROM projects ORDER BY id DESC LIMIT 1", one=True)
    return redirect(start_response, f"/projects/{p['id']}/data")


def project_nav(pid):
    links = [
        ("개요", f"/projects/{pid}"),("데이터", f"/projects/{pid}/data"),("데이터 건강도", f"/projects/{pid}/health"),
        ("분석개요", f"/projects/{pid}/analysis"),("정량", f"/projects/{pid}/quant"),("세그먼트", f"/projects/{pid}/segments"),
        ("텍스트", f"/projects/{pid}/text"),("우선순위", f"/projects/{pid}/priorities"),("리포트", f"/projects/{pid}/report"),("내보내기", f"/projects/{pid}/export")
    ]
    return " ".join([f"<a class='btn btn2' style='margin-right:6px;margin-bottom:6px' href='{u}'>{n}</a>" for n,u in links])


def ensure_analysis(project):
    if not project.get("data_json") or not project.get("schema_json"):
        return None
    rows = project["data_json"]
    schema = project["schema_json"]
    cfg = project.get("analysis_json", {}).get("config", {}) if project.get("analysis_json") else {}
    segment = cfg.get("segment_col")
    target = cfg.get("target_col")
    health = calc_health(rows, schema)
    quant = analyze_quant(rows, schema, segment, target)
    text = analyze_text(rows, schema, segment)
    priority = priority_model(rows, schema, quant, text, segment, target)
    analysis = {"config": {"segment_col": segment, "target_col": target}, "health": health, "quant": quant, "text": text, "priority": priority}
    analysis["report"] = generate_report(project, analysis)
    path = DATA_DIR / f"analysis_{project['id']}.json"
    save_json(path, analysis)
    db_query("UPDATE projects SET analysis_json_path=?, updated_at=? WHERE id=?", (str(path), now(), project["id"]))
    return analysis


def route_project(environ, start_response, pid, page):
    p = load_project(pid)
    if not p:
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]
    analysis = p.get("analysis_json") or (ensure_analysis(p) if p.get("data_json") and p.get("schema_json") else None)
    head = f"<div class='card'><h2>{p['title']}</h2><p class='muted'>파일: {p.get('file_name') or '없음'}</p>{project_nav(pid)}</div>"

    if page == "overview":
        body = head + "<div class='grid'>"
        body += f"<div class='card'><h3>상태</h3><p>{'분석 준비 완료' if analysis else '데이터 업로드 필요'}</p></div>"
        body += f"<div class='card'><h3>최종 업데이트</h3><p>{p['updated_at']}</p></div>"
        body += f"<div class='card'><h3>핵심 CTA</h3><a class='btn' href='/projects/{pid}/analysis'>분석 열기</a></div>"
        body += "</div>"
        body += f"""
        <div class='card'>
          <h3>빠른 시작 가이드</h3>
          <ol>
            <li><a href='/projects/{pid}/data'>데이터 업로드/컬럼 매핑</a>에서 세그먼트와 타깃 지표를 설정하세요.</li>
            <li><a href='/projects/{pid}/health'>데이터 건강도</a>에서 결측/중복/의심필드를 점검하세요.</li>
            <li><a href='/projects/{pid}/analysis'>분석 개요</a>에서 핵심 인사이트 카드를 확인하세요.</li>
            <li><a href='/projects/{pid}/priorities'>우선순위</a>와 <a href='/projects/{pid}/report'>리포트</a>로 액션 플랜을 도출하세요.</li>
          </ol>
        </div>
        """
    elif page == "data":
        preview = ""
        if p.get("data_json"):
            rows = p["data_json"][:8]
            headers = list(rows[0].keys()) if rows else []
            t = "<table><tr>" + "".join([f"<th>{h}</th>" for h in headers]) + "</tr>"
            for r in rows:
                t += "<tr>" + "".join([f"<td>{str(r.get(h,''))[:80]}</td>" for h in headers]) + "</tr>"
            t += "</table>"
            opts = "".join([f"<option value='{h}'>{h}</option>" for h in headers])
            schema_rows = ""
            for h, typ in p.get("schema_json", {}).items():
                select = "<select name='type_%s'>" % h + "".join([f"<option {'selected' if typ==x else ''} value='{x}'>{x}</option>" for x in ["id","metadata","categorical","ordinal","numeric","multi_select","free_text","date"]]) + "</select>"
                schema_rows += f"<tr><td>{h}</td><td>{select}</td></tr>"
            preview = f"<div class='card'><h3>데이터 프리뷰</h3>{t}</div><div class='card'><h3>컬럼 타입 매핑(수정 가능)</h3><form method='POST' action='/projects/{pid}/schema'><table><tr><th>컬럼</th><th>타입</th></tr>{schema_rows}</table><br/><label>세그먼트 컬럼</label><select name='segment_col'><option value=''>선택 안함</option>{opts}</select><br/><br/><label>주요 성과지표(타깃)</label><select name='target_col'><option value=''>선택 안함</option>{opts}</select><br/><br/><button class='btn'>매핑 저장 및 재분석</button></form></div>"
        body = head + """
        <div class='card'>
        <h3>CSV/XLSX 업로드</h3>
        <form method='POST' action='/projects/%s/upload' enctype='multipart/form-data'>
          <input type='file' name='file' accept='.csv,.xlsx' required/><br/><br/>
          <button class='btn'>업로드 및 자동분석</button>
        </form>
        <p class='muted'>지원 형식: CSV, XLSX. 업로드 후 스키마 추론과 건강도 분석이 자동 실행됩니다.</p>
        </div>
        %s
        """ % (pid, preview)
    elif page == "health":
        if not analysis:
            body = head + "<div class='card'>먼저 데이터를 업로드하세요.</div>"
        else:
            mrows = "".join([f"<tr><td>{c}</td><td>{v['count']}</td><td>{v['ratio']}</td></tr>" for c,v in analysis['health']['missing'].items()])
            srows = "".join([f"<li>{x['column']} - {x['issue']}</li>" for x in analysis['health']['suspicious']]) or "<li>의심 컬럼 없음</li>"
            ns = "".join([f"<li>{n}</li>" for n in analysis['health']['normalization_suggestions']])
            body = head + f"<div class='grid'><div class='card'><h3>행 수</h3><p>{analysis['health']['row_count']}</p></div><div class='card'><h3>컬럼 수</h3><p>{analysis['health']['column_count']}</p></div><div class='card'><h3>중복 ID</h3><p>{analysis['health']['duplicate_respondent_ids']}</p></div></div><div class='card'><h3>결측치 현황</h3><table><tr><th>컬럼</th><th>결측 수</th><th>비율</th></tr>{mrows}</table></div><div class='card'><h3>의심 필드</h3><ul>{srows}</ul></div><div class='card'><h3>정규화 제안</h3><ul>{ns}</ul></div>"
    elif page == "analysis":
        if not analysis:
            body = head + "<div class='card'>분석 데이터가 없습니다.</div>"
        else:
            findings = "".join([f"<li>{x}</li>" for x in analysis['report']['key_findings']])
            body = head + f"<div class='grid'><div class='card'><h3>헤드라인: 우선 Pain Point 수</h3><p>{len(analysis['priority']['items'])}</p></div><div class='card'><h3>텍스트 클러스터</h3><p>{len(analysis['text']['clusters'])}</p></div><div class='card'><h3>상관관계 계산쌍</h3><p>{len(analysis['quant']['correlations'])}</p></div></div><div class='card'><h3>탑 인사이트 요약</h3><ul>{findings}</ul><p class='muted'>모든 인사이트는 계산된 통계/클러스터에 기반합니다.</p></div>"
    elif page == "quant":
        if not analysis:
            body = head + "<div class='card'>분석 데이터가 없습니다.</div>"
        else:
            st = "".join([f"<tr><td>{k}</td><td>{v['mean']}</td><td>{v['median']}</td><td>{v['std']}</td><td>{v['min']}~{v['max']}</td></tr>" for k,v in analysis['quant']['stats'].items()])
            cor = "".join([f"<tr><td>{x['a']}</td><td>{x['b']}</td><td>{x['corr']}</td></tr>" for x in analysis['quant']['correlations'][:15]])
            drv = "".join([f"<tr><td>{d['factor']}</td><td>{d['association']}</td><td>{d['interpretation']}</td></tr>" for d in analysis['quant']['driver_analysis']]) or "<tr><td colspan='3'>타깃 미설정 또는 데이터 부족</td></tr>"
            body = head + f"<div class='card'><h3>기술통계</h3><table><tr><th>지표</th><th>평균</th><th>중앙값</th><th>표준편차</th><th>범위</th></tr>{st}</table></div><div class='card'><h3>상관관계(절댓값 상위)</h3><table><tr><th>A</th><th>B</th><th>r</th></tr>{cor}</table></div><div class='card'><h3>드라이버 분석(연관 요인)</h3><table><tr><th>요인</th><th>연관계수</th><th>주의</th></tr>{drv}</table></div>"
    elif page == "segments":
        if not analysis:
            body = head + "<div class='card'>세그먼트 분석 데이터가 없습니다.</div>"
        else:
            blocks = ""
            for g in analysis['quant']['group_comparison'][:6]:
                rows_html = "".join([f"<tr><td>{r['segment']}</td><td>{r['mean']}</td><td>{r['delta_vs_total']}</td></tr>" for r in sorted(g['groups'], key=lambda x: x['mean'])])
                blocks += f"<div class='card'><h3>{g['metric']} 세그먼트 비교</h3><table><tr><th>세그먼트</th><th>평균</th><th>전체 대비</th></tr>{rows_html}</table></div>"
            risk = "".join([f"<tr><td>{x['segment']}</td><td>{x['negative_ratio']}</td><td>{x['text_count']}</td></tr>" for x in analysis['text']['segment_text_risk'][:10]])
            body = head + blocks + f"<div class='card'><h3>세그먼트 텍스트 리스크</h3><table><tr><th>세그먼트</th><th>부정 비율</th><th>텍스트 수</th></tr>{risk}</table></div>"
    elif page == "text":
        if not analysis:
            body = head + "<div class='card'>텍스트 분석 데이터가 없습니다.</div>"
        else:
            cl = ""
            for c in analysis['text']['clusters'][:15]:
                quotes = "".join([f"<li>“{q[:120]}”</li>" for q in c['sample_quotes']])
                cl += f"<div class='card'><h3>{c['column']} · {c['cluster']} <span class='badge'>{c['count']}건</span></h3><p>감성: {c['sentiment']}</p><ul>{quotes}</ul></div>"
            body = head + cl
    elif page == "priorities":
        if not analysis:
            body = head + "<div class='card'>우선순위 데이터가 없습니다.</div>"
        else:
            rows_html = "".join([f"<tr><td>{i+1}</td><td>{p['pain_point']}</td><td>{p['priority_score']}</td><td>{p['frequency']}</td><td>{p['severity']}</td><td>{p['segment_concentration']}</td><td>{p['target_association']}</td></tr>" for i,p in enumerate(analysis['priority']['items'])])
            body = head + f"<div class='card'><h3>우선순위 모델</h3><p>{analysis['priority']['formula']}</p><p class='muted'>설명가능성 확보를 위해 각 구성요소를 분리 표시합니다.</p></div><div class='card'><h3>Pain Point 우선순위</h3><table><tr><th>#</th><th>Pain Point</th><th>점수</th><th>빈도</th><th>심각도</th><th>세그먼트집중</th><th>타깃연관</th></tr>{rows_html}</table></div>"
    elif page == "report":
        if not analysis:
            body = head + "<div class='card'>리포트 데이터가 없습니다.</div>"
        else:
            rpt = analysis['report']
            k = "".join([f"<li>{x}</li>" for x in rpt['key_findings']])
            h = "".join([f"<li><b>{x['statement']}</b><br/>근거: {x['evidence']}<br/>신뢰도: {x['confidence']} · 다음 검증: {x['next_step']}</li>" for x in rpt['hypotheses']])
            a = "".join([f"<li><b>{x['what']}</b> - {x['why']} / 대상: {x['who']} / 기대효과: {x['benefit']} / 신뢰도:{x['confidence']}</li>" for x in rpt['actions']])
            fup = "".join([f"<li>{x}</li>" for x in rpt['followup_questions']])
            body = head + f"<div class='card'><h3>Executive Summary</h3><p>{rpt['executive_summary']}</p></div><div class='card'><h3>핵심 발견</h3><ul>{k}</ul></div><div class='card'><h3>가설</h3><ul>{h}</ul></div><div class='card'><h3>액션 플랜</h3><ul>{a}</ul></div><div class='card'><h3>후속 리서치 질문</h3><ul>{fup}</ul></div>"
    elif page == "export":
        body = head + f"""
        <div class='card'><h3>내보내기 센터</h3>
          <a class='btn' href='/projects/{pid}/export/md'>분석 요약 Markdown</a>
          <a class='btn btn2' href='/projects/{pid}/report?print=1'>인쇄용 리포트 페이지</a>
          <a class='btn' href='/projects/{pid}/export/clean.csv'>정제 데이터 CSV</a>
          <a class='btn' href='/projects/{pid}/export/pain_points.csv'>Pain Point CSV</a>
        </div>
        """
    else:
        body = head + "<div class='card'>unknown page</div>"

    html = render_layout("SIG 프로젝트", body)
    start_response("200 OK", [("Content-Type", "text/html; charset=utf-8")])
    return [html.encode("utf-8")]


def parse_multipart(environ):
    ctype = environ.get("CONTENT_TYPE", "")
    if "multipart/form-data" not in ctype:
        return None, None
    boundary = ctype.split("boundary=")[-1].encode()
    length = int(environ.get("CONTENT_LENGTH", "0") or 0)
    body = environ["wsgi.input"].read(length)
    parts = body.split(b"--" + boundary)
    for p in parts:
        if b"name=\"file\"" in p and b"filename=\"" in p:
            header, content = p.split(b"\r\n\r\n", 1)
            m = re.search(br'filename="([^"]+)"', header)
            fn = m.group(1).decode("utf-8", errors="ignore") if m else "upload.csv"
            content = content.rsplit(b"\r\n", 1)[0]
            return fn, content
    return None, None


def app(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET").upper()
    if method == "HEAD":
        method = "GET"

    # explicit routes
    for (rp, methods), fn in ROUTES.items():
        if path == rp and method in methods:
            return fn(environ, start_response)

    m = re.match(r"^/projects/(\d+)(?:/(.*))?$", path)
    if m:
        pid = int(m.group(1))
        tail = m.group(2) or ""
        if tail == "" and method == "GET":
            return route_project(environ, start_response, pid, "overview")
        if tail in ["data","health","analysis","quant","segments","text","priorities","report","export"] and method == "GET":
            return route_project(environ, start_response, pid, tail)
        if tail == "upload" and method == "POST":
            project = load_project(pid)
            fname, content = parse_multipart(environ)
            if not fname:
                return redirect(start_response, f"/projects/{pid}/data")
            upath = UPLOAD_DIR / f"{pid}_{fname}"
            with open(upath, "wb") as f:
                f.write(content)
            rows = parse_file(upath)
            if not rows:
                return redirect(start_response, f"/projects/{pid}/data")
            schema = {c: infer_col_type([r.get(c, "") for r in rows], c) for c in rows[0].keys()}
            dpath = DATA_DIR / f"dataset_{pid}.json"
            spath = DATA_DIR / f"schema_{pid}.json"
            save_json(dpath, rows)
            save_json(spath, schema)
            db_query("UPDATE projects SET file_name=?, file_path=?, data_json_path=?, schema_json_path=?, updated_at=? WHERE id=?",
                     (fname, str(upath), str(dpath), str(spath), now(), pid))
            p = load_project(pid)
            ensure_analysis(p)
            return redirect(start_response, f"/projects/{pid}/data")
        if tail == "schema" and method == "POST":
            form = parse_post(environ)
            p = load_project(pid)
            if p and p.get("schema_json"):
                schema = p["schema_json"]
                for c in list(schema.keys()):
                    key = f"type_{c}"
                    if key in form:
                        schema[c] = form[key]
                save_json(Path(p["schema_json_path"]), schema)
                ana = p.get("analysis_json") or {}
                ana["config"] = {"segment_col": form.get("segment_col") or None, "target_col": form.get("target_col") or None}
                apath = DATA_DIR / f"analysis_{pid}.json"
                save_json(apath, ana)
                db_query("UPDATE projects SET schema_json_path=?, analysis_json_path=?, updated_at=? WHERE id=?", (p["schema_json_path"], str(apath), now(), pid))
                p2 = load_project(pid)
                ensure_analysis(p2)
            return redirect(start_response, f"/projects/{pid}/data")
        if tail.startswith("export/") and method == "GET":
            p = load_project(pid)
            analysis = p.get("analysis_json") if p else None
            if not p or not analysis:
                start_response("404 Not Found", [("Content-Type","text/plain")])
                return [b"no analysis"]
            sub = tail.replace("export/", "")
            if sub == "md":
                rpt = analysis["report"]
                lines = [f"# {p['title']} 분석 리포트", "", "## Executive Summary", rpt["executive_summary"], "", "## 핵심 발견"]
                lines += [f"- {x}" for x in rpt["key_findings"]]
                lines += ["", "## 우선순위 Pain Points"]
                lines += [f"- {x['pain_point']} (점수 {x['priority_score']})" for x in analysis['priority']['items'][:10]]
                data = "\n".join(lines).encode("utf-8")
                start_response("200 OK", [("Content-Type", "text/markdown; charset=utf-8"), ("Content-Disposition", f"attachment; filename=project_{pid}_summary.md")])
                return [data]
            if sub == "clean.csv":
                rows = p["data_json"]
                output = io.StringIO()
                w = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
                w.writeheader()
                for r in rows:
                    w.writerow(r)
                data = output.getvalue().encode("utf-8-sig")
                start_response("200 OK", [("Content-Type", "text/csv"), ("Content-Disposition", f"attachment; filename=project_{pid}_clean.csv")])
                return [data]
            if sub == "pain_points.csv":
                rows = analysis["priority"]["items"]
                output = io.StringIO()
                w = csv.DictWriter(output, fieldnames=["pain_point","priority_score","frequency","severity","segment_concentration","target_association"])
                w.writeheader()
                for r in rows:
                    w.writerow({k:r[k] for k in w.fieldnames})
                data = output.getvalue().encode("utf-8-sig")
                start_response("200 OK", [("Content-Type", "text/csv"), ("Content-Disposition", f"attachment; filename=project_{pid}_pain_points.csv")])
                return [data]

    start_response("404 Not Found", [("Content-Type", "text/plain")])
    return [b"Not Found"]


def seed_demo_project():
    if db_query("SELECT COUNT(*) AS c FROM projects", one=True)["c"] > 0:
        return
    title = "데모: SaaS UX 리서치 설문 분석"
    ts = now()
    db_query("INSERT INTO projects(title,created_at,updated_at) VALUES (?,?,?)", (title, ts, ts))
    pid = db_query("SELECT id FROM projects ORDER BY id DESC LIMIT 1", one=True)["id"]
    fields = [
        "respondent_id","age_group","user_type","region","plan_type","tenure_months","overall_satisfaction","ease_of_use",
        "pricing_satisfaction","support_satisfaction","likelihood_to_recommend","main_goal_achieved","biggest_frustration_text","improvement_request_text"
    ]
    age_groups = ["20대","30대","40대","50대+"]
    user_types = ["실무자","팀장","운영자","개발협업"]
    regions = ["서울","경기","부산","대전","광주"]
    plans = ["Free","Starter","Pro","Enterprise"]
    frustrations = [
        "온보딩이 복잡해서 첫 설정이 어렵습니다",
        "가격이 비싸고 기능 대비 가치가 애매해요",
        "모바일에서 속도가 느려 업무 중 답답합니다",
        "리포트 필터가 불편해서 원하는 데이터 찾기 어려워요",
        "가끔 저장 오류가 나서 신뢰가 떨어집니다",
        "고객지원 답변이 느려서 문제 해결이 지연돼요",
        "알림 설정이 복잡해 중요한 업데이트를 놓칩니다",
        "검색 정확도가 낮아 같은 작업을 반복합니다",
    ]
    improvements = [
        "첫 화면 튜토리얼을 단계별로 단순화해 주세요",
        "요금제별 기능 차이를 더 명확히 보여주세요",
        "모바일 성능 최적화를 해주면 좋겠습니다",
        "자주 쓰는 리포트 템플릿 저장 기능이 필요합니다",
        "오류 발생 시 복구 가이드를 즉시 제공해주세요",
        "채팅 기반 빠른 지원 채널이 있으면 좋겠습니다",
    ]
    rows = []
    random.seed(42)
    for i in range(1, 241):
        plan = random.choices(plans, weights=[0.2,0.35,0.3,0.15])[0]
        tenure = max(1, int(random.gauss(11, 7)))
        ease = max(1, min(5, int(round(random.gauss(3.2 if plan in ["Free","Starter"] else 3.8, 1.0)))))
        price = max(1, min(5, int(round(random.gauss(2.6 if plan=="Free" else 3.3, 0.9)))))
        support = max(1, min(5, int(round(random.gauss(3.1, 0.9)))))
        overall = max(1, min(5, int(round((ease*0.35 + price*0.25 + support*0.2 + random.uniform(0.5,1.3))))))
        nps = max(0, min(10, int(round(overall*2 + random.uniform(-2,2)))))
        goal = "Y" if overall >= 3 else random.choice(["Y","N"])
        fr = random.choice(frustrations)
        imp = random.choice(improvements)
        rows.append({
            "respondent_id": f"R{i:04d}",
            "age_group": random.choice(age_groups),
            "user_type": random.choice(user_types),
            "region": random.choice(regions),
            "plan_type": plan,
            "tenure_months": tenure,
            "overall_satisfaction": overall,
            "ease_of_use": ease,
            "pricing_satisfaction": price,
            "support_satisfaction": support,
            "likelihood_to_recommend": nps,
            "main_goal_achieved": goal,
            "biggest_frustration_text": fr,
            "improvement_request_text": imp,
        })
    csv_path = UPLOAD_DIR / "demo_survey.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    schema = {c: infer_col_type([r[c] for r in rows], c) for c in fields}
    schema["respondent_id"] = "id"
    schema["overall_satisfaction"] = "ordinal"
    schema["likelihood_to_recommend"] = "numeric"
    dpath = DATA_DIR / f"dataset_{pid}.json"
    spath = DATA_DIR / f"schema_{pid}.json"
    apath = DATA_DIR / f"analysis_{pid}.json"
    save_json(dpath, rows)
    save_json(spath, schema)
    save_json(apath, {"config": {"segment_col": "plan_type", "target_col": "overall_satisfaction"}})
    db_query("UPDATE projects SET file_name=?, file_path=?, data_json_path=?, schema_json_path=?, analysis_json_path=?, updated_at=? WHERE id=?",
             ("demo_survey.csv", str(csv_path), str(dpath), str(spath), str(apath), now(), pid))
    ensure_analysis(load_project(pid))


if __name__ == "__main__":
    init_db()
    seed_demo_project()
    print("SIG 서버 실행: http://localhost:8000")
    with make_server("0.0.0.0", 8000, app) as httpd:
        httpd.serve_forever()
