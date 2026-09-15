"""SentraSQL Streamlit dashboard package.

Top-level ``dashboard`` package for the natural-language analytics dashboard.
It follows the repository's one-responsibility-per-file convention (the same
discipline ``graph/`` and ``db/`` already use): ``app.py`` is a thin entry
point that only assembles pieces, ``graph_client`` is the sole owner of
``sentrasql_graph`` invocations, ``styling`` is the single home of shared
visual constants, and ``components`` holds one file per UI piece. The package
itself carries no logic -- each submodule is self-contained so features can be
added, reworked, or tested independently.
"""
