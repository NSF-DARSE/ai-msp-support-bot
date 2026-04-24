import psycopg2

conn = psycopg2.connect(
    host='udrsechatbotdb.postgres.database.azure.com',
    database='postgres',
    user='dbadmin',
    password='FhDmbGChY5klxMc',
    port=5432,
    sslmode='require'
)
cur = conn.cursor()

queries = ["wifi not working", "wifi", "wireless", "internet not working", "network"]

for query in queries:
    cur.execute("""
        SELECT COUNT(*) FROM autotask_tickets
        WHERE title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s
    """, (f"%{query}%", f"%{query}%", f"%{query}%"))
    count = cur.fetchone()[0]
    print(f"'{query}' -> {count} tickets found")

print("\n--- Top tickets containing wifi ---")
cur.execute("""
    SELECT ticket_id, title, resolution_notes
    FROM autotask_tickets
    WHERE title ILIKE '%wifi%' OR title ILIKE '%wi-fi%' OR title ILIKE '%wireless%'
    LIMIT 5
""")
rows = cur.fetchall()
for r in rows:
    print(f"ID: {r[0]} | Title: {r[1]}")
    print(f"Resolution: {str(r[2])[:100]}")
    print()

conn.close()