# Upgrading the earlier ZIP

For a clean demo, extract V2 into a new folder and follow README.md.

To preserve an existing local shop:

1. Stop the old website and any worker.
2. Back up the old `db.sqlite3`, `media/` and `.env`.
3. Extract V2 separately; copy the backed-up database and your media/custom `.env` into it. Preserve custom product images.
4. Activate/install the new environment and run:

```bash
python -m pip install -r requirements.txt
python manage.py migrate
python manage.py seed_demo
python manage.py check
python manage.py runserver
```

5. In a second terminal, run `python manage.py run_agents --watch`.
6. Add the new provider/model settings from `.env.example` selectively; never overwrite your existing secrets with blank example values.

Existing administrator accounts remain in a migrated database; do not create another unless needed. The seed does not reset prices or stock. It refreshes only recognised old default hero settings; custom hero content is preserved.

Old orders may not have structured `delivery_data` or cost snapshots. Do not invent those values. Old orders missing structured addresses will enter courier review rather than generating an unreliable booking. Repair such historical records deliberately through a controlled data migration before booking them.

Review old pending/cancelled payments with the payment provider before starting automated processing. Actual account/provider testing remains necessary. Keep the backup until the upgrade is verified.
