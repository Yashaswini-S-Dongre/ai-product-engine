# Industrial AI Platform

A production-ready, fully functional Industrial AI application built with Streamlit and SQLite. This platform processes unstructured telemetry and specification documents (PDF/TXT) into a unified structured catalog using **Gemini AI** data extraction.

https://ai-catalog.streamlit.app

## Key Features

- **Google Gemini AI Extraction**: Intelligently parses complex technical documents (PDFs/TXTs) into structured JSON formats. Includes a robust Regex fallback mode.
- **Bulk Document Processing**: Upload and process multiple technical documents simultaneously. Review and edit batch results in an interactive data editor before committing to the database.
- **Advanced Plotly Dashboard**: A visually rich command center featuring interactive KPI charts (Confidence Distribution, Status Breakdown, Manufacturer Stats) and a real-time activity feed.
- **Duplicate Detection & Audit Trail**: Enterprise-grade data integrity checks prevent duplicate SKUs from being ingested. Full audit trails track exact field-level changes made in the Catalog.
- **PIM Backend**: Local SQLite database (`industrial_pim.db`) managing the `catalog`, `system_logs`, and persistent `app_settings` tables.
- **Advanced Catalog Management**: Deep search capabilities, multi-select status filtering, and confidence threshold sliders. Export the live catalog instantly to **CSV**, **JSON**, or **Excel (.xlsx)**.
- **Data Protection & Undo Mechanics**: Includes a mandatory Two-Step Deletion Confirmation flow for catalog items and logs, and a 10-Second Undo grace period to prevent accidental data loss.
- **Configuration & Security Decoupled**: Hardcoded values and logic mappings have been moved to `config.json`. API Keys are securely managed via a hidden `.env` file backend, keeping the UI secure.
- **Professional UI**: Custom Streamlit configuration and structural formatting for a sleek, corporate-grade aesthetic (clean layouts, loading spinners, and non-blocking `st.toast` notifications).

## Setup & Installation

1. **Prerequisites**: Ensure you have Python 3.9+ installed.
2. **Virtual Environment**:
   ```bash
   python3 -m venv venv
   source venv/bin/activate
   ```
3. **Install Dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
4. **Environment Setup**:
   Create a `.env` file in the root directory (you can use `.env.example` as a template) and add your database configuration and Gemini API Key:
   ```plaintext
   DB_PATH=industrial_pim.db
   GEMINI_API_KEY=your_google_ai_studio_api_key_here
   ```

## Usage

Start the Streamlit application:

```bash
streamlit run app.py
```

### Application Modules

1. **Dashboard**: High-level metrics on the PIM catalog and system health, powered by Plotly graphs.
2. **Ingestion (Batch AI)**: Upload multiple technical spec sheets. The system uses Gemini AI to extract attributes and auto-classify categories. Review, edit, and approve items in bulk before pushing to the central catalog.
3. **Catalog**: View and edit the live asset catalog via a seamless data editor. Features advanced search/filtering, multi-format export (CSV, JSON, Excel), and a targeted SKU deletion tool equipped with a 10-second undo buffer.
4. **System Logs**: Diagnose system events and view the detailed Audit Trail using an interactive row-by-row layout with inline actions.
5. **Settings**: Modify the persistent confidence threshold required for ingestion, and safely perform database resets. (API Keys are managed securely via the backend `.env` file).

## Technologies Used

- **Frontend**: Streamlit, Plotly
- **Backend**: Python 3, SQLite3
- **Data Processing**: Pandas, PyPDF2, Regex
- **AI / LLM**: Google Gemini (`google-genai` SDK)
- **Configuration**: JSON, python-dotenv
