# The one-time setup that Docker Compose runs before the API starts (the "backend-setup" service).
# It runs as the OWNER of the tables, then stops, so the API itself never needs the owner's password.
#
# The same steps you would type by hand (see README.md), in order. Each one is safe to run again:
#   1. alembic upgrade head    create or update the tables (does nothing if they are up to date)
#   2. create_app_user.py      the API's database user and its rights
#   3. seed.py                 the demo data, only if SEED_DEMO_DATA is "yes" (skips full tables)

import os
import subprocess

# check=True: if a step fails, stop here with an error, so the API never starts on a broken database.
subprocess.run(["alembic", "upgrade", "head"], check=True)
subprocess.run(["python", "create_app_user.py"], check=True)

if os.getenv("SEED_DEMO_DATA") == "yes":
    subprocess.run(["python", "seed.py"], check=True)
else:
    print("SEED_DEMO_DATA is not 'yes': no demo data added.")
