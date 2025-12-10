"""
Context variables for enriched logging with batch and worker information.
Allows automatic addition of batch/worker context to all logs within an async task.
"""
import contextvars
from typing import Optional

# Context variables for automatic log enrichment
worker_idx: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "worker_idx", default=None
)
batch_number: contextvars.ContextVar[Optional[int]] = contextvars.ContextVar(
    "batch_number", default=None
)
export_entity: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "export_entity", default=None
)


def get_context_prefix() -> str:
    """
    Build a context prefix string for logs.
    Returns formatted string like "[Worker 2]" or "[Batch 5]" or "[Worker 2|Batch 5]"
    """
    parts = []
    w_idx = worker_idx.get()
    b_num = batch_number.get()

    if w_idx is not None:
        parts.append(f"Worker {w_idx}")

    if b_num is not None:
        parts.append(f"Batch {b_num}")

    if parts:
        return "[" + "|".join(parts) + "] "
    return ""


def set_worker_context(idx: int):
    """Set the worker index for this async context."""
    worker_idx.set(idx)


def set_batch_context(num: int):
    """Set the batch number for this async context."""
    batch_number.set(num)


def set_entity_context(entity: str):
    """Set the entity (chat name) being exported."""
    export_entity.set(entity)


def clear_context():
    """Clear all context variables."""
    worker_idx.set(None)
    batch_number.set(None)
    export_entity.set(None)

# Prefetch context
is_prefetch: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "is_prefetch", default=False
)


def set_prefetch_context(is_prefetch_flag: bool):
    """Mark if this operation is a prefetch."""
    is_prefetch.set(is_prefetch_flag)


def update_context_prefix() -> str:
    """
    Build an updated context prefix including prefetch status.
    Returns formatted string like "[Batch 5|PREFETCH]" or "[Worker 2|Batch 5]"
    """
    parts = []
    w_idx = worker_idx.get()
    b_num = batch_number.get()
    is_pf = is_prefetch.get()

    if w_idx is not None:
        parts.append(f"Worker {w_idx}")

    if b_num is not None:
        parts.append(f"Batch {b_num}")
    
    if is_pf:
        parts.append("PREFETCH")

    if parts:
        return "[" + "|".join(parts) + "] "
    return ""
