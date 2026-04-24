# ============================================================
# app.py — FastAPI Web App
# Shows ticket data in a clean browser table
# Run: uvicorn app:app --reload
# ============================================================

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
import psycopg2
import logging
from config import PG_HOST, PG_DATABASE, PG_USER, PG_PASSWORD, PG_PORT

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Autotask Ticket Viewer")


# ─── DB Connection ────────────────────────────────────────────
def get_db_connection():
    return psycopg2.connect(
        host=PG_HOST,
        database=PG_DATABASE,
        user=PG_USER,
        password=PG_PASSWORD,
        port=PG_PORT,
        sslmode="require"
    )


# ─── API: Get All Tickets (JSON) ─────────────────────────────
@app.get("/tickets")
def get_tickets():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticket_id, title, description, resolution_notes, synced_at
            FROM autotask_tickets
            ORDER BY ticket_id DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        tickets = [
            {
                "ticket_id":        row[0],
                "title":            row[1],
                "description":      row[2],
                "resolution_notes": row[3],
                "synced_at":        str(row[4])
            }
            for row in rows
        ]
        return {"total": len(tickets), "tickets": tickets}

    except Exception as e:
        logger.error(f"DB error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Web UI: Show Tickets in HTML Table ──────────────────────
@app.get("/", response_class=HTMLResponse)
def show_tickets_ui():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticket_id, title, description, resolution_notes, synced_at
            FROM autotask_tickets
            ORDER BY ticket_id DESC
        """)
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        rows_html = ""
        for row in rows:
            ticket_id, title, desc, resolution, synced = row
            rows_html += f"""
            <tr>
                <td>{ticket_id}</td>
                <td>{title or '—'}</td>
                <td>{(desc or '')[:200]}{'...' if desc and len(desc) > 200 else ''}</td>
                <td>{(resolution or '')[:200]}{'...' if resolution and len(resolution) > 200 else ''}</td>
                <td>{synced}</td>
            </tr>
            """

        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Autotask Tickets</title>
            <style>
                body {{
                    font-family: 'Segoe UI', sans-serif;
                    background: #f0f2f5;
                    padding: 30px;
                    color: #333;
                }}
                h1 {{
                    color: #0078d4;
                    margin-bottom: 5px;
                }}
                .subtitle {{
                    color: #666;
                    margin-bottom: 20px;
                    font-size: 14px;
                }}
                .badge {{
                    background: #0078d4;
                    color: white;
                    padding: 4px 12px;
                    border-radius: 20px;
                    font-size: 13px;
                    margin-bottom: 20px;
                    display: inline-block;
                }}
                table {{
                    width: 100%;
                    border-collapse: collapse;
                    background: white;
                    border-radius: 10px;
                    overflow: hidden;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.08);
                }}
                thead {{
                    background: #0078d4;
                    color: white;
                }}
                th {{
                    padding: 14px 16px;
                    text-align: left;
                    font-weight: 600;
                    font-size: 14px;
                }}
                td {{
                    padding: 12px 16px;
                    font-size: 13px;
                    border-bottom: 1px solid #f0f0f0;
                    vertical-align: top;
                    max-width: 300px;
                    word-wrap: break-word;
                }}
                tr:hover td {{
                    background: #f7fbff;
                }}
                tr:last-child td {{
                    border-bottom: none;
                }}
                .ticket-id {{
                    font-weight: bold;
                    color: #0078d4;
                }}
                .search-bar {{
                    margin-bottom: 20px;
                }}
                .search-bar input {{
                    padding: 10px 16px;
                    border: 1px solid #ddd;
                    border-radius: 8px;
                    font-size: 14px;
                    width: 300px;
                    outline: none;
                }}
                .search-bar input:focus {{
                    border-color: #0078d4;
                    box-shadow: 0 0 0 2px rgba(0,120,212,0.15);
                }}
            </style>
        </head>
        <body>
            <h1>🎫 Autotask Tickets</h1>
            <p class="subtitle">Synced from Autotask Sandbox API</p>
            <div class="badge">Total Tickets: {len(rows)}</div>

            <div class="search-bar">
                <input type="text" id="searchInput" placeholder="🔍 Search tickets..." onkeyup="filterTable()">
            </div>

            <table id="ticketTable">
                <thead>
                    <tr>
                        <th>Ticket ID</th>
                        <th>Title</th>
                        <th>Description</th>
                        <th>Resolution Notes</th>
                        <th>Synced At</th>
                    </tr>
                </thead>
                <tbody>
                    {rows_html}
                </tbody>
            </table>

            <script>
                function filterTable() {{
                    const input = document.getElementById('searchInput').value.toLowerCase();
                    const rows = document.querySelectorAll('#ticketTable tbody tr');
                    rows.forEach(row => {{
                        const text = row.innerText.toLowerCase();
                        row.style.display = text.includes(input) ? '' : 'none';
                    }});
                }}
            </script>
        </body>
        </html>
        """
        return HTMLResponse(content=html)

    except Exception as e:
        logger.error(f"UI error: {e}")
        return HTMLResponse(content=f"<h1>Error: {e}</h1>", status_code=500)
