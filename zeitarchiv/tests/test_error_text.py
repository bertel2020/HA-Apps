"""Fehlertexte für die Oberfläche enthalten keine Serverpfade."""

from __future__ import annotations

from app.error_text import user_message
from app.progress import JobProgress


def test_an_os_error_shows_the_cause_and_the_file_name_but_not_the_folder() -> None:
    error = OSError(28, "No space left on device", "/data/archive/sensor.x/2024-07.parquet")
    assert user_message(error) == "No space left on device: 2024-07.parquet"
    assert "/data" not in user_message(error)


def test_paths_inside_other_messages_are_shortened_but_other_text_stays() -> None:
    assert user_message(ValueError("kaputt in /config/zeitarchiv/index.sqlite bei 3/4 (http://host/x)")) == (
        "kaputt in …/index.sqlite bei 3/4 (http://host/x)"
    )
    assert user_message(RuntimeError("")) == "RuntimeError"
    assert len(user_message(ValueError("x" * 5000))) == 2000


def test_a_failed_job_keeps_the_path_out_of_the_displayed_error() -> None:
    progress = JobProgress("pfad-test")

    def work() -> None:
        raise OSError(13, "Permission denied", "/data/rollup/sensor.a/2024.parquet")

    progress.run(work)
    assert progress.error == "Permission denied: 2024.parquet"
