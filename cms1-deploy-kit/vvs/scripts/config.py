import os
HOME = os.path.expanduser("~")
DB_PATH = os.environ.get("VVS_DB_PATH", os.path.join(HOME, "vvs", "database", "vvs.db"))