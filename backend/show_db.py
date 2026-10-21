import sqlite3

conn = sqlite3.connect('freecollab.db')
c = conn.cursor()

print('=' * 60)
print('   FreeCollab Database Structure')
print('=' * 60)

c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
tables = [t[0] for t in c.fetchall()]

for table in tables:
    c.execute(f'PRAGMA table_info({table})')
    cols = c.fetchall()
    c.execute(f'SELECT COUNT(*) FROM {table}')
    count = c.fetchone()[0]
    
    print(f'\n{table.upper()} ({count} rows)')
    print('-' * 40)
    for col in cols:
        col_id, name, dtype, notnull, default, pk = col
        pk_mark = ' [PK]' if pk else ''
        print(f'  {name}: {dtype}{pk_mark}')

conn.close()
print('\n' + '=' * 60)
