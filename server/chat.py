from . import db
from GLP import *

class MessageService:
    def __init__(self, database):
        self.db = database
    
    def send(self, sender_id: str, reciver_id: str|None, msg: str): return db.send_msg(self.db, sender_id, reciver_id, msg)

    def get(self, sender_id: str, reciver_id: str|None): return db.get_msg(self.db, sender_id, reciver_id)

    def chat(self, user_id: str): return db.get_chat(self.db, user_id)
    

