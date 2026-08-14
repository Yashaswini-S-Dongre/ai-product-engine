import streamlit as st
import pandas as pd
import sqlite3
import json
import re
import os
from datetime import datetime
import time
import io
import PyPDF2
import plotly.express as px
import plotly.graph_objects as go
from dotenv import load_dotenv
import bcrypt

# Load prompts
try:
    from prompts import GEMINI_SYSTEM_PROMPT
except ImportError:
    GEMINI_SYSTEM_PROMPT = ""

# Load environment variables
load_dotenv()

# Load Configuration
with open("config.json", "r") as f:
    config = json.load(f)

# Extract config variables
CATEGORY_MAP = config.get("category_map", {})
REGEX_PATTERNS = config.get("regex_patterns", {})
CORE_FIELDS = config.get("core_extraction_fields", [])
BONUS_FIELDS = config.get("bonus_extraction_fields", [])
GEMINI_MODEL = config.get("gemini_model", "gemini-2.0-flash")
UI_COLORS = config.get("ui_colors", {})
CONFIDENCE_BINS = config.get("confidence_bins", [])
DEFAULT_THRESHOLD = config.get("default_threshold", 0.5)
NAVIGATION = config.get("navigation", ["Dashboard", "Ingestion", "Catalog", "System Logs"])
EXPORT_FILES = config.get("export_files", {})

try:
    from google import genai
    GEMINI_AVAILABLE = True
except ImportError:
    GEMINI_AVAILABLE = False

st.set_page_config(page_title="Industrial AI", layout="wide")

# Build dynamic CSS string
css = f"""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap');
    html, body, [class*="css"] {{ font-family: 'Plus Jakarta Sans', sans-serif !important; }}
    html {{ scroll-behavior: smooth; }}
    button, .stButton>button, [data-baseweb="tab"], input, select, textarea {{ cursor: pointer !important; }}
    div[data-testid="stVerticalBlock"] > div[data-testid="stContainer"] {{
        border-radius: 16px !important;
        transition: all 0.3s cubic-bezier(0.16, 1, 0.3, 1) !important;
    }}
    div[data-testid="stVerticalBlock"] > div[data-testid="stContainer"]:hover {{
        border-color: {UI_COLORS.get("hover_border", "rgba(0,0,0,0.1)")} !important;
        box-shadow: 0 10px 30px -10px rgba(0, 0, 0, 0.1) !important;
    }}
    .stButton > button[kind="primary"] {{
        background: linear-gradient(135deg, {UI_COLORS.get("primary_gradient_start", "#000")} 0%, {UI_COLORS.get("primary_gradient_end", "#000")} 100%) !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em !important;
        box-shadow: 0 4px 14px {UI_COLORS.get("hover_shadow", "rgba(0,0,0,0.1)")} !important;
        transition: all 0.25s ease-in-out !important;
    }}
    .stButton > button[kind="primary"]:hover {{
        transform: translateY(-2px) !important;
        box-shadow: 0 6px 20px {UI_COLORS.get("hover_shadow", "rgba(0,0,0,0.2)")} !important;
    }}
    .stButton > button[kind="secondary"] {{
        border-radius: 10px !important;
        font-weight: 500 !important;
        transition: all 0.2s ease-in-out !important;
    }}
    .stButton > button[kind="secondary"]:hover {{
        transform: translateY(-1px) !important;
        border-color: {UI_COLORS.get("primary_gradient_start", "#000")} !important;
        color: {UI_COLORS.get("primary_gradient_start", "#000")} !important;
    }}
    [data-testid="stMetric"] {{ background: transparent; padding: 5px; transition: transform 0.2s ease; }}
    [data-testid="stMetric"]:hover {{ transform: scale(1.02); }}
    [data-testid="stSidebar"] {{ border-right: 1px solid rgba(150, 150, 150, 0.1); }}
    [data-testid="stSidebar"] .stButton > button {{ text-align: left !important; padding-left: 1rem !important; border-radius: 8px !important; }}
    [data-testid="stDataFrame"] {{ border-radius: 12px !important; overflow: hidden !important; border: 1px solid rgba(150, 150, 150, 0.15) !important; }}
    input, textarea {{ border-radius: 10px !important; transition: border-color 0.2s ease, box-shadow 0.2s ease !important; }}
    input:focus, textarea:focus {{ border-color: {UI_COLORS.get("primary_gradient_start", "#000")} !important; box-shadow: 0 0 0 3px {UI_COLORS.get("input_focus_shadow", "rgba(0,0,0,0.1)")} !important; }}
</style>
"""
st.markdown(css, unsafe_allow_html=True)

