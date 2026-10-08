"""Build data/erp.db and data/crm.db with realistic, deterministic ERP + CRM data.

The dataset's "today" is pinned (AS_OF, stored in each DB's `meta.as_of`) so every
run produces byte-identical data and the golden answers in golden.json stay valid.
Some customers and vendors are deliberately planted so the demo questions have known
answers; their reference queries live in golden.json and are asserted below.

    uv run seed.py                       pinned date
    AS_OF=2027-01-15 uv run seed.py      different "today" (then: uv run eval.py --freeze)
"""
from demo_data.generate import main

if __name__ == "__main__":
    main()
