"""Typed adapter around Celery (untyped, conventions §2.2).

Config: Redis broker (its own DB index), JSON only (never pickle), late acks with
``reject_on_worker_lost`` (a crashed worker's task is redelivered), prefetch 1 (long media
tasks don't hoard messages), no result backend (status lives in the database).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from celery import Celery  # pyright: ignore[reportMissingTypeStubs]

from spoldzielnia_ai.workers.jobs import TASK_QUEUE, TIME_LIMITS, DerivativeJob, TaskName

if TYPE_CHECKING:
    from collections.abc import Callable

APP_NAME = "spoldzielnia"
# Longest task (transcription hard limit) plus margin: unacked messages are redelivered after it.
VISIBILITY_TIMEOUT = 2 * 3660


def make_celery(broker_url: str, *, eager: bool = False) -> Any:
    app: Any = Celery(APP_NAME, broker=broker_url, set_as_current=False)
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        result_backend=None,
        task_ignore_result=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        worker_hijack_root_logger=False,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": VISIBILITY_TIMEOUT},
        task_routes={name.value: {"queue": queue.value} for name, queue in TASK_QUEUE.items()},
        task_always_eager=eager,
        task_eager_propagates=eager,
        enable_utc=True,
    )
    return app


def register(
    app: Any, name: TaskName, run: Callable[[DerivativeJob, int], bool], max_retries: int = 3
) -> None:
    """Register ``run(job, attempt) -> retry?`` as a bound task with exponential backoff."""
    soft, hard = TIME_LIMITS[name]

    def task(self: Any, **message: object) -> None:
        job = DerivativeJob.model_validate(message)  # ids only; anything else raises
        attempt = int(self.request.retries)
        while run(job, attempt) and attempt < max_retries:
            if self.request.is_eager:  # tests: Celery would raise Retry instead of re-running
                attempt += 1
                continue
            raise self.retry(countdown=min(30 * 2**attempt, 600), max_retries=max_retries)

    app.task(
        bind=True,
        shared=False,  # default True attaches the task to every Celery app in the process
        name=name.value,
        soft_time_limit=soft,
        time_limit=hard,
        acks_late=True,
        reject_on_worker_lost=True,
    )(task)


def send(app: Any, name: TaskName, job: DerivativeJob) -> None:
    if app.conf.task_always_eager:  # tests: `send_task` would ignore eager mode and publish
        app.tasks[name.value].apply(kwargs=job.to_message())
        return
    app.send_task(name.value, kwargs=job.to_message(), queue=TASK_QUEUE[name].value)


def worker_main(app: Any, queues: list[str], concurrency: int, loglevel: str) -> None:
    app.worker_main(
        [
            "worker",
            f"--queues={','.join(queues)}",
            f"--concurrency={concurrency}",
            f"--loglevel={loglevel}",
            "--without-gossip",
            "--without-mingle",
            "--without-heartbeat",
        ]
    )
