"""Resumable catalog importer for website sources.

The importer walks a source's own pagination, normalizes each record, writes it
into the catalog store, and checkpoints after every page.  It never loads a whole
catalog into memory: one page of records is in flight at a time, and the store
already holds what previous runs persisted.

Design points that matter for a large catalog:

* bounded memory - only one page of source records is held;
* durable checkpoints - the next cursor and the counters are saved per page, so
  an interrupted import resumes where it stopped;
* loop detection - a cursor or page URL that repeats, or a page that did not
  advance, stops the run with ``blocked`` instead of paging forever;
* error isolation - one malformed record or one failed detail request is
  recorded and skipped, never fatal to the page;
* honest completion - the run is ``complete`` only when the source said there is
  no next page and no page failed; otherwise it is ``partial``.
"""

from __future__ import annotations

import bisect
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from . import sidecar as sidecarlib
from .catalog_store import (
    MAX_IMAGE_HOSTS,
    CatalogStore,
    ImportJob,
    MagnetRecord,
    MirrorRecord,
    SourceRecord,
    UpsertResult,
)
from .sources import (
    Capability,
    SourceError,
    SourceGame,
    UnsupportedOperation,
    WebsiteSource,
    source_for_url,
)

DEFAULT_PAGE_SIZE = 100
#: Upper bound for one run's record limit; anything larger is clamped.
MAX_RECORDS_LIMIT = 32768
#: The catalog document is rewritten at most this often while a run is going.
#: Every checkpoint stays in memory, and the run always saves before it ends,
#: so throttling costs no data while removing most of the disk churn.
SAVE_INTERVAL_SECONDS = 2.0
SAVE_EVERY_PAGES = 10
#: A record that already had its own page read during an earlier run is not
#: opened again; this key in ``CatalogGame.extra`` is that marker.
DETAIL_STATE_KEY = "details_state"
DETAILS_FETCHED = "fetched"
#: The page was read and published nothing usable; not worth a second request.
DETAILS_UNSUPPORTED = "unsupported"
_DETAIL_STATES_DONE = (DETAILS_FETCHED, DETAILS_UNSUPPORTED)
#: A sidecar JSON URL is read once; this key in ``CatalogGame.extra`` records
#: the outcome so a later run neither repeats the request nor re-parses it.
SIDECAR_KEY = "sidecar"
_SIDECAR_DONE = (
    sidecarlib.STATUS_MAGNET,
    sidecarlib.STATUS_INFOHASH,
    sidecarlib.STATUS_NONE,
    sidecarlib.STATUS_UNSUPPORTED,
)
DEFAULT_RETRIES = 2
DEFAULT_DELAY = 1.0
DEFAULT_ENRICH_WORKERS = 4
#: No artificial stall between enrichment batches.  The bounded worker pool is
#: what paces the requests, so latency decides throughput instead of a sleep:
#: a fixed 0.5 s gap capped the pass at a few records per second on a slow host.
DEFAULT_ENRICH_DELAY = 0.0


class ImportCancelled(Exception):
    """Raised internally when the caller cancels the run."""


@dataclass
class ImportOptions:
    """Tunables for one import run."""

    page_size: int = DEFAULT_PAGE_SIZE
    #: Seconds to wait between pages.  Sources are requested politely.
    delay: float = DEFAULT_DELAY
    max_retries: int = DEFAULT_RETRIES
    #: Run a second, independent pass that fetches details, artwork, mirrors,
    #: and magnets.  It is off by default: a catalog with thousands of records
    #: must enumerate quickly before any per-game work starts.
    enrich: bool = False
    #: Bounded concurrency for the enrichment pass.
    workers: int = DEFAULT_ENRICH_WORKERS
    #: Seconds to wait between batches of per-game requests (extra pacing, off
    #: by default; the worker pool is the normal limiter).
    enrich_delay: float = DEFAULT_ENRICH_DELAY
    #: Optional hard cap on request starts per second across all workers.  0
    #: means "no extra cap": at most ``workers`` requests are ever in flight,
    #: so the observed rate stays bounded by the source's own latency.
    enrich_rate: float = 0.0
    #: Stop after this many discovered records.  0 means "no limit".
    max_records: int = 0
    #: Stop after this many page requests.  0 means "no limit".
    max_requests: int = 0

    def clamped(self) -> "ImportOptions":
        return ImportOptions(
            page_size=max(1, min(int(self.page_size), 1000)),
            delay=max(0.0, min(float(self.delay), 60.0)),
            max_retries=max(0, min(int(self.max_retries), 10)),
            enrich=bool(self.enrich),
            workers=max(1, min(int(self.workers), 8)),
            enrich_delay=max(0.0, min(float(self.enrich_delay), 30.0)),
            enrich_rate=max(0.0, min(float(self.enrich_rate), 100.0)),
            max_records=max(0, min(int(self.max_records), MAX_RECORDS_LIMIT)),
            max_requests=max(0, min(int(self.max_requests), 100_000)),
        )


