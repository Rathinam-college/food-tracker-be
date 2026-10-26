import sqlite3

conn = sqlite3.connect('db.sqlite3')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT id, name FROM core_item WHERE name LIKE '%Bread%'")
breads = c.fetchall()

if not breads:
    print("No bread found in DB.")
    exit()

for bread in breads:
    print(f"Item: {bread['name']} (ID: {bread['id']})")
    
    # Purchased qty: sum(initial_quantity) from StockBatch
    c.execute("SELECT sum(initial_quantity) as bought FROM core_stockbatch WHERE item_id = ?", (bread['id'],))
    bought = c.fetchone()['bought'] or 0
    
    # Pushed qty: sum(quantity) from StockMovement where transaction_type='issue' or something?
    # Wait, in the rewritten logic, pushed qty is calculated via Procurement model (pushed_qty).
    c.execute("SELECT sum(pushed_qty) as pushed FROM core_procurement WHERE item_id = ?", (bread['id'],))
    pushed = c.fetchone()['pushed'] or 0
    
    # Alternatively, use core_stockbatch_transaction? Oh, wait, the old API used stockbatch.
    # The new API loops over `Procurement` items for pushing?
    
    print(f"Bought (Purchased): {bought}")
    print(f"Pushed (Issued to kitchen): {pushed}")
    print("-" * 20)

conn.close()
