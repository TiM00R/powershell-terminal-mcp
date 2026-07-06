import sys
sys.path.insert(0, 'D:/powershell_terminal/src')
from db import Database
db = Database('D:/powershell_terminal/data/commands.db')
convos = db.list_conversations()
all_cmds = []
for conv in convos:
    for c in db.get_conversation_commands(conv['id']):
        all_cmds.append(c)
all_cmds.sort(key=lambda c: c['executed_at'], reverse=True)
for c in all_cmds[:10]:
    print(c['id'], c['exit_code'], c['command_text'][:70])
