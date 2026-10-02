"""Research worker pool: the only place research compute runs (ADR 0092).

``thytrader-research-worker`` starts a light supervisor that keeps
``research_worker_count`` worker processes alive. Each process claims one job at a time
(backtest, study, or portfolio backtest) from PostgreSQL with ``FOR UPDATE SKIP LOCKED``,
holds it under a renewed lease, and recycles itself after ``research_worker_max_jobs``
jobs or ``research_worker_max_rss_growth_mb`` of RSS growth.
"""
