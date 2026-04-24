import psycopg2

conn = psycopg2.connect(
    host='udrsechatbotdb.postgres.database.azure.com',
    database='postgres', user='dbadmin',
    password='FhDmbGChY5klxMc', port=5432, sslmode='require'
)
cur = conn.cursor()

print("=== Searching for Outlook/email tickets ===\n")

# Check what tickets exist for outlook/email
for term in ['outlook', 'email', 'mail', 'receive']:
    cur.execute("""
        SELECT COUNT(*) FROM autotask_tickets
        WHERE title ILIKE %s OR description ILIKE %s OR resolution_notes ILIKE %s
    """, (f'%{term}%', f'%{term}%', f'%{term}%'))
    count = cur.fetchone()[0]
    print(f"'{term}' → {count} tickets")

print("\n=== Top 5 Outlook/email ticket titles ===\n")
cur.execute("""
    SELECT ticket_id, title FROM autotask_tickets
    WHERE title ILIKE '%outlook%' OR title ILIKE '%email%' OR title ILIKE '%mail%'
    ORDER BY ticket_id DESC LIMIT 5
""")
for r in cur.fetchall():
    print(f"#{r[0]} | {r[1]}")

print("\n=== Full-text search result for 'outlook email' ===\n")
cur.execute("""
    SELECT ticket_id, title,
        ts_rank(search_vector, plainto_tsquery('english', 'outlook email')) AS rank
    FROM autotask_tickets
    WHERE search_vector @@ plainto_tsquery('english', 'outlook email')
    ORDER BY rank DESC LIMIT 3
""")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"#{r[0]} | {r[1]} | rank: {r[2]}")
else:
    print("No results from full-text search")

conn.close()