@dataclass
class ImportOutcome:
    """What one run ended up doing."""

    status: str
    job_id: str
    pages_processed: int = 0
    games_discovered: int = 0
    games_imported: int = 0
    duplicates: int = 0
    skipped: int = 0
    games_enriched: int = 0
    magnets_found: int = 0
    mirrors_found: int = 0
    retries: int = 0
    errors: int = 0
    #: Records whose own page offered no readable structured metadata.
    details_skipped: int = 0
    message: str = ""

    @property
    def summary(self) -> str:
        bits = [
            f"{self.games_imported} game(s) imported",
            f"{self.games_discovered} seen",
        ]
        if self.duplicates:
            bits.append(f"{self.duplicates} duplicate(s)")
        if self.skipped:
            bits.append(f"{self.skipped} skipped")
        if self.magnets_found:
            bits.append(f"{self.magnets_found} magnet(s)")
        if self.mirrors_found:
            bits.append(f"{self.mirrors_found} mirror(s)")
        if self.details_skipped:
            bits.append(f"{self.details_skipped} without page metadata")
        if self.errors:
            bits.append(f"{self.errors} error(s)")
        if self.retries:
            bits.append(f"{self.retries} retry(ies)")
        text = f"{self.status}: " + ", ".join(bits) + "."
        return f"{text} {self.message}".strip()


ProgressCallback = Callable[[ImportJob], None]


