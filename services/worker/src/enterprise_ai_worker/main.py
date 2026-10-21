import logging
from enum import StrEnum

logger = logging.getLogger(__name__)


class WorkerResult(StrEnum):
    IDLE = "idle"


def run_once() -> WorkerResult:
    """Run one worker polling cycle.

    The database-backed job claim will be added with the first ingestion slice.
    Returning an explicit state keeps the process testable before infrastructure is available.
    """

    logger.info("No jobs available")
    return WorkerResult.IDLE


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    run_once()


if __name__ == "__main__":
    main()

