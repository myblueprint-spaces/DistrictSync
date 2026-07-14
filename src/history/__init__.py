"""Run-history persistence layer (UI-neutral, platform-neutral).

The single home of the durable run-history store (``history.db``): the ETL
pipeline + the manual Convert path WRITE run records here, the Flet UI READS
them. This package imports NO ``ui_flet`` module — it is a data-and-persistence
layer consumed from both above (business/ETL) and beside (UI).
"""