# 1. DB ARCHITECTURE
DB_PATH = os.environ.get("DB_PATH", "industrial_pim.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password_hash TEXT, created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS catalog (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, sku TEXT, product_name TEXT, manufacturer TEXT,
        category TEXT, extracted_data_json TEXT, overall_confidence REAL, status TEXT, timestamp TEXT,
        UNIQUE(username, sku)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS system_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT, timestamp TEXT, level TEXT, message TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS app_settings (
        key TEXT, username TEXT, value TEXT,
        PRIMARY KEY (key, username)
    )''')

    for table in ["catalog", "system_logs", "app_settings"]:
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN username TEXT")
        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()

# AUTHENTICATION HELPERS
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

def register_user(username, password):
    conn = get_db_connection()
    c = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    hashed_pw = hash_password(password)
    try:
        c.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", (username, hashed_pw, timestamp))
        c.execute("INSERT OR IGNORE INTO app_settings (key, username, value) VALUES (?, ?, ?)", ("conf_threshold", username, str(DEFAULT_THRESHOLD)))
        conn.commit()
        success = True
    except sqlite3.IntegrityError:
        success = False # Username exists
    conn.close()
    return success

def authenticate_user(username, password):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT password_hash FROM users WHERE username = ?", (username,))
    row = c.fetchone()
    conn.close()
    if row and verify_password(password, row['password_hash']):
        return True
    return False

def seed_sample_data(cursor):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open("seed_data.json", "r") as f:
            samples = json.load(f)
            for item in samples:
                sku = item.get("sku")
                product_name = item.get("product_name")
                manufacturer = item.get("manufacturer")
                category = item.get("category")
                data_json = json.dumps(item.get("extracted_data", {}))
                overall_conf = item.get("overall_confidence", 1.0)
                status = item.get("status", "PENDING")
                try:
                    cursor.execute('''INSERT OR IGNORE INTO catalog (sku, product_name, manufacturer, category, extracted_data_json, overall_confidence, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)''', 
                        (sku, product_name, manufacturer, category, data_json, overall_conf, status, ts))
                except sqlite3.IntegrityError:
                    pass
    except Exception as e:
        pass # Handle if seed_data.json doesn't exist

def get_setting(key, default=None):
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT value FROM app_settings WHERE key = ? AND username = ?", (key, username))
    row = c.fetchone()
    conn.close()
    return json.loads(row[0]) if row else default

def save_setting(key, value):
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("INSERT OR REPLACE INTO app_settings (key, username, value) VALUES (?, ?, ?)", (key, username, json.dumps(value)))
    conn.commit()
    conn.close()

def log_event(level, message):
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("INSERT INTO system_logs (username, timestamp, level, message) VALUES (?, ?, ?, ?)", (username, timestamp, level, message))
    conn.commit()
    conn.close()

def fetch_catalog():
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT id, sku, product_name, manufacturer, category, overall_confidence, status, timestamp FROM catalog WHERE username = ? ORDER BY id ASC", conn, params=(username,))
    conn.close()
    if not df.empty:
        df.reset_index(drop=True, inplace=True)
        df.index += 1
        df.insert(0, '#', df.index)
    return df

def delete_catalog_items(skus):
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    placeholders = ','.join(['?']*len(skus))
    c.execute(f"DELETE FROM catalog WHERE LOWER(TRIM(sku)) IN ({placeholders}) AND username = ?", [s.strip().lower() for s in skus] + [username])
    c.execute("SELECT count(*) FROM catalog WHERE username = ?", (username,))
    if c.fetchone()[0] == 0:
        try: c.execute("DELETE FROM sqlite_sequence WHERE name='catalog'")
        except: pass
    conn.commit()
    conn.close()

def sync_catalog_edits(orig_df, edited_df):
    username = st.session_state.get("username", "system")
    conn = None
    try:
        conn = get_db_connection()
        c = conn.cursor()
        
        # Helper to write logs using the SAME connection so we don't lock SQLite
        def tx_log(level, msg):
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            c.execute("INSERT INTO system_logs (username, timestamp, level, message) VALUES (?, ?, ?, ?)", (username, ts, level, msg))

        c.execute("SELECT id FROM catalog WHERE username = ?", (username,))
        db_ids = [int(row[0]) for row in c.fetchall()]
        
        edited_ids = set(int(x) for x in edited_df['id'].dropna())
        orig_view_ids = set(int(x) for x in orig_df['id'].dropna())
        deleted_ids = (set(db_ids) & orig_view_ids) - edited_ids
        
        for d_id in deleted_ids:
            c.execute("SELECT sku FROM catalog WHERE id = ? AND username = ?", (d_id, username))
            del_sku = c.fetchone()
            del_sku_str = del_sku[0] if del_sku else str(d_id)
            c.execute("DELETE FROM catalog WHERE id = ? AND username = ?", (d_id, username))
            tx_log("WARNING", f"AUDIT: Deleted SKU '{del_sku_str}'")
            
        c.execute("SELECT count(*) FROM catalog WHERE username = ?", (username,))
        if c.fetchone()[0] == 0:
            try: c.execute("DELETE FROM sqlite_sequence WHERE name='catalog'")
            except: pass
            
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for idx, row in edited_df.iterrows():
            sku = str(row.get('sku', '')).strip()
            name = str(row.get('product_name', '')).strip()
            mfr = str(row.get('manufacturer', '')).strip()
            category = str(row.get('category', '')).strip()
            status = str(row.get('status', 'MANUAL')).strip()
            
            if pd.isna(row.get('id')):
                c.execute('''INSERT INTO catalog (username, sku, product_name, manufacturer, category, extracted_data_json, overall_confidence, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (username, sku, name, mfr, category, '{}', 1.0, status, timestamp))
                tx_log("INFO", f"AUDIT: Created new SKU '{sku}' manually.")
            else:
                row_id = int(row['id'])
                orig_row = orig_df[orig_df['id'] == row_id].iloc[0]
                changes = []
                for field in ['sku', 'product_name', 'manufacturer', 'category', 'status']:
                    old_val = str(orig_row.get(field, '')).strip()
                    new_val = str(row.get(field, '')).strip()
                    if old_val != new_val:
                        changes.append(f"{field}: '{old_val}' -> '{new_val}'")
                if changes:
                    c.execute('''UPDATE catalog SET sku = ?, product_name = ?, manufacturer = ?, category = ?, status = ? WHERE id = ? AND username = ?''', (sku, name, mfr, category, status, row_id, username))
                    tx_log("INFO", f"AUDIT: Updated SKU '{sku}' - {', '.join(changes)}")
                    
        conn.commit()
        
    except Exception as e:
        if conn:
            conn.rollback() # Undo the broken transaction completely
            conn.close()    # Release the database lock immediately!
            conn = None     # Prevent 'finally' block from erroring out
            
        # Now that the lock is released, we can safely use the external log_event
        log_event("ERROR", f"Database error during sync_catalog_edits: {str(e)}")
        st.error(f"Failed to save changes (Likely a duplicate SKU): {str(e)}")
        
    finally:
        if conn:
            conn.close()

def fetch_logs():
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    df = pd.read_sql_query("SELECT id, timestamp, level, message FROM system_logs WHERE username = ? ORDER BY timestamp DESC", conn, params=(username,))
    conn.close()
    return df

def delete_log(log_id):
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM system_logs WHERE id = ? AND username = ?", (log_id, username))
    conn.commit()
    conn.close()

def delete_all_logs():
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM system_logs WHERE username = ?", (username,))
    conn.commit()
    conn.close()

def nuke_database():
    username = st.session_state.get("username", "system")
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("DELETE FROM catalog WHERE username = ?", (username,))
    c.execute("DELETE FROM system_logs WHERE username = ?", (username,))
    c.execute("DELETE FROM app_settings WHERE username = ?", (username,))
    conn.commit()
    conn.close()

init_db()

if not st.session_state.get("authenticated"):
    st.markdown("<h2 style='text-align: center; margin-bottom: 2rem;'>System Intelligence | Authenticate</h2>", unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.container(border=True):
            tab1, tab2 = st.tabs(["Log In", "Sign Up"])
            with tab1:
                login_user = st.text_input("Username", key="login_user")
                login_pass = st.text_input("Password", type="password", key="login_pass")
                if st.button("Log In", type="primary", use_container_width=True):
                    if authenticate_user(login_user, login_pass):
                        st.session_state["authenticated"] = True
                        st.session_state["username"] = login_user
                        st.rerun()
                    else:
                        st.error("Invalid username or password.")
            with tab2:
                reg_user = st.text_input("Username", key="reg_user")
                reg_pass = st.text_input("Password", type="password", key="reg_pass")
                if st.button("Create Account", type="primary", use_container_width=True):
                    if reg_user and reg_pass:
                        if register_user(reg_user, reg_pass):
                            st.success("Account created successfully! You can now log in.")
                        else:
                            st.error("Username already exists.")
                    else:
                        st.error("Please fill out both fields.")

    for key in ["settings", "gemini_api_key", "ingestion_results", "batch_results", "deleted_sku_cache", "deleted_log_cache"]:
        if key in st.session_state: del st.session_state[key]
    st.stop()

# 2. STATE MANAGEMENT
if st.session_state.get("authenticated"):
    if "current_page" not in st.session_state: st.session_state.current_page = NAVIGATION[0]
    if "ingestion_results" not in st.session_state: st.session_state.ingestion_results = None
    if "settings" not in st.session_state: st.session_state.settings = {"conf_threshold": get_setting("conf_threshold", DEFAULT_THRESHOLD)}
    if "gemini_api_key" not in st.session_state:
        st.session_state.gemini_api_key = os.environ.get("GEMINI_API_KEY") or get_setting("gemini_api_key", "")

# 3. SIDEBAR NAVIGATION
with st.sidebar:
    st.markdown(f"<h2 style='color: {UI_COLORS.get('primary_gradient_start', '#2563EB')}; margin-bottom: 0;'>Industrial AI</h2>", unsafe_allow_html=True)
    if st.button('Logout', use_container_width=True):
        st.session_state["authenticated"] = False
        st.rerun()
    st.markdown(f"<hr style='border-color: rgba(150,150,150,0.2);'>", unsafe_allow_html=True)
    for page in NAVIGATION:
        if st.button(f"{page}", key=f"nav_{page}", use_container_width=True, type="primary" if st.session_state.current_page == page else "secondary"):
            st.session_state.current_page = page
            st.rerun()
    st.markdown("<br><br>", unsafe_allow_html=True)
    if st.button("Settings", key="nav_Settings", use_container_width=True, type="primary" if st.session_state.current_page == "Settings" else "secondary"):
        st.session_state.current_page = "Settings"
        st.rerun()

# 4. GLOBAL TOP BAR
st.markdown(f"<div style='display: flex; align-items: center;'><span style='font-weight: 700; font-size: 1.2rem; letter-spacing: -0.02em;'>System Intelligence</span></div>", unsafe_allow_html=True)
st.markdown(f"<hr style='margin-bottom: 2rem; border-color: rgba(150,150,150,0.15);'>", unsafe_allow_html=True)

# 5. DATA EXTRACTION ALGORITHM
def parse_document(text):
    results, confidences = {}, {}
    for key, pattern in REGEX_PATTERNS.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            results[key] = match.group(1).strip()
            confidences[key] = 1.0
        else:
            results[key] = "Not Found"
            confidences[key] = 0.0
    found = sum(1 for v in results.values() if v != "Not Found")
    return results, confidences, (found / len(REGEX_PATTERNS))

def classify_category(text):
    text_lower = text.lower()
    for keyword, category in CATEGORY_MAP.items():
        if keyword in text_lower: return category
    return "Industrial Equipment"

def parse_document_ai(text):
    api_key = st.session_state.get("gemini_api_key", "")
    if not api_key or not GEMINI_AVAILABLE: return parse_document(text)
    try:
        client = genai.Client(api_key=api_key)
        prompt = GEMINI_SYSTEM_PROMPT.format(text=text)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
        response_text = response.text.strip()
        if "```json" in response_text: response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text: response_text = response_text.split("```")[1].split("```")[0].strip()
        ai_data = json.loads(response_text)
        
        results, confidences = {}, {}
        for field in CORE_FIELDS:
            val = ai_data.get(field, "Not Found")
            if val and val != "Not Found":
                results[field] = str(val).strip()
                confidences[field] = 1.0
            else:
                results[field] = "Not Found"
                confidences[field] = 0.0
        for b_field in BONUS_FIELDS:
            val = ai_data.get(b_field, "Not Found")
            if val and val != "Not Found":
                results[b_field] = str(val).strip()
                confidences[b_field] = 1.0
        found = sum(1 for v in results.values() if v != "Not Found")
        total = len(results)
        return results, confidences, (found / total if total > 0 else 0)
    except Exception as e:
        log_event("WARNING", f"Gemini AI extraction failed: {str(e)}. Falling back to regex.")
        return parse_document(text)

def style_status(val):
    if val == "AI ENRICHED":
        return f"background-color: {UI_COLORS.get('status_ai_enriched_bg', '#e0e0e0')}; color: {UI_COLORS.get('status_ai_enriched_text', '#000')}; border-radius: 9999px; font-weight: 700; text-align: center; padding: 4px 12px;"
    elif val == "PENDING":
        return f"background-color: {UI_COLORS.get('status_pending_bg', '#e0e0e0')}; color: {UI_COLORS.get('status_pending_text', '#000')}; border-radius: 9999px; font-weight: 700; text-align: center; padding: 4px 12px;"
    return ''

# 6. PAGE RENDERING
if st.session_state.current_page == "Dashboard":
    st.header("Welcome to Industrial AI")
    df = fetch_catalog()
    logs_df = fetch_logs()
    
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        with st.container(border=True): st.metric("Total Assets in PIM", len(df))
    with c2:
        with st.container(border=True):
            avg_conf = df['overall_confidence'].mean() if not df.empty and 'overall_confidence' in df.columns else 0
            st.metric("Average Confidence", f"{avg_conf*100:.1f}%" if len(df)>0 else "0%")
    with c3:
        with st.container(border=True):
            enriched = len(df[df['status'] == 'AI ENRICHED']) if not df.empty and 'status' in df.columns else 0
            st.metric("AI Enriched", enriched)
    with c4:
        with st.container(border=True): st.metric("System Events", len(logs_df))
    
    if not df.empty:
        chart1, chart2 = st.columns(2)
        with chart1:
            with st.container(border=True):
                st.subheader("Confidence Distribution")
                if 'overall_confidence' in df.columns:
                    conf_vals = df['overall_confidence']
                    counts = [len(conf_vals[conf_vals < 0.5]), len(conf_vals[(conf_vals >= 0.5) & (conf_vals <= 0.8)]), len(conf_vals[conf_vals > 0.8])]
                    fig = go.Figure(data=[go.Bar(x=CONFIDENCE_BINS, y=counts, marker_color=[UI_COLORS.get("chart_red"), UI_COLORS.get("chart_orange"), UI_COLORS.get("chart_green")], text=counts, textposition="auto")])
                    fig.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20), yaxis_title="Count", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig, use_container_width=True)
        with chart2:
            with st.container(border=True):
                st.subheader("Status Breakdown")
                if 'status' in df.columns:
                    status_counts = df['status'].value_counts()
                    fig2 = go.Figure(data=[go.Pie(labels=status_counts.index, values=status_counts.values, hole=0.5, marker_colors=[UI_COLORS.get("chart_green"), UI_COLORS.get("chart_orange"), UI_COLORS.get("chart_blue"), UI_COLORS.get("chart_purple")])])
                    fig2.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=True)
                    st.plotly_chart(fig2, use_container_width=True)
        chart3, chart4 = st.columns(2)
        with chart3:
            with st.container(border=True):
                st.subheader("Top Manufacturers")
                if 'manufacturer' in df.columns:
                    mfr_counts = df['manufacturer'].value_counts().head(8)
                    fig3 = go.Figure(data=[go.Bar(x=mfr_counts.values, y=mfr_counts.index, orientation='h', marker_color=UI_COLORS.get("primary_gradient_start"), text=mfr_counts.values, textposition="auto")])
                    fig3.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20), xaxis_title="Count", plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig3, use_container_width=True)
        with chart4:
            with st.container(border=True):
                st.subheader("Category Distribution")
                if 'category' in df.columns:
                    cat_counts = df['category'].value_counts()
                    fig4 = go.Figure(data=[go.Pie(labels=cat_counts.index, values=cat_counts.values, hole=0.4, marker_colors=px.colors.qualitative.Set3)])
                    fig4.update_layout(height=300, margin=dict(l=20, r=20, t=20, b=20), plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", showlegend=True)
                    st.plotly_chart(fig4, use_container_width=True)
                    
    with st.container(border=True):
        st.subheader("Recent Activity")
        if not logs_df.empty:
            for _, row in logs_df.head(5).iterrows():
                color = UI_COLORS.get("chart_blue") if row['level'] == "INFO" else UI_COLORS.get("chart_orange") if row['level'] == "WARNING" else UI_COLORS.get("chart_red")
                st.markdown(f"<span style='color: {color}; font-weight: 700;'>[{row['level']}]</span> {row['message']} — <span style='opacity: 0.6;'>{row['timestamp']}</span>", unsafe_allow_html=True)
        else:
            st.info("No activity recorded yet.")

elif st.session_state.current_page == "Ingestion":
    st.header("Extraction & Enrichment")
    
    api_key = st.session_state.get("gemini_api_key", "")
    if api_key and GEMINI_AVAILABLE:
        st.success("Extraction Mode: Gemini AI (Powered by Google)")
    
    if "batch_results" not in st.session_state: st.session_state.batch_results = []
    
    c1, c2 = st.columns([1, 1], gap="large")
    with c1:
        with st.container(border=True):
            st.subheader("INPUT SOURCE (BATCH UPLOAD)")
            uploaded_files = st.file_uploader("Upload technical PDFs or TXTs", type=["pdf", "txt"], accept_multiple_files=True, label_visibility="collapsed")
            pasted_text = st.text_area("Or paste raw text here (Single item):", height=150)
            if st.button("PROCESS DOCUMENTS", type="primary", use_container_width=True, key="btn_doc"):
                try:
                    new_results = []
                    if uploaded_files:
                        progress_bar = st.progress(0, text="Starting batch processing...")
                        total_files = len(uploaded_files)
                        for i, f in enumerate(uploaded_files):
                            progress_bar.progress((i) / total_files, text=f"Analyzing {f.name} ({i+1}/{total_files})...")
                            text_content = ""
                            if f.name.endswith(".pdf"):
                                reader = PyPDF2.PdfReader(f)
                                for page in reader.pages:
                                    pt = page.extract_text()
                                    if pt: text_content += pt + "\n"
                            else:
                                text_content = f.read().decode("utf-8")
                            if text_content.strip():
                                res, confs, overall = parse_document_ai(text_content)
                                auto_cat = res.get('category', classify_category(text_content))
                                new_results.append({"filename": f.name, "sku": res.get("sku", "Not Found"), "product_name": res.get("product_name", "Not Found"), "manufacturer": res.get("manufacturer", "Not Found"), "category": auto_cat, "overall_confidence": overall, "raw_data": res, "push_ready": overall >= st.session_state.settings["conf_threshold"]})
                        progress_bar.progress(1.0, text="Batch processing complete!")
                        time.sleep(0.5)
                        progress_bar.empty()
                    elif pasted_text.strip():
                        with st.spinner("Analyzing pasted text..."):
                            res, confs, overall = parse_document_ai(pasted_text)
                            auto_cat = res.get('category', classify_category(pasted_text))
                            new_results.append({"filename": "Pasted Text", "sku": res.get("sku", "Not Found"), "product_name": res.get("product_name", "Not Found"), "manufacturer": res.get("manufacturer", "Not Found"), "category": auto_cat, "overall_confidence": overall, "raw_data": res, "push_ready": overall >= st.session_state.settings["conf_threshold"]})
                    else:
                        st.error("Please upload documents or paste raw text.")
                    if new_results:
                        st.session_state.batch_results.extend(new_results)
                        st.rerun()
                except Exception as e:
                    st.error(f"Error parsing input: {e}")
                    log_event("ERROR", f"Failed to parse input: {str(e)}")

    with c2:
        with st.container(border=True):
            st.subheader("BATCH REVIEW & PUSH")
            if st.session_state.batch_results:
                st.write(f"**{len(st.session_state.batch_results)} items pending review.**")
                df_batch = pd.DataFrame(st.session_state.batch_results)
                edited_batch = st.data_editor(df_batch, column_config={"filename": st.column_config.TextColumn("Source"), "sku": "SKU", "product_name": "Product Name", "manufacturer": "Manufacturer", "category": "Category", "overall_confidence": st.column_config.ProgressColumn("Confidence", format="%.2f", min_value=0, max_value=1), "raw_data": None, "push_ready": st.column_config.CheckboxColumn("Approve", help="Check to approve for push")}, disabled=["filename", "overall_confidence"], hide_index=True, use_container_width=True, num_rows="dynamic", key="batch_editor")
                if st.button("Push Approved to PIM", type="primary", use_container_width=True):
                    approved_rows = [row for _, row in edited_batch.iterrows() if row.get("push_ready") == True]
                    if not approved_rows: st.warning("No items approved for push.")
                    else:
                        conn = get_db_connection()
                        success_count, dup_count = 0, 0
                        username = st.session_state.get("username", "system")
                        for row in approved_rows:
                            sku_val = str(row['sku']).strip()
                            if not pd.read_sql_query("SELECT id FROM catalog WHERE LOWER(sku) = ? AND username = ?", conn, params=(sku_val.lower(), username)).empty:
                                dup_count += 1
                                log_event("WARNING", f"Duplicate SKU '{sku_val}' blocked during batch ingestion.")
                                continue
                            data = row.get('raw_data', {})
                            if isinstance(data, str):
                                try:
                                    data = json.loads(data)
                                except Exception:
                                    data = {}
                            if not isinstance(data, dict):
                                data = {}
                            data['sku'], data['product_name'], data['manufacturer'], data['category'] = sku_val, row['product_name'], row['manufacturer'], row['category']
                            try:
                                conn.cursor().execute('''INSERT INTO catalog (username, sku, product_name, manufacturer, category, extracted_data_json, overall_confidence, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (username, sku_val, row['product_name'], row['manufacturer'], row['category'], json.dumps(data), row['overall_confidence'], "AI ENRICHED", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                                success_count += 1
                            except Exception as e: log_event("ERROR", f"Batch insert failed for {sku_val}: {e}")
                        conn.commit()
                        conn.close()
                        if success_count > 0: st.toast(f"Successfully pushed {success_count} items to PIM!")
                        if dup_count > 0: st.warning(f"Skipped {dup_count} duplicate SKUs.")
                        st.session_state.batch_results = [r for r in st.session_state.batch_results if not r.get("push_ready")]
                        time.sleep(1)
                        st.rerun()
                if st.button("Clear Batch", type="secondary"):
                    st.session_state.batch_results = []
                    st.rerun()
            else:
                st.info("Upload documents to start batch extraction.")

elif st.session_state.current_page == "Catalog":
    st.header("Product Catalog")
    df = fetch_catalog()
    with st.container(border=True):
        st.subheader("CATALOG MANAGEMENT")
        with st.expander("🔍 Search & Filter Options", expanded=False):
            f1, f2, f3 = st.columns(3)
            with f1: search_query = st.text_input("Search (SKU, Name, Mfr)...", key="catalog_search")
            with f2: status_filter = st.multiselect("Filter by Status", options=["AI ENRICHED", "PENDING", "MANUAL"], default=[])
            with f3: conf_filter = st.slider("Min Confidence", 0.0, 1.0, 0.0, 0.1)
            if not df.empty:
                if search_query: df = df[df.apply(lambda row: search_query.lower() in str(row.values).lower(), axis=1)]
                if status_filter: df = df[df['status'].isin(status_filter)]
                if 'overall_confidence' in df.columns and conf_filter > 0: df = df[df['overall_confidence'] >= conf_filter]
        
        t1, t2, t3, t4, t5 = st.columns([2, 2, 2, 3, 1])
        with t1:
            csv = df.to_csv(index=False) if not df.empty else ""
            st.download_button("📥 Export CSV", data=csv, file_name=EXPORT_FILES.get("csv", "catalog.csv"), mime="text/csv", disabled=df.empty, use_container_width=True)
        with t2:
            json_str = df.to_json(orient="records") if not df.empty else ""
            st.download_button("📥 Export JSON", data=json_str, file_name=EXPORT_FILES.get("json", "catalog.json"), mime="application/json", disabled=df.empty, use_container_width=True)
        with t3:
            excel_data = ""
            if not df.empty:
                output = io.BytesIO()
                with pd.ExcelWriter(output, engine='openpyxl') as writer: df.to_excel(writer, index=False, sheet_name='Catalog')
                excel_data = output.getvalue()
            st.download_button("📥 Export Excel", data=excel_data, file_name=EXPORT_FILES.get("excel", "catalog.xlsx"), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", disabled=df.empty, use_container_width=True)
        with t4:
            target_sku = st.text_input("Enter exact SKU to delete:", key="delete_sku_target", placeholder="Target SKU to delete...", label_visibility="collapsed")
        with t5:
            if "confirm_delete_active" not in st.session_state: st.session_state.confirm_delete_active = False
            if st.button("Delete", key="trigger_delete_btn", type="secondary", use_container_width=True):
                if target_sku:
                    st.session_state.confirm_delete_active = True
                    st.session_state.target_sku_to_delete = target_sku
                else:
                    st.warning("Please enter a valid SKU first.")

        st.markdown("<br>", unsafe_allow_html=True)
        if st.session_state.get("confirm_delete_active", False):
            sku_to_del = st.session_state.target_sku_to_delete
            st.error(f"Are you sure you want to delete SKU: **{sku_to_del}**?")
            d1, d2 = st.columns(2)
            with d1:
                if st.button("YES, DELETE PERMANENTLY", type="primary", use_container_width=True):
                    conn = get_db_connection()
                    username = st.session_state.get("username", "system")
                    row_to_del = pd.read_sql_query("SELECT * FROM catalog WHERE LOWER(TRIM(sku)) = ? AND username = ?", conn, params=(sku_to_del.strip().lower(), username))
                    conn.close()
                    if not row_to_del.empty:
                        st.session_state.deleted_sku_cache = row_to_del
                        st.session_state.deleted_sku_timestamp = time.time()
                        delete_catalog_items([sku_to_del])
                        log_event("WARNING", f"User deleted SKU: {sku_to_del}")
                        st.toast(f"Deleted SKU: {sku_to_del}")
                    else:
                        st.warning(f"SKU '{sku_to_del}' not found in catalog.")
                    st.session_state.confirm_delete_active = False
                    st.rerun()
            with d2:
                if st.button("CANCEL", use_container_width=True):
                    st.session_state.confirm_delete_active = False
                    st.rerun()

        if "deleted_sku_cache" in st.session_state and st.session_state.deleted_sku_cache is not None:
            time_since_del = time.time() - st.session_state.deleted_sku_timestamp
            if time_since_del < 10:
                st.info(f"SKU deleted. You have {int(10 - time_since_del)} seconds to undo.")
                if st.button("UNDO DELETION", key="undo_sku_btn", use_container_width=True):
                    conn = get_db_connection()
                    row = st.session_state.deleted_sku_cache.iloc[0]
                    c = conn.cursor()
                    try:
                        username = st.session_state.get("username", "system")
                        c.execute('''INSERT INTO catalog (username, sku, product_name, manufacturer, category, extracted_data_json, overall_confidence, status, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''', (username, row['sku'], row['product_name'], row['manufacturer'], row['category'], row['extracted_data_json'], row['overall_confidence'], row['status'], row['timestamp']))
                        conn.commit()
                        log_event("INFO", f"Undo: Restored SKU: {row['sku']}")
                        st.toast("Restored successfully!")
                    except Exception as e:
                        st.error(f"Failed to restore: {e}")
                    conn.close()
                    st.session_state.deleted_sku_cache = None
                    st.rerun()
                time.sleep(1)
                st.rerun()
            else:
                st.session_state.deleted_sku_cache = None

        if not df.empty:
            st.markdown("<br>", unsafe_allow_html=True)
            editable_df = df.copy()
            styled_df = editable_df.style.map(style_status, subset=['status'])
            edited_df = st.data_editor(
                styled_df,
                column_config={
                    "#": st.column_config.NumberColumn(),
                    "id": None,
                    "overall_confidence": st.column_config.ProgressColumn("Confidence", format="%.2f", min_value=0, max_value=1),
                    "timestamp": st.column_config.TextColumn("Last Updated"),
                    "sku": st.column_config.TextColumn("SKU", required=True),
                    "status": st.column_config.SelectboxColumn("Status", options=["AI ENRICHED", "PENDING", "MANUAL"], required=True)
                },
                disabled=["#", "overall_confidence", "timestamp"],
                hide_index=True,
                use_container_width=True,
                num_rows="dynamic",
                key="catalog_editor"
            )
            if st.button("Save Catalog Changes", type="primary", use_container_width=True):
                sync_catalog_edits(df, edited_df)
                st.toast("Changes saved successfully!")
                time.sleep(0.5)
                st.rerun()
        else:
            st.info("The catalog is currently empty.")

elif st.session_state.current_page == "System Logs":
    st.header("System Logs & Audit Trail")
    logs_df = fetch_logs()
    
    with st.container(border=True):
        st.subheader("SYSTEM LOGS")
        
        # Flush Button
        col_flush, _ = st.columns([2, 8])
        with col_flush:
            if st.button("Flush All Logs", type="secondary", use_container_width=True):
                delete_all_logs()
                st.toast("All logs flushed.")
                time.sleep(0.5)
                st.rerun()

        # Undo Deletion Logic
        if "deleted_log_cache" in st.session_state and st.session_state.deleted_log_cache is not None:
            time_since = time.time() - st.session_state.deleted_log_timestamp
            if time_since < 10:
                st.info(f"Log deleted. {int(10 - time_since)} seconds to undo.")
                if st.button("UNDO LOG DELETION", use_container_width=True):
                    conn = get_db_connection()
                    c = conn.cursor()
                    row = st.session_state.deleted_log_cache
                    username = st.session_state.get("username", "system")
                    c.execute("INSERT INTO system_logs (username, timestamp, level, message) VALUES (?, ?, ?, ?)", (username, row['timestamp'], row['level'], row['message']))
                    conn.commit()
                    conn.close()
                    st.session_state.deleted_log_cache = None
                    st.toast("Log restored!")
                    time.sleep(0.5)
                    st.rerun()
                time.sleep(1)
                st.rerun()
            else:
                st.session_state.deleted_log_cache = None
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Interactive Row-by-Row Table
        if not logs_df.empty:
            h1, h2, h3, h4 = st.columns([2, 1, 6, 1])
            h1.markdown("<span style='font-size: 0.85rem; font-weight: 600; opacity: 0.7;'>TIMESTAMP</span>", unsafe_allow_html=True)
            h2.markdown("<span style='font-size: 0.85rem; font-weight: 600; opacity: 0.7;'>LEVEL</span>", unsafe_allow_html=True)
            h3.markdown("<span style='font-size: 0.85rem; font-weight: 600; opacity: 0.7;'>MESSAGE</span>", unsafe_allow_html=True)
            h4.markdown("<span style='font-size: 0.85rem; font-weight: 600; opacity: 0.7;'>ACTION</span>", unsafe_allow_html=True)
            st.markdown(f"<hr style='margin: 0.5rem 0; border-color: rgba(150,150,150,0.15);'>", unsafe_allow_html=True)
            
            for idx, row in logs_df.iterrows():
                lc1, lc2, lc3, lc4 = st.columns([2, 1, 6, 1], vertical_alignment="center")
                
                with lc1:
                    st.markdown(f"<span style='font-size: 0.85rem; font-weight: 500;'>{row['timestamp']}</span>", unsafe_allow_html=True)
                with lc2:
                    color = UI_COLORS.get("chart_blue", "#3B82F6") if row['level'] == "INFO" else UI_COLORS.get("chart_orange", "#F59E0B") if row['level'] == "WARNING" else UI_COLORS.get("chart_red", "#EF4444")
                    st.markdown(f"<span style='color: {color}; font-weight: 700; font-size: 0.85rem;'>{row['level']}</span>", unsafe_allow_html=True)
                with lc3:
                    st.markdown(f"<span style='font-size: 0.9rem;'>{row['message']}</span>", unsafe_allow_html=True)
                with lc4:
                    if st.button("Delete", key=f"del_log_{row['id']}", use_container_width=True):
                        st.session_state.deleted_log_cache = row
                        st.session_state.deleted_log_timestamp = time.time()
                        delete_log(row['id'])
                        st.toast("Log deleted.")
                        time.sleep(0.5)
                        st.rerun()
                        
                st.markdown(f"<hr style='margin: 0.5rem 0; border-color: rgba(150,150,150,0.1);'>", unsafe_allow_html=True)
        else:
            st.info("System logs are clear.")

elif st.session_state.current_page == "Settings":
    st.header("Application Settings")
    
    with st.container(border=True):
        st.subheader("Global Settings")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**AI Extraction Rules**")
            new_threshold = st.slider(
                "Minimum Confidence Threshold for Auto-Approval", 
                min_value=0.0, max_value=1.0, 
                value=float(st.session_state.settings["conf_threshold"]), 
                step=0.05
            )
            
            if st.button("Save Settings", type="primary"):
                st.session_state.settings["conf_threshold"] = new_threshold
                save_setting("conf_threshold", new_threshold)
                
                log_event("INFO", f"Settings updated. Threshold: {new_threshold}")
                st.toast("Settings saved securely to database!")
        with c2:
            st.markdown("**Danger Zone**")
            st.warning("These actions cannot be undone.")
            if "confirm_nuke" not in st.session_state:
                st.session_state.confirm_nuke = False
                
            if st.button("Nuke Database", type="secondary"):
                st.session_state.confirm_nuke = True
                
            if st.session_state.confirm_nuke:
                st.error("This will delete ALL data. Are you sure?")
                n1, n2 = st.columns(2)
                with n1:
                    if st.button("YES, NUKE IT", type="primary", use_container_width=True):
                        nuke_database()
                        st.session_state.confirm_nuke = False
                        st.toast("Database reset to factory settings.")
                        time.sleep(0.5)
                        st.rerun()
                with n2:
                    if st.button("CANCEL", use_container_width=True):
                        st.session_state.confirm_nuke = False
                        st.rerun()

# 7. FOOTER
st.markdown("<br><hr style='border-color: rgba(150,150,150,0.15);'>", unsafe_allow_html=True)
st.markdown("<div style='text-align: center; font-size: 0.85rem; opacity: 0.6;'>Industrial AI Platform v1.1.0 | Enterprise Edition</div>", unsafe_allow_html=True)
