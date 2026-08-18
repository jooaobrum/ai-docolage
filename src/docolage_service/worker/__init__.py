"""The Worker process: claims page jobs and runs the Core parse path.

Ticket #19 provides only a placeholder loop (see main.py) so the compose
stack wires `worker` correctly and is scalable via `--scale worker=N`.
The real claim loop (atomic SKIP LOCKED claiming, docling parse, result
write) lands in tickets #21/#25.
"""
