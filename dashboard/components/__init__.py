"""Individual UI pieces of the dashboard, one file per feature.

Each submodule exposes one clear ``render_*`` function that draws its piece of
the page, so a feature's markup and behavior live entirely in its own file and
``app.py`` stays a thin assembly point. Components never invoke
``sentrasql_graph`` themselves -- ``dashboard.graph_client`` owns every call
into the graph -- which keeps any single UI piece reworkable without touching
how answers are produced.
"""