class _RateGate:
    """Space request starts to at most *rate* per second across every worker.

    A shared gate rather than a per-worker sleep: with four workers a per-worker
    pause still allows a burst of four at once.  ``rate <= 0`` disables it.
    """

    def __init__(
        self,
        rate: float,
        *,
        clock: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        self._interval = (1.0 / rate) if rate > 0 else 0.0
        self._clock = clock
        self._sleep = sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = self._clock()
            if self._next > now:
                self._sleep(self._next - now)
                now = self._clock()
            self._next = now + self._interval


class CatalogImporter:
    """Drive one source's pagination into a :class:`CatalogStore`."""

    def __init__(
        self,
        store: CatalogStore,
        source: WebsiteSource,
        *,
        options: ImportOptions | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.store = store
        self.source = source
        self.options = (options or ImportOptions()).clamped()
        self._clock = clock
        self._sleep = sleep
        self._last_save = 0.0
        self._pages_since_save = 0
        #: A local file has no server to be polite to, so paging it is not paced.
        self._paced = getattr(source, "source_kind", "") != "file"

    # -- discovery ----------------------------------------------------------

    def inspect(self) -> Any:
        """Ask the source what actually works, and remember the answer."""
        report = self.source.inspect()
        record = self.store.get_source(self.source.id)
        if record is None:
            record = SourceRecord(
                source_id=self.source.id,
                url=getattr(self.source, "canonical_url", "") or self.source.url,
                adapter=type(self.source).__name__,
            )
            self.store.add_source(record)
        record.capabilities = report.to_dict()
        record.name = record.name or self.source.name
        record.collection = getattr(self.source, "collection", "")
        record.image_hosts = list(getattr(self.source, "image_hosts", ()))[:MAX_IMAGE_HOSTS]
        if getattr(self.source, "config", None):
            # A configured catalog keeps the mapping that made it work, so the
            # next run needs no retyping.
            record.config = dict(self.source.config)
        record.last_error = self.source.last_error
        return report

    # -- run ----------------------------------------------------------------

    def run(
        self,
        *,
        job: ImportJob | None = None,
        cancel: threading.Event | None = None,
        progress: ProgressCallback | None = None,
        enrich: bool | None = None,
    ) -> ImportOutcome:
        resuming = bool(job is not None and self._resumable(job))
        resume_cursor = job.cursor if resuming and job is not None else None
        resume_enrichment = bool(resuming and job is not None and job.phase == "enrichment")
        if job is None:
            job = self.store.start_job(
                self.source.id,
                getattr(self.source, "canonical_url", "") or self.source.url,
            )
        else:
            job = self.store.start_job(job.source_id, job.source_url, resume=job)

        do_enrich = self.options.enrich if enrich is None else bool(enrich)
        if do_enrich:
            capabilities = self._capabilities()
            if capabilities is not None and capabilities.state("details") == Capability.UNSUPPORTED:
                do_enrich = False
                job.add_error(
                    "The source does not expose a details interface; catalog records "
                    "were imported without enrichment."
                )

        job.response_format = getattr(self.source, "response_format", "") or job.response_format
        job.pagination = getattr(self.source, "pagination_method", "") or job.pagination
        outcome = ImportOutcome(status="failed", job_id=job.job_id)
        job.errors = list(job.errors)
        job.finished_at = 0.0
        details_skipped = 0
        # A run started without a prior inspect() still needs its source record,
        # otherwise counters and approved artwork hosts have nowhere to live.
        self._ensure_source_record()

        try:
            if resume_enrichment:
                # The catalog was already enumerated; only per-game work remains.
                if do_enrich:
                    job.status = "importing"
                    self._publish(job, progress)
                    details_skipped = self._enrichment_pass(job, cancel, progress)
                else:
                    job.status = "complete"
                    job.phase = "catalog"
                    job.add_error(
                        "Enrichment was skipped on resume; the catalog itself is complete."
                    )
            else:
                job.status = "importing"
                job.phase = "catalog"
                self._publish(job, progress)
                self._enumerate(job, resume_cursor, cancel, progress)
                if do_enrich and job.status in ("complete", "partial"):
                    # A bounded or partially failed catalog still has records worth
                    # reading; the enrichment pass must not upgrade a partial
                    # catalog to complete, so the status is carried through.
                    details_skipped = self._enrichment_pass(
                        job, cancel, progress,
                        keep_partial=job.status == "partial" or job.catalog_limited,
                    )
        except ImportCancelled:
            # A user cancel is not an error: the status says so, and inflating the
            # error count would hide real failures in the diagnostics.
            job.status = "interrupted"
        except SourceError as exc:
            job.pages_failed += 1
            job.last_error = str(exc)
            job.add_error(str(exc))
            job.status = "failed" if job.pages_processed == 0 else "partial"
        except Exception as exc:  # pragma: no cover - unexpected adapter bug
            job.pages_failed += 1
            job.last_error = f"Unexpected failure: {type(exc).__name__}: {exc}"
            job.add_error(job.last_error)
            job.status = "failed" if job.pages_processed == 0 else "partial"
        finally:
            job.finished_at = time.time()
            self.store.save_job(job)
            try:
                self.store.save()
            except OSError as exc:
                job.add_error(f"Catalog could not be saved: {exc}")
            self._publish(job, progress)
            source_record = self.store.get_source(self.source.id)
            if source_record is not None:
                source_record.last_error = self.source.last_error
                if job.status in ("complete", "partial"):
                    source_record.last_sync = job.finished_at
                self.store.refresh_source_count(self.source.id)
                self.store.save()

        outcome.status = job.status
        outcome.pages_processed = job.pages_processed
        outcome.games_discovered = job.games_discovered
        outcome.games_imported = job.games_imported
        outcome.duplicates = job.duplicates
        outcome.skipped = job.skipped
        outcome.retries = job.retries
        outcome.games_enriched = job.games_enriched
        outcome.magnets_found = job.magnets_found
        outcome.mirrors_found = job.mirrors_found
        outcome.details_skipped = details_skipped
        outcome.errors = len(job.errors)
        outcome.message = self._closing_message(job)
        return outcome

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _resumable(job: ImportJob | None) -> bool:
        return bool(job and job.resumable)

    # -- catalog enumeration -------------------------------------------------

    def _enumerate(
        self,
        job: ImportJob,
        cursor: str | None,
        cancel: threading.Event | None,
        progress: ProgressCallback | None,
    ) -> None:
        """Walk every catalog page and persist each one before asking for more."""
        seen_cursors: set[str] = {cursor} if cursor else set()
        seen_urls: set[str] = set()
        seen_ids: set[str] = set()
        while True:
            self._check_cancel(cancel)
            limit_note = self._limit_reached(job)
            if limit_note:
                job.status = "partial"
                job.catalog_limited = True
                job.add_error(limit_note)
                return
            page = self._fetch_page(cursor, job, cancel)
            page_ids = {game.game_id for game in page.games if game.game_id}
            if page.url:
                # A backer that serves one file (a local catalog, a sitemap) may
                # legitimately reuse its URL while the offset advances, so a
                # repeat is only a loop when the page carries nothing new.
                if page.url in seen_urls and page_ids and page_ids <= seen_ids:
                    job.status = "blocked"
                    job.add_error(
                        f"Stopped: the source returned page {page.page_number} again "
                        f"({page.url}) with no new records. Its pagination is not "
                        "advancing.",
                        page=page.page_number,
                    )
                    return
                seen_urls.add(page.url)
            seen_ids.update(page_ids)

            job.pages_processed += 1
            if page.total is not None:
                job.total_estimate = page.total
            for note in page.notes:
                job.add_error(note, page=page.page_number)

            if not page.games and page.next_cursor:
                job.status = "blocked"
                job.add_error(
                    f"Stopped: page {page.page_number} returned no records but the "
                    "source says more pages exist.",
                    page=page.page_number,
                )
                return

            # Store the page in one batch: one pass over the records instead of
            # one store call and one result object per game.
            self._check_cancel(cancel)
            records = page.games
            if self.options.max_records:
                allowance = max(0, self.options.max_records - job.games_discovered)
                records = records[:allowance]
            if records:
                tally = self.store.upsert_games(records)
                job.games_discovered += len(records)
                job.games_imported += tally["inserted"] + tally["updated"]
                job.duplicates += tally["updated"] + tally["duplicate"]
                job.skipped += tally["rejected"]
                for message in tally["errors"]:
                    job.add_error(message, page=page.page_number)

            job.cursor = page.next_cursor
            job.phase = "catalog"
            self._commit(job, progress, force=False)

            limit_note = self._limit_reached(job)
            if limit_note:
                job.status = "partial"
                job.catalog_limited = True
                job.cursor = None
                job.add_error(limit_note)
                return

            if page.complete:
                # Compare against records actually seen, never against
                # ``page_number * page_size``: an adapter may legitimately return
                # a whole document in one page, and that is not a short page.
                if (
                    page.total is not None
                    and job.games_discovered < page.total
                    and page.games
                ):
                    job.status = "blocked"
                    job.add_error(
                        f"Stopped: the source reports {page.total} records but its "
                        f"page {page.page_number} had no next cursor.",
                        page=page.page_number,
                    )
                    return
                job.status = "complete" if job.pages_failed == 0 else "partial"
                job.cursor = None
                return
            if not page.next_cursor:
                # The source gave neither a next page nor a completion signal.
                job.status = "blocked"
                job.add_error(
                    "Stopped: the page reported more results but no next cursor.",
                    page=page.page_number,
                )
                return
            if page.next_cursor in seen_cursors:
                job.status = "blocked"
                job.add_error(
                    f"Stopped: the source repeated the cursor {page.next_cursor!r}.",
                    page=page.page_number,
                )
                return
            seen_cursors.add(page.next_cursor)
            cursor = page.next_cursor
            if self.options.delay and self._paced:
                self._wait(self.options.delay, cancel)

    def _limit_reached(self, job: ImportJob) -> str:
        """Return an explanation when a configured import limit is reached."""
        if self.options.max_records and job.games_discovered >= self.options.max_records:
            return (
                f"Stopped at the configured limit of {self.options.max_records} "
                "record(s); the source has more."
            )
        if self.options.max_requests and job.pages_processed >= self.options.max_requests:
            return (
                f"Stopped at the configured limit of {self.options.max_requests} "
                "page request(s); the source has more."
            )
        return ""

    # -- enrichment ----------------------------------------------------------

    def _enrichment_pass(
        self,
        job: ImportJob,
        cancel: threading.Event | None,
        progress: ProgressCallback | None,
        *,
        keep_partial: bool = False,
    ) -> int:
        """Fetch details, artwork, mirrors, and magnets without blocking catalog import.

        The pass runs only after the catalog is fully enumerated.  Work is
        checkpointed by the last enriched game key, so a cancel or crash resumes
        here rather than re-walking the catalog.
        """
        job.phase = "enrichment"
        keys = sorted(game.key for game in self.store.iter_games(self.source.id))
        job.enrich_total = len(keys)
        start = bisect.bisect_right(keys, job.enrich_cursor) if job.enrich_cursor else 0
        job.enrich_done = max(job.enrich_done, start)
        pending = keys[start:]
        if not pending:
            job.status = "complete"
            self._commit(job, progress)
            return 0

        job.status = "importing"
        self._publish(job, progress)
        # Several records per scheduling round keep the pool busy without ever
        # exceeding the configured concurrency.
        batch_size = max(4, self.options.workers * 4)
        gate = _RateGate(
            self.options.enrich_rate, clock=self._clock, sleep=self._sleep
        )
        failures = 0
        unsupported = 0
        pool = ThreadPoolExecutor(max_workers=self.options.workers, thread_name_prefix="lgl-enrich")
        try:
            for offset in range(0, len(pending), batch_size):
                self._check_cancel(cancel)
                window = pending[offset:offset + batch_size]
                ready: list[str] = []
                for key in window:
                    stored = self.store.get_game(key)
                    if (
                        stored is not None
                        and stored.extra.get(DETAIL_STATE_KEY) in _DETAIL_STATES_DONE
                    ):
                        # Already answered by an earlier run: no network request.
                        job.enrich_cursor = key
                        job.enrich_done += 1
                        continue
                    ready.append(key)
                if ready:
                    futures = [
                        (key, pool.submit(self._enrich_limited, key, gate))
                        for key in ready
                    ]
                    for key, future in futures:
                        self._check_cancel(cancel)
                        try:
                            details, mirrors, links = future.result()
                        except UnsupportedOperation:
                            details, mirrors, links = None, [], []
                            unsupported += 1
                            self._mark_detail_state(key, DETAILS_UNSUPPORTED)
                        except Exception as exc:  # a bad detail never kills the pass
                            failures += 1
                            job.enrich_failures += 1
                            job.last_error = f"{key}: {exc}"
                            job.add_error(f"{key}: enrichment failed ({exc})")
                        else:
                            if details is None:
                                # ``_enrich_one`` reports a record whose source
                                # has no details interface as ``None`` instead of
                                # raising, because its sidecar may still hold a
                                # magnet.  It is still a skipped detail.
                                unsupported += 1
                            self._apply_enrichment(key, details, mirrors, links, job)
                        job.enrich_cursor = key
                        job.enrich_done += 1
                        # Report each finished record, not just each batch: the
                        # pass is network bound, so this is the only live signal.
                        self._publish(job, progress)
                self._commit(job, progress, force=False)
                if self.options.enrich_delay:
                    self._wait(self.options.enrich_delay, cancel)
        finally:
            pool.shutdown(wait=True, cancel_futures=True)

        job.status = "partial" if failures or keep_partial or job.catalog_limited else "complete"
        self._commit(job, progress)
        return unsupported

    def _enrich_limited(self, key: str, gate: _RateGate):
        """Wait for the shared rate gate, then fetch one record's details."""
        gate.wait()
        return self._enrich_one(key)

    def _enrich_one(self, key: str):
        """Fetch one game's extra records; one retry for a transient failure."""
        # A source id may itself contain a colon (file catalogs use
        # ``file:<digest>``), so the stored record is the only reliable place
        # to read the provider id from.
        stored = self.store.get_game(key)
        if stored is not None and stored.game_id:
            game_id = stored.game_id
        else:
            game_id = key.split(":", 1)[1] if ":" in key else key
        attempt = 0
        while True:
            try:
                try:
                    details = self.source.details(game_id)
                except UnsupportedOperation:
                    # A record with no readable page is still worth enriching:
                    # its own .json sidecar may publish a magnet.
                    details = None
                try:
                    mirrors = self.source.mirrors(game_id)
                except UnsupportedOperation:
                    mirrors = []
                try:
                    links = self.source.magnets(game_id)
                except UnsupportedOperation:
                    links = []
                return details, mirrors, links
            except UnsupportedOperation:
                raise
            except SourceError:
                attempt += 1
                if attempt >= 3:
                    raise
                self._sleep(min(4.0, 0.5 * (2 ** (attempt - 1))))

    def _apply_enrichment(
        self,
        key: str,
        details,
        mirrors,
        links,
        job: ImportJob,
    ) -> None:
        stored = self.store.get_game(key)
        if stored is None:
            return
        if details is not None:
            if _apply_details(stored, details):
                job.games_enriched += 1
            # Remember the read, so a later run does not open the page again.
            stored.extra[DETAIL_STATE_KEY] = DETAILS_FETCHED
            self._remember_image_host(stored.source_id, details.cover_url)
        else:
            # No readable page for this record; remember that so a resumed run
            # does not retry it.  The sidecar below still runs.
            self._mark_detail_state(key, DETAILS_UNSUPPORTED)
        for mirror in mirrors:
            if self.store.add_mirror(
                MirrorRecord.from_source(mirror, source_id=details.source_id if details else stored.source_id, game_key=key)
            ) == "inserted":
                job.mirrors_found += 1
        for link in links:
            if self.store.add_magnet(
                MagnetRecord.from_link(
                    link,
                    source_id=details.source_id if details else stored.source_id,
                    game_key=key,
                    source_url=stored.source_url,
                    method="source interface",
                )
            ) == "inserted":
                job.magnets_found += 1
        self._apply_sidecar(stored, job)

    def _apply_sidecar(self, stored: Any, job: ImportJob) -> None:
        """Read a record's own ``.json`` sidecar once and keep its magnet link.

        The URL comes from the record's preserved provider fields, so nothing is
        guessed from a title or a page.  Only an adapter that declares it may
        distribute files can have a sidecar read at all; the gate lives in
        :func:`lgl.sidecar.discover_magnet`, so a store-page catalog or a local
        file is refused there rather than here.  Every outcome is stored, which
        is what keeps a re-run from repeating the request.
        """
        capability = getattr(self.source, "download_capability", "none") or "none"
        if capability not in sidecarlib.PERMITTED_CAPABILITIES:
            # Nothing is read and nothing is written: a catalog that only links to
            # pages is not a file source, so per-record "not permitted" notes would
            # be noise on thousands of rows.  discover_magnet enforces the same gate.
            return
        urls = sidecarlib.json_urls(_scalar_strings(stored.extra))
        if not urls:
            return
        state = stored.extra.get(SIDECAR_KEY)
        state = state if isinstance(state, Mapping) else {}
        previous = str(state.get("url") or "")
        if previous in urls and str(state.get("status") or "") in _SIDECAR_DONE:
            return
        url = urls[0]
        allowed = tuple(getattr(self.source, "hosts", ()) or ()) + tuple(
            getattr(self.source, "image_hosts", ()) or ()
        )
        result = sidecarlib.discover_magnet(
            url,
            allowed_hosts=allowed,
            download_capability=capability,
            title=stored.title,
        )
        stored.extra[SIDECAR_KEY] = {
            **result.to_dict(),
            "reason": result.describe(),
        }
        if not result.found or result.link is None:
            return
        added = self.store.add_magnet(
            MagnetRecord.from_link(
                result.link,
                source_id=stored.source_id,
                game_key=stored.key,
                source_url=stored.source_url,
                method="json sidecar",
            )
        )
        if added == "inserted":
            job.magnets_found += 1

    def _mark_detail_state(self, key: str, state: str) -> None:
        stored = self.store.get_game(key)
        if stored is not None:
            stored.extra[DETAIL_STATE_KEY] = state

    def _ensure_source_record(self) -> SourceRecord:
        record = self.store.get_source(self.source.id)
        if record is None:
            record = SourceRecord(
                source_id=self.source.id,
                url=getattr(self.source, "canonical_url", "") or self.source.url,
                adapter=type(self.source).__name__,
                name=self.source.name,
                collection=getattr(self.source, "collection", ""),
                image_hosts=list(getattr(self.source, "image_hosts", ()))[:MAX_IMAGE_HOSTS],
            )
            self.store.add_source(record)
        if getattr(self.source, "config", None):
            # A run started without inspect() still records the mapping it used.
            record.config = dict(self.source.config)
        return record

    def _remember_image_host(self, source_id: str, url: str) -> None:
        """Approve a cover host the details pass actually saw, for the UI."""
        if not url:
            return
        record = self.store.get_source(source_id)
        if record is None:
            return
        from urllib.parse import urlsplit

        split = urlsplit(url)
        host = (split.hostname or "").casefold()
        if split.scheme != "https" or not host or host in record.image_hosts:
            return
        if len(record.image_hosts) >= MAX_IMAGE_HOSTS:
            return
        record.image_hosts.append(host)

    def _capabilities(self) -> Any:
        record = self.store.get_source(self.source.id)
        if record is None or not record.capabilities:
            return None
        from .sources import CapabilityReport

        return CapabilityReport.from_dict(record.capabilities)

    @staticmethod
    def _count(job: ImportJob, result: UpsertResult) -> None:
        if result.kind == "inserted":
            job.games_imported += 1
        elif result.kind == "updated":
            job.games_imported += 1
            job.duplicates += 1
        elif result.kind == "duplicate":
            job.duplicates += 1
        else:
            job.skipped += 1

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise ImportCancelled

    def _wait(self, seconds: float, cancel: threading.Event | None) -> None:
        deadline = self._clock() + seconds
        while self._clock() < deadline:
            self._check_cancel(cancel)
            step = min(0.2, max(0.0, deadline - self._clock()))
            if step <= 0:
                break
            self._sleep(step)

    def _fetch_page(self, cursor: str | None, job: ImportJob, cancel: threading.Event | None):
        attempt = 0
        while True:
            self._check_cancel(cancel)
            try:
                return self.source.page(cursor, limit=self.options.page_size)
            except UnsupportedOperation as exc:
                raise SourceError(str(exc)) from exc
            except SourceError as exc:
                attempt += 1
                job.last_error = str(exc)
                if attempt > self.options.max_retries:
                    self.source.last_error = str(exc)
                    raise
                job.retries += 1
                backoff = min(30.0, (2 ** (attempt - 1)) + random.uniform(0.0, 0.5))
                job.add_error(
                    f"Page {cursor or 1} failed ({exc}); retrying in {backoff:.1f}s."
                )
                self._commit(job, None)
                self._wait(backoff, cancel)

    def _commit(
        self,
        job: ImportJob,
        progress: ProgressCallback | None,
        *,
        force: bool = True,
    ) -> None:
        """Checkpoint in memory, then write the catalog when it is worth it.

        ``force=False`` is the per-page path: the job (cursor, counters) is kept
        in memory immediately, and the whole document is only rewritten every
        ``SAVE_EVERY_PAGES`` pages or ``SAVE_INTERVAL_SECONDS``.  A forced commit
        always runs before a run ends, so cancelling cannot lose a checkpoint.
        """
        self.store.save_job(job)
        self._pages_since_save += 1
        due = (
            force
            or self._pages_since_save >= SAVE_EVERY_PAGES
            or self._clock() - self._last_save >= SAVE_INTERVAL_SECONDS
        )
        if due:
            try:
                self.store.save()
            except OSError as exc:
                job.add_error(f"Catalog could not be saved: {exc}")
            self._pages_since_save = 0
            self._last_save = self._clock()
        self._publish(job, progress)

    @staticmethod
    def _publish(job: ImportJob, progress: ProgressCallback | None) -> None:
        if progress is None:
            return
        try:
            progress(job)
        except Exception:  # a UI callback must never break an import
            pass

    @staticmethod
    def _closing_message(job: ImportJob) -> str:
        if job.status == "complete":
            if job.phase == "enrichment":
                return "The catalog and the requested details were fully processed."
            return "The source reported no further pages."
        if job.status == "partial":
            if job.phase == "enrichment":
                return (
                    f"The catalog is complete; {job.enrich_failures} enrichment "
                    "request(s) failed. The games themselves are kept."
                )
            return (
                f"{job.pages_failed} page(s) failed; the imported records are kept and "
                "the run can be resumed."
            )
        if job.status == "interrupted":
            return "Resume to continue from the saved cursor."
        if job.status == "blocked":
            # Name the page that actually stopped the run instead of asserting
            # a cause the diagnostics may contradict.
            for entry in reversed(job.errors):
                message = str(entry.get("message") or "")
                if message.startswith("Stopped:"):
                    return message
            return "The page walk stopped; see the error log."
        return "See the error log."


def _scalar_strings(value: Any, depth: int = 0) -> list[str]:
    """Every string under *value*, bounded in depth; used to find ``.json`` URLs."""
    found: list[str] = []
    if depth > 4:
        return found
    if isinstance(value, Mapping):
        for item in value.values():
            found.extend(_scalar_strings(item, depth + 1))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.extend(_scalar_strings(item, depth + 1))
    elif isinstance(value, str):
        found.append(value)
    return found


@dataclass
class SidecarRefreshReport:
    """What a retroactive magnet pass did with already-imported records."""

    permitted: bool = False
    reason: str = ""
    scanned: int = 0
    with_links: int = 0
    requested: int = 0
    found: int = 0
    empty: int = 0
    skipped: int = 0
    failed: int = 0
    cancelled: bool = False

    def summary(self) -> str:
        if not self.permitted:
            return self.reason or "This source does not permit reading .json links."
        if self.cancelled:
            return (
                f"Stopped after {self.scanned} record(s); {self.found} magnet(s) "
                f"added from {self.requested} .json link(s)."
            )
        if not self.with_links:
            return "No imported record in this catalog carries a .json link."
        bits = [
            f"{self.scanned} record(s) scanned",
            f"{self.with_links} with a .json link",
            f"{self.requested} link(s) read",
            f"{self.found} magnet(s) added",
        ]
        if self.empty:
            bits.append(f"{self.empty} published no magnet")
        if self.skipped:
            bits.append(f"{self.skipped} already read")
        if self.failed:
            bits.append(f"{self.failed} failed")
        return ", ".join(bits) + "."


def _says_no_torrent(extra: Any) -> bool:
    """True only when the record's own provider fields say it has no torrent.

    ``hasTorrent: false`` is a statement by the provider, not a guess, so the
    pass honours it and does not spend a request on a record that cannot yield
    a magnet.  Any other value (or a missing key) means "read the link".
    """
    if not isinstance(extra, Mapping):
        return False
    fields = extra.get("source_fields")
    if not isinstance(fields, Mapping):
        return False
    for key, value in fields.items():
        if str(key).casefold() == "hastorrent" and value is False:
            return True
    return False


def refresh_sidecar_magnets(
    store: CatalogStore,
    source: WebsiteSource,
    *,
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
    force: bool = False,
    max_records: int = 0,
    workers: int = 4,
    discovery: Callable[..., Any] | None = None,
    save_every: int = 250,
) -> SidecarRefreshReport:
    """Read the ``.json`` links of records that were imported before sidecars.

    A catalog imported without the sidecar switch keeps its ``.json`` URL as
    informational text and no magnet.  This pass walks those existing records
    again, and it is the only way to fill them in short of re-importing the
    whole catalog.  The same gates as the import-time reader apply: the source
    must declare that it may distribute files, and only the hosts it names are
    approved.  Nothing about a record's title is guessed.

    Requests run on a small bounded pool, but every write to the store happens
    on the calling thread, so the store itself stays single-threaded.  Each
    outcome is stored on the record, so a second run skips a link that was
    already answered; ``force`` re-reads it anyway.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    read = discovery or sidecarlib.discover_magnet
    report = SidecarRefreshReport()
    capability = getattr(source, "download_capability", "none") or "none"
    if capability not in sidecarlib.PERMITTED_CAPABILITIES:
        report.reason = (
            "This source declares that it does not distribute files "
            f"({capability}), so its .json links are not read for magnets."
        )
        return report
    report.permitted = True
    approved: list[str] = []
    for host in tuple(getattr(source, "hosts", ()) or ()) + tuple(
        getattr(source, "image_hosts", ()) or ()
    ):
        if host and host not in approved:
            approved.append(host)
    allowed = tuple(approved)

    games = list(store.iter_games(source.id))
    total = len(games)
    pending: list[tuple[Any, str]] = []
    for index, stored in enumerate(games, 1):
        if cancel is not None and cancel.is_set():
            report.cancelled = True
            break
        if max_records and report.scanned >= max_records:
            break
        report.scanned += 1
        urls = sidecarlib.json_urls(_scalar_strings(stored.extra))
        if not urls:
            continue
        report.with_links += 1
        if _says_no_torrent(stored.extra) and not store.magnets_for(stored.key):
            report.skipped += 1
            continue
        state = stored.extra.get(SIDECAR_KEY)
        state = state if isinstance(state, Mapping) else {}
        previous = str(state.get("url") or "")
        if (
            not force
            and previous in urls
            and str(state.get("status") or "") in _SIDECAR_DONE
        ):
            report.skipped += 1
            continue
        pending.append((stored, urls[0]))

    processed = 0
    pool = ThreadPoolExecutor(
        max_workers=max(1, min(int(workers), 8)), thread_name_prefix="lgl-sidecar"
    )
    try:
        futures = {
            pool.submit(
                read,
                url,
                allowed_hosts=allowed,
                download_capability=capability,
                title=stored.title,
            ): (stored, url)
            for stored, url in pending
        }
        report.requested = len(futures)
        for future in as_completed(futures):
            stored, url = futures[future]
            if cancel is not None and cancel.is_set():
                report.cancelled = True
                for other in futures:
                    other.cancel()
                break
            processed += 1
            try:
                result = future.result()
            except Exception as exc:  # a sidecar failure is recorded, never fatal
                stored.extra[SIDECAR_KEY] = {
                    "status": sidecarlib.STATUS_ERROR,
                    "url": url,
                    "reason": f"The .json link could not be read: {exc}"[:300],
                    "checked_at": time.time(),
                    "infohash": "",
                }
                report.failed += 1
            else:
                stored.extra[SIDECAR_KEY] = {
                    **result.to_dict(),
                    "reason": result.describe(),
                }
                if result.found and result.link is not None:
                    added = store.add_magnet(
                        MagnetRecord.from_link(
                            result.link,
                            source_id=stored.source_id,
                            game_key=stored.key,
                            source_url=stored.source_url,
                            method="json sidecar",
                        )
                    )
                    if added == "inserted":
                        report.found += 1
                else:
                    # The link was read cleanly and simply publishes nothing.
                    report.empty += 1
            if save_every and processed % save_every == 0:
                store.save()
            if progress is not None:
                try:
                    progress(processed, report.requested)
                except Exception:
                    pass
    finally:
        pool.shutdown(wait=True, cancel_futures=True)

    store.save()
    return report


def _apply_details(game: Any, details: SourceGame) -> bool:
    """Merge detail fields into a stored catalog record without clobbering."""
    changed = False
    for name in (
        "year",
        "creator",
        "developer",
        "publisher",
        "release_date",
        "genres",
        "platforms",
        "description",
        "cover_url",
    ):
        new_value = getattr(details, name)
        if not new_value:
            continue
        if not getattr(game, name):
            setattr(game, name, new_value)
            changed = True
    return changed


def import_source(
    store: CatalogStore,
    url: str,
    *,
    options: ImportOptions | None = None,
    cancel: threading.Event | None = None,
    progress: ProgressCallback | None = None,
    inspect_first: bool = True,
    api_keys: Mapping[str, str] | None = None,
    config: Any = None,
) -> tuple[ImportOutcome, Any]:
    """Resolve a URL to a source, inspect it, then import the whole catalog."""
    source = source_for_url(url, api_keys=api_keys, config=config)
    record = store.get_source(source.id)
    if record is None:
        record = SourceRecord(
            source_id=source.id,
            url=getattr(source, "canonical_url", url),
            adapter=type(source).__name__,
            name=source.name,
            collection=getattr(source, "collection", ""),
        )
        store.add_source(record)
    importer = CatalogImporter(store, source, options=options)
    report = importer.inspect() if inspect_first else None
    outcome = importer.run(cancel=cancel, progress=progress)
    store.save()
    return outcome, report


__all__ = [
    "CatalogImporter",
    "ImportCancelled",
    "ImportOptions",
    "ImportOutcome",
    "SidecarRefreshReport",
    "import_source",
    "refresh_sidecar_magnets",
]
